/*
 * NidoOS Zero-Underline IBus Bridge for Sen Viet (VieLotusIM v2.0.11+)
 *
 * Dynamically loads libVieLotusCAPI.so.0 from the unmodified upstream `vielotus`
 * package and commits Vietnamese text directly without ever invoking IBus
 * preedit APIs, achieving 0% preedit underline across GTK4, Firefox, Chromium,
 * Electron (NidoOffice), and terminals on both Wayland and X11.
 *
 * Includes full VieLotusIM v2.0.11 feature parity (VI/EN localized IBus menu,
 * two-way Telex/VNI sync, mid-word zero-underline surrounding adoption, VNI
 * Numpad literal digits, XKB combining-mark NFD->NFC normalization, per-app
 * typing-modes.yml passthrough rules, inline + fenced Developer Mode FSM)
 * alongside NidoOS customizations (post-commit BackSpace rehydration, mouse-click
 * surrounding cursor-jump guard, and verified 0ms Tier-1 surrounding replacement).
 *
 * SPDX-License-Identifier: MIT
 */

#include <ctype.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <glib.h>
#include <ibus.h>
#include <linux/input-event-codes.h>
#include <linux/uinput.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <unistd.h>

#define NIDO_IME_VERSION "0.2.2"
#define NIDO_COMPONENT_XML "/usr/share/ibus/component/nido-vielotus.xml"
#define NIDO_BUS_NAME "org.nidoos.IBus.VieLotus"
#define NIDO_SHADOW_MAX 512
#define NIDO_KEY_QUEUE_CAP 64
#define NIDO_BS_KEYCODE_EVDEV 14u
#define NIDO_DEFAULT_BS_DELAY_US 8000u
#define NIDO_MAX_BS_DELAY_US 50000u
#define NIDO_SURROUNDING_SYNC_GRACE_US 150000
#define NIDO_CONFIG_POLL_INTERVAL_US 500000
#define NIDO_MAX_PRESENTATION_RULES 128
#define NIDO_MAX_RULE_KEY_LEN 96

/* ============================================================================
 * Upstream libVieLotusCAPI.so.0 ABI (from /usr/include/vielotus.h)
 * ============================================================================ */

typedef void *vielotus_session_t;

typedef enum {
    VIELOTUS_INPUT_TELEX = 0,
    VIELOTUS_INPUT_VNI = 1
} vielotus_input_method_t;

typedef enum {
    VIELOTUS_GRAMMAR_CLASSIC = 0,
    VIELOTUS_GRAMMAR_SEN_FLEX = 1
} vielotus_grammar_profile_t;

typedef enum {
    VIELOTUS_ACTION_PASSTHROUGH = 0,
    VIELOTUS_ACTION_CONSUMED = 1,
    VIELOTUS_ACTION_REPLACE = 2,
    VIELOTUS_ACTION_COMMIT = 3,
    VIELOTUS_ACTION_RESET = 4
} vielotus_action_t;

typedef struct {
    void *handle;
    vielotus_session_t (*create_session)(void);
    void (*destroy_session)(vielotus_session_t);
    void (*reset_session)(vielotus_session_t);
    void (*set_input_method)(vielotus_session_t, vielotus_input_method_t);
    void (*set_grammar_profile)(vielotus_session_t, int32_t);
    int32_t (*get_grammar_profile)(vielotus_session_t);
    void (*set_macro_enabled)(vielotus_session_t, bool);
    bool (*load_macros_json)(const char *);
    void (*reset_default_macros)(void);
    void (*set_smart_bilingual)(vielotus_session_t, bool);
    void (*set_modern_orthography)(vielotus_session_t, bool);
    void (*set_quick_telex)(vielotus_session_t, bool);
    void (*set_relaxed_coda)(vielotus_session_t, bool);
    int32_t (*feed_char)(vielotus_session_t, uint32_t);
    int32_t (*feed_char_ex)(vielotus_session_t, uint32_t, int32_t *, const char **);
    int32_t (*feed_backspace)(vielotus_session_t);
    int32_t (*commit_word)(vielotus_session_t);
    int32_t (*get_last_backspaces)(vielotus_session_t);
    const char *(*get_last_text)(vielotus_session_t);
    bool (*get_last_is_override)(vielotus_session_t);
    bool (*is_composing)(vielotus_session_t);
    const char *(*get_composing_text)(vielotus_session_t);
    const char *(*get_raw_text)(vielotus_session_t);
    void (*append_context)(vielotus_session_t, const char *);
    void (*sync_armed_word)(vielotus_session_t, const char *);
    const char *(*get_version)(void);
} VieLotusApi;

static VieLotusApi g_vl = {0};

static bool load_sym_req(void *h, const char *name, void **out) {
    *out = dlsym(h, name);
    if (!*out) {
        g_warning("nido-ibus-vielotus: missing required symbol %s in libVieLotusCAPI", name);
        return false;
    }
    return true;
}

static void load_sym_opt(void *h, const char *name, void **out) {
    *out = dlsym(h, name);
}

static bool vielotus_api_load(const char *override_path) {
    if (g_vl.handle) {
        return true;
    }

    const char *candidates[4] = {
        override_path,
        "libVieLotusCAPI.so.0",
        "/usr/lib/libVieLotusCAPI.so.0",
        "/usr/lib/libVieLotusCAPI.so"
    };

    void *h = NULL;
    for (size_t i = 0; i < 4; i++) {
        if (!candidates[i] || !*candidates[i]) {
            continue;
        }
        h = dlopen(candidates[i], RTLD_NOW | RTLD_LOCAL);
        if (h) {
            break;
        }
    }
    if (!h) {
        g_warning("nido-ibus-vielotus: failed to dlopen libVieLotusCAPI.so.0: %s", dlerror());
        return false;
    }

    bool ok = true;
    ok &= load_sym_req(h, "vielotus_create_session", (void **)&g_vl.create_session);
    ok &= load_sym_req(h, "vielotus_destroy_session", (void **)&g_vl.destroy_session);
    ok &= load_sym_req(h, "vielotus_reset_session", (void **)&g_vl.reset_session);
    ok &= load_sym_req(h, "vielotus_set_input_method", (void **)&g_vl.set_input_method);
    ok &= load_sym_req(h, "vielotus_feed_char", (void **)&g_vl.feed_char);
    ok &= load_sym_req(h, "vielotus_feed_backspace", (void **)&g_vl.feed_backspace);
    ok &= load_sym_req(h, "vielotus_commit_word", (void **)&g_vl.commit_word);
    ok &= load_sym_req(h, "vielotus_get_last_backspaces", (void **)&g_vl.get_last_backspaces);
    ok &= load_sym_req(h, "vielotus_get_last_text", (void **)&g_vl.get_last_text);
    ok &= load_sym_req(h, "vielotus_is_composing", (void **)&g_vl.is_composing);
    ok &= load_sym_req(h, "vielotus_get_composing_text", (void **)&g_vl.get_composing_text);

    if (!ok) {
        dlclose(h);
        memset(&g_vl, 0, sizeof(g_vl));
        return false;
    }

    load_sym_opt(h, "vielotus_feed_char_ex", (void **)&g_vl.feed_char_ex);
    load_sym_opt(h, "vielotus_set_grammar_profile", (void **)&g_vl.set_grammar_profile);
    load_sym_opt(h, "vielotus_get_grammar_profile", (void **)&g_vl.get_grammar_profile);
    load_sym_opt(h, "vielotus_set_macro_enabled", (void **)&g_vl.set_macro_enabled);
    load_sym_opt(h, "vielotus_load_macros_json", (void **)&g_vl.load_macros_json);
    load_sym_opt(h, "vielotus_reset_default_macros", (void **)&g_vl.reset_default_macros);
    load_sym_opt(h, "vielotus_set_smart_bilingual", (void **)&g_vl.set_smart_bilingual);
    load_sym_opt(h, "vielotus_set_modern_orthography", (void **)&g_vl.set_modern_orthography);
    load_sym_opt(h, "vielotus_set_quick_telex", (void **)&g_vl.set_quick_telex);
    load_sym_opt(h, "vielotus_set_relaxed_coda", (void **)&g_vl.set_relaxed_coda);
    load_sym_opt(h, "vielotus_get_last_is_override", (void **)&g_vl.get_last_is_override);
    load_sym_opt(h, "vielotus_get_raw_text", (void **)&g_vl.get_raw_text);
    load_sym_opt(h, "vielotus_append_context", (void **)&g_vl.append_context);
    load_sym_opt(h, "vielotus_sync_armed_word", (void **)&g_vl.sync_armed_word);
    load_sym_opt(h, "vielotus_get_version", (void **)&g_vl.get_version);

    g_vl.handle = h;
    return true;
}

static int32_t vl_feed_char(vielotus_session_t session, uint32_t cp, int32_t *out_bs, const char **out_txt) {
    if (g_vl.feed_char_ex) {
        return g_vl.feed_char_ex(session, cp, out_bs, out_txt);
    }
    int32_t act = g_vl.feed_char(session, cp);
    if (out_bs) {
        *out_bs = g_vl.get_last_backspaces(session);
    }
    if (out_txt) {
        *out_txt = g_vl.get_last_text(session);
    }
    return act;
}

/* ============================================================================
 * Clean-Room VieLotusIM v2.0.11 Shared Helpers (NFC Reconstruction, Numpad,
 * XKB Combining Marks, UI Localization, Presentation Rules & Dev Code FSM)
 * ============================================================================ */

typedef struct {
    const char *lower_utf8;
    const char *upper_utf8;
    char lower_base;
    char upper_base;
    uint8_t mod;  /* 0=none, 1=d-stroke, 2=circumflex(^), 3=breve(ă), 4=horn(ư/ơ) */
    uint8_t tone; /* 0=none, 1=grave(f/2), 2=acute(s/1), 3=hook(r/3), 4=tilde(x/4), 5=dot(j/5) */
} VlUtf8CharMap;

static const VlUtf8CharMap kVlVnUtf8Map[] = {
    { "đ", "Đ", 'd', 'D', 1, 0 },
    { "à", "À", 'a', 'A', 0, 1 }, { "á", "Á", 'a', 'A', 0, 2 }, { "ả", "Ả", 'a', 'A', 0, 3 }, { "ã", "Ã", 'a', 'A', 0, 4 }, { "ạ", "Ạ", 'a', 'A', 0, 5 },
    { "ă", "Ă", 'a', 'A', 3, 0 }, { "ằ", "Ằ", 'a', 'A', 3, 1 }, { "ắ", "Ắ", 'a', 'A', 3, 2 }, { "ẳ", "Ẳ", 'a', 'A', 3, 3 }, { "ẵ", "Ẵ", 'a', 'A', 3, 4 }, { "ặ", "Ặ", 'a', 'A', 3, 5 },
    { "â", "Â", 'a', 'A', 2, 0 }, { "ầ", "Ầ", 'a', 'A', 2, 1 }, { "ấ", "Ấ", 'a', 'A', 2, 2 }, { "ẩ", "Ẩ", 'a', 'A', 2, 3 }, { "ẫ", "Ẫ", 'a', 'A', 2, 4 }, { "ậ", "Ậ", 'a', 'A', 2, 5 },
    { "è", "È", 'e', 'E', 0, 1 }, { "é", "É", 'e', 'E', 0, 2 }, { "ẻ", "Ẻ", 'e', 'E', 0, 3 }, { "ẽ", "Ẽ", 'e', 'E', 0, 4 }, { "ẹ", "Ẹ", 'e', 'E', 0, 5 },
    { "ê", "Ê", 'e', 'E', 2, 0 }, { "ề", "Ề", 'e', 'E', 2, 1 }, { "ế", "Ế", 'e', 'E', 2, 2 }, { "ể", "Ể", 'e', 'E', 2, 3 }, { "ễ", "Ễ", 'e', 'E', 2, 4 }, { "ệ", "Ệ", 'e', 'E', 2, 5 },
    { "ì", "Ì", 'i', 'I', 0, 1 }, { "í", "Í", 'i', 'I', 0, 2 }, { "ỉ", "Ỉ", 'i', 'I', 0, 3 }, { "ĩ", "Ĩ", 'i', 'I', 0, 4 }, { "ị", "Ị", 'i', 'I', 0, 5 },
    { "ò", "Ò", 'o', 'O', 0, 1 }, { "ó", "Ó", 'o', 'O', 0, 2 }, { "ỏ", "Ỏ", 'o', 'O', 0, 3 }, { "õ", "Õ", 'o', 'O', 0, 4 }, { "ọ", "Ọ", 'o', 'O', 0, 5 },
    { "ô", "Ô", 'o', 'O', 2, 0 }, { "ồ", "Ồ", 'o', 'O', 2, 1 }, { "ố", "Ố", 'o', 'O', 2, 2 }, { "ổ", "Ổ", 'o', 'O', 2, 3 }, { "ỗ", "Ỗ", 'o', 'O', 2, 4 }, { "ộ", "Ộ", 'o', 'O', 2, 5 },
    { "ơ", "Ơ", 'o', 'O', 4, 0 }, { "ờ", "Ờ", 'o', 'O', 4, 1 }, { "ớ", "Ớ", 'o', 'O', 4, 2 }, { "ở", "Ở", 'o', 'O', 4, 3 }, { "ỡ", "Ỡ", 'o', 'O', 4, 4 }, { "ợ", "Ợ", 'o', 'O', 4, 5 },
    { "ù", "Ù", 'u', 'U', 0, 1 }, { "ú", "Ú", 'u', 'U', 0, 2 }, { "ủ", "Ủ", 'u', 'U', 0, 3 }, { "ũ", "Ũ", 'u', 'U', 0, 4 }, { "ụ", "Ụ", 'u', 'U', 0, 5 },
    { "ư", "Ư", 'u', 'U', 4, 0 }, { "ừ", "Ừ", 'u', 'U', 4, 1 }, { "ứ", "Ứ", 'u', 'U', 4, 2 }, { "ử", "Ử", 'u', 'U', 4, 3 }, { "ữ", "Ữ", 'u', 'U', 4, 4 }, { "ự", "Ự", 'u', 'U', 4, 5 },
    { "ỳ", "Ỳ", 'y', 'Y', 0, 1 }, { "ý", "Ý", 'y', 'Y', 0, 2 }, { "ỷ", "Ỷ", 'y', 'Y', 0, 3 }, { "ỹ", "Ỹ", 'y', 'Y', 0, 4 }, { "ỵ", "Ỵ", 'y', 'Y', 0, 5 },
};

static char vl_ascii_tolower(char c) {
    return (c >= 'A' && c <= 'Z') ? (char)(c + ('a' - 'A')) : c;
}

static size_t vl_utf8_char_len(unsigned char b) {
    if ((b & 0x80) == 0) return 1;
    if ((b & 0xE0) == 0xC0) return 2;
    if ((b & 0xF0) == 0xE0) return 3;
    if ((b & 0xF8) == 0xF0) return 4;
    return 1;
}

static bool vl_has_non_ascii(const char *utf8_str) {
    if (!utf8_str) {
        return false;
    }
    for (const unsigned char *p = (const unsigned char *)utf8_str; *p; p++) {
        if (*p >= 0x80) {
            return true;
        }
    }
    return false;
}

static uint8_t vl_telex_tone_for_key(char lower_key) {
    switch (lower_key) {
        case 'f': return 1;
        case 's': return 2;
        case 'r': return 3;
        case 'x': return 4;
        case 'j': return 5;
        default: return 0;
    }
}

static uint8_t vl_active_tone_of_utf8(const char *utf8_str) {
    if (!utf8_str) {
        return 0;
    }
    const char *p = utf8_str;
    const size_t map_len = sizeof(kVlVnUtf8Map) / sizeof(kVlVnUtf8Map[0]);
    while (*p) {
        size_t clen = vl_utf8_char_len((unsigned char)*p);
        if (clen > 1) {
            for (size_t i = 0; i < map_len; i++) {
                if ((strlen(kVlVnUtf8Map[i].lower_utf8) == clen && strncmp(p, kVlVnUtf8Map[i].lower_utf8, clen) == 0) ||
                    (strlen(kVlVnUtf8Map[i].upper_utf8) == clen && strncmp(p, kVlVnUtf8Map[i].upper_utf8, clen) == 0)) {
                    if (kVlVnUtf8Map[i].tone > 0) {
                        return kVlVnUtf8Map[i].tone;
                    }
                }
            }
        }
        p += clen;
    }
    return 0;
}

