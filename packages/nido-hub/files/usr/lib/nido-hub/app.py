#!/usr/bin/env python3
"""NidoHub Desktop — Native GTK4 / Libadwaita Welcome, App Manager & Agent UI."""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Ensure sibling helper module (agent_accounts.py) resolves both installed and in sandbox tests.
_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from agent_accounts import (  # noqa: E402
    MANAGED_AGENT_SPECS,
    PROVIDER_API_SECRET_KEYS,
    CommandResult,
    NidoSecretStore,
    connect_agent_provider,
    disconnect_agent_provider,
    plan_managed_install_command,
    plan_sign_in_command,
    probe_all_agents,
    probe_provider_status,
    read_agents_status,
    resolve_antigravity_platform,
    verify_sha512_hex,
    write_agents_status,
)
from ai_discovery import (  # noqa: E402
    HardwareProfile,
    OLLAMA_DEFAULT_BASE_URL,
    build_ai_readiness_report,
    compute_capability_matrix,
    detect_hardware,
    install_ollama_user,
    is_vision_model,
    live_verify_backend,
    parse_ollama_sha256sum,
    probe_local_ai_runtimes,
    pull_ollama_model,
    recommend_local_models,
)
from app_store import (  # noqa: E402
    CATEGORY_LABELS,
    NidoAppStore,
    OperationHandle,
)
from markdown_render import (  # noqa: E402
    parse_inline_spans,
    parse_markdown_blocks,
    render_markdown_to_gtk_buffer,
    render_markdown_to_plain,
)

PROVIDERS = [
    ("gemini", "Google Gemini API (Miễn phí $0 / BYOK)"),
    ("openai", "OpenAI API (BYOK)"),
    ("anthropic", "Anthropic Console API (BYOK)"),
    ("local", "Local Ollama (Offline 127.0.0.1:11434)"),
]

GEMINI_FREE_KEY_URL = "https://aistudio.google.com/apikey"

REQUIRED_AGENT_PROVIDER_IDS = ("agy", "codex", "claude")
REQUIRED_AGENT_LABELS = {
    "agy": "Google / Gemini (agy)",
    "codex": "ChatGPT / OpenAI (codex)",
    "claude": "Claude / Anthropic (claude)",
}


