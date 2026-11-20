#!/usr/bin/env python3
"""Append a minimal NidoOS Plymouth branding CPIO overlay to casper/initrd.

Preserves 100% of the upstream Casper/dracut initrd bytes untouched (microcode +
compressed initramfs) while overriding Ubuntu's Plymouth boot logos with NidoOS
assets from second zero of live ISO boot.

Format follows Linux kernel Documentation/driver-api/early-userspace/buffer-format.rst
(SVR4 portable ASCII 'newc' CPIO, 4-byte aligned).
"""

from __future__ import annotations

import argparse
import os
import stat
import struct
import sys
import tempfile
import zlib
from pathlib import Path

MARK_VERSION = "NidoOS-v2"
MARKER_RELPATH = "etc/nidoos-initrd-branded"

# Primary Plymouth crest / fallback logo paths replaced with NidoOS emblem PNG.
# NOTE: On VMs without an ACPI UEFI BGRT table (e.g., UTM/QEMU), Ubuntu's initrd
# bgrt theme displays spinner/bgrt-fallback.png at top-center (Ubuntu's orange
# Circle of Friends). Replacing it eliminates the upstream logo completely.
INITRD_PLYMOUTH_LOGO_PATHS = (
    "usr/share/plymouth/ubuntu-logo.png",
    "usr/share/plymouth/themes/spinner/bgrt-fallback.png",
    "usr/share/pixmaps/ubuntu-logo-text-dark.png",
    "usr/share/pixmaps/ubuntu-logo-text.png",
    "usr/share/plymouth/themes/ubuntu-logo/ubuntu-logo.png",
    "usr/share/plymouth/themes/ubuntu-logo/ubuntu-logo16.png",
    "usr/share/plymouth/themes/nidoos/watermark.png",
)

# Secondary bottom watermark slots in bgrt/spinner themes: replace with a 1x1
# transparent PNG so the NidoOS emblem appears cleanly once above the spinner
# instead of duplicating top + bottom.
INITRD_PLYMOUTH_TRANSPARENT_PATHS = (
    "usr/share/plymouth/themes/spinner/watermark.png",
    "usr/share/plymouth/themes/bgrt/watermark.png",
)

def _make_transparent_1x1_png() -> bytes:
    """Build a guaranteed-valid 1x1 fully transparent RGBA32 PNG."""
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00\x00\x00\x00", level=6)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


TRANSPARENT_1X1_PNG = _make_transparent_1x1_png()

NIDOOS_TEXT_PLYMOUTH = (
    b"[Plymouth Theme]\n"
    b"Name=NidoOS Text\n"
    b"Description=NidoOS text-mode boot splash\n"
    b"ModuleName=ubuntu-text\n\n"
    b"[ubuntu-text]\n"
    b"title=NidoOS\n"
    b"black=0x000000\n"
    b"white=0xf8fafc\n"
    b"brown=0x10b981\n"
    b"blue=0x0d9488\n"
)


def _pad4(n: int) -> int:
    return (4 - (n % 4)) % 4


def _cpio_entry(relpath: str, mode: int, data: bytes, ino: int) -> bytes:
    name_bytes = relpath.lstrip("/").encode("utf-8") + b"\x00"
    nlink = 2 if stat.S_ISDIR(mode) else 1
    header = (
        f"070701"
        f"{ino:08X}"
        f"{mode:08X}"
        f"{0:08X}"  # uid
        f"{0:08X}"  # gid
        f"{nlink:08X}"
        f"{0:08X}"  # mtime
        f"{len(data):08X}"
        f"{0:08X}"  # devmajor
        f"{0:08X}"  # devminor
        f"{0:08X}"  # rdevmajor
        f"{0:08X}"  # rdevminor
        f"{len(name_bytes):08X}"
        f"{0:08X}"  # check
    ).encode("ascii")
    assert len(header) == 110
    head_pad = b"\x00" * _pad4(len(header) + len(name_bytes))
    data_pad = b"\x00" * _pad4(len(data))
    return header + name_bytes + head_pad + data + data_pad


def build_newc_cpio(files: dict[str, bytes]) -> bytes:
    """Build a deterministic uncompressed newc CPIO archive for ``files``."""
    dirs: set[str] = set()
    for relpath in files:
        parts = relpath.strip("/").split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))

    chunks: list[bytes] = []
    ino = 1
    for d in sorted(dirs):
        chunks.append(_cpio_entry(d, stat.S_IFDIR | 0o755, b"", ino))
        ino += 1
    for relpath in sorted(files):
        chunks.append(_cpio_entry(relpath.strip("/"), stat.S_IFREG | 0o644, files[relpath], ino))
        ino += 1

    # TRAILER!!! terminates this CPIO archive cleanly
    chunks.append(_cpio_entry("TRAILER!!!", 0, b"", 0))
    raw = b"".join(chunks)
    # Pad archive length to 512-byte block boundary (conventional for initramfs segments)
    rem = len(raw) % 512
    if rem:
        raw += b"\x00" * (512 - rem)
    return raw


