#!/usr/bin/env python3
"""Reconcile Ubuntu layered Casper live overlay against customized base rootfs.

Ubuntu 24.04/26.04 Desktop ISOs boot via a multi-layer Casper stack
(e.g. ``minimal.squashfs`` <- ``minimal.standard.squashfs`` <-
``minimal.standard.live.squashfs``). When Ubuntu-Nido customizes packages inside
``minimal.squashfs`` (installing ``vielotus``, ``nido*``, Node.js, etc.), the
upstream ``minimal.standard.live.squashfs`` still carries an older snapshot of
``/var/lib/dpkg/status`` and monolithic binary caches (``etc/ld.so.cache``,
``gschemas.compiled``) that would shadow the customized lower layer once
overlayfs mounts them.

This tool:
1. Reconciles ``--live-delta/var/lib/dpkg/status`` so it preserves all packages
   from ``--base-rootfs`` while applying only the genuine live-layer removals
   (overlayfs whiteouts in ``var/lib/dpkg/info/*.list``) and live-added
   packages (regular ``var/lib/dpkg/info/*.list`` files in ``--live-delta``).
2. Removes stale monolithic binary caches from ``--live-delta`` so an offline
   overlay chroot can cleanly regenerate them over the customized base.
3. Strips ``trusted.overlay.*`` extended attributes after unmounting overlayfs
   so host filesystem handles are never baked into SquashFS.
"""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path
from typing import Callable

PROTECTED_CUSTOM_PREFIXES = (
    "nido-",
    "nidoai",
    "nidoagent",
    "nidobox",
    "nidooffice",
    "vielotus",
)

STALE_LIVE_CACHES = (
    "etc/ld.so.cache",
    "var/cache/ldconfig/aux-cache",
    "usr/share/glib-2.0/schemas/gschemas.compiled",
    "usr/share/icons/hicolor/icon-theme.cache",
)


def parse_dpkg_status(status_text: str) -> list[tuple[str, str]]:
    """Parse dpkg status text into an ordered list of ``(package_name, stanza)``."""
    entries: list[tuple[str, str]] = []
    for block in status_text.strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        pkg_name = ""
        for line in block.splitlines():
            if line.startswith("Package:"):
                pkg_name = line.split(":", 1)[1].strip()
                break
        if pkg_name:
            entries.append((pkg_name, block))
    return entries