static void vl_raw_keys_for_utf8(
    const char *utf8_str,
    bool is_vni,
    char *out,
    size_t out_cap
) {
    if (!out || out_cap == 0) {
        return;
    }
    if (!utf8_str) {
        out[0] = '\0';
        return;
    }
    size_t len = 0;
    char tone_char = 0;
    const char *p = utf8_str;
    const size_t map_len = sizeof(kVlVnUtf8Map) / sizeof(kVlVnUtf8Map[0]);
    while (*p && len + 4 < out_cap) {
        size_t clen = vl_utf8_char_len((unsigned char)*p);
        if (clen == 1) {
            out[len++] = *p;
            p++;
            continue;
        }
        bool matched = false;
        for (size_t i = 0; i < map_len; i++) {
            bool is_lower = (strlen(kVlVnUtf8Map[i].lower_utf8) == clen && strncmp(p, kVlVnUtf8Map[i].lower_utf8, clen) == 0);
            bool is_upper = !is_lower && (strlen(kVlVnUtf8Map[i].upper_utf8) == clen && strncmp(p, kVlVnUtf8Map[i].upper_utf8, clen) == 0);
            if (is_lower || is_upper) {
                char base = is_upper ? kVlVnUtf8Map[i].upper_base : kVlVnUtf8Map[i].lower_base;
                out[len++] = base;
                switch (kVlVnUtf8Map[i].mod) {
                    case 1:
                        out[len++] = is_vni ? '9' : (is_upper ? 'D' : 'd');
                        break;
                    case 2:
                        out[len++] = is_vni ? '6' : kVlVnUtf8Map[i].lower_base;
                        break;
                    case 3:
                        out[len++] = is_vni ? '8' : 'w';
                        break;
                    case 4:
                        if ((base == 'o' || base == 'O') && len >= 3 &&
                            (out[len - 3] == 'u' || out[len - 3] == 'U') &&
                            (out[len - 2] == 'w' || out[len - 2] == '7')) {
                            out[len - 2] = base;
                            out[len - 1] = is_vni ? '7' : 'w';
                        } else {
                            out[len++] = is_vni ? '7' : 'w';
                        }
                        break;
                    default:
                        break;
                }
                if (kVlVnUtf8Map[i].tone > 0 && kVlVnUtf8Map[i].tone <= 5) {
                    tone_char = is_vni ? "021345"[kVlVnUtf8Map[i].tone] : "0fsrxj"[kVlVnUtf8Map[i].tone];
                }
                matched = true;
                break;
            }
        }
        if (!matched) {
            for (size_t k = 0; k < clen && p[k] && len + 1 < out_cap; k++) {
                out[len++] = p[k];
            }
        }
        p += clen;
    }
    if (tone_char != 0 && len + 1 < out_cap) {
        out[len++] = tone_char;
    }
    out[len] = '\0';
}