def parse_newc_cpio(blob: bytes) -> dict[str, bytes]:
    """Minimal newc CPIO parser used for self-testing & verification."""
    out: dict[str, bytes] = {}
    pos = 0
    total = len(blob)
    while pos + 110 <= total:
        magic = blob[pos : pos + 6]
        if magic == b"\x00\x00\x00\x00\x00\x00":
            break
        if magic != b"070701":
            raise ValueError(f"Invalid CPIO magic at offset {pos}: {magic!r}")
        mode = int(blob[pos + 14 : pos + 22], 16)
        filesize = int(blob[pos + 54 : pos + 62], 16)
        namesize = int(blob[pos + 94 : pos + 102], 16)
        name_start = pos + 110
        name_end = name_start + namesize
        name = blob[name_start : name_end - 1].decode("utf-8")
        data_start = name_end + _pad4(110 + namesize)
        data_end = data_start + filesize
        if name == "TRAILER!!!":
            break
        if stat.S_ISREG(mode):
            out[name] = blob[data_start:data_end]
        pos = data_end + _pad4(filesize)
    return out


def patch_initrd(initrd_path: Path, logo_png_path: Path) -> int:
    """Append NidoOS Plymouth branding CPIO overlay to ``initrd_path``.

    Returns the number of bytes appended (0 if already patched).
    """
    if not initrd_path.is_file():
        raise FileNotFoundError(f"Missing initrd: {initrd_path}")
    logo_bytes = logo_png_path.read_bytes()
    if logo_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG header in {logo_png_path}")

    orig_size = initrd_path.stat().st_size
    tail_probe = min(orig_size, 256 * 1024)
    with initrd_path.open("rb") as rf:
        rf.seek(orig_size - tail_probe)
        if MARKER_RELPATH.encode("ascii") in rf.read(tail_probe):
            return 0

    overlay_files: dict[str, bytes] = {
        rel: logo_bytes for rel in INITRD_PLYMOUTH_LOGO_PATHS
    }
    for rel in INITRD_PLYMOUTH_TRANSPARENT_PATHS:
        overlay_files[rel] = TRANSPARENT_1X1_PNG
    overlay_files["usr/share/plymouth/themes/ubuntu-text/ubuntu-text.plymouth"] = (
        NIDOOS_TEXT_PLYMOUTH
    )
    overlay_files[MARKER_RELPATH] = f"{MARK_VERSION}\n".encode("ascii")

    cpio_blob = build_newc_cpio(overlay_files)
    align_pad = b"\x00" * _pad4(orig_size)

    with initrd_path.open("ab") as af:
        if align_pad:
            af.write(align_pad)
        af.write(cpio_blob)

    return len(align_pad) + len(cpio_blob)


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="nidoos-initrd-test-") as td:
        root = Path(td)
        # Minimal valid 1x1 PNG bytes for test
        fake_png = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        logo_p = root / "nidoos-logo.png"
        logo_p.write_bytes(fake_png)

        # Simulate an unaligned compressed initrd prefix (len % 4 != 0)
        orig_prefix = b"\x28\xb5\x2f\xfdFAKE_ZSTD_CASPER_INITRD_STREAM_123"
        initrd_p = root / "initrd"
        initrd_p.write_bytes(orig_prefix)

        added = patch_initrd(initrd_p, logo_p)
        assert added > 0, "Expected non-zero appended overlay"
        full = initrd_p.read_bytes()
        assert full[: len(orig_prefix)] == orig_prefix, "Original Casper initrd prefix was mutated!"

        aligned_start = len(orig_prefix) + _pad4(len(orig_prefix))
        assert aligned_start % 4 == 0
        parsed = parse_newc_cpio(full[aligned_start:])
        for rel in INITRD_PLYMOUTH_LOGO_PATHS:
            assert parsed.get(rel) == fake_png, f"Missing or mismatched {rel} in CPIO overlay"
        for rel in INITRD_PLYMOUTH_TRANSPARENT_PATHS:
            assert parsed.get(rel) == TRANSPARENT_1X1_PNG, f"Missing transparent placeholder for {rel}"
        assert b"title=NidoOS" in parsed.get(
            "usr/share/plymouth/themes/ubuntu-text/ubuntu-text.plymouth", b""
        )
        assert parsed.get(MARKER_RELPATH) == f"{MARK_VERSION}\n".encode("ascii")

        # Verify idempotency (second run appends 0 bytes)
        added_again = patch_initrd(initrd_p, logo_p)
        assert added_again == 0, f"Expected idempotent 0 bytes, got {added_again}"
    print("patch_initrd_branding.py self-test passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append NidoOS Plymouth branding CPIO overlay to casper/initrd."
    )
    parser.add_argument("--initrd", type=Path, help="Path to casper/initrd to patch in place")
    parser.add_argument("--logo", type=Path, help="Path to NidoOS RGBA PNG logo")
    parser.add_argument("--self-test", action="store_true", help="Run headless unit tests")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    if not args.initrd or not args.logo:
        parser.error("Provide --initrd and --logo (or --self-test)")

    added = patch_initrd(args.initrd, args.logo)
    if added == 0:
        print(f"casper/initrd already branded with NidoOS overlay ({args.initrd}).")
    else:
        print(f"Appended {added} bytes NidoOS Plymouth CPIO overlay to {args.initrd}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