def evaluate_ai_onboarding_gate(
    ai_enabled: bool,
    agent_states: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate whether mandatory first-run AI onboarding may complete.

    Policy:
    - AI OFF -> allowed to complete immediately without signing in.
    - AI ON  -> requires at least 1 signed-in AI account among Google/Gemini (agy),
      ChatGPT (codex), or Claude (claude); API keys may be added optionally.
    """
    signed_ids: list[str] = []
    for entry in agent_states:
        pid = str(entry.get("id") or "")
        if pid in REQUIRED_AGENT_PROVIDER_IDS and bool(entry.get("signedIn")):
            if pid not in signed_ids:
                signed_ids.append(pid)
    signed_labels = [REQUIRED_AGENT_LABELS.get(pid, pid) for pid in signed_ids]

    if not ai_enabled:
        return {
            "aiEnabled": False,
            "signedInProviders": signed_ids,
            "signedInLabels": signed_labels,
            "canComplete": True,
            "statusBadge": "AI ĐANG TẮT",
            "reasonVi": (
                "Chế độ AI đang TẮT — bấm 'Hoàn tất (Tắt AI)' để vào thẳng màn hình chính. "
                "Bạn có thể bật lại AI bất cứ lúc nào trong NidoHub."
            ),
        }

    if signed_ids:
        joined = ", ".join(signed_labels)
        return {
            "aiEnabled": True,
            "signedInProviders": signed_ids,
            "signedInLabels": signed_labels,
            "canComplete": True,
            "statusBadge": f"ĐẠT YÊU CẦU ({len(signed_ids)} TÀI KHOẢN)",
            "reasonVi": (
                f"Đã đăng nhập {joined}. Bạn có thể thêm API Key bên dưới (nếu có) "
                "rồi bấm 'Hoàn tất & Bắt đầu dùng Ubuntu-Nido'."
            ),
        }

    return {
        "aiEnabled": True,
        "signedInProviders": [],
        "signedInLabels": [],
        "canComplete": False,
        "statusBadge": "BẮT BUỘC — 0/1 TÀI KHOẢN",
        "reasonVi": (
            "Chế độ AI đang BẬT: bắt buộc đăng nhập ít nhất 1 tài khoản "
            "(Google/Gemini, ChatGPT hoặc Claude) ở Bước 1 trước khi hoàn tất, "
            "hoặc gạt công tắc sang TẮT AI."
        ),
    }


def escape_markup(text: str) -> str:
    """Escape text for GTK/Libadwaita widgets whose labels parse Pango markup."""
    return html.escape(text, quote=False)


class NidoHubBackend:
    """Headless-testable state and process backend for NidoHub."""

    def __init__(
        self,
        home_dir: Path | None = None,
        catalog_path: Path | None = None,
        secret_store: NidoSecretStore | None = None,
    ) -> None:
        if home_dir is not None:
            self.home_dir = home_dir
            self.nidoai_dir = home_dir / ".nidoai"
            self.config_dir = home_dir / ".config" / "ubuntu-nido"
            self.secret_store = secret_store or NidoSecretStore(env={"NIDOAI_SECRET_STORE": "file"})
        else:
            self.home_dir = Path(os.environ.get("HOME", str(Path.home())))
            self.nidoai_dir = Path(os.environ.get("NIDOAI_HOME", str(self.home_dir / ".nidoai")))
            self.config_dir = Path(
                os.environ.get("XDG_CONFIG_HOME", str(self.home_dir / ".config"))
            ) / "ubuntu-nido"
            self.secret_store = secret_store or NidoSecretStore()
        self.catalog_path = catalog_path or Path(
            os.environ.get("NIDOHUB_CATALOG", "/etc/nido-hub/catalog.json")
        )
        self.app_store = NidoAppStore(catalog_path=self.catalog_path)

    @property
    def env_file(self) -> Path:
        return self.nidoai_dir / ".env.local"

    @property
    def welcome_done_file(self) -> Path:
        return self.config_dir / "welcome-done"

    @property
    def ai_policy_file(self) -> Path:
        return self.config_dir / "ai-policy.json"

    def load_ai_policy(self) -> dict[str, Any]:
        if self.ai_policy_file.is_file():
            try:
                raw = json.loads(self.ai_policy_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return {
                        "schemaVersion": 1,
                        "setupCompleted": bool(raw.get("setupCompleted", False)),
                        "aiEnabled": bool(raw.get("aiEnabled", True)),
                        "signedInProviders": list(raw.get("signedInProviders") or []),
                        "updatedAt": str(raw.get("updatedAt") or ""),
                    }
            except (OSError, ValueError):
                pass

        env = self.load_env_file_only()
        flag = env.get("NIDOAI_ENABLED", "").strip().lower()
        ai_enabled = flag not in {"0", "false", "off", "no"}
        return {
            "schemaVersion": 1,
            "setupCompleted": self.welcome_done_file.is_file(),
            "aiEnabled": ai_enabled,
            "signedInProviders": [],
            "updatedAt": "",
        }

    def is_ai_enabled(self) -> bool:
        return bool(self.load_ai_policy().get("aiEnabled", True))

    def is_onboarding_required(self) -> bool:
        return not bool(self.load_ai_policy().get("setupCompleted", False))

    def _write_ai_policy(
        self,
        setup_completed: bool,
        ai_enabled: bool,
        signed_in_providers: list[str] | None = None,
    ) -> dict[str, Any]:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload = {
            "schemaVersion": 1,
            "setupCompleted": bool(setup_completed),
            "aiEnabled": bool(ai_enabled),
            "signedInProviders": list(signed_in_providers or []),
            "updatedAt": now_iso,
        }
        fd, tmp_name = tempfile.mkstemp(dir=str(self.config_dir), prefix=".ai-policy.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.ai_policy_file)
            os.chmod(self.ai_policy_file, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return payload

    def set_ai_enabled(
        self,
        enabled: bool,
        restart_service: bool = True,
        mark_completed: bool = False,
        signed_in_providers: list[str] | None = None,
    ) -> str:
        cur = self.load_ai_policy()
        env = self.load_env_file_only()
        if enabled and len(env.get("NIDOAI_CLIENT_TOKEN", "")) < 16:
            env["NIDOAI_CLIENT_TOKEN"] = f"nidoai_{secrets.token_hex(24)}"
        env["NIDOAI_ENABLED"] = "1" if enabled else "0"
        self._write_env_file(env)

        completed = bool(mark_completed or cur.get("setupCompleted", False))
        providers = (
            list(signed_in_providers)
            if signed_in_providers is not None
            else list(cur.get("signedInProviders") or [])
        )
        self._write_ai_policy(completed, enabled, providers)
        if completed:
            self.welcome_done_file.write_text(
                datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n",
                encoding="utf-8",
            )

        if restart_service and shutil.which("systemctl"):
            if enabled:
                res = subprocess.run(
                    ["systemctl", "--user", "restart", "nidoai-gateway.service"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if res.returncode != 0:
                    subprocess.run(
                        ["systemctl", "--user", "enable", "--now", "nidoai-gateway.service"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
            else:
                subprocess.run(
                    ["systemctl", "--user", "stop", "nidoai-gateway.service"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        if enabled:
            return "Đã BẬT chế độ AI trên Ubuntu-Nido."
        return "Đã TẮT chế độ AI trên Ubuntu-Nido (có thể bật lại bất cứ lúc nào trong NidoHub)."

    def complete_ai_onboarding(
        self,
        ai_enabled: bool,
        primary_backend: str = "gemini",
        gemini_key: str = "",
        openai_key: str = "",
        anthropic_key: str = "",
        local_base_url: str = "",
        local_model: str = "",
        restart_service: bool = True,
        verify_live: bool = False,
        agent_states: list[dict[str, Any]] | None = None,
    ) -> tuple[bool, str]:
        if not ai_enabled:
            msg = self.set_ai_enabled(
                False,
                restart_service=restart_service,
                mark_completed=True,
                signed_in_providers=[],
            )
            return True, msg

        states = agent_states if agent_states is not None else self.probe_agent_accounts(persist=True)
        gate = evaluate_ai_onboarding_gate(True, states)
        if not gate["canComplete"]:
            return False, gate["reasonVi"]

        has_any_api_key = any(
            k.strip() for k in (gemini_key, openai_key, anthropic_key, local_base_url)
        )
        should_verify = bool(verify_live and has_any_api_key)
        save_msg = self.save_ai_settings(
            primary_backend=primary_backend,
            gemini_key=gemini_key,
            openai_key=openai_key,
            anthropic_key=anthropic_key,
            local_base_url=local_base_url,
            local_model=local_model,
            restart_service=restart_service,
            verify_live=should_verify,
        )
        # Ensure NIDOAI_ENABLED=1 & ai-policy.json reflect completed ON state
        env = self.load_env_file_only()
        env["NIDOAI_ENABLED"] = "1"
        self._write_env_file(env)
        self._write_ai_policy(True, True, gate["signedInProviders"])
        joined = ", ".join(gate["signedInLabels"])
        if has_any_api_key:
            return True, f"Hoàn tất thiết lập AI ({joined}). {save_msg}"
        return True, f"Hoàn tất thiết lập AI với tài khoản {joined}!"

    def load_env_file_only(self) -> dict[str, str]:
        if not self.env_file.is_file():
            return {}
        data: dict[str, str] = {}
        for raw in self.env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip("'\"")
        return data

    def load_env(self) -> dict[str, str]:
        data = self.load_env_file_only()
        if self.secret_store.is_keyring_active:
            for secret_key in PROVIDER_API_SECRET_KEYS:
                val = self.secret_store.get_secret(secret_key)
                if val:
                    data[secret_key] = val
        return data

    def _write_env_file(self, env: dict[str, str]) -> None:
        self.nidoai_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.nidoai_dir, 0o700)
        lines = [f"{k}={v}" for k, v in sorted(env.items())]
        fd, tmp_name = tempfile.mkstemp(dir=str(self.nidoai_dir), prefix=".env.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.env_file)
            os.chmod(self.env_file, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def ensure_gateway_ready(self, start_service: bool = True) -> None:
        if not self.is_ai_enabled():
            return
        env = self.load_env_file_only()
        if len(env.get("NIDOAI_CLIENT_TOKEN", "")) < 16:
            env["NIDOAI_CLIENT_TOKEN"] = f"nidoai_{secrets.token_hex(24)}"
            self._write_env_file(env)

        if not start_service:
            return

        port = os.environ.get("NIDOAI_PORT") or env.get("NIDOAI_PORT") or "18090"
        url = f"http://127.0.0.1:{port}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=0.6):
                return
        except Exception:
            pass

        if shutil.which("systemctl"):
            subprocess.run(
                ["systemctl", "--user", "start", "nidoai-gateway.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(6):
                try:
                    with urllib.request.urlopen(url, timeout=0.4):
                        return
                except Exception:
                    time.sleep(0.25)

    def save_ai_settings(
        self,
        primary_backend: str,
        gemini_key: str = "",
        openai_key: str = "",
        anthropic_key: str = "",
        local_base_url: str = "",
        restart_service: bool = True,
        local_model: str = "",
        verify_live: bool = False,
    ) -> str:
        if primary_backend not in {"gemini", "openai", "anthropic", "local"}:
            raise ValueError(f"Invalid backend: {primary_backend}")

        self.config_dir.mkdir(parents=True, exist_ok=True)

        env = self.load_env_file_only()
        if len(env.get("NIDOAI_CLIENT_TOKEN", "")) < 16:
            env["NIDOAI_CLIENT_TOKEN"] = f"nidoai_{secrets.token_hex(24)}"

        env["NIDOAI_PRIMARY_BACKEND"] = primary_backend

        keyring_active = self.secret_store.is_keyring_active
        stored_any_in_keyring = False
        for key_name, raw_val in (
            ("GEMINI_API_KEY", gemini_key),
            ("OPENAI_API_KEY", openai_key),
            ("ANTHROPIC_API_KEY", anthropic_key),
        ):
            trimmed = raw_val.strip()
            if not trimmed:
                continue
            if keyring_active and self.secret_store.set_secret(key_name, trimmed):
                # Strip plaintext key from .env.local once safely persisted to OS Keyring.
                env.pop(key_name, None)
                stored_any_in_keyring = True
            else:
                env[key_name] = trimmed

        if local_base_url.strip():
            env["NIDOAI_LOCAL_BASE_URL"] = local_base_url.strip()
        elif primary_backend == "local":
            env["NIDOAI_LOCAL_BASE_URL"] = OLLAMA_DEFAULT_BASE_URL
        else:
            env.pop("NIDOAI_LOCAL_BASE_URL", None)

        if local_model.strip():
            env["NIDOAI_LOCAL_MODEL"] = local_model.strip()

        live_note = ""
        if verify_live:
            resolved_key = {
                "gemini": gemini_key.strip() or self.load_env().get("GEMINI_API_KEY", ""),
                "openai": openai_key.strip() or self.load_env().get("OPENAI_API_KEY", ""),
                "anthropic": anthropic_key.strip() or self.load_env().get("ANTHROPIC_API_KEY", ""),
                "local": "",
            }.get(primary_backend, "")
            v_res = live_verify_backend(
                primary_backend,
                api_key=resolved_key,
                local_base_url=env.get("NIDOAI_LOCAL_BASE_URL", ""),
                local_model=env.get("NIDOAI_LOCAL_MODEL", ""),
            )
            if primary_backend == "local" and v_res.get("selectedModel") and not local_model.strip():
                env["NIDOAI_LOCAL_MODEL"] = str(v_res["selectedModel"])
            live_note = f" · {v_res['message']}"

        self._write_env_file(env)

        self.welcome_done_file.write_text(
            datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n",
            encoding="utf-8",
        )

        if restart_service and shutil.which("systemctl"):
            res = subprocess.run(
                ["systemctl", "--user", "restart", "nidoai-gateway.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode != 0:
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "nidoai-gateway.service"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

        if stored_any_in_keyring:
            return f"Đã lưu cấu hình NidoAI ({self.secret_store.mode_label_vi()})!{live_note}"
        return f"Đã lưu cấu hình NidoAI!{live_note}"

    def probe_gateway_health_detail(self) -> dict[str, Any] | None:
        env = self.load_env_file_only()
        port = os.environ.get("NIDOAI_PORT") or env.get("NIDOAI_PORT") or "18090"
        url = f"http://127.0.0.1:{port}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, dict) else None
        except Exception:
            return None

    def probe_gateway_health(self) -> tuple[bool, str]:
        payload = self.probe_gateway_health_detail()
        if not payload:
            return False, "NidoAI Gateway chưa phản hồi (sẽ tự bật sau khi lưu cấu hình)"
        pb = payload.get("primaryBackend", {})
        kind = pb.get("kind", "gemini")
        configured = bool(pb.get("configured", False))
        configured_raw = bool(pb.get("configuredRaw", configured))
        reachable = bool(pb.get("reachable", configured))
        caps = payload.get("capabilities", {})
        vision_txt = " · Vision OCR" if caps.get("vision") else ""
        if kind == "local" and configured_raw and not reachable:
            state_txt = "chưa bật Ollama cục bộ"
        elif kind == "local" and configured and reachable:
            local_info = probe_local_ai_runtimes(self.home_dir)
            if not local_info.get("hasReadyLocalModel"):
                state_txt = "Ollama đã chạy nhưng chưa tải model (0 model)"
            else:
                v_note = " · Vision OCR" if local_info.get("hasVisionLocalModel") else " · chỉ văn bản"
                state_txt = f"sẵn sàng ({len(local_info['installedModels'])} model{v_note})"
        elif configured and reachable:
            state_txt = f"sẵn sàng{vision_txt}"
        else:
            state_txt = "chưa cấu hình model/khóa AI"
        return True, f"NidoAI Gateway đang chạy ({kind} — {state_txt})"

    def get_ai_readiness_report(self, persist_agents: bool = False) -> dict[str, Any]:
        hw = detect_hardware(self.home_dir)
        local_info = probe_local_ai_runtimes(self.home_dir)
        gw_health = self.probe_gateway_health_detail()
        agents = self.probe_agent_accounts(persist=persist_agents)
        return build_ai_readiness_report(hw, local_info, gw_health, agents)

    def probe_agent_accounts(self, persist: bool = True) -> list[dict[str, Any]]:
        return probe_all_agents(self.home_dir, self.nidoai_dir, persist=persist)

    def connect_agent(self, provider_id: str) -> tuple[bool, str]:
        # Uses zero-root SHA-512-verified user-scoped npm bootstrap automatically when npm is missing.
        return connect_agent_provider(provider_id, self.home_dir, self.nidoai_dir)

    def install_ollama(self, on_progress: Any = None) -> tuple[bool, str]:
        if not shutil.which("zstd"):
            if on_progress:
                on_progress(3, "Đang cài công cụ giải nén zstd qua APT...")
            ok_z, msg_z, _ = self.app_store.install_item(
                {"name": "zstd", "source": "ubuntu-apt", "packages": ["zstd"]}
            )
            if not ok_z:
                return False, f"Không cài được zstd: {msg_z}"
        ok, msg, _ = install_ollama_user(self.home_dir, on_progress=on_progress)
        return ok, msg

    def pull_and_activate_local_model(
        self, model_name: str, on_progress: Any = None
    ) -> tuple[bool, str]:
        ok_p, msg_p = pull_ollama_model(model_name, self.home_dir, on_progress=on_progress)
        if not ok_p:
            return False, msg_p
        saved_msg = self.save_ai_settings(
            primary_backend="local",
            local_base_url=OLLAMA_DEFAULT_BASE_URL,
            local_model=model_name,
            restart_service=True,
            verify_live=True,
        )
        return True, f"{msg_p} {saved_msg}"

    def disconnect_agent(self, provider_id: str) -> tuple[bool, str]:
        return disconnect_agent_provider(provider_id, self.home_dir, self.nidoai_dir)

    def open_external_url(self, url: str) -> bool:
        opener = shutil.which("xdg-open") or shutil.which("open")
        if not opener:
            return False
        try:
            subprocess.Popen([opener, url], start_new_session=True)
            return True
        except OSError:
            return False

    def is_package_installed(self, pkg_name: str) -> bool:
        dpkg_query = shutil.which("dpkg-query")
        if not dpkg_query:
            return False
        res = subprocess.run(
            [dpkg_query, "-W", "-f=${Status}", pkg_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return res.returncode == 0 and "install ok installed" in res.stdout

    def load_catalog_items(self, include_arch_probe: bool = False) -> list[dict[str, Any]]:
        return self.app_store.load_catalog_entries(include_arch_probe=include_arch_probe)

    def install_packages(self, packages: list[str]) -> tuple[bool, str]:
        ok, msg, _ = self.app_store.install_item(
            {"name": ", ".join(packages), "source": "ubuntu-apt", "packages": packages}
        )
        return ok, msg

    def run_agent_task(
        self,
        prompt: str,
        files: list[str] | None = None,
        out_path: str | None = None,
    ) -> tuple[bool, str]:
        self.ensure_gateway_ready(start_service="NIDOAGENT_BIN" not in os.environ)
        agent_bin = os.environ.get("NIDOAGENT_BIN") or shutil.which("nidoagent") or "/usr/bin/nidoagent"
        argv = [agent_bin, "ask"]
        for fpath in files or []:
            argv.extend(["--file", fpath])
        if out_path:
            argv.extend(["--out", out_path])
        argv.append(prompt.strip() or "Tóm tắt nội dung và tạo báo cáo tổng hợp.")

        res = subprocess.run(argv, check=False, capture_output=True, text=True)
        output = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        return res.returncode == 0, output.strip()


def run_self_test() -> int:
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
        import hashlib
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
                            "description": "Music",
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
            raise RuntimeError(f"Unexpected URL: {url}")

        store_arm64 = NidoAppStore(
            catalog_path=cat_v2,
            vendors_path=vendors_test,
            host_arch="arm64",
            url_fetcher=mock_fetcher,
        )
        ranked = store_arm64.load_catalog_entries(include_arch_probe=True)
        # Native-First ordering: nido-apt -> vendor-apt -> ubuntu-apt -> flatpak
        assert [r["source"] for r in ranked] == ["nido-apt", "vendor-apt", "ubuntu-apt", "flatpak"], (
            f"Expected Native-First ordering, got {[r['source'] for r in ranked]}"
        )
        chrome_entry = next(r for r in ranked if r["id"] == "chrome")
        assert chrome_entry["archSupported"] is True, "Chrome arm64 must be dynamically detected as supported"
        spotify_entry = next(r for r in ranked if r["id"] == "spotify")
        assert spotify_entry["archSupported"] is False, "Spotify x86_64-only must be dynamically detected on aarch64"

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

        import xml.etree.ElementTree as ET

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
        plain_md = render_markdown_to_plain(md_sample)
        assert "**Tên tài liệu:**" not in plain_md and "`v0.13.1`" not in plain_md
        assert "• Tên tài liệu: Biên bản ký số v0.13.1" in plain_md
        assert "  ◦ Chuẩn: PAdES (ETSI.CAdES.detached)" in plain_md
        assert "2 * 3 * 4 = 24" in plain_md and "*.deb và *.rpm" in plain_md and "snake_case_var" in plain_md
        assert "def f(**kwargs):" in plain_md, "Fenced code block must stay verbatim"

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

        # AI OFF must succeed immediately and write NIDOAI_ENABLED=0
        ok_off, _ = ob_backend.complete_ai_onboarding(
            ai_enabled=False,
            restart_service=False,
            agent_states=[],
        )
        assert ok_off
        assert not ob_backend.is_onboarding_required()
        assert not ob_backend.is_ai_enabled()
        assert ob_backend.load_env_file_only().get("NIDOAI_ENABLED") == "0"

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
    print("nido-hub self-test passed.")
    return 0


def can_launch_gtk() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi  # type: ignore

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401

        return True
    except Exception:
        return False


def _format_agent_status_subtitle(spec: Any, state: dict[str, Any]) -> str:
    if state.get("signedIn"):
        acct = state.get("accountLabel") or "Đã đăng nhập"
        plan = f" · {state['plan']}" if state.get("plan") else ""
        src = " (đồng bộ từ ORCA)" if state.get("source") == "orca" else ""
        return f"Đã kết nối: {acct}{plan}{src}"
    if state.get("installed"):
        ver = f" ({state['version']})" if state.get("version") else ""
        return f"Đã cài CLI{ver} — chưa đăng nhập. {spec.subtitle}"
    if getattr(spec, "download_url", ""):
        return f"Chưa cài — {spec.subtitle}"
    return f"Chưa cài CLI — bấm 1 lần để tự tải (không cần root) & đăng nhập. {spec.subtitle}"


def _resolve_opened_package_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    val = raw.strip()
    if val.startswith("file://"):
        parsed = urllib.parse.urlparse(val)
        val = urllib.parse.unquote(parsed.path)
    p = Path(val).expanduser()
    if p.is_file() and p.suffix.lower() in (".deb", ".flatpakref"):
        return p.resolve()
    return None


def run_gtk_app(
    backend: NidoHubBackend,
    initial_page: str,
    package_file: Path | None = None,
    onboarding_mode: bool = False,
    first_login_launch: bool = False,
) -> int:
    import gi  # type: ignore

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk  # type: ignore

    def _make_plain_row(row: Any, title: str, subtitle: str | None = None) -> Any:
        if hasattr(row, "set_use_markup"):
            row.set_use_markup(False)
        row.set_title(title)
        if subtitle is not None and hasattr(row, "set_subtitle"):
            row.set_subtitle(subtitle)
        return row

    def _make_group(title: str = "", description: str = "") -> Any:
        kwargs: dict[str, str] = {}
        if title:
            kwargs["title"] = escape_markup(title)
        if description:
            kwargs["description"] = escape_markup(description)
        return Adw.PreferencesGroup(**kwargs)

    def _add_toast(overlay: Any, message: str) -> None:
        toast = Adw.Toast.new(message)
        if hasattr(toast, "set_use_markup"):
            toast.set_use_markup(False)
        else:
            toast.set_title(escape_markup(message))
        overlay.add_toast(toast)

    class NidoHubApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="com.nidoholdings.NidoHub",
                flags=Gio.ApplicationFlags.NON_UNIQUE,
            )
            self.selected_files: list[str] = []

        def do_activate(self) -> None:
            Gtk.Window.set_default_icon_name("com.nidoholdings.NidoHub")
            win = Adw.ApplicationWindow(application=self)
            win.set_icon_name("com.nidoholdings.NidoHub")
            win.set_title("NidoHub — Ubuntu-Nido")
            win.set_default_size(920, 720)

            root_stack = Gtk.Stack()
            root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            onboarding_active = [bool(onboarding_mode)]
            allow_close = [False]
            ob_poll_source_id: list[int] = [0]
            ob_poll_ticks = [0]

            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            stack = Adw.ViewStack()

            switcher = Adw.ViewSwitcher()
            switcher.set_stack(stack)
            switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(switcher)
            toolbar.add_top_bar(header)

            toast_overlay = Adw.ToastOverlay()
            toast_overlay.set_child(stack)
            toolbar.set_content(toast_overlay)

            # --- Page 1: AI Setup (Hardware-Aware Discovery + Gateway + Official Agents + Local Ollama) ---
            ai_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            backend.ensure_gateway_ready(start_service=True)
            banner = Adw.Banner()
            if hasattr(banner, "set_use_markup"):
                banner.set_use_markup(False)
            _, gw_msg = backend.probe_gateway_health()
            if not backend.is_ai_enabled():
                gw_msg = "Chế độ AI đang TẮT trên Ubuntu-Nido — gạt công tắc Bật AI bên dưới để kích hoạt."
            banner.set_title(gw_msg if hasattr(banner, "set_use_markup") else escape_markup(gw_msg))
            banner.set_revealed(True)
            ai_box.append(banner)

            ai_page = Adw.PreferencesPage()
            env = backend.load_env()
            initial_report = backend.get_ai_readiness_report(persist_agents=False)

            # Group 0 (Pinned Top): Master AI ON/OFF Switch + Hardware-Aware AI Discovery
            diag_group = _make_group(
                title="Trạng thái AI & Đề xuất theo cấu hình máy",
                description=initial_report["recommendationVi"],
            )
            ai_master_guard = [False]
            ai_master_switch = _make_plain_row(
                Adw.SwitchRow(),
                "Kích hoạt Trợ lý AI Ubuntu-Nido (AI ON / OFF)",
                "Bật để dùng Google/Gemini, ChatGPT, Claude, NidoAgent & NidoOffice AI; Tắt để không chạy nền AI.",
            )
            ai_master_switch.set_active(backend.is_ai_enabled())
            diag_group.add(ai_master_switch)

            reopen_ob_row = _make_plain_row(
                Adw.ActionRow(),
                "Trình hướng dẫn thiết lập AI ban đầu (ON/OFF & Đăng nhập tài khoản)",
                "Mở lại màn hình thiết lập tài khoản Google/Gemini, ChatGPT, Claude và API Key tùy chọn.",
            )
            reopen_ob_btn = Gtk.Button(label="Mở màn hình thiết lập", valign=Gtk.Align.CENTER)
            reopen_ob_row.add_suffix(reopen_ob_btn)
            diag_group.add(reopen_ob_row)

            hw_row = _make_plain_row(
                Adw.ActionRow(),
                "Phần cứng & Môi trường AI phát hiện được",
                f"{initial_report['hardwareLineVi']} · {initial_report['ollamaStatusVi']}",
            )
            rescan_btn = Gtk.Button(label="Quét lại", valign=Gtk.Align.CENTER)
            hw_row.add_suffix(rescan_btn)
            diag_group.add(hw_row)

            cap_rows: dict[str, Any] = {}
            for cap_key in ("nidobox_ocr", "nidooffice_ai", "nidoagent"):
                cap = initial_report["capabilities"][cap_key]
                c_row = _make_plain_row(
                    Adw.ActionRow(),
                    f"{cap['title']} — [{cap['badge']}]",
                    cap["detail"],
                )
                diag_group.add(c_row)
                cap_rows[cap_key] = c_row

            # Group 1: Stateless App Gateway (BYOK / Free Gemini Key / Local Ollama)
            prov_group = _make_group(
                title="1. NidoAI Gateway — Ứng dụng nền (NidoBox OCR, NidoOffice, NidoAgent)",
                description=(
                    "Thiết lập 1 nơi duy nhất — tự động chia sẻ qua 127.0.0.1:18090 cho toàn hệ sinh thái. "
                    f"Kho khóa: {backend.secret_store.mode_label_vi()}."
                ),
            )
            model_list = Gtk.StringList.new([label for _, label in PROVIDERS])
            prov_row = _make_plain_row(Adw.ComboRow(model=model_list), "Nguồn AI mặc định")
            cur_prov = env.get("NIDOAI_PRIMARY_BACKEND", "gemini")
            prov_idx = next((i for i, (k, _) in enumerate(PROVIDERS) if k == cur_prov), 0)
            prov_row.set_selected(prov_idx)
            prov_group.add(prov_row)

            free_key_row = _make_plain_row(
                Adw.ActionRow(),
                "Khóa Google AI Studio miễn phí ($0 — khuyên dùng cho máy dưới 8 GB RAM)",
                "Mở trình duyệt tạo GEMINI_API_KEY miễn phí rồi dán vào ô bên dưới để bật ngay Vision OCR & NidoOffice AI.",
            )
            get_key_btn = Gtk.Button(label="Lấy khóa miễn phí", valign=Gtk.Align.CENTER)
            get_key_btn.connect(
                "clicked",
                lambda _b: _add_toast(
                    toast_overlay,
                    "Đã mở Google AI Studio trong trình duyệt."
                    if backend.open_external_url(GEMINI_FREE_KEY_URL)
                    else f"Mở trình duyệt truy cập: {GEMINI_FREE_KEY_URL}",
                ),
            )
            free_key_row.add_suffix(get_key_btn)
            prov_group.add(free_key_row)

            gemini_row = _make_plain_row(Adw.PasswordEntryRow(), "Google Gemini API Key")
            gemini_row.set_text(env.get("GEMINI_API_KEY", ""))
            prov_group.add(gemini_row)

            openai_row = _make_plain_row(Adw.PasswordEntryRow(), "OpenAI API Key (BYOK)")
            openai_row.set_text(env.get("OPENAI_API_KEY", ""))
            prov_group.add(openai_row)

            anthropic_row = _make_plain_row(
                Adw.PasswordEntryRow(), "Anthropic Console API Key (BYOK)"
            )
            anthropic_row.set_text(env.get("ANTHROPIC_API_KEY", ""))
            prov_group.add(anthropic_row)

            ollama_row = _make_plain_row(
                Adw.EntryRow(), "Local Ollama Endpoint (mặc định http://127.0.0.1:11434/v1)"
            )
            ollama_row.set_text(env.get("NIDOAI_LOCAL_BASE_URL", ""))
            prov_group.add(ollama_row)

            local_model_row = _make_plain_row(
                Adw.EntryRow(), "Model cục bộ mặc định (VD: gemma3:4b, qwen2.5:7b — để trống sẽ tự chọn)"
            )
            local_model_row.set_text(env.get("NIDOAI_LOCAL_MODEL", ""))
            prov_group.add(local_model_row)

            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            btn_box.set_halign(Gtk.Align.CENTER)
            btn_box.set_margin_top(12)

            save_btn = Gtk.Button(label="Lưu & Xác thực sống")
            save_btn.add_css_class("suggested-action")
            save_btn.add_css_class("pill")

            check_btn = Gtk.Button(label="Kiểm tra kết nối")
            check_btn.add_css_class("pill")

            # Forward refs updated by refresh_readiness_ui()
            ollama_status_row: Any = None
            ollama_install_btn: Any = None
            ollama_model_btns: list[tuple[str, Gtk.Button]] = []
            agent_rows: dict[str, tuple[Any, Gtk.Button, Any]] = {}

            def _format_banner_line(gw_line: str, agent_states_list: list[dict[str, Any]], caps: dict[str, Any]) -> str:
                if not backend.is_ai_enabled():
                    return "Chế độ AI đang TẮT trên Ubuntu-Nido — gạt công tắc Bật AI bên dưới để kích hoạt."
                signed = [str(a.get("id", "")).upper() for a in agent_states_list if a.get("signedIn")]
                if signed and "chưa cấu hình" in gw_line:
                    names = ", ".join(signed)
                    return (
                        f"Đã kết nối {names} cho NidoOffice & NidoAgent · "
                        "Gateway chưa có khóa API (chỉ cần thêm nếu bật NidoBox OCR)"
                    )
                return gw_line

            def _apply_agent_rows_ui(states_map: dict[str, dict[str, Any]]) -> None:
                for spec in MANAGED_AGENT_SPECS:
                    if spec.id not in agent_rows:
                        continue
                    st = states_map.get(spec.id, {"installed": False, "signedIn": False})
                    row_w, btn_w, _ = agent_rows[spec.id]
                    row_w.set_subtitle(_format_agent_status_subtitle(spec, st))
                    if st.get("signedIn"):
                        btn_w.set_label("Đăng xuất")
                        btn_w.remove_css_class("suggested-action")
                    elif st.get("installed"):
                        btn_w.set_label("Đăng nhập trình duyệt")
                        btn_w.add_css_class("suggested-action")
                    elif getattr(spec, "download_url", ""):
                        btn_w.set_label("Mở trang tải")
                        btn_w.remove_css_class("suggested-action")
                    else:
                        btn_w.set_label("Cài & Đăng nhập")
                        btn_w.add_css_class("suggested-action")
                    btn_w.set_sensitive(True)

            def refresh_readiness_ui(persist_agents: bool = False, show_toast: bool = False) -> None:
                def worker() -> None:
                    agents_list = backend.probe_agent_accounts(persist=persist_agents)
                    hw = detect_hardware(backend.home_dir)
                    local_info = probe_local_ai_runtimes(backend.home_dir)
                    gw_detail = backend.probe_gateway_health_detail()
                    rep = build_ai_readiness_report(hw, local_info, gw_detail, agents_list)
                    _, gw_line = backend.probe_gateway_health()
                    banner_line = _format_banner_line(gw_line, agents_list, rep["capabilities"])
                    states_map = {e["id"]: e for e in agents_list}

                    def apply_ui() -> bool:
                        banner.set_title(
                            banner_line if hasattr(banner, "set_use_markup") else escape_markup(banner_line)
                        )
                        hw_row.set_subtitle(
                            f"{rep['hardwareLineVi']} · {rep['ollamaStatusVi']}"
                        )
                        for ck in ("nidobox_ocr", "nidooffice_ai", "nidoagent"):
                            c_info = rep["capabilities"][ck]
                            cap_rows[ck].set_title(f"{c_info['title']} — [{c_info['badge']}]")
                            cap_rows[ck].set_subtitle(c_info["detail"])
                        _apply_agent_rows_ui(states_map)
                        ol_info = rep["local"]["ollama"]
                        if ollama_status_row is not None:
                            ollama_status_row.set_subtitle(rep["ollamaStatusVi"])
                        if ollama_install_btn is not None:
                            if ol_info.get("installed"):
                                ollama_install_btn.set_label("Đã cài Ollama")
                                ollama_install_btn.set_sensitive(False)
                                ollama_install_btn.remove_css_class("suggested-action")
                            else:
                                ollama_install_btn.set_label("Cài Ollama (SHA256)")
                                ollama_install_btn.set_sensitive(True)
                                ollama_install_btn.add_css_class("suggested-action")
                        installed_set = set(rep["local"].get("installedModels", []))
                        for mid, mbtn in ollama_model_btns:
                            is_pulled = any(m == mid or m.startswith(f"{mid}:") for m in installed_set)
                            mbtn.set_label("Kích hoạt" if is_pulled else "Tải & Kích hoạt")
                            mbtn.set_sensitive(True)
                        if show_toast:
                            _add_toast(toast_overlay, banner_line[:160])
                        return False

                    GLib.idle_add(apply_ui)

                threading.Thread(target=worker, daemon=True).start()

            def on_save_clicked(_btn: Gtk.Button) -> None:
                sel = prov_row.get_selected()
                kind = PROVIDERS[sel][0] if 0 <= sel < len(PROVIDERS) else "gemini"
                g_key = gemini_row.get_text()
                o_key = openai_row.get_text()
                a_key = anthropic_row.get_text()
                l_url = ollama_row.get_text()
                l_mod = local_model_row.get_text()
                _btn.set_sensitive(False)
                _btn.set_label("Đang xác thực...")

                def worker() -> None:
                    saved_msg = backend.save_ai_settings(
                        primary_backend=kind,
                        gemini_key=g_key,
                        openai_key=o_key,
                        anthropic_key=a_key,
                        local_base_url=l_url,
                        local_model=l_mod,
                        restart_service=True,
                        verify_live=True,
                    )

                    def finish() -> bool:
                        _btn.set_sensitive(True)
                        _btn.set_label("Lưu & Xác thực sống")
                        _add_toast(toast_overlay, saved_msg[:160])
                        refresh_readiness_ui(persist_agents=False)
                        return False

                    GLib.idle_add(finish)

                threading.Thread(target=worker, daemon=True).start()

            def on_check_clicked(_btn: Gtk.Button) -> None:
                refresh_readiness_ui(persist_agents=True, show_toast=True)

            rescan_btn.connect("clicked", lambda _b: refresh_readiness_ui(persist_agents=True, show_toast=True))
            save_btn.connect("clicked", on_save_clicked)
            check_btn.connect("clicked", on_check_clicked)
            btn_box.append(save_btn)
            btn_box.append(check_btn)

            btn_group = Adw.PreferencesGroup()
            btn_group.add(btn_box)

            # Group 2: Stateful Agent Accounts (Official CLI + System Browser OAuth)
            agents_group = _make_group(
                title="2. Tài khoản Agent (Google AI Ultra / Pro / Free · ChatGPT · Claude)",
                description=(
                    "Cài 1 chạm CLI chính chủ kèm xác thực SHA-512 (không cần root) và đăng nhập OAuth hợp lệ ToS. "
                    "Tự động dùng được ngay trong NidoOffice & NidoAgent."
                ),
            )

            initial_states_list = backend.probe_agent_accounts(persist=False)
            initial_states = {entry["id"]: entry for entry in initial_states_list}
            init_banner = _format_banner_line(gw_msg, initial_states_list, initial_report["capabilities"])
            banner.set_title(
                init_banner if hasattr(banner, "set_use_markup") else escape_markup(init_banner)
            )

            def refresh_agent_rows() -> None:
                refresh_readiness_ui(persist_agents=True, show_toast=False)

            for spec in MANAGED_AGENT_SPECS:
                st = initial_states.get(spec.id, {"installed": False, "signedIn": False})
                row = _make_plain_row(
                    Adw.ActionRow(),
                    spec.display_name,
                    _format_agent_status_subtitle(spec, st),
                )
                btn_label = (
                    "Đăng xuất"
                    if st.get("signedIn")
                    else (
                        "Đăng nhập trình duyệt"
                        if st.get("installed")
                        else ("Mở trang tải" if getattr(spec, "download_url", "") else "Cài & Đăng nhập")
                    )
                )
                act_btn = Gtk.Button(label=btn_label, valign=Gtk.Align.CENTER)
                if not st.get("signedIn") and not (not st.get("installed") and getattr(spec, "download_url", "")):
                    act_btn.add_css_class("suggested-action")

                def on_agent_btn_clicked(
                    _b: Gtk.Button, pid: str = spec.id
                ) -> None:
                    is_logout = _b.get_label() == "Đăng xuất"
                    _b.set_sensitive(False)
                    _b.set_label("Đang xử lý...")

                    def worker() -> None:
                        if is_logout:
                            ok_a, msg_a = backend.disconnect_agent(pid)
                        else:
                            ok_a, msg_a = backend.connect_agent(pid)

                        def finish_ui() -> bool:
                            _add_toast(toast_overlay, msg_a[:140])
                            refresh_agent_rows()
                            return False

                        GLib.idle_add(finish_ui)

                    threading.Thread(target=worker, daemon=True).start()

                act_btn.connect("clicked", on_agent_btn_clicked)
                row.add_suffix(act_btn)
                agents_group.add(row)
                agent_rows[spec.id] = (row, act_btn, spec)

            # Group 3: Hardware-Matched Local AI (Ollama SHA256 Installer + Model Puller)
            ollama_group = _make_group(
                title="3. AI Cục bộ Offline (Ollama — Khuyến nghị theo RAM & GPU máy)",
                description=(
                    "Cài đặt chính chủ kèm xác thực SHA256 vào thư mục người dùng (không cần root, chỉ nghe trên 127.0.0.1:11434)."
                ),
            )

            ol_prog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            ol_prog_box.set_visible(False)
            ol_prog_lbl = Gtk.Label(xalign=0)
            ol_prog_lbl.set_ellipsize(3)
            ol_prog_bar = Gtk.ProgressBar()
            ol_prog_box.append(ol_prog_lbl)
            ol_prog_box.append(ol_prog_bar)
            ollama_group.add(ol_prog_box)

            ol_init = initial_report["local"]["ollama"]
            ollama_status_row = _make_plain_row(
                Adw.ActionRow(),
                "Runtime Ollama cục bộ",
                initial_report["ollamaStatusVi"],
            )
            ollama_install_btn = Gtk.Button(
                label="Đã cài Ollama" if ol_init.get("installed") else "Cài Ollama (SHA256)",
                valign=Gtk.Align.CENTER,
            )
            if ol_init.get("installed"):
                ollama_install_btn.set_sensitive(False)
            else:
                ollama_install_btn.add_css_class("suggested-action")

            def _set_ol_progress(pct: int, text: str) -> None:
                def _ui() -> bool:
                    ol_prog_box.set_visible(True)
                    ol_prog_bar.set_fraction(max(0.0, min(1.0, pct / 100.0)))
                    ol_prog_lbl.set_text(text)
                    return False

                GLib.idle_add(_ui)

            def on_install_ollama_clicked(_b: Gtk.Button) -> None:
                _b.set_sensitive(False)
                _b.set_label("Đang cài...")
                _set_ol_progress(2, "Đang chuẩn bị cài đặt Ollama...")

                def worker() -> None:
                    ok_o, msg_o = backend.install_ollama(on_progress=_set_ol_progress)

                    def finish() -> bool:
                        if ok_o:
                            ol_prog_box.set_visible(False)
                        _add_toast(toast_overlay, msg_o[:150])
                        refresh_readiness_ui(persist_agents=False)
                        return False

                    GLib.idle_add(finish)

                threading.Thread(target=worker, daemon=True).start()

            ollama_install_btn.connect("clicked", on_install_ollama_clicked)
            ollama_status_row.add_suffix(ollama_install_btn)
            ollama_group.add(ollama_status_row)

            installed_models_init = set(initial_report["local"].get("installedModels", []))
            for rec in initial_report["recommendedModels"]:
                mid = str(rec["id"])
                tags: list[str] = []
                if rec["recommended"]:
                    tags.append("Khuyên dùng cho máy này")
                elif not rec["fits_hardware"]:
                    tags.append(f"Cần >= {rec['min_ram_gb']:.0f} GB RAM")
                tags.append(f"~{rec['size_gb']:.1f} GB")
                if rec["supports_vision"]:
                    tags.append("Vision OCR")
                m_row = _make_plain_row(
                    Adw.ActionRow(),
                    rec["label"],
                    f"[{' · '.join(tags)}] {rec['reason_vi']}",
                )
                is_pulled = any(m == mid or m.startswith(f"{mid}:") for m in installed_models_init)
                m_btn = Gtk.Button(
                    label="Kích hoạt" if is_pulled else "Tải & Kích hoạt",
                    valign=Gtk.Align.CENTER,
                )
                if rec["recommended"]:
                    m_btn.add_css_class("suggested-action")

                def on_pull_model_clicked(_b: Gtk.Button, target_mid: str = mid) -> None:
                    _b.set_sensitive(False)
                    _b.set_label("Đang tải...")
                    _set_ol_progress(4, f"Đang tải & kích hoạt {target_mid}...")

                    def worker() -> None:
                        ok_m, msg_m = backend.pull_and_activate_local_model(
                            target_mid, on_progress=_set_ol_progress
                        )

                        def finish() -> bool:
                            if ok_m:
                                ol_prog_box.set_visible(False)
                                ollama_row.set_text(OLLAMA_DEFAULT_BASE_URL)
                                local_model_row.set_text(target_mid)
                                local_idx = next(
                                    (i for i, (k, _) in enumerate(PROVIDERS) if k == "local"), 3
                                )
                                prov_row.set_selected(local_idx)
                            _add_toast(toast_overlay, msg_m[:150])
                            refresh_readiness_ui(persist_agents=False)
                            return False

                        GLib.idle_add(finish)

                    threading.Thread(target=worker, daemon=True).start()

                m_btn.connect("clicked", on_pull_model_clicked)
                m_row.add_suffix(m_btn)
                ollama_group.add(m_row)
                ollama_model_btns.append((mid, m_btn))

            refresh_agents_btn = Gtk.Button(label="Làm mới toàn bộ trạng thái AI")
            refresh_agents_btn.add_css_class("pill")
            refresh_agents_btn.set_halign(Gtk.Align.CENTER)
            refresh_agents_btn.set_margin_top(8)
            refresh_agents_btn.connect("clicked", lambda _b: refresh_agent_rows())
            agents_refresh_group = Adw.PreferencesGroup()
            agents_refresh_group.add(refresh_agents_btn)

            ai_page.add(diag_group)
            ai_page.add(prov_group)
            ai_page.add(btn_group)
            ai_page.add(agents_group)
            ai_page.add(ollama_group)
            ai_page.add(agents_refresh_group)
            ai_box.append(ai_page)
            stack.add_titled_with_icon(ai_box, "welcome", "Thiết lập AI", "preferences-system-symbolic")

            # --- Page 2: Native-First Software Center (Ubuntu APT, Nido APT, Vendor DEB, Optional Flathub) ---
            apps_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            # Live Operation Progress Bar (revealed during install / remove / update / repair)
            op_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            op_box.set_margin_top(10)
            op_box.set_margin_bottom(8)
            op_box.set_margin_start(20)
            op_box.set_margin_end(20)
            op_box.set_visible(False)

            op_top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            op_status_lbl = Gtk.Label(label="", xalign=0.0, hexpand=True)
            op_status_lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
            op_cancel_btn = Gtk.Button(label="Hủy")
            op_repair_btn = Gtk.Button(label="Khôi phục APT")
            op_repair_btn.add_css_class("destructive-action")
            op_repair_btn.set_visible(False)
            op_top_row.append(op_status_lbl)
            op_top_row.append(op_cancel_btn)
            op_top_row.append(op_repair_btn)

            op_progress = Gtk.ProgressBar()
            op_box.append(op_top_row)
            op_box.append(op_progress)
            apps_outer.append(op_box)

            # Search & Action Toolbar
            apps_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            apps_ctrl.set_margin_top(12)
            apps_ctrl.set_margin_bottom(6)
            apps_ctrl.set_margin_start(20)
            apps_ctrl.set_margin_end(20)

            search_entry = Gtk.SearchEntry(hexpand=True)
            search_entry.set_placeholder_text(
                "Tìm gói .deb Ubuntu / hãng / Flathub (nhấn Enter để tìm trực tuyến)..."
            )
            cat_keys = list(CATEGORY_LABELS.keys())
            cat_dropdown = Gtk.DropDown.new_from_strings([CATEGORY_LABELS[k] for k in cat_keys])
            sys_update_btn = Gtk.Button(label="Cập nhật hệ thống")
            sys_update_btn.add_css_class("suggested-action")
            apps_ctrl.append(search_entry)
            apps_ctrl.append(cat_dropdown)
            apps_ctrl.append(sys_update_btn)
            apps_outer.append(apps_ctrl)

            apps_page = Adw.PreferencesPage()
            apps_outer.append(apps_page)

            local_pkg_group = None
            local_pkg_install_btn = None
            local_pkg_meta: dict[str, Any] | None = None
            if package_file is not None and package_file.is_file():
                if package_file.suffix.lower() == ".deb":
                    try:
                        local_pkg_meta = backend.app_store.inspect_local_deb(package_file)
                    except Exception as exc:
                        local_pkg_meta = {
                            "path": str(package_file),
                            "package": package_file.name,
                            "version": "",
                            "arch": "",
                            "summary": str(exc),
                            "invalid": True,
                        }
                    local_pkg_group = _make_group(
                        title="Cài đặt gói .deb cục bộ",
                        description=f"Tệp: {package_file.name}",
                    )
                    pkg_title = (
                        f"{local_pkg_meta['package']} {local_pkg_meta['version']}".strip()
                        + (f" ({local_pkg_meta['arch']})" if local_pkg_meta.get("arch") else "")
                    )
                    local_row = _make_plain_row(
                        Adw.ActionRow(),
                        pkg_title,
                        str(local_pkg_meta.get("summary") or str(package_file)),
                    )
                    local_pkg_install_btn = Gtk.Button(label="Cài đặt .deb", valign=Gtk.Align.CENTER)
                    local_pkg_install_btn.add_css_class("suggested-action")
                    if local_pkg_meta.get("invalid"):
                        local_pkg_install_btn.set_sensitive(False)
                    local_row.add_suffix(local_pkg_install_btn)
                    local_pkg_group.add(local_row)
                    apps_page.add(local_pkg_group)
                elif package_file.suffix.lower() == ".flatpakref":
                    local_pkg_group = _make_group(
                        title="Cài đặt tệp Flatpak (.flatpakref)",
                        description=f"Tệp: {package_file.name}",
                    )
                    local_row = _make_plain_row(
                        Adw.ActionRow(),
                        package_file.stem,
                        f"Cài đặt ở phạm vi người dùng (--user) từ {package_file}",
                    )
                    local_pkg_install_btn = Gtk.Button(label="Cài đặt Flatpak", valign=Gtk.Align.CENTER)
                    local_pkg_install_btn.add_css_class("suggested-action")
                    local_row.add_suffix(local_pkg_install_btn)
                    local_pkg_group.add(local_row)
                    apps_page.add(local_pkg_group)

            maint_status = backend.app_store.probe_system_maintenance_status()
            sec_txt = (
                "Đang bật tự động (unattended-upgrades)"
                if maint_status["securityUpdatesAuto"]
                else ("Đã cài unattended-upgrades" if maint_status["securityUpdatesInstalled"] else "Chưa bật")
            )
            fw_txt = "Sẵn sàng (fwupd)" if maint_status["fwupdAvailable"] else "Chưa cài fwupd"
            maint_group = _make_group(
                title="Tình trạng hệ thống & Cập nhật độc lập",
                description=(
                    f"Kiến trúc: {maint_status['hostArch']} ({maint_status['codename']}) · "
                    f"Bảo mật Ubuntu: {sec_txt} · Firmware: {fw_txt}."
                ),
            )
            apps_page.add(maint_group)

            apps_group = _make_group(
                title="Kho Ứng dụng Native-First (Ưu tiên gói .deb chính chủ)",
                description=(
                    "Hỗ trợ Ubuntu APT, Nido APT, kho hãng có xác thực GPG + APT Pinning, và Flathub tùy chọn."
                ),
            )
            apps_page.add(apps_group)

            active_op: dict[str, OperationHandle | None] = {"handle": None}
            rendered_rows: list[Any] = []
            current_items: list[dict[str, Any]] = backend.load_catalog_items(include_arch_probe=False)

            def start_store_operation(
                label: str,
                fn: Any,
            ) -> None:
                if active_op["handle"] is not None:
                    _add_toast(toast_overlay, "Đang có tác vụ cài đặt/cập nhật khác chạy.")
                    return
                handle = OperationHandle(label=label)
                active_op["handle"] = handle
                op_box.set_visible(True)
                op_repair_btn.set_visible(False)
                op_cancel_btn.set_visible(True)
                op_cancel_btn.set_sensitive(True)
                op_progress.set_fraction(0.05)
                op_status_lbl.set_text(f"{label}...")

                def on_prog(pct: int, msg: str, safe_cancel: bool) -> None:
                    def _ui() -> bool:
                        op_progress.set_fraction(max(0.0, min(1.0, pct / 100.0)))
                        op_status_lbl.set_text(msg)
                        op_cancel_btn.set_sensitive(safe_cancel)
                        return False

                    GLib.idle_add(_ui)

                def worker() -> None:
                    ok_op, msg_op, needs_repair = fn(handle, on_prog)

                    def _finish() -> bool:
                        active_op["handle"] = None
                        op_cancel_btn.set_visible(False)
                        op_progress.set_fraction(1.0 if ok_op else 0.0)
                        op_status_lbl.set_text(msg_op[:160])
                        op_repair_btn.set_visible(bool(needs_repair))
                        if ok_op:
                            op_box.set_visible(False)
                        _add_toast(toast_overlay, msg_op[:140])
                        reload_catalog_view(search_entry.get_text(), online_search=False)
                        return False

                    GLib.idle_add(_finish)

                threading.Thread(target=worker, daemon=True).start()

            def on_cancel_op(_b: Gtk.Button) -> None:
                h = active_op["handle"]
                if h is None:
                    return
                ok_c, msg_c = h.cancel()
                _add_toast(toast_overlay, msg_c)
                if ok_c:
                    _b.set_sensitive(False)

            op_cancel_btn.connect("clicked", on_cancel_op)
            op_repair_btn.connect(
                "clicked",
                lambda _b: start_store_operation(
                    "Khôi phục gói APT/dpkg",
                    lambda h, cb: backend.app_store.repair_apt(handle=h, on_progress=cb),
                ),
            )
            sys_update_btn.connect(
                "clicked",
                lambda _b: start_store_operation(
                    "Cập nhật toàn bộ hệ thống",
                    lambda h, cb: backend.app_store.upgrade_system(handle=h, on_progress=cb),
                ),
            )
            if local_pkg_install_btn is not None and package_file is not None:
                if package_file.suffix.lower() == ".deb":
                    local_pkg_install_btn.connect(
                        "clicked",
                        lambda _b, pf=package_file: start_store_operation(
                            f"Cài đặt {pf.name}",
                            lambda h, cb, p=pf: backend.app_store.install_local_deb(
                                p, handle=h, on_progress=cb
                            ),
                        ),
                    )
                elif package_file.suffix.lower() == ".flatpakref":
                    local_pkg_install_btn.connect(
                        "clicked",
                        lambda _b, pf=package_file: start_store_operation(
                            f"Cài đặt {pf.name}",
                            lambda h, cb, p=pf: backend.app_store.install_flatpakref(
                                p, handle=h, on_progress=cb
                            ),
                        ),
                    )

            def render_app_rows(items_to_show: list[dict[str, Any]]) -> None:
                for old_r in rendered_rows:
                    apps_group.remove(old_r)
                rendered_rows.clear()

                sel_idx = cat_dropdown.get_selected()
                selected_cat = cat_keys[sel_idx] if 0 <= sel_idx < len(cat_keys) else "all"
                q_low = search_entry.get_text().strip().lower()

                for item in items_to_show:
                    if selected_cat != "all" and item.get("category") != selected_cat:
                        continue
                    if q_low:
                        hay = f"{item['name']} {item['description']} {' '.join(item.get('packages', []))} {item.get('flatpakId', '')}".lower()
                        if q_low not in hay:
                            continue

                    state_badge = "Đã cài đặt" if item["installed"] else "Chưa cài đặt"
                    arch_part = f" · {item['archNote']}" if item.get("archNote") else ""
                    sub = f"[{item['sourceLabel']} · {state_badge}{arch_part}] {item['description']}"
                    row = _make_plain_row(Adw.ActionRow(), item["name"], sub)

                    btn_box_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    btn_box_row.set_valign(Gtk.Align.CENTER)

                    act_btn = Gtk.Button(
                        label="Cập nhật" if item["installed"] else "Cài đặt",
                        valign=Gtk.Align.CENTER,
                    )
                    if item.get("archSupported") is False:
                        act_btn.set_sensitive(False)
                        act_btn.set_label("Không hỗ trợ arch")
                    elif not item["installed"]:
                        act_btn.add_css_class("suggested-action")

                    act_btn.connect(
                        "clicked",
                        lambda _b, target=item: start_store_operation(
                            f"{'Cập nhật' if target['installed'] else 'Cài đặt'} {target['name']}",
                            lambda h, cb, t=target: backend.app_store.install_item(
                                t, handle=h, on_progress=cb
                            ),
                        ),
                    )
                    btn_box_row.append(act_btn)

                    if item["installed"] and not item.get("protected"):
                        rem_btn = Gtk.Button(label="Gỡ", valign=Gtk.Align.CENTER)
                        rem_btn.connect(
                            "clicked",
                            lambda _b, target=item: start_store_operation(
                                f"Gỡ {target['name']}",
                                lambda h, cb, t=target: backend.app_store.remove_item(
                                    t, handle=h, on_progress=cb
                                ),
                            ),
                        )
                        btn_box_row.append(rem_btn)

                    row.add_suffix(btn_box_row)
                    apps_group.add(row)
                    rendered_rows.append(row)

            def reload_catalog_view(query: str = "", online_search: bool = False) -> None:
                def worker() -> None:
                    if online_search and query.strip():
                        fetched = backend.app_store.search_packages_native_first(
                            query.strip(), include_flathub=True
                        )
                    else:
                        fetched = backend.load_catalog_items(include_arch_probe=True)

                    def _apply() -> bool:
                        current_items[:] = fetched
                        render_app_rows(current_items)
                        return False

                    GLib.idle_add(_apply)

                threading.Thread(target=worker, daemon=True).start()

            render_app_rows(current_items)
            # Dynamically probe vendor & Flathub architecture compatibility in background
            reload_catalog_view("", online_search=False)

            search_entry.connect("search-changed", lambda _e: render_app_rows(current_items))
            search_entry.connect(
                "activate",
                lambda e: reload_catalog_view(e.get_text(), online_search=True),
            )
            cat_dropdown.connect("notify::selected", lambda *_a: render_app_rows(current_items))

            stack.add_titled_with_icon(apps_outer, "apps", "Ứng dụng", "system-software-install-symbolic")

            # --- Page 3: Quick NidoAgent & Document Report ---
            agent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            agent_box.set_margin_top(16)
            agent_box.set_margin_bottom(16)
            agent_box.set_margin_start(20)
            agent_box.set_margin_end(20)

            ctrl_group = _make_group(
                title="Trợ lý Điều phối NidoAgent",
                description="Đính kèm hợp đồng / tài liệu (.docx, .xlsx, .pdf, .md) và xuất báo cáo tự động.",
            )
            prompt_row = _make_plain_row(Adw.EntryRow(), "Yêu cầu công việc")
            prompt_row.set_text("Tổng hợp các thông tin và giá trị chính từ tài liệu đính kèm.")
            ctrl_group.add(prompt_row)

            files_row = _make_plain_row(Adw.ActionRow(), "Tệp đính kèm", "Chưa chọn tệp nào")
            pick_btn = Gtk.Button(label="Chọn tệp...", valign=Gtk.Align.CENTER)
            clear_btn = Gtk.Button(label="Xóa", valign=Gtk.Align.CENTER)
            files_row.add_suffix(pick_btn)
            files_row.add_suffix(clear_btn)
            ctrl_group.add(files_row)

            export_modes = [
                ("", "Chỉ hiển thị câu trả lời"),
                (".docx", "Xuất báo cáo Word (.docx)"),
                (".xlsx", "Xuất bảng tổng hợp Excel (.xlsx)"),
            ]
            export_row = _make_plain_row(
                Adw.ComboRow(model=Gtk.StringList.new([m[1] for m in export_modes])),
                "Định dạng kết quả",
            )
            ctrl_group.add(export_row)
            agent_box.append(ctrl_group)

            agent_act_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            agent_act_box.set_halign(Gtk.Align.START)
            run_btn = Gtk.Button(label="Thực thi với NidoAgent")
            run_btn.add_css_class("suggested-action")
            run_btn.add_css_class("pill")
            copy_btn = Gtk.Button(label="Sao chép kết quả")
            copy_btn.add_css_class("pill")
            copy_btn.set_sensitive(False)
            agent_act_box.append(run_btn)
            agent_act_box.append(copy_btn)
            agent_box.append(agent_act_box)

            scroller = Gtk.ScrolledWindow(vexpand=True)
            out_view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            out_view.set_monospace(False)
            out_view.set_left_margin(14)
            out_view.set_right_margin(14)
            out_view.set_top_margin(12)
            out_view.set_bottom_margin(12)
            out_view.set_pixels_below_lines(3)
            out_buf = out_view.get_buffer()
            scroller.set_child(out_view)
            agent_box.append(scroller)

            last_agent_plain: list[str] = [""]

            def on_copy_result(_b: Gtk.Button) -> None:
                txt = last_agent_plain[0].strip()
                if not txt:
                    return
                try:
                    clipboard = win.get_clipboard()
                    clipboard.set(txt)
                    _add_toast(toast_overlay, "Đã sao chép văn bản sạch vào bộ nhớ tạm.")
                except Exception:
                    pass

            def on_pick_files(_b: Gtk.Button) -> None:
                dialog = Gtk.FileDialog(title="Chọn tài liệu cho NidoAgent")

                def on_done(dlg: Any, res: Any) -> None:
                    try:
                        gfiles = dlg.open_multiple_finish(res)
                        self.selected_files = [
                            gf.get_path() for gf in gfiles if gf.get_path()
                        ]
                        files_row.set_subtitle(
                            f"Đã chọn {len(self.selected_files)} tệp: "
                            + ", ".join(Path(p).name for p in self.selected_files[:3])
                        )
                    except Exception:
                        pass

                dialog.open_multiple(win, None, on_done)

            def on_clear_files(_b: Gtk.Button) -> None:
                self.selected_files = []
                files_row.set_subtitle("Chưa chọn tệp nào")

            def on_run_agent(_b: Gtk.Button) -> None:
                _b.set_sensitive(False)
                copy_btn.set_sensitive(False)
                out_buf.set_text("Đang xử lý qua NidoAgent...")
                ext = export_modes[export_row.get_selected()][0]
                out_file = ""
                if ext:
                    docs_dir = backend.home_dir / "Documents"
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    out_file = str(docs_dir / f"Bao-cao-NidoAgent-{stamp}{ext}")

                prompt_txt = prompt_row.get_text()
                chosen = list(self.selected_files)

                def worker() -> None:
                    _, result_txt = backend.run_agent_task(
                        prompt=prompt_txt,
                        files=chosen,
                        out_path=out_file or None,
                    )

                    def finish_agent() -> bool:
                        _b.set_sensitive(True)
                        render_markdown_to_gtk_buffer(out_buf, result_txt)
                        last_agent_plain[0] = render_markdown_to_plain(result_txt)
                        copy_btn.set_sensitive(bool(last_agent_plain[0].strip()))
                        return False

                    GLib.idle_add(finish_agent)

                threading.Thread(target=worker, daemon=True).start()

            pick_btn.connect("clicked", on_pick_files)
            clear_btn.connect("clicked", on_clear_files)
            run_btn.connect("clicked", on_run_agent)
            copy_btn.connect("clicked", on_copy_result)

            stack.add_titled_with_icon(agent_box, "agent", "NidoAgent", "system-run-symbolic")

            if initial_page in {"welcome", "apps", "agent"}:
                stack.set_visible_child_name(initial_page)

            # --- Dedicated Mandatory AI Onboarding Gate (AI ON/OFF + >=1 Required Account + Optional API Key) ---
            ob_toolbar = Adw.ToolbarView()
            ob_header = Adw.HeaderBar()
            ob_title = Adw.WindowTitle(
                title="Thiết lập AI ban đầu — Ubuntu-Nido",
                subtitle="Chọn bật/tắt AI và kết nối ít nhất 1 tài khoản AI để sẵn sàng sử dụng",
            )
            ob_header.set_title_widget(ob_title)
            ob_toolbar.add_top_bar(ob_header)

            ob_toast_overlay = Adw.ToastOverlay()
            ob_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            ob_banner = Adw.Banner()
            if hasattr(ob_banner, "set_use_markup"):
                ob_banner.set_use_markup(False)
            ob_banner.set_revealed(True)
            ob_outer.append(ob_banner)

            ob_page = Adw.PreferencesPage()
            ob_page.set_vexpand(True)

            # Onboarding Group 0: Mandatory AI ON / OFF switch
            ob_toggle_group = _make_group(
                title="Chọn chế độ AI trên Ubuntu-Nido (Bắt buộc)",
                description=(
                    "Nếu BẬT AI (ON), bạn bắt buộc đăng nhập ít nhất 1 tài khoản (Google/Gemini, ChatGPT hoặc Claude) "
                    "để NidoAgent & NidoOffice AI hoạt động ngay. Nếu chưa muốn dùng AI, hãy gạt sang TẮT AI (OFF)."
                ),
            )
            ob_ai_switch = _make_plain_row(
                Adw.SwitchRow(),
                "Sử dụng Trợ lý AI Ubuntu-Nido (AI ON / OFF)",
                "BẬT (ON): yêu cầu đăng nhập ≥ 1 tài khoản AI bên dưới · TẮT (OFF): vào thẳng desktop không chạy AI.",
            )
            ob_ai_switch.set_active(True if backend.is_onboarding_required() else backend.is_ai_enabled())
            ob_toggle_group.add(ob_ai_switch)

            # Onboarding Group 1: Mandatory Sign-In (visible when AI ON)
            ob_accounts_group = _make_group(
                title="1. Đăng nhập tài khoản AI (Bắt buộc — chọn ít nhất 1 tài khoản)",
                description="Bấm 1 chạm để tự tải CLI chính chủ (không cần root) và đăng nhập OAuth qua trình duyệt.",
            )
            ob_agent_rows: dict[str, tuple[Any, Gtk.Button, Any]] = {}
            ob_latest_states: list[dict[str, Any]] = list(initial_states_list)

            ob_check_row = _make_plain_row(
                Adw.ActionRow(),
                "Trạng thái xác thực tài khoản AI",
                "Sau khi hoàn tất đăng nhập trên trình duyệt / cửa sổ dòng lệnh, bấm kiểm tra hoặc đợi tự cập nhật.",
            )
            ob_check_btn = Gtk.Button(label="Kiểm tra đăng nhập", valign=Gtk.Align.CENTER)
            ob_check_row.add_suffix(ob_check_btn)

            # Onboarding Group 2: Optional API Key (visible when AI ON)
            ob_apikey_group = _make_group(
                title="2. Nhập API Key bổ sung (Tùy chọn, nếu có)",
                description=(
                    "Không bắt buộc nếu đã đăng nhập tài khoản ở Bước 1. Thêm API Key giúp kích hoạt thêm "
                    "NidoBox Vision OCR và NidoAI Gateway cục bộ (lưu an toàn trong OS Keyring)."
                ),
            )
            ob_prov_model = Gtk.StringList.new([label for _, label in PROVIDERS])
            ob_prov_row = _make_plain_row(Adw.ComboRow(model=ob_prov_model), "Nguồn API Gateway ưu tiên")
            ob_prov_row.set_selected(prov_idx)
            ob_apikey_group.add(ob_prov_row)

            ob_free_key_row = _make_plain_row(
                Adw.ActionRow(),
                "Lấy Google Gemini API Key miễn phí ($0 — tùy chọn)",
                "Mở Google AI Studio trên trình duyệt nếu bạn muốn thêm khóa dự phòng cho NidoBox OCR.",
            )
            ob_get_key_btn = Gtk.Button(label="Mở AI Studio", valign=Gtk.Align.CENTER)
            ob_get_key_btn.connect(
                "clicked",
                lambda _b: _add_toast(
                    ob_toast_overlay,
                    "Đã mở Google AI Studio trong trình duyệt."
                    if backend.open_external_url(GEMINI_FREE_KEY_URL)
                    else f"Mở trình duyệt truy cập: {GEMINI_FREE_KEY_URL}",
                ),
            )
            ob_free_key_row.add_suffix(ob_get_key_btn)
            ob_apikey_group.add(ob_free_key_row)

            ob_gemini_row = _make_plain_row(Adw.PasswordEntryRow(), "Google Gemini API Key (tùy chọn)")
            ob_gemini_row.set_text(env.get("GEMINI_API_KEY", ""))
            ob_apikey_group.add(ob_gemini_row)

            ob_openai_row = _make_plain_row(Adw.PasswordEntryRow(), "OpenAI API Key (tùy chọn)")
            ob_openai_row.set_text(env.get("OPENAI_API_KEY", ""))
            ob_apikey_group.add(ob_openai_row)

            ob_anthropic_row = _make_plain_row(Adw.PasswordEntryRow(), "Anthropic API Key (tùy chọn)")
            ob_anthropic_row.set_text(env.get("ANTHROPIC_API_KEY", ""))
            ob_apikey_group.add(ob_anthropic_row)

            # Onboarding Group 3: AI OFF Confirmation (visible when AI OFF)
            ob_off_group = _make_group(
                title="Chế độ TẮT AI (AI OFF)",
                description="Ubuntu-Nido sẽ hoạt động hoàn toàn như máy tính để bàn truyền thống, không khởi chạy dịch vụ AI.",
            )
            ob_off_info_row = _make_plain_row(
                Adw.ActionRow(),
                "Tạm tắt NidoAI Gateway, NidoAgent và NidoOffice AI",
                "Bạn có thể mở NidoHub bất cứ lúc nào để gạt AI sang BẬT (ON) và đăng nhập tài khoản AI sau.",
            )
            ob_off_group.add(ob_off_info_row)

            # Pinned Bottom Action Bar for Onboarding
            ob_footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            ob_footer.set_margin_top(10)
            ob_footer.set_margin_bottom(14)
            ob_footer.set_margin_start(24)
            ob_footer.set_margin_end(24)

            ob_gate_lbl = Gtk.Label(xalign=0.0, wrap=True)
            ob_gate_lbl.add_css_class("dim-label")
            ob_footer.append(ob_gate_lbl)

            ob_action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            ob_action_row.set_halign(Gtk.Align.END)
            ob_finish_btn = Gtk.Button(label="Hoàn tất & Bắt đầu dùng Ubuntu-Nido")
            ob_finish_btn.add_css_class("suggested-action")
            ob_finish_btn.add_css_class("pill")
            ob_action_row.append(ob_finish_btn)
            ob_footer.append(ob_action_row)

            def _stop_ob_poll() -> None:
                if ob_poll_source_id[0]:
                    try:
                        GLib.source_remove(ob_poll_source_id[0])
                    except Exception:
                        pass
                    ob_poll_source_id[0] = 0

            def _update_onboarding_ui() -> None:
                ai_on = bool(ob_ai_switch.get_active())
                gate = evaluate_ai_onboarding_gate(ai_on, ob_latest_states)
                states_map = {e["id"]: e for e in ob_latest_states}

                ob_accounts_group.set_visible(ai_on)
                ob_apikey_group.set_visible(ai_on)
                ob_off_group.set_visible(not ai_on)

                banner_txt = f"[{gate['statusBadge']}] {gate['reasonVi']}"
                ob_banner.set_title(
                    banner_txt if hasattr(ob_banner, "set_use_markup") else escape_markup(banner_txt)
                )
                ob_gate_lbl.set_text(gate["reasonVi"])
                ob_check_row.set_subtitle(
                    f"Trạng thái: {gate['statusBadge']} — "
                    + (
                        ", ".join(gate["signedInLabels"])
                        if gate["signedInLabels"]
                        else "chưa phát hiện tài khoản nào đã đăng nhập."
                    )
                )

                for spec in MANAGED_AGENT_SPECS:
                    if spec.id not in ob_agent_rows:
                        continue
                    st = states_map.get(spec.id, {"installed": False, "signedIn": False})
                    row_w, btn_w, _ = ob_agent_rows[spec.id]
                    row_w.set_subtitle(_format_agent_status_subtitle(spec, st))
                    if st.get("signedIn"):
                        btn_w.set_label("Đã đăng nhập ✓")
                        btn_w.remove_css_class("suggested-action")
                        btn_w.set_sensitive(True)
                    elif st.get("installed"):
                        btn_w.set_label("Đăng nhập ngay")
                        btn_w.add_css_class("suggested-action")
                        btn_w.set_sensitive(True)
                    else:
                        btn_w.set_label("Cài & Đăng nhập")
                        btn_w.add_css_class("suggested-action")
                        btn_w.set_sensitive(True)

                if not ai_on:
                    ob_finish_btn.set_label("Hoàn tất (Tắt AI & vào màn hình chính)")
                    ob_finish_btn.set_sensitive(True)
                elif gate["canComplete"]:
                    ob_finish_btn.set_label("Hoàn tất & Bắt đầu dùng Ubuntu-Nido")
                    ob_finish_btn.set_sensitive(True)
                    _stop_ob_poll()
                else:
                    ob_finish_btn.set_label("Hoàn tất (Cần đăng nhập ≥ 1 tài khoản AI)")
                    ob_finish_btn.set_sensitive(False)

            def _refresh_onboarding_accounts(show_toast: bool = False) -> None:
                def worker() -> None:
                    fresh_states = backend.probe_agent_accounts(persist=True)

                    def apply() -> bool:
                        ob_latest_states[:] = fresh_states
                        _update_onboarding_ui()
                        if show_toast:
                            g = evaluate_ai_onboarding_gate(bool(ob_ai_switch.get_active()), ob_latest_states)
                            _add_toast(ob_toast_overlay, f"{g['statusBadge']}: {g['reasonVi']}"[:150])
                        return False

                    GLib.idle_add(apply)

                threading.Thread(target=worker, daemon=True).start()

            def _start_ob_poll() -> None:
                _stop_ob_poll()
                ob_poll_ticks[0] = 0

                def _tick() -> bool:
                    if not onboarding_active[0] or not ob_ai_switch.get_active():
                        ob_poll_source_id[0] = 0
                        return False
                    ob_poll_ticks[0] += 1
                    if ob_poll_ticks[0] > 45:  # Stop after ~3 minutes
                        ob_poll_source_id[0] = 0
                        return False
                    _refresh_onboarding_accounts(show_toast=False)
                    return True

                ob_poll_source_id[0] = int(GLib.timeout_add_seconds(4, _tick))

            for spec in MANAGED_AGENT_SPECS:
                st = initial_states.get(spec.id, {"installed": False, "signedIn": False})
                ob_row = _make_plain_row(
                    Adw.ActionRow(),
                    spec.display_name,
                    _format_agent_status_subtitle(spec, st),
                )
                ob_btn = Gtk.Button(label="Cài & Đăng nhập", valign=Gtk.Align.CENTER)

                def on_ob_agent_clicked(_b: Gtk.Button, pid: str = spec.id) -> None:
                    cur_st = next((x for x in ob_latest_states if x.get("id") == pid), {})
                    if cur_st.get("signedIn"):
                        _add_toast(
                            ob_toast_overlay,
                            f"{REQUIRED_AGENT_LABELS.get(pid, pid)} đã đăng nhập sẵn sàng!",
                        )
                        return
                    _b.set_sensitive(False)
                    _b.set_label("Đang mở đăng nhập...")

                    def worker() -> None:
                        ok_c, msg_c = backend.connect_agent(pid)
                        fresh = backend.probe_agent_accounts(persist=True)

                        def done() -> bool:
                            ob_latest_states[:] = fresh
                            _add_toast(ob_toast_overlay, msg_c[:150])
                            _update_onboarding_ui()
                            if ok_c and not evaluate_ai_onboarding_gate(True, ob_latest_states)["canComplete"]:
                                _start_ob_poll()
                            return False

                        GLib.idle_add(done)

                    threading.Thread(target=worker, daemon=True).start()

                ob_btn.connect("clicked", on_ob_agent_clicked)
                ob_row.add_suffix(ob_btn)
                ob_accounts_group.add(ob_row)
                ob_agent_rows[spec.id] = (ob_row, ob_btn, spec)

            ob_accounts_group.add(ob_check_row)
            ob_check_btn.connect("clicked", lambda _b: _refresh_onboarding_accounts(show_toast=True))
            ob_ai_switch.connect("notify::active", lambda *_a: _update_onboarding_ui())

            def _sync_main_from_onboarding(ai_on: bool) -> None:
                ai_master_guard[0] = True
                try:
                    ai_master_switch.set_active(ai_on)
                finally:
                    ai_master_guard[0] = False
                if ob_gemini_row.get_text().strip():
                    gemini_row.set_text(ob_gemini_row.get_text().strip())
                if ob_openai_row.get_text().strip():
                    openai_row.set_text(ob_openai_row.get_text().strip())
                if ob_anthropic_row.get_text().strip():
                    anthropic_row.set_text(ob_anthropic_row.get_text().strip())
                prov_row.set_selected(ob_prov_row.get_selected())

            def on_ob_finish_clicked(_b: Gtk.Button) -> None:
                ai_on = bool(ob_ai_switch.get_active())
                sel = ob_prov_row.get_selected()
                kind = PROVIDERS[sel][0] if 0 <= sel < len(PROVIDERS) else "gemini"
                g_key = ob_gemini_row.get_text()
                o_key = ob_openai_row.get_text()
                a_key = ob_anthropic_row.get_text()
                _b.set_sensitive(False)
                _b.set_label("Đang hoàn tất thiết lập...")

                def worker() -> None:
                    fresh = backend.probe_agent_accounts(persist=True) if ai_on else []
                    ok_f, msg_f = backend.complete_ai_onboarding(
                        ai_enabled=ai_on,
                        primary_backend=kind,
                        gemini_key=g_key,
                        openai_key=o_key,
                        anthropic_key=a_key,
                        restart_service=True,
                        verify_live=True,
                        agent_states=fresh,
                    )

                    def done() -> bool:
                        if fresh:
                            ob_latest_states[:] = fresh
                        if not ok_f:
                            _add_toast(ob_toast_overlay, msg_f[:160])
                            _update_onboarding_ui()
                            return False
                        _stop_ob_poll()
                        onboarding_active[0] = False
                        _sync_main_from_onboarding(ai_on)
                        if not ai_on and first_login_launch:
                            allow_close[0] = True
                            win.close()
                            return False
                        root_stack.set_visible_child_name("main")
                        stack.set_visible_child_name("welcome")
                        _add_toast(toast_overlay, msg_f[:160])
                        refresh_readiness_ui(persist_agents=False)
                        return False

                    GLib.idle_add(done)

                threading.Thread(target=worker, daemon=True).start()

            ob_finish_btn.connect("clicked", on_ob_finish_clicked)

            ob_page.add(ob_toggle_group)
            ob_page.add(ob_accounts_group)
            ob_page.add(ob_apikey_group)
            ob_page.add(ob_off_group)
            ob_outer.append(ob_page)
            ob_toast_overlay.set_child(ob_outer)
            ob_toolbar.set_content(ob_toast_overlay)
            ob_toolbar.add_bottom_bar(ob_footer)
            _update_onboarding_ui()

            def _enter_onboarding_view() -> None:
                onboarding_active[0] = True
                ob_ai_switch.set_active(True)
                root_stack.set_visible_child_name("onboarding")
                _update_onboarding_ui()
                _refresh_onboarding_accounts(show_toast=False)

            reopen_ob_btn.connect("clicked", lambda _b: _enter_onboarding_view())

            def on_master_ai_toggled(sw: Any, _pspec: Any) -> None:
                if ai_master_guard[0]:
                    return
                want_on = bool(sw.get_active())

                def worker() -> None:
                    if not want_on:
                        msg_t = backend.set_ai_enabled(False, restart_service=True)

                        def done_off() -> bool:
                            _add_toast(toast_overlay, msg_t[:150])
                            banner.set_title(
                                "Chế độ AI đang TẮT trên Ubuntu-Nido — gạt công tắc Bật AI bên dưới để kích hoạt."
                            )
                            return False

                        GLib.idle_add(done_off)
                        return

                    fresh_agents = backend.probe_agent_accounts(persist=True)
                    gate_now = evaluate_ai_onboarding_gate(True, fresh_agents)
                    if not gate_now["canComplete"]:
                        def open_gate() -> bool:
                            ob_latest_states[:] = fresh_agents
                            _enter_onboarding_view()
                            _add_toast(
                                ob_toast_overlay,
                                "Bật AI yêu cầu đăng nhập ít nhất 1 tài khoản (Google/Gemini, ChatGPT hoặc Claude).",
                            )
                            return False

                        GLib.idle_add(open_gate)
                        return

                    msg_t = backend.set_ai_enabled(
                        True,
                        restart_service=True,
                        signed_in_providers=gate_now["signedInProviders"],
                    )

                    def done_on() -> bool:
                        _add_toast(toast_overlay, msg_t[:150])
                        refresh_readiness_ui(persist_agents=False)
                        return False

                    GLib.idle_add(done_on)

                threading.Thread(target=worker, daemon=True).start()

            ai_master_switch.connect("notify::active", on_master_ai_toggled)

            def on_window_close_request(_w: Any) -> bool:
                if allow_close[0] or not onboarding_active[0]:
                    _stop_ob_poll()
                    return False

                def _confirm_disable_and_exit() -> None:
                    _stop_ob_poll()
                    backend.set_ai_enabled(
                        False,
                        restart_service=True,
                        mark_completed=True,
                        signed_in_providers=[],
                    )
                    onboarding_active[0] = False
                    allow_close[0] = True
                    win.close()

                if hasattr(Adw, "AlertDialog"):
                    dlg = Adw.AlertDialog(
                        heading="Chưa hoàn tất thiết lập AI",
                        body=(
                            "Chế độ AI yêu cầu đăng nhập ít nhất 1 tài khoản (Google/Gemini, ChatGPT hoặc Claude) "
                            "hoặc chọn Tắt AI trước khi vào màn hình chính."
                        ),
                    )
                    dlg.add_response("stay", "Quay lại thiết lập")
                    dlg.add_response("disable", "Tắt AI & Thoát")
                    dlg.set_default_response("stay")
                    dlg.set_close_response("stay")
                    if hasattr(Adw, "ResponseAppearance"):
                        dlg.set_response_appearance("disable", Adw.ResponseAppearance.DESTRUCTIVE)

                    def _on_dlg_resp(_d: Any, resp: str) -> None:
                        if resp == "disable":
                            _confirm_disable_and_exit()

                    dlg.connect("response", _on_dlg_resp)
                    dlg.present(win)
                else:
                    _add_toast(
                        ob_toast_overlay,
                        "Vui lòng đăng nhập ≥ 1 tài khoản AI hoặc gạt công tắc sang TẮT AI rồi bấm Hoàn tất.",
                    )
                return True

            win.connect("close-request", on_window_close_request)

            root_stack.add_named(ob_toolbar, "onboarding")
            root_stack.add_named(toolbar, "main")
            root_stack.set_visible_child_name("onboarding" if onboarding_mode else "main")

            win.set_content(root_stack)
            win.present()

    app = NidoHubApp()
    return int(app.run([]))


def main() -> int:
    parser = argparse.ArgumentParser(description="NidoHub Desktop")
    parser.add_argument(
        "--page",
        choices=("welcome", "apps", "agent"),
        default="welcome",
        help="Initial NidoHub tab to open",
    )
    parser.add_argument(
        "--first-login",
        action="store_true",
        help="Launch mandatory AI ON/OFF onboarding if not yet completed, else exit immediately",
    )
    parser.add_argument(
        "--onboarding",
        action="store_true",
        help="Open the mandatory AI ON/OFF & Account Sign-in onboarding screen",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run headless backend self-tests and exit",
    )
    parser.add_argument(
        "package_file",
        nargs="?",
        default=None,
        help="Optional local .deb or .flatpakref file to inspect and install",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    backend = NidoHubBackend()
    if args.first_login and not backend.is_onboarding_required():
        return 0

    if not can_launch_gtk():
        return 0

    opened_pkg = _resolve_opened_package_path(args.package_file)
    start_page = "apps" if opened_pkg is not None else args.page
    open_onboarding = bool(
        opened_pkg is None and (args.onboarding or (args.first_login and backend.is_onboarding_required()))
    )
    return run_gtk_app(
        backend,
        start_page,
        package_file=opened_pkg,
        onboarding_mode=open_onboarding,
        first_login_launch=bool(args.first_login),
    )


if __name__ == "__main__":
    sys.exit(main())