static size_t vl_encode_utf8_cp(uint32_t cp, char out[5]) {
    if (cp < 0x80) {
        out[0] = (char)cp;
        out[1] = '\0';
        return 1;
    }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | (cp >> 6));
        out[1] = (char)(0x80 | (cp & 0x3F));
        out[2] = '\0';
        return 2;
    }
    if (cp < 0x10000) {
        out[0] = (char)(0xE0 | (cp >> 12));
        out[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (char)(0x80 | (cp & 0x3F));
        out[3] = '\0';
        return 3;
    }
    if (cp <= 0x10FFFF) {
        out[0] = (char)(0xF0 | (cp >> 18));
        out[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        out[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        out[3] = (char)(0x80 | (cp & 0x3F));
        out[4] = '\0';
        return 4;
    }
    out[0] = '\0';
    return 0;
}

static bool vl_keysym_to_raw_seq(
    uint32_t keysym,
    uint32_t unicode_cp,
    bool is_vni,
    char *out_seq,
    size_t out_cap
) {
    if (!out_seq || out_cap < 5) {
        return false;
    }
    out_seq[0] = '\0';

    if (unicode_cp == 0x0300 || keysym == 0xFE50) {
        out_seq[0] = is_vni ? '2' : 'f';
        out_seq[1] = '\0';
        return true;
    }
    if (unicode_cp == 0x0301 || keysym == 0xFE51) {
        out_seq[0] = is_vni ? '1' : 's';
        out_seq[1] = '\0';
        return true;
    }
    if (unicode_cp == 0x0309 || keysym == 0xFE61) {
        out_seq[0] = is_vni ? '3' : 'r';
        out_seq[1] = '\0';
        return true;
    }
    if (unicode_cp == 0x0303 || keysym == 0xFE53) {
        out_seq[0] = is_vni ? '4' : 'x';
        out_seq[1] = '\0';
        return true;
    }
    if (unicode_cp == 0x0323 || keysym == 0xFE60) {
        out_seq[0] = is_vni ? '5' : 'j';
        out_seq[1] = '\0';
        return true;
    }
    if (unicode_cp == 0x031B || keysym == 0xFE62) {
        out_seq[0] = is_vni ? '7' : 'w';
        out_seq[1] = '\0';
        return true;
    }
    if (unicode_cp == 0x0306 || keysym == 0xFE55) {
        out_seq[0] = is_vni ? '8' : 'w';
        out_seq[1] = '\0';
        return true;
    }

    if (unicode_cp >= 0x00C0 && unicode_cp <= 0x1EFF) {
        char utf8_ch[5];
        size_t ulen = vl_encode_utf8_cp(unicode_cp, utf8_ch);
        if (ulen > 1) {
            const size_t map_len = sizeof(kVlVnUtf8Map) / sizeof(kVlVnUtf8Map[0]);
            for (size_t i = 0; i < map_len; i++) {
                if ((strlen(kVlVnUtf8Map[i].lower_utf8) == ulen &&
                     memcmp(utf8_ch, kVlVnUtf8Map[i].lower_utf8, ulen) == 0) ||
                    (strlen(kVlVnUtf8Map[i].upper_utf8) == ulen &&
                     memcmp(utf8_ch, kVlVnUtf8Map[i].upper_utf8, ulen) == 0)) {
                    vl_raw_keys_for_utf8(utf8_ch, is_vni, out_seq, out_cap);
                    return out_seq[0] != '\0';
                }
            }
        }
    }
    return false;
}

static bool vl_is_numpad_digit_keysym(uint32_t keysym) {
    return keysym >= 0xFFB0u && keysym <= 0xFFB9u;
}

static uint32_t vl_normalize_numpad_keysym(uint32_t keysym) {
    if (keysym >= 0xFFB0u && keysym <= 0xFFB9u) {
        return (uint32_t)('0' + (keysym - 0xFFB0u));
    }
    switch (keysym) {
        case 0xFFAAu: return (uint32_t)'*';
        case 0xFFABu: return (uint32_t)'+';
        case 0xFFACu: return (uint32_t)',';
        case 0xFFADu: return (uint32_t)'-';
        case 0xFFAEu: return (uint32_t)'.';
        case 0xFFAFu: return (uint32_t)'/';
        case 0xFFBDu: return (uint32_t)'=';
        case 0xFF80u: return (uint32_t)' ';
        default: return keysym;
    }
}

static void vl_normalize_ui_language(const char *val, char out[8]) {
    if (!out) {
        return;
    }
    if (!val || val[0] == '\0') {
        strcpy(out, "auto");
        return;
    }
    if (g_ascii_strcasecmp(val, "vi") == 0) {
        strcpy(out, "vi");
    } else if (g_ascii_strcasecmp(val, "en") == 0) {
        strcpy(out, "en");
    } else {
        strcpy(out, "auto");
    }
}

static bool vl_ui_is_english(const char *ui_language) {
    char norm[8];
    vl_normalize_ui_language(ui_language, norm);
    if (strcmp(norm, "vi") == 0) {
        return false;
    }
    if (strcmp(norm, "en") == 0) {
        return true;
    }

    char cand[128] = {0};
    const char *lc_all = getenv("LC_ALL");
    if (lc_all && lc_all[0] != '\0') {
        if (strcmp(lc_all, "C") == 0 || strcmp(lc_all, "POSIX") == 0) {
            return true;
        }
        g_strlcpy(cand, lc_all, sizeof(cand));
    } else {
        const char *language = getenv("LANGUAGE");
        if (language && language[0] != '\0') {
            const char *colon = strchr(language, ':');
            size_t len = colon ? (size_t)(colon - language) : strlen(language);
            if (len >= sizeof(cand)) {
                len = sizeof(cand) - 1;
            }
            memcpy(cand, language, len);
            cand[len] = '\0';
        }
        if (cand[0] == '\0') {
            const char *lc_msg = getenv("LC_MESSAGES");
            if (lc_msg && lc_msg[0] != '\0') {
                g_strlcpy(cand, lc_msg, sizeof(cand));
            }
        }
        if (cand[0] == '\0') {
            const char *lang = getenv("LANG");
            if (lang && lang[0] != '\0') {
                g_strlcpy(cand, lang, sizeof(cand));
            }
        }
    }

    if (cand[0] == '\0') {
        return true;
    }
    char c0 = vl_ascii_tolower(cand[0]);
    char c1 = vl_ascii_tolower(cand[1]);
    char c2 = cand[2];
    if (c0 == 'v' && c1 == 'i' &&
        (c2 == '\0' || c2 == '_' || c2 == '-' || c2 == '.' || c2 == '@')) {
        return false;
    }
    return true;
}

/* Per-app presentation rules (`typing-modes.yml`): honors `passthrough` cleanly */
typedef enum {
    VL_PRESENTATION_AUTOMATIC = 0,
    VL_PRESENTATION_DIRECT_REPLACEMENT = 1,
    VL_PRESENTATION_MARKED_TEXT = 2,
    VL_PRESENTATION_PASSTHROUGH = 3
} VlPresentationMode;

typedef struct {
    char key[NIDO_MAX_RULE_KEY_LEN];
    VlPresentationMode mode;
} VlPresentationRule;

typedef struct {
    VlPresentationRule items[NIDO_MAX_PRESENTATION_RULES];
    size_t count;
    time_t loaded_mtime;
    char loaded_path[512];
} VlPresentationRules;

static void vl_presentation_rules_init(VlPresentationRules *rules) {
    if (!rules) {
        return;
    }
    rules->count = 0;
    rules->loaded_mtime = 0;
    rules->loaded_path[0] = '\0';
}

static bool vl_presentation_rule_matches(const char *rule_key, const char *app_id_lower) {
    if (!rule_key || !app_id_lower || rule_key[0] == '\0' || app_id_lower[0] == '\0') {
        return false;
    }
    if (strchr(rule_key, '*') == NULL) {
        return strcmp(rule_key, app_id_lower) == 0;
    }

    const size_t key_len = strlen(rule_key);
    const size_t id_len = strlen(app_id_lower);
    const bool anchored_start = (rule_key[0] != '*');
    const bool anchored_end = (rule_key[key_len - 1] != '*');

    char key_buf[NIDO_MAX_RULE_KEY_LEN];
    if (key_len >= sizeof(key_buf)) {
        return false;
    }
    memcpy(key_buf, rule_key, key_len + 1);

    const char *segments[16];
    size_t seg_lens[16];
    size_t seg_count = 0;
    char *cur = key_buf;
    while (seg_count < 16) {
        char *star = strchr(cur, '*');
        if (!star) {
            segments[seg_count] = cur;
            seg_lens[seg_count] = strlen(cur);
            seg_count++;
            break;
        }
        *star = '\0';
        segments[seg_count] = cur;
        seg_lens[seg_count] = (size_t)(star - cur);
        seg_count++;
        cur = star + 1;
    }

    size_t search_pos = 0;
    for (size_t idx = 0; idx < seg_count; idx++) {
        const char *seg = segments[idx];
        const size_t slen = seg_lens[idx];
        if (slen == 0) {
            continue;
        }
        if (idx == seg_count - 1 && anchored_end) {
            if (id_len < slen) {
                return false;
            }
            const size_t suffix_pos = id_len - slen;
            if (suffix_pos < search_pos) {
                return false;
            }
            return memcmp(app_id_lower + suffix_pos, seg, slen) == 0;
        }
        const char *found = strstr(app_id_lower + search_pos, seg);
        if (!found) {
            return false;
        }
        const size_t found_pos = (size_t)(found - app_id_lower);
        if (idx == 0 && anchored_start && found_pos != 0) {
            return false;
        }
        search_pos = found_pos + slen;
    }
    return true;
}

static bool vl_parse_presentation_mode(const char *raw_val, VlPresentationMode *out_mode) {
    if (!raw_val || !out_mode) {
        return false;
    }
    if (g_ascii_strcasecmp(raw_val, "automatic") == 0 || g_ascii_strcasecmp(raw_val, "auto") == 0) {
        *out_mode = VL_PRESENTATION_AUTOMATIC;
        return true;
    }
    if (g_ascii_strcasecmp(raw_val, "directReplacement") == 0 ||
        g_ascii_strcasecmp(raw_val, "direct_replacement") == 0 ||
        g_ascii_strcasecmp(raw_val, "direct") == 0) {
        *out_mode = VL_PRESENTATION_DIRECT_REPLACEMENT;
        return true;
    }
    if (g_ascii_strcasecmp(raw_val, "markedText") == 0 ||
        g_ascii_strcasecmp(raw_val, "marked_text") == 0) {
        *out_mode = VL_PRESENTATION_MARKED_TEXT;
        return true;
    }
    if (g_ascii_strcasecmp(raw_val, "passthrough") == 0) {
        *out_mode = VL_PRESENTATION_PASSTHROUGH;
        return true;
    }
    return false;
}

static void vl_presentation_rules_parse_text(VlPresentationRules *rules, const char *yaml_text) {
    if (!rules) {
        return;
    }
    rules->count = 0;
    if (!yaml_text) {
        return;
    }

    const char *p = yaml_text;
    while (*p && rules->count < NIDO_MAX_PRESENTATION_RULES) {
        const char *eol = strchr(p, '\n');
        size_t line_len = eol ? (size_t)(eol - p) : strlen(p);
        char line[256];
        if (line_len >= sizeof(line)) {
            line_len = sizeof(line) - 1;
        }
        memcpy(line, p, line_len);
        line[line_len] = '\0';
        p = eol ? (eol + 1) : (p + strlen(p));

        char *s = line;
        while (*s == ' ' || *s == '\t' || *s == '\r') {
            s++;
        }
        if (*s == '\0' || *s == '#' || (s[0] == '/' && s[1] == '/')) {
            continue;
        }
        char *colon = strchr(s, ':');
        if (!colon) {
            continue;
        }
        *colon = '\0';

        char *ks = s;
        size_t klen = strlen(ks);
        while (klen > 0 && (ks[klen - 1] == ' ' || ks[klen - 1] == '\t')) {
            ks[--klen] = '\0';
        }
        if (klen >= 2 && ((ks[0] == '"' && ks[klen - 1] == '"') || (ks[0] == '\'' && ks[klen - 1] == '\''))) {
            ks++;
            klen -= 2;
            ks[klen] = '\0';
        }
        if (klen == 0 || klen >= NIDO_MAX_RULE_KEY_LEN) {
            continue;
        }

        char *vs = colon + 1;
        while (*vs == ' ' || *vs == '\t') {
            vs++;
        }
        char *hash = strchr(vs, '#');
        if (hash) {
            *hash = '\0';
        }
        size_t vlen = strlen(vs);
        while (vlen > 0 && (vs[vlen - 1] == ' ' || vs[vlen - 1] == '\t' || vs[vlen - 1] == '\r')) {
            vs[--vlen] = '\0';
        }
        if (vlen >= 2 && ((vs[0] == '"' && vs[vlen - 1] == '"') || (vs[0] == '\'' && vs[vlen - 1] == '\''))) {
            vs++;
            vlen -= 2;
            vs[vlen] = '\0';
        }

        VlPresentationMode mode = VL_PRESENTATION_AUTOMATIC;
        if (!vl_parse_presentation_mode(vs, &mode)) {
            continue;
        }
        VlPresentationRule *dst = &rules->items[rules->count++];
        for (size_t i = 0; i < klen; i++) {
            dst->key[i] = vl_ascii_tolower(ks[i]);
        }
        dst->key[klen] = '\0';
        dst->mode = mode;
    }
}

static void vl_presentation_rules_reload_if_changed(
    VlPresentationRules *rules,
    const char *user_path
) {
    if (!rules) {
        return;
    }
    static const char *const kSysPaths[] = {
        "/usr/share/ibus-vielotus/typing-modes.yml",
        "/usr/share/vielotus/typing-modes.yml",
        "/usr/share/vielotus/VietLotusIM_VieLotusCore.resources/typing-modes.yml"
    };
    const char *chosen = NULL;
    struct stat st;
    if (user_path && user_path[0] != '\0' && stat(user_path, &st) == 0 && st.st_size >= 0 && st.st_size <= 131072) {
        chosen = user_path;
    } else {
        for (size_t i = 0; i < sizeof(kSysPaths) / sizeof(kSysPaths[0]); i++) {
            if (stat(kSysPaths[i], &st) == 0 && st.st_size > 0 && st.st_size <= 131072) {
                chosen = kSysPaths[i];
                break;
            }
        }
    }
    if (!chosen) {
        if (rules->loaded_path[0] != '\0') {
            vl_presentation_rules_init(rules);
        }
        return;
    }
    if (strcmp(rules->loaded_path, chosen) == 0 && rules->loaded_mtime == st.st_mtime) {
        return;
    }
    gchar *content = NULL;
    if (g_file_get_contents(chosen, &content, NULL, NULL)) {
        vl_presentation_rules_parse_text(rules, content);
        g_free(content);
        rules->loaded_mtime = st.st_mtime;
        g_strlcpy(rules->loaded_path, chosen, sizeof(rules->loaded_path));
    }
}

static VlPresentationMode vl_presentation_rules_resolve(
    const VlPresentationRules *rules,
    const char *app_id
) {
    if (!rules || rules->count == 0 || !app_id || app_id[0] == '\0') {
        return VL_PRESENTATION_AUTOMATIC;
    }
    char lower_id[256];
    size_t i = 0;
    for (; app_id[i] != '\0' && i + 1 < sizeof(lower_id); i++) {
        lower_id[i] = vl_ascii_tolower(app_id[i]);
    }
    lower_id[i] = '\0';
    for (size_t r = 0; r < rules->count; r++) {
        if (vl_presentation_rule_matches(rules->items[r].key, lower_id)) {
            return rules->items[r].mode;
        }
    }
    const char *colon = strchr(lower_id, ':');
    if (colon && colon[1] != '\0') {
        for (size_t r = 0; r < rules->count; r++) {
            if (vl_presentation_rule_matches(rules->items[r].key, colon + 1)) {
                return rules->items[r].mode;
            }
        }
    }
    return VL_PRESENTATION_AUTOMATIC;
}

/* Developer Mode inline (`...`) & fenced (```...```) code bypass FSM */
typedef struct {
    uint8_t consecutive_backticks;
    bool in_inline_code;
    bool in_fenced_code;
} VlDevCodeState;

static void vl_dev_code_reset(VlDevCodeState *st) {
    if (!st) {
        return;
    }
    st->consecutive_backticks = 0;
    st->in_inline_code = false;
    st->in_fenced_code = false;
}

static void vl_dev_code_on_backtick(VlDevCodeState *st) {
    if (!st) {
        return;
    }
    st->consecutive_backticks++;
    if (st->consecutive_backticks >= 3) {
        st->in_fenced_code = !st->in_fenced_code;
        st->in_inline_code = false;
        st->consecutive_backticks = 0;
    } else if (!st->in_fenced_code) {
        st->in_inline_code = !st->in_inline_code;
    }
}

static void vl_dev_code_on_non_backtick(VlDevCodeState *st) {
    if (!st) {
        return;
    }
    st->consecutive_backticks = 0;
}

static void vl_dev_code_on_newline(VlDevCodeState *st) {
    if (!st) {
        return;
    }
    st->consecutive_backticks = 0;
    st->in_inline_code = false;
}

static bool vl_dev_code_is_bypassing(const VlDevCodeState *st) {
    if (!st) {
        return false;
    }
    return st->in_inline_code || st->in_fenced_code;
}

/* ============================================================================
 * Shared Config (~/.config/vielotus/config.env & macros.json)
 * ============================================================================ */

typedef enum {
    NIDO_BS_AUTO = 0,
    NIDO_BS_FORWARD = 1,
    NIDO_BS_UINPUT = 2
} NidoBsBackend;

typedef struct {
    bool has_input_method;
    vielotus_input_method_t config_method;
    vielotus_grammar_profile_t grammar_profile;
    bool smart_bilingual;
    bool modern_orthography;
    bool quick_telex;
    bool relaxed_coda;
    bool macro_enabled;
    bool developer_mode;
    char ui_language[8];
    NidoBsBackend bs_backend;
    guint bs_delay_us;
} NidoConfig;

static void nido_config_defaults(NidoConfig *cfg) {
    cfg->has_input_method = false;
    cfg->config_method = VIELOTUS_INPUT_TELEX;
    cfg->grammar_profile = VIELOTUS_GRAMMAR_CLASSIC;
    cfg->smart_bilingual = true;
    cfg->modern_orthography = true;
    cfg->quick_telex = false;
    cfg->relaxed_coda = false;
    cfg->macro_enabled = false;
    cfg->developer_mode = false;
    strcpy(cfg->ui_language, "auto");
    cfg->bs_backend = NIDO_BS_AUTO;
    cfg->bs_delay_us = NIDO_DEFAULT_BS_DELAY_US;
}

static bool parse_bool_val(const char *val, bool fallback) {
    if (!val) {
        return fallback;
    }
    if (g_ascii_strcasecmp(val, "true") == 0 || strcmp(val, "1") == 0 ||
        g_ascii_strcasecmp(val, "yes") == 0 || g_ascii_strcasecmp(val, "on") == 0) {
        return true;
    }
    if (g_ascii_strcasecmp(val, "false") == 0 || strcmp(val, "0") == 0 ||
        g_ascii_strcasecmp(val, "no") == 0 || g_ascii_strcasecmp(val, "off") == 0) {
        return false;
    }
    return fallback;
}

static gchar *get_vielotus_config_dir(void) {
    const char *snap_real_home = g_getenv("SNAP_REAL_HOME");
    if (snap_real_home && *snap_real_home) {
        return g_build_filename(snap_real_home, ".config", "vielotus", NULL);
    }
    const char *xdg = g_getenv("XDG_CONFIG_HOME");
    if (xdg && *xdg) {
        return g_build_filename(xdg, "vielotus", NULL);
    }
    const char *home = g_get_home_dir();
    return g_build_filename(home ? home : "/tmp", ".config", "vielotus", NULL);
}

static void parse_config_env_text(const char *text, NidoConfig *cfg) {
    nido_config_defaults(cfg);
    if (!text) {
        return;
    }

    gchar **lines = g_strsplit(text, "\n", -1);
    for (gsize i = 0; lines && lines[i]; i++) {
        gchar *line = g_strstrip(lines[i]);
        if (!*line || line[0] == '#') {
            continue;
        }
        char *eq = strchr(line, '=');
        if (!eq) {
            continue;
        }
        *eq = '\0';
        gchar *key = g_strstrip(line);
        gchar *val = g_strstrip(eq + 1);
        size_t vlen = strlen(val);
        if (vlen >= 2 && ((val[0] == '"' && val[vlen - 1] == '"') ||
                          (val[0] == '\'' && val[vlen - 1] == '\''))) {
            val[vlen - 1] = '\0';
            val++;
        }

        if (strcmp(key, "INPUT_METHOD") == 0) {
            cfg->has_input_method = true;
            if (g_ascii_strcasecmp(val, "vni") == 0 || strstr(val, "vni") != NULL || strstr(val, "VNI") != NULL) {
                cfg->config_method = VIELOTUS_INPUT_VNI;
            } else {
                cfg->config_method = VIELOTUS_INPUT_TELEX;
            }
        } else if (strcmp(key, "GRAMMAR_PROFILE") == 0) {
            if (g_ascii_strcasecmp(val, "sen_flex") == 0 ||
                g_ascii_strcasecmp(val, "sen-flex") == 0 ||
                g_ascii_strcasecmp(val, "senflex") == 0 ||
                g_ascii_strcasecmp(val, "flex") == 0 || strcmp(val, "1") == 0) {
                cfg->grammar_profile = VIELOTUS_GRAMMAR_SEN_FLEX;
            } else {
                cfg->grammar_profile = VIELOTUS_GRAMMAR_CLASSIC;
            }
        } else if (strcmp(key, "SMART_BILINGUAL") == 0) {
            cfg->smart_bilingual = parse_bool_val(val, cfg->smart_bilingual);
        } else if (strcmp(key, "MODERN_ORTHOGRAPHY") == 0) {
            cfg->modern_orthography = parse_bool_val(val, cfg->modern_orthography);
        } else if (strcmp(key, "QUICK_TELEX") == 0) {
            cfg->quick_telex = parse_bool_val(val, cfg->quick_telex);
        } else if (strcmp(key, "RELAXED_CODA") == 0) {
            cfg->relaxed_coda = parse_bool_val(val, cfg->relaxed_coda);
        } else if (strcmp(key, "MACRO_ENABLED") == 0) {
            cfg->macro_enabled = parse_bool_val(val, cfg->macro_enabled);
        } else if (strcmp(key, "DEVELOPER_MODE") == 0) {
            cfg->developer_mode = parse_bool_val(val, cfg->developer_mode);
        } else if (strcmp(key, "UI_LANGUAGE") == 0) {
            vl_normalize_ui_language(val, cfg->ui_language);
        } else if (strcmp(key, "NIDO_BS_BACKEND") == 0) {
            if (g_ascii_strcasecmp(val, "uinput") == 0) {
                cfg->bs_backend = NIDO_BS_UINPUT;
            } else if (g_ascii_strcasecmp(val, "forward") == 0) {
                cfg->bs_backend = NIDO_BS_FORWARD;
            } else {
                cfg->bs_backend = NIDO_BS_AUTO;
            }
        } else if (strcmp(key, "NIDO_BS_DELAY_US") == 0) {
            char *endp = NULL;
            unsigned long parsed = strtoul(val, &endp, 10);
            if (endp && endp != val) {
                if (parsed > NIDO_MAX_BS_DELAY_US) {
                    parsed = NIDO_MAX_BS_DELAY_US;
                }
                cfg->bs_delay_us = (guint)parsed;
            }
        }
    }
    g_strfreev(lines);
}

static bool save_config_key_atomic(const char *cfg_dir, const char *target_key, const char *target_val) {
    if (!cfg_dir || !target_key || !target_val) {
        return false;
    }
    if (g_mkdir_with_parents(cfg_dir, 0700) != 0) {
        return false;
    }

    gchar *cfg_path = g_build_filename(cfg_dir, "config.env", NULL);
    gchar *existing = NULL;
    g_file_get_contents(cfg_path, &existing, NULL, NULL);

    GString *out = g_string_new(NULL);
    bool replaced = false;
    if (existing) {
        gchar **lines = g_strsplit(existing, "\n", -1);
        for (gsize i = 0; lines && lines[i]; i++) {
            const char *raw = lines[i];
            if (!lines[i + 1] && *raw == '\0') {
                break;
            }
            const char *eq = strchr(raw, '=');
            if (eq && raw[0] != '#') {
                gchar *k = g_strstrip(g_strndup(raw, (gsize)(eq - raw)));
                bool match = (strcmp(k, target_key) == 0);
                g_free(k);
                if (match) {
                    if (!replaced) {
                        g_string_append_printf(out, "%s=\"%s\"\n", target_key, target_val);
                        replaced = true;
                    }
                    continue;
                }
            }
            g_string_append(out, raw);
            g_string_append_c(out, '\n');
        }
        g_strfreev(lines);
        g_free(existing);
    }
    if (!replaced) {
        g_string_append_printf(out, "%s=\"%s\"\n", target_key, target_val);
    }

    gchar *tmp_path = g_build_filename(cfg_dir, ".config.env.XXXXXX", NULL);
    int fd = mkstemp(tmp_path);
    bool ok = false;
    if (fd >= 0) {
        if (fchmod(fd, 0600) == 0) {
            ssize_t wrote = write(fd, out->str, out->len);
            if (wrote == (ssize_t)out->len && close(fd) == 0) {
                fd = -1;
                if (rename(tmp_path, cfg_path) == 0) {
                    ok = true;
                }
            }
        }
        if (fd >= 0) {
            close(fd);
        }
        if (!ok) {
            unlink(tmp_path);
        }
    }

    g_string_free(out, TRUE);
    g_free(tmp_path);
    g_free(cfg_path);
    return ok;
}

/* ============================================================================
 * Minimal UTF-8 Codepoint Diff Engine (Core Zero-Underline Math)
 * ============================================================================ */

static size_t utf8_codepoint_len(const char *s) {
    if (!s || !*s) {
        return 0;
    }
    return (size_t)g_utf8_strlen(s, -1);
}

static void compute_utf8_lcp_diff(
    const char *old_utf8,
    const char *new_utf8,
    int *out_bs,
    const char **out_insert
) {
    const char *p_old = old_utf8 ? old_utf8 : "";
    const char *p_new = new_utf8 ? new_utf8 : "";

    while (*p_old && *p_new) {
        gunichar c_old = g_utf8_get_char(p_old);
        gunichar c_new = g_utf8_get_char(p_new);
        if (c_old != c_new) {
            break;
        }
        p_old = g_utf8_next_char(p_old);
        p_new = g_utf8_next_char(p_new);
    }

    *out_bs = (int)utf8_codepoint_len(p_old);
    *out_insert = p_new;
}

static bool surrounding_matches_tail(
    const char *surrounding_utf8,
    guint cursor_pos,
    const char *shadow_utf8,
    int bs
) {
    if (!surrounding_utf8 || !shadow_utf8 || bs <= 0) {
        return false;
    }
    size_t sur_len = utf8_codepoint_len(surrounding_utf8);
    size_t sha_len = utf8_codepoint_len(shadow_utf8);
    if ((size_t)bs > sha_len || (size_t)cursor_pos > sur_len || (size_t)bs > (size_t)cursor_pos) {
        return false;
    }

    const char *sur_start = g_utf8_offset_to_pointer(surrounding_utf8, (glong)(cursor_pos - (guint)bs));
    const char *sur_end = g_utf8_offset_to_pointer(surrounding_utf8, (glong)cursor_pos);
    const char *sha_start = g_utf8_offset_to_pointer(shadow_utf8, (glong)(sha_len - (size_t)bs));
    size_t sur_bytes = (size_t)(sur_end - sur_start);
    size_t sha_bytes = strlen(sha_start);
    return (sur_bytes == sha_bytes) && (memcmp(sur_start, sha_start, sur_bytes) == 0);
}

/* ============================================================================
 * Optional Lazy /dev/uinput BackSpace Emitter (NIDO_BS_BACKEND=uinput)
 * ============================================================================ */

typedef struct {
    int fd;
    bool initialized;
    bool failed;
} NidoUinput;

static void uinput_emit_event(int fd, uint16_t type, uint16_t code, int32_t value) {
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type;
    ev.code = code;
    ev.value = value;
    ssize_t wr = write(fd, &ev, sizeof(ev));
    (void)wr;
}

static bool uinput_ensure_open(NidoUinput *ui) {
    if (ui->initialized) {
        return true;
    }
    if (ui->failed) {
        return false;
    }
    int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (fd < 0) {
        ui->failed = true;
        return false;
    }

    bool ok = true;
    ok &= (ioctl(fd, UI_SET_EVBIT, EV_KEY) == 0);
    ok &= (ioctl(fd, UI_SET_EVBIT, EV_SYN) == 0);
    ok &= (ioctl(fd, UI_SET_KEYBIT, KEY_BACKSPACE) == 0);

    struct uinput_setup usetup;
    memset(&usetup, 0, sizeof(usetup));
    usetup.id.bustype = BUS_VIRTUAL;
    usetup.id.vendor = 0x1D6B;
    usetup.id.product = 0x564C; /* "VL" */
    usetup.id.version = 1;
    snprintf(usetup.name, UINPUT_MAX_NAME_SIZE, "NidoOS Sen Viet Virtual Backspace");

    ok &= (ioctl(fd, UI_DEV_SETUP, &usetup) == 0);
    ok &= (ioctl(fd, UI_DEV_CREATE) == 0);
    if (!ok) {
        close(fd);
        ui->failed = true;
        return false;
    }

    ui->fd = fd;
    ui->initialized = true;
    return true;
}

static bool uinput_send_backspaces(NidoUinput *ui, int count) {
    if (count <= 0) {
        return true;
    }
    if (!uinput_ensure_open(ui)) {
        return false;
    }
    for (int i = 0; i < count; i++) {
        uinput_emit_event(ui->fd, EV_KEY, KEY_BACKSPACE, 1);
        uinput_emit_event(ui->fd, EV_SYN, SYN_REPORT, 0);
        uinput_emit_event(ui->fd, EV_KEY, KEY_BACKSPACE, 0);
        uinput_emit_event(ui->fd, EV_SYN, SYN_REPORT, 0);
    }
    return true;
}

static void uinput_close(NidoUinput *ui) {
    if (ui->initialized && ui->fd >= 0) {
        ioctl(ui->fd, UI_DEV_DESTROY);
        close(ui->fd);
    }
    ui->fd = -1;
    ui->initialized = false;
    ui->failed = false;
}

/* ============================================================================
 * Pure Direct-Replacement Core (Shared Between Live Engine and --self-test)
 * ============================================================================ */

typedef struct {
    void (*commit_text)(void *ctx, const char *utf8);
    void (*delete_and_commit)(void *ctx, int bs, const char *utf8, const char *prev_shadow);
} NidoSink;

typedef struct {
    vielotus_session_t session;
    vielotus_input_method_t method;
    NidoConfig cfg;
    guint purpose;
    VlPresentationMode active_presentation_mode;
    VlDevCodeState dev_code;
    char shadow[NIDO_SHADOW_MAX];
    char last_committed[NIDO_SHADOW_MAX];
    int post_commit_state; /* 0 = idle, 1 = space committed, 2 = space deleted (BS2 may rehydrate) */
} NidoCoreState;

static void core_clear_shadow(NidoCoreState *st) {
    st->shadow[0] = '\0';
}

static void core_set_shadow(NidoCoreState *st, const char *utf8) {
    if (!utf8 || !*utf8) {
        st->shadow[0] = '\0';
        return;
    }
    g_strlcpy(st->shadow, utf8, sizeof(st->shadow));
}

static void core_clear_post_commit(NidoCoreState *st) {
    st->last_committed[0] = '\0';
    st->post_commit_state = 0;
}

static void core_arm_post_commit(NidoCoreState *st, const char *utf8) {
    if (!utf8 || !*utf8) {
        core_clear_post_commit(st);
        return;
    }
    g_strlcpy(st->last_committed, utf8, sizeof(st->last_committed));
    st->post_commit_state = 1;
}

static void core_apply_config(NidoCoreState *st) {
    if (!st->session) {
        return;
    }
    g_vl.set_input_method(st->session, st->method);
    if (g_vl.set_grammar_profile) {
        g_vl.set_grammar_profile(st->session, (int32_t)st->cfg.grammar_profile);
    }
    if (g_vl.set_smart_bilingual) {
        g_vl.set_smart_bilingual(st->session, st->cfg.smart_bilingual);
    }
    if (g_vl.set_modern_orthography) {
        g_vl.set_modern_orthography(st->session, st->cfg.modern_orthography);
    }
    if (g_vl.set_quick_telex) {
        g_vl.set_quick_telex(st->session, st->cfg.quick_telex);
    }
    if (g_vl.set_relaxed_coda) {
        g_vl.set_relaxed_coda(st->session, st->cfg.relaxed_coda);
    }
    if (g_vl.set_macro_enabled) {
        g_vl.set_macro_enabled(st->session, st->cfg.macro_enabled);
    }
    if (!st->cfg.developer_mode) {
        vl_dev_code_reset(&st->dev_code);
    }
}

static void core_reset(NidoCoreState *st) {
    if (st->session) {
        g_vl.reset_session(st->session);
    }
    core_clear_shadow(st);
    core_clear_post_commit(st);
    vl_dev_code_reset(&st->dev_code);
}

static void core_replay_raw_keys(NidoCoreState *st, const char *raw_keys) {
    g_vl.reset_session(st->session);
    for (size_t i = 0; raw_keys && raw_keys[i] != '\0'; i++) {
        g_vl.feed_char(st->session, (uint32_t)(unsigned char)raw_keys[i]);
    }
}

/* ============================================================================
 * Surrounding-Text Cursor-Jump Guard (NidoSurGuard)
 * ============================================================================ */

typedef struct {
    gchar *last_text;
    guint last_cursor;
    guint last_anchor;
    bool has_last;
    bool expect_updates;
    gint64 last_key_time_us;
} NidoSurGuard;

static void sur_guard_clear(NidoSurGuard *sg) {
    g_free(sg->last_text);
    sg->last_text = NULL;
    sg->last_cursor = 0;
    sg->last_anchor = 0;
    sg->has_last = false;
    sg->expect_updates = false;
    sg->last_key_time_us = 0;
}

static bool surrounding_matches_post_commit(
    const NidoCoreState *st,
    const char *sur_str,
    guint cursor_pos
) {
    if (!sur_str || st->last_committed[0] == '\0') {
        return false;
    }
    size_t word_len = utf8_codepoint_len(st->last_committed);
    if (word_len == 0) {
        return false;
    }
    if (st->post_commit_state == 2) {
        return surrounding_matches_tail(sur_str, cursor_pos, st->last_committed, (int)word_len);
    }
    if (st->post_commit_state == 1) {
        if ((size_t)cursor_pos < word_len + 1) {
            return false;
        }
        size_t sur_len = utf8_codepoint_len(sur_str);
        if ((size_t)cursor_pos > sur_len) {
            return false;
        }
        const char *sp_ptr = g_utf8_offset_to_pointer(sur_str, (glong)(cursor_pos - 1));
        if (g_utf8_get_char(sp_ptr) != ' ') {
            return false;
        }
        return surrounding_matches_tail(sur_str, cursor_pos - 1, st->last_committed, (int)word_len);
    }
    return false;
}

static bool sur_guard_on_update(
    NidoSurGuard *sg,
    NidoCoreState *st,
    const char *new_str,
    guint cursor_pos,
    guint anchor_pos,
    bool pending_in_flight
) {
    const char *txt = new_str ? new_str : "";
    bool same_text = sg->has_last && sg->last_text && (strcmp(txt, sg->last_text) == 0);
    bool cursor_moved = same_text && (cursor_pos != sg->last_cursor || anchor_pos != sg->last_anchor);
    guint sel_start = cursor_pos < anchor_pos ? cursor_pos : anchor_pos;
    bool has_active = (st->shadow[0] != '\0') || (st->post_commit_state != 0) ||
                      (st->session && g_vl.is_composing(st->session));

    bool matches_shadow = false;
    if (st->shadow[0] != '\0') {
        size_t sha_len = utf8_codepoint_len(st->shadow);
        matches_shadow = (sha_len > 0) &&
                         surrounding_matches_tail(txt, sel_start, st->shadow, (int)sha_len);
    }
    bool matches_post = (st->post_commit_state != 0 && cursor_pos == anchor_pos) &&
                        surrounding_matches_post_commit(st, txt, cursor_pos);

    bool did_reset = false;
    if (!pending_in_flight && has_active) {
        if (cursor_pos != anchor_pos) {
            if (!matches_shadow) {
                core_reset(st);
                sg->expect_updates = false;
                did_reset = true;
            } else {
                sg->expect_updates = false;
            }
        } else if (cursor_moved) {
            if (sg->expect_updates && (matches_shadow || matches_post)) {
                sg->expect_updates = false;
            } else {
                core_reset(st);
                sg->expect_updates = false;
                did_reset = true;
            }
        } else if (!same_text) {
            if (matches_shadow || matches_post) {
                sg->expect_updates = false;
            } else if (!sg->expect_updates) {
                core_reset(st);
                did_reset = true;
            }
        }
    } else if (matches_shadow || matches_post) {
        sg->expect_updates = false;
    }

    if (!same_text || !sg->last_text) {
        g_free(sg->last_text);
        sg->last_text = g_strdup(txt);
    }
    sg->last_cursor = cursor_pos;
    sg->last_anchor = anchor_pos;
    sg->has_last = true;
    return did_reset;
}

static bool sur_guard_before_key(
    NidoSurGuard *sg,
    NidoCoreState *st,
    guint keyval,
    const char *sur_str,
    guint cursor_pos,
    guint anchor_pos,
    gint64 now_us
) {
    if (!sg->has_last || !sur_str) {
        return false;
    }
    bool settled = !sg->expect_updates ||
                   (sg->last_key_time_us > 0 &&
                    (now_us - sg->last_key_time_us) >= NIDO_SURROUNDING_SYNC_GRACE_US);
    guint sel_start = cursor_pos < anchor_pos ? cursor_pos : anchor_pos;

    if (st->shadow[0] != '\0') {
        size_t sha_len = utf8_codepoint_len(st->shadow);
        bool matches = (sha_len > 0) &&
                       surrounding_matches_tail(sur_str, sel_start, st->shadow, (int)sha_len);
        if (cursor_pos != anchor_pos && !matches) {
            core_reset(st);
            sg->expect_updates = false;
            return true;
        }
        if (settled && !matches) {
            core_reset(st);
            sg->expect_updates = false;
            return true;
        }
    } else if (st->post_commit_state != 0 && keyval == IBUS_KEY_BackSpace) {
        if (cursor_pos != anchor_pos) {
            core_reset(st);
            sg->expect_updates = false;
            return true;
        }
        if (settled && !surrounding_matches_post_commit(st, sur_str, cursor_pos)) {
            core_reset(st);
            sg->expect_updates = false;
            return true;
        }
    }
    return false;
}

/* ============================================================================
 * Zero-Underline Mid-Word & Existing-Word Surrounding Adoption (v2.0.11 Parity)
 * ============================================================================ */

static bool core_is_ime_modifier_key(const NidoCoreState *st, guint keyval) {
    if (st->method == VIELOTUS_INPUT_VNI) {
        return (keyval >= '0' && keyval <= '9');
    }
    char lower = vl_ascii_tolower((char)keyval);
    return (lower == 's' || lower == 'f' || lower == 'r' || lower == 'x' || lower == 'j' ||
            lower == 'w' || lower == 'a' || lower == 'e' || lower == 'o' || lower == 'd' || lower == 'z');
}

static bool try_adopt_candidate_word(
    NidoCoreState *st,
    const char *word_utf8,
    guint word_cp,
    guint keyval,
    const char *full_sur_str,
    char *out_after,
    size_t out_after_cap
) {
    char canon_raw[256];
    vl_raw_keys_for_utf8(word_utf8, st->method == VIELOTUS_INPUT_VNI, canon_raw, sizeof(canon_raw));
    if (canon_raw[0] == '\0') {
        return false;
    }
    g_vl.reset_session(st->session);
    if (g_vl.append_context && (vl_has_non_ascii(word_utf8) || vl_has_non_ascii(full_sur_str))) {
        g_vl.append_context(st->session, "ự");
    }
    for (size_t i = 0; canon_raw[i] != '\0'; i++) {
        g_vl.feed_char(st->session, (uint32_t)(unsigned char)canon_raw[i]);
    }
    const char *reconstructed = g_vl.get_composing_text(st->session);
    if (g_vl.is_composing(st->session) && reconstructed && strcmp(reconstructed, word_utf8) == 0) {
        g_vl.feed_char(st->session, (uint32_t)keyval);
        const char *after_comp = g_vl.get_composing_text(st->session);
        if (g_vl.is_composing(st->session) &&
            after_comp &&
            utf8_codepoint_len(after_comp) == (size_t)word_cp &&
            strcmp(after_comp, word_utf8) != 0 &&
            vl_has_non_ascii(after_comp)) {
            g_strlcpy(out_after, after_comp, out_after_cap);
            return true;
        }
    }
    g_vl.reset_session(st->session);
    return false;
}

/*
 * When idle and the user presses a Telex/VNI modifier inside or right after an
 * existing word (e.g. `gõ thử đư|c` + `r` -> `gõ thử đử|c`, or `chao|` + `f` -> `chào|`),
 * safely reconstruct the Vietnamese syllable and compute an atomic zero-underline
 * delete_surrounding_text + commit_text edit. Returns false for plain English words
 * such as `hello|` + `s` -> `hellos`.
 */
static bool core_try_adopt_surrounding_modifier(
    NidoCoreState *st,
    guint keyval,
    const char *sur_str,
    guint cursor_pos,
    guint anchor_pos,
    gint *out_del_offset,
    guint *out_del_len,
    gchar **out_commit_text
) {
    if (!st || !st->session || !sur_str || sur_str[0] == '\0' || cursor_pos != anchor_pos) {
        return false;
    }
    if (g_vl.is_composing(st->session) || st->shadow[0] != '\0') {
        return false;
    }
    if (!core_is_ime_modifier_key(st, keyval)) {
        return false;
    }

    size_t total_chars = utf8_codepoint_len(sur_str);
    if (cursor_pos > (guint)total_chars) {
        cursor_pos = (guint)total_chars;
    }

    const gchar *cursor_ptr = g_utf8_offset_to_pointer(sur_str, (glong)cursor_pos);
    const gchar *word_start_ptr = cursor_ptr;
    guint left_chars = 0;
    while (word_start_ptr > sur_str && left_chars < 15) {
        const gchar *prev_p = g_utf8_prev_char(word_start_ptr);
        if (!g_unichar_isalpha(g_utf8_get_char(prev_p))) {
            break;
        }
        word_start_ptr = prev_p;
        left_chars++;
    }

    const gchar *word_end_ptr = cursor_ptr;
    guint right_chars = 0;
    while (*word_end_ptr && right_chars < 15) {
        if (!g_unichar_isalpha(g_utf8_get_char(word_end_ptr))) {
            break;
        }
        word_end_ptr = g_utf8_next_char(word_end_ptr);
        right_chars++;
    }

    /* Priority 1: Transform the slice strictly to the left of the cursor so mid-word
     * edits like `gõ thử đư|c` + `r` -> `gõ thử đử|c` preserve the cursor position. */
    if (left_chars >= 1 && left_chars <= 15) {
        size_t left_bytes = (size_t)(cursor_ptr - word_start_ptr);
        if (left_bytes > 0 && left_bytes < 128) {
            char left_word[128];
            char after_word[NIDO_SHADOW_MAX];
            memcpy(left_word, word_start_ptr, left_bytes);
            left_word[left_bytes] = '\0';
            if (try_adopt_candidate_word(st, left_word, left_chars, keyval, sur_str, after_word, sizeof(after_word))) {
                int bs = 0;
                const char *ins = "";
                compute_utf8_lcp_diff(left_word, after_word, &bs, &ins);
                if (bs > 0 && (guint)bs <= cursor_pos) {
                    core_clear_post_commit(st);
                    core_set_shadow(st, after_word);
                    *out_del_offset = -bs;
                    *out_del_len = (guint)bs;
                    *out_commit_text = g_strdup(ins ? ins : "");
                    return true;
                }
            }
        }
    }

    /* Priority 2: If mid-word modifier needs both sides (e.g. `h|oa` + `f` -> `hòa`),
     * replace the full surrounding syllable atomically. */
    guint total_word_cp = left_chars + right_chars;
    size_t word_bytes = (size_t)(word_end_ptr - word_start_ptr);
    if (right_chars > 0 && total_word_cp >= 1 && total_word_cp <= 15 &&
        word_bytes > 0 && word_bytes < 128 &&
        cursor_pos >= left_chars && (cursor_pos - left_chars + total_word_cp) <= (guint)total_chars) {
        char full_word[128];
        char after_word[NIDO_SHADOW_MAX];
        memcpy(full_word, word_start_ptr, word_bytes);
        full_word[word_bytes] = '\0';
        if (try_adopt_candidate_word(st, full_word, total_word_cp, keyval, sur_str, after_word, sizeof(after_word))) {
            core_clear_post_commit(st);
            core_set_shadow(st, after_word);
            *out_del_offset = -(gint)left_chars;
            *out_del_len = total_word_cp;
            *out_commit_text = g_strdup(after_word);
            return true;
        }
    }

    return false;
}

/* ============================================================================
 * Key Classification & Core Zero-Underline State Machine
 * ============================================================================ */

static bool purpose_requires_passthrough(guint purpose) {
    switch (purpose) {
        case IBUS_INPUT_PURPOSE_PASSWORD:
        case IBUS_INPUT_PURPOSE_PIN:
        case IBUS_INPUT_PURPOSE_DIGITS:
        case IBUS_INPUT_PURPOSE_NUMBER:
        case IBUS_INPUT_PURPOSE_PHONE:
            return true;
        default:
            return false;
    }
}

static bool keyval_is_modifier_only(guint keyval) {
    if (keyval >= 0xFFE1u && keyval <= 0xFFEEu) {
        return true;
    }
    return keyval == IBUS_KEY_ISO_Level3_Shift ||
           keyval == IBUS_KEY_ISO_Level5_Shift ||
           keyval == IBUS_KEY_Num_Lock ||
           keyval == IBUS_KEY_Scroll_Lock;
}

static bool keyval_is_boundary_commit_key(guint keyval) {
    return (keyval == IBUS_KEY_Return || keyval == IBUS_KEY_KP_Enter ||
            keyval == IBUS_KEY_Linefeed || keyval == '\r' || keyval == '\n' ||
            keyval == IBUS_KEY_Tab || keyval == IBUS_KEY_ISO_Left_Tab ||
            keyval == IBUS_KEY_KP_Tab || keyval == '\t');
}

static bool keyval_is_navigation_reset_key(guint keyval) {
    if ((keyval >= IBUS_KEY_Home && keyval <= IBUS_KEY_Insert) ||
        (keyval >= IBUS_KEY_KP_Home && keyval <= IBUS_KEY_KP_Delete)) {
        return true;
    }
    switch (keyval) {
        case IBUS_KEY_Delete:
        case IBUS_KEY_Escape:
        case 0x1bu:
            return true;
        default:
            return (keyval >= IBUS_KEY_F1 && keyval <= IBUS_KEY_F35);
    }
}

static void core_commit_active_diff(
    NidoCoreState *st,
    const NidoSink *sink,
    void *sink_ctx
) {
    if (!g_vl.is_composing(st->session)) {
        core_clear_shadow(st);
        return;
    }
    int32_t act = g_vl.commit_word(st->session);
    const char *final_txt = g_vl.get_last_text(st->session);
    if (act == VIELOTUS_ACTION_COMMIT && final_txt && st->shadow[0] != '\0') {
        char prev_shadow[NIDO_SHADOW_MAX];
        g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
        int bs = 0;
        const char *ins = "";
        compute_utf8_lcp_diff(prev_shadow, final_txt, &bs, &ins);
        core_clear_shadow(st);
        if (bs > 0 || (ins && *ins)) {
            sink->delete_and_commit(sink_ctx, bs, ins, prev_shadow);
        }
    } else {
        core_clear_shadow(st);
    }
}

/*
 * Process one key-press through the zero-underline state machine.
 * Returns TRUE if the key was consumed (output already sent to `sink`),
 * or FALSE if the caller should pass the physical key straight through.
 */
static bool core_process_key_press(
    NidoCoreState *st,
    guint keyval,
    guint state,
    const NidoSink *sink,
    void *sink_ctx,
    bool force_commit_passthrough
) {
    if (!st->session) {
        return false;
    }

    /* Per-app passthrough rule from typing-modes.yml */
    if (st->active_presentation_mode == VL_PRESENTATION_PASSTHROUGH) {
        core_reset(st);
        return false;
    }

    if (keyval_is_modifier_only(keyval)) {
        return false;
    }

    /* Shortcuts (Ctrl/Alt/Super/Meta/Hyper): never intercept */
    const guint shortcut_mask = IBUS_CONTROL_MASK | IBUS_MOD1_MASK | IBUS_MOD4_MASK |
                                IBUS_SUPER_MASK | IBUS_META_MASK | IBUS_HYPER_MASK;
    if (state & shortcut_mask) {
        core_commit_active_diff(st, sink, sink_ctx);
        core_clear_post_commit(st);
        return false;
    }

    if (purpose_requires_passthrough(st->purpose)) {
        core_reset(st);
        return false;
    }

    /* Navigation / escape keys clear active word composition */
    if (keyval_is_navigation_reset_key(keyval)) {
        core_reset(st);
        if (g_vl.append_context) {
            g_vl.append_context(st->session, "");
        }
        return false;
    }

    /* Return / Tab commit active syllable (only modifying screen if Smart Bilingual/Macro rewrote it) */
    if (keyval_is_boundary_commit_key(keyval)) {
        core_clear_post_commit(st);
        if (keyval == IBUS_KEY_Return || keyval == IBUS_KEY_KP_Enter ||
            keyval == IBUS_KEY_Linefeed || keyval == '\r' || keyval == '\n') {
            vl_dev_code_on_newline(&st->dev_code);
        }
        core_commit_active_diff(st, sink, sink_ctx);
        if (g_vl.append_context) {
            g_vl.append_context(st->session, "");
        }
        return false;
    }

    /* Developer Mode inline (`...`) & fenced (```...```) code bypass FSM */
    if (st->cfg.developer_mode) {
        if (keyval == '`') {
            core_clear_post_commit(st);
            if (g_vl.is_composing(st->session)) {
                core_commit_active_diff(st, sink, sink_ctx);
            }
            vl_dev_code_on_backtick(&st->dev_code);
            g_vl.feed_char(st->session, '`');
            return false;
        }
        if (vl_dev_code_is_bypassing(&st->dev_code)) {
            vl_dev_code_on_non_backtick(&st->dev_code);
            return false;
        }
    }

    /* BackSpace during active word composition OR post-commit rehydration (Space -> BS -> BS) */
    if (keyval == IBUS_KEY_BackSpace) {
        bool was_composing = g_vl.is_composing(st->session);
        int32_t act = g_vl.feed_backspace(st->session);
        const char *new_comp = g_vl.get_composing_text(st->session);

        if (act == VIELOTUS_ACTION_PASSTHROUGH) {
            core_clear_shadow(st);
            if (!was_composing && st->post_commit_state == 1) {
                st->post_commit_state = 2;
            } else {
                core_clear_post_commit(st);
            }
            return false;
        }
        if (act == VIELOTUS_ACTION_RESET) {
            core_clear_shadow(st);
            core_clear_post_commit(st);
            return false;
        }

        char prev_shadow[NIDO_SHADOW_MAX];
        if (st->shadow[0] != '\0') {
            g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
        } else if (!was_composing && st->last_committed[0] != '\0') {
            g_strlcpy(prev_shadow, st->last_committed, sizeof(prev_shadow));
        } else {
            prev_shadow[0] = '\0';
        }
        core_clear_post_commit(st);

        int bs = 0;
        const char *ins = "";
        if (prev_shadow[0] != '\0') {
            compute_utf8_lcp_diff(prev_shadow, new_comp ? new_comp : "", &bs, &ins);
        } else {
            bs = g_vl.get_last_backspaces(st->session);
            ins = g_vl.get_last_text(st->session);
            if (!ins) {
                ins = "";
            }
        }
        core_set_shadow(st, new_comp);

        /* Fast path: deleting exactly 1 character with empty replacement -> let native BackSpace run */
        if (bs == 1 && (!ins || *ins == '\0') && !force_commit_passthrough) {
            return false;
        }
        if (bs == 0 && (!ins || *ins == '\0')) {
            return true;
        }
        sink->delete_and_commit(sink_ctx, bs, ins, prev_shadow);
        return true;
    }

    /* Numpad digit/operator normalization (VNI Numpad digits emit literal '0'..'9') */
    bool is_numpad_digit = vl_is_numpad_digit_keysym((uint32_t)keyval);
    guint norm_kv = (guint)vl_normalize_numpad_keysym((uint32_t)keyval);

    /* Non-ASCII XKB `vn` / VM-forwarded Unicode keysyms & combining marks (NFD -> NFC) */
    if (norm_kv >= 128 && !is_numpad_digit) {
        uint32_t uni_cp = (uint32_t)ibus_keyval_to_unicode(norm_kv);
        char mapped_seq[16];
        if (vl_keysym_to_raw_seq(norm_kv, uni_cp, st->method == VIELOTUS_INPUT_VNI, mapped_seq, sizeof(mapped_seq))) {
            core_clear_post_commit(st);
            guint clean_state = state & ~(IBUS_SHIFT_MASK | IBUS_LOCK_MASK);
            for (size_t i = 0; mapped_seq[i] != '\0'; i++) {
                guint sub_kv = (guint)(unsigned char)mapped_seq[i];
                bool sub_consumed = core_process_key_press(st, sub_kv, clean_state, sink, sink_ctx, true);
                if (!sub_consumed) {
                    char single[2] = { mapped_seq[i], '\0' };
                    sink->commit_text(sink_ctx, single);
                }
            }
            return true;
        }
    }

    /* Translate normalized keyval to Unicode scalar */
    gunichar ch = (norm_kv >= 0x20 && norm_kv < 0x7F)
        ? (gunichar)norm_kv
        : ibus_keyval_to_unicode(norm_kv);
    if (ch == 0 || ch < 0x20 || ch == 0x7F) {
        core_reset(st);
        return false;
    }

    /* Any non-BackSpace printable key disarms post-commit BackSpace rehydration */
    core_clear_post_commit(st);
    if (st->cfg.developer_mode) {
        vl_dev_code_on_non_backtick(&st->dev_code);
    }

    char typed_utf8[8];
    gint typed_len = g_unichar_to_utf8(ch, typed_utf8);
    typed_utf8[typed_len] = '\0';

    /* VNI Numpad digit: always commit current word and emit literal digit (never tone/vowel mark) */
    if (st->method == VIELOTUS_INPUT_VNI && is_numpad_digit) {
        if (g_vl.is_composing(st->session)) {
            int32_t c_act = g_vl.commit_word(st->session);
            const char *final_txt = g_vl.get_last_text(st->session);
            char prev_shadow[NIDO_SHADOW_MAX];
            g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
            int bs = 0;
            const char *ins = "";
            if (c_act == VIELOTUS_ACTION_COMMIT && final_txt && prev_shadow[0] != '\0') {
                compute_utf8_lcp_diff(prev_shadow, final_txt, &bs, &ins);
            }
            core_clear_shadow(st);
            if (bs > 0 || (ins && *ins)) {
                gchar *combined = g_strconcat(ins ? ins : "", typed_utf8, NULL);
                if (bs > 0) {
                    sink->delete_and_commit(sink_ctx, bs, combined, prev_shadow);
                } else {
                    sink->commit_text(sink_ctx, combined);
                }
                g_free(combined);
                return true;
            }
        } else {
            core_clear_shadow(st);
        }
        if (force_commit_passthrough) {
            sink->commit_text(sink_ctx, typed_utf8);
            return true;
        }
        return false;
    }

    /* Prevent runaway composition buffers (>48 bytes) */
    if (g_vl.is_composing(st->session)) {
        const char *raw_check = g_vl.get_raw_text ? g_vl.get_raw_text(st->session) : NULL;
        const char *comp_check = g_vl.get_composing_text(st->session);
        if ((raw_check && strlen(raw_check) >= 48) ||
            (comp_check && strlen(comp_check) >= 48)) {
            core_commit_active_diff(st, sink, sink_ctx);
        }
    }

    /* Telex post-coda invalid tone replacement (e.g. `t/c/p/h` + `f/r/x` then `s/j`) */
    if (st->method == VIELOTUS_INPUT_TELEX && g_vl.is_composing(st->session) &&
        g_vl.get_raw_text && ch < 0x80) {
        char lower_key = vl_ascii_tolower((char)ch);
        uint8_t key_tone = vl_telex_tone_for_key(lower_key);
        const char *raw_before = g_vl.get_raw_text(st->session);
        const char *comp_before = g_vl.get_composing_text(st->session);
        if ((key_tone == 2 || key_tone == 5) && raw_before && comp_before && vl_has_non_ascii(comp_before)) {
            size_t rlen = strlen(raw_before);
            size_t clen = strlen(comp_before);
            uint8_t active_tone = vl_active_tone_of_utf8(comp_before);
            if (active_tone == 0 && rlen >= 3 && clen >= 3 && rlen < 512) {
                char last_r = vl_ascii_tolower(raw_before[rlen - 1]);
                char prev_c = vl_ascii_tolower(comp_before[clen - 2]);
                if ((last_r == 'f' || last_r == 'r' || last_r == 'x') &&
                    vl_ascii_tolower(comp_before[clen - 1]) == last_r &&
                    (prev_c == 't' || prev_c == 'c' || prev_c == 'p' || prev_c == 'h')) {
                    char prev_shadow[NIDO_SHADOW_MAX];
                    g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
                    char fixed_raw[512];
                    memcpy(fixed_raw, raw_before, rlen);
                    fixed_raw[rlen - 1] = (char)ch;
                    fixed_raw[rlen] = '\0';
                    core_replay_raw_keys(st, fixed_raw);
                    const char *new_comp = g_vl.get_composing_text(st->session);
                    int bs = 0;
                    const char *ins = "";
                    compute_utf8_lcp_diff(prev_shadow, new_comp ? new_comp : "", &bs, &ins);
                    core_set_shadow(st, new_comp);
                    if (bs > 0) {
                        sink->delete_and_commit(sink_ctx, bs, ins, prev_shadow);
                    } else if (ins && *ins) {
                        sink->commit_text(sink_ctx, ins);
                    }
                    return true;
                }
            }
        }
    }

    int32_t capi_bs = 0;
    const char *capi_txt = NULL;
    int32_t act = vl_feed_char(st->session, (uint32_t)ch, &capi_bs, &capi_txt);

    /* Telex vowel-tone swap edge case (e.g. `u` + tone + `oo`, `i/y` + tone + `ee`) */
    if (st->method == VIELOTUS_INPUT_TELEX && g_vl.is_composing(st->session) &&
        g_vl.get_raw_text && ch < 0x80) {
        char lower_key = vl_ascii_tolower((char)ch);
        if (lower_key == 'a' || lower_key == 'e' || lower_key == 'o') {
            const char *raw_after = g_vl.get_raw_text(st->session);
            const char *comp_after = g_vl.get_composing_text(st->session);
            if (raw_after && comp_after) {
                size_t rlen = strlen(raw_after);
                size_t clen = strlen(comp_after);
                if (rlen >= 3 && rlen < 512 && clen >= 2 &&
                    vl_ascii_tolower(raw_after[rlen - 1]) == lower_key &&
                    vl_ascii_tolower(raw_after[rlen - 3]) == lower_key &&
                    vl_ascii_tolower(comp_after[clen - 1]) == lower_key) {
                    uint8_t mid_tone = vl_telex_tone_for_key(vl_ascii_tolower(raw_after[rlen - 2]));
                    bool allow_swap = (mid_tone == 5) ||
                        (mid_tone > 0 && lower_key == 'e' && rlen >= 4 &&
                         (vl_ascii_tolower(raw_after[rlen - 4]) == 'i' || vl_ascii_tolower(raw_after[rlen - 4]) == 'y') &&
                         vl_ascii_tolower(raw_after[0]) != 'p') ||
                        (mid_tone > 0 && lower_key == 'o' && rlen >= 4 &&
                         vl_ascii_tolower(raw_after[rlen - 4]) == 'u');
                    if (allow_swap && vl_active_tone_of_utf8(comp_after) == mid_tone) {
                        char swapped_raw[512];
                        memcpy(swapped_raw, raw_after, rlen + 1);
                        char tmp = swapped_raw[rlen - 2];
                        swapped_raw[rlen - 2] = swapped_raw[rlen - 1];
                        swapped_raw[rlen - 1] = tmp;
                        core_replay_raw_keys(st, swapped_raw);
                        act = VIELOTUS_ACTION_REPLACE;
                    }
                }
            }
        }
    }

    if (act == VIELOTUS_ACTION_PASSTHROUGH || act == VIELOTUS_ACTION_RESET) {
        core_clear_shadow(st);
        return false;
    }

    if (act == VIELOTUS_ACTION_CONSUMED) {
        const char *new_comp = g_vl.get_composing_text(st->session);
        if (new_comp) {
            char prev_shadow[NIDO_SHADOW_MAX];
            g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
            int bs = 0;
            const char *ins = "";
            if (prev_shadow[0] != '\0') {
                compute_utf8_lcp_diff(prev_shadow, new_comp, &bs, &ins);
            } else {
                bs = capi_bs > 0 ? capi_bs : 0;
                ins = capi_txt ? capi_txt : new_comp;
            }
            core_set_shadow(st, new_comp);
            if (bs > 0) {
                sink->delete_and_commit(sink_ctx, bs, ins, prev_shadow);
            } else if (ins && *ins) {
                sink->commit_text(sink_ctx, ins);
            }
        }
        return true;
    }

    if (act == VIELOTUS_ACTION_REPLACE) {
        const char *new_comp = g_vl.get_composing_text(st->session);
        char prev_shadow[NIDO_SHADOW_MAX];
        g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
        int bs = 0;
        const char *ins = "";

        if (prev_shadow[0] != '\0' && new_comp != NULL) {
            compute_utf8_lcp_diff(prev_shadow, new_comp, &bs, &ins);
        } else {
            bs = capi_bs > 0 ? capi_bs : 0;
            ins = capi_txt ? capi_txt : "";
        }
        core_set_shadow(st, new_comp);

        /* Fast zero-IPC path: single unchanged ASCII character appended with 0 backspaces */
        if (bs == 0 && ins && strcmp(ins, typed_utf8) == 0 && !force_commit_passthrough) {
            return false;
        }
        if (bs == 0) {
            if (ins && *ins) {
                sink->commit_text(sink_ctx, ins);
            }
            return true;
        }
        sink->delete_and_commit(sink_ctx, bs, ins, prev_shadow);
        return true;
    }

    if (act == VIELOTUS_ACTION_COMMIT) {
        const char *final_txt = capi_txt ? capi_txt : g_vl.get_last_text(st->session);
        bool is_override = g_vl.get_last_is_override ? g_vl.get_last_is_override(st->session) : false;
        char prev_shadow[NIDO_SHADOW_MAX];
        g_strlcpy(prev_shadow, st->shadow, sizeof(prev_shadow));
        int bs = 0;
        const char *ins = "";

        if (final_txt && prev_shadow[0] != '\0') {
            compute_utf8_lcp_diff(prev_shadow, final_txt, &bs, &ins);
        }
        core_clear_shadow(st);

        if (ch == ' ' && !is_override && final_txt && *final_txt) {
            core_arm_post_commit(st, final_txt);
        }

        /* 99% common case: screen already holds the exact finalized word -> pass separator natively! */
        if (bs == 0 && (!ins || *ins == '\0')) {
            return false;
        }

        /* Smart Bilingual restore or Macro expansion: emit diff + separator atomically in one commit */
        gchar *combined = g_strconcat(ins ? ins : "", typed_utf8, NULL);
        if (bs > 0) {
            sink->delete_and_commit(sink_ctx, bs, combined, prev_shadow);
        } else {
            sink->commit_text(sink_ctx, combined);
        }
        g_free(combined);
        return true;
    }

    return false;
}

/* ============================================================================
 * IBus Engine Subclass (NidoVielotusEngine) — ZERO Preedit Calls
 * ============================================================================ */

typedef struct {
    guint keyval;
    guint keycode;
    guint state;
} QueuedKey;

typedef struct _NidoVielotusEngine NidoVielotusEngine;
typedef struct _NidoVielotusEngineClass NidoVielotusEngineClass;

struct _NidoVielotusEngine {
    IBusEngine parent;

    NidoCoreState core;
    gchar *config_dir;
    time_t config_mtime;
    time_t macros_mtime;
    bool macros_had_file;
    bool engine_enabled;
    gint64 last_cfg_poll_us;
    VlPresentationRules presentation_rules;

    IBusPropList *prop_list;
    IBusProperty *prop_telex;
    IBusProperty *prop_vni;
    IBusProperty *prop_grammar;
    IBusProperty *prop_bilingual;
    IBusProperty *prop_modern;
    IBusProperty *prop_quick;
    IBusProperty *prop_relaxed;
    IBusProperty *prop_macro;
    IBusProperty *prop_dev;
    bool prop_is_en;

    /* Ordered async commit queue (prevents BS/commit race on Chromium/Firefox/Terminal) */
    gchar *pending_commit;
    guint pending_timer_id;
    int expected_uinput_bs;
    QueuedKey queue[NIDO_KEY_QUEUE_CAP];
    size_t q_head;
    size_t q_tail;
    size_t q_len;

    NidoUinput uinput;
    NidoSurGuard sur_guard;
};

struct _NidoVielotusEngineClass {
    IBusEngineClass parent;
};

#define NIDO_TYPE_VIELOTUS_ENGINE (nido_vielotus_engine_get_type())
#define NIDO_VIELOTUS_ENGINE(obj) (G_TYPE_CHECK_INSTANCE_CAST((obj), NIDO_TYPE_VIELOTUS_ENGINE, NidoVielotusEngine))

G_DEFINE_TYPE(NidoVielotusEngine, nido_vielotus_engine, IBUS_TYPE_ENGINE)

static gboolean on_pending_commit_timeout(gpointer user_data);
static void drain_key_queue(NidoVielotusEngine *self);
static void build_properties(NidoVielotusEngine *self);

static bool queue_push(NidoVielotusEngine *self, guint keyval, guint keycode, guint state) {
    if (self->q_len >= NIDO_KEY_QUEUE_CAP) {
        return false;
    }
    self->queue[self->q_tail].keyval = keyval;
    self->queue[self->q_tail].keycode = keycode;
    self->queue[self->q_tail].state = state;
    self->q_tail = (self->q_tail + 1) % NIDO_KEY_QUEUE_CAP;
    self->q_len++;
    return true;
}

static bool queue_pop(NidoVielotusEngine *self, QueuedKey *out) {
    if (self->q_len == 0) {
        return false;
    }
    *out = self->queue[self->q_head];
    self->q_head = (self->q_head + 1) % NIDO_KEY_QUEUE_CAP;
    self->q_len--;
    return true;
}

static void queue_clear(NidoVielotusEngine *self) {
    self->q_head = 0;
    self->q_tail = 0;
    self->q_len = 0;
}

static void flush_pending_now(NidoVielotusEngine *self, bool commit_text_if_any) {
    if (self->pending_timer_id != 0) {
        g_source_remove(self->pending_timer_id);
        self->pending_timer_id = 0;
    }
    self->expected_uinput_bs = 0;
    if (self->pending_commit) {
        if (commit_text_if_any && *self->pending_commit) {
            ibus_engine_commit_text(IBUS_ENGINE(self), ibus_text_new_from_string(self->pending_commit));
        }
        g_free(self->pending_commit);
        self->pending_commit = NULL;
    }
}

static void engine_sink_commit(void *ctx, const char *utf8) {
    NidoVielotusEngine *self = (NidoVielotusEngine *)ctx;
    if (utf8 && *utf8) {
        ibus_engine_commit_text(IBUS_ENGINE(self), ibus_text_new_from_string(utf8));
    }
}

static void schedule_delayed_commit(NidoVielotusEngine *self, const char *utf8, guint delay_ms) {
    g_free(self->pending_commit);
    self->pending_commit = g_strdup(utf8 ? utf8 : "");
    if (self->pending_timer_id != 0) {
        g_source_remove(self->pending_timer_id);
    }
    self->pending_timer_id = g_timeout_add(delay_ms, on_pending_commit_timeout, self);
}

static void engine_sink_delete_and_commit(void *ctx, int bs, const char *utf8, const char *prev_shadow) {
    NidoVielotusEngine *self = (NidoVielotusEngine *)ctx;
    IBusEngine *engine = IBUS_ENGINE(self);
    const char *text = utf8 ? utf8 : "";
    /* Never fall back to self->core.shadow here: callers already updated it to the NEW text. */
    const char *match_shadow = (prev_shadow && *prev_shadow) ? prev_shadow : NULL;

    if (bs <= 0) {
        if (*text) {
            ibus_engine_commit_text(engine, ibus_text_new_from_string(text));
        }
        return;
    }

    int extra_selection_bs = 0;

    /* Tier 1: Verified atomic surrounding-text replacement (0ms delay, 0 race) */
    if (match_shadow &&
        self->core.cfg.bs_backend == NIDO_BS_AUTO &&
        (engine->client_capabilities & IBUS_CAP_SURROUNDING_TEXT) &&
        self->core.purpose != IBUS_INPUT_PURPOSE_TERMINAL) {
        IBusText *stext = NULL;
        guint cursor_pos = 0;
        guint anchor_pos = 0;
        ibus_engine_get_surrounding_text(engine, &stext, &cursor_pos, &anchor_pos);
        const char *sur_str = stext ? ibus_text_get_text(stext) : NULL;

        if (sur_str && cursor_pos == anchor_pos &&
            surrounding_matches_tail(sur_str, cursor_pos, match_shadow, bs)) {
            ibus_engine_delete_surrounding_text(engine, -bs, (guint)bs);
            if (*text) {
                ibus_engine_commit_text(engine, ibus_text_new_from_string(text));
            }
            return;
        }

        /* URL bar autocomplete selection guard: 1st BackSpace clears selection */
        if (sur_str && cursor_pos != anchor_pos) {
            guint sel_start = cursor_pos < anchor_pos ? cursor_pos : anchor_pos;
            if (surrounding_matches_tail(sur_str, sel_start, match_shadow, bs)) {
                extra_selection_bs = 1;
            }
        }
    }

    int total_bs = bs + extra_selection_bs;
    guint delay_ms = (self->core.cfg.bs_delay_us + 999u) / 1000u;

    /* Tier 3 (opt-in): /dev/uinput virtual BackSpace with echo-barrier sync */
    if (self->core.cfg.bs_backend == NIDO_BS_UINPUT) {
        if (uinput_send_backspaces(&self->uinput, total_bs)) {
            self->expected_uinput_bs = total_bs;
            guint timeout_ms = delay_ms > 25u ? delay_ms : 25u;
            schedule_delayed_commit(self, text, timeout_ms);
            return;
        }
    }

    /* Tier 2 (default fallback): IBus forward_key_event + ordered FIFO commit timer */
    for (int i = 0; i < total_bs; i++) {
        ibus_engine_forward_key_event(engine, IBUS_KEY_BackSpace, NIDO_BS_KEYCODE_EVDEV, 0);
        ibus_engine_forward_key_event(engine, IBUS_KEY_BackSpace, NIDO_BS_KEYCODE_EVDEV, IBUS_RELEASE_MASK);
    }

    if (delay_ms == 0) {
        if (*text) {
            ibus_engine_commit_text(engine, ibus_text_new_from_string(text));
        }
    } else {
        schedule_delayed_commit(self, text, delay_ms);
    }
}

static gboolean on_pending_commit_timeout(gpointer user_data) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(user_data);
    self->pending_timer_id = 0;
    self->expected_uinput_bs = 0;

    if (self->pending_commit) {
        gchar *to_commit = self->pending_commit;
        self->pending_commit = NULL;
        if (*to_commit) {
            ibus_engine_commit_text(IBUS_ENGINE(self), ibus_text_new_from_string(to_commit));
        }
        g_free(to_commit);
    }

    drain_key_queue(self);
    return G_SOURCE_REMOVE;
}

static void drain_key_queue(NidoVielotusEngine *self) {
    const NidoSink sink = {
        .commit_text = engine_sink_commit,
        .delete_and_commit = engine_sink_delete_and_commit,
    };

    QueuedKey qk;
    while (!self->pending_commit && queue_pop(self, &qk)) {
        bool consumed = core_process_key_press(&self->core, qk.keyval, qk.state, &sink, self, true);
        if (!consumed) {
            const guint shortcut_mask = IBUS_CONTROL_MASK | IBUS_MOD1_MASK | IBUS_MOD4_MASK |
                                        IBUS_SUPER_MASK | IBUS_META_MASK | IBUS_HYPER_MASK;
            guint norm_kv = (guint)vl_normalize_numpad_keysym((uint32_t)qk.keyval);
            gunichar ch = (norm_kv >= 0x20 && norm_kv < 0x7F)
                ? (gunichar)norm_kv
                : ibus_keyval_to_unicode(norm_kv);
            if (!(qk.state & shortcut_mask) && ch >= 0x20 && ch != 0x7F &&
                qk.keyval != IBUS_KEY_BackSpace && !keyval_is_boundary_commit_key(qk.keyval) &&
                !keyval_is_navigation_reset_key(qk.keyval)) {
                char u8[8];
                gint n = g_unichar_to_utf8(ch, u8);
                u8[n] = '\0';
                ibus_engine_commit_text(IBUS_ENGINE(self), ibus_text_new_from_string(u8));
            } else {
                ibus_engine_forward_key_event(IBUS_ENGINE(self), qk.keyval, qk.keycode, qk.state);
                ibus_engine_forward_key_event(IBUS_ENGINE(self), qk.keyval, qk.keycode, qk.state | IBUS_RELEASE_MASK);
            }
        }
    }
}

static void update_prop_toggle(NidoVielotusEngine *self, IBusProperty *prop, bool checked) {
    if (!prop) {
        return;
    }
    ibus_property_set_state(prop, checked ? PROP_STATE_CHECKED : PROP_STATE_UNCHECKED);
    ibus_engine_update_property(IBUS_ENGINE(self), prop);
}

static void sync_properties_ui(NidoVielotusEngine *self) {
    bool want_en = vl_ui_is_english(self->core.cfg.ui_language);
    if (!self->prop_list || want_en != self->prop_is_en) {
        build_properties(self);
        if (self->engine_enabled && self->prop_list) {
            ibus_engine_register_properties(IBUS_ENGINE(self), self->prop_list);
        }
        return;
    }
    update_prop_toggle(self, self->prop_telex, self->core.method == VIELOTUS_INPUT_TELEX);
    update_prop_toggle(self, self->prop_vni, self->core.method == VIELOTUS_INPUT_VNI);
    update_prop_toggle(self, self->prop_grammar, self->core.cfg.grammar_profile == VIELOTUS_GRAMMAR_SEN_FLEX);
    update_prop_toggle(self, self->prop_bilingual, self->core.cfg.smart_bilingual);
    update_prop_toggle(self, self->prop_modern, self->core.cfg.modern_orthography);
    update_prop_toggle(self, self->prop_quick, self->core.cfg.quick_telex);
    update_prop_toggle(self, self->prop_relaxed, self->core.cfg.relaxed_coda);
    update_prop_toggle(self, self->prop_macro, self->core.cfg.macro_enabled);
    update_prop_toggle(self, self->prop_dev, self->core.cfg.developer_mode);
}

static void reload_user_config_if_changed(NidoVielotusEngine *self, bool force, bool apply_input_method) {
    if (!self->config_dir) {
        return;
    }

    gchar *cfg_path = g_build_filename(self->config_dir, "config.env", NULL);
    struct stat st;
    if (stat(cfg_path, &st) == 0) {
        if (force || st.st_mtime != self->config_mtime) {
            gchar *content = NULL;
            if (g_file_get_contents(cfg_path, &content, NULL, NULL)) {
                parse_config_env_text(content, &self->core.cfg);
                g_free(content);
                self->config_mtime = st.st_mtime;
                if (apply_input_method && self->core.cfg.has_input_method) {
                    self->core.method = self->core.cfg.config_method;
                }
                core_apply_config(&self->core);
                sync_properties_ui(self);
            }
        }
    } else if (force) {
        nido_config_defaults(&self->core.cfg);
        core_apply_config(&self->core);
        sync_properties_ui(self);
    }
    g_free(cfg_path);

    if (g_vl.load_macros_json) {
        gchar *macro_path = g_build_filename(self->config_dir, "macros.json", NULL);
        if (stat(macro_path, &st) == 0 && st.st_size >= 2 && st.st_size <= 262144) {
            if (force || !self->macros_had_file || st.st_mtime != self->macros_mtime) {
                gchar *json_txt = NULL;
                if (g_file_get_contents(macro_path, &json_txt, NULL, NULL)) {
                    if (g_vl.load_macros_json(json_txt)) {
                        self->macros_mtime = st.st_mtime;
                        self->macros_had_file = true;
                    }
                    g_free(json_txt);
                }
            }
        } else if ((force || self->macros_had_file) && g_vl.reset_default_macros) {
            g_vl.reset_default_macros();
            self->macros_mtime = 0;
            self->macros_had_file = false;
        }
        g_free(macro_path);
    }

    gchar *rules_path = g_build_filename(self->config_dir, "typing-modes.yml", NULL);
    vl_presentation_rules_reload_if_changed(&self->presentation_rules, rules_path);
    g_free(rules_path);
}

static IBusProperty *make_toggle_prop(const char *key, const char *label, bool checked) {
    return ibus_property_new(
        key,
        PROP_TYPE_TOGGLE,
        ibus_text_new_from_string(label),
        NULL,
        NULL,
        TRUE,
        TRUE,
        checked ? PROP_STATE_CHECKED : PROP_STATE_UNCHECKED,
        NULL
    );
}

static void build_properties(NidoVielotusEngine *self) {
    if (self->prop_list) {
        g_object_unref(self->prop_list);
        self->prop_list = NULL;
    }
    self->prop_list = ibus_prop_list_new();
    g_object_ref_sink(self->prop_list);

    const bool is_en = vl_ui_is_english(self->core.cfg.ui_language);
    self->prop_is_en = is_en;

    self->prop_telex = make_toggle_prop(
        "im-telex",
        is_en ? "Input Method: Telex (vieetj → việt)" : "Kiểu gõ: Telex Tự Do (vieetj → việt)",
        self->core.method == VIELOTUS_INPUT_TELEX
    );
    self->prop_vni = make_toggle_prop(
        "im-vni",
        is_en ? "Input Method: VNI (viet65 → việt)" : "Kiểu gõ: VNI Chuẩn (viet65 → việt)",
        self->core.method == VIELOTUS_INPUT_VNI
    );
    self->prop_bilingual = make_toggle_prop(
        "smart-bilingual",
        is_en ? "Smart Bilingual (EN–VI)" : "Song ngữ thông minh Anh-Việt",
        self->core.cfg.smart_bilingual
    );
    self->prop_grammar = make_toggle_prop(
        "grammar-flex",
        is_en ? "Tone-First Typing (SEN Flex)" : "Gõ dấu tự do (SEN Flex)",
        self->core.cfg.grammar_profile == VIELOTUS_GRAMMAR_SEN_FLEX
    );
    self->prop_modern = make_toggle_prop(
        "modern-orthography",
        is_en ? "Modern Orthography (oà, uý)" : "Chính tả hiện đại (oà, uý)",
        self->core.cfg.modern_orthography
    );
    self->prop_macro = make_toggle_prop(
        "macro-enabled",
        is_en ? "Text Shortcuts (Smart Macro)" : "Gõ tắt thông minh (Smart Macro)",
        self->core.cfg.macro_enabled
    );
    self->prop_quick = make_toggle_prop(
        "quick-telex",
        is_en ? "Quick Consonants (cc=ch, gg=gi...)" : "Gõ tắt phụ âm (Quick Telex)",
        self->core.cfg.quick_telex
    );
    self->prop_relaxed = make_toggle_prop(
        "relaxed-coda",
        is_en ? "Relaxed Coda (-g→-ng, -h, -k)" : "Gõ nhanh vần cuối (-g→-ng, -h, -k)",
        self->core.cfg.relaxed_coda
    );
    self->prop_dev = make_toggle_prop(
        "developer-mode",
        is_en ? "Code / IDE Mode (skip inside `code`)" : "Chế độ lập trình (bỏ qua trong `code`)",
        self->core.cfg.developer_mode
    );

    IBusProperty *sep_im = ibus_property_new(
        "sep-im", PROP_TYPE_SEPARATOR, NULL, NULL, NULL, TRUE, TRUE, PROP_STATE_UNCHECKED, NULL
    );
    IBusProperty *sep_cfg = ibus_property_new(
        "sep-1", PROP_TYPE_SEPARATOR, NULL, NULL, NULL, TRUE, TRUE, PROP_STATE_UNCHECKED, NULL
    );
    IBusProperty *setup = ibus_property_new(
        "setup",
        PROP_TYPE_NORMAL,
        ibus_text_new_from_string(is_en ? "Sen Việt Settings..." : "Cài đặt Sen Việt..."),
        "preferences-system",
        NULL,
        TRUE,
        TRUE,
        PROP_STATE_UNCHECKED,
        NULL
    );

    ibus_prop_list_append(self->prop_list, self->prop_telex);
    ibus_prop_list_append(self->prop_list, self->prop_vni);
    ibus_prop_list_append(self->prop_list, sep_im);
    ibus_prop_list_append(self->prop_list, self->prop_bilingual);
    ibus_prop_list_append(self->prop_list, self->prop_grammar);
    ibus_prop_list_append(self->prop_list, self->prop_modern);
    ibus_prop_list_append(self->prop_list, self->prop_macro);
    ibus_prop_list_append(self->prop_list, self->prop_quick);
    ibus_prop_list_append(self->prop_list, self->prop_relaxed);
    ibus_prop_list_append(self->prop_list, self->prop_dev);
    ibus_prop_list_append(self->prop_list, sep_cfg);
    ibus_prop_list_append(self->prop_list, setup);
}

static void engine_set_surrounding_text(
    IBusEngine *engine,
    IBusText *text,
    guint cursor_pos,
    guint anchor_pos
) {
    if (IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->set_surrounding_text) {
        IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->set_surrounding_text(
            engine, text, cursor_pos, anchor_pos
        );
    }
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);
    if (self->core.purpose == IBUS_INPUT_PURPOSE_TERMINAL ||
        purpose_requires_passthrough(self->core.purpose) ||
        self->core.active_presentation_mode == VL_PRESENTATION_PASSTHROUGH) {
        return;
    }
    const char *sur_str = text ? ibus_text_get_text(text) : "";
    sur_guard_on_update(
        &self->sur_guard,
        &self->core,
        sur_str,
        cursor_pos,
        anchor_pos,
        self->pending_commit != NULL
    );
}

