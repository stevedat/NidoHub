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
    cleanup_active_login_processes,
    connect_agent_provider,
    default_command_runner,
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
    detect_hardware_fast,
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
    SOURCE_LABELS,
    NidoAppStore,
    OperationHandle,
    normalize_arch_name,
)
from markdown_render import (  # noqa: E402
    parse_inline_spans,
    parse_markdown_blocks,
    render_markdown_to_gtk_buffer,
    render_markdown_to_plain,
)
from nido_account import NidoAccountManager  # noqa: E402
from printer_probe import (  # noqa: E402
    UNIVERSAL_ADAPTER_MODELS,
    build_network_printer_spec,
    probe_printers,
)

PROVIDERS = [
    ("nido", "Tài khoản Nido (Nido AI)"),
    ("gemini", "Google Gemini (Miễn phí / Khóa API)"),
    ("openai", "OpenAI (Khóa API)"),
    ("anthropic", "Anthropic Claude (Khóa API)"),
    ("local", "AI ngoại tuyến trên máy (Ollama)"),
]

NIDO_HUB_CSS = """
.nido-status-tile {
    background-color: @card_bg_color;
    border: 1px solid alpha(@window_fg_color, 0.08);
    border-radius: 12px;
    padding: 12px 14px;
}
.nido-status-title {
    font-weight: 700;
    font-size: 13px;
}
.nido-status-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
}
.nido-badge-ok {
    background-color: alpha(#10B981, 0.16);
    color: #34D399;
}
.nido-badge-warn {
    background-color: alpha(#F59E0B, 0.16);
    color: #FBBF24;
}
.nido-badge-muted {
    background-color: alpha(@window_fg_color, 0.08);
    color: alpha(@window_fg_color, 0.72);
}
.nido-result-frame {
    background-color: @card_bg_color;
    border: 1px solid alpha(@window_fg_color, 0.08);
    border-radius: 12px;
}
.nido-footer-bar {
    border-top: 1px solid alpha(@window_fg_color, 0.08);
    padding-top: 10px;
}
"""

GEMINI_FREE_KEY_URL = "https://aistudio.google.com/apikey"

REQUIRED_AGENT_PROVIDER_IDS = ("nido", "agy", "codex", "claude")
REQUIRED_AGENT_LABELS = {
    "nido": "Tài khoản Nido",
    "agy": "Google / Gemini (agy)",
    "codex": "ChatGPT / OpenAI (codex)",
    "claude": "Claude / Anthropic (claude)",
}


