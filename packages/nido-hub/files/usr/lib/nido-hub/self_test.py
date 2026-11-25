"""Self-test suite for NidoHub Desktop backend, App Store, AI discovery, and onboarding."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# Ensure sibling helper modules resolve both when installed and when invoked standalone.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from agent_accounts import (  # noqa: E402
    CommandResult,
    NidoSecretStore,
    default_command_runner,
    plan_managed_install_command,
    plan_sign_in_command,
    probe_provider_status,
    read_agents_status,
    resolve_antigravity_platform,
    verify_sha512_hex,
    write_agents_status,
)
from ai_discovery import (  # noqa: E402
    OLLAMA_DEFAULT_BASE_URL,
    HardwareProfile,
    compute_capability_matrix,
    is_vision_model,
    live_verify_backend,
    parse_ollama_sha256sum,
    probe_local_ai_runtimes,
    recommend_local_models,
)
from app_store import (  # noqa: E402
    SOURCE_LABELS,
    NidoAppStore,
    OperationHandle,
    has_populated_apt_lists,
)
from markdown_render import (  # noqa: E402
    parse_inline_spans,
    parse_markdown_blocks,
    render_markdown_to_plain,
)
from printer_probe import (  # noqa: E402
    UNIVERSAL_ADAPTER_MODELS,
    build_network_printer_spec,
    probe_printers,
)


def run_self_test(app_mod: Any) -> int:
    ALLOWED_SETTINGS_PANELS = app_mod.ALLOWED_SETTINGS_PANELS
    NIDO_HUB_CSS = app_mod.NIDO_HUB_CSS
    PROVIDERS = app_mod.PROVIDERS
    NidoHubBackend = app_mod.NidoHubBackend
    build_settings_panel_argv = app_mod.build_settings_panel_argv
    escape_markup = app_mod.escape_markup
    evaluate_ai_onboarding_gate = app_mod.evaluate_ai_onboarding_gate

    with tempfile.TemporaryDirectory(prefix="nidohub-selftest-") as tmp:
        root = Path(tmp)
        cat_file = root / "catalog.json"
        cat_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "editions": [
                        {
                            "id": "core",
                            "name": "Ubuntu-Nido Core",
                            "packages": ["nidoai-gateway", "nidoagent-cli"],
                            "description": "Core packages",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        # 1. Headless 0600 fallback test + Anthropic backend support
        backend = NidoHubBackend(home_dir=root / "home", catalog_path=cat_file)
        backend.ensure_gateway_ready(start_service=False)
        assert backend.env_file.is_file(), "Missing bootstrapped .env.local"
        assert not backend.welcome_done_file.is_file(), "welcome-done should wait for save"
        boot_mode = backend.env_file.stat().st_mode & 0o777
        assert boot_mode == 0o600, f"Expected 0600 on bootstrapped .env.local, got {oct(boot_mode)}"
        assert len(backend.load_env().get("NIDOAI_CLIENT_TOKEN", "")) >= 32
        backend.save_ai_settings(
            primary_backend="anthropic",
            gemini_key="test_gemini_key_placeholder",
            anthropic_key="test_anthropic_key_placeholder",
            restart_service=False,
        )
        assert backend.env_file.is_file(), "Missing .env.local"
        mode = backend.env_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600 on .env.local, got {oct(mode)}"
        loaded = backend.load_env()
        assert loaded["NIDOAI_PRIMARY_BACKEND"] == "anthropic"
        assert loaded["GEMINI_API_KEY"] == "test_gemini_key_placeholder"
        assert loaded["ANTHROPIC_API_KEY"] == "test_anthropic_key_placeholder"
        assert len(loaded["NIDOAI_CLIENT_TOKEN"]) >= 32
        assert backend.welcome_done_file.is_file()

        # 2. OS Keyring (linux-libsecret) test: secret passed strictly over stdin, stripped from .env.local
        keyring_mem: dict[str, str] = {}
        keyring_calls: list[tuple[list[str], str | None]] = []

        def mock_keyring_runner(
            argv: list[str], stdin_text: str | None, _timeout: float
        ) -> CommandResult:
            keyring_calls.append((argv, stdin_text))
            if argv[:2] == ["which", "secret-tool"]:
                return CommandResult(0, "/usr/bin/secret-tool\n", "")
            if argv[:2] == ["secret-tool", "store"]:
                acct = argv[argv.index("account") + 1]
                keyring_mem[acct] = (stdin_text or "").strip()
                return CommandResult(0, "", "")
            if argv[:2] == ["secret-tool", "lookup"]:
                acct = argv[argv.index("account") + 1]
                if acct in keyring_mem:
                    return CommandResult(0, keyring_mem[acct] + "\n", "")
                return CommandResult(1, "", "")
            return CommandResult(1, "", "")

        mock_store = NidoSecretStore(
            platform="linux",
            env={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"},
            runner=mock_keyring_runner,
        )
        assert mock_store.detect_mode() == "linux-libsecret"
        kr_backend = NidoHubBackend(
            home_dir=root / "home-keyring",
            catalog_path=cat_file,
            secret_store=mock_store,
        )
        kr_backend.save_ai_settings(
            primary_backend="gemini",
            gemini_key="secret_stored_only_in_keyring",
            restart_service=False,
        )
        raw_env_text = kr_backend.env_file.read_text(encoding="utf-8")
        assert "secret_stored_only_in_keyring" not in raw_env_text, (
            "Plaintext API key must be stripped from .env.local when OS keyring is active"
        )
        store_invocations = [c for c in keyring_calls if c[0][:2] == ["secret-tool", "store"]]
        assert store_invocations, "Expected secret-tool store call"
        assert "secret_stored_only_in_keyring" not in " ".join(store_invocations[0][0]), (
            "Secret must never be passed via argv"
        )
        assert kr_backend.load_env().get("GEMINI_API_KEY") == "secret_stored_only_in_keyring"

        # 3. Managed agent runtime command planning & agents-status.json secret stripping
        install_cmd = plan_managed_install_command("codex", root / "home")
        assert install_cmd[:2] == ["npm", "install"] and "@openai/codex" in install_cmd
        try:
            plan_managed_install_command("untrusted-pkg;rm -rf /", root / "home")
            raise AssertionError("Allowlist should reject unknown provider")
        except ValueError:
            pass
        assert plan_sign_in_command("codex", "/usr/bin/codex", env={}, platform="linux") == [
            "/usr/bin/codex",
            "login",
            "--device-auth",
        ]
        assert plan_sign_in_command(
            "codex", "/usr/bin/codex", env={"WAYLAND_DISPLAY": "wayland-0"}, platform="linux"
        ) == ["/usr/bin/codex", "login"]
        assert plan_sign_in_command(
            "claude", "/usr/bin/claude", env={"DISPLAY": ":0"}, platform="linux"
        ) == [
            "/usr/bin/claude",
            "auth",
            "login",
            "--claudeai",
        ]
        assert plan_sign_in_command(
            "agy", "/home/u/.local/bin/agy", env={"WAYLAND_DISPLAY": "wayland-0"}, platform="linux"
        ) == ["/home/u/.local/bin/agy"]
        assert resolve_antigravity_platform("linux", "aarch64") == "linux_arm64"
        assert resolve_antigravity_platform("linux", "x86_64") == "linux_amd64"
        assert resolve_antigravity_platform("darwin", "arm64") == "darwin_arm64"
        _sample_b = b"nido-agy-payload"
        _good_sha512 = hashlib.sha512(_sample_b).hexdigest()
        assert verify_sha512_hex(_sample_b, _good_sha512) is True
        assert verify_sha512_hex(_sample_b, "0" * 128) is False

        # Verify agy status detection via `agy models`
        fake_agy_bin = backend.home_dir / ".local" / "bin" / "agy"
        fake_agy_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_agy_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_agy_bin.chmod(0o755)
        st_agy_unauth = probe_provider_status(
            "agy",
            home_dir=backend.home_dir,
            runner=lambda argv, _in, _to: (
                CommandResult(0, "1.5.13\n", "")
                if argv[1:] == ["--version"]
                else CommandResult(1, "", "Error: Please sign in to view available models.")
            ),
        )
        assert st_agy_unauth["installed"] is True and st_agy_unauth["signedIn"] is False
        st_agy_auth = probe_provider_status(
            "agy",
            home_dir=backend.home_dir,
            runner=lambda argv, _in, _to: (
                CommandResult(0, "1.5.13\n", "")
                if argv[1:] == ["--version"]
                else CommandResult(
                    0,
                    "Fetching available models...\ngemini-4-neon-high\tGemini 4 Neon (High)\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n",
                    "",
                )
            ),
        )
        assert st_agy_auth["installed"] is True and st_agy_auth["signedIn"] is True
        assert "2 models" in st_agy_auth["accountLabel"]
        fake_agy_bin.unlink()
        status_path = write_agents_status(
            backend.nidoai_dir,
            {
                "codex": {
                    "installed": True,
                    "signedIn": True,
                    "plan": "Plus",
                    "source": "orca",
                    "accessToken": "LEAKED_TOKEN_MUST_BE_STRIPPED",
                }
            },
        )
        assert (status_path.stat().st_mode & 0o777) == 0o600
        assert "LEAKED_TOKEN_MUST_BE_STRIPPED" not in status_path.read_text(encoding="utf-8")
        read_back = read_agents_status(backend.nidoai_dir)
        assert read_back["codex"]["signedIn"] is True and read_back["codex"]["plan"] == "Plus"

        # 4. Catalog, Native-First AppStore dynamic arch detection & safe cancellation
        items = backend.load_catalog_items()
        assert len(items) == 1 and items[0]["id"] == "core"

        vendors_test = root / "vendors.json"
        vendors_test.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "vendors": {
                        "google-chrome": {
                            "id": "google-chrome",
                            "name": "Google Chrome Official APT",
                            "uri": "https://dl.google.com/linux/chrome/deb",
                            "suites": ["stable"],
                            "components": ["main"],
                            "keyUrl": "https://dl.google.com/linux/linux_signing_key.pub",
                            "expectedFingerprints": ["EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796"],
                            "pinOrigin": "dl.google.com",
                            "allowedPackages": ["google-chrome-stable"],
                            "pinPriority": 500,
                            "suppressVendorSelfSource": "google-chrome",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        cat_v2 = root / "catalog-v2.json"
        cat_v2.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "editions": [
                        {
                            "id": "core",
                            "name": "Nido Core",
                            "source": "nido-apt",
                            "packages": ["nido-hub"],
                            "description": "Core",
                        }
                    ],
                    "apps": [
                        {
                            "id": "spotify",
                            "name": "Spotify",
                            "source": "flatpak",
                            "flatpakId": "com.spotify.Client",
                            "arches": ["amd64"],
                            "description": "Music",
                        },
                        {
                            "id": "telegram",
                            "name": "Telegram",
                            "source": "flatpak",
                            "flatpakId": "org.telegram.desktop",
                            "description": "Chat",
                        },
                        {
                            "id": "vlc",
                            "name": "VLC",
                            "source": "ubuntu-apt",
                            "packages": ["vlc"],
                            "description": "Media",
                        },
                        {
                            "id": "chrome",
                            "name": "Google Chrome",
                            "source": "vendor-apt",
                            "vendorId": "google-chrome",
                            "packages": ["google-chrome-stable"],
                            "description": "Browser",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        def mock_fetcher(url: str, _timeout: float) -> bytes:
            if "dl.google.com" in url and url.endswith("/Release"):
                return b"Origin: Google LLC\nArchitectures: amd64 arm64\nComponents: main\n"
            if "com.spotify.Client" in url:
                return json.dumps({"arches": ["x86_64"], "download_size": 160_000_000}).encode()
            if "org.telegram.desktop" in url:
                return json.dumps({"arches": ["aarch64", "x86_64"], "download_size": 80_000_000}).encode()
            raise RuntimeError(f"Unexpected URL: {url}")

        def mock_flathub_search(_url: str, _payload: dict[str, Any], _timeout: float) -> dict[str, Any]:
            return {
                "hits": [
                    {
                        "app_id": "com.spotify.Client",
                        "name": "Spotify",
                        "summary": "Music",
                        "arches": ["x86_64"],
                    },
                    {
                        "app_id": "dev.diegovsky.Riff",
                        "name": "Riff",
                        "summary": "Spotify player for arm64",
                        "arches": ["x86_64", "aarch64"],
                    },
                ]
            }

        def mock_apt_prober(pkg_name: str, _source: str) -> bool | None:
            return pkg_name in {"nido-hub", "vlc"}

        store_arm64 = NidoAppStore(
            catalog_path=cat_v2,
            vendors_path=vendors_test,
            host_arch="arm64",
            url_fetcher=mock_fetcher,
            json_poster=mock_flathub_search,
            apt_candidate_prober=mock_apt_prober,
        )
        # Pre-probe / offline load immediately hides known x86_64-only apps on arm64
        instant_arm64 = store_arm64.load_catalog_entries(include_arch_probe=False)
        assert "spotify" not in [r["id"] for r in instant_arm64], (
            f"x86_64-only app must be hidden immediately on arm64: {[r['id'] for r in instant_arm64]}"
        )
        # Dynamic probe hides unsupported apps by default while preserving Native-First order
        ranked = store_arm64.load_catalog_entries(include_arch_probe=True)
        assert [r["id"] for r in ranked] == ["core", "chrome", "vlc", "telegram"], (
            f"Expected only arm64-supported apps in Native-First order, got {[r['id'] for r in ranked]}"
        )
        chrome_entry = next(r for r in ranked if r["id"] == "chrome")
        assert chrome_entry["archSupported"] is True, "Chrome arm64 must be dynamically detected as supported"
        # Empty / unsynced local APT lists (e.g. fresh install before `apt update`) must NOT hide ubuntu-apt apps
        empty_lists = root / "empty-apt-lists"
        empty_lists.mkdir()
        assert has_populated_apt_lists("ubuntu-apt", empty_lists) is False
        assert has_populated_apt_lists("nido-apt", empty_lists) is False
        pop_lists = root / "populated-apt-lists"
        pop_lists.mkdir()
        (pop_lists / "ports.ubuntu.com_ubuntu-ports_dists_resolute_universe_binary-arm64_Packages").write_text(
            "Package: vlc\n", encoding="utf-8"
        )
        (pop_lists / "ubuntu-nido-apt.pages.dev_dists_resolute_main_binary-arm64_Packages").write_text(
            "Package: nido-hub\n", encoding="utf-8"
        )
        assert has_populated_apt_lists("ubuntu-apt", pop_lists) is True
        assert has_populated_apt_lists("nido-apt", pop_lists) is True
        store_unsynced = NidoAppStore(
            catalog_path=cat_v2,
            vendors_path=vendors_test,
            host_arch="arm64",
            url_fetcher=mock_fetcher,
            json_poster=mock_flathub_search,
            apt_lists_dir=empty_lists,
        )
        unsynced_ids = [r["id"] for r in store_unsynced.load_catalog_entries(include_arch_probe=True)]
        assert "vlc" in unsynced_ids and "core" in unsynced_ids, (
            f"Fresh install without populated APT lists must never hide ubuntu-apt/nido-apt apps: {unsynced_ids}"
        )
        store_missing_pkg = NidoAppStore(
            catalog_path=cat_v2,
            vendors_path=vendors_test,
            host_arch="arm64",
            url_fetcher=mock_fetcher,
            json_poster=mock_flathub_search,
            apt_candidate_prober=lambda pkg, _src: pkg != "vlc",
        )
        assert "vlc" not in [r["id"] for r in store_missing_pkg.load_catalog_entries(include_arch_probe=True)], (
            "Genuinely unavailable APT package in synced cache must be hidden"
        )
        # Raw diagnostic view still marks x86_64-only app as archSupported=False
        raw_arm64 = store_arm64.load_catalog_entries(include_arch_probe=True, hide_unsupported=False)
        spotify_raw = next(r for r in raw_arm64 if r["id"] == "spotify")
        assert spotify_raw["archSupported"] is False, "Spotify x86_64-only must be dynamically detected on aarch64"
        # Flathub search on arm64 must hide x86_64-only hits and keep aarch64 hits
        search_arm64 = store_arm64.search_packages_native_first("spotify", include_flathub=True)
        search_flatpak_ids = [r["flatpakId"] for r in search_arm64 if r["source"] == "flatpak"]
        assert "com.spotify.Client" not in search_flatpak_ids, (
            f"x86_64-only Flathub search hit leaked on arm64: {search_flatpak_ids}"
        )
        assert "dev.diegovsky.Riff" in search_flatpak_ids, f"aarch64 Flathub search hit missing: {search_flatpak_ids}"
        # Same catalog on amd64 must show Spotify
        store_amd64 = NidoAppStore(
            catalog_path=cat_v2,
            vendors_path=vendors_test,
            host_arch="amd64",
            url_fetcher=mock_fetcher,
            json_poster=mock_flathub_search,
            apt_candidate_prober=mock_apt_prober,
        )
        ranked_amd64 = store_amd64.load_catalog_entries(include_arch_probe=True)
        assert "spotify" in [r["id"] for r in ranked_amd64], "amd64 must display x86_64 apps"
        # Live probe takes precedence if upstream adds aarch64 support later
        store_future = NidoAppStore(
            catalog_path=cat_v2,
            vendors_path=vendors_test,
            host_arch="arm64",
            url_fetcher=lambda u, _t: (
                json.dumps({"arches": ["x86_64", "aarch64"]}).encode()
                if "com.spotify.Client" in u
                else mock_fetcher(u, _t)
            ),
            apt_candidate_prober=mock_apt_prober,
        )
        assert "spotify" in [r["id"] for r in store_future.load_catalog_entries(include_arch_probe=True)]

        op_handle = OperationHandle(label="test-op")
        ok_cancel, _ = op_handle.cancel()
        assert ok_cancel is True and op_handle.cancelled is True
        op_commit = OperationHandle(label="commit-op")
        op_commit.set_safe_to_cancel(False)
        ok_commit_cancel, cancel_reason = op_commit.cancel()
        assert ok_commit_cancel is False and "dpkg" in cancel_reason

        # Verify Hardware-Aware AI Discovery, Vision Detection, Capability Matrix & Live Verify
        hw_low = HardwareProfile("arm64", 4, 4.0, 14.0, "Virtio GPU (VM)", 0.0, "cloud-first")
        hw_mid = HardwareProfile("amd64", 8, 16.0, 40.0, "NVIDIA RTX 3060", 6.0, "hybrid")
        hw_pro = HardwareProfile("amd64", 16, 32.0, 120.0, "NVIDIA RTX 4080", 16.0, "local-pro")
        rec_low = recommend_local_models(hw_low)
        rec_mid = recommend_local_models(hw_mid)
        rec_pro = recommend_local_models(hw_pro)
        assert not any(m.fits_hardware and m.size_gb >= 4.0 for m in rec_low), "4GB VM must not mark 7B+ as fitting"
        assert any(m.id == "gemma3:4b" and m.recommended and m.supports_vision for m in rec_mid)
        assert any(m.id == "qwen2.5vl:7b" and m.recommended and m.supports_vision for m in rec_pro)
        assert is_vision_model("gemma3:4b") is True and is_vision_model("gemma3:1b") is False
        assert is_vision_model("qwen2.5vl:7b") is True and is_vision_model("qwen2.5:7b") is False

        # Distinguish Ollama 0-model vs ready Vision model
        local_empty = probe_local_ai_runtimes(
            home_dir=backend.home_dir,
            http_getter=lambda url, _h, _t: (200, {"models": []}, "") if "11434" in url else (0, None, "down"),
        )
        assert local_empty["ollama"]["state"] == "installed-no-models"
        assert local_empty["hasReadyLocalModel"] is False

        local_text_only = probe_local_ai_runtimes(
            home_dir=backend.home_dir,
            http_getter=lambda url, _h, _t: (200, {"models": [{"name": "qwen2.5:7b"}]}, "")
            if "11434" in url
            else (0, None, "down"),
        )
        assert local_text_only["ollama"]["state"] == "ready"
        assert local_text_only["hasReadyLocalModel"] is True
        assert local_text_only["hasVisionLocalModel"] is False
        caps_text = compute_capability_matrix(
            {"primaryBackend": {"kind": "local", "configured": True, "reachable": True, "defaultModel": "qwen2.5:7b"}},
            local_text_only,
            [],
        )
        assert caps_text["nidooffice_ai"]["ready"] is True
        assert caps_text["nidobox_ocr"]["ready"] is False, "Text-only Ollama must not mark Vision OCR ready"

        caps_vision = compute_capability_matrix(
            {"primaryBackend": {"kind": "gemini", "configured": True, "reachable": True, "defaultModel": "gemini-2.5-flash"}},
            local_empty,
            [],
        )
        assert caps_vision["nidobox_ocr"]["ready"] is True and caps_vision["nidooffice_ai"]["ready"] is True

        # Signed-in Antigravity (Google AI Ultra/Pro) enables NidoOffice AI + NidoAgent even without Gateway key
        caps_agy_only = compute_capability_matrix(
            {"primaryBackend": {"kind": "gemini", "configured": False, "reachable": False, "defaultModel": "gemini-2.5-flash"}},
            local_empty,
            [{"id": "agy", "installed": True, "signedIn": True}],
        )
        assert caps_agy_only["nidooffice_ai"]["ready"] is True
        assert caps_agy_only["nidoagent"]["ready"] is True
        assert caps_agy_only["nidobox_ocr"]["ready"] is False

        v_empty = live_verify_backend(
            "local",
            local_base_url=OLLAMA_DEFAULT_BASE_URL,
            http_getter=lambda _u, _h, _t: (200, {"models": []}, ""),
        )
        assert v_empty["ok"] is False and "0 model" in v_empty["message"]
        v_vision = live_verify_backend(
            "local",
            local_base_url=OLLAMA_DEFAULT_BASE_URL,
            http_getter=lambda _u, _h, _t: (200, {"models": [{"name": "qwen2.5:7b"}, {"name": "gemma3:4b"}]}, ""),
        )
        assert v_vision["ok"] is True and v_vision["selectedModel"] == "gemma3:4b" and v_vision["visionReady"] is True
        v_bad_key = live_verify_backend(
            "gemini",
            api_key="bad-key",
            http_getter=lambda _u, _h, _t: (400, None, "API_KEY_INVALID"),
        )
        assert v_bad_key["ok"] is False and "không hợp lệ" in v_bad_key["message"]

        sha_sample = "a" * 64 + "  ollama-linux-arm64.tar.zst\n" + "b" * 64 + "  ollama-linux-amd64.tar.zst\n"
        assert parse_ollama_sha256sum(sha_sample, "ollama-linux-arm64.tar.zst") == "a" * 64

        # Verify NIDOAI_LOCAL_MODEL persists cleanly
        backend.save_ai_settings(
            primary_backend="local",
            local_base_url=OLLAMA_DEFAULT_BASE_URL,
            local_model="gemma3:4b",
            restart_service=False,
        )
        assert backend.load_env_file_only().get("NIDOAI_LOCAL_MODEL") == "gemma3:4b"

        for sample in (
            "Thành phần & Ứng dụng <Ubuntu-Nido>",
            "Cụm 1 — NidoAI Gateway (NidoBox OCR & NidoOffice)",
            "Cụm 2 — Tài khoản Agent (Free / Plus / Pro / Max)",
        ):
            parsed = ET.fromstring(f"<markup>{escape_markup(sample)}</markup>")
            assert parsed.text == sample, f"Markup round-trip mismatch: {parsed.text!r}"

        # Verify Markdown inline/block parser strips ** / ` into semantic spans while preserving math/globs/code
        md_sample = (
            "## Tổng hợp tài liệu\n\n"
            "* **Tên tài liệu:** Biên bản ký số `v0.13.1`\n"
            "  * **Chuẩn:** **PAdES** (*ETSI.CAdES.detached*)\n"
            "Phép nhân 2 * 3 * 4 = 24, glob *.deb và *.rpm, biến snake_case_var.\n"
            "```python\n"
            "def f(**kwargs):\n"
            "    return 2 * 3\n"
            "```\n"
        )
        spans_line = parse_inline_spans("**Tên tài liệu:** Biên bản `v0.13.1` (*chuẩn*)")
        assert ("bold", "Tên tài liệu:") in spans_line
        assert ("code", "v0.13.1") in spans_line
        assert ("italic", "chuẩn") in spans_line
        blocks_md = parse_markdown_blocks(md_sample)
        assert blocks_md[0]["type"] == "heading" and blocks_md[0]["level"] == 2
        assert any(b["type"] == "bullet" and b["level"] == 1 and b["glyph"] == "◦" for b in blocks_md)

        # Verify allowlisted GNOME Settings panel launcher guard
        try:
            build_settings_panel_argv("../../evil")
            raise AssertionError("Expected ValueError for disallowed settings panel")
        except ValueError:
            pass
        assert {"online-accounts", "users", "printers"}.issubset(ALLOWED_SETTINGS_PANELS)

        # Verify Global Printer Probe (sysfs + IEEE-1284 + Adapter-First + APT On-Demand)
        fake_sys = root / "fake_sys"
        usb_base = fake_sys / "sys/bus/usb/devices"
        # 1) Universal Office PDL printer (PCLXL/PCL5e)
        (usb_base / "1-1").mkdir(parents=True)
        (usb_base / "1-1/idVendor").write_text("03f0\n", encoding="utf-8")
        (usb_base / "1-1/idProduct").write_text("4117\n", encoding="utf-8")
        (usb_base / "1-1:1.0").mkdir(parents=True)
        (usb_base / "1-1:1.0/bInterfaceClass").write_text("07\n", encoding="utf-8")
        (usb_base / "1-1:1.0/ieee1284_id").write_text(
            "MFG:HP;MDL:LaserJet Pro;CMD:PCLXL,PCL5E,PJL;\n", encoding="utf-8"
        )
        # 2) Epson inkjet needing on-demand APT driver (ESC/P-R)
        (usb_base / "1-2").mkdir(parents=True)
        (usb_base / "1-2/idVendor").write_text("04b8\n", encoding="utf-8")
        (usb_base / "1-2/idProduct").write_text("1135\n", encoding="utf-8")
        (usb_base / "1-2:1.0").mkdir(parents=True)
        (usb_base / "1-2:1.0/bInterfaceClass").write_text("07\n", encoding="utf-8")
        (usb_base / "1-2:1.0/ieee1284_id").write_text(
            "MFG:EPSON;MDL:L3110 Series;CMD:ESCPL2,BDC,D4;\n", encoding="utf-8"
        )
        # 3) Legacy Canon CAPT LBP2900 on arm64
        (usb_base / "1-3").mkdir(parents=True)
        (usb_base / "1-3/idVendor").write_text("04a9\n", encoding="utf-8")
        (usb_base / "1-3/idProduct").write_text("2676\n", encoding="utf-8")
        (usb_base / "1-3:1.0").mkdir(parents=True)
        (usb_base / "1-3:1.0/bInterfaceClass").write_text("07\n", encoding="utf-8")
        (usb_base / "1-3:1.0/ieee1284_id").write_text("MFG:Canon;MDL:LBP2900;CMD:CAPT;\n", encoding="utf-8")

        printers_found = probe_printers(
            root_dir=fake_sys,
            host_arch="arm64",
            installed_packages={"cups", "ipp-usb"},
            cups_queues={},
            driverless_uris=[],
        )
        assert len(printers_found) == 3, f"Expected 3 USB printers, got {len(printers_found)}"
        hp_p = next(p for p in printers_found if p.vendor_id == "03f0")
        assert hp_p.status == "adapter-available" and hp_p.recommended_model == "drv:///sample.drv/pxlmono.ppd"
        epson_p = next(p for p in printers_found if p.vendor_id == "04b8")
        assert epson_p.status == "needs-apt" and "printer-driver-escpr" in epson_p.recommended_packages
        canon_p = next(p for p in printers_found if p.vendor_id == "04a9")
        assert canon_p.status == "arch-limited" and "arm64" in canon_p.note_vi

        lan_q, lan_uri, lan_mod = build_network_printer_spec("192.168.1.50", "pcl6")
        assert lan_q == "NidoLAN_192_168_1_50" and lan_uri == "socket://192.168.1.50:9100" and "pxlmono" in lan_mod

        # Verify NidoHub 3-tab navigation structure, icons, legacy --page aliases & gate guidance
        assert [(p, t) for p, t, _, _ in app_mod.NAV_TABS] == [
            ("assistant", "Trợ lý"),
            ("apps", "Ứng dụng"),
            ("settings", "Cài đặt"),
        ], f"Unexpected NAV_TABS: {app_mod.NAV_TABS}"
        assert [ic for _, _, ic, _ in app_mod.NAV_TABS] == [
            "user-available-symbolic",
            "system-software-install-symbolic",
            "preferences-system-symbolic",
        ]
        assert app_mod.resolve_page_name("assistant") == "assistant"
        assert app_mod.resolve_page_name("apps") == "apps"
        assert app_mod.resolve_page_name("settings") == "settings"
        assert app_mod.resolve_page_name("agent") == "assistant"
        assert app_mod.resolve_page_name("welcome") == "settings"
        assert app_mod.resolve_page_name("printers") == "apps"
        assert app_mod.resolve_page_name("") == "assistant"
        assert app_mod.resolve_page_name(None) == "assistant"
        assert app_mod.resolve_page_name("unknown-tab") == "assistant"

        # Verify jargon-free AI account titles, subtitles & collapsed summary formatter
        class _FakeAgentSpec:
            def __init__(self, pid: str, display_name: str, subtitle: str, download_url: str = "") -> None:
                self.id = pid
                self.display_name = display_name
                self.subtitle = subtitle
                self.download_url = download_url

        fake_agy = _FakeAgentSpec("agy", "Google Antigravity CLI", "SHA-512 (không cần root) OAuth CLI")
        assert app_mod._format_agent_row_title(fake_agy) == "Google / Gemini"
        assert app_mod._format_agent_row_title(_FakeAgentSpec("codex", "OpenAI Codex", "")) == "ChatGPT / OpenAI"
        assert app_mod._format_agent_row_title(_FakeAgentSpec("claude", "Anthropic Claude", "")) == "Claude / Anthropic"
        sub_installed = app_mod._format_agent_status_subtitle(fake_agy, {"installed": True, "signedIn": False})
        for forbidden in ("SHA-512", "không cần root", "CLI", "OAuth"):
            assert forbidden not in sub_installed, f"Technical jargon leaked in subtitle: {sub_installed}"
        assert "Google" in app_mod._format_other_ai_accounts_summary({"agy": {"signedIn": True}})
        assert "Đã kết nối 2 tài khoản" in app_mod._format_other_ai_accounts_summary(
            {"agy": {"signedIn": True}, "claude": {"signedIn": True}}
        )
        assert "Tùy chọn" in app_mod._format_other_ai_accounts_summary({})

        # Verify NidoOS UI styling & polished copywriting registries
        assert ".nido-status-tile" in NIDO_HUB_CSS and ".nido-footer-bar" in NIDO_HUB_CSS
        assert {"nido", "gemini", "openai", "anthropic", "local"} == {k for k, _ in PROVIDERS}
        assert all(SOURCE_LABELS.get(k) for k in ("nido-apt", "vendor-apt", "ubuntu-apt", "flatpak"))
        assert all(isinstance(v[0], str) and v[0] for v in UNIVERSAL_ADAPTER_MODELS.values())
        plain_md = render_markdown_to_plain(md_sample)
        assert "**Tên tài liệu:**" not in plain_md and "`v0.13.1`" not in plain_md
        assert "• Tên tài liệu: Biên bản ký số v0.13.1" in plain_md
        assert "  ◦ Chuẩn: PAdES (ETSI.CAdES.detached)" in plain_md
        assert "2 * 3 * 4 = 24" in plain_md and "*.deb và *.rpm" in plain_md and "snake_case_var" in plain_md
        assert "def f(**kwargs):" in plain_md, "Fenced code block must stay verbatim"

        # Verify Nido Account SSO + keyring isolation + quota capability + OS Settings Sync
        nido_home = root / "nido_account_home"
        nido_home.mkdir()
        (nido_home / ".config" / "vielotus").mkdir(parents=True, exist_ok=True)
        (nido_home / ".config" / "vielotus" / "config.env").write_text("INPUT_METHOD=telex\n", encoding="utf-8")
        (nido_home / ".config" / "vielotus" / "macros.json").write_text('{"chxh": "Cộng hòa xã hội"}\n', encoding="utf-8")
        (nido_home / ".config" / "nidobox").mkdir(parents=True, exist_ok=True)
        (nido_home / ".config" / "nidobox" / "settings.json").write_text(
            json.dumps({"lang": "vi", "background": True, "secretToken": "MUST_BE_STRIPPED"}),
            encoding="utf-8",
        )
        nido_keyring_mem: dict[str, str] = {}

        def nido_keyring_runner(
            argv: list[str], stdin_text: str | None, _timeout: float
        ) -> CommandResult:
            if argv[:2] == ["which", "secret-tool"]:
                return CommandResult(0, "/usr/bin/secret-tool\n", "")
            if argv[:2] == ["secret-tool", "store"]:
                acct = argv[argv.index("account") + 1]
                nido_keyring_mem[acct] = (stdin_text or "").strip()
                return CommandResult(0, "", "")
            if argv[:2] == ["secret-tool", "lookup"]:
                acct = argv[argv.index("account") + 1]
                if acct in nido_keyring_mem:
                    return CommandResult(0, nido_keyring_mem[acct] + "\n", "")
                return CommandResult(1, "", "")
            if argv[:2] == ["secret-tool", "clear"]:
                acct = argv[argv.index("account") + 1]
                nido_keyring_mem.pop(acct, None)
                return CommandResult(0, "", "")
            return CommandResult(1, "", "")

        def _make_fake_jwt(exp_offset_sec: int) -> str:
            hdr = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode("ascii").rstrip("=")
            exp_ts = int(time.time()) + exp_offset_sec
            body = base64.urlsafe_b64encode(json.dumps({"sub": "u-1", "exp": exp_ts}).encode("utf-8")).decode("ascii").rstrip("=")
            return f"{hdr}.{body}.sig"

        fresh_jwt = _make_fake_jwt(3600)
        expired_jwt = _make_fake_jwt(-60)

        def mock_nido_http(method: str, url: str, headers: dict[str, str] | None, payload: Any, _to: float):
            if url.endswith("/auth/v1/token?grant_type=password"):
                return 200, {
                    "access_token": fresh_jwt,
                    "refresh_token": "refresh-tok-01",
                    "user": {"id": "u-1", "email": "user@nidoholdings.example", "user_metadata": {"full_name": "Minh Anh"}},
                }, ""
            if url.endswith("/auth/v1/token?grant_type=refresh_token"):
                return 200, {
                    "access_token": _make_fake_jwt(7200),
                    "refresh_token": "refresh-tok-02",
                }, ""
            if url.endswith("/auth/v1/signup"):
                return 200, {"user": {"id": "u-2", "email": "new@nidoholdings.example"}}, ""
            if url.endswith("/auth/v1/logout"):
                return 204, {}, ""
            if url.endswith("/quota"):
                return 200, {
                    "ok": True,
                    "userId": "u-1",
                    "planCode": "companion",
                    "isCompanionPro": True,
                    "source": "educenter",
                    "quotaSnapshot": {"aiCreditsQuota": 1500, "aiCreditsUsed": 120, "aiCreditsRemaining": 1380},
                }, ""
            return 404, {"error": "not found"}, "not found"

        nido_store = NidoSecretStore(
            platform="linux",
            env={"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"},
            runner=nido_keyring_runner,
        )
        nido_backend = NidoHubBackend(home_dir=nido_home, catalog_path=cat_file, secret_store=nido_store)
        nido_backend.nido_account.http_requester = mock_nido_http

        ok_reg, _, reg_info = nido_backend.register_nido_account("new@nidoholdings.example", "secret123", "Minh", restart_service=False)
        assert ok_reg and reg_info.get("emailConfirmationRequired") is True

        ok_lgn, msg_lgn, prof_lgn = nido_backend.login_nido_account("user@nidoholdings.example", "secret123", restart_service=False)
        assert ok_lgn and prof_lgn is not None and prof_lgn["isPro"] is True, msg_lgn
        assert prof_lgn["quota"]["aiCreditsRemaining"] == 1380
        # Tokens stored only in keyring, NEVER plaintext in .env.local or account.json
        assert nido_keyring_mem.get("NIDO_ACCOUNT_ACCESS_TOKEN") == fresh_jwt
        assert fresh_jwt not in nido_backend.env_file.read_text(encoding="utf-8")
        acct_json_txt = nido_backend.nido_account.profile_path.read_text(encoding="utf-8")
        assert fresh_jwt not in acct_json_txt and "refresh-tok" not in acct_json_txt

        # Verify refresh rotation when JWT expires
        nido_keyring_mem["NIDO_ACCOUNT_ACCESS_TOKEN"] = expired_jwt
        ok_ref, _, _ = nido_backend.nido_account.refresh_session(force=False)
        assert ok_ref and nido_keyring_mem["NIDO_ACCOUNT_ACCESS_TOKEN"] != expired_jwt
        assert nido_keyring_mem["NIDO_ACCOUNT_REFRESH_TOKEN"] == "refresh-tok-02"

        # Verify capability matrix + onboarding gate + Stage 3 OS Sync round-trip
        st_nido = nido_backend.get_nido_account_status(refresh_quota=False)
        caps_nido = compute_capability_matrix(
            {"primaryBackend": {"kind": "nido", "configured": True, "reachable": True, "defaultModel": "nido-llama"}},
            local_empty,
            [],
            nido_account=st_nido,
        )
        assert caps_nido["nidooffice_ai"]["ready"] is True and caps_nido["nidoagent"]["ready"] is True
        assert caps_nido["nidobox_ocr"]["ready"] is False, "Text-only Nido AI proxy must not mark Vision OCR ready"

        gate_nido = evaluate_ai_onboarding_gate(True, [], nido_account=st_nido)
        assert gate_nido["canComplete"] and gate_nido["signedInProviders"] == ["nido"]
        ok_ob_nido, _ = nido_backend.complete_ai_onboarding(ai_enabled=True, restart_service=False, agent_states=[])
        assert ok_ob_nido and nido_backend.load_env_file_only().get("NIDOAI_PRIMARY_BACKEND") == "nido"

        ok_exp, _, sync_file = nido_backend.export_os_sync_bundle()
        assert ok_exp and sync_file.is_file()
        sync_raw = sync_file.read_text(encoding="utf-8")
        assert "MUST_BE_STRIPPED" not in sync_raw and "Cộng hòa xã hội" in sync_raw
        (nido_home / ".config" / "vielotus" / "macros.json").unlink()
        ok_imp, _, restored = nido_backend.import_os_sync_bundle(sync_file, restart_service=False)
        assert ok_imp and "vielotus:macros.json" in restored
        assert "Cộng hòa xã hội" in (nido_home / ".config" / "vielotus" / "macros.json").read_text(encoding="utf-8")

        ok_out, _ = nido_backend.logout_nido_account(restart_service=False)
        assert ok_out and not nido_backend.nido_account.profile_path.is_file()
        assert "NIDO_ACCOUNT_ACCESS_TOKEN" not in nido_keyring_mem

        # Regression test: signing in or auto-signing-up after prior AI OFF (NIDOAI_ENABLED=0)
        # MUST re-enable NIDOAI_ENABLED=1 and bootstrap NIDOAI_CLIENT_TOKEN so NidoOffice SSO works.
        nido_backend._write_env_file({"NIDOAI_ENABLED": "0"})
        ok_relogin, _, _ = nido_backend.login_nido_account("user@nidoholdings.example", "secret123", restart_service=False)
        assert ok_relogin
        env_after_relogin = nido_backend.load_env_file_only()
        assert env_after_relogin.get("NIDOAI_ENABLED") == "1", f"login must flip NIDOAI_ENABLED=0 -> 1: {env_after_relogin}"
        assert len(env_after_relogin.get("NIDOAI_CLIENT_TOKEN", "")) >= 16

        nido_backend._write_env_file({"NIDOAI_ENABLED": "0"})
        def mock_nido_http_auto_signup(method: str, url: str, headers: dict[str, str] | None, payload: Any, _to: float):
            if url.endswith("/auth/v1/signup"):
                return 200, {
                    "access_token": fresh_jwt,
                    "refresh_token": "refresh-tok-signup",
                    "user": {"id": "u-3", "email": "auto@nidoholdings.example", "user_metadata": {"full_name": "Auto User"}},
                }, ""
            return mock_nido_http(method, url, headers, payload, _to)
        nido_backend.nido_account.http_requester = mock_nido_http_auto_signup
        ok_autoreg, _, autoreg_info = nido_backend.register_nido_account("auto@nidoholdings.example", "secret123", "Auto User", restart_service=False)
        assert ok_autoreg and autoreg_info.get("profile") is not None
        env_after_autoreg = nido_backend.load_env_file_only()
        assert env_after_autoreg.get("NIDOAI_ENABLED") == "1", f"register must flip NIDOAI_ENABLED=0 -> 1: {env_after_autoreg}"
        assert len(env_after_autoreg.get("NIDOAI_CLIENT_TOKEN", "")) >= 16
        nido_backend.logout_nido_account(restart_service=False)

        # Verify mandatory AI ON/OFF onboarding gate policy
        gate_on_empty = evaluate_ai_onboarding_gate(
            True,
            [
                {"id": "agy", "installed": True, "signedIn": False},
                {"id": "codex", "installed": False, "signedIn": False},
                {"id": "claude", "installed": False, "signedIn": False},
            ],
        )
        assert not gate_on_empty["canComplete"], "AI ON with 0 signed-in accounts must be blocked"

        gate_on_agy = evaluate_ai_onboarding_gate(
            True,
            [
                {"id": "agy", "installed": True, "signedIn": True},
                {"id": "codex", "installed": False, "signedIn": False},
            ],
        )
        assert gate_on_agy["canComplete"] and gate_on_agy["signedInProviders"] == ["agy"]

        gate_on_codex = evaluate_ai_onboarding_gate(
            True,
            [{"id": "codex", "installed": True, "signedIn": True}],
        )
        assert gate_on_codex["canComplete"] and gate_on_codex["signedInProviders"] == ["codex"]

        gate_on_claude = evaluate_ai_onboarding_gate(
            True,
            [{"id": "claude", "installed": True, "signedIn": True}],
        )
        assert gate_on_claude["canComplete"] and gate_on_claude["signedInProviders"] == ["claude"]

        gate_off = evaluate_ai_onboarding_gate(False, [])
        assert gate_off["canComplete"] and not gate_off["aiEnabled"]

        ob_home = root / "onboarding_home"
        ob_home.mkdir()
        ob_backend = NidoHubBackend(home_dir=ob_home, catalog_path=cat_file)
        assert ob_backend.is_onboarding_required(), "Fresh user must require AI onboarding"

        # AI ON without signed-in account MUST fail even if API key is provided
        ok_block, msg_block = ob_backend.complete_ai_onboarding(
            ai_enabled=True,
            gemini_key="test-gemini-key-placeholder-01",
            restart_service=False,
            agent_states=[{"id": "agy", "installed": True, "signedIn": False}],
        )
        assert not ok_block and "bắt buộc đăng nhập" in msg_block.lower()
        assert ob_backend.is_onboarding_required(), "Blocked onboarding must not mark complete"

        # AI OFF must succeed immediately, write NIDOAI_ENABLED=0, delete stale runtime.json, and block run_agent_task cleanly
        stale_rt = ob_home / ".nidoai" / "runtime.json"
        stale_rt.parent.mkdir(parents=True, exist_ok=True)
        stale_rt.write_text('{"baseUrl":"http://127.0.0.1:18090"}\n', encoding="utf-8")
        ok_off, _ = ob_backend.complete_ai_onboarding(
            ai_enabled=False,
            restart_service=False,
            agent_states=[],
        )
        assert ok_off
        assert not ob_backend.is_onboarding_required()
        assert not ob_backend.is_ai_enabled()
        assert not stale_rt.exists(), "Turning AI OFF must remove stale runtime.json"
        assert ob_backend.load_env_file_only().get("NIDOAI_ENABLED") == "0"
        ok_task_off, msg_task_off = ob_backend.run_agent_task("test off")
        assert not ok_task_off and "tắt" in msg_task_off.lower() and "fetch failed" not in msg_task_off.lower()
        assert "cài đặt" in msg_task_off.lower() and "tab trợ lý ai" not in msg_task_off.lower(), msg_task_off

        # AI ON with 0 signed-in accounts / 0 configured backends must refuse run_agent_task cleanly before fetch
        unsigned_home = root / "unsigned_home"
        unsigned_home.mkdir()
        unsigned_backend = NidoHubBackend(
            home_dir=unsigned_home,
            catalog_path=cat_file,
            secret_store=NidoSecretStore(platform="linux", env={"NIDO_SECRET_STORE": "file"}),
        )
        unsigned_backend._write_env_file({"NIDOAI_PORT": "18999", "NIDOAI_ENABLED": "1"})
        unsigned_backend.probe_agent_accounts = lambda persist=False: []  # type: ignore[method-assign]
        orig_probe_local = app_mod.probe_local_ai_runtimes
        app_mod.probe_local_ai_runtimes = lambda _h: {
            "ollama": {"running": False},
            "installedModels": [],
        }
        try:
            ok_task_unsigned, msg_task_unsigned = unsigned_backend.run_agent_task("test unsigned")
        finally:
            app_mod.probe_local_ai_runtimes = orig_probe_local
        assert not ok_task_unsigned and "chưa đăng nhập" in msg_task_unsigned.lower(), (
            ok_task_unsigned,
            msg_task_unsigned,
        )
        assert "cài đặt" in msg_task_unsigned.lower() and "tab trợ lý ai" not in msg_task_unsigned.lower(), (
            msg_task_unsigned
        )
        assert "fetch failed" not in msg_task_unsigned.lower()

        # Switching to AI ON with >=1 signed-in account + optional API key must succeed
        ok_on, _ = ob_backend.complete_ai_onboarding(
            ai_enabled=True,
            primary_backend="gemini",
            gemini_key="test-gemini-key-placeholder-02",
            restart_service=False,
            verify_live=False,
            agent_states=[{"id": "agy", "installed": True, "signedIn": True}],
        )
        assert ok_on
        assert not ob_backend.is_onboarding_required()
        assert ob_backend.is_ai_enabled()
        assert ob_backend.load_env_file_only().get("NIDOAI_ENABLED") == "1"
        assert ob_backend.load_ai_policy()["signedInProviders"] == ["agy"]

        cached_rep = ob_backend.get_cached_ai_readiness_report()
        assert {"capabilities", "hardwareLineVi", "agents", "nidoAccount"} <= set(cached_rep.keys())

        t_kill_start = time.monotonic()
        res_to = default_command_runner(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.3,
        )
        t_kill_elapsed = time.monotonic() - t_kill_start
        assert res_to.returncode == 127 and t_kill_elapsed < 3.0, (res_to, t_kill_elapsed)

        # Verify parse_hub_argv (single-instance / CLI parser)
        p_def = app_mod.parse_hub_argv([])
        assert p_def.page == "assistant" and not p_def.first_login and not p_def.onboarding
        p_leg = app_mod.parse_hub_argv(["--page", "welcome", "--first-login"])
        assert app_mod.resolve_page_name(p_leg.page) == "settings" and p_leg.first_login is True
        p_deb = app_mod.parse_hub_argv(["/tmp/example.deb"])
        assert p_deb.package_file == "/tmp/example.deb"

        # Verify NidoAccountManager cross-process refresh lock: stale lock recovery & peer-rotated token coalescing
        lock_home = root / "lock_home"
        lock_home.mkdir()
        lock_store = NidoSecretStore(platform="linux", env={"NIDO_SECRET_STORE": "file"})
        lock_backend = NidoHubBackend(home_dir=lock_home, catalog_path=cat_file, secret_store=lock_store)
        refresh_posts = [0]

        def mock_lock_http(method: str, url: str, headers: dict[str, str] | None, payload: Any, _to: float):
            if url.endswith("/auth/v1/token?grant_type=refresh_token"):
                refresh_posts[0] += 1
                return 200, {"access_token": fresh_jwt, "refresh_token": "refresh-rotated-new"}, ""
            if url.endswith("/quota"):
                return 200, {"ok": True, "userId": "u-lock", "planCode": "free", "quotaSnapshot": {"aiCreditsQuota": 50, "aiCreditsRemaining": 50}}, ""
            return 404, {}, ""

        lock_backend.nido_account.http_requester = mock_lock_http
        lock_backend.nido_account._save_tokens(expired_jwt, "refresh-old", set_as_primary=True)
        # Plant a dead-PID lockfile and verify automatic stale-lock recovery
        lock_file = lock_backend.nido_account.nidoai_dir / ".nido-refresh.lock"
        lock_file.write_text('{"pid": 9999999, "ts": 1}\n', encoding="utf-8")
        ok_lk, _, _ = lock_backend.nido_account.refresh_session(force=False)
        assert ok_lk and refresh_posts[0] == 1 and not lock_file.exists(), (ok_lk, refresh_posts[0])

        # Verify run_agent_task timeout enforcement & OperationHandle.cancel() process-group termination
        fake_sleep_agent = root / "fake-nidoagent-sleep.py"
        fake_sleep_agent.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n", encoding="utf-8")
        fake_sleep_agent.chmod(0o755)
        import os as _os
        prev_agent_env = _os.environ.get("NIDOAGENT_BIN")
        prev_allow_env = _os.environ.get("NIDOAGENT_ALLOW_UNCONFIGURED_TEST")
        _os.environ["NIDOAGENT_BIN"] = str(fake_sleep_agent)
        _os.environ["NIDOAGENT_ALLOW_UNCONFIGURED_TEST"] = "1"
        try:
            t_ag = time.monotonic()
            ok_ag_to, msg_ag_to = unsigned_backend.run_agent_task("test timeout", timeout_sec=0.35)
            elapsed_ag = time.monotonic() - t_ag
            assert not ok_ag_to and "quá thời gian" in msg_ag_to.lower() and elapsed_ag < 3.0, (msg_ag_to, elapsed_ag)

            import threading as _thr
            cancel_handle = OperationHandle(label="cancel-agent")
            def _cancel_soon() -> None:
                time.sleep(0.25)
                cancel_handle.cancel()
            _thr.Thread(target=_cancel_soon, daemon=True).start()
            t_can = time.monotonic()
            ok_ag_can, msg_ag_can = unsigned_backend.run_agent_task("test cancel", timeout_sec=10.0, handle=cancel_handle)
            elapsed_can = time.monotonic() - t_can
            assert not ok_ag_can and cancel_handle.cancelled and elapsed_can < 3.0, (msg_ag_can, elapsed_can)
        finally:
            if prev_agent_env is None:
                _os.environ.pop("NIDOAGENT_BIN", None)
            else:
                _os.environ["NIDOAGENT_BIN"] = prev_agent_env
            if prev_allow_env is None:
                _os.environ.pop("NIDOAGENT_ALLOW_UNCONFIGURED_TEST", None)
            else:
                _os.environ["NIDOAGENT_ALLOW_UNCONFIGURED_TEST"] = prev_allow_env
    print("nido-hub self-test passed.")
    return 0


if __name__ == "__main__":
    import app as _app

    raise SystemExit(run_self_test(_app))