static gboolean engine_process_key_event(
    IBusEngine *engine,
    guint keyval,
    guint keycode,
    guint state
) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);

    /* Handle synthetic /dev/uinput BackSpace echoes before anything else */
    if (self->expected_uinput_bs > 0 && keyval == IBUS_KEY_BackSpace) {
        if (state & IBUS_RELEASE_MASK) {
            self->expected_uinput_bs--;
            if (self->expected_uinput_bs == 0 && self->pending_commit != NULL) {
                if (self->pending_timer_id != 0) {
                    g_source_remove(self->pending_timer_id);
                }
                self->pending_timer_id = g_timeout_add(2, on_pending_commit_timeout, self);
            }
        }
        return FALSE;
    }

    if (state & IBUS_RELEASE_MASK) {
        return FALSE;
    }

    /* Keep strict FIFO ordering while a delayed BS->commit flush is in flight */
    if (self->pending_commit != NULL) {
        if (queue_push(self, keyval, keycode, state)) {
            return TRUE;
        }
        flush_pending_now(self, true);
    }

    if (!keyval_is_modifier_only(keyval)) {
        const guint shortcut_mask = IBUS_CONTROL_MASK | IBUS_MOD1_MASK | IBUS_MOD4_MASK |
                                    IBUS_SUPER_MASK | IBUS_META_MASK | IBUS_HYPER_MASK;
        gint64 now_us = g_get_monotonic_time();

        /* Low-overhead hot-reload when idle so live toggles in Settings apply inside the same window */
        if (self->core.shadow[0] == '\0' &&
            (!self->core.session || !g_vl.is_composing(self->core.session)) &&
            (now_us - self->last_cfg_poll_us) >= NIDO_CONFIG_POLL_INTERVAL_US) {
            self->last_cfg_poll_us = now_us;
            reload_user_config_if_changed(self, false, true);
        }

        bool has_sur = (engine->client_capabilities & IBUS_CAP_SURROUNDING_TEXT) &&
                       self->core.purpose != IBUS_INPUT_PURPOSE_TERMINAL &&
                       !purpose_requires_passthrough(self->core.purpose) &&
                       self->core.active_presentation_mode != VL_PRESENTATION_PASSTHROUGH;

        if (!(state & shortcut_mask) && !keyval_is_navigation_reset_key(keyval) && has_sur) {
            IBusText *stext = NULL;
            guint cursor_pos = 0;
            guint anchor_pos = 0;
            ibus_engine_get_surrounding_text(engine, &stext, &cursor_pos, &anchor_pos);
            const char *sur_str = stext ? ibus_text_get_text(stext) : NULL;
            sur_guard_before_key(
                &self->sur_guard,
                &self->core,
                keyval,
                sur_str,
                cursor_pos,
                anchor_pos,
                now_us
            );

            /* Zero-underline mid-word & existing-word modifier adoption (v2.0.11 parity) */
            bool is_numpad_digit = vl_is_numpad_digit_keysym((uint32_t)keyval);
            if (sur_str && !is_numpad_digit &&
                !(self->core.cfg.developer_mode && vl_dev_code_is_bypassing(&self->core.dev_code)) &&
                keyval >= 32 && keyval < 127) {
                gint del_offset = 0;
                guint del_len = 0;
                gchar *commit_txt = NULL;
                if (core_try_adopt_surrounding_modifier(
                        &self->core, keyval, sur_str, cursor_pos, anchor_pos,
                        &del_offset, &del_len, &commit_txt)) {
                    self->sur_guard.expect_updates = true;
                    self->sur_guard.last_key_time_us = now_us;
                    ibus_engine_delete_surrounding_text(engine, del_offset, del_len);
                    if (commit_txt && *commit_txt) {
                        ibus_engine_commit_text(engine, ibus_text_new_from_string(commit_txt));
                    }
                    g_free(commit_txt);
                    return TRUE;
                }
            }
        }

        if ((state & shortcut_mask) || keyval_is_navigation_reset_key(keyval)) {
            self->sur_guard.expect_updates = false;
        } else {
            self->sur_guard.expect_updates = true;
            self->sur_guard.last_key_time_us = now_us;
        }
    }

    const NidoSink sink = {
        .commit_text = engine_sink_commit,
        .delete_and_commit = engine_sink_delete_and_commit,
    };
    bool consumed = core_process_key_press(&self->core, keyval, state, &sink, self, false);
    if (!consumed && self->pending_commit != NULL) {
        if (queue_push(self, keyval, keycode, state)) {
            return TRUE;
        }
        flush_pending_now(self, true);
    }
    return consumed ? TRUE : FALSE;
}