def evaluate_ai_onboarding_gate(
    ai_enabled: bool,
    agent_states: list[dict[str, Any]],
    nido_account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate whether mandatory first-run AI onboarding may complete.

    Policy:
    - AI OFF -> allowed to complete immediately without signing in.
    - AI ON  -> requires at least 1 signed-in AI account (Tài khoản Nido,
      Google/Gemini, ChatGPT, or Claude); API keys may be added optionally.
    """
    signed_ids: list[str] = []
    if isinstance(nido_account, dict) and bool(nido_account.get("signedIn")):
        signed_ids.append("nido")
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
                f"Đã đăng nhập {joined}. Bạn có thể thêm khóa API bên dưới (nếu có) "
                "rồi bấm 'Hoàn tất & Bắt đầu dùng NidoOS'."
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
            "(Tài khoản Nido, Google/Gemini, ChatGPT hoặc Claude) trước khi hoàn tất, "
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
        self.nido_account = NidoAccountManager(
            home_dir=self.home_dir,
            nidoai_dir=self.nidoai_dir,
            config_dir=self.config_dir,
            secret_store=self.secret_store,
        )

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

    def _apply_gateway_service_state(self, active: bool, restart_service: bool = True) -> None:
        if not active:
            runtime_file = self.nidoai_dir / "runtime.json"
            if runtime_file.exists():
                try:
                    runtime_file.unlink()
                except OSError:
                    pass
        if not restart_service or not shutil.which("systemctl"):
            return
        if active:
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
                ["systemctl", "--user", "disable", "--now", "nidoai-gateway.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["systemctl", "--user", "stop", "nidoai-gateway.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

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

        self._apply_gateway_service_state(enabled, restart_service=restart_service)

        if enabled:
            return "Đã BẬT chế độ AI trên NidoOS."
        return "Đã TẮT chế độ AI trên NidoOS (có thể bật lại bất cứ lúc nào trong NidoHub)."

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
        nido_status = self.get_nido_account_status(refresh_quota=False)
        gate = evaluate_ai_onboarding_gate(True, states, nido_account=nido_status)
        if not gate["canComplete"]:
            return False, gate["reasonVi"]

        has_any_api_key = any(
            k.strip() for k in (gemini_key, openai_key, anthropic_key, local_base_url)
        )
        chosen_backend = primary_backend
        if "nido" in gate["signedInProviders"] and not has_any_api_key and chosen_backend not in {"local"}:
            chosen_backend = "nido"
        should_verify = bool(verify_live and has_any_api_key)
        save_msg = self.save_ai_settings(
            primary_backend=chosen_backend,
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
        return True, f"Hoàn tất thiết lập AI với {joined}!"

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
        if primary_backend not in {"nido", "gemini", "openai", "anthropic", "local"}:
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
            if primary_backend == "nido":
                _, nido_msg, _ = self.nido_account.refresh_session(force=False)
                live_note = f" · {nido_msg}"
            else:
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

        self._apply_gateway_service_state(True, restart_service=restart_service)

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
            return False, "Dịch vụ Trợ lý AI chưa khởi động (sẽ tự bật sau khi lưu cấu hình)"
        pb = payload.get("primaryBackend", {})
        kind = pb.get("kind", "gemini")
        configured = bool(pb.get("configured", False))
        configured_raw = bool(pb.get("configuredRaw", configured))
        reachable = bool(pb.get("reachable", configured))
        caps = payload.get("capabilities", {})
        vision_txt = " · hỗ trợ ảnh" if caps.get("vision") else ""
        if kind == "nido":
            nido_acct = self.get_nido_account_status(refresh_quota=False)
            if nido_acct.get("signedIn") and configured:
                email_lbl = nido_acct.get("email") or "Tài khoản Nido"
                plan_lbl = nido_acct.get("planName") or "Nido"
                return True, f"Đang dùng {email_lbl} ({plan_lbl})"
            return True, "Chưa đăng nhập Tài khoản Nido hoặc nguồn AI"
        if kind == "local" and configured_raw and not reachable:
            state_txt = "chưa bật Ollama cục bộ"
        elif kind == "local" and configured and reachable:
            local_info = probe_local_ai_runtimes(self.home_dir)
            if not local_info.get("hasReadyLocalModel"):
                state_txt = "Ollama đã chạy nhưng chưa tải model (0 model)"
            else:
                v_note = " · hỗ trợ ảnh" if local_info.get("hasVisionLocalModel") else " · chỉ văn bản"
                state_txt = f"sẵn sàng ({len(local_info['installedModels'])} model{v_note})"
        elif configured and reachable:
            state_txt = f"sẵn sàng{vision_txt}"
        else:
            state_txt = "chưa cấu hình model hoặc khóa AI"
        return True, f"Trợ lý AI đang hoạt động ({kind} — {state_txt})"

    def get_nido_account_status(self, refresh_quota: bool = False) -> dict[str, Any]:
        return self.nido_account.get_status(refresh_quota=refresh_quota)

    def login_nido_account(
        self,
        email: str,
        password: str,
        set_as_primary: bool = True,
        restart_service: bool = True,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        ok, msg, profile = self.nido_account.login(email, password, set_as_primary=set_as_primary)
        if ok:
            env = self.load_env_file_only()
            if len(env.get("NIDOAI_CLIENT_TOKEN", "")) < 16:
                env["NIDOAI_CLIENT_TOKEN"] = f"nidoai_{secrets.token_hex(24)}"
            env.setdefault("NIDOAI_ENABLED", "1")
            if set_as_primary:
                env["NIDOAI_PRIMARY_BACKEND"] = "nido"
            self._write_env_file(env)
            cur = self.load_ai_policy()
            providers = list(cur.get("signedInProviders") or [])
            if "nido" not in providers:
                providers.insert(0, "nido")
            self._write_ai_policy(bool(cur.get("setupCompleted", False)), True, providers)
            self._apply_gateway_service_state(True, restart_service=restart_service)
        return ok, msg, profile

    def register_nido_account(
        self,
        email: str,
        password: str,
        full_name: str = "",
        set_as_primary: bool = True,
        restart_service: bool = True,
    ) -> tuple[bool, str, dict[str, Any]]:
        ok, msg, detail = self.nido_account.register(
            email, password, full_name=full_name, set_as_primary=set_as_primary
        )
        if ok and detail.get("profile"):
            cur = self.load_ai_policy()
            providers = list(cur.get("signedInProviders") or [])
            if "nido" not in providers:
                providers.insert(0, "nido")
            self._write_ai_policy(bool(cur.get("setupCompleted", False)), True, providers)
            self._apply_gateway_service_state(True, restart_service=restart_service)
        return ok, msg, detail

    def resend_nido_confirmation(self, email: str) -> tuple[bool, str]:
        return self.nido_account.resend_confirmation(email)

    def logout_nido_account(self, restart_service: bool = True) -> tuple[bool, str]:
        ok, msg = self.nido_account.logout()
        cur = self.load_ai_policy()
        providers = [p for p in (cur.get("signedInProviders") or []) if p != "nido"]
        self._write_ai_policy(
            bool(cur.get("setupCompleted", False)),
            bool(cur.get("aiEnabled", True)),
            providers,
        )
        self._apply_gateway_service_state(self.is_ai_enabled(), restart_service=restart_service)
        return ok, msg

    def set_os_sync_enabled(self, enabled: bool) -> dict[str, Any] | None:
        return self.nido_account.set_sync_enabled(enabled)

    def export_os_sync_bundle(self, dest_path: Path | None = None) -> tuple[bool, str, Path]:
        return self.nido_account.export_sync_bundle(dest_path=dest_path)

    def import_os_sync_bundle(
        self, src_path: Path | None = None, restart_service: bool = True
    ) -> tuple[bool, str, list[str]]:
        ok, msg, restored = self.nido_account.import_sync_bundle(src_path=src_path)
        if ok and "ai-policy" in restored:
            self._apply_gateway_service_state(self.is_ai_enabled(), restart_service=restart_service)
        return ok, msg, restored

    def get_cached_ai_readiness_report(self) -> dict[str, Any]:
        """Instantaneous cached snapshot using zero subprocesses or HTTP probes for UI startup."""
        hw = detect_hardware_fast(self.home_dir)
        empty_local: dict[str, Any] = {
            "ollama": {
                "installed": False,
                "running": False,
                "state": "checking",
                "models": [],
                "visionModels": [],
            },
            "runtimes": [],
            "activeRuntime": None,
            "installedModels": [],
            "visionModels": [],
            "hasReadyLocalModel": False,
            "hasVisionLocalModel": False,
        }
        cached_map = read_agents_status(self.nidoai_dir)
        cached_states: list[dict[str, Any]] = []
        for spec in MANAGED_AGENT_SPECS:
            entry = dict(
                cached_map.get(spec.id)
                or {
                    "id": spec.id,
                    "installed": False,
                    "signedIn": False,
                    "authMethod": spec.auth_method,
                    "source": "nidohub",
                }
            )
            cached_states.append(entry)
        nido_acct = self.get_nido_account_status(refresh_quota=False)
        rep = build_ai_readiness_report(
            hw, empty_local, None, cached_states, nido_account=nido_acct
        )
        rep["ollamaStatusVi"] = "Đang kiểm tra Ollama cục bộ..."
        rep["agents"] = cached_states
        return rep

    def get_ai_readiness_report(
        self, persist_agents: bool = False, refresh_nido: bool = False
    ) -> dict[str, Any]:
        hw = detect_hardware(self.home_dir)
        local_info = probe_local_ai_runtimes(self.home_dir)
        gw_health = self.probe_gateway_health_detail()
        agents = self.probe_agent_accounts(persist=persist_agents)
        nido_acct = self.get_nido_account_status(refresh_quota=refresh_nido)
        rep = build_ai_readiness_report(hw, local_info, gw_health, agents, nido_account=nido_acct)
        rep["agents"] = agents
        return rep

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

    def load_catalog_items(
        self,
        include_arch_probe: bool = False,
        hide_unsupported: bool = True,
    ) -> list[dict[str, Any]]:
        return self.app_store.load_catalog_entries(
            include_arch_probe=include_arch_probe,
            hide_unsupported=hide_unsupported,
        )

    def install_packages(self, packages: list[str]) -> tuple[bool, str]:
        ok, msg, _ = self.app_store.install_item(
            {"name": ", ".join(packages), "source": "ubuntu-apt", "packages": packages}
        )
        return ok, msg

    def configure_printer(
        self,
        queue_name: str,
        uri: str,
        model: str,
        handle: OperationHandle | None = None,
        on_progress: Any = None,
    ) -> tuple[bool, str, bool]:
        op = handle or OperationHandle(label=f"Cấu hình máy in {queue_name}")
        return self.app_store._run_streaming_helper(
            ["configure-printer", "--name", queue_name, "--uri", uri, "--model", model],
            op,
            on_progress=on_progress,
        )

    def is_agent_execution_ready(self) -> tuple[bool, str]:
        if not self.is_ai_enabled():
            return (
                False,
                "Trợ lý AI đang TẮT trên NidoOS. Mở tab Trợ lý AI để bật lại và đăng nhập ít nhất 1 tài khoản AI trước khi chạy tác vụ.",
            )
        if os.environ.get("NIDOAGENT_ALLOW_UNCONFIGURED_TEST") == "1":
            return True, ""
        nido_acct = self.get_nido_account_status(refresh_quota=False)
        if nido_acct.get("signedIn"):
            return True, ""
        agents_list = self.probe_agent_accounts(persist=False)
        if any(bool(a.get("signedIn")) for a in agents_list):
            return True, ""
        gw_detail = self.probe_gateway_health_detail() or {}
        if bool((gw_detail.get("primaryBackend") or {}).get("configured")):
            return True, ""
        local_info = probe_local_ai_runtimes(self.home_dir)
        ol = local_info.get("ollama") or {}
        if bool(ol.get("running")) and bool(local_info.get("installedModels")):
            return True, ""
        return (
            False,
            "Chưa đăng nhập tài khoản AI hoặc chưa cấu hình nguồn AI sẵn sàng. Vào tab Trợ lý AI để đăng nhập Tài khoản Nido, Google/Gemini, ChatGPT, Claude hoặc cấu hình khóa API / Ollama.",
        )

    def run_agent_task(
        self,
        prompt: str,
        files: list[str] | None = None,
        out_path: str | None = None,
    ) -> tuple[bool, str]:
        ready, block_msg = self.is_agent_execution_ready()
        if not ready:
            return False, block_msg
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


ALLOWED_SETTINGS_PANELS = frozenset({"online-accounts", "users", "printers", "system", "about"})


def build_settings_panel_argv(panel: str) -> list[str]:
    """Build a safe argv list to open an allowlisted GNOME Settings panel."""
    if panel not in ALLOWED_SETTINGS_PANELS:
        raise ValueError(f"Unsupported settings panel: {panel!r}")
    gcc = shutil.which("gnome-control-center")
    if not gcc:
        raise FileNotFoundError("Không tìm thấy Cài đặt hệ thống (gnome-control-center).")
    return [gcc, panel]


def launch_settings_panel(panel: str) -> tuple[bool, str]:
    """Open an allowlisted GNOME Settings panel in a detached process."""
    try:
        argv = build_settings_panel_argv(panel)
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, f"Đã mở Cài đặt ({panel})."
    except Exception as exc:
        return False, str(exc)


def run_self_test() -> int:
    from self_test import run_self_test as _run_self_test

    return _run_self_test(sys.modules[__name__])


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


def _format_nido_account_subtitle(acct: dict[str, Any]) -> str:
    if acct.get("signedIn"):
        who = str(acct.get("fullName") or acct.get("email") or "Đã đăng nhập").strip()
        email = str(acct.get("email") or "").strip()
        if email and who != email:
            who = f"{who} ({email})"
        plan = str(acct.get("planName") or "Miễn phí (Free)")
        quota = acct.get("quota") if isinstance(acct.get("quota"), dict) else {}
        if acct.get("isPro") and quota:
            rem = int(quota.get("aiCreditsRemaining") or 0)
            tot = int(quota.get("aiCreditsQuota") or 0)
            return f"{who} · {plan} (còn {rem}/{tot} lượt AI)"
        return f"{who} · {plan}"
    return "Đăng nhập một lần cho NidoOffice, NidoAgent và sao lưu cài đặt NidoOS."


def _format_agent_status_subtitle(spec: Any, state: dict[str, Any]) -> str:
    if state.get("signedIn"):
        acct = state.get("accountLabel") or "Đã kết nối"
        plan = f" · {state['plan']}" if state.get("plan") else ""
        return f"Đã kết nối: {acct}{plan}"
    if state.get("installed"):
        return f"Đã cài đặt, chưa đăng nhập — {spec.subtitle}"
    if getattr(spec, "download_url", ""):
        return f"Chưa cài đặt — {spec.subtitle}"
    return f"Chưa kết nối — bấm để cài đặt và đăng nhập qua trình duyệt."


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
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # type: ignore

    def _install_css() -> None:
        try:
            display = Gdk.Display.get_default()
            if display is None:
                return
            provider = Gtk.CssProvider()
            if hasattr(provider, "load_from_string"):
                provider.load_from_string(NIDO_HUB_CSS)
            else:
                provider.load_from_data(NIDO_HUB_CSS.encode("utf-8"))
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        except Exception:
            pass

    def _wrap_clamped(child: Any, max_width: int = 860) -> Any:
        clamp = Adw.Clamp(maximum_size=max_width, tightening_threshold=min(680, max_width))
        clamp.set_child(child)
        return clamp

    def _make_scrolled_canvas(max_width: int = 860) -> tuple[Any, Any]:
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        if hasattr(scroll, "set_policy"):
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        vbox.set_margin_top(18)
        vbox.set_margin_bottom(24)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)
        scroll.set_child(_wrap_clamped(vbox, max_width=max_width))
        return scroll, vbox

    def _set_badge_state(badge: Any, label_text: str, ready: bool) -> None:
        badge.set_text(label_text)
        for cls in ("nido-badge-ok", "nido-badge-warn", "nido-badge-muted"):
            badge.remove_css_class(cls)
        if ready:
            badge.add_css_class("nido-badge-ok")
        elif "tắt" in label_text.lower():
            badge.add_css_class("nido-badge-muted")
        else:
            badge.add_css_class("nido-badge-warn")

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
            _install_css()
            Gtk.Window.set_default_icon_name("com.nidoholdings.NidoHub")
            win = Adw.ApplicationWindow(application=self)
            win.set_icon_name("com.nidoholdings.NidoHub")
            win.set_title("NidoHub")
            win.set_default_size(980, 700)
            win.set_size_request(740, 540)

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

            # --- Page 1: AI Assistant & Accounts (Instant Cached Canvas -> Async Background Probe) ---
            ai_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            banner = Adw.Banner()
            if hasattr(banner, "set_use_markup"):
                banner.set_use_markup(False)
            ai_enabled_init = backend.is_ai_enabled()
            gw_msg = (
                "Trợ lý AI đang tắt trên NidoOS — bật công tắc bên dưới khi bạn muốn sử dụng."
                if not ai_enabled_init
                else "Đang kiểm tra trạng thái Trợ lý AI..."
            )
            banner.set_title(gw_msg if hasattr(banner, "set_use_markup") else escape_markup(gw_msg))
            banner.set_revealed(not ai_enabled_init)
            ai_box.append(banner)

            ai_scroll, ai_page = _make_scrolled_canvas(max_width=860)
            env = backend.load_env_file_only()
            initial_report = backend.get_cached_ai_readiness_report()
            initial_states_list = list(initial_report.get("agents") or [])
            initial_states = {entry["id"]: entry for entry in initial_states_list}
            initial_nido_acct = initial_report.get("nidoAccount") or backend.get_nido_account_status(
                refresh_quota=False
            )

            # Horizontal 3-Tile Capability Strip (pinned compactly at top)
            cap_strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            cap_strip.set_homogeneous(True)
            cap_tiles: dict[str, tuple[Any, Any, Any]] = {}
            short_cap_titles = {
                "nidobox_ocr": "Nhận diện ảnh (OCR)",
                "nidooffice_ai": "Trợ lý NidoOffice",
                "nidoagent": "Điều phối NidoAgent",
            }
            for cap_key in ("nidobox_ocr", "nidooffice_ai", "nidoagent"):
                cap = initial_report["capabilities"][cap_key]
                tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                tile.add_css_class("nido-status-tile")
                hdr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                t_lbl = Gtk.Label(label=short_cap_titles.get(cap_key, cap["title"]), xalign=0.0, hexpand=True)
                t_lbl.set_ellipsize(3)
                t_lbl.add_css_class("nido-status-title")
                b_lbl = Gtk.Label()
                b_lbl.add_css_class("nido-status-badge")
                _set_badge_state(b_lbl, cap["badge"], bool(cap.get("ready")))
                hdr_row.append(t_lbl)
                hdr_row.append(b_lbl)
                d_lbl = Gtk.Label(label=cap["detail"], xalign=0.0)
                d_lbl.set_ellipsize(3)
                d_lbl.add_css_class("dim-label")
                tile.append(hdr_row)
                tile.append(d_lbl)
                cap_strip.append(tile)
                cap_tiles[cap_key] = (t_lbl, b_lbl, d_lbl)
            ai_page.append(cap_strip)

            # Group 1: Master AI Toggle & Hardware Overview
            diag_group = _make_group(
                title="Trợ lý AI trên NidoOS",
                description="Bật để dùng AI trong NidoOffice, NidoBox và NidoAgent; tắt để máy chạy hoàn toàn ngoại tuyến.",
            )
            ai_master_guard = [False]
            ai_master_switch = _make_plain_row(
                Adw.SwitchRow(),
                "Bật Trợ lý AI",
                "Dùng chung cho toàn bộ ứng dụng NidoOS.",
            )
            ai_master_switch.set_active(backend.is_ai_enabled())
            diag_group.add(ai_master_switch)

            reopen_ob_row = _make_plain_row(
                Adw.ActionRow(),
                "Trình thiết lập AI lần đầu",
                "Mở lại màn hình hướng dẫn chọn chế độ và đăng nhập tài khoản.",
            )
            reopen_ob_btn = Gtk.Button(label="Mở trình hướng dẫn", valign=Gtk.Align.CENTER)
            reopen_ob_row.add_suffix(reopen_ob_btn)
            reopen_ob_row.set_activatable_widget(reopen_ob_btn)
            diag_group.add(reopen_ob_row)

            hw_row = _make_plain_row(
                Adw.ActionRow(),
                "Cấu hình máy",
                f"{initial_report['hardwareLineVi']} · {initial_report['ollamaStatusVi']}",
            )
            rescan_btn = Gtk.Button(label="Quét lại", valign=Gtk.Align.CENTER)
            hw_row.add_suffix(rescan_btn)
            diag_group.add(hw_row)

            # Advanced settings group (collapsed by default via Adw.ExpanderRow)
            adv_group = _make_group(
                title="Cài đặt nâng cao",
                description="Tùy chọn cho người dùng có khóa API riêng hoặc muốn chạy AI trực tiếp trên máy.",
            )
            apikey_expander = _make_plain_row(
                Adw.ExpanderRow(),
                "Khóa API cá nhân",
                "Kết nối trực tiếp khóa Google Gemini, OpenAI, Anthropic hoặc địa chỉ Ollama.",
            )
            if hasattr(apikey_expander, "set_expanded"):
                apikey_expander.set_expanded(False)

            model_list = Gtk.StringList.new([label for _, label in PROVIDERS])
            prov_row = _make_plain_row(Adw.ComboRow(model=model_list), "Nguồn AI ưu tiên")
            cur_prov = env.get("NIDOAI_PRIMARY_BACKEND", "gemini")
            prov_idx = next((i for i, (k, _) in enumerate(PROVIDERS) if k == cur_prov), 0)
            prov_row.set_selected(prov_idx)
            apikey_expander.add_row(prov_row)

            free_key_row = _make_plain_row(
                Adw.ActionRow(),
                "Lấy khóa Google Gemini miễn phí",
                "Mở Google AI Studio trên trình duyệt để tạo khóa dùng cho nhận diện ảnh và soạn thảo.",
            )
            get_key_btn = Gtk.Button(label="Lấy khóa", valign=Gtk.Align.CENTER)
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
            apikey_expander.add_row(free_key_row)

            gemini_row = _make_plain_row(Adw.PasswordEntryRow(), "Khóa Google Gemini")
            gemini_row.set_text(env.get("GEMINI_API_KEY", ""))
            apikey_expander.add_row(gemini_row)

            openai_row = _make_plain_row(Adw.PasswordEntryRow(), "Khóa OpenAI")
            openai_row.set_text(env.get("OPENAI_API_KEY", ""))
            apikey_expander.add_row(openai_row)

            anthropic_row = _make_plain_row(Adw.PasswordEntryRow(), "Khóa Anthropic Claude")
            anthropic_row.set_text(env.get("ANTHROPIC_API_KEY", ""))
            apikey_expander.add_row(anthropic_row)

            ollama_row = _make_plain_row(
                Adw.EntryRow(), "Địa chỉ Ollama trên máy"
            )
            ollama_row.set_text(env.get("NIDOAI_LOCAL_BASE_URL", ""))
            apikey_expander.add_row(ollama_row)

            local_model_row = _make_plain_row(
                Adw.EntryRow(), "Tên mô hình cục bộ mặc định (ví dụ: gemma3:4b)"
            )
            local_model_row.set_text(env.get("NIDOAI_LOCAL_MODEL", ""))
            apikey_expander.add_row(local_model_row)

            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_box.set_valign(Gtk.Align.CENTER)

            save_btn = Gtk.Button(label="Lưu & kiểm tra", valign=Gtk.Align.CENTER)
            save_btn.add_css_class("suggested-action")

            check_btn = Gtk.Button(label="Kiểm tra kết nối", valign=Gtk.Align.CENTER)
            btn_box.append(check_btn)
            btn_box.append(save_btn)

            api_action_row = _make_plain_row(
                Adw.ActionRow(),
                "Áp dụng cấu hình khóa API",
                "Lưu an toàn vào kho khóa hệ thống và kiểm tra kết nối ngay.",
            )
            api_action_row.add_suffix(btn_box)
            apikey_expander.add_row(api_action_row)
            adv_group.add(apikey_expander)

            # Forward refs updated by refresh_readiness_ui()
            ollama_status_row: Any = None
            ollama_install_btn: Any = None
            ollama_model_btns: list[tuple[str, Gtk.Button]] = []
            agent_rows: dict[str, tuple[Any, Gtk.Button, Any]] = {}
            nido_acct_row: Any = None
            nido_auth_btn: Any = None
            nido_refresh_btn: Any = None
            sync_toggle_row: Any = None
            sync_toggle_guard = [False]

            def _apply_nido_account_main_ui(acct: dict[str, Any]) -> None:
                signed_in = bool(acct.get("signedIn"))
                if nido_acct_row is not None:
                    nido_acct_row.set_subtitle(_format_nido_account_subtitle(acct))
                if nido_refresh_btn is not None:
                    nido_refresh_btn.set_visible(signed_in)
                    nido_refresh_btn.set_sensitive(True)
                if nido_auth_btn is not None:
                    nido_auth_btn.set_label("Đăng xuất" if signed_in else "Đăng nhập")
                    if signed_in:
                        nido_auth_btn.remove_css_class("suggested-action")
                    else:
                        nido_auth_btn.add_css_class("suggested-action")
                    nido_auth_btn.set_sensitive(True)
                if sync_toggle_row is not None:
                    sync_obj = acct.get("sync") if isinstance(acct.get("sync"), dict) else {}
                    sync_toggle_guard[0] = True
                    try:
                        sync_toggle_row.set_active(bool(sync_obj.get("enabled")))
                        sync_toggle_row.set_sensitive(signed_in)
                    finally:
                        sync_toggle_guard[0] = False

            def _open_nido_account_dialog(
                on_signed_in: Any,
                toast_target: Any,
            ) -> None:
                use_adw_dialog = hasattr(Adw, "Dialog")
                if use_adw_dialog:
                    dlg = Adw.Dialog(title="Tài khoản Nido", content_width=440, content_height=420)
                    dlg_tb = Adw.ToolbarView()
                    dlg_hb = Adw.HeaderBar()
                    dlg_tb.add_top_bar(dlg_hb)
                else:
                    dlg = Adw.Window(title="Tài khoản Nido", modal=True)
                    dlg.set_transient_for(win)
                    dlg.set_default_size(440, 420)
                    dlg_tb = Adw.ToolbarView()
                    dlg_hb = Adw.HeaderBar()
                    dlg_tb.add_top_bar(dlg_hb)

                body_scroll, body_box = _make_scrolled_canvas(max_width=420)
                mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                mode_row.add_css_class("linked")
                mode_row.set_halign(Gtk.Align.CENTER)
                btn_mode_login = Gtk.ToggleButton(label="Đăng nhập", active=True)
                btn_mode_reg = Gtk.ToggleButton(label="Tạo tài khoản")
                btn_mode_reg.set_group(btn_mode_login)
                mode_row.append(btn_mode_login)
                mode_row.append(btn_mode_reg)
                body_box.append(mode_row)

                form_group = _make_group(
                    title="Đăng nhập Tài khoản Nido",
                    description="Dùng chung cho NidoOffice, Trợ lý NidoAgent và đồng bộ cài đặt NidoOS.",
                )
                name_row = _make_plain_row(Adw.EntryRow(), "Họ và tên")
                name_row.set_visible(False)
                email_row = _make_plain_row(Adw.EntryRow(), "Email")
                pass_row = _make_plain_row(Adw.PasswordEntryRow(), "Mật khẩu")
                form_group.add(name_row)
                form_group.add(email_row)
                form_group.add(pass_row)
                body_box.append(form_group)

                status_lbl = Gtk.Label(xalign=0.0, wrap=True)
                status_lbl.add_css_class("dim-label")
                status_lbl.set_visible(False)
                body_box.append(status_lbl)

                btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                btn_bar.set_halign(Gtk.Align.END)
                resend_btn = Gtk.Button(label="Gửi lại xác nhận")
                resend_btn.set_visible(False)
                cancel_btn = Gtk.Button(label="Đóng")
                submit_btn = Gtk.Button(label="Đăng nhập")
                submit_btn.add_css_class("suggested-action")
                submit_btn.add_css_class("pill")
                btn_bar.append(resend_btn)
                btn_bar.append(cancel_btn)
                btn_bar.append(submit_btn)
                body_box.append(btn_bar)

                def _close_dlg() -> None:
                    dlg.close()

                def _sync_mode(*_a: Any) -> None:
                    is_reg = bool(btn_mode_reg.get_active())
                    name_row.set_visible(is_reg)
                    submit_btn.set_label("Tạo tài khoản" if is_reg else "Đăng nhập")
                    resend_btn.set_visible(False)
                    status_lbl.set_visible(False)

                btn_mode_login.connect("toggled", _sync_mode)
                btn_mode_reg.connect("toggled", _sync_mode)
                cancel_btn.connect("clicked", lambda _b: _close_dlg())

                def _on_resend(_b: Gtk.Button) -> None:
                    em = email_row.get_text().strip()
                    if not em:
                        return
                    _b.set_sensitive(False)

                    def worker() -> None:
                        _, msg_r = backend.resend_nido_confirmation(em)

                        def done() -> bool:
                            _b.set_sensitive(True)
                            status_lbl.set_text(msg_r)
                            status_lbl.set_visible(True)
                            return False

                        GLib.idle_add(done)

                    threading.Thread(target=worker, daemon=True).start()

                resend_btn.connect("clicked", _on_resend)

                def _on_submit(*_a: Any) -> None:
                    is_reg = bool(btn_mode_reg.get_active())
                    em = email_row.get_text().strip()
                    pw = pass_row.get_text()
                    nm = name_row.get_text().strip()
                    submit_btn.set_sensitive(False)
                    submit_btn.set_label("Đang xử lý...")
                    status_lbl.set_visible(False)

                    def worker() -> None:
                        if is_reg:
                            ok_a, msg_a, detail = backend.register_nido_account(
                                em, pw, full_name=nm, set_as_primary=True, restart_service=True
                            )
                            needs_confirm = bool(ok_a and detail.get("emailConfirmationRequired"))
                            acct_now = backend.get_nido_account_status(refresh_quota=False) if (ok_a and not needs_confirm) else None
                        else:
                            ok_a, msg_a, acct_now = backend.login_nido_account(
                                em, pw, set_as_primary=True, restart_service=True
                            )
                            needs_confirm = False

                        def done() -> bool:
                            submit_btn.set_sensitive(True)
                            submit_btn.set_label("Tạo tài khoản" if is_reg else "Đăng nhập")
                            if needs_confirm:
                                status_lbl.set_text(msg_a)
                                status_lbl.set_visible(True)
                                resend_btn.set_visible(True)
                                btn_mode_login.set_active(True)
                                return False
                            if not ok_a:
                                status_lbl.set_text(msg_a)
                                status_lbl.set_visible(True)
                                return False
                            _add_toast(toast_target, msg_a[:150])
                            _close_dlg()
                            on_signed_in(acct_now or backend.get_nido_account_status(refresh_quota=False))
                            return False

                        GLib.idle_add(done)

                    threading.Thread(target=worker, daemon=True).start()

                submit_btn.connect("clicked", _on_submit)
                pass_row.connect("entry-activated", _on_submit)

                dlg_tb.set_content(body_scroll)
                if use_adw_dialog:
                    dlg.set_child(dlg_tb)
                    dlg.present(win)
                else:
                    dlg.set_content(dlg_tb)
                    dlg.present()

            def _format_banner_line(
                gw_line: str,
                agent_states_list: list[dict[str, Any]],
                caps: dict[str, Any],
                nido_acct: dict[str, Any] | None = None,
            ) -> str:
                if not backend.is_ai_enabled():
                    return "Trợ lý AI đang tắt trên NidoOS — bật công tắc bên dưới khi bạn muốn sử dụng."
                if nido_acct and nido_acct.get("signedIn") and caps.get("nidooffice_ai", {}).get("ready"):
                    return f"Đã kết nối {nido_acct.get('email') or 'Tài khoản Nido'} ({nido_acct.get('planName', 'Nido')})."
                signed = [str(a.get("id", "")).upper() for a in agent_states_list if a.get("signedIn")]
                if signed and "chưa cấu hình" in gw_line:
                    names = ", ".join(signed)
                    return f"Đã kết nối {names} cho NidoOffice và NidoAgent."
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
                        btn_w.set_label("Đăng nhập")
                        btn_w.add_css_class("suggested-action")
                    elif getattr(spec, "download_url", ""):
                        btn_w.set_label("Tải về")
                        btn_w.remove_css_class("suggested-action")
                    else:
                        btn_w.set_label("Cài & đăng nhập")
                        btn_w.add_css_class("suggested-action")
                    btn_w.set_sensitive(True)

            readiness_in_flight = [False]

            def refresh_readiness_ui(
                persist_agents: bool = False,
                show_toast: bool = False,
                refresh_nido: bool = False,
                ensure_gateway: bool = False,
            ) -> None:
                if readiness_in_flight[0] and not show_toast:
                    return
                readiness_in_flight[0] = True

                def worker() -> None:
                    try:
                        if (
                            ensure_gateway
                            and backend.is_ai_enabled()
                            and not backend.is_onboarding_required()
                        ):
                            backend.ensure_gateway_ready(start_service=True)
                        agents_list = backend.probe_agent_accounts(persist=persist_agents)
                        hw = detect_hardware(backend.home_dir)
                        local_info = probe_local_ai_runtimes(backend.home_dir)
                        gw_detail = backend.probe_gateway_health_detail()
                        nido_acct = backend.get_nido_account_status(refresh_quota=refresh_nido)
                        full_env = backend.load_env()
                        rep = build_ai_readiness_report(
                            hw, local_info, gw_detail, agents_list, nido_account=nido_acct
                        )
                        _, gw_line = backend.probe_gateway_health()
                        banner_line = _format_banner_line(
                            gw_line, agents_list, rep["capabilities"], nido_acct=nido_acct
                        )
                        states_map = {e["id"]: e for e in agents_list}

                        def apply_ui() -> bool:
                            ai_on_now = backend.is_ai_enabled()
                            banner.set_title(
                                banner_line
                                if hasattr(banner, "set_use_markup")
                                else escape_markup(banner_line)
                            )
                            banner.set_revealed(not ai_on_now)
                            hw_row.set_subtitle(
                                f"{rep['hardwareLineVi']} · {rep['ollamaStatusVi']}"
                            )
                            for ck in ("nidobox_ocr", "nidooffice_ai", "nidoagent"):
                                c_info = rep["capabilities"][ck]
                                _, b_lbl, d_lbl = cap_tiles[ck]
                                if not ai_on_now:
                                    _set_badge_state(b_lbl, "Đang tắt", False)
                                    d_lbl.set_text("Đã tắt trên NidoOS — bật lại Trợ lý AI khi cần.")
                                else:
                                    _set_badge_state(
                                        b_lbl, c_info["badge"], bool(c_info.get("ready"))
                                    )
                                    d_lbl.set_text(c_info["detail"])
                            _apply_agent_tab_availability(
                                ai_on_now,
                                bool(rep["capabilities"]["nidoagent"].get("ready")),
                            )
                            _apply_nido_account_main_ui(nido_acct)
                            _apply_agent_rows_ui(states_map)
                            if not gemini_row.get_text() and full_env.get("GEMINI_API_KEY"):
                                gemini_row.set_text(full_env["GEMINI_API_KEY"])
                            if not openai_row.get_text() and full_env.get("OPENAI_API_KEY"):
                                openai_row.set_text(full_env["OPENAI_API_KEY"])
                            if not anthropic_row.get_text() and full_env.get("ANTHROPIC_API_KEY"):
                                anthropic_row.set_text(full_env["ANTHROPIC_API_KEY"])
                            p_kind = full_env.get("NIDOAI_PRIMARY_BACKEND", "gemini")
                            p_idx = next((i for i, (k, _) in enumerate(PROVIDERS) if k == p_kind), 0)
                            prov_row.set_selected(p_idx)
                            ol_info = rep["local"]["ollama"]
                            if ollama_status_row is not None:
                                ollama_status_row.set_subtitle(rep["ollamaStatusVi"])
                            if ollama_install_btn is not None:
                                if ol_info.get("installed"):
                                    ollama_install_btn.set_label("Đã cài")
                                    ollama_install_btn.set_sensitive(False)
                                    ollama_install_btn.remove_css_class("suggested-action")
                                else:
                                    ollama_install_btn.set_label("Cài Ollama")
                                    ollama_install_btn.set_sensitive(True)
                                    ollama_install_btn.add_css_class("suggested-action")
                            installed_set = set(rep["local"].get("installedModels", []))
                            for mid, mbtn in ollama_model_btns:
                                is_pulled = any(
                                    m == mid or m.startswith(f"{mid}:") for m in installed_set
                                )
                                mbtn.set_label("Kích hoạt" if is_pulled else "Tải & bật")
                                mbtn.set_sensitive(True)
                            if onboarding_active[0]:
                                ob_latest_states[:] = agents_list
                                ob_latest_nido[0] = nido_acct
                                _update_onboarding_ui()
                            if show_toast:
                                _add_toast(toast_overlay, banner_line[:160])
                            return False

                        GLib.idle_add(apply_ui)
                    finally:
                        readiness_in_flight[0] = False

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
                _btn.set_label("Đang kiểm tra...")

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
                        _btn.set_label("Lưu & kiểm tra")
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

            # Group 1b: Unified Nido Account (SSO for NidoOffice, NidoAgent & OS Sync)
            nido_group = _make_group(
                title="Tài khoản Nido",
                description="Một tài khoản dùng chung cho NidoOffice, NidoAgent và sao lưu cài đặt hệ thống.",
            )
            nido_acct_row = _make_plain_row(
                Adw.ActionRow(),
                "Tài khoản Nido",
                _format_nido_account_subtitle(initial_nido_acct),
            )
            nido_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            nido_btn_box.set_valign(Gtk.Align.CENTER)
            nido_refresh_btn = Gtk.Button(label="Làm mới", valign=Gtk.Align.CENTER)
            nido_auth_btn = Gtk.Button(
                label="Đăng xuất" if initial_nido_acct.get("signedIn") else "Đăng nhập",
                valign=Gtk.Align.CENTER,
            )
            if not initial_nido_acct.get("signedIn"):
                nido_auth_btn.add_css_class("suggested-action")
            nido_refresh_btn.set_visible(bool(initial_nido_acct.get("signedIn")))

            def _on_nido_auth_clicked(_b: Gtk.Button) -> None:
                if _b.get_label() == "Đăng xuất":
                    _b.set_sensitive(False)
                    _b.set_label("Đang đăng xuất...")

                    def worker() -> None:
                        _, msg_out = backend.logout_nido_account(restart_service=True)

                        def done() -> bool:
                            _add_toast(toast_overlay, msg_out[:140])
                            refresh_readiness_ui(persist_agents=False)
                            return False

                        GLib.idle_add(done)

                    threading.Thread(target=worker, daemon=True).start()
                    return

                _open_nido_account_dialog(
                    lambda _acct: refresh_readiness_ui(persist_agents=False),
                    toast_overlay,
                )

            nido_refresh_btn.connect(
                "clicked",
                lambda _b: refresh_readiness_ui(persist_agents=False, show_toast=True, refresh_nido=True),
            )
            nido_auth_btn.connect("clicked", _on_nido_auth_clicked)
            nido_btn_box.append(nido_refresh_btn)
            nido_btn_box.append(nido_auth_btn)
            nido_acct_row.add_suffix(nido_btn_box)
            nido_group.add(nido_acct_row)

            sync_expander = _make_plain_row(
                Adw.ExpanderRow(),
                "Đồng bộ cài đặt NidoOS",
                "Lưu hoặc khôi phục bảng gõ tắt Sen Việt, tùy chọn NidoBox và cấu hình Trợ lý AI.",
            )
            if hasattr(sync_expander, "set_expanded"):
                sync_expander.set_expanded(False)

            sync_toggle_row = _make_plain_row(
                Adw.SwitchRow(),
                "Ghi nhận trạng thái đồng bộ",
                "Cần đăng nhập Tài khoản Nido để gắn hồ sơ sao lưu vào tài khoản.",
            )
            sync_toggle_guard[0] = True
            try:
                sync_toggle_row.set_active(bool((initial_nido_acct.get("sync") or {}).get("enabled")))
                sync_toggle_row.set_sensitive(bool(initial_nido_acct.get("signedIn")))
            finally:
                sync_toggle_guard[0] = False

            def _on_sync_toggled(sw: Any, _pspec: Any) -> None:
                if sync_toggle_guard[0]:
                    return
                backend.set_os_sync_enabled(bool(sw.get_active()))
                _add_toast(
                    toast_overlay,
                    "Đã bật ghi nhận đồng bộ cài đặt." if sw.get_active() else "Đã tắt ghi nhận đồng bộ cài đặt.",
                )

            sync_toggle_row.connect("notify::active", _on_sync_toggled)
            sync_expander.add_row(sync_toggle_row)

            sync_export_row = _make_plain_row(
                Adw.ActionRow(),
                "Sao lưu cài đặt ra tệp",
                "Xuất bảng gõ tắt Sen Việt, tùy chọn NidoBox và cài đặt AI (không bao gồm mật khẩu hay khóa).",
            )
            sync_export_btn = Gtk.Button(label="Sao lưu...", valign=Gtk.Align.CENTER)

            def _run_export_sync(dest: Path | None) -> None:
                sync_export_btn.set_sensitive(False)

                def worker() -> None:
                    _, msg_e, _ = backend.export_os_sync_bundle(dest_path=dest)

                    def done() -> bool:
                        sync_export_btn.set_sensitive(True)
                        _add_toast(toast_overlay, msg_e[:150])
                        return False

                    GLib.idle_add(done)

                threading.Thread(target=worker, daemon=True).start()

            def _on_export_clicked(_b: Gtk.Button) -> None:
                if hasattr(Gtk.FileDialog, "save"):
                    fdlg = Gtk.FileDialog(title="Lưu bản sao lưu cài đặt NidoOS")
                    if hasattr(fdlg, "set_initial_name"):
                        fdlg.set_initial_name("nidoos-settings-backup.json")

                    def _on_saved(d: Any, res: Any) -> None:
                        try:
                            gf = d.save_finish(res)
                            if gf and gf.get_path():
                                _run_export_sync(Path(gf.get_path()))
                        except Exception:
                            pass

                    fdlg.save(win, None, _on_saved)
                else:
                    _run_export_sync(None)

            sync_export_btn.connect("clicked", _on_export_clicked)
            sync_export_row.add_suffix(sync_export_btn)
            sync_expander.add_row(sync_export_row)

            sync_import_row = _make_plain_row(
                Adw.ActionRow(),
                "Khôi phục cài đặt từ tệp",
                "Nạp lại hồ sơ cài đặt NidoOS đã sao lưu trước đó.",
            )
            sync_import_btn = Gtk.Button(label="Khôi phục...", valign=Gtk.Align.CENTER)

            def _run_import_sync(src: Path | None) -> None:
                sync_import_btn.set_sensitive(False)

                def worker() -> None:
                    ok_i, msg_i, _ = backend.import_os_sync_bundle(src_path=src, restart_service=True)

                    def done() -> bool:
                        sync_import_btn.set_sensitive(True)
                        _add_toast(toast_overlay, msg_i[:150])
                        if ok_i:
                            refresh_readiness_ui(persist_agents=False)
                        return False

                    GLib.idle_add(done)

                threading.Thread(target=worker, daemon=True).start()

            def _on_import_clicked(_b: Gtk.Button) -> None:
                if hasattr(Gtk.FileDialog, "open"):
                    fdlg = Gtk.FileDialog(title="Chọn tệp sao lưu cài đặt NidoOS")

                    def _on_opened(d: Any, res: Any) -> None:
                        try:
                            gf = d.open_finish(res)
                            if gf and gf.get_path():
                                _run_import_sync(Path(gf.get_path()))
                        except Exception:
                            pass

                    fdlg.open(win, None, _on_opened)
                else:
                    _run_import_sync(None)

            sync_import_btn.connect("clicked", _on_import_clicked)
            sync_import_row.add_suffix(sync_import_btn)
            sync_expander.add_row(sync_import_row)
            nido_group.add(sync_expander)

            # Group 2: Connected AI Accounts
            agents_group = _make_group(
                title="Tài khoản AI",
                description="Đăng nhập một chạm qua trình duyệt bằng tài khoản Google, ChatGPT hoặc Claude của bạn.",
            )

            init_banner = (
                gw_msg
                if not ai_enabled_init
                else _format_banner_line(
                    gw_msg, initial_states_list, initial_report["capabilities"], nido_acct=initial_nido_acct
                )
            )
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
                        "Đăng nhập"
                        if st.get("installed")
                        else ("Tải về" if getattr(spec, "download_url", "") else "Cài & đăng nhập")
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

            # Collapsed Local Offline AI (Ollama) Expander inside Cài đặt nâng cao
            ollama_expander = _make_plain_row(
                Adw.ExpanderRow(),
                "AI ngoại tuyến trên máy (Ollama)",
                "Tải và chạy mô hình trực tiếp trên máy tính khi không có mạng.",
            )
            if hasattr(ollama_expander, "set_expanded"):
                ollama_expander.set_expanded(False)

            ol_prog_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            ol_prog_box.set_margin_top(6)
            ol_prog_box.set_margin_bottom(6)
            ol_prog_box.set_margin_start(12)
            ol_prog_box.set_margin_end(12)
            ol_prog_lbl = Gtk.Label(xalign=0)
            ol_prog_lbl.set_ellipsize(3)
            ol_prog_bar = Gtk.ProgressBar()
            ol_prog_box.append(ol_prog_lbl)
            ol_prog_box.append(ol_prog_bar)
            ol_prog_row = Adw.PreferencesRow()
            ol_prog_row.set_child(ol_prog_box)
            ol_prog_row.set_visible(False)
            ollama_expander.add_row(ol_prog_row)

            ol_init = initial_report["local"]["ollama"]
            ollama_status_row = _make_plain_row(
                Adw.ActionRow(),
                "Nền tảng Ollama",
                initial_report["ollamaStatusVi"],
            )
            ollama_install_btn = Gtk.Button(
                label="Đã cài" if ol_init.get("installed") else "Cài Ollama",
                valign=Gtk.Align.CENTER,
            )
            if ol_init.get("installed"):
                ollama_install_btn.set_sensitive(False)
            else:
                ollama_install_btn.add_css_class("suggested-action")

            def _set_ol_progress(pct: int, text: str) -> None:
                def _ui() -> bool:
                    ol_prog_row.set_visible(True)
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
                            ol_prog_row.set_visible(False)
                        _add_toast(toast_overlay, msg_o[:150])
                        refresh_readiness_ui(persist_agents=False)
                        return False

                    GLib.idle_add(finish)

                threading.Thread(target=worker, daemon=True).start()

            ollama_install_btn.connect("clicked", on_install_ollama_clicked)
            ollama_status_row.add_suffix(ollama_install_btn)
            ollama_expander.add_row(ollama_status_row)

            installed_models_init = set(initial_report["local"].get("installedModels", []))
            for rec in initial_report["recommendedModels"]:
                mid = str(rec["id"])
                tags: list[str] = []
                if rec["recommended"]:
                    tags.append("Khuyên dùng")
                elif not rec["fits_hardware"]:
                    tags.append(f"Cần từ {rec['min_ram_gb']:.0f} GB RAM")
                tags.append(f"~{rec['size_gb']:.1f} GB")
                if rec["supports_vision"]:
                    tags.append("Hỗ trợ ảnh")
                m_row = _make_plain_row(
                    Adw.ActionRow(),
                    rec["label"],
                    f"{' · '.join(tags)} — {rec['reason_vi']}",
                )
                is_pulled = any(m == mid or m.startswith(f"{mid}:") for m in installed_models_init)
                m_btn = Gtk.Button(
                    label="Kích hoạt" if is_pulled else "Tải & bật",
                    valign=Gtk.Align.CENTER,
                )
                if rec["recommended"]:
                    m_btn.add_css_class("suggested-action")

                def on_pull_model_clicked(_b: Gtk.Button, target_mid: str = mid) -> None:
                    _b.set_sensitive(False)
                    _b.set_label("Đang tải...")
                    _set_ol_progress(4, f"Đang tải và bật {target_mid}...")

                    def worker() -> None:
                        ok_m, msg_m = backend.pull_and_activate_local_model(
                            target_mid, on_progress=_set_ol_progress
                        )

                        def finish() -> bool:
                            if ok_m:
                                ol_prog_row.set_visible(False)
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
                ollama_expander.add_row(m_row)
                ollama_model_btns.append((mid, m_btn))

            adv_group.add(ollama_expander)

            cloud_accounts_group = _make_group(
                title="Tài khoản máy & Đám mây",
                description="Đồng bộ hộp thư Geary, ổ đĩa đám mây trong Files và quản lý người dùng máy tính.",
            )

            def _on_open_panel(_btn: Any, panel: str) -> None:
                ok_open, msg_open = launch_settings_panel(panel)
                if not ok_open:
                    _add_toast(toast_overlay, msg_open)

            goa_row = _make_plain_row(
                Adw.ActionRow(),
                "Email & Đám mây (Google / Microsoft / Nextcloud)",
                "Kết nối hộp thư Geary Mail, Drive, OneDrive và Lịch.",
            )
            goa_btn = Gtk.Button(label="Mở cài đặt", valign=Gtk.Align.CENTER)
            goa_btn.connect("clicked", _on_open_panel, "online-accounts")
            goa_row.add_suffix(goa_btn)
            goa_row.set_activatable_widget(goa_btn)
            cloud_accounts_group.add(goa_row)

            users_row = _make_plain_row(
                Adw.ActionRow(),
                "Người dùng máy tính",
                "Đổi mật khẩu đăng nhập, ảnh đại diện hoặc thêm tài khoản mới.",
            )
            users_btn = Gtk.Button(label="Quản lý", valign=Gtk.Align.CENTER)
            users_btn.connect("clicked", _on_open_panel, "users")
            users_row.add_suffix(users_btn)
            users_row.set_activatable_widget(users_btn)
            cloud_accounts_group.add(users_row)

            refresh_agents_btn = Gtk.Button(label="Làm mới trạng thái")
            refresh_agents_btn.add_css_class("pill")
            refresh_agents_btn.set_halign(Gtk.Align.CENTER)
            refresh_agents_btn.connect("clicked", lambda _b: refresh_agent_rows())
            agents_refresh_group = Adw.PreferencesGroup()
            agents_refresh_group.add(refresh_agents_btn)

            ai_page.append(diag_group)
            ai_page.append(nido_group)
            ai_page.append(agents_group)
            ai_page.append(adv_group)
            ai_page.append(cloud_accounts_group)
            ai_page.append(agents_refresh_group)
            ai_box.append(ai_scroll)
            stack.add_titled_with_icon(ai_box, "welcome", "Trợ lý AI", "preferences-system-symbolic")

            # --- Page 2: Software Center & Updates ---
            apps_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            # Live Operation Progress Bar (revealed during install / remove / update / repair)
            op_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            op_box.set_margin_top(12)
            op_box.set_margin_bottom(6)
            op_box.set_margin_start(20)
            op_box.set_margin_end(20)
            op_box.set_visible(False)

            op_top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            op_status_lbl = Gtk.Label(label="", xalign=0.0, hexpand=True)
            op_status_lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
            op_cancel_btn = Gtk.Button(label="Hủy")
            op_repair_btn = Gtk.Button(label="Khôi phục hệ thống gói")
            op_repair_btn.add_css_class("destructive-action")
            op_repair_btn.set_visible(False)
            op_top_row.append(op_status_lbl)
            op_top_row.append(op_cancel_btn)
            op_top_row.append(op_repair_btn)

            op_progress = Gtk.ProgressBar()
            op_box.append(op_top_row)
            op_box.append(op_progress)
            apps_outer.append(_wrap_clamped(op_box, max_width=860))

            # Search & Action Toolbar (aligned with the 860px card stack)
            apps_ctrl = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            apps_ctrl.set_margin_top(14)
            apps_ctrl.set_margin_bottom(4)
            apps_ctrl.set_margin_start(20)
            apps_ctrl.set_margin_end(20)

            search_entry = Gtk.SearchEntry(hexpand=True)
            search_entry.set_placeholder_text(
                "Tìm ứng dụng (nhấn Enter để tìm thêm trực tuyến)..."
            )
            cat_keys = list(CATEGORY_LABELS.keys())
            cat_dropdown = Gtk.DropDown.new_from_strings([CATEGORY_LABELS[k] for k in cat_keys])
            sys_update_btn = Gtk.Button(label="Cập nhật hệ thống")
            sys_update_btn.add_css_class("suggested-action")
            apps_ctrl.append(search_entry)
            apps_ctrl.append(cat_dropdown)
            apps_ctrl.append(sys_update_btn)
            apps_outer.append(_wrap_clamped(apps_ctrl, max_width=860))

            apps_scroll, apps_page = _make_scrolled_canvas(max_width=860)
            apps_outer.append(apps_scroll)

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
                        title="Cài đặt gói phần mềm (.deb)",
                        description=f"Tệp đã mở: {package_file.name}",
                    )
                    local_pkg_arch = normalize_arch_name(str(local_pkg_meta.get("arch") or ""))
                    local_pkg_arch_incompatible = bool(
                        local_pkg_arch
                        and local_pkg_arch != "all"
                        and local_pkg_arch != backend.app_store.host_arch
                    )
                    pkg_title = (
                        f"{local_pkg_meta['package']} {local_pkg_meta['version']}".strip()
                        + (f" ({local_pkg_meta['arch']})" if local_pkg_meta.get("arch") else "")
                    )
                    local_summary = (
                        f"Tệp cài đặt này dành cho {local_pkg_meta['arch']}, không tương thích với máy tính hiện tại ({backend.app_store.host_arch})."
                        if local_pkg_arch_incompatible
                        else str(local_pkg_meta.get("summary") or str(package_file))
                    )
                    local_row = _make_plain_row(
                        Adw.ActionRow(),
                        pkg_title,
                        local_summary,
                    )
                    local_pkg_install_btn = Gtk.Button(label="Cài đặt", valign=Gtk.Align.CENTER)
                    if local_pkg_meta.get("invalid") or local_pkg_arch_incompatible:
                        local_pkg_install_btn.set_sensitive(False)
                    else:
                        local_pkg_install_btn.add_css_class("suggested-action")
                    local_row.add_suffix(local_pkg_install_btn)
                    local_pkg_group.add(local_row)
                    apps_page.append(local_pkg_group)
                elif package_file.suffix.lower() == ".flatpakref":
                    local_pkg_group = _make_group(
                        title="Cài đặt ứng dụng Flatpak",
                        description=f"Tệp đã mở: {package_file.name}",
                    )
                    local_row = _make_plain_row(
                        Adw.ActionRow(),
                        package_file.stem,
                        "Cài đặt ứng dụng cho tài khoản hiện tại.",
                    )
                    local_pkg_install_btn = Gtk.Button(label="Cài đặt", valign=Gtk.Align.CENTER)
                    local_pkg_install_btn.add_css_class("suggested-action")
                    local_row.add_suffix(local_pkg_install_btn)
                    local_pkg_group.add(local_row)
                    apps_page.append(local_pkg_group)

            maint_status = backend.app_store.probe_system_maintenance_status()
            sec_txt = (
                "Đang bật tự động"
                if maint_status["securityUpdatesAuto"]
                else ("Đã cài đặt" if maint_status["securityUpdatesInstalled"] else "Chưa bật")
            )
            fw_txt = "Sẵn sàng" if maint_status["fwupdAvailable"] else "Chưa bật"
            maint_group = _make_group(
                title="Cập nhật & Bảo trì hệ thống",
                description=(
                    f"Kiến trúc {maint_status['hostArch']} · Vá bảo mật tự động: {sec_txt} · "
                    f"Cập nhật phần cứng: {fw_txt}."
                ),
            )
            apps_page.append(maint_group)

            apps_group = _make_group(
                title="Kho ứng dụng",
                description="Chỉ hiển thị các ứng dụng được kiểm duyệt và tương thích với thiết bị này.",
            )
            apps_page.append(apps_group)

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
                    if item.get("archSupported") is False and not item.get("installed"):
                        continue
                    if selected_cat != "all" and item.get("category") != selected_cat:
                        continue
                    if q_low:
                        hay = f"{item['name']} {item['description']} {' '.join(item.get('packages', []))} {item.get('flatpakId', '')}".lower()
                        if q_low not in hay:
                            continue

                    state_badge = "Đã cài" if item["installed"] else "Chưa cài"
                    arch_part = f" · {item['archNote']}" if item.get("archNote") else ""
                    sub = f"{item['sourceLabel']} · {state_badge}{arch_part} — {item['description']}"
                    row = _make_plain_row(Adw.ActionRow(), item["name"], sub)

                    btn_box_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                    btn_box_row.set_valign(Gtk.Align.CENTER)

                    act_btn = Gtk.Button(
                        label="Cập nhật" if item["installed"] else "Cài đặt",
                        valign=Gtk.Align.CENTER,
                    )
                    if not item["installed"]:
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

                if not rendered_rows:
                    empty_row = _make_plain_row(
                        Adw.ActionRow(),
                        "Không tìm thấy ứng dụng tương thích",
                        "Thử từ khóa khác hoặc chọn danh mục Tất cả.",
                    )
                    apps_group.add(empty_row)
                    rendered_rows.append(empty_row)

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

            # --- Page 3: Printers & Scanners (Single Clamped ScrolledCanvas, No Double Scroll) ---
            printer_scroll, printer_page = _make_scrolled_canvas(max_width=860)

            printer_list_group = _make_group(
                title="Máy in đang kết nối",
                description="Tự nhận máy in USB và Wi-Fi hiện đại; chỉ tải thêm trình điều khiển khi dòng máy cần.",
            )
            printer_status_row = _make_plain_row(
                Adw.ActionRow(),
                "Đang tìm máy in USB và mạng nội bộ...",
                "Nhấn Làm mới sau khi cắm cáp USB hoặc bật Wi-Fi cho máy in.",
            )
            printer_refresh_btn = Gtk.Button(label="Làm mới", valign=Gtk.Align.CENTER)
            printer_sys_btn = Gtk.Button(label="Cài đặt hệ thống", valign=Gtk.Align.CENTER)
            printer_sys_btn.connect("clicked", _on_open_panel, "printers")
            p_hdr_btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            p_hdr_btns.append(printer_refresh_btn)
            p_hdr_btns.append(printer_sys_btn)
            printer_status_row.add_suffix(p_hdr_btns)
            printer_list_group.add(printer_status_row)

            printer_dynamic_rows: list[Any] = []

            def _refresh_printers_ui() -> None:
                printer_refresh_btn.set_sensitive(False)

                def worker() -> None:
                    detected = probe_printers(host_arch=backend.app_store.host_arch)

                    def _apply() -> bool:
                        printer_refresh_btn.set_sensitive(True)
                        for old in printer_dynamic_rows:
                            printer_list_group.remove(old)
                        printer_dynamic_rows.clear()

                        if not detected:
                            printer_status_row.set_title("Chưa tìm thấy máy in")
                            printer_status_row.set_subtitle(
                                "Cắm cáp USB hoặc thêm máy in qua địa chỉ IP ở mục bên dưới."
                            )
                            return False

                        printer_status_row.set_title(f"Tìm thấy {len(detected)} máy in")
                        printer_status_row.set_subtitle(
                            "Chọn Kết nối ngay hoặc cài trình điều khiển nếu máy yêu cầu."
                        )
                        for pr in detected:
                            sub_txt = f"{pr.adapter_label} — {pr.note_vi}"
                            p_row = _make_plain_row(Adw.ActionRow(), pr.name, sub_txt)
                            if pr.status == "needs-apt" and pr.recommended_packages:
                                pkgs_list = list(pr.recommended_packages)
                                act_b = Gtk.Button(label="Cài trình điều khiển", valign=Gtk.Align.CENTER)
                                act_b.add_css_class("suggested-action")
                                act_b.connect(
                                    "clicked",
                                    lambda _b, pl=pkgs_list, pname=pr.name: (
                                        stack.set_visible_child_name("apps"),
                                        start_store_operation(
                                            f"Cài trình điều khiển {pname}",
                                            lambda h, cb, p=pl: backend.app_store._run_streaming_helper(
                                                ["install-apt", *p], h, on_progress=cb
                                            ),
                                        ),
                                    ),
                                )
                                p_row.add_suffix(act_b)
                            elif pr.status == "adapter-available" and pr.uri and pr.recommended_model:
                                act_b = Gtk.Button(label="Kết nối ngay", valign=Gtk.Align.CENTER)
                                act_b.add_css_class("suggested-action")
                                q_default = "".join(c if c.isalnum() or c in "_-" else "_" for c in pr.name).strip("_") or "NidoPrinter"
                                act_b.connect(
                                    "clicked",
                                    lambda _b, qn=q_default[:48], u=pr.uri, m=pr.recommended_model, pn=pr.name: (
                                        stack.set_visible_child_name("apps"),
                                        start_store_operation(
                                            f"Kết nối máy in {pn}",
                                            lambda h, cb, q=qn, uri_v=u, mod_v=m: backend.configure_printer(
                                                q, uri_v, mod_v, handle=h, on_progress=cb
                                            ),
                                        ),
                                    ),
                                )
                                p_row.add_suffix(act_b)
                            else:
                                act_b = Gtk.Button(label="Mở cài đặt", valign=Gtk.Align.CENTER)
                                act_b.connect("clicked", _on_open_panel, "printers")
                                p_row.add_suffix(act_b)

                            printer_list_group.add(p_row)
                            printer_dynamic_rows.append(p_row)
                        return False

                    GLib.idle_add(_apply)

                threading.Thread(target=worker, daemon=True).start()

            printer_refresh_btn.connect("clicked", lambda _b: _refresh_printers_ui())
            _refresh_printers_ui()

            lan_printer_group = _make_group(
                title="Thêm máy in mạng văn phòng",
                description="Nhập địa chỉ IP của máy in hoặc máy photocopy trong mạng nội bộ.",
            )
            lan_ip_row = _make_plain_row(Adw.EntryRow(), "Địa chỉ IP hoặc liên kết máy in (ví dụ: 192.168.1.50)")
            lan_printer_group.add(lan_ip_row)

            adapter_keys = list(UNIVERSAL_ADAPTER_MODELS.keys())
            adapter_labels = Gtk.StringList.new([UNIVERSAL_ADAPTER_MODELS[k][0] for k in adapter_keys])
            lan_adapter_row = _make_plain_row(Adw.ComboRow(model=adapter_labels), "Kiểu máy in")
            lan_adapter_row.set_selected(0)
            lan_printer_group.add(lan_adapter_row)

            lan_add_row = _make_plain_row(
                Adw.ActionRow(),
                "Kết nối máy in mạng",
                "Sử dụng trình điều khiển tiêu chuẩn có sẵn trên hệ thống.",
            )
            lan_add_btn = Gtk.Button(label="Thêm máy in", valign=Gtk.Align.CENTER)
            lan_add_btn.add_css_class("suggested-action")

            def _on_add_lan_printer(_btn: Gtk.Button) -> None:
                host_val = lan_ip_row.get_text().strip()
                idx = int(lan_adapter_row.get_selected())
                akey = adapter_keys[idx] if 0 <= idx < len(adapter_keys) else "everywhere"
                try:
                    qn, quri, qmod = build_network_printer_spec(host_val, akey)
                except ValueError as exc:
                    _add_toast(toast_overlay, str(exc))
                    return
                stack.set_visible_child_name("apps")
                start_store_operation(
                    f"Thêm máy in {qn}",
                    lambda h, cb: backend.configure_printer(qn, quri, qmod, handle=h, on_progress=cb),
                )

            lan_add_btn.connect("clicked", _on_add_lan_printer)
            lan_add_row.add_suffix(lan_add_btn)
            lan_printer_group.add(lan_add_row)

            printer_page.append(printer_list_group)
            printer_page.append(lan_printer_group)
            stack.add_titled_with_icon(printer_scroll, "printers", "Máy in", "printer-symbolic")

            # --- Page 4: Quick AI Task & Document Assistant ---
            agent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            agent_box.set_margin_top(18)
            agent_box.set_margin_bottom(24)
            agent_box.set_margin_start(20)
            agent_box.set_margin_end(20)
            agent_box.set_vexpand(True)

            agent_gate_group = _make_group(
                title="Chưa sẵn sàng chạy Trợ lý AI",
                description="Bật Trợ lý AI và đăng nhập ít nhất 1 tài khoản để sử dụng tác vụ thông minh.",
            )
            agent_gate_row = _make_plain_row(
                Adw.ActionRow(),
                "Chưa đăng nhập tài khoản AI",
                "Đăng nhập Tài khoản Nido, Google/Gemini, ChatGPT hoặc Claude trong tab Trợ lý AI.",
            )
            agent_gate_btn = Gtk.Button(label="Mở Cài đặt AI", valign=Gtk.Align.CENTER)
            agent_gate_btn.add_css_class("suggested-action")
            agent_gate_row.add_suffix(agent_gate_btn)
            agent_gate_group.add(agent_gate_row)
            agent_box.append(agent_gate_group)

            ctrl_group = _make_group(
                title="Xử lý tài liệu với NidoAgent",
                description="Đính kèm tài liệu (.docx, .xlsx, .pdf, .md) để tóm tắt hoặc xuất báo cáo nhanh.",
            )
            prompt_row = _make_plain_row(Adw.EntryRow(), "Yêu cầu thực hiện")
            prompt_row.set_text("Tổng hợp các thông tin và giá trị chính từ tài liệu đính kèm.")
            ctrl_group.add(prompt_row)

            files_row = _make_plain_row(Adw.ActionRow(), "Tệp đính kèm", "Chưa chọn tệp nào")
            pick_btn = Gtk.Button(label="Chọn tệp...", valign=Gtk.Align.CENTER)
            clear_btn = Gtk.Button(label="Xóa", valign=Gtk.Align.CENTER)
            files_row.add_suffix(pick_btn)
            files_row.add_suffix(clear_btn)
            ctrl_group.add(files_row)

            export_modes = [
                ("", "Hiển thị trực tiếp trên màn hình"),
                (".docx", "Xuất tệp Word (.docx)"),
                (".xlsx", "Xuất bảng tính Excel (.xlsx)"),
            ]
            export_row = _make_plain_row(
                Adw.ComboRow(model=Gtk.StringList.new([m[1] for m in export_modes])),
                "Định dạng xuất",
            )
            ctrl_group.add(export_row)
            agent_box.append(ctrl_group)

            agent_act_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            agent_act_box.set_halign(Gtk.Align.START)
            run_btn = Gtk.Button(label="Chạy tác vụ")
            run_btn.add_css_class("suggested-action")
            run_btn.add_css_class("pill")
            copy_btn = Gtk.Button(label="Sao chép")
            copy_btn.add_css_class("pill")
            copy_btn.set_sensitive(False)
            agent_act_box.append(run_btn)
            agent_act_box.append(copy_btn)
            agent_box.append(agent_act_box)

            scroller = Gtk.ScrolledWindow(vexpand=True)
            scroller.set_size_request(-1, 220)
            out_view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            out_view.set_monospace(False)
            out_view.set_left_margin(16)
            out_view.set_right_margin(16)
            out_view.set_top_margin(14)
            out_view.set_bottom_margin(14)
            out_view.set_pixels_below_lines(4)
            out_buf = out_view.get_buffer()
            scroller.set_child(out_view)

            out_frame = Gtk.Frame()
            out_frame.add_css_class("nido-result-frame")
            out_frame.set_vexpand(True)
            out_frame.set_child(scroller)
            agent_box.append(out_frame)

            last_agent_plain: list[str] = [""]
            agent_tab_usable: list[bool] = [False]

            def _apply_agent_tab_availability(ai_on: bool, agent_ready: bool) -> None:
                usable = bool(ai_on and agent_ready)
                agent_tab_usable[0] = usable
                agent_gate_group.set_visible(not usable)
                if not ai_on:
                    agent_gate_row.set_title("Trợ lý AI đang tắt trên NidoOS")
                    agent_gate_row.set_subtitle(
                        "Hệ thống không chạy dịch vụ AI. Bật lại Trợ lý AI khi bạn cần xử lý tài liệu."
                    )
                    agent_gate_btn.set_label("Bật Trợ lý AI")
                elif not agent_ready:
                    agent_gate_row.set_title("Chưa đăng nhập tài khoản AI")
                    agent_gate_row.set_subtitle(
                        "Vui lòng đăng nhập Tài khoản Nido, Google/Gemini, ChatGPT hoặc Claude để bắt đầu."
                    )
                    agent_gate_btn.set_label("Mở Cài đặt AI")
                for w in (ctrl_group, prompt_row, pick_btn, clear_btn, export_row, run_btn):
                    w.set_sensitive(usable)
                if not usable:
                    copy_btn.set_sensitive(False)

            def _on_agent_gate_clicked(_b: Gtk.Button) -> None:
                if not backend.is_ai_enabled():
                    _enter_onboarding_view()
                else:
                    stack.set_visible_child_name("welcome")

            agent_gate_btn.connect("clicked", _on_agent_gate_clicked)

            def on_copy_result(_b: Gtk.Button) -> None:
                txt = last_agent_plain[0].strip()
                if not txt:
                    return
                try:
                    clipboard = win.get_clipboard()
                    clipboard.set(txt)
                    _add_toast(toast_overlay, "Đã sao chép kết quả vào bộ nhớ tạm.")
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
                out_buf.set_text("Đang xử lý yêu cầu...")
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
                        _b.set_sensitive(agent_tab_usable[0])
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

            _apply_agent_tab_availability(
                backend.is_ai_enabled(),
                bool(initial_report["capabilities"]["nidoagent"].get("ready")),
            )

            agent_clamp = _wrap_clamped(agent_box, max_width=860)
            agent_clamp.set_vexpand(True)
            stack.add_titled_with_icon(agent_clamp, "agent", "Tác vụ AI", "system-run-symbolic")

            if initial_page in {"welcome", "apps", "printers", "agent"}:
                stack.set_visible_child_name(initial_page)

            # --- Dedicated Mandatory AI Onboarding Gate ---
            ob_toolbar = Adw.ToolbarView()
            ob_header = Adw.HeaderBar()
            ob_title = Adw.WindowTitle(
                title="Thiết lập Trợ lý AI",
                subtitle="Bật hoặc tắt AI và kết nối ít nhất 1 tài khoản khi bật",
            )
            ob_header.set_title_widget(ob_title)
            ob_toolbar.add_top_bar(ob_header)

            ob_toast_overlay = Adw.ToastOverlay()
            ob_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

            ob_banner = Adw.Banner()
            if hasattr(ob_banner, "set_use_markup"):
                ob_banner.set_use_markup(False)
            ob_banner.set_revealed(False)
            ob_outer.append(ob_banner)

            ob_scroll, ob_page = _make_scrolled_canvas(max_width=760)
            ob_scroll.set_vexpand(True)

            # Onboarding Group 0: AI ON / OFF switch
            ob_toggle_group = _make_group(
                title="Trợ lý AI trên NidoOS",
                description=(
                    "Bật AI để dùng trợ lý và soạn thảo thông minh, hoặc tắt để giữ máy gọn nhẹ "
                    "như máy tính truyền thống."
                ),
            )
            ob_ai_switch = _make_plain_row(
                Adw.SwitchRow(),
                "Bật Trợ lý AI",
                "Khi bật, cần đăng nhập ít nhất 1 tài khoản AI bên dưới",
            )
            ob_ai_switch.set_active(True if backend.is_onboarding_required() else backend.is_ai_enabled())
            ob_toggle_group.add(ob_ai_switch)

            # Onboarding Group 1: Mandatory Sign-In (visible when AI ON)
            ob_accounts_group = _make_group(
                title="Kết nối tài khoản AI",
                description=(
                    "Đăng nhập Tài khoản Nido (dùng chung NidoOffice & NidoAgent) "
                    "hoặc ít nhất 1 tài khoản trợ lý bên dưới"
                ),
            )
            ob_agent_rows: dict[str, tuple[Any, Gtk.Button, Any]] = {}
            ob_latest_states: list[dict[str, Any]] = list(initial_states_list)
            ob_latest_nido: list[dict[str, Any]] = [dict(initial_nido_acct)]

            ob_nido_row = _make_plain_row(
                Adw.ActionRow(),
                "Tài khoản Nido (Khuyên dùng)",
                _format_nido_account_subtitle(ob_latest_nido[0]),
            )
            ob_nido_btn = Gtk.Button(label="Đăng nhập / Đăng ký", valign=Gtk.Align.CENTER)
            ob_nido_row.add_suffix(ob_nido_btn)

            ob_check_row = _make_plain_row(
                Adw.ActionRow(),
                "Trạng thái kết nối",
                "Đang kiểm tra tài khoản đã đăng nhập...",
            )
            ob_check_btn = Gtk.Button(label="Kiểm tra lại", valign=Gtk.Align.CENTER)
            ob_check_row.add_suffix(ob_check_btn)

            # Onboarding Group 2: Optional API Key (collapsed by default inside ExpanderRow)
            ob_apikey_group = _make_group(
                title="Tùy chọn nâng cao",
                description="Không bắt buộc nếu bạn đã đăng nhập tài khoản ở trên.",
            )
            ob_apikey_expander = _make_plain_row(
                Adw.ExpanderRow(),
                "Khóa API cá nhân (tùy chọn)",
                "Dùng cho đọc ảnh thông minh và dự phòng kết nối",
            )
            ob_apikey_expander.set_expanded(False)
            ob_apikey_group.add(ob_apikey_expander)

            ob_prov_model = Gtk.StringList.new([label for _, label in PROVIDERS])
            ob_prov_row = _make_plain_row(Adw.ComboRow(model=ob_prov_model), "Nhà cung cấp ưu tiên")
            ob_prov_row.set_selected(prov_idx)
            ob_apikey_expander.add_row(ob_prov_row)

            ob_free_key_row = _make_plain_row(
                Adw.ActionRow(),
                "Lấy khóa Google Gemini miễn phí",
                "Mở Google AI Studio trên trình duyệt nếu bạn muốn tạo khóa dự phòng",
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
            ob_apikey_expander.add_row(ob_free_key_row)

            ob_gemini_row = _make_plain_row(Adw.PasswordEntryRow(), "Khóa Google Gemini")
            ob_gemini_row.set_text(env.get("GEMINI_API_KEY", ""))
            ob_apikey_expander.add_row(ob_gemini_row)

            ob_openai_row = _make_plain_row(Adw.PasswordEntryRow(), "Khóa OpenAI")
            ob_openai_row.set_text(env.get("OPENAI_API_KEY", ""))
            ob_apikey_expander.add_row(ob_openai_row)

            ob_anthropic_row = _make_plain_row(Adw.PasswordEntryRow(), "Khóa Anthropic")
            ob_anthropic_row.set_text(env.get("ANTHROPIC_API_KEY", ""))
            ob_apikey_expander.add_row(ob_anthropic_row)

            # Onboarding Group 3: AI OFF Confirmation (visible when AI OFF)
            ob_off_group = _make_group(
                title="Chế độ tắt AI",
                description="Máy chạy gọn nhẹ ở chế độ desktop truyền thống, không khởi chạy dịch vụ AI.",
            )
            ob_off_info_row = _make_plain_row(
                Adw.ActionRow(),
                "Bạn có thể bật lại bất cứ lúc nào",
                "Mở NidoHub để bật AI và đăng nhập tài khoản khi cần dùng.",
            )
            ob_off_group.add(ob_off_info_row)

            # Pinned Bottom Action Bar for Onboarding
            ob_footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            ob_footer.set_margin_top(10)
            ob_footer.set_margin_bottom(14)
            ob_footer.set_margin_start(20)
            ob_footer.set_margin_end(20)

            ob_gate_lbl = Gtk.Label(xalign=0.0, wrap=True)
            ob_gate_lbl.add_css_class("dim-label")
            ob_footer.append(ob_gate_lbl)

            ob_action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            ob_action_row.set_halign(Gtk.Align.END)
            ob_finish_btn = Gtk.Button(label="Hoàn tất & Bắt đầu dùng NidoOS")
            ob_finish_btn.add_css_class("suggested-action")
            ob_finish_btn.add_css_class("pill")
            ob_action_row.append(ob_finish_btn)
            ob_footer.append(ob_action_row)
            ob_footer_wrap = _wrap_clamped(ob_footer, max_width=760)
            ob_footer_wrap.add_css_class("nido-footer-bar")

            def _stop_ob_poll() -> None:
                if ob_poll_source_id[0]:
                    try:
                        GLib.source_remove(ob_poll_source_id[0])
                    except Exception:
                        pass
                    ob_poll_source_id[0] = 0

            def _update_onboarding_ui() -> None:
                ai_on = bool(ob_ai_switch.get_active())
                nido_now = ob_latest_nido[0]
                gate = evaluate_ai_onboarding_gate(ai_on, ob_latest_states, nido_account=nido_now)
                states_map = {e["id"]: e for e in ob_latest_states}

                ob_accounts_group.set_visible(ai_on)
                ob_apikey_group.set_visible(ai_on)
                ob_off_group.set_visible(not ai_on)

                banner_txt = gate["reasonVi"]
                ob_banner.set_title(
                    banner_txt if hasattr(ob_banner, "set_use_markup") else escape_markup(banner_txt)
                )
                ob_banner.set_revealed(ai_on and not gate["canComplete"])
                ob_gate_lbl.set_text(gate["reasonVi"])
                ob_check_row.set_subtitle(
                    f"{gate['statusBadge']} — "
                    + (
                        ", ".join(gate["signedInLabels"])
                        if gate["signedInLabels"]
                        else "Chưa có tài khoản nào được kết nối."
                    )
                )

                ob_nido_row.set_subtitle(_format_nido_account_subtitle(nido_now))
                if nido_now.get("signedIn"):
                    ob_nido_btn.set_label("Đã kết nối ✓")
                    ob_nido_btn.remove_css_class("suggested-action")
                    ob_nido_btn.set_sensitive(True)
                else:
                    ob_nido_btn.set_label("Đăng nhập / Đăng ký")
                    ob_nido_btn.add_css_class("suggested-action")
                    ob_nido_btn.set_sensitive(True)

                for spec in MANAGED_AGENT_SPECS:
                    if spec.id not in ob_agent_rows:
                        continue
                    st = states_map.get(spec.id, {"installed": False, "signedIn": False})
                    row_w, btn_w, _ = ob_agent_rows[spec.id]
                    row_w.set_subtitle(_format_agent_status_subtitle(spec, st))
                    if st.get("signedIn"):
                        btn_w.set_label("Đã kết nối ✓")
                        btn_w.remove_css_class("suggested-action")
                        btn_w.set_sensitive(True)
                    elif st.get("installed"):
                        btn_w.set_label("Đăng nhập ngay")
                        btn_w.add_css_class("suggested-action")
                        btn_w.set_sensitive(True)
                    else:
                        btn_w.set_label("Kết nối ngay")
                        btn_w.add_css_class("suggested-action")
                        btn_w.set_sensitive(True)

                if not ai_on:
                    ob_finish_btn.set_label("Hoàn tất & Vào màn hình chính")
                    ob_finish_btn.set_sensitive(True)
                elif gate["canComplete"]:
                    ob_finish_btn.set_label("Hoàn tất & Bắt đầu dùng NidoOS")
                    ob_finish_btn.set_sensitive(True)
                    _stop_ob_poll()
                else:
                    ob_finish_btn.set_label("Hoàn tất (Cần ≥ 1 tài khoản AI)")
                    ob_finish_btn.set_sensitive(False)

            ob_poll_in_flight = [False]

            def _refresh_onboarding_accounts(show_toast: bool = False) -> None:
                if ob_poll_in_flight[0] and not show_toast:
                    return
                ob_poll_in_flight[0] = True

                def worker() -> None:
                    try:
                        fresh_states = backend.probe_agent_accounts(persist=True)
                        fresh_nido = backend.get_nido_account_status(refresh_quota=show_toast)

                        def apply() -> bool:
                            ob_latest_states[:] = fresh_states
                            ob_latest_nido[0] = fresh_nido
                            _apply_nido_account_main_ui(fresh_nido)
                            _update_onboarding_ui()
                            if show_toast:
                                g = evaluate_ai_onboarding_gate(
                                    bool(ob_ai_switch.get_active()),
                                    ob_latest_states,
                                    nido_account=ob_latest_nido[0],
                                )
                                _add_toast(
                                    ob_toast_overlay, f"{g['statusBadge']}: {g['reasonVi']}"[:150]
                                )
                            return False

                        GLib.idle_add(apply)
                    finally:
                        ob_poll_in_flight[0] = False

                threading.Thread(target=worker, daemon=True).start()

            def _start_ob_poll() -> None:
                _stop_ob_poll()
                ob_poll_ticks[0] = 0

                def _tick() -> bool:
                    if not onboarding_active[0] or not ob_ai_switch.get_active():
                        ob_poll_source_id[0] = 0
                        return False
                    ob_poll_ticks[0] += 1
                    if ob_poll_ticks[0] > 18:  # Stop after ~3 minutes
                        ob_poll_source_id[0] = 0
                        return False
                    _refresh_onboarding_accounts(show_toast=False)
                    return True

                ob_poll_source_id[0] = int(GLib.timeout_add_seconds(10, _tick))

            def _on_ob_nido_signed_in(acct_now: dict[str, Any]) -> None:
                ob_latest_nido[0] = acct_now
                _apply_nido_account_main_ui(acct_now)
                if acct_now.get("signedIn"):
                    nido_idx = next((idx for idx, (k, _) in enumerate(PROVIDERS) if k == "nido"), -1)
                    if nido_idx >= 0:
                        ob_prov_row.set_selected(nido_idx)
                _update_onboarding_ui()

            def _on_ob_nido_clicked(_b: Gtk.Button) -> None:
                if ob_latest_nido[0].get("signedIn"):
                    email_txt = str(ob_latest_nido[0].get("email") or "Nido").strip()
                    _add_toast(ob_toast_overlay, f"Tài khoản Nido ({email_txt}) đã đăng nhập sẵn sàng!")
                    return
                _open_nido_account_dialog(_on_ob_nido_signed_in, ob_toast_overlay)

            ob_nido_btn.connect("clicked", _on_ob_nido_clicked)
            ob_accounts_group.add(ob_nido_row)

            for spec in MANAGED_AGENT_SPECS:
                st = initial_states.get(spec.id, {"installed": False, "signedIn": False})
                ob_row = _make_plain_row(
                    Adw.ActionRow(),
                    spec.display_name,
                    _format_agent_status_subtitle(spec, st),
                )
                ob_btn = Gtk.Button(label="Kết nối ngay", valign=Gtk.Align.CENTER)

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
                        fresh_nido = backend.get_nido_account_status(refresh_quota=False)

                        def done() -> bool:
                            ob_latest_states[:] = fresh
                            ob_latest_nido[0] = fresh_nido
                            _apply_nido_account_main_ui(fresh_nido)
                            _add_toast(ob_toast_overlay, msg_c[:150])
                            _update_onboarding_ui()
                            if ok_c and not evaluate_ai_onboarding_gate(
                                True,
                                ob_latest_states,
                                nido_account=ob_latest_nido[0],
                            )["canComplete"]:
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

            ob_page.append(ob_toggle_group)
            ob_page.append(ob_accounts_group)
            ob_page.append(ob_apikey_group)
            ob_page.append(ob_off_group)
            ob_outer.append(ob_scroll)
            ob_toast_overlay.set_child(ob_outer)
            ob_toolbar.set_content(ob_toast_overlay)
            ob_toolbar.add_bottom_bar(ob_footer_wrap)
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
                                "Trợ lý AI đang tắt trên NidoOS — bật công tắc bên dưới khi bạn muốn sử dụng."
                            )
                            banner.set_revealed(True)
                            for ck in ("nidobox_ocr", "nidooffice_ai", "nidoagent"):
                                if ck in cap_tiles:
                                    _, b_lbl, d_lbl = cap_tiles[ck]
                                    _set_badge_state(b_lbl, "Đang tắt", False)
                                    d_lbl.set_text("Đã tắt trên NidoOS — bật lại Trợ lý AI khi cần.")
                            _apply_agent_tab_availability(False, False)
                            return False

                        GLib.idle_add(done_off)
                        return

                    fresh_agents = backend.probe_agent_accounts(persist=True)
                    fresh_nido = backend.get_nido_account_status(refresh_quota=False)
                    gate_now = evaluate_ai_onboarding_gate(True, fresh_agents, nido_account=fresh_nido)
                    if not gate_now["canComplete"]:
                        def open_gate() -> bool:
                            ob_latest_states[:] = fresh_agents
                            ob_latest_nido[0] = fresh_nido
                            _apply_nido_account_main_ui(fresh_nido)
                            _enter_onboarding_view()
                            _add_toast(
                                ob_toast_overlay,
                                (
                                    "Vui lòng đăng nhập Tài khoản Nido hoặc ít nhất 1 tài khoản trợ lý "
                                    "(Google/Gemini, ChatGPT, Claude)."
                                ),
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
                    cleanup_active_login_processes()
                    return False

                def _confirm_disable_and_exit() -> None:
                    _stop_ob_poll()
                    cleanup_active_login_processes()
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
                            "Chế độ AI yêu cầu đăng nhập Tài khoản Nido hoặc ít nhất 1 tài khoản trợ lý "
                            "(Google/Gemini, ChatGPT, Claude) hoặc chọn Tắt AI trước khi vào màn hình chính."
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
            GLib.idle_add(
                lambda: (
                    refresh_readiness_ui(
                        persist_agents=False,
                        ensure_gateway=(not onboarding_mode),
                    ),
                    False,
                )[1]
            )

    app = NidoHubApp()
    return int(app.run([]))


def main() -> int:
    parser = argparse.ArgumentParser(description="NidoHub Desktop")
    parser.add_argument(
        "--page",
        choices=("welcome", "apps", "printers", "agent"),
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
