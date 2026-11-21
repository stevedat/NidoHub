#!/usr/bin/env python3
"""Global Printer Probe & Adapter-First Resolver for NidoOS (CaramOS-inspired).

Combines:
- CaramOS philosophy: fast (<1s), rootless sysfs/udev probing, zero ISO bloat.
- macOS philosophy: Protocol/Adapter-First (IPP Everywhere / AirPrint / ipp-usb).
- Windows breadth: Universal PDL adapters (PCL6 / PCL5e / PostScript / RAW POS)
  plus targeted on-demand APT vendor drivers matched by IEEE-1284 CMD & USB ID.
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

UNIVERSAL_ADAPTER_MODELS: dict[str, tuple[str, str]] = {
    "everywhere": ("IPP Everywhere / AirPrint (Hiện đại — không cần driver)", "everywhere"),
    "pwgrast": ("Driverless PWG Raster", "drv:///cupsfilters.drv/pwgrast.ppd"),
    "pcl6": ("Universal PCL 6 / XL (HP, Ricoh, Toshiba, Xerox, Canon lớn)", "drv:///sample.drv/pxlmono.ppd"),
    "pcl6-color": ("Universal PCL 6 / XL Màu", "drv:///sample.drv/pxlcolor.ppd"),
    "pcl5e": ("Universal PCL 5e / Laser cổ điển", "drv:///sample.drv/generpcl.ppd"),
    "postscript": ("Universal PostScript / PDF (Văn phòng)", "drv:///sample.drv/generic.ppd"),
    "zebra": ("Máy in mã vạch Zebra (ZPL / EPL)", "drv:///sample.drv/zebra.ppd"),
    "raw": ("RAW / Máy in bill nhiệt POS (58mm / 80mm ESC/POS)", "raw"),
}

ALLOWED_CONFIGURE_URIS = ("ipp://", "ipps://", "usb://", "socket://", "lpd://", "dnssd://")
PRINTER_QUEUE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PPD_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/+-]{3,160}$")

# Targeted USB Vendor/Product rules for devices that do NOT speak IPP or standard PDL
LEGACY_CAPT_PIDS: dict[str, str] = {
    "2676": "Canon LBP2900",
    "26a3": "Canon LBP3000",
    "271a": "Canon LBP6000",
    "26da": "Canon LBP3010/3018",
    "262b": "Canon LBP1120",
}

POS_THERMAL_VENDORS = frozenset({"0416", "0483", "0fe6", "1fc9", "6868", "1504", "0519", "0dd4"})
ZEBRA_VENDOR = "0a5f"
EPSON_VENDOR = "04b8"
CANON_VENDOR = "04a9"
BROTHER_VENDOR = "04f9"
HP_VENDOR = "03f0"
SAMSUNG_VENDOR = "04e8"
XEROX_VENDOR = "0924"


@dataclass(frozen=True)
class PrinterProposal:
    id: str
    name: str
    transport: str  # "usb" | "network" | "cups"
    vendor_id: str = ""
    product_id: str = ""
    uri: str = ""
    ieee1284_cmd: tuple[str, ...] = ()
    status: str = "unknown"  # "ready" | "adapter-available" | "needs-apt" | "arch-limited"
    adapter_label: str = ""
    recommended_model: str = ""
    recommended_packages: tuple[str, ...] = ()
    note_vi: str = ""
    configured_queue: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def parse_ieee1284_id(raw: str) -> dict[str, str]:
    """Parse an IEEE-1284 device ID string (e.g. MFG:HP;MDL:LaserJet;CMD:PCLXL,PCL5E;)."""
    parsed: dict[str, str] = {}
    for part in raw.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        key = k.strip().upper()
        val = v.strip()
        if key and val:
            parsed[key] = val
    return parsed


def _split_cmd_tokens(cmd_str: str) -> tuple[str, ...]:
    tokens = [tok.strip().upper() for tok in re.split(r"[,;\s]+", cmd_str) if tok.strip()]
    return tuple(dict.fromkeys(tokens))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def scan_usb_printers(root_dir: Path = Path("/")) -> list[dict[str, Any]]:
    """Read USB printer interfaces (bInterfaceClass == '07') from sysfs without root."""
    usb_dir = root_dir / "sys/bus/usb/devices"
    if not usb_dir.is_dir():
        return []

    found: dict[str, dict[str, Any]] = {}
    for entry in sorted(usb_dir.iterdir()):
        name = entry.name
        if ":" not in name:
            continue
        cls = _read_text(entry / "bInterfaceClass").lower()
        if cls != "07":
            continue

        dev_name = name.split(":", 1)[0]
        dev_dir = usb_dir / dev_name
        vid = _read_text(dev_dir / "idVendor").lower()
        pid = _read_text(dev_dir / "idProduct").lower()
        if not vid or not pid:
            continue

        mfg = _read_text(dev_dir / "manufacturer")
        prod = _read_text(dev_dir / "product")
        serial = _read_text(dev_dir / "serial")
        driver_link = entry / "driver"
        driver_name = ""
        if driver_link.is_symlink():
            try:
                driver_name = driver_link.resolve().name
            except OSError:
                driver_name = driver_link.readlink().name

        # Read IEEE-1284 ID from usbmisc/lp* or interface attribute if exposed
        ieee_raw = _read_text(entry / "ieee1284_id") or _read_text(dev_dir / "ieee1284_id")
        if not ieee_raw:
            usbmisc = entry / "usbmisc"
            if usbmisc.is_dir():
                for lp_dir in sorted(usbmisc.iterdir()):
                    ieee_raw = _read_text(lp_dir / "ieee1284_id")
                    if ieee_raw:
                        break
        ieee = parse_ieee1284_id(ieee_raw)
        cmd_tokens = _split_cmd_tokens(ieee.get("CMD", ""))

        rec = found.setdefault(
            dev_name,
            {
                "sysfs_dev": dev_name,
                "vid": vid,
                "pid": pid,
                "manufacturer": ieee.get("MFG") or mfg,
                "product": ieee.get("MDL") or prod,
                "serial": serial,
                "drivers": set(),
                "cmd": list(cmd_tokens),
                "ieee1284": ieee,
            },
        )
        if driver_name:
            rec["drivers"].add(driver_name)
        for c in cmd_tokens:
            if c not in rec["cmd"]:
                rec["cmd"].append(c)

    out: list[dict[str, Any]] = []
    for rec in found.values():
        rec["drivers"] = sorted(rec["drivers"])
        rec["cmd"] = tuple(rec["cmd"])
        out.append(rec)
    return out


def list_cups_queues(
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, str]:
    """Return {queue_name: device_uri} via `lpstat -v` without root."""
    lpstat = shutil.which("lpstat")
    if not lpstat and runner is None:
        return {}
    run = runner or subprocess.run
    try:
        res = run([lpstat or "lpstat", "-v"], capture_output=True, text=True, check=False, timeout=3)
    except Exception:
        return {}
    if res.returncode != 0:
        return {}
    queues: dict[str, str] = {}
    for line in res.stdout.splitlines():
        # device for HP_LaserJet: ipp://...
        if ":" not in line:
            continue
        left, uri = line.split(":", 1)
        tokens = left.strip().split()
        if tokens:
            queues[tokens[-1]] = uri.strip()
    return queues


def list_driverless_uris(
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[str]:
    """Discover IPP Everywhere / AirPrint / ipp-usb URIs via `driverless` if available."""
    drv_bin = shutil.which("driverless")
    if not drv_bin and runner is None:
        return []
    run = runner or subprocess.run
    try:
        res = run([drv_bin or "driverless", "list"], capture_output=True, text=True, check=False, timeout=4)
    except Exception:
        return []
    if res.returncode != 0:
        return []
    uris: list[str] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # driverless list outputs: "ipp://..." or "\"ipp://...\" ..."
        first = line.split()[0].strip('"')
        if first.startswith(("ipp://", "ipps://")):
            uris.append(first)
    return uris


def resolve_usb_printer(
    usb_dev: dict[str, Any],
    *,
    host_arch: str,
    installed_packages: set[str],
    cups_queues: dict[str, str],
    driverless_uris: list[str],
) -> PrinterProposal:
    """Resolve a USB printer using Adapter-First (IPP/PDL) -> On-Demand APT Driver."""
    vid = str(usb_dev.get("vid", "")).lower()
    pid = str(usb_dev.get("pid", "")).lower()
    mfg = str(usb_dev.get("manufacturer") or "").strip()
    prod = str(usb_dev.get("product") or "").strip()
    display_name = f"{mfg} {prod}".strip() or f"Máy in USB ({vid}:{pid})"
    drivers = set(usb_dev.get("drivers") or ())
    cmd = tuple(str(c).upper() for c in (usb_dev.get("cmd") or ()))

    serial = str(usb_dev.get("serial") or "").strip()
    usb_uri = ""
    if mfg and prod:
        q = f"?serial={urllib.parse.quote(serial)}" if serial else ""
        usb_uri = f"usb://{urllib.parse.quote(mfg)}/{urllib.parse.quote(prod)}{q}"

    # Match existing CUPS queue by model/serial/ipp-usb
    matched_queue = ""
    for q_name, q_uri in cups_queues.items():
        low_uri = q_uri.lower()
        if (serial and serial.lower() in low_uri) or (prod and prod.lower().replace(" ", "") in low_uri.replace("%20", "")):
            matched_queue = q_name
            break

    # 1. Modern IPP-USB / IPP Everywhere (macOS-style zero-driver)
    if "ipp-usb" in drivers or any("IPP" in c or "URF" in c or "PWGR" in c for c in cmd):
        ipp_uri = next((u for u in driverless_uris if "localhost" in u or "127.0.0.1" in u or (prod and prod.split()[0].lower() in u.lower())), "")
        if matched_queue:
            return PrinterProposal(
                id=f"usb:{vid}:{pid}",
                name=display_name,
                transport="usb",
                vendor_id=vid,
                product_id=pid,
                uri=cups_queues[matched_queue],
                ieee1284_cmd=cmd,
                status="ready",
                adapter_label="Driverless IPP-USB (Sẵn sàng)",
                recommended_model="everywhere",
                configured_queue=matched_queue,
                note_vi="Máy in hiện đại hỗ trợ IPP-USB/AirPrint — in trực tiếp không cần driver hãng.",
            )
        return PrinterProposal(
            id=f"usb:{vid}:{pid}",
            name=display_name,
            transport="usb",
            vendor_id=vid,
            product_id=pid,
            uri=ipp_uri or usb_uri,
            ieee1284_cmd=cmd,
            status="adapter-available",
            adapter_label="Adapter IPP Everywhere",
            recommended_model="everywhere",
            note_vi="Hỗ trợ IPP-USB hiện đại (kiểu macOS). Bấm Gắn Adapter để tạo hàng đợi in ngay.",
        )

    # 2. Legacy Canon CAPT (LBP2900/3000/6000) — proprietary protocol, amd64 only
    if vid == CANON_VENDOR and pid in LEGACY_CAPT_PIDS:
        model_label = LEGACY_CAPT_PIDS[pid]
        arch_note = (
            "Dòng Canon CAPT cổ dùng giao thức độc quyền (chỉ có driver chính chủ cho x86_64/amd64, "
            "không hỗ trợ arm64)."
            if host_arch != "amd64"
            else "Dòng Canon CAPT cổ dùng giao thức độc quyền của Canon (cần driver CAPT .deb từ Canon)."
        )
        return PrinterProposal(
            id=f"usb:{vid}:{pid}",
            name=f"{display_name} ({model_label})",
            transport="usb",
            vendor_id=vid,
            product_id=pid,
            uri=usb_uri,
            ieee1284_cmd=cmd,
            status="arch-limited",
            adapter_label="Giao thức Canon CAPT độc quyền",
            configured_queue=matched_queue,
            note_vi=arch_note,
        )

    # 3. Universal Office PDL (PCLXL / PCL6 / PCL5e / PostScript / PDF) — Windows Universal style
    if any(c in ("PCLXL", "PCL6", "PXL", "PCL5E", "PCL5", "PCL", "POSTSCRIPT", "PS", "PDF") for c in cmd):
        if any(c in ("POSTSCRIPT", "PS", "PDF") for c in cmd):
            model = "drv:///sample.drv/generic.ppd"
            label = "Universal PostScript / PDF"
        elif any(c in ("PCLXL", "PCL6", "PXL") for c in cmd):
            model = "drv:///sample.drv/pxlmono.ppd"
            label = "Universal PCL 6 / XL"
        else:
            model = "drv:///sample.drv/generpcl.ppd"
            label = "Universal PCL 5e"
        return PrinterProposal(
            id=f"usb:{vid}:{pid}",
            name=display_name,
            transport="usb",
            vendor_id=vid,
            product_id=pid,
            uri=usb_uri,
            ieee1284_cmd=cmd,
            status="ready" if matched_queue else "adapter-available",
            adapter_label=label,
            recommended_model=model,
            configured_queue=matched_queue,
            note_vi=f"Nhận diện giao thức chuẩn ({', '.join(cmd)}). Dùng {label}, không cần tải thêm driver.",
        )

    # 4. Thermal POS / Barcode Label (ZPL / EPL / ESC-POS)
    if vid == ZEBRA_VENDOR or any(c in ("ZPL", "EPL", "EPL2") for c in cmd):
        return PrinterProposal(
            id=f"usb:{vid}:{pid}",
            name=display_name,
            transport="usb",
            vendor_id=vid,
            product_id=pid,
            uri=usb_uri,
            ieee1284_cmd=cmd,
            status="ready" if matched_queue else "adapter-available",
            adapter_label="Adapter Zebra ZPL/EPL",
            recommended_model="drv:///sample.drv/zebra.ppd",
            configured_queue=matched_queue,
            note_vi="Máy in mã vạch/tem nhãn Zebra — dùng adapter ZPL/EPL có sẵn.",
        )

    if vid in POS_THERMAL_VENDORS or "POS" in prod.upper() or "THERMAL" in prod.upper() or "XPRINTER" in prod.upper():
        return PrinterProposal(
            id=f"usb:{vid}:{pid}",
            name=display_name,
            transport="usb",
            vendor_id=vid,
            product_id=pid,
            uri=usb_uri,
            ieee1284_cmd=cmd,
            status="ready" if matched_queue else "adapter-available",
            adapter_label="Adapter RAW / ESC-POS (POS 58/80mm)",
            recommended_model="raw",
            configured_queue=matched_queue,
            note_vi="Máy in hóa đơn nhiệt ESC/POS — hỗ trợ in trực tiếp qua hàng đợi RAW hoặc phần mềm bán hàng.",
        )

    # 5. Vendor-specific APT On-Demand Drivers
    needed_pkgs: list[str] = []
    adapter_desc = "Driver hãng qua APT"
    if vid == EPSON_VENDOR or any("ESC/P" in c or "ESCPR" in c for c in cmd):
        adapter_desc = "Epson ESC/P-R (APT On-Demand)"
        if "printer-driver-escpr" not in installed_packages:
            needed_pkgs.append("printer-driver-escpr")
    elif vid == CANON_VENDOR:
        adapter_desc = "Canon PIXMA / Gutenprint (APT On-Demand)"
        if "printer-driver-gutenprint" not in installed_packages:
            needed_pkgs.append("printer-driver-gutenprint")
    elif vid == BROTHER_VENDOR:
        adapter_desc = "Brother Laser (brlaser)"
        if "printer-driver-brlaser" not in installed_packages:
            needed_pkgs.append("printer-driver-brlaser")
    elif vid == HP_VENDOR:
        adapter_desc = "HP HPLIP"
        for pkg in ("hplip", "printer-driver-hpcups"):
            if pkg not in installed_packages:
                needed_pkgs.append(pkg)
    elif vid in (SAMSUNG_VENDOR, XEROX_VENDOR):
        adapter_desc = "Samsung / Xerox SPL (splix)"
        if "printer-driver-splix" not in installed_packages:
            needed_pkgs.append("printer-driver-splix")

    if needed_pkgs:
        return PrinterProposal(
            id=f"usb:{vid}:{pid}",
            name=display_name,
            transport="usb",
            vendor_id=vid,
            product_id=pid,
            uri=usb_uri,
            ieee1284_cmd=cmd,
            status="needs-apt",
            adapter_label=adapter_desc,
            recommended_packages=tuple(needed_pkgs),
            configured_queue=matched_queue,
            note_vi=f"Cần cài gói driver nhẹ từ APT: {', '.join(needed_pkgs)}.",
        )

    return PrinterProposal(
        id=f"usb:{vid}:{pid}",
        name=display_name,
        transport="usb",
        vendor_id=vid,
        product_id=pid,
        uri=usb_uri,
        ieee1284_cmd=cmd,
        status="ready" if matched_queue else "adapter-available",
        adapter_label=adapter_desc,
        recommended_model="everywhere",
        configured_queue=matched_queue,
        note_vi="Driver tương thích đã sẵn sàng. Có thể kiểm tra trong Cài đặt Máy in.",
    )


def build_network_printer_spec(
    host: str,
    adapter_key: str,
    queue_name: str = "",
) -> tuple[str, str, str]:
    """Validate LAN printer IP/hostname + adapter and return (queue_name, uri, model)."""
    clean_host = host.strip()
    if not clean_host or any(ch.isspace() for ch in clean_host):
        raise ValueError("Địa chỉ IP hoặc tên máy chủ máy in không hợp lệ.")

    # Strip scheme if user pasted ip/host with port
    if "://" in clean_host:
        parsed = urllib.parse.urlsplit(clean_host)
        if parsed.scheme not in ("ipp", "ipps", "socket", "lpd"):
            raise ValueError(f"Giao thức máy in không hỗ trợ: {parsed.scheme}")
        uri = clean_host
        host_part = parsed.hostname or "lan-printer"
    else:
        # Validate IP or simple hostname[:port]
        host_only = clean_host
        port = 9100
        if ":" in clean_host and clean_host.count(":") == 1:
            h_p, p_s = clean_host.split(":", 1)
            host_only = h_p
            if not p_s.isdigit() or not (1 <= int(p_s) <= 65535):
                raise ValueError("Cổng mạng máy in không hợp lệ (1–65535).")
            port = int(p_s)
        try:
            ipaddress.ip_address(host_only)
        except ValueError:
            if not re.match(r"^[A-Za-z0-9.-]{1,120}$", host_only):
                raise ValueError(f"Địa chỉ IP/hostname không hợp lệ: {host_only}")
        if adapter_key in ("everywhere", "pwgrast"):
            uri = f"ipp://{host_only}/ipp/print"
        else:
            uri = f"socket://{host_only}:{port}"
        host_part = host_only

    if adapter_key not in UNIVERSAL_ADAPTER_MODELS:
        raise ValueError(f"Adapter máy in không hợp lệ: {adapter_key}")

    _, model = UNIVERSAL_ADAPTER_MODELS[adapter_key]
    qname = queue_name.strip() or f"NidoLAN_{re.sub(r'[^A-Za-z0-9_-]+', '_', host_part)}".strip("_")
    if not PRINTER_QUEUE_NAME_RE.match(qname):
        qname = "NidoLAN_Printer"
    return qname, uri, model


def probe_printers(
    *,
    root_dir: Path = Path("/"),
    host_arch: str = "amd64",
    installed_packages: set[str] | None = None,
    cups_queues: dict[str, str] | None = None,
    driverless_uris: list[str] | None = None,
) -> list[PrinterProposal]:
    """Run a fast (<1s) rootless scan of USB + CUPS + driverless IPP printers."""
    inst = installed_packages if installed_packages is not None else _detect_installed_printer_pkgs()
    queues = cups_queues if cups_queues is not None else list_cups_queues()
    drv_uris = driverless_uris if driverless_uris is not None else list_driverless_uris()

    proposals: list[PrinterProposal] = []
    seen_queues: set[str] = set()

    for usb_dev in scan_usb_printers(root_dir=root_dir):
        prop = resolve_usb_printer(
            usb_dev,
            host_arch=host_arch,
            installed_packages=inst,
            cups_queues=queues,
            driverless_uris=drv_uris,
        )
        if prop.configured_queue:
            seen_queues.add(prop.configured_queue)
        proposals.append(prop)

    # Include remaining configured CUPS queues (network/AirPrint/manual)
    for q_name, q_uri in sorted(queues.items()):
        if q_name in seen_queues:
            continue
        proposals.append(
            PrinterProposal(
                id=f"cups:{q_name}",
                name=q_name.replace("_", " "),
                transport="cups",
                uri=q_uri,
                status="ready",
                adapter_label="Hàng đợi CUPS đang hoạt động",
                configured_queue=q_name,
                note_vi=f"Đã cấu hình tại {q_uri}",
            )
        )

    # Include discovered driverless LAN printers not yet added to CUPS
    existing_uris = {u.lower() for u in queues.values()}
    for u in drv_uris:
        if u.lower() in existing_uris or "localhost" in u.lower() or "127.0.0.1" in u.lower():
            continue
        parsed = urllib.parse.urlsplit(u)
        label = parsed.hostname or u
        proposals.append(
            PrinterProposal(
                id=f"ipp:{u}",
                name=f"Máy in mạng ({label})",
                transport="network",
                uri=u,
                status="adapter-available",
                adapter_label="Driverless IPP / AirPrint",
                recommended_model="everywhere",
                note_vi="Tìm thấy qua mạng LAN/Wi-Fi. Bấm Gắn Adapter để in ngay không cần driver.",
            )
        )

    return proposals


def _detect_installed_printer_pkgs() -> set[str]:
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return set()
    candidates = [
        "cups",
        "ipp-usb",
        "sane-airscan",
        "hplip",
        "printer-driver-hpcups",
        "printer-driver-brlaser",
        "printer-driver-splix",
        "printer-driver-escpr",
        "printer-driver-gutenprint",
        "printer-driver-ptouch",
    ]
    res = subprocess.run(
        [dpkg_query, "-W", "-f=${Package}\t${db:Status-Abbrev}\n", *candidates],
        capture_output=True,
        text=True,
        check=False,
    )
    installed: set[str] = set()
    for line in res.stdout.splitlines():
        if "\t" not in line:
            continue
        pkg, st = line.split("\t", 1)
        if st.strip().startswith("i"):
            installed.add(pkg.strip())
    return installed
