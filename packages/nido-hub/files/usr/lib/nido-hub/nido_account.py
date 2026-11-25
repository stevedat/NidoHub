#!/usr/bin/env python3
"""Unified Nido Account (SSO), AI quota inspection, and OS settings sync for NidoOS."""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent_accounts import NIDO_ACCOUNT_SECRET_KEYS, NidoSecretStore

DEFAULT_NIDO_AUTH_URL = "https://ahjpwfyaadpqyldxfjvi.supabase.co"
# Public publishable Supabase anon client key (role=anon, RLS-protected, matches NidoOffice Desktop).
DEFAULT_NIDO_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFoanB3ZnlhYWRwcXlsZHhmanZpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI4MTY3NzYsImV4cCI6MjA4ODM5Mjc3Nn0."
    "QciPYGj8ic8Ltk6UStUNTv1RsHrEhcs4vwWlGBCApl4"
)
DEFAULT_NIDO_AI_PROXY_URL = (
    "https://educenter-ai-proxy.ttd1232004.workers.dev/functions/v1/ai-proxy"
)
DEFAULT_NIDO_MODEL = "nido-llama"

ACCESS_TOKEN_MIN_TTL_SEC = 120
AUTH_TIMEOUT_SEC = 10.0

SAFE_ACCOUNT_PROFILE_KEYS = frozenset(
    {
        "schemaVersion",
        "signedIn",
        "userId",
        "email",
        "fullName",
        "planCode",
        "planName",
        "isPro",
        "source",
        "quota",
        "sync",
        "updatedAt",
    }
)

_SENSITIVE_KEY_RE = re.compile(r"(token|secret|password|apikey|api_key|^key$)", re.IGNORECASE)
_SYNC_ALLOWED_KEYS = frozenset({"vielotus", "nidobox", "aiPolicy"})
HttpJsonRequester = Callable[
    [str, str, dict[str, str] | None, Any | None, float], tuple[int, Any, str]
]


@dataclass(frozen=True)
class NidoAccountProviderConfig:
    auth_url: str
    anon_key: str
    ai_proxy_url: str


TRUSTED_PROVIDER_HOSTS = frozenset(
    {
        "ahjpwfyaadpqyldxfjvi.supabase.co",
        "educenter-ai-proxy.ttd1232004.workers.dev",
    }
)


def _is_trusted_provider_hostname(hostname: str) -> bool:
    h = (hostname or "").lower()
    if h in TRUSTED_PROVIDER_HOSTS:
        return True
    return h in ("nido-holdings.com", "nidoos.vn") or h.endswith(".nido-holdings.com") or h.endswith(".nidoos.vn")


