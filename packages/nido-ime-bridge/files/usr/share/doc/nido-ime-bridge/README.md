# nido-ime-bridge

Native zero-underline IBus bridge and NidoOS-customized Bento Settings UI for **Sen Việt (VieLotusIM v2.0.11+)** on NidoOS.

## Architecture
- Dynamically loads `/usr/lib/libVieLotusCAPI.so.0` from the unmodified upstream `vielotus` (>= 2.0.11) package via `dlopen()`.
- Never invokes IBus preedit APIs (`update_preedit_text` / `show_preedit_text`), eliminating Wayland `zwp_text_input_v3` underlines across GTK4, Firefox, Chromium/Electron (NidoOffice), and terminals.
- Ships `/usr/bin/nido-vielotus-settings` (diverted safely over `/usr/bin/vielotus-settings` via `dpkg-divert`), bringing the full v2.0.11 Bento GTK3 UI (bilingual VI–EN switcher, single-instance window, Launch-at-Login card, Macro Editor, App Rules Editor, Feedback sheet, Doctor) with NidoOS Teal-Slate theming (`Be Vietnam Pro`), `nido-vielotus` engine protection, and Backspace pipeline tuning.
- Shares `~/.config/vielotus/config.env`, `~/.config/vielotus/macros.json`, and `~/.config/vielotus/typing-modes.yml` directly with upstream `vielotus`.

## Key Capabilities (v0.2.2 / VieLotusIM v2.0.11 Parity + NidoOS Customizations)
- **Zero-Underline Mid-Word & Existing-Word Adoption:** Safely adopts surrounding Vietnamese words when adding diacritics mid-word (`đư|c + r -> đử|c`) or at word end (`chao| + f -> chào|`) without enabling underlined preedit, while keeping English words (`hello| + s -> hellos`) untouched.
- **Post-Commit BackSpace Rehydration & Mouse-Click Cursor Guard:** Preserves tone editing right after space + backspace (`được␣⌫⌫⌫ực`) while resetting composition when the cursor moves elsewhere.
- **Bilingual VI–EN IBus Menu & Two-Way Telex/VNI Sync:** Instant hot-reload when toggling settings from `vielotus-settings` or the GNOME IBus top-bar menu (`UI_LANGUAGE=auto|vi|en`).
- **VNI Numpad Literal Digits, XKB Combining-Mark NFC Normalization, Per-App `typing-modes.yml` (`passthrough`), and Full Inline/Fenced Developer Mode FSM.**

## Optional NidoOS Tuning (`~/.config/vielotus/config.env`)
- `NIDO_BS_BACKEND=auto|forward|uinput` (default: `auto` — verified `delete_surrounding_text` first, then ordered `forward_key_event` fallback; `uinput` uses `/dev/uinput` with echo-barrier synchronization).
- `NIDO_BS_DELAY_US=8000` (microseconds between synthetic BackSpace burst and queued commit on non-surrounding-text clients; default `8000`, range `0..50000`).
