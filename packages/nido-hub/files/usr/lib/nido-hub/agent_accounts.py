#!/usr/bin/env python3
"""OS Keystore (libsecret/Keychain) & Managed Official Agent CLI helpers for NidoHub."""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import platform as py_platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Official npm 10.9.2 supports Node ^18.17.0 || >=20.5.0 (covers Ubuntu 24.04 Node 18 & 26.04 Node 22)
USER_NPM_VERSION = "10.9.2"
USER_NPM_TARBALL_URL = f"https://registry.npmjs.org/npm/-/npm-{USER_NPM_VERSION}.tgz"
USER_NPM_SHA512_B64 = (
    "iriPEPIkoMYUy3F6f3wwSZAU93E0Eg6cHwIR6jzzOXWSy+SD/rOODEs74cVONHKSx2obXtuUoyidVEhISrisgQ=="
)

# Official Google Antigravity CLI release manifest endpoint (same as https://antigravity.google/cli/install.sh)
AGY_MANIFEST_BASE_URL = (
    "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests"
)

KEYRING_SERVICE = "nidoai"
MANAGED_SECRET_KEYS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "NIDOAI_CLIENT_TOKEN",
    "NIDO_ACCOUNT_ACCESS_TOKEN",
    "NIDO_ACCOUNT_REFRESH_TOKEN",
)
PROVIDER_API_SECRET_KEYS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)
NIDO_ACCOUNT_SECRET_KEYS = (
    "NIDO_ACCOUNT_ACCESS_TOKEN",
    "NIDO_ACCOUNT_REFRESH_TOKEN",
)

SAFE_AGENT_STATUS_KEYS = frozenset(
    {
        "id",
        "installed",
        "signedIn",
        "authMethod",
        "accountLabel",
        "plan",
        "binaryPath",
        "version",
        "source",
        "updatedAt",
    }
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str], str | None, float], CommandResult]


def _build_agy_probe_env() -> dict[str, str]:
    """Ensure local desktop GNOME Keyring is reachable by `agy` even from SSH/subshells."""
    env = dict(os.environ)
    if sys.platform.startswith("linux"):
        try:
            uid = os.getuid()
            bus_path = Path(f"/run/user/{uid}/bus")
            if bus_path.exists():
                env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
                env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_path}")
                for ssh_var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
                    env.pop(ssh_var, None)
        except Exception:
            pass
    return env