def _is_allowed_endpoint_url(raw_url: str, allow_custom: bool = False) -> bool:
    try:
        parsed = urllib.parse.urlparse(raw_url.strip())
    except Exception:
        return False
    host = parsed.hostname or ""
    if parsed.scheme == "http" and host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return True
    if parsed.scheme == "https":
        if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            return True
        return bool(parsed.netloc) and (allow_custom or _is_trusted_provider_hostname(host))
    return False


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_verified_root_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        st = path.stat()
        if st.st_uid != 0 or (st.st_mode & 0o022) != 0:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_account_provider_config(
    config_dir: Path,
    env: dict[str, str] | None = None,
    system_cfg_path: Path = Path("/etc/ubuntu-nido/account-provider.json"),
) -> NidoAccountProviderConfig:
    """Resolve IdP & AI proxy URLs (env override -> verified root /etc JSON -> Nido defaults)."""
    _ = config_dir
    proc_env = os.environ if env is None else env
    sys_cfg = _read_verified_root_json_dict(system_cfg_path)
    allow_custom = (
        (proc_env.get("NIDO_ALLOW_CUSTOM_PROVIDER") or "").strip().lower() in ("1", "true", "yes")
        or bool(sys_cfg.get("allowCustomProvider"))
    )

    def _pick(env_keys: tuple[str, ...], cfg_key: str, default_val: str) -> str:
        for k in env_keys:
            val = (proc_env.get(k) or "").strip()
            if val:
                return val
        val = sys_cfg.get(cfg_key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        return default_val

    auth_url = _pick(
        ("NIDO_ACCOUNT_AUTH_URL", "NIDO_COMPANION_SUPABASE_URL"),
        "authUrl",
        DEFAULT_NIDO_AUTH_URL,
    ).rstrip("/")
    anon_key = _pick(
        ("NIDO_ACCOUNT_ANON_KEY", "NIDO_COMPANION_SUPABASE_ANON_KEY"),
        "anonKey",
        DEFAULT_NIDO_ANON_KEY,
    )
    ai_proxy_url = _pick(
        ("NIDO_ACCOUNT_AI_PROXY_URL", "NIDO_COMPANION_AI_PROXY_URL"),
        "aiProxyUrl",
        DEFAULT_NIDO_AI_PROXY_URL,
    ).rstrip("/")

    return NidoAccountProviderConfig(
        auth_url=(
            auth_url if _is_allowed_endpoint_url(auth_url, allow_custom) else DEFAULT_NIDO_AUTH_URL
        ),
        anon_key=anon_key,
        ai_proxy_url=(
            ai_proxy_url
            if _is_allowed_endpoint_url(ai_proxy_url, allow_custom)
            else DEFAULT_NIDO_AI_PROXY_URL
        ),
    )


def default_http_json_requester(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    payload: Any | None = None,
    timeout: float = AUTH_TIMEOUT_SEC,
) -> tuple[int, Any, str]:
    """Perform an HTTPS/loopback JSON request returning ``(status, parsed_json, raw_err)``."""
    data_bytes = None
    req_headers = dict(headers or {})
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw), ""
            except ValueError:
                return int(resp.status), None, raw
    except urllib.error.HTTPError as err:
        raw = ""
        parsed = None
        try:
            raw = err.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
        except Exception:
            pass
        return int(err.code), parsed, raw
    except Exception as exc:
        return 0, None, str(exc)


def is_jwt_fresh(token: str, min_remaining_sec: int = ACCESS_TOKEN_MIN_TTL_SEC) -> bool:
    """Return True when ``token`` is a valid JWT expiring more than ``min_remaining_sec`` later."""
    if not token or not isinstance(token, str):
        return False
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return False
    try:
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            return False
        return (float(exp) - datetime.datetime.now(datetime.timezone.utc).timestamp()) > min_remaining_sec
    except Exception:
        return False


def format_nido_plan_name(plan_code: str, is_pro: bool) -> str:
    code = (plan_code or "").strip().lower()
    if is_pro:
        if "enterprise" in code:
            return "Nido Enterprise"
        if "apple" in code or "iap" in code:
            return "Nido AI Pro"
        return "Gói Đồng Hành (Pro)"
    return "Miễn phí (Free)"