static void engine_focus_in_common(NidoVielotusEngine *self, const gchar *client_id) {
    IBusEngine *engine = IBUS_ENGINE(self);
    flush_pending_now(self, false);
    queue_clear(self);
    core_reset(&self->core);
    sur_guard_clear(&self->sur_guard);
    self->last_cfg_poll_us = g_get_monotonic_time();
    reload_user_config_if_changed(self, false, true);

    const char *app_candidate = (client_id && client_id[0] != '\0') ? client_id : g_getenv("VIELOTUS_APP_ID");
    self->core.active_presentation_mode = vl_presentation_rules_resolve(&self->presentation_rules, app_candidate);

    if (self->prop_list) {
        ibus_engine_register_properties(engine, self->prop_list);
    }

    /* Request surrounding text updates from supporting clients */
    if (engine->client_capabilities & IBUS_CAP_SURROUNDING_TEXT) {
        IBusText *stext = NULL;
        guint cursor_pos = 0;
        guint anchor_pos = 0;
        ibus_engine_get_surrounding_text(engine, &stext, &cursor_pos, &anchor_pos);
        if (stext) {
            const char *txt = ibus_text_get_text(stext);
            if (txt) {
                self->sur_guard.last_text = g_strdup(txt);
                self->sur_guard.last_cursor = cursor_pos;
                self->sur_guard.last_anchor = anchor_pos;
                self->sur_guard.has_last = true;
            }
            if (g_vl.sync_armed_word && txt && cursor_pos > 0 && cursor_pos == anchor_pos) {
                size_t len = utf8_codepoint_len(txt);
                if (cursor_pos <= len) {
                    const char *end = g_utf8_offset_to_pointer(txt, (glong)cursor_pos);
                    gchar *prefix = g_strndup(txt, (gsize)(end - txt));
                    g_vl.sync_armed_word(self->core.session, prefix);
                    g_free(prefix);
                }
            }
        }
    }
}