def _is_overlay_whiteout(path: Path) -> bool:
    """Return True if ``path`` is an overlayfs character-device whiteout (0, 0)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISCHR(st.st_mode) and st.st_rdev == 0


def detect_live_package_changes(
    info_dir: Path,
    whiteout_predicate: Callable[[Path], bool] | None = None,
) -> tuple[set[str], set[str]]:
    """Scan ``var/lib/dpkg/info`` in the live delta layer.

    Returns ``(removed_pkgs, live_added_pkgs)`` derived from ``*.list`` entries.
    """
    is_whiteout = whiteout_predicate or _is_overlay_whiteout
    removed: set[str] = set()
    live_added: set[str] = set()

    if not info_dir.is_dir():
        return removed, live_added

    for entry in sorted(info_dir.iterdir()):
        if not entry.name.endswith(".list"):
            continue
        stem = entry.name[:-5]
        pkg = stem.split(":", 1)[0]
        if is_whiteout(entry):
            if pkg not in live_added:
                removed.add(pkg)
            continue
        try:
            st = os.lstat(entry)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            live_added.add(pkg)
            removed.discard(pkg)

    return removed, live_added


def reconcile_status_texts(
    base_status_text: str,
    live_status_text: str,
    removed_pkgs: set[str],
    live_added_pkgs: set[str],
) -> str:
    """Merge base and live-delta dpkg status stanzas consistently."""
    base_entries = parse_dpkg_status(base_status_text)
    live_entries = parse_dpkg_status(live_status_text)

    base_pkg_names = {pkg for pkg, _ in base_entries}
    live_by_pkg: dict[str, list[str]] = {}
    for pkg, stanza in live_entries:
        live_by_pkg.setdefault(pkg, []).append(stanza)

    merged: list[str] = []
    preserved_custom: set[str] = set()

    for pkg, stanza in base_entries:
        if pkg in removed_pkgs:
            continue
        if pkg in live_added_pkgs:
            if pkg.startswith(PROTECTED_CUSTOM_PREFIXES) and pkg in base_pkg_names:
                preserved_custom.add(pkg)
                merged.append(stanza)
            continue
        merged.append(stanza)

    seen_appended: set[str] = set()
    for pkg, stanza in live_entries:
        if pkg in live_added_pkgs and pkg not in preserved_custom:
            merged.append(stanza)
            seen_appended.add(pkg)

    # Fallback if a live-added .list package was missing from live_status_text
    missing_from_live = (live_added_pkgs - seen_appended - preserved_custom)
    if missing_from_live:
        for pkg, stanza in base_entries:
            if pkg in missing_from_live:
                merged.append(stanza)

    if not merged:
        return ""
    return "\n\n".join(merged) + "\n"


def reconcile_live_layer(
    base_rootfs: Path,
    live_delta: Path,
    whiteout_predicate: Callable[[Path], bool] | None = None,
) -> tuple[int, int, int]:
    """Reconcile dpkg status and clear stale caches inside ``live_delta``.

    Returns ``(removed_count, live_added_count, unlinked_caches_count)``.
    """
    base_status_path = base_rootfs / "var/lib/dpkg/status"
    live_status_path = live_delta / "var/lib/dpkg/status"
    live_info_dir = live_delta / "var/lib/dpkg/info"

    removed_pkgs, live_added_pkgs = detect_live_package_changes(
        live_info_dir, whiteout_predicate=whiteout_predicate
    )

    if base_status_path.is_file():
        base_text = base_status_path.read_text(encoding="utf-8", errors="replace")
        live_text = (
            live_status_path.read_text(encoding="utf-8", errors="replace")
            if live_status_path.is_file()
            else ""
        )
        merged_text = reconcile_status_texts(
            base_text, live_text, removed_pkgs, live_added_pkgs
        )
        live_status_path.parent.mkdir(parents=True, exist_ok=True)
        live_status_path.write_text(merged_text, encoding="utf-8")

    unlinked = 0
    for rel in STALE_LIVE_CACHES:
        target = live_delta / rel
        try:
            st = os.lstat(target)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            target.unlink()
            unlinked += 1

    return len(removed_pkgs), len(live_added_pkgs), unlinked


def _strip_overlay_xattrs_on_path(path: str) -> int:
    """Remove any ``trusted.overlay.*`` xattrs from a single path."""
    removed = 0
    try:
        attrs = os.listxattr(path, follow_symlinks=False)
    except (AttributeError, OSError):
        return 0
    for attr in attrs:
        if attr.startswith("trusted.overlay."):
            try:
                os.removexattr(path, attr, follow_symlinks=False)
                removed += 1
            except OSError:
                pass
    return removed


def strip_overlay_xattrs(target_dir: Path) -> int:
    """Walk ``target_dir`` without following symlinks and strip overlay xattrs."""
    root_str = str(target_dir)
    stripped = _strip_overlay_xattrs_on_path(root_str)
    for dirpath, dirnames, filenames in os.walk(root_str, topdown=True, followlinks=False):
        stripped += _strip_overlay_xattrs_on_path(dirpath)
        for dname in dirnames:
            dpath = os.path.join(dirpath, dname)
            if os.path.islink(dpath):
                stripped += _strip_overlay_xattrs_on_path(dpath)
        for fname in filenames:
            stripped += _strip_overlay_xattrs_on_path(os.path.join(dirpath, fname))
    return stripped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile layered Casper live overlay with customized base rootfs."
    )
    parser.add_argument("--base-rootfs", type=Path, help="Path to customized base rootfs")
    parser.add_argument("--live-delta", type=Path, help="Path to unpacked live delta layer")
    parser.add_argument(
        "--strip-overlay-xattrs",
        type=Path,
        help="Strip trusted.overlay.* xattrs recursively from a directory",
    )
    args = parser.parse_args()

    if args.strip_overlay_xattrs is not None:
        count = strip_overlay_xattrs(args.strip_overlay_xattrs.resolve())
        print(f"Stripped {count} trusted.overlay.* xattr(s) under {args.strip_overlay_xattrs}")
        return

    if args.base_rootfs is not None and args.live_delta is not None:
        rem, add, unlinked = reconcile_live_layer(
            args.base_rootfs.resolve(),
            args.live_delta.resolve(),
        )
        print(
            f"Reconciled live delta dpkg/status "
            f"(removed={rem}, live_added={add}, unlinked_caches={unlinked})"
        )
        return

    parser.error("Specify either (--base-rootfs AND --live-delta) or --strip-overlay-xattrs")


if __name__ == "__main__":
    main()