def default_command_runner(
    argv: list[str],
    stdin_text: str | None = None,
    timeout: float = 5.0,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> CommandResult:
    try:
        proc = subprocess.run(
            argv,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
            check=False,
        )
        return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(127, "", str(exc))


class NidoSecretStore:
    """Protocol-compatible secret store matching NidoAI's src/security/secret-store.ts."""

    def __init__(
        self,
        platform: str | None = None,
        env: dict[str, str] | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.platform = platform or sys.platform
        self.env = env if env is not None else dict(os.environ)
        self.runner = runner or default_command_runner

    def _has_cmd(self, name: str) -> bool:
        if self.runner is default_command_runner:
            return shutil.which(name) is not None
        res = self.runner(["which", name], None, 2.0)
        return res.returncode == 0 and bool(res.stdout.strip())

    def detect_mode(self) -> str:
        override = self.env.get("NIDOAI_SECRET_STORE", "").strip().lower()
        if override in {"file", "env", "off", "0"}:
            return "file-0600-fallback"

        if self.platform.startswith("linux"):
            has_dbus = bool(self.env.get("DBUS_SESSION_BUS_ADDRESS", "").strip())
            if (has_dbus or override == "keyring") and self._has_cmd("secret-tool"):
                return "linux-libsecret"
            return "file-0600-fallback"

        if self.platform == "darwin":
            if self.env.get("SSH_CONNECTION") and override != "keyring":
                return "file-0600-fallback"
            if self._has_cmd("security"):
                return "macos-keychain"
            return "file-0600-fallback"

        return "file-0600-fallback"

    @property
    def is_keyring_active(self) -> bool:
        return self.detect_mode() != "file-0600-fallback"

    def mode_label_vi(self) -> str:
        mode = self.detect_mode()
        if mode == "linux-libsecret":
            return "GNOME Keyring (libsecret — mã hóa theo phiên)"
        if mode == "macos-keychain":
            return "macOS Keychain (mã hóa hệ thống)"
        return "Tệp cục bộ ~/.nidoai/.env.local (0600 — fallback headless)"

    def get_secret(self, key: str) -> str | None:
        if key not in MANAGED_SECRET_KEYS:
            return None
        mode = self.detect_mode()
        if mode == "linux-libsecret":
            res = self.runner(
                ["secret-tool", "lookup", "service", KEYRING_SERVICE, "account", key],
                None,
                3.0,
            )
            if res.returncode == 0:
                val = res.stdout.rstrip("\r\n")
                return val or None
            return None
        if mode == "macos-keychain":
            res = self.runner(
                ["security", "find-generic-password", "-s", KEYRING_SERVICE, "-a", key, "-w"],
                None,
                3.0,
            )
            if res.returncode == 0:
                val = res.stdout.rstrip("\r\n")
                return val or None
            return None
        return None

    def set_secret(self, key: str, value: str) -> bool:
        if key not in MANAGED_SECRET_KEYS:
            return False
        trimmed = value.strip()
        if not trimmed:
            return False
        mode = self.detect_mode()
        if mode == "linux-libsecret":
            # Secret passed strictly over stdin, never in process argv.
            res = self.runner(
                [
                    "secret-tool",
                    "store",
                    f"--label=NidoAI {key}",
                    "service",
                    KEYRING_SERVICE,
                    "account",
                    key,
                ],
                f"{trimmed}\n",
                4.0,
            )
            return res.returncode == 0
        if mode == "macos-keychain":
            esc_val = trimmed.replace("\\", "\\\\").replace('"', '\\"')
            line = f'add-generic-password -U -s "{KEYRING_SERVICE}" -a "{key}" -w "{esc_val}"\n'
            res = self.runner(["security", "-i"], line, 4.0)
            return res.returncode == 0
        return False

    def clear_secret(self, key: str) -> bool:
        if key not in MANAGED_SECRET_KEYS:
            return False
        mode = self.detect_mode()
        if mode == "linux-libsecret":
            res = self.runner(
                ["secret-tool", "clear", "service", KEYRING_SERVICE, "account", key],
                None,
                3.0,
            )
            return res.returncode == 0
        if mode == "macos-keychain":
            res = self.runner(
                ["security", "delete-generic-password", "-s", KEYRING_SERVICE, "-a", key],
                None,
                3.0,
            )
            return res.returncode == 0
        return False


@dataclass(frozen=True)
class ManagedAgentSpec:
    id: str
    display_name: str
    subtitle: str
    binary_name: str
    npm_package: str
    auth_method: str
    interactive_terminal_login: bool = False
    download_url: str = ""


MANAGED_AGENT_SPECS: tuple[ManagedAgentSpec, ...] = (
    ManagedAgentSpec(
        id="agy",
        display_name="Google Antigravity CLI (Google AI Ultra / Pro / Free)",
        subtitle="Thay thế Gemini CLI cá nhân. Cài 1 chạm kèm xác thực SHA-512 (không cần root) — đăng nhập gói Google AI Ultra/Pro dùng cho NidoOffice & NidoAgent.",
        binary_name="agy",
        npm_package="",
        auth_method="antigravity-oauth",
        interactive_terminal_login=True,
        download_url="",
    ),
    ManagedAgentSpec(
        id="codex",
        display_name="OpenAI Codex (ChatGPT Free / Plus / Pro)",
        subtitle="Chạy chính thức qua codex CLI, NidoOffice & NidoAgent (System Browser OAuth, không cần root).",
        binary_name="codex",
        npm_package="@openai/codex",
        auth_method="chatgpt-oauth",
        interactive_terminal_login=False,
    ),
    ManagedAgentSpec(
        id="claude",
        display_name="Anthropic Claude Code (Free / Pro / Max)",
        subtitle="Chạy trực tiếp qua claude CLI chính chủ, NidoOffice & NidoAgent (không proxy OAuth trái phép).",
        binary_name="claude",
        npm_package="@anthropic-ai/claude-code",
        auth_method="subscription-oauth",
        interactive_terminal_login=False,
    ),
)

MANAGED_AGENT_MAP: dict[str, ManagedAgentSpec] = {spec.id: spec for spec in MANAGED_AGENT_SPECS}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sanitize_agent_entry(provider_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {
        "id": provider_id,
        "installed": bool(raw.get("installed", False)),
        "signedIn": bool(raw.get("signedIn", False)),
        "updatedAt": str(raw.get("updatedAt") or _now_iso()),
    }
    for key in (
        "authMethod",
        "accountLabel",
        "plan",
        "binaryPath",
        "version",
        "source",
    ):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            clean[key] = val.strip()
    return {k: v for k, v in clean.items() if k in SAFE_AGENT_STATUS_KEYS}


def read_agents_status(nidoai_dir: Path) -> dict[str, dict[str, Any]]:
    status_file = nidoai_dir / "agents-status.json"
    if not status_file.is_file():
        return {}
    try:
        parsed = json.loads(status_file.read_text(encoding="utf-8"))
        providers = parsed.get("providers")
        if not isinstance(providers, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for pid, entry in providers.items():
            if isinstance(pid, str) and isinstance(entry, dict):
                out[pid] = sanitize_agent_entry(pid, entry)
        return out
    except Exception:
        return {}


def write_agents_status(
    nidoai_dir: Path,
    updates: dict[str, dict[str, Any]],
    merge: bool = True,
) -> Path:
    nidoai_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(nidoai_dir, 0o700)
    merged: dict[str, dict[str, Any]] = read_agents_status(nidoai_dir) if merge else {}
    for pid, entry in updates.items():
        if pid.strip():
            merged[pid] = sanitize_agent_entry(pid, entry)

    payload = {
        "schemaVersion": 1,
        "updatedAt": _now_iso(),
        "providers": merged,
    }
    target = nidoai_dir / "agents-status.json"
    fd, tmp_name = tempfile.mkstemp(dir=str(nidoai_dir), prefix=".agents-status.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return target


def managed_runtime_dir(home_dir: Path, provider_id: str) -> Path:
    if provider_id not in MANAGED_AGENT_MAP:
        raise ValueError(f"Unsupported agent provider: {provider_id}")
    return home_dir / ".local" / "share" / "nido" / "runtimes" / provider_id


def resolve_agent_binary(provider_id: str, home_dir: Path) -> str | None:
    spec = MANAGED_AGENT_MAP.get(provider_id)
    if spec is None:
        return None
    candidates = [
        managed_runtime_dir(home_dir, provider_id) / "node_modules" / ".bin" / spec.binary_name,
        home_dir / ".local" / "bin" / spec.binary_name,
        Path("/usr/local/bin") / spec.binary_name,
        Path("/usr/bin") / spec.binary_name,
    ]
    if provider_id == "agy":
        candidates.extend(
            [
                Path("/opt/Antigravity/resources/app/bin/agy"),
                Path("/opt/antigravity/resources/app/bin/agy"),
                Path("/usr/share/antigravity/resources/app/bin/agy"),
                Path("/Applications/Antigravity.app/Contents/Resources/app/bin/agy"),
            ]
        )
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    found = shutil.which(spec.binary_name)
    return found


def resolve_antigravity_platform(
    plat: str | None = None,
    machine: str | None = None,
) -> str | None:
    os_raw = (plat or sys.platform).lower()
    if os_raw.startswith("linux"):
        os_name = "linux"
    elif os_raw == "darwin":
        os_name = "darwin"
    else:
        return None

    m_raw = (machine or py_platform.machine()).lower()
    if m_raw in ("x86_64", "amd64"):
        arch = "amd64"
    elif m_raw in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        return None

    if os_name == "linux" and plat is None and (
        Path("/lib/libc.musl-x86_64.so.1").exists()
        or Path("/lib/libc.musl-aarch64.so.1").exists()
    ):
        return f"linux_{arch}_musl"
    return f"{os_name}_{arch}"


def verify_sha512_hex(payload: bytes, expected_hex: str) -> bool:
    exp = expected_hex.strip().lower()
    if len(exp) != 128:
        return False
    return hashlib.sha512(payload).hexdigest().lower() == exp


def install_antigravity_cli(
    home_dir: Path,
    platform_name: str | None = None,
    machine_name: str | None = None,
) -> tuple[bool, str, str | None]:
    """Install official Google Antigravity CLI (`agy`) with SHA-512 verification (zero root, amd64 & arm64)."""
    existing = resolve_agent_binary("agy", home_dir)
    if existing:
        return True, f"Đã có sẵn agy tại {existing}.", existing

    plat_key = resolve_antigravity_platform(platform_name, machine_name)
    if not plat_key:
        return False, "Kiến trúc hoặc hệ điều hành chưa được Antigravity CLI hỗ trợ.", None

    manifest_url = f"{AGY_MANIFEST_BASE_URL}/{plat_key}.json"
    try:
        req = urllib.request.Request(
            manifest_url, headers={"User-Agent": "NidoHub-AgentSetup/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return False, f"Không tải được manifest Google Antigravity CLI: {exc}", None

    version = str(manifest.get("version") or "").strip()
    dl_url = str(manifest.get("url") or "").strip()
    expected_sha512 = str(manifest.get("sha512") or "").strip()
    if not dl_url.startswith("https://") or len(expected_sha512) != 128:
        return False, "Manifest phát hành Google Antigravity CLI không hợp lệ.", None

    try:
        req_bin = urllib.request.Request(
            dl_url, headers={"User-Agent": "NidoHub-AgentSetup/1.0"}
        )
        with urllib.request.urlopen(req_bin, timeout=180.0) as resp:
            raw = resp.read()
    except Exception as exc:
        return False, f"Không tải được gói Google Antigravity CLI {version}: {exc}", None

    if not verify_sha512_hex(raw, expected_sha512):
        return False, f"Xác thực SHA-512 gói Google Antigravity CLI {version} thất bại.", None

    staging_root = home_dir / ".local" / "share" / "nido" / "runtimes"
    staging_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="nido-agy-", dir=str(staging_root)))
    local_bin = home_dir / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    target_bin = local_bin / "agy"
    try:
        if ".tar.gz" in dl_url.lower():
            tgz_path = tmp_dir / "agy.tar.gz"
            tgz_path.write_bytes(raw)
            with tarfile.open(tgz_path, mode="r:gz") as tf:
                base_resolved = tmp_dir.resolve()
                for member in tf.getmembers():
                    m_target = (tmp_dir / member.name).resolve()
                    if not str(m_target).startswith(str(base_resolved) + os.sep) and m_target != base_resolved:
                        raise ValueError(f"Unsafe tar member path: {member.name}")
                if sys.version_info >= (3, 12):
                    tf.extractall(path=tmp_dir, filter="data")
                else:
                    tf.extractall(path=tmp_dir)
            extracted = tmp_dir / "antigravity"
            if not extracted.is_file():
                extracted = tmp_dir / "agy"
            if not extracted.is_file():
                return False, "Gói Antigravity CLI giải nén không chứa tệp thực thi antigravity.", None
        else:
            extracted = tmp_dir / "agy"
            extracted.write_bytes(raw)

        tmp_dest = local_bin / f".agy.tmp.{os.getpid()}"
        shutil.copy2(str(extracted), str(tmp_dest))
        os.chmod(tmp_dest, 0o755)
        os.replace(str(tmp_dest), str(target_bin))
    except Exception as exc:
        return False, f"Lỗi cài đặt Google Antigravity CLI cục bộ: {exc}", None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Non-fatal native shell setup handoff matching official install.sh
    try:
        subprocess.run(
            [str(target_bin), "install"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15.0,
            check=False,
        )
    except Exception:
        pass

    ver_lbl = f" {version}" if version else ""
    return (
        True,
        f"Đã cài Google Antigravity CLI{ver_lbl} (SHA-512 hợp lệ, không cần root).",
        str(target_bin),
    )


def resolve_npm_binary(home_dir: Path) -> str | None:
    candidates = [
        home_dir / ".local" / "bin" / "npm",
        home_dir / ".local" / "share" / "nido" / "runtimes" / "npm" / "bin" / "npm",
    ]
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which("npm")


def verify_sha512_integrity(payload: bytes, expected_b64: str) -> bool:
    actual = base64.b64encode(hashlib.sha512(payload).digest()).decode("ascii")
    return actual == expected_b64.strip()


def bootstrap_user_npm(home_dir: Path) -> tuple[bool, str, str | None]:
    """Download & SHA-512 verify official npm into ~/.local/share/nido/runtimes/npm (zero root, zero apt bloat)."""
    existing = resolve_npm_binary(home_dir)
    if existing:
        return True, f"Đã có sẵn npm tại {existing}.", existing

    node_bin = shutil.which("node")
    if not node_bin:
        return False, "Không tìm thấy `node` (Node.js) trên hệ thống.", None

    runtime_dir = home_dir / ".local" / "share" / "nido" / "runtimes" / "npm"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    try:
        req = urllib.request.Request(
            USER_NPM_TARBALL_URL, headers={"User-Agent": "NidoHub-AgentSetup/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60.0) as resp:
            raw = resp.read()
    except Exception as exc:
        return False, f"Không tải được npm {USER_NPM_VERSION} từ npmjs.org: {exc}", None

    if not verify_sha512_integrity(raw, USER_NPM_SHA512_B64):
        return False, f"Xác thực SHA-512 gói npm {USER_NPM_VERSION} thất bại.", None

    tmp_extract = Path(tempfile.mkdtemp(prefix="nido-npm-", dir=str(runtime_dir.parent)))
    try:
        tgz_path = tmp_extract / f"npm-{USER_NPM_VERSION}.tgz"
        tgz_path.write_bytes(raw)
        with tarfile.open(tgz_path, mode="r:gz") as tf:
            base_resolved = tmp_extract.resolve()
            for member in tf.getmembers():
                target = (tmp_extract / member.name).resolve()
                if not str(target).startswith(str(base_resolved) + os.sep) and target != base_resolved:
                    raise ValueError(f"Unsafe tar member path: {member.name}")
            if sys.version_info >= (3, 12):
                tf.extractall(path=tmp_extract, filter="data")
            else:
                tf.extractall(path=tmp_extract)
        pkg_src = tmp_extract / "package"
        npm_cli = pkg_src / "bin" / "npm-cli.js"
        if not npm_cli.is_file():
            return False, "Gói npm giải nén thiếu bin/npm-cli.js.", None
        pkg_dst = runtime_dir / "package"
        if pkg_dst.exists():
            shutil.rmtree(pkg_dst, ignore_errors=True)
        shutil.move(str(pkg_src), str(pkg_dst))
    except Exception as exc:
        return False, f"Lỗi giải nén npm cục bộ: {exc}", None
    finally:
        shutil.rmtree(tmp_extract, ignore_errors=True)

    npm_cli_final = runtime_dir / "package" / "bin" / "npm-cli.js"
    local_bin_dir = home_dir / ".local" / "bin"
    local_bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = local_bin_dir / "npm"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{node_bin}" "{npm_cli_final}" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    return True, f"Đã cài đặt npm {USER_NPM_VERSION} cục bộ (SHA-512 hợp lệ, không cần root).", str(wrapper)


def plan_managed_install_command(
    provider_id: str,
    home_dir: Path,
    npm_bin: str = "npm",
) -> list[str]:
    spec = MANAGED_AGENT_MAP.get(provider_id)
    if spec is None:
        raise ValueError(f"Unsupported agent provider: {provider_id}")
    prefix_dir = managed_runtime_dir(home_dir, provider_id)
    return [
        npm_bin,
        "install",
        "--prefix",
        str(prefix_dir),
        "--no-audit",
        "--no-fund",
        spec.npm_package,
    ]


def plan_sign_in_command(
    provider_id: str,
    binary_path: str,
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> list[str]:
    if provider_id not in MANAGED_AGENT_MAP:
        raise ValueError(f"Unsupported agent provider: {provider_id}")
    e = env if env is not None else dict(os.environ)
    plat = platform or sys.platform
    has_gui = bool(
        e.get("DISPLAY")
        or e.get("WAYLAND_DISPLAY")
        or (plat == "darwin" and not e.get("SSH_CONNECTION"))
    )
    if provider_id == "codex":
        return [binary_path, "login"] if has_gui else [binary_path, "login", "--device-auth"]
    if provider_id == "claude":
        return [binary_path, "auth", "login", "--claudeai"]
    if provider_id == "agy":
        # Official Antigravity CLI authenticates via interactive launch (`agy` with no arguments).
        return [binary_path]
    return [binary_path]


def install_managed_agent_cli(
    provider_id: str,
    home_dir: Path,
    runner: CommandRunner | None = None,
    ensure_npm_cb: Callable[[], tuple[bool, str]] | None = None,
) -> tuple[bool, str, str | None]:
    spec = MANAGED_AGENT_MAP.get(provider_id)
    if spec is None:
        return False, f"Nhà cung cấp không hợp lệ: {provider_id}", None
    existing = resolve_agent_binary(provider_id, home_dir)
    if existing:
        return True, f"Đã có sẵn {spec.binary_name} tại {existing}", existing

    run = runner or default_command_runner
    npm_bin = "npm"
    if runner is None:
        found_npm = resolve_npm_binary(home_dir)
        if not found_npm:
            if ensure_npm_cb is not None:
                ok_npm, msg_npm = ensure_npm_cb()
                found_npm = resolve_npm_binary(home_dir) if ok_npm else None
                if not ok_npm or not found_npm:
                    return False, f"Không cài được `npm` tự động: {msg_npm}", None
            else:
                ok_npm, msg_npm, found_npm = bootstrap_user_npm(home_dir)
                if not ok_npm or not found_npm:
                    return False, msg_npm, None
        npm_bin = found_npm

    prefix_dir = managed_runtime_dir(home_dir, provider_id)
    prefix_dir.mkdir(parents=True, exist_ok=True)
    argv = plan_managed_install_command(provider_id, home_dir, npm_bin=npm_bin)
    res = run(argv, None, 240.0)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "Lỗi cài đặt npm").strip()
        return False, f"Không tải được {spec.npm_package}: {detail[:240]}", None

    installed_bin = prefix_dir / "node_modules" / ".bin" / spec.binary_name
    local_bin_dir = home_dir / ".local" / "bin"
    try:
        local_bin_dir.mkdir(parents=True, exist_ok=True)
        link_path = local_bin_dir / spec.binary_name
        if installed_bin.exists() and not link_path.exists():
            link_path.symlink_to(installed_bin)
    except OSError:
        pass

    final_bin = str(installed_bin) if installed_bin.exists() else resolve_agent_binary(provider_id, home_dir)
    if not final_bin:
        return False, f"Đã cài {spec.npm_package} nhưng không tìm thấy tệp thực thi {spec.binary_name}.", None
    return True, f"Đã cài đặt {spec.npm_package} vào thư mục người dùng.", final_bin


def probe_provider_status(
    provider_id: str,
    home_dir: Path,
    existing_entry: dict[str, Any] | None = None,
    runner: CommandRunner | None = None,
) -> dict[str, Any]:
    spec = MANAGED_AGENT_MAP[provider_id]
    run = runner or default_command_runner
    binary_path = resolve_agent_binary(provider_id, home_dir)

    installed = binary_path is not None
    signed_in = False
    account_label = ""
    plan = ""
    version = ""
    source = "nidohub"

    if existing_entry and existing_entry.get("source") == "orca" and existing_entry.get("signedIn"):
        signed_in = True
        account_label = str(existing_entry.get("accountLabel", ""))
        plan = str(existing_entry.get("plan", ""))
        source = "orca"

    if binary_path:
        v_res = run([binary_path, "--version"], None, 3.0)
        if v_res.returncode == 0:
            version = v_res.stdout.strip().splitlines()[0][:64] if v_res.stdout.strip() else ""

        if provider_id == "codex":
            l_res = run([binary_path, "login", "status"], None, 4.0)
            combined = f"{l_res.stdout}\n{l_res.stderr}".strip()
            low = combined.lower()
            if l_res.returncode == 0 and ("logged in" in low or "chatgpt" in low):
                signed_in = True
                if not account_label:
                    account_label = combined.splitlines()[0][:80]
                if "chatgpt" in low and not plan:
                    plan = "ChatGPT"
        elif provider_id == "claude":
            l_res = run([binary_path, "auth", "status"], None, 4.0)
            combined = f"{l_res.stdout}\n{l_res.stderr}".strip()
            if l_res.returncode == 0 and combined:
                try:
                    parsed = json.loads(combined)
                    if isinstance(parsed, dict) and parsed.get("loggedIn"):
                        signed_in = True
                        account_label = str(parsed.get("email") or account_label or "Claude")
                        plan = str(parsed.get("subscriptionType") or parsed.get("orgName") or plan)
                except Exception:
                    low = combined.lower()
                    if "logged in" in low and "not logged in" not in low:
                        signed_in = True
                        account_label = account_label or "Claude"
        elif provider_id == "agy":
            # `agy models` exits 0 with tab-separated models when authenticated,
            # or exits non-zero ("Please sign in to view available models") when unauthenticated.
            # Run from an empty/temp cwd with local desktop D-Bus env so it avoids $HOME file-watcher
            # scanning and resolves GNOME Keyring reliably even under SSH/subshells.
            if runner is None:
                agy_ws = home_dir / ".cache" / "nidoagent-agy-ws"
                try:
                    agy_ws.mkdir(parents=True, exist_ok=True)
                    os.chmod(agy_ws, 0o700)
                    probe_cwd = str(agy_ws)
                except OSError:
                    probe_cwd = tempfile.gettempdir()
                l_res = default_command_runner(
                    [binary_path, "models"],
                    "",
                    8.0,
                    env=_build_agy_probe_env(),
                    cwd=probe_cwd,
                )
            else:
                l_res = run([binary_path, "models"], "", 8.0)
            combined = f"{l_res.stdout}\n{l_res.stderr}".strip()
            low = combined.lower()
            model_lines = [
                ln.strip()
                for ln in l_res.stdout.splitlines()
                if "\t" in ln and not ln.strip().lower().startswith("fetching ")
            ]
            if l_res.returncode == 0 and model_lines and "please sign in" not in low:
                signed_in = True
                account_label = f"Google Antigravity ({len(model_lines)} models sẵn sàng)"
                plan = plan or "Google AI (Ultra / Pro / Free)"

    return sanitize_agent_entry(
        provider_id,
        {
            "id": provider_id,
            "installed": installed or bool(existing_entry and existing_entry.get("installed")),
            "signedIn": signed_in,
            "authMethod": spec.auth_method,
            "accountLabel": account_label,
            "plan": plan,
            "binaryPath": binary_path or "",
            "version": version,
            "source": source,
            "updatedAt": _now_iso(),
        },
    )


def probe_all_agents(
    home_dir: Path,
    nidoai_dir: Path,
    persist: bool = True,
    runner: CommandRunner | None = None,
) -> list[dict[str, Any]]:
    existing = read_agents_status(nidoai_dir)
    # Preserve non-managed external entries (e.g. from ORCA) while pruning deprecated gemini-cli personal OAuth
    updates: dict[str, dict[str, Any]] = {
        pid: entry
        for pid, entry in existing.items()
        if pid not in MANAGED_AGENT_MAP
        and not (pid == "gemini" and entry.get("authMethod") == "google-personal-oauth")
    }
    ordered: list[dict[str, Any]] = []
    for spec in MANAGED_AGENT_SPECS:
        entry = probe_provider_status(
            spec.id,
            home_dir=home_dir,
            existing_entry=existing.get(spec.id),
            runner=runner,
        )
        updates[spec.id] = entry
        ordered.append(entry)
    if persist:
        write_agents_status(nidoai_dir, updates, merge=False)
    return ordered


def _launch_in_terminal(
    argv: list[str],
    title: str = "NidoHub — Đăng nhập Agent",
    tall_window: bool = False,
) -> bool:
    ptyxis_cmd = ["ptyxis", "--new-window", "--title", title]
    if tall_window:
        ptyxis_cmd.append("--maximize")
    ptyxis_cmd.extend(["--", *argv])

    gterm_cmd = ["gnome-terminal", "--title", title]
    if tall_window:
        gterm_cmd.append("--geometry=110x42")
    gterm_cmd.extend(["--", *argv])

    for term_cmd in (
        ptyxis_cmd,
        gterm_cmd,
        ["kgx", "--", *argv],
        ["x-terminal-emulator", "-e", *argv],
        ["kitty", "--", *argv],
    ):
        if shutil.which(term_cmd[0]):
            subprocess.Popen(term_cmd, start_new_session=True)
            return True
    return False


def _open_browser_url(url: str) -> bool:
    opener = shutil.which("xdg-open") or shutil.which("open")
    if not opener:
        return False
    try:
        subprocess.Popen([opener, url], start_new_session=True)
        return True
    except OSError:
        return False


def connect_agent_provider(
    provider_id: str,
    home_dir: Path,
    nidoai_dir: Path,
    runner: CommandRunner | None = None,
    ensure_npm_cb: Callable[[], tuple[bool, str]] | None = None,
) -> tuple[bool, str]:
    spec = MANAGED_AGENT_MAP.get(provider_id)
    if spec is None:
        return False, f"Không hỗ trợ nhà cung cấp: {provider_id}"

    if provider_id == "agy":
        existing_bin = resolve_agent_binary(provider_id, home_dir)
        if existing_bin:
            binary_path = existing_bin
        elif runner is None:
            ok_agy, msg_agy, binary_path = install_antigravity_cli(home_dir)
            if not ok_agy or not binary_path:
                return False, msg_agy
        else:
            return False, "Chưa cài agy trong môi trường test."
    elif not spec.npm_package:
        existing_bin = resolve_agent_binary(provider_id, home_dir)
        if not existing_bin:
            if spec.download_url and runner is None:
                _open_browser_url(spec.download_url)
                return (
                    True,
                    f"Đã mở trang chính chủ {spec.download_url} để cài {spec.display_name}. Hoặc lấy GEMINI_API_KEY miễn phí ($0) ở mục 1.",
                )
            return False, f"Chưa cài {spec.binary_name}. Tải tại {spec.download_url or 'trang chính chủ'}."
        binary_path = existing_bin
    else:
        ok_inst, inst_msg, binary_path = install_managed_agent_cli(
            provider_id, home_dir, runner=runner, ensure_npm_cb=ensure_npm_cb
        )
        if not ok_inst or not binary_path:
            return False, inst_msg

    login_argv = plan_sign_in_command(provider_id, binary_path)
    if spec.interactive_terminal_login and runner is None:
        probe_all_agents(home_dir, nidoai_dir, persist=True, runner=runner)
        if _launch_in_terminal(
            login_argv,
            title=f"Đăng nhập {spec.display_name}",
            tall_window=(provider_id == "agy"),
        ):
            if provider_id == "agy":
                return (
                    True,
                    "Đã mở agy: bấm Cho phép trên trình duyệt → copy mã dán vào ô authorization code (Ctrl+Shift+V → Enter), rồi bấm Kiểm tra kết nối.",
                )
            return (
                True,
                f"Đã mở Terminal chạy `{spec.binary_name}` để đăng nhập qua trình duyệt. Hoàn tất xong hãy bấm Kiểm tra kết nối.",
            )
        return (
            False,
            f"Đã cài {spec.binary_name}. Hãy mở Terminal và chạy `{binary_path}` để hoàn tất đăng nhập.",
        )

    run = runner or default_command_runner
    res = run(login_argv, None, 180.0)
    probe_all_agents(home_dir, nidoai_dir, persist=True, runner=runner)
    if res.returncode == 0:
        return True, f"Đã kết nối {spec.display_name} thành công!"
    detail = (res.stderr or res.stdout or "Đăng nhập chưa hoàn tất hoặc đã hủy.").strip()
    return False, f"Đăng nhập {spec.binary_name} chưa hoàn tất: {detail[:220]}"


def disconnect_agent_provider(
    provider_id: str,
    home_dir: Path,
    nidoai_dir: Path,
    runner: CommandRunner | None = None,
) -> tuple[bool, str]:
    spec = MANAGED_AGENT_MAP.get(provider_id)
    if spec is None:
        return False, f"Không hỗ trợ nhà cung cấp: {provider_id}"

    run = runner or default_command_runner
    binary_path = resolve_agent_binary(provider_id, home_dir)
    if provider_id == "agy":
        entry = probe_provider_status(provider_id, home_dir=home_dir, existing_entry=None, runner=runner)
        write_agents_status(nidoai_dir, {provider_id: entry}, merge=True)
        if entry.get("signedIn"):
            return (
                False,
                "Để đăng xuất Google Antigravity khỏi OS Keyring, mở Terminal chạy `agy` rồi gõ `/logout`.",
            )
        return True, f"Đã cập nhật trạng thái ngắt kết nối {spec.display_name}."

    if provider_id == "codex" and binary_path:
        run([binary_path, "logout"], None, 10.0)
    elif provider_id == "claude" and binary_path:
        run([binary_path, "auth", "logout"], None, 10.0)

    entry = probe_provider_status(provider_id, home_dir=home_dir, existing_entry=None, runner=runner)
    entry["signedIn"] = False
    entry["source"] = "nidohub"
    write_agents_status(nidoai_dir, {provider_id: entry}, merge=True)
    return True, f"Đã ngắt kết nối {spec.display_name}."