static void engine_focus_in(IBusEngine *engine) {
    engine_focus_in_common(NIDO_VIELOTUS_ENGINE(engine), NULL);
    IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->focus_in(engine);
}

static void engine_focus_in_id(IBusEngine *engine, const gchar *object_path, const gchar *client) {
    (void)object_path;
    engine_focus_in_common(NIDO_VIELOTUS_ENGINE(engine), client);
    if (IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->focus_in_id) {
        IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->focus_in_id(engine, object_path, client);
    }
}

static void engine_focus_out_common(NidoVielotusEngine *self) {
    flush_pending_now(self, true);
    queue_clear(self);
    self->core.active_presentation_mode = VL_PRESENTATION_AUTOMATIC;
    core_reset(&self->core);
    sur_guard_clear(&self->sur_guard);
}

static void engine_focus_out(IBusEngine *engine) {
    engine_focus_out_common(NIDO_VIELOTUS_ENGINE(engine));
    IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->focus_out(engine);
}

static void engine_focus_out_id(IBusEngine *engine, const gchar *object_path) {
    (void)object_path;
    engine_focus_out_common(NIDO_VIELOTUS_ENGINE(engine));
    if (IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->focus_out_id) {
        IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->focus_out_id(engine, object_path);
    }
}

static void engine_reset(IBusEngine *engine) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);
    flush_pending_now(self, false);
    queue_clear(self);
    core_reset(&self->core);
    sur_guard_clear(&self->sur_guard);
    IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->reset(engine);
}

