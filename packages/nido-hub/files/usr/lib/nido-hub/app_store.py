#!/usr/bin/env python3
"""Native-First Software Management backend for NidoHub.

Supports 4 official sources in strict preference order:
1. nido-apt   (Ubuntu-Nido APT .deb packages)
2. vendor-apt (Verified official upstream vendor APT .deb repositories)
3. ubuntu-apt (Official upstream Ubuntu APT .deb repositories)
4. flatpak    (Optional user-scoped Flathub applications)

Features:
- Dynamic host & repository architecture detection (never assumes static arch limits).
- Narrowly scoped privilege elevation via Polkit (`/usr/libexec/nido-hub/nido-pkg-helper`).
- Real-time progress callbacks, safe cancellation gating (disabled during dpkg commits),
  and 1-click APT/dpkg error recovery.
- Independent read-only health inspection for Ubuntu security updates & fwupd firmware.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SOURCE_PRIORITY = {
    "nido-apt": 0,
    "vendor-apt": 1,
    "ubuntu-apt": 2,
    "flatpak": 3,
}

SOURCE_LABELS = {
    "nido-apt": "NidoOS",
    "vendor-apt": "Chính hãng",
    "ubuntu-apt": "Hệ thống",
    "flatpak": "Flathub",
}

CATEGORY_LABELS = {
    "all": "Tất cả",
    "nido": "Hệ sinh thái Nido",
    "browser": "Trình duyệt",
    "dev": "Lập trình",
    "media": "Đồ họa & Media",
    "social": "Giao tiếp",
    "system": "Hệ thống",
}

PKG_HELPER_DEFAULT = Path("/usr/libexec/nido-hub/nido-pkg-helper")
VENDORS_DEFAULT = Path("/usr/share/nido-hub/vendors.json")
FLATHUB_API_BASE = "https://flathub.org/api/v2"
FLATHUB_REPO_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"

PROGRESS_LINE_RE = re.compile(r"^NIDO_PROGRESS:([^:]+):(\d+):([01]):(.*)$")
FLATPAK_PCT_RE = re.compile(r"(\d{1,3})%")

ProgressCallback = Callable[[int, str, bool], None]
UrlFetcher = Callable[[str, float], bytes]
JsonPoster = Callable[[str, dict[str, Any], float], dict[str, Any]]


def default_url_fetcher(url: str, timeout: float = 6.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "NidoHub-AppStore/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def default_json_poster(url: str, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "NidoHub-AppStore/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}


def normalize_arch_name(arch: str) -> str:
    val = str(arch or "").strip().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "arm": "armhf",
        "armhf": "armhf",
        "i386": "i386",
        "i686": "i386",
        "all": "all",
        "any": "all",
        "noarch": "all",
    }
    return mapping.get(val, val)


def is_declared_arch_supported(declared_arches: Any, host_arch: str) -> bool | None:
    """Evaluate a declared architecture list (Debian or Flatpak names) against ``host_arch``."""
    if not isinstance(declared_arches, (list, tuple, set)) or not declared_arches:
        return None
    norm_host = normalize_arch_name(host_arch)
    norm_set = {normalize_arch_name(str(a)) for a in declared_arches if str(a).strip()}
    if not norm_set:
        return None
    return "all" in norm_set or norm_host in norm_set


def detect_host_arch() -> str:
    env_arch = os.environ.get("NIDO_HOST_ARCH", "").strip()
    if env_arch:
        return env_arch
    dpkg = shutil.which("dpkg")
    if dpkg:
        res = subprocess.run([dpkg, "--print-architecture"], capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    machine = os.uname().machine.lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    return machine


def debian_arch_to_flatpak(deb_arch: str) -> str:
    mapping = {
        "amd64": "x86_64",
        "arm64": "aarch64",
        "armhf": "arm",
        "i386": "i386",
    }
    return mapping.get(deb_arch, deb_arch)


def detect_ubuntu_codename() -> str:
    env_code = os.environ.get("NIDO_UBUNTU_CODENAME", "").strip()
    if env_code:
        return env_code
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        if path.is_file():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("UBUNTU_CODENAME=") or line.startswith("VERSION_CODENAME="):
                    val = line.split("=", 1)[1].strip().strip("\"'")
                    if val:
                        return val
    return "resolute"


def parse_release_architectures(release_text: str) -> set[str]:
    for line in release_text.splitlines():
        if line.startswith("Architectures:"):
            return {tok.strip() for tok in line.split(":", 1)[1].split() if tok.strip()}
    return set()


@dataclass
class OperationHandle:
    """Tracks a running install/remove/update subprocess for safe cancellation."""

    label: str
    _lock: threading.Lock = None  # type: ignore[assignment]
    _proc: subprocess.Popen[str] | None = None
    _safe_to_cancel: bool = True
    _cancelled: bool = False

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def attach(self, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._proc = proc

    def set_safe_to_cancel(self, safe: bool) -> None:
        with self._lock:
            self._safe_to_cancel = safe

    @property
    def safe_to_cancel(self) -> bool:
        with self._lock:
            return self._safe_to_cancel

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> tuple[bool, str]:
        with self._lock:
            if not self._safe_to_cancel:
                return (
                    False,
                    "Đang ghi gói hệ thống (dpkg commit) — không thể hủy giữa chừng để bảo toàn hệ điều hành.",
                )
            self._cancelled = True
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
        return True, "Đã gửi tín hiệu hủy thao tác an toàn."


class NidoAppStore:
    """Native-First package catalog, dynamic arch checker, and scoped executor."""

    def __init__(
        self,
        catalog_path: Path,
        vendors_path: Path | None = None,
        pkg_helper_path: Path | None = None,
        host_arch: str | None = None,
        url_fetcher: UrlFetcher | None = None,
        json_poster: JsonPoster | None = None,
    ) -> None:
        self.catalog_path = catalog_path
        self.vendors_path = vendors_path or Path(
            os.environ.get("NIDOHUB_VENDORS", str(VENDORS_DEFAULT))
        )
        self.pkg_helper_path = pkg_helper_path or Path(
            os.environ.get("NIDOHUB_PKG_HELPER", str(PKG_HELPER_DEFAULT))
        )
        self.host_arch = normalize_arch_name(host_arch or detect_host_arch())
        self.flatpak_arch = debian_arch_to_flatpak(self.host_arch)
        self.codename = detect_ubuntu_codename()
        self.url_fetcher = url_fetcher or default_url_fetcher
        self.json_poster = json_poster or default_json_poster
        self._arch_cache: dict[str, tuple[float, bool | None, str]] = {}
        self._cache_ttl_sec = 300.0

    def load_vendors(self) -> dict[str, dict[str, Any]]:
        if not self.vendors_path.is_file():
            return {}
        try:
            raw = json.loads(self.vendors_path.read_text(encoding="utf-8"))
            vendors = raw.get("vendors", {})
            return vendors if isinstance(vendors, dict) else {}
        except Exception:
            return {}

    def is_deb_installed(self, pkg_name: str) -> bool:
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

    def is_flatpak_installed(self, app_id: str) -> bool:
        fp = shutil.which("flatpak")
        if not fp:
            return False
        res = subprocess.run(
            [fp, "info", app_id],
            check=False,
            capture_output=True,
            text=True,
        )
        return res.returncode == 0

    def probe_apt_candidate_available(self, pkg_name: str) -> bool | None:
        apt_cache = shutil.which("apt-cache")
        if not apt_cache:
            return None
        res = subprocess.run(
            [apt_cache, "policy", pkg_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return False
        for line in res.stdout.splitlines():
            s = line.strip()
            if s.startswith("Candidate:"):
                cand = s.split(":", 1)[1].strip()
                return bool(cand and cand != "(none)")
        return False

    def probe_vendor_arch_support(self, vendor_id: str) -> tuple[bool | None, str]:
        """Dynamically check host architecture support from the vendor's live Release index."""
        cache_key = f"vendor:{vendor_id}:{self.host_arch}"
        now = time.monotonic()
        if cache_key in self._arch_cache:
            ts, ok_cached, note = self._arch_cache[cache_key]
            if now - ts < self._cache_ttl_sec:
                return ok_cached, note

        vendors = self.load_vendors()
        vendor = vendors.get(vendor_id)
        if not vendor:
            return False, f"Không tìm thấy định nghĩa kho hãng '{vendor_id}'"

        uri = str(vendor["uri"]).rstrip("/")
        suites = [str(s).replace("{codename}", self.codename) for s in vendor.get("suites", [])]
        suite = suites[0] if suites else "stable"
        release_url = f"{uri}/dists/{suite}/Release"
        try:
            raw = self.url_fetcher(release_url, 6.0).decode("utf-8", errors="replace")
            arches = parse_release_architectures(raw)
            if not arches:
                res = (None, "Chưa xác định được danh sách kiến trúc từ Release")
            elif self.host_arch in arches or "all" in arches:
                res = (True, f"Hỗ trợ native {self.host_arch} (xác minh từ Release)")
            else:
                res = (
                    False,
                    f"Kho {vendor['name']} hiện hỗ trợ: {', '.join(sorted(arches))} (không có {self.host_arch})",
                )
        except Exception:
            # Offline or unreachable: never falsely block an architecture
            res = (None, "Chưa kiểm tra trực tuyến (sẽ kiểm tra lại khi cài)")

        self._arch_cache[cache_key] = (now, res[0], res[1])
        return res

    def probe_flathub_arch_support(self, app_id: str) -> tuple[bool | None, str]:
        """Dynamically check Flathub API v2 summary arches for host Flatpak arch."""
        cache_key = f"flatpak:{app_id}:{self.flatpak_arch}"
        now = time.monotonic()
        if cache_key in self._arch_cache:
            ts, ok_cached, note = self._arch_cache[cache_key]
            if now - ts < self._cache_ttl_sec:
                return ok_cached, note

        url = f"{FLATHUB_API_BASE}/summary/{urllib.parse.quote(app_id, safe='')}"
        try:
            raw = self.url_fetcher(url, 6.0).decode("utf-8", errors="replace")
            data = json.loads(raw)
            arches = data.get("arches")
            if isinstance(arches, list) and arches:
                if self.flatpak_arch in arches:
                    dl_mb = round(int(data.get("download_size", 0)) / (1024 * 1024))
                    size_note = f" (~{dl_mb} MB)" if dl_mb > 0 else ""
                    res = (True, f"Hỗ trợ {self.flatpak_arch} trên Flathub{size_note}")
                else:
                    res = (
                        False,
                        f"Flathub hiện chỉ có bản {', '.join(str(a) for a in arches)} (chưa có {self.flatpak_arch})",
                    )
            else:
                res = (None, "Chưa rõ kiến trúc Flathub")
        except Exception:
            res = (None, "Chưa kiểm tra trực tuyến Flathub")

        self._arch_cache[cache_key] = (now, res[0], res[1])
        return res

    def _cached_arch_support(self, item: dict[str, Any]) -> tuple[bool | None, str] | None:
        now = time.monotonic()
        source = item.get("source", "nido-apt")
        cache_key = ""
        if source == "vendor-apt" and item.get("vendorId"):
            cache_key = f"vendor:{item['vendorId']}:{self.host_arch}"
        elif source == "flatpak" and item.get("flatpakId"):
            cache_key = f"flatpak:{item['flatpakId']}:{self.flatpak_arch}"
        if cache_key and cache_key in self._arch_cache:
            ts, ok_cached, note = self._arch_cache[cache_key]
            if now - ts < self._cache_ttl_sec and ok_cached is not None:
                return ok_cached, note
        return None

    def check_item_arch_support(self, item: dict[str, Any]) -> tuple[bool | None, str]:
        source = item.get("source", "nido-apt")
        live_ok: bool | None = None
        live_note = f"Kiến trúc hệ thống: {self.host_arch}"
        if source == "vendor-apt" and item.get("vendorId"):
            live_ok, live_note = self.probe_vendor_arch_support(str(item["vendorId"]))
        elif source == "flatpak" and item.get("flatpakId"):
            live_ok, live_note = self.probe_flathub_arch_support(str(item["flatpakId"]))
        elif source in ("ubuntu-apt", "nido-apt"):
            pkgs = item.get("packages", [])
            if pkgs:
                avail = self.probe_apt_candidate_available(str(pkgs[0]))
                if avail is True:
                    live_ok, live_note = True, f"Có sẵn gói .deb native cho {self.host_arch}"
                elif avail is False:
                    live_ok, live_note = False, f"Không tìm thấy gói .deb trên APT cho {self.host_arch}"

        if live_ok is not None:
            return live_ok, live_note

        declared_ok = is_declared_arch_supported(item.get("arches"), self.host_arch)
        if declared_ok is False:
            return False, f"Không hỗ trợ kiến trúc {self.host_arch}"
        return live_ok, live_note

    def load_catalog_entries(
        self,
        include_arch_probe: bool = False,
        hide_unsupported: bool = True,
    ) -> list[dict[str, Any]]:
        if not self.catalog_path.is_file():
            return []
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        combined_raw: list[dict[str, Any]] = []
        for ed in raw.get("editions", []):
            entry = dict(ed)
            entry.setdefault("source", "nido-apt")
            entry.setdefault("category", "nido")
            combined_raw.append(entry)
        for app in raw.get("apps", []):
            entry = dict(app)
            entry.setdefault("source", "ubuntu-apt")
            entry.setdefault("category", "system")
            combined_raw.append(entry)

        items: list[dict[str, Any]] = []
        for entry in combined_raw:
            source = str(entry.get("source", "nido-apt"))
            pkgs = [str(p) for p in entry.get("packages", []) if p]
            flatpak_id = str(entry.get("flatpakId", "")).strip()
            vendor_id = str(entry.get("vendorId", "")).strip()
            declared_arches = entry.get("arches") if isinstance(entry.get("arches"), list) else []

            if source == "flatpak" and flatpak_id:
                installed = self.is_flatpak_installed(flatpak_id)
            else:
                installed = bool(pkgs) and all(self.is_deb_installed(p) for p in pkgs)

            arch_supported: bool | None = None
            arch_note = ""
            if include_arch_probe:
                arch_supported, arch_note = self.check_item_arch_support(entry)
            else:
                cached = self._cached_arch_support(entry)
                if cached is not None:
                    arch_supported, arch_note = cached
                else:
                    declared_ok = is_declared_arch_supported(declared_arches, self.host_arch)
                    if declared_ok is False:
                        arch_supported = False
                        arch_note = f"Không hỗ trợ kiến trúc {self.host_arch}"

            if hide_unsupported and arch_supported is False and not installed:
                continue

            items.append(
                {
                    "id": str(entry.get("id", "")),
                    "name": str(entry.get("name", "")),
                    "description": str(entry.get("description", "")),
                    "category": str(entry.get("category", "system")),
                    "source": source,
                    "sourceLabel": SOURCE_LABELS.get(source, source),
                    "vendorId": vendor_id,
                    "packages": pkgs,
                    "flatpakId": flatpak_id,
                    "arches": list(declared_arches),
                    "protected": bool(entry.get("protected", source == "nido-apt")),
                    "installed": installed,
                    "archSupported": arch_supported,
                    "archNote": arch_note,
                }
            )

        items.sort(key=lambda it: (SOURCE_PRIORITY.get(it["source"], 99), it["name"].lower()))
        return items

    def search_packages_native_first(
        self,
        query: str,
        include_flathub: bool = True,
        limit: int = 12,
        hide_unsupported: bool = True,
    ) -> list[dict[str, Any]]:
        """Search local catalog, Ubuntu APT (preferred native DEB), then optional Flathub API v2."""
        q = query.strip()
        if not q:
            return self.load_catalog_entries(
                include_arch_probe=False,
                hide_unsupported=hide_unsupported,
            )

        q_low = q.lower()
        results: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        curated_ids: set[str] = set()

        # 1. Matching curated entries first (preserving Native-First priority & arch filter)
        for item in self.load_catalog_entries(
            include_arch_probe=False,
            hide_unsupported=hide_unsupported,
        ):
            hay = f"{item['name']} {item['description']} {' '.join(item['packages'])} {item['flatpakId']}".lower()
            if q_low in hay:
                results.append(item)
                curated_ids.add(item["id"])
                for p in item["packages"]:
                    seen_keys.add(f"apt:{p}")
                if item["flatpakId"]:
                    seen_keys.add(f"flatpak:{item['flatpakId']}")

        # 2. Ubuntu APT native DEB search (filter internal libraries unless explicitly requested)
        allow_lib_search = q_low.startswith(("lib", "python", "node-", "php-", "ruby-", "golang-", "perl"))
        apt_cache = shutil.which("apt-cache")
        if apt_cache and re.match(r"^[a-zA-Z0-9.+_ -]{2,64}$", q):
            res = subprocess.run(
                [apt_cache, "search", "--names-only", q],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0:
                added_apt = 0
                for line in res.stdout.splitlines():
                    if " - " not in line:
                        continue
                    pkg_name, summary = line.split(" - ", 1)
                    pkg_name = pkg_name.strip()
                    if not pkg_name or f"apt:{pkg_name}" in seen_keys:
                        continue
                    if not allow_lib_search and (
                        pkg_name.startswith(
                            (
                                "lib",
                                "python3-",
                                "node-",
                                "php-",
                                "ruby-",
                                "golang-",
                                "gir1.2-",
                                "qml-",
                                "qml6-",
                                "gem-plugin-",
                            )
                        )
                        or pkg_name.endswith(
                            ("-dev", "-doc", "-dbg", "-dbgsym", "-common", "-perl", "-data", "-l10n")
                        )
                    ):
                        continue
                    seen_keys.add(f"apt:{pkg_name}")
                    results.append(
                        {
                            "id": f"apt-{pkg_name}",
                            "name": pkg_name,
                            "description": summary.strip(),
                            "category": "system",
                            "source": "ubuntu-apt",
                            "sourceLabel": SOURCE_LABELS["ubuntu-apt"],
                            "vendorId": "",
                            "packages": [pkg_name],
                            "flatpakId": "",
                            "protected": False,
                            "installed": self.is_deb_installed(pkg_name),
                            "archSupported": True,
                            "archNote": f"Có sẵn gói .deb native ({self.host_arch})",
                        }
                    )
                    added_apt += 1
                    if added_apt >= limit:
                        break

        # 3. Optional Flathub API v2 live search (ranked strictly after native DEB)
        if include_flathub and len(q) >= 2:
            try:
                sdata = self.json_poster(
                    f"{FLATHUB_API_BASE}/search",
                    {"query": q, "hits_per_page": min(max(limit * 2, 12), 20)},
                    5.0,
                )
                added_flathub = 0
                flathub_cap = min(limit, 8)
                for hit in sdata.get("hits", []):
                    app_id = str(hit.get("app_id", "")).strip()
                    if not app_id or f"flatpak:{app_id}" in seen_keys:
                        continue
                    seen_keys.add(f"flatpak:{app_id}")
                    is_installed = self.is_flatpak_installed(app_id)
                    arches = hit.get("arches")
                    arch_ok: bool | None = None
                    arch_note = ""
                    if isinstance(arches, list) and arches:
                        arch_ok = is_declared_arch_supported(arches, self.host_arch)
                        if hide_unsupported and arch_ok is False and not is_installed:
                            continue
                        arch_note = (
                            f"Hỗ trợ {self.flatpak_arch} trên Flathub"
                            if arch_ok
                            else f"Flathub chỉ hỗ trợ {', '.join(str(a) for a in arches)}"
                        )
                    results.append(
                        {
                            "id": f"flatpak-{app_id}",
                            "name": str(hit.get("name") or app_id),
                            "description": str(hit.get("summary") or "Ứng dụng từ Flathub"),
                            "category": "social",
                            "source": "flatpak",
                            "sourceLabel": SOURCE_LABELS["flatpak"],
                            "vendorId": "",
                            "packages": [],
                            "flatpakId": app_id,
                            "arches": list(arches) if isinstance(arches, list) else [],
                            "protected": False,
                            "installed": is_installed,
                            "archSupported": arch_ok,
                            "archNote": arch_note,
                        }
                    )
                    added_flathub += 1
                    if added_flathub >= flathub_cap:
                        break
            except Exception:
                pass

        def _rank(it: dict[str, Any]) -> tuple[int, int, int, str]:
            is_curated = it["id"] in curated_ids
            is_native = it["source"] != "flatpak"
            name_low = it["name"].lower()
            exact_hit = 0 if (name_low == q_low or q_low in it["packages"] or name_low.startswith(q_low)) else 1
            if is_curated and is_native:
                tier = 0
            elif is_native and exact_hit == 0:
                tier = 1
            elif is_curated:
                tier = 2
            elif is_native:
                tier = 3
            else:
                tier = 4
            return (tier, SOURCE_PRIORITY.get(it["source"], 99), exact_hit, name_low)

        results.sort(key=_rank)
        return results

    def build_privileged_command(self, helper_args: list[str]) -> list[str]:
        helper = str(self.pkg_helper_path)
        if os.geteuid() == 0:
            return [helper, *helper_args]
        pkexec = shutil.which("pkexec")
        if not pkexec:
            raise RuntimeError("Cần Polkit (pkexec) để thực hiện thao tác quản trị gói.")
        return [pkexec, helper, *helper_args]

    def _run_streaming_helper(
        self,
        helper_args: list[str],
        handle: OperationHandle,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        """Run nido-pkg-helper with structured progress. Returns (ok, message, needs_apt_repair)."""
        try:
            cmd = self.build_privileged_command(helper_args)
        except Exception as exc:
            return False, str(exc), False

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        handle.attach(proc)
        assert proc.stdout is not None
        last_msg = ""
        error_lines: list[str] = []

        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            m = PROGRESS_LINE_RE.match(line)
            if m:
                pct = int(m.group(2))
                safe = m.group(3) == "1"
                msg = m.group(4).strip()
                last_msg = msg
                handle.set_safe_to_cancel(safe)
                if on_progress:
                    on_progress(pct, msg, safe)
            else:
                error_lines.append(line)
                if on_progress:
                    on_progress(50, line[:140], handle.safe_to_cancel)

        rc = proc.wait()
        if handle.cancelled:
            return False, "Đã hủy thao tác cài đặt/cập nhật.", False
        if rc == 0:
            return True, last_msg or "Hoàn tất thành công.", False

        joined_err = " | ".join(error_lines[-3:]) if error_lines else last_msg
        low = joined_err.lower()
        needs_repair = any(
            k in low for k in ("dpkg was interrupted", "unmet dependencies", "dpkg --configure", "broken packages")
        )
        if rc in (126, 127) and "dismissed" in low:
            return False, "Đã hủy xác thực quản trị Polkit.", False
        if "textual authentication agent" in low or "no polkit authentication agent" in low:
            return (
                False,
                "Không tìm thấy hộp thoại xác thực Polkit trong phiên hiện tại (hãy mở NidoHub từ biểu tượng trên Dock/Menu ứng dụng).",
                False,
            )
        return False, joined_err or f"Thao tác gói thất bại (mã lỗi {rc}).", needs_repair

    def install_item(
        self,
        item: dict[str, Any],
        handle: OperationHandle | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        op = handle or OperationHandle(label=f"Cài đặt {item.get('name', '')}")
        source = item.get("source", "nido-apt")

        if source == "vendor-apt":
            vendor_id = str(item.get("vendorId", "")).strip()
            pkgs = [str(p) for p in item.get("packages", []) if p]
            if not vendor_id or not pkgs:
                return False, "Thiếu thông tin kho hãng hoặc tên gói.", False
            ok_arch, arch_note = self.probe_vendor_arch_support(vendor_id)
            if ok_arch is False:
                return False, arch_note, False
            return self._run_streaming_helper(
                ["install-vendor", vendor_id, *pkgs], op, on_progress=on_progress
            )

        if source in ("nido-apt", "ubuntu-apt"):
            pkgs = [str(p) for p in item.get("packages", []) if p]
            if not pkgs:
                return False, "Danh sách gói trống.", False
            return self._run_streaming_helper(
                ["install-apt", *pkgs], op, on_progress=on_progress
            )

        if source == "flatpak":
            app_id = str(item.get("flatpakId", "")).strip()
            if not app_id or not re.match(r"^[A-Za-z0-9_.-]+$", app_id):
                return False, f"Mã ứng dụng Flatpak không hợp lệ: {app_id!r}", False
            ok_arch, arch_note = self.probe_flathub_arch_support(app_id)
            if ok_arch is False:
                return False, arch_note, False
            return self._install_flatpak_user(app_id, op, on_progress=on_progress)

        return False, f"Nguồn cài đặt không hỗ trợ: {source}", False

    def remove_item(
        self,
        item: dict[str, Any],
        handle: OperationHandle | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        op = handle or OperationHandle(label=f"Gỡ {item.get('name', '')}")
        if item.get("protected"):
            return False, "Không thể gỡ thành phần cốt lõi của hệ thống.", False

        source = item.get("source", "nido-apt")
        if source == "flatpak":
            app_id = str(item.get("flatpakId", "")).strip()
            fp = shutil.which("flatpak")
            if not fp:
                return False, "Chưa cài đặt Flatpak.", False
            op.set_safe_to_cancel(False)
            if on_progress:
                on_progress(35, f"Đang gỡ ứng dụng Flatpak {app_id}...", False)
            res = subprocess.run(
                [fp, "uninstall", "--user", "-y", "--noninteractive", app_id],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                if on_progress:
                    on_progress(100, f"Đã gỡ {app_id}.", False)
                return True, f"Đã gỡ thành công {item.get('name', app_id)}.", False
            return False, (res.stderr or res.stdout or "Lỗi gỡ Flatpak").strip(), False

        pkgs = [str(p) for p in item.get("packages", []) if p]
        return self._run_streaming_helper(["remove-apt", *pkgs], op, on_progress=on_progress)

    def _install_flatpak_user(
        self,
        app_id: str,
        op: OperationHandle,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        fp = shutil.which("flatpak")
        if not fp:
            if on_progress:
                on_progress(5, "Cần cài nền tảng Flatpak lần đầu (chỉ ~2 MB, không kèm app rác)...", True)
            ok_fp, msg_fp, needs_rep = self._run_streaming_helper(
                ["ensure-flathub"], op, on_progress=on_progress
            )
            if not ok_fp:
                return False, msg_fp, needs_rep
            fp = shutil.which("flatpak")
            if not fp:
                return False, "Không tìm thấy lệnh flatpak sau khi cài đặt.", False

        # Register user-scoped Flathub remote without needing root
        subprocess.run(
            [fp, "remote-add", "--user", "--if-not-exists", "flathub", FLATHUB_REPO_URL],
            capture_output=True,
            text=True,
            check=False,
        )

        if on_progress:
            on_progress(15, f"Đang tải và cài đặt {app_id} từ Flathub (--user)...", True)
        proc = subprocess.Popen(
            [fp, "install", "--user", "-y", "--noninteractive", "flathub", app_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        op.attach(proc)
        assert proc.stdout is not None
        last_lines: list[str] = []
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            last_lines.append(line)
            pct_match = FLATPAK_PCT_RE.search(line)
            pct = int(pct_match.group(1)) if pct_match else 50
            if on_progress:
                on_progress(min(98, max(15, pct)), line[:150], True)

        rc = proc.wait()
        if op.cancelled:
            return False, "Đã hủy tải ứng dụng Flathub.", False
        if rc == 0:
            if on_progress:
                on_progress(100, f"Đã cài đặt {app_id} từ Flathub.", False)
            return True, f"Đã cài đặt thành công {app_id} (Flathub).", False
        return False, (" | ".join(last_lines[-2:]) or f"Lỗi cài Flatpak (mã {rc})"), False

    def upgrade_system(
        self,
        handle: OperationHandle | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        op = handle or OperationHandle(label="Cập nhật hệ thống")
        ok, msg, needs_rep = self._run_streaming_helper(["upgrade-apt"], op, on_progress=on_progress)
        if not ok:
            return False, msg, needs_rep

        fp = shutil.which("flatpak")
        if fp:
            if on_progress:
                on_progress(92, "Đang kiểm tra cập nhật ứng dụng Flatpak...", True)
            subprocess.run(
                [fp, "update", "--user", "-y", "--noninteractive"],
                capture_output=True,
                text=True,
                check=False,
            )
        if on_progress:
            on_progress(100, "Hệ thống đã được cập nhật mới nhất.", False)
        return True, "Đã cập nhật xong các gói APT và Flatpak.", False

    def repair_apt(
        self,
        handle: OperationHandle | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        op = handle or OperationHandle(label="Khôi phục APT")
        return self._run_streaming_helper(["repair-apt"], op, on_progress=on_progress)

    def inspect_local_deb(self, deb_path: Path) -> dict[str, Any]:
        """Read Debian package metadata without root privileges."""
        resolved = deb_path.expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() != ".deb":
            raise ValueError(f"Không tìm thấy tệp .deb hợp lệ: {resolved}")
        dpkg_deb = shutil.which("dpkg-deb")
        if not dpkg_deb:
            return {
                "path": str(resolved),
                "package": resolved.stem,
                "version": "",
                "arch": "",
                "summary": resolved.name,
            }
        res = subprocess.run(
            [dpkg_deb, "-f", str(resolved), "Package", "Version", "Architecture", "Description"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise ValueError(f"Tệp .deb bị hỏng hoặc không hợp lệ: {resolved.name}")
        fields: dict[str, str] = {}
        for line in res.stdout.splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                fields[k.strip().lower()] = v.strip()
        return {
            "path": str(resolved),
            "package": fields.get("package", resolved.stem),
            "version": fields.get("version", ""),
            "arch": fields.get("architecture", ""),
            "summary": fields.get("description", resolved.name),
        }

    def install_local_deb(
        self,
        deb_path: Path,
        handle: OperationHandle | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        resolved = deb_path.expanduser().resolve()
        if not resolved.is_file() or resolved.suffix.lower() != ".deb":
            return False, f"Tệp gói .deb không hợp lệ: {resolved}", False
        op = handle or OperationHandle(label=f"Cài đặt {resolved.name}")
        return self._run_streaming_helper(
            ["install-local-deb", str(resolved)], op, on_progress=on_progress
        )

    def install_flatpakref(
        self,
        ref_path: Path,
        handle: OperationHandle | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[bool, str, bool]:
        resolved = ref_path.expanduser().resolve()
        if not resolved.is_file():
            return False, f"Không tìm thấy tệp Flatpak: {resolved}", False
        op = handle or OperationHandle(label=f"Cài đặt {resolved.name}")
        fp = shutil.which("flatpak")
        if not fp:
            ok_fp, msg_fp, needs_rep = self._run_streaming_helper(
                ["ensure-flathub"], op, on_progress=on_progress
            )
            if not ok_fp:
                return False, msg_fp, needs_rep
            fp = shutil.which("flatpak")
            if not fp:
                return False, "Không tìm thấy lệnh flatpak sau khi cài đặt.", False
        if on_progress:
            on_progress(20, f"Đang cài đặt {resolved.name} qua Flatpak (--user)...", True)
        proc = subprocess.Popen(
            [fp, "install", "--user", "-y", "--noninteractive", "--from", str(resolved)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        op.attach(proc)
        assert proc.stdout is not None
        last_lines: list[str] = []
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            last_lines.append(line)
            pct_match = FLATPAK_PCT_RE.search(line)
            pct = int(pct_match.group(1)) if pct_match else 55
            if on_progress:
                on_progress(min(98, max(20, pct)), line[:150], True)
        rc = proc.wait()
        if op.cancelled:
            return False, "Đã hủy cài đặt tệp Flatpak.", False
        if rc == 0:
            if on_progress:
                on_progress(100, f"Đã cài đặt xong {resolved.name}.", False)
            return True, f"Đã cài đặt thành công {resolved.name} (Flatpak).", False
        return False, (" | ".join(last_lines[-2:]) or f"Lỗi cài Flatpak (mã {rc})"), False

    def probe_system_maintenance_status(self) -> dict[str, Any]:
        """Inspect upstream Ubuntu security updates & fwupd firmware independently of NidoHub."""
        unattended_pkg = self.is_deb_installed("unattended-upgrades")
        auto_cfg = Path("/etc/apt/apt.conf.d/20auto-upgrades")
        auto_enabled = False
        if auto_cfg.is_file():
            txt = auto_cfg.read_text(encoding="utf-8", errors="replace")
            auto_enabled = 'Unattended-Upgrade "1"' in txt

        fwupd_pkg = self.is_deb_installed("fwupd") or shutil.which("fwupdmgr") is not None
        flatpak_ready = shutil.which("flatpak") is not None
        snap_store_present = Path("/snap/bin/snap-store").exists() or Path(
            "/var/lib/snapd/desktop/applications/snap-store_snap-store.desktop"
        ).exists()

        return {
            "hostArch": self.host_arch,
            "flatpakArch": self.flatpak_arch,
            "codename": self.codename,
            "securityUpdatesInstalled": unattended_pkg,
            "securityUpdatesAuto": unattended_pkg and auto_enabled,
            "fwupdAvailable": fwupd_pkg,
            "flatpakReady": flatpak_ready,
            "snapStoreRemoved": not snap_store_present,
        }