def normalize_quota_snapshot(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        raw_q = raw.get("aiCreditsQuota")
        if raw_q is None:
            raw_q = raw.get("limit")
        quota = max(0, int(raw_q or 0))
    except (TypeError, ValueError):
        quota = 0
    raw_rem = raw.get("aiCreditsRemaining")
    if raw_rem is None:
        raw_rem = raw.get("remaining")
    raw_u = raw.get("aiCreditsUsed")
    if raw_u is None:
        raw_u = raw.get("used")
    if raw_u is not None:
        try:
            used = max(0, int(raw_u))
        except (TypeError, ValueError):
            used = 0
    elif raw_rem is not None:
        try:
            used = max(0, quota - max(0, int(raw_rem)))
        except (TypeError, ValueError):
            used = 0
    else:
        used = 0
    if raw_rem is not None:
        try:
            remaining = max(0, int(raw_rem))
        except (TypeError, ValueError):
            remaining = max(0, quota - used)
    else:
        remaining = max(0, quota - used)
    return {
        "aiCreditsQuota": quota,
        "aiCreditsUsed": used,
        "aiCreditsRemaining": remaining,
        "aiCreditsResetAt": str(raw.get("aiCreditsResetAt") or ""),
        "quotaMode": str(raw.get("quotaMode") or "monthly_allowance"),
    }


def sanitize_account_profile(raw: Any) -> dict[str, Any] | None:
    """Validate and strip sensitive fields before reading/writing ``account.json``."""
    if not isinstance(raw, dict):
        return None
    if not raw.get("signedIn") or not (raw.get("email") or raw.get("userId")):
        return None
    is_pro = bool(raw.get("isPro") if "isPro" in raw else raw.get("isCompanionPro", False))
    plan_code = str(raw.get("planCode") or ("companion" if is_pro else "free")).strip()
    plan_name = str(raw.get("planName") or "").strip() or format_nido_plan_name(plan_code, is_pro)
    quota = normalize_quota_snapshot(raw.get("quota"))
    sync_raw = raw.get("sync") if isinstance(raw.get("sync"), dict) else {}
    sync_items = sync_raw.get("items")
    clean: dict[str, Any] = {
        "schemaVersion": 1,
        "signedIn": True,
        "userId": str(raw.get("userId") or "").strip(),
        "email": str(raw.get("email") or "").strip(),
        "fullName": str(raw.get("fullName") or "").strip(),
        "planCode": plan_code,
        "planName": plan_name,
        "isPro": is_pro,
        "source": str(raw.get("source") or "nido").strip() or "nido",
        "sync": {
            "enabled": bool(sync_raw.get("enabled", False)),
            "lastSyncAt": str(sync_raw.get("lastSyncAt") or ""),
            "items": (
                [str(x) for x in sync_items if str(x).strip()]
                if isinstance(sync_items, list) and sync_items
                else ["vielotus", "nidobox", "ai-policy"]
            ),
        },
        "updatedAt": str(raw.get("updatedAt") or "")
        or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if quota is not None:
        clean["quota"] = quota
    return {k: v for k, v in clean.items() if k in SAFE_ACCOUNT_PROFILE_KEYS}


def _atomic_write_0600(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target.parent, 0o700)
    except OSError:
        pass
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _strip_sensitive_dict_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_sensitive_dict_keys(v)
            for k, v in obj.items()
            if isinstance(k, str) and not _SENSITIVE_KEY_RE.search(k)
        }
    if isinstance(obj, list):
        return [_strip_sensitive_dict_keys(item) for item in obj]
    return obj


class NidoAccountManager:
    """Manages unified Nido Account auth tokens, profile metadata, quota, and OS settings sync."""

    def __init__(
        self,
        home_dir: Path,
        nidoai_dir: Path,
        config_dir: Path,
        secret_store: NidoSecretStore | None = None,
        http_requester: HttpJsonRequester | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.home_dir = home_dir
        self.nidoai_dir = nidoai_dir
        self.config_dir = config_dir
        self.secret_store = secret_store or NidoSecretStore(env=env)
        self.http_requester = http_requester or default_http_json_requester
        self.env = env

    @property
    def profile_path(self) -> Path:
        return self.config_dir / "account.json"

    @property
    def env_file(self) -> Path:
        return self.nidoai_dir / ".env.local"

    @property
    def default_sync_bundle_path(self) -> Path:
        return self.config_dir / "sync-bundle.json"

    def provider_config(self) -> NidoAccountProviderConfig:
        return resolve_account_provider_config(self.config_dir, env=self.env)

    def _read_env_file_only(self) -> dict[str, str]:
        if not self.env_file.is_file():
            return {}
        data: dict[str, str] = {}
        for raw in self.env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip("'\"")
        return data

    def _write_env_file(self, env: dict[str, str]) -> None:
        lines = [f"{k}={v}" for k, v in sorted(env.items())]
        _atomic_write_0600(self.env_file, "\n".join(lines) + "\n")

    def _save_tokens(
        self,
        access_token: str,
        refresh_token: str = "",
        set_as_primary: bool = True,
    ) -> None:
        env = self._read_env_file_only()
        keyring_active = self.secret_store.is_keyring_active
        pairs = [("NIDO_ACCOUNT_ACCESS_TOKEN", access_token)]
        if refresh_token:
            pairs.append(("NIDO_ACCOUNT_REFRESH_TOKEN", refresh_token))
        for key, raw_val in pairs:
            trimmed = raw_val.strip()
            if not trimmed:
                continue
            if keyring_active and self.secret_store.set_secret(key, trimmed):
                env.pop(key, None)
            else:
                env[key] = trimmed
        if set_as_primary:
            env["NIDOAI_PRIMARY_BACKEND"] = "nido"
        self._write_env_file(env)

    def _clear_tokens(self) -> None:
        for key in NIDO_ACCOUNT_SECRET_KEYS:
            if self.secret_store.is_keyring_active:
                self.secret_store.clear_secret(key)
        env = self._read_env_file_only()
        changed = False
        for key in NIDO_ACCOUNT_SECRET_KEYS:
            if key in env:
                env.pop(key, None)
                changed = True
        if env.get("NIDOAI_PRIMARY_BACKEND") == "nido":
            env["NIDOAI_PRIMARY_BACKEND"] = "gemini"
            changed = True
        if changed or self.env_file.is_file():
            self._write_env_file(env)

    def _load_tokens(self) -> tuple[str, str]:
        env = self._read_env_file_only()
        access = ""
        refresh = ""
        if self.secret_store.is_keyring_active:
            access = self.secret_store.get_secret("NIDO_ACCOUNT_ACCESS_TOKEN") or ""
            refresh = self.secret_store.get_secret("NIDO_ACCOUNT_REFRESH_TOKEN") or ""
        if not access:
            access = env.get("NIDO_ACCOUNT_ACCESS_TOKEN", "")
        if not refresh:
            refresh = env.get("NIDO_ACCOUNT_REFRESH_TOKEN", "")
        return access.strip(), refresh.strip()

    def read_profile(self) -> dict[str, Any] | None:
        if not self.profile_path.is_file():
            return None
        return sanitize_account_profile(_read_json_dict(self.profile_path))

    def write_profile(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        clean = sanitize_account_profile(raw)
        if not clean:
            return None
        _atomic_write_0600(
            self.profile_path, json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
        )
        return clean

    def clear_profile(self) -> None:
        try:
            if self.profile_path.is_file():
                self.profile_path.unlink()
        except OSError:
            pass

    def _headers(self, cfg: NidoAccountProviderConfig, token: str = "") -> dict[str, str]:
        hdrs: dict[str, str] = {"Accept": "application/json"}
        if cfg.anon_key:
            hdrs["apikey"] = cfg.anon_key
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        return hdrs

    def _fetch_quota(self, cfg: NidoAccountProviderConfig, access_token: str) -> dict[str, Any]:
        status, data, err = self.http_requester(
            "GET",
            f"{cfg.ai_proxy_url}/quota",
            self._headers(cfg, access_token),
            None,
            AUTH_TIMEOUT_SEC,
        )
        if status != 200 or not isinstance(data, dict):
            msg = data.get("error") if isinstance(data, dict) else ""
            return {"ok": False, "status": status, "error": str(msg or err or f"HTTP {status}")}
        is_pro = bool(data.get("isCompanionPro") if "isCompanionPro" in data else data.get("isPro", False))
        plan_code = str(data.get("planCode") or ("companion" if is_pro else "free"))
        return {
            "ok": True,
            "status": status,
            "userId": str(data.get("userId") or ""),
            "planCode": plan_code,
            "planName": format_nido_plan_name(plan_code, is_pro),
            "isPro": is_pro,
            "source": str(data.get("source") or "nido"),
            "quota": normalize_quota_snapshot(data.get("quotaSnapshot")),
        }

    def get_status(self, refresh_quota: bool = False) -> dict[str, Any]:
        """Return sanitized local account status (never containing auth tokens)."""
        profile = self.read_profile()
        access_token, refresh_token = self._load_tokens()
        has_token = bool(access_token or refresh_token)
        if refresh_quota and has_token:
            _, _, refreshed_profile = self.refresh_session(force=False)
            if refreshed_profile:
                profile = refreshed_profile
            access_token, refresh_token = self._load_tokens()
            has_token = bool(access_token or refresh_token)
        signed_in = bool((profile and profile.get("signedIn")) or has_token)
        quota = (profile or {}).get("quota") if profile else None
        is_pro = bool((profile or {}).get("isPro", False))
        remaining = int((quota or {}).get("aiCreditsRemaining") or 0) if quota else 0
        out = dict(profile or {})
        out.update(
            {
                "signedIn": signed_in,
                "hasToken": has_token,
                "tokenFresh": is_jwt_fresh(access_token),
                "isPro": is_pro,
                "canUseAi": bool(signed_in and has_token and is_pro and remaining > 0),
                "planCode": str(out.get("planCode") or "free"),
                "planName": str(out.get("planName") or format_nido_plan_name(str(out.get("planCode") or "free"), is_pro)),
                "email": str(out.get("email") or ""),
                "fullName": str(out.get("fullName") or ""),
                "sync": out.get("sync")
                or {"enabled": False, "lastSyncAt": "", "items": ["vielotus", "nidobox", "ai-policy"]},
            }
        )
        return out

    def _finish_auth_session(
        self,
        cfg: NidoAccountProviderConfig,
        access_token: str,
        refresh_token: str,
        user_obj: dict[str, Any],
        fallback_email: str,
        fallback_name: str = "",
        set_as_primary: bool = True,
    ) -> dict[str, Any]:
        self._save_tokens(access_token, refresh_token, set_as_primary=set_as_primary)
        quota_res = self._fetch_quota(cfg, access_token)
        prev = self.read_profile() or {}
        is_pro = bool(quota_res.get("isPro")) if quota_res.get("ok") else False
        plan_code = str(quota_res.get("planCode") or ("companion" if is_pro else "free"))
        meta = user_obj.get("user_metadata") if isinstance(user_obj.get("user_metadata"), dict) else {}
        full_name = (
            str(meta.get("full_name") or meta.get("display_name") or fallback_name or "").strip()
        )
        raw_profile: dict[str, Any] = {
            "schemaVersion": 1,
            "signedIn": True,
            "userId": str(quota_res.get("userId") or user_obj.get("id") or ""),
            "email": str(user_obj.get("email") or fallback_email).strip(),
            "fullName": full_name,
            "planCode": plan_code,
            "planName": format_nido_plan_name(plan_code, is_pro),
            "isPro": is_pro,
            "source": str(quota_res.get("source") or "nido"),
            "sync": prev.get("sync")
            or {"enabled": False, "lastSyncAt": "", "items": ["vielotus", "nidobox", "ai-policy"]},
            "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if quota_res.get("ok") and quota_res.get("quota") is not None:
            raw_profile["quota"] = quota_res["quota"]
        written = self.write_profile(raw_profile)
        return written or raw_profile

    def login(
        self,
        email: str,
        password: str,
        set_as_primary: bool = True,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        clean_email = email.strip()
        if not clean_email or not password:
            return False, "Vui lòng nhập đầy đủ Email và Mật khẩu.", None
        cfg = self.provider_config()
        status, data, err = self.http_requester(
            "POST",
            f"{cfg.auth_url}/auth/v1/token?grant_type=password",
            self._headers(cfg),
            {"email": clean_email, "password": password},
            AUTH_TIMEOUT_SEC,
        )
        if status != 200 or not isinstance(data, dict) or not data.get("access_token"):
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("error_description") or data.get("msg") or data.get("error") or "")
            return False, msg or err or "Đăng nhập không thành công. Kiểm tra lại Email hoặc Mật khẩu.", None

        user_obj = data.get("user") if isinstance(data.get("user"), dict) else {}
        profile = self._finish_auth_session(
            cfg,
            str(data["access_token"]).strip(),
            str(data.get("refresh_token") or "").strip(),
            user_obj,
            fallback_email=clean_email,
            set_as_primary=set_as_primary,
        )
        plan_note = profile.get("planName", "")
        quota = profile.get("quota") or {}
        if profile.get("isPro") and quota:
            msg = (
                f"Đã đăng nhập {profile.get('email')} ({plan_note} · còn "
                f"{quota.get('aiCreditsRemaining', 0)}/{quota.get('aiCreditsQuota', 0)} lượt AI)."
            )
        else:
            msg = f"Đã đăng nhập {profile.get('email')} ({plan_note})."
        return True, msg, profile

    def register(
        self,
        email: str,
        password: str,
        full_name: str = "",
        set_as_primary: bool = True,
    ) -> tuple[bool, str, dict[str, Any]]:
        clean_email = email.strip()
        clean_name = full_name.strip()
        if not clean_email or not password:
            return False, "Vui lòng nhập đầy đủ Email và Mật khẩu.", {}
        if len(password) < 6:
            return False, "Mật khẩu cần tối thiểu 6 ký tự.", {}

        cfg = self.provider_config()
        payload: dict[str, Any] = {"email": clean_email, "password": password}
        if clean_name:
            payload["data"] = {"full_name": clean_name, "display_name": clean_name}

        status, data, err = self.http_requester(
            "POST",
            f"{cfg.auth_url}/auth/v1/signup",
            self._headers(cfg),
            payload,
            AUTH_TIMEOUT_SEC,
        )
        if status not in (200, 201) or not isinstance(data, dict):
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("error_description") or data.get("msg") or data.get("error") or "")
            return False, msg or err or "Không tạo được tài khoản mới. Vui lòng thử lại.", {}

        access_token = str(data.get("access_token") or "").strip()
        if not access_token:
            return (
                True,
                f"Đã tạo tài khoản {clean_email}. Kiểm tra hộp thư để xác nhận email rồi đăng nhập.",
                {"emailConfirmationRequired": True, "email": clean_email},
            )

        user_obj = data.get("user") if isinstance(data.get("user"), dict) else {}
        profile = self._finish_auth_session(
            cfg,
            access_token,
            str(data.get("refresh_token") or "").strip(),
            user_obj,
            fallback_email=clean_email,
            fallback_name=clean_name,
            set_as_primary=set_as_primary,
        )
        return True, f"Đã tạo và đăng nhập {clean_email}.", {"profile": profile}

    def resend_confirmation(self, email: str) -> tuple[bool, str]:
        clean_email = email.strip()
        if not clean_email:
            return False, "Thiếu địa chỉ Email cần gửi lại xác nhận."
        cfg = self.provider_config()
        status, data, err = self.http_requester(
            "POST",
            f"{cfg.auth_url}/auth/v1/resend",
            self._headers(cfg),
            {"type": "signup", "email": clean_email},
            AUTH_TIMEOUT_SEC,
        )
        if status in (200, 201, 204):
            return True, f"Đã gửi lại email xác nhận tới {clean_email}."
        if status == 429:
            return False, "Bạn vừa yêu cầu gửi lại email. Vui lòng chờ 60 giây rồi thử lại."
        msg = data.get("msg") or data.get("error_description") or data.get("error") if isinstance(data, dict) else ""
        return False, str(msg or err or f"Gửi lại xác nhận thất bại (HTTP {status}).")

    def refresh_session(self, force: bool = False) -> tuple[bool, str, dict[str, Any] | None]:
        access_token, refresh_token = self._load_tokens()
        profile = self.read_profile()
        if not access_token and not refresh_token:
            return False, "Chưa đăng nhập Tài khoản Nido.", None

        cfg = self.provider_config()
        if (force or not is_jwt_fresh(access_token)) and refresh_token:
            st_r, data_r, err_r = self.http_requester(
                "POST",
                f"{cfg.auth_url}/auth/v1/token?grant_type=refresh_token",
                self._headers(cfg),
                {"refresh_token": refresh_token},
                AUTH_TIMEOUT_SEC,
            )
            if st_r in (400, 401, 403):
                self._clear_tokens()
                self.clear_profile()
                return False, "Phiên đăng nhập Tài khoản Nido đã hết hạn. Vui lòng đăng nhập lại.", None
            if st_r == 200 and isinstance(data_r, dict) and data_r.get("access_token"):
                access_token = str(data_r["access_token"]).strip()
                next_refresh = str(data_r.get("refresh_token") or refresh_token).strip()
                self._save_tokens(access_token, next_refresh, set_as_primary=False)
            elif not access_token:
                return False, f"Không làm mới được phiên Nido: {err_r or st_r}", profile

        quota_res = self._fetch_quota(cfg, access_token)
        if not quota_res.get("ok"):
            if quota_res.get("status") == 401 and refresh_token and not force:
                return self.refresh_session(force=True)
            return False, str(quota_res.get("error") or "Không kiểm tra được trạng thái tài khoản."), profile

        prev = profile or {}
        is_pro = bool(quota_res.get("isPro"))
        plan_code = str(quota_res.get("planCode") or prev.get("planCode") or "free")
        updated_raw: dict[str, Any] = {
            **prev,
            "schemaVersion": 1,
            "signedIn": True,
            "userId": str(quota_res.get("userId") or prev.get("userId") or ""),
            "email": str(prev.get("email") or ""),
            "fullName": str(prev.get("fullName") or ""),
            "planCode": plan_code,
            "planName": format_nido_plan_name(plan_code, is_pro),
            "isPro": is_pro,
            "source": str(quota_res.get("source") or prev.get("source") or "nido"),
            "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        if quota_res.get("quota") is not None:
            updated_raw["quota"] = quota_res["quota"]
        written = self.write_profile(updated_raw)
        quota = (written or updated_raw).get("quota") or {}
        msg = (
            f"Đã làm mới Tài khoản Nido ({updated_raw['planName']} · còn "
            f"{quota.get('aiCreditsRemaining', 0)}/{quota.get('aiCreditsQuota', 0)} lượt AI)."
        )
        return True, msg, written or updated_raw

    def logout(self) -> tuple[bool, str]:
        access_token, _ = self._load_tokens()
        if access_token:
            cfg = self.provider_config()
            self.http_requester(
                "POST",
                f"{cfg.auth_url}/auth/v1/logout",
                self._headers(cfg, access_token),
                None,
                4.0,
            )
        self._clear_tokens()
        self.clear_profile()
        return True, "Đã đăng xuất Tài khoản Nido."

    # --- Stage 3: Non-Secret OS Settings Sync (Sen Viet, NidoBox, AI Policy) ---

    def set_sync_enabled(self, enabled: bool) -> dict[str, Any] | None:
        profile = self.read_profile()
        if not profile:
            return None
        sync_obj = dict(profile.get("sync") or {})
        sync_obj["enabled"] = bool(enabled)
        profile["sync"] = sync_obj
        profile["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return self.write_profile(profile)

    def collect_sync_items(self) -> dict[str, Any]:
        """Collect sanitized user preferences (Sen Viet, NidoBox, AI policy; zero secrets)."""
        items: dict[str, Any] = {}

        vl_dir = self.home_dir / ".config" / "vielotus"
        vl_data: dict[str, Any] = {}
        vl_env = vl_dir / "config.env"
        if vl_env.is_file():
            vl_data["configEnv"] = vl_env.read_text(encoding="utf-8", errors="replace")
        vl_macros = vl_dir / "macros.json"
        if vl_macros.is_file():
            try:
                vl_data["macros"] = json.loads(vl_macros.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        if vl_data:
            items["vielotus"] = vl_data

        nb_settings = self.home_dir / ".config" / "nidobox" / "settings.json"
        if nb_settings.is_file():
            try:
                parsed_nb = json.loads(nb_settings.read_text(encoding="utf-8"))
                if isinstance(parsed_nb, dict):
                    items["nidobox"] = {"settings": _strip_sensitive_dict_keys(parsed_nb)}
            except (OSError, ValueError):
                pass

        ai_policy_path = self.config_dir / "ai-policy.json"
        env_vars = self._read_env_file_only()
        ai_policy_raw = _read_json_dict(ai_policy_path)
        items["aiPolicy"] = {
            "aiEnabled": bool(ai_policy_raw.get("aiEnabled", env_vars.get("NIDOAI_ENABLED", "1") != "0")),
            "primaryBackend": str(env_vars.get("NIDOAI_PRIMARY_BACKEND") or "gemini"),
            "localModel": str(env_vars.get("NIDOAI_LOCAL_MODEL") or ""),
        }
        return items

    def export_sync_bundle(self, dest_path: Path | None = None) -> tuple[bool, str, Path]:
        target = dest_path or self.default_sync_bundle_path
        items = _strip_sensitive_dict_keys(self.collect_sync_items())
        canonical_items = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(canonical_items.encode("utf-8")).hexdigest()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        bundle = {
            "schemaVersion": 1,
            "exportedAt": now_iso,
            "sha256": digest,
            "items": items,
        }
        _atomic_write_0600(target, json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")

        profile = self.read_profile()
        if profile:
            sync_obj = dict(profile.get("sync") or {})
            sync_obj["lastSyncAt"] = now_iso
            sync_obj["items"] = sorted(items.keys())
            profile["sync"] = sync_obj
            profile["updatedAt"] = now_iso
            self.write_profile(profile)

        return True, f"Đã sao lưu {len(items)} nhóm cài đặt NidoOS tới {target}.", target

    def import_sync_bundle(self, src_path: Path | None = None) -> tuple[bool, str, list[str]]:
        source = src_path or self.default_sync_bundle_path
        if not source.is_file():
            return False, f"Không tìm thấy tệp sao lưu cài đặt tại {source}.", []
        try:
            bundle = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return False, f"Tệp sao lưu không hợp lệ: {exc}", []
        if not isinstance(bundle, dict) or bundle.get("schemaVersion") != 1:
            return False, "Định dạng tệp sao lưu cài đặt NidoOS không được hỗ trợ.", []
        raw_items = bundle.get("items")
        if not isinstance(raw_items, dict):
            return False, "Tệp sao lưu thiếu mục cài đặt hợp lệ.", []

        expected_sha = str(bundle.get("sha256") or "").strip().lower()
        if expected_sha:
            canonical = json.dumps(raw_items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            actual_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if actual_sha != expected_sha:
                return False, "Mã kiểm tra toàn vẹn tệp sao lưu (SHA256) không khớp.", []

        items = _strip_sensitive_dict_keys(
            {k: v for k, v in raw_items.items() if k in _SYNC_ALLOWED_KEYS}
        )
        restored: list[str] = []

        if isinstance(items.get("vielotus"), dict):
            vl = items["vielotus"]
            vl_dir = self.home_dir / ".config" / "vielotus"
            if isinstance(vl.get("configEnv"), str) and vl["configEnv"].strip():
                _atomic_write_0600(vl_dir / "config.env", vl["configEnv"].rstrip() + "\n")
                restored.append("vielotus:config.env")
            if isinstance(vl.get("macros"), dict):
                _atomic_write_0600(
                    vl_dir / "macros.json",
                    json.dumps(vl["macros"], indent=2, ensure_ascii=False) + "\n",
                )
                restored.append("vielotus:macros.json")

        if isinstance(items.get("nidobox"), dict) and isinstance(items["nidobox"].get("settings"), dict):
            nb_path = self.home_dir / ".config" / "nidobox" / "settings.json"
            existing_nb = _read_json_dict(nb_path)
            merged_nb = {**existing_nb, **items["nidobox"]["settings"]}
            _atomic_write_0600(nb_path, json.dumps(merged_nb, indent=2, ensure_ascii=False) + "\n")
            restored.append("nidobox:settings.json")

        if isinstance(items.get("aiPolicy"), dict):
            ap = items["aiPolicy"]
            env = self._read_env_file_only()
            if "aiEnabled" in ap:
                env["NIDOAI_ENABLED"] = "1" if ap["aiEnabled"] else "0"
            pb = str(ap.get("primaryBackend") or "").strip()
            if pb in {"nido", "gemini", "openai", "anthropic", "local"}:
                env["NIDOAI_PRIMARY_BACKEND"] = pb
            lm = str(ap.get("localModel") or "").strip()
            if lm:
                env["NIDOAI_LOCAL_MODEL"] = lm
            self._write_env_file(env)
            restored.append("ai-policy")

        if not restored:
            return False, "Tệp sao lưu không chứa mục cài đặt nào có thể khôi phục.", []

        return True, f"Đã khôi phục {len(restored)} mục cài đặt ({', '.join(restored)}).", restored