static void engine_enable(IBusEngine *engine) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);
    self->engine_enabled = true;
    const char *ename = ibus_engine_get_name(engine);
    reload_user_config_if_changed(self, true, false);
    self->core.method = (ename && strstr(ename, "vni") != NULL)
        ? VIELOTUS_INPUT_VNI
        : VIELOTUS_INPUT_TELEX;
    core_apply_config(&self->core);
    if (save_config_key_atomic(self->config_dir, "INPUT_METHOD",
                               self->core.method == VIELOTUS_INPUT_VNI ? "vni" : "telex")) {
        gchar *cfg_path = g_build_filename(self->config_dir, "config.env", NULL);
        struct stat st;
        if (stat(cfg_path, &st) == 0) {
            self->config_mtime = st.st_mtime;
        }
        g_free(cfg_path);
    }
    build_properties(self);
    if (self->prop_list) {
        ibus_engine_register_properties(engine, self->prop_list);
    }
    if (engine->client_capabilities & IBUS_CAP_SURROUNDING_TEXT) {
        IBusText *stext = NULL;
        guint cpos = 0, apos = 0;
        ibus_engine_get_surrounding_text(engine, &stext, &cpos, &apos);
    }
    IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->enable(engine);
}

static void engine_disable(IBusEngine *engine) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);
    self->engine_enabled = false;
    flush_pending_now(self, true);
    queue_clear(self);
    core_reset(&self->core);
    sur_guard_clear(&self->sur_guard);
    IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->disable(engine);
}

static void engine_set_content_type(IBusEngine *engine, guint purpose, guint hints) {
    (void)hints;
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);
    self->core.purpose = purpose;
    if (purpose_requires_passthrough(purpose)) {
        flush_pending_now(self, false);
        queue_clear(self);
        core_reset(&self->core);
        sur_guard_clear(&self->sur_guard);
    }
    if (IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->set_content_type) {
        IBUS_ENGINE_CLASS(nido_vielotus_engine_parent_class)->set_content_type(engine, purpose, hints);
    }
}

static void engine_property_activate(IBusEngine *engine, const gchar *prop_name, guint prop_state) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(engine);
    if (!prop_name) {
        return;
    }

    if (strcmp(prop_name, "setup") == 0) {
        GError *err = NULL;
        const char *cmd = g_file_test("/usr/bin/vielotus-settings", G_FILE_TEST_IS_EXECUTABLE)
            ? "/usr/bin/vielotus-settings"
            : "vielotus-settings";
        if (!g_spawn_command_line_async(cmd, &err)) {
            if (err) {
                g_warning("nido-ibus-vielotus: failed to launch vielotus-settings: %s", err->message);
                g_error_free(err);
            }
        }
        return;
    }

    bool checked = (prop_state == PROP_STATE_CHECKED);
    const char *env_key = NULL;
    const char *env_val = checked ? "true" : "false";

    if (strcmp(prop_name, "im-telex") == 0 && checked) {
        self->core.method = VIELOTUS_INPUT_TELEX;
        env_key = "INPUT_METHOD";
        env_val = "telex";
    } else if (strcmp(prop_name, "im-vni") == 0 && checked) {
        self->core.method = VIELOTUS_INPUT_VNI;
        env_key = "INPUT_METHOD";
        env_val = "vni";
    } else if (strcmp(prop_name, "grammar-flex") == 0) {
        self->core.cfg.grammar_profile = checked ? VIELOTUS_GRAMMAR_SEN_FLEX : VIELOTUS_GRAMMAR_CLASSIC;
        env_key = "GRAMMAR_PROFILE";
        env_val = checked ? "sen_flex" : "classic";
    } else if (strcmp(prop_name, "smart-bilingual") == 0) {
        self->core.cfg.smart_bilingual = checked;
        env_key = "SMART_BILINGUAL";
    } else if (strcmp(prop_name, "modern-orthography") == 0) {
        self->core.cfg.modern_orthography = checked;
        env_key = "MODERN_ORTHOGRAPHY";
    } else if (strcmp(prop_name, "quick-telex") == 0) {
        self->core.cfg.quick_telex = checked;
        env_key = "QUICK_TELEX";
    } else if (strcmp(prop_name, "relaxed-coda") == 0) {
        self->core.cfg.relaxed_coda = checked;
        env_key = "RELAXED_CODA";
    } else if (strcmp(prop_name, "macro-enabled") == 0) {
        self->core.cfg.macro_enabled = checked;
        env_key = "MACRO_ENABLED";
    } else if (strcmp(prop_name, "developer-mode") == 0) {
        self->core.cfg.developer_mode = checked;
        env_key = "DEVELOPER_MODE";
    }

    if (env_key) {
        core_apply_config(&self->core);
        sync_properties_ui(self);
        if (save_config_key_atomic(self->config_dir, env_key, env_val)) {
            gchar *cfg_path = g_build_filename(self->config_dir, "config.env", NULL);
            struct stat st;
            if (stat(cfg_path, &st) == 0) {
                self->config_mtime = st.st_mtime;
            }
            g_free(cfg_path);
        }
    }
}

static GObject *engine_constructor(
    GType type,
    guint n_construct_params,
    GObjectConstructParam *construct_params
) {
#if IBUS_CHECK_VERSION(1, 5, 27)
    for (guint i = 0; i < n_construct_params; i++) {
        if (construct_params[i].pspec &&
            g_strcmp0(construct_params[i].pspec->name, "has-focus-id") == 0) {
            g_value_set_boolean(construct_params[i].value, TRUE);
        }
    }
#endif
    GObject *obj = G_OBJECT_CLASS(nido_vielotus_engine_parent_class)->constructor(
        type, n_construct_params, construct_params
    );
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(obj);
    const char *ename = ibus_engine_get_name(IBUS_ENGINE(self));
    self->core.method = (ename && strstr(ename, "vni") != NULL)
        ? VIELOTUS_INPUT_VNI
        : VIELOTUS_INPUT_TELEX;
    reload_user_config_if_changed(self, true, false);
    build_properties(self);
    return obj;
}

static void engine_finalize(GObject *obj) {
    NidoVielotusEngine *self = NIDO_VIELOTUS_ENGINE(obj);
    flush_pending_now(self, false);
    queue_clear(self);
    sur_guard_clear(&self->sur_guard);
    uinput_close(&self->uinput);
    if (self->core.session) {
        g_vl.destroy_session(self->core.session);
        self->core.session = NULL;
    }
    if (self->prop_list) {
        g_object_unref(self->prop_list);
        self->prop_list = NULL;
    }
    g_free(self->config_dir);
    self->config_dir = NULL;
    G_OBJECT_CLASS(nido_vielotus_engine_parent_class)->finalize(obj);
}

static void nido_vielotus_engine_init(NidoVielotusEngine *self) {
    memset(&self->core, 0, sizeof(self->core));
    memset(&self->sur_guard, 0, sizeof(self->sur_guard));
    vl_presentation_rules_init(&self->presentation_rules);
    nido_config_defaults(&self->core.cfg);
    self->core.method = VIELOTUS_INPUT_TELEX;
    self->core.purpose = IBUS_INPUT_PURPOSE_FREE_FORM;
    self->core.active_presentation_mode = VL_PRESENTATION_AUTOMATIC;
    self->core.session = g_vl.create_session();
    self->config_dir = get_vielotus_config_dir();
    self->uinput.fd = -1;
    build_properties(self);
}

static void nido_vielotus_engine_class_init(NidoVielotusEngineClass *klass) {
    GObjectClass *obj_class = G_OBJECT_CLASS(klass);
    IBusEngineClass *eng_class = IBUS_ENGINE_CLASS(klass);

    obj_class->constructor = engine_constructor;
    obj_class->finalize = engine_finalize;

    eng_class->process_key_event = engine_process_key_event;
    eng_class->focus_in = engine_focus_in;
    eng_class->focus_in_id = engine_focus_in_id;
    eng_class->focus_out = engine_focus_out;
    eng_class->focus_out_id = engine_focus_out_id;
    eng_class->reset = engine_reset;
    eng_class->enable = engine_enable;
    eng_class->disable = engine_disable;
    eng_class->set_content_type = engine_set_content_type;
    eng_class->set_surrounding_text = engine_set_surrounding_text;
    eng_class->property_activate = engine_property_activate;
}

/* ============================================================================
 * Headless --self-test Suite (Zero Display / Zero IBus Daemon Required)
 * ============================================================================ */

typedef struct {
    GString *buf;
    int total_backspaces;
    int total_commits;
    int tier1_verified_hits;
    int tier1_mismatches;
} TestScreen;

static void test_screen_delete_chars(GString *buf, int n) {
    while (n > 0 && buf->len > 0) {
        const char *end = buf->str + buf->len;
        const char *prev = g_utf8_find_prev_char(buf->str, end);
        if (!prev) {
            g_string_truncate(buf, 0);
            break;
        }
        g_string_truncate(buf, (gsize)(prev - buf->str));
        n--;
    }
}

static void test_sink_commit(void *ctx, const char *utf8) {
    TestScreen *sc = (TestScreen *)ctx;
    if (utf8 && *utf8) {
        g_string_append(sc->buf, utf8);
        sc->total_commits++;
    }
}

static void test_sink_delete_and_commit(void *ctx, int bs, const char *utf8, const char *prev_shadow) {
    TestScreen *sc = (TestScreen *)ctx;
    if (bs > 0) {
        guint cursor_pos = (guint)utf8_codepoint_len(sc->buf->str);
        if (prev_shadow && *prev_shadow &&
            surrounding_matches_tail(sc->buf->str, cursor_pos, prev_shadow, bs)) {
            sc->tier1_verified_hits++;
        } else {
            sc->tier1_mismatches++;
        }
        test_screen_delete_chars(sc->buf, bs);
        sc->total_backspaces += bs;
    }
    if (utf8 && *utf8) {
        g_string_append(sc->buf, utf8);
        sc->total_commits++;
    }
}

static bool test_type_sequence(
    NidoCoreState *st,
    const char *seq,
    const char *expected,
    const char *label,
    int *out_bs_count
) {
    TestScreen sc = {
        .buf = g_string_new(NULL),
        .total_backspaces = 0,
        .total_commits = 0,
        .tier1_verified_hits = 0,
        .tier1_mismatches = 0,
    };
    const NidoSink sink = {
        .commit_text = test_sink_commit,
        .delete_and_commit = test_sink_delete_and_commit,
    };

    core_reset(st);
    for (const char *p = seq; *p; p++) {
        unsigned char c = (unsigned char)*p;
        guint keyval = (c == '\b') ? IBUS_KEY_BackSpace : (c == '\n') ? IBUS_KEY_Return : (guint)c;
        bool consumed = core_process_key_press(st, keyval, 0, &sink, &sc, false);
        if (!consumed) {
            if (keyval == IBUS_KEY_BackSpace) {
                test_screen_delete_chars(sc.buf, 1);
            } else if (keyval == IBUS_KEY_Return) {
                g_string_append_c(sc.buf, '\n');
            } else {
                g_string_append_c(sc.buf, (char)c);
            }
        }
    }

    bool ok = (strcmp(sc.buf->str, expected) == 0) && (sc.tier1_mismatches == 0);
    if (!ok) {
        fprintf(stderr, "[FAIL] %s: expected \"%s\", got \"%s\" (tier1_mismatches=%d)\n",
                label, expected, sc.buf->str, sc.tier1_mismatches);
    }
    if (out_bs_count) {
        *out_bs_count = sc.total_backspaces;
    }
    g_string_free(sc.buf, TRUE);
    return ok;
}

static int run_self_test(const char *capi_path) {
    if (!vielotus_api_load(capi_path)) {
        fprintf(stderr, "[FAIL] Could not load libVieLotusCAPI.so.0\n");
        return 1;
    }

    const char *ver = g_vl.get_version ? g_vl.get_version() : "unknown";
    int bs = 0;
    const char *ins = NULL;

    /* 1. LCP UTF-8 minimal diff checks */
    compute_utf8_lcp_diff("Việt", "Việt", &bs, &ins);
    if (bs != 0 || strcmp(ins, "") != 0) {
        fprintf(stderr, "[FAIL] LCP identical mismatch: bs=%d ins=%s\n", bs, ins);
        return 1;
    }
    compute_utf8_lcp_diff("tét", "test", &bs, &ins);
    if (bs != 2 || strcmp(ins, "est") != 0) {
        fprintf(stderr, "[FAIL] LCP Smart Bilingual mismatch: bs=%d ins=%s\n", bs, ins);
        return 1;
    }
    compute_utf8_lcp_diff("chao", "chào", &bs, &ins);
    if (bs != 2 || strcmp(ins, "ào") != 0) {
        fprintf(stderr, "[FAIL] LCP tone mismatch: bs=%d ins=%s\n", bs, ins);
        return 1;
    }

    /* 2. Surrounding-text suffix guard & UI language resolution */
    if (!surrounding_matches_tail("Xin chao", 8, "chao", 2) ||
        surrounding_matches_tail("Xin chao", 4, "chao", 2)) {
        fprintf(stderr, "[FAIL] surrounding_matches_tail check failed\n");
        return 1;
    }
    if (vl_ui_is_english("vi") || !vl_ui_is_english("en")) {
        fprintf(stderr, "[FAIL] vl_ui_is_english explicit override failed\n");
        return 1;
    }

    /* 3. PresentationRules (typing-modes.yml) glob matcher */
    VlPresentationRules rules;
    vl_presentation_rules_init(&rules);
    vl_presentation_rules_parse_text(&rules,
        "# Test rules\n"
        "*remmina*: passthrough\n"
        "org.mozilla.*: directReplacement\n"
        "code: passthrough\n"
    );
    if (vl_presentation_rules_resolve(&rules, "gtk-im:org.remmina.Remmina") != VL_PRESENTATION_PASSTHROUGH ||
        vl_presentation_rules_resolve(&rules, "org.mozilla.firefox") != VL_PRESENTATION_DIRECT_REPLACEMENT ||
        vl_presentation_rules_resolve(&rules, "nautilus") != VL_PRESENTATION_AUTOMATIC) {
        fprintf(stderr, "[FAIL] typing-modes.yml PresentationRules check failed\n");
        return 1;
    }

    /* 4. Config parser + atomic writer round-trip preserving unknown keys + UI_LANGUAGE + INPUT_METHOD */
    char tmp_dir[] = "/tmp/nido-ime-test-XXXXXX";
    if (!mkdtemp(tmp_dir)) {
        fprintf(stderr, "[FAIL] mkdtemp failed\n");
        return 1;
    }
    gchar *cfg_file = g_build_filename(tmp_dir, "config.env", NULL);
    const char *seed_cfg =
        "# Sen Viet config\n"
        "INPUT_METHOD=telex\n"
        "SMART_BILINGUAL=true\n"
        "UI_LANGUAGE=en\n"
        "CUSTOM_UPSTREAM_KEY=preserve_me\n"
        "NIDO_BS_BACKEND=forward\n"
        "NIDO_BS_DELAY_US=6000\n";
    g_file_set_contents(cfg_file, seed_cfg, -1, NULL);
    if (!save_config_key_atomic(tmp_dir, "MACRO_ENABLED", "true") ||
        !save_config_key_atomic(tmp_dir, "SMART_BILINGUAL", "false") ||
        !save_config_key_atomic(tmp_dir, "INPUT_METHOD", "vni")) {
        fprintf(stderr, "[FAIL] save_config_key_atomic failed\n");
        return 1;
    }
    gchar *saved_cfg = NULL;
    g_file_get_contents(cfg_file, &saved_cfg, NULL, NULL);
    NidoConfig parsed_cfg;
    parse_config_env_text(saved_cfg, &parsed_cfg);
    bool cfg_ok = saved_cfg &&
                  strstr(saved_cfg, "CUSTOM_UPSTREAM_KEY=preserve_me") != NULL &&
                  parsed_cfg.has_input_method &&
                  parsed_cfg.config_method == VIELOTUS_INPUT_VNI &&
                  strcmp(parsed_cfg.ui_language, "en") == 0 &&
                  parsed_cfg.macro_enabled == true &&
                  parsed_cfg.smart_bilingual == false &&
                  parsed_cfg.bs_backend == NIDO_BS_FORWARD &&
                  parsed_cfg.bs_delay_us == 6000u;
    g_free(saved_cfg);
    unlink(cfg_file);
    g_free(cfg_file);
    rmdir(tmp_dir);
    if (!cfg_ok) {
        fprintf(stderr, "[FAIL] config.env atomic round-trip check failed\n");
        return 1;
    }

    /* 5. Live libVieLotusCAPI zero-underline typing verification */
    NidoCoreState st;
    memset(&st, 0, sizeof(st));
    nido_config_defaults(&st.cfg);
    st.method = VIELOTUS_INPUT_TELEX;
    st.purpose = IBUS_INPUT_PURPOSE_FREE_FORM;
    st.active_presentation_mode = VL_PRESENTATION_AUTOMATIC;
    st.session = g_vl.create_session();
    core_apply_config(&st);

    bool ok = true;
    int synth_bs = 0;
    ok &= test_type_sequence(&st, "Xin chaof Vieejt Nam ", "Xin chào Việt Nam ", "Telex basic", &synth_bs);
    ok &= test_type_sequence(&st, "tieengs Vieejt nguwowif ddooij ", "tiếng Việt người đội ", "Telex complex", NULL);
    ok &= test_type_sequence(&st, "world server windows test ", "world server windows test ", "Smart Bilingual", NULL);
    ok &= test_type_sequence(&st, "arm64 he has been ", "arm64 he has been ", "v2.0.11 Smart Bilingual arm64/been", NULL);
    ok &= test_type_sequence(&st, "eees ", "ees ", "Undo diacritic", NULL);
    ok &= test_type_sequence(&st, "Vieej\bt ", "Vit ", "Mid-word BackSpace", NULL);
    ok &= test_type_sequence(&st, "chaof, ", "chào, ", "Punctuation commit", NULL);

    /* Post-commit BackSpace rehydration (Space -> BS -> BS -> edit tone/vowel) */
    ok &= test_type_sequence(&st, "dduocwj \b\b\br ", "đử ", "Post-commit BS rehydrate tone", NULL);
    ok &= test_type_sequence(&st, "dduocwj \b\b\b\bucj ", "đục ", "Post-commit BS rehydrate vowel", NULL);
    ok &= test_type_sequence(&st, "toanf \b\b ", "toà ", "Post-commit BS rehydrate coda", NULL);

    /* Zero-underline mid-word & existing-word surrounding adoption (fixes Lỗi 4) */
    {
        gint del_off = 0;
        guint del_len = 0;
        gchar *commit_txt = NULL;

        /* 5a. Mid-word edit: `gõ thử đư|c` (cursor=9) + `r` -> delete -1, len 1, commit `ử` (`gõ thử đử|c`) */
        core_reset(&st);
        if (!core_try_adopt_surrounding_modifier(&st, 'r', "gõ thử đưc", 9, 9, &del_off, &del_len, &commit_txt) ||
            del_off != -1 || del_len != 1 || !commit_txt || strcmp(commit_txt, "ử") != 0 ||
            strcmp(st.shadow, "đử") != 0) {
            fprintf(stderr, "[FAIL] Mid-word surrounding adoption (đư|c + r -> đử|c) failed: off=%d len=%u txt=%s shadow=%s\n",
                    del_off, del_len, commit_txt ? commit_txt : "(null)", st.shadow);
            ok = false;
        }
        g_free(commit_txt);
        commit_txt = NULL;

        /* 5b. End-of-word edit: `chao|` (cursor=4) + `f` -> delete -2, len 2, commit `ào` (`chào|`) */
        core_reset(&st);
        if (!core_try_adopt_surrounding_modifier(&st, 'f', "chao", 4, 4, &del_off, &del_len, &commit_txt) ||
            del_off != -2 || del_len != 2 || !commit_txt || strcmp(commit_txt, "ào") != 0) {
            fprintf(stderr, "[FAIL] End-of-word surrounding adoption (chao| + f -> chào|) failed\n");
            ok = false;
        }
        g_free(commit_txt);
        commit_txt = NULL;

        /* 5c. Plain English safety: `hello|` (cursor=5) + `s` must NOT adopt */
        core_reset(&st);
        if (core_try_adopt_surrounding_modifier(&st, 's', "hello", 5, 5, &del_off, &del_len, &commit_txt)) {
            fprintf(stderr, "[FAIL] English word 'hello' + 's' falsely adopted as Vietnamese!\n");
            g_free(commit_txt);
            ok = false;
        }
    }

    /* Surrounding-text cursor-jump / mouse-click guard regression checks */
    {
        NidoSurGuard sg;
        memset(&sg, 0, sizeof(sg));

        core_reset(&st);
        core_set_shadow(&st, "ụ");
        sg.expect_updates = true;
        if (sur_guard_on_update(&sg, &st, "gõ thử đục", 9, 9, false) || sg.expect_updates) {
            fprintf(stderr, "[FAIL] sur_guard failed on matching commit\n");
            ok = false;
        }
        if (!sur_guard_on_update(&sg, &st, "gõ thử đục", 10, 10, false) || st.shadow[0] != '\0') {
            fprintf(stderr, "[FAIL] sur_guard failed to reset on mouse click cursor move (9 -> 10)\n");
            ok = false;
        }

        sur_guard_clear(&sg);
        core_reset(&st);
        core_set_shadow(&st, "đươc");
        sg.expect_updates = true;
        if (sur_guard_on_update(&sg, &st, "gõ thử đươc", 8, 8, false) || st.shadow[0] == '\0') {
            fprintf(stderr, "[FAIL] sur_guard falsely reset on GTK phase-1 insert-text\n");
            ok = false;
        }
        if (sur_guard_on_update(&sg, &st, "gõ thử đươc", 11, 11, false) || strcmp(st.shadow, "đươc") != 0 || sg.expect_updates) {
            fprintf(stderr, "[FAIL] sur_guard falsely reset on GTK phase-2 cursor advance (8 -> 11)\n");
            ok = false;
        }
        if (!sur_guard_on_update(&sg, &st, "Hello world", 5, 5, false) || st.shadow[0] != '\0') {
            fprintf(stderr, "[FAIL] sur_guard failed to reset on cross-paragraph click\n");
            ok = false;
        }

        sur_guard_clear(&sg);
        core_reset(&st);
        core_set_shadow(&st, "goo");
        sg.expect_updates = true;
        if (sur_guard_on_update(&sg, &st, "google.com", 3, 10, false) || strcmp(st.shadow, "goo") != 0) {
            fprintf(stderr, "[FAIL] sur_guard falsely reset URL-bar autocomplete selection\n");
            ok = false;
        }

        sur_guard_clear(&sg);
        core_reset(&st);
        core_arm_post_commit(&st, "được");
        sg.expect_updates = true;
        if (sur_guard_on_update(&sg, &st, "gõ thử được ", 12, 12, false) || st.post_commit_state != 1) {
            fprintf(stderr, "[FAIL] sur_guard falsely cleared post_commit_state on matching space\n");
            ok = false;
        }
        if (!sur_guard_before_key(&sg, &st, IBUS_KEY_BackSpace, "hello ", 6, 6, 500000) || st.post_commit_state != 0) {
            fprintf(stderr, "[FAIL] sur_guard_before_key failed to disarm stale post-commit BackSpace\n");
            ok = false;
        }
        sur_guard_clear(&sg);
    }

    /* Macro expansion */
    if (g_vl.load_macros_json) {
        st.cfg.macro_enabled = true;
        core_apply_config(&st);
        g_vl.load_macros_json("{\"vn\":\"Việt Nam\"}");
        ok &= test_type_sequence(&st, "vn ", "Việt Nam ", "Macro expansion", NULL);
        st.cfg.macro_enabled = false;
        core_apply_config(&st);
    }

    /* VNI mode + VNI Numpad literal digit check */
    st.method = VIELOTUS_INPUT_VNI;
    core_apply_config(&st);
    ok &= test_type_sequence(&st, "Xin cha2o Vie65t Nam ", "Xin chào Việt Nam ", "VNI basic", NULL);
    {
        TestScreen sc = { .buf = g_string_new(NULL) };
        const NidoSink sink = { .commit_text = test_sink_commit, .delete_and_commit = test_sink_delete_and_commit };
        core_reset(&st);
        const guint seq[] = { 'a', IBUS_KEY_KP_1, ' ' };
        for (size_t i = 0; i < 3; i++) {
            bool c = core_process_key_press(&st, seq[i], 0, &sink, &sc, false);
            if (!c) {
                guint nkv = (guint)vl_normalize_numpad_keysym((uint32_t)seq[i]);
                g_string_append_c(sc.buf, (char)nkv);
            }
        }
        if (strcmp(sc.buf->str, "a1 ") != 0) {
            fprintf(stderr, "[FAIL] VNI Numpad literal digit: expected \"a1 \", got \"%s\"\n", sc.buf->str);
            ok = false;
        }
        g_string_free(sc.buf, TRUE);
    }

    /* XKB combining mark NFD->NFC normalization (`u` + `w` + combining hook U+0309 -> `ử`) */
    st.method = VIELOTUS_INPUT_TELEX;
    core_apply_config(&st);
    {
        TestScreen sc = { .buf = g_string_new(NULL) };
        const NidoSink sink = { .commit_text = test_sink_commit, .delete_and_commit = test_sink_delete_and_commit };
        core_reset(&st);
        const guint seq[] = { 't', 'h', 'u', 'w', 0xFE61u /* dead_hook */ };
        for (size_t i = 0; i < 5; i++) {
            bool c = core_process_key_press(&st, seq[i], 0, &sink, &sc, false);
            if (!c) {
                g_string_append_c(sc.buf, (char)seq[i]);
            }
        }
        if (strcmp(sc.buf->str, "thử") != 0) {
            fprintf(stderr, "[FAIL] XKB dead-key NFC normalization: expected \"thử\", got \"%s\"\n", sc.buf->str);
            ok = false;
        }
        g_string_free(sc.buf, TRUE);
    }

    /* Password field & Per-app passthrough guards */
    st.purpose = IBUS_INPUT_PURPOSE_PASSWORD;
    core_apply_config(&st);
    ok &= test_type_sequence(&st, "Vieejt123", "Vieejt123", "Password passthrough", NULL);

    st.purpose = IBUS_INPUT_PURPOSE_FREE_FORM;
    st.active_presentation_mode = VL_PRESENTATION_PASSTHROUGH;
    ok &= test_type_sequence(&st, "Vieejt", "Vieejt", "App-rule passthrough", NULL);
    st.active_presentation_mode = VL_PRESENTATION_AUTOMATIC;

    /* Developer mode inline (`code`) and fenced (```code```) guard */
    st.cfg.developer_mode = true;
    core_apply_config(&st);
    ok &= test_type_sequence(&st, "`ddooij` ddooij ", "`ddooij` đội ", "Developer inline backtick guard", NULL);
    ok &= test_type_sequence(&st, "```\nddooij\n```\nddooij ", "```\nddooij\n```\nđội ", "Developer fenced backtick guard", NULL);

    g_vl.destroy_session(st.session);
    if (!ok) {
        return 1;
    }

    printf("nido-ibus-vielotus v%s self-test passed (libVieLotusCAPI=%s, zero-preedit verified).\n",
           NIDO_IME_VERSION, ver);
    return 0;
}

/* ============================================================================
 * Main Entry Point
 * ============================================================================ */

static void on_bus_disconnected(IBusBus *bus, gpointer user_data) {
    (void)bus;
    (void)user_data;
    ibus_quit();
}

int main(int argc, char **argv) {
    gboolean opt_ibus = FALSE;
    gboolean opt_self_test = FALSE;
    gboolean opt_version = FALSE;
    gchar *opt_capi_path = NULL;

    const GOptionEntry entries[] = {
        {"ibus", 'i', 0, G_OPTION_ARG_NONE, &opt_ibus, "Run as an IBus component launched by ibus-daemon", NULL},
        {"self-test", 0, 0, G_OPTION_ARG_NONE, &opt_self_test, "Run headless zero-underline verification suite", NULL},
        {"capi-path", 0, 0, G_OPTION_ARG_FILENAME, &opt_capi_path, "Optional path to libVieLotusCAPI.so.0", "PATH"},
        {"version", 'v', 0, G_OPTION_ARG_NONE, &opt_version, "Print version and exit", NULL},
        {NULL, 0, 0, G_OPTION_ARG_NONE, NULL, NULL, NULL}
    };

    GOptionContext *ctx = g_option_context_new("- NidoOS Sen Viet Zero-Underline IBus Bridge");
    g_option_context_add_main_entries(ctx, entries, NULL);
    GError *err = NULL;
    if (!g_option_context_parse(ctx, &argc, &argv, &err)) {
        fprintf(stderr, "Option parse error: %s\n", err ? err->message : "unknown");
        if (err) {
            g_error_free(err);
        }
        g_option_context_free(ctx);
        return 2;
    }
    g_option_context_free(ctx);

    if (opt_version) {
        printf("nido-ibus-vielotus %s\n", NIDO_IME_VERSION);
        g_free(opt_capi_path);
        return 0;
    }

    if (opt_self_test) {
        int rc = run_self_test(opt_capi_path);
        g_free(opt_capi_path);
        return rc;
    }

    if (!vielotus_api_load(opt_capi_path)) {
        g_free(opt_capi_path);
        return 1;
    }
    g_free(opt_capi_path);

    ibus_init();
    IBusBus *bus = ibus_bus_new();
    if (!ibus_bus_is_connected(bus)) {
        g_warning("nido-ibus-vielotus: cannot connect to ibus-daemon");
        g_object_unref(bus);
        return 1;
    }

    g_signal_connect(bus, "disconnected", G_CALLBACK(on_bus_disconnected), NULL);

    IBusFactory *factory = ibus_factory_new(ibus_bus_get_connection(bus));
    ibus_factory_add_engine(factory, "nido-vielotus", NIDO_TYPE_VIELOTUS_ENGINE);
    ibus_factory_add_engine(factory, "nido-vielotus-vni", NIDO_TYPE_VIELOTUS_ENGINE);

    if (opt_ibus) {
        ibus_bus_request_name(bus, NIDO_BUS_NAME, 0);
    } else {
        IBusComponent *comp = ibus_component_new_from_file(NIDO_COMPONENT_XML);
        if (comp) {
            ibus_bus_register_component(bus, comp);
            g_object_unref(comp);
        }
    }

    ibus_main();

    g_object_unref(factory);
    g_object_unref(bus);
    return 0;
}
