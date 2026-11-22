#!/usr/bin/env python3
"""Verify that ISO pool dists/*/Release.gpg is verifiable by the rootfs APT keys.

Ubuntu 26.04+ Desktop ISOs sign the on-ISO pool (dists/<suite>/Release.gpg) with
an ephemeral per-ISO Ed25519 key embedded inline in the rootfs file
``/etc/apt/sources.list.d/cdrom.sources`` (Deb822 ``Signed-By:`` field).
Earlier Ubuntu 24.04 ISOs rely on ``/etc/apt/trusted.gpg.d/ubuntu-keyring-*-cdimage.gpg``.

If customization deletes ``cdrom.sources``, Subiquity falls back to a legacy
``file:///cdrom`` entry without the ephemeral public key and aborts installation
with ``NO_PUBKEY`` / ``The repository 'file:/cdrom ... Release' is not signed``.

This build-time gate extracts all keys available to APT inside ``--rootfs``
(both ``cdrom.sources`` inline/file ``Signed-By:`` keys and ``trusted.gpg.d``)
and cryptographically verifies every ``dists/*/Release.gpg`` in ``--iso-tree``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def parse_deb822_signed_by(sources_text: str) -> tuple[list[str], list[str]]:
    """Parse Deb822 ``.sources`` text and return ``(inline_pgp_blocks, key_paths)``."""
    inline_blocks: list[str] = []
    key_paths: list[str] = []

    for stanza in sources_text.split("\n\n"):
        lines = stanza.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            if line.lower().startswith("signed-by:"):
                first_val = line.split(":", 1)[1].strip()
                cont_lines: list[str] = []
                i += 1
                while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                    val = lines[i].strip()
                    cont_lines.append("" if val == "." else val)
                    i += 1
                all_lines = ([first_val] if first_val else []) + cont_lines
                joined = "\n".join(all_lines)
                if "-----BEGIN PGP PUBLIC KEY BLOCK-----" in joined:
                    inline_blocks.append(joined.strip() + "\n")
                else:
                    for token in joined.replace(",", " ").split():
                        if token.startswith("/"):
                            key_paths.append(token)
                continue
            i += 1

    return inline_blocks, key_paths


def collect_rootfs_keys(rootfs: Path) -> tuple[list[str], list[Path], list[Path]]:
    """Return ``(inline_pgp_blocks, cdrom_key_files, trusted_gpg_files)``."""
    inline_blocks: list[str] = []
    cdrom_key_files: list[Path] = []
    trusted_files: list[Path] = []

    sources_dir = rootfs / "etc/apt/sources.list.d"
    if sources_dir.is_dir():
        for src in sorted(sources_dir.glob("*cdrom*.sources")):
            if not src.is_file():
                continue
            blocks, paths = parse_deb822_signed_by(src.read_text(encoding="utf-8", errors="replace"))
            inline_blocks.extend(blocks)
            for kp in paths:
                resolved = rootfs / kp.lstrip("/")
                if resolved.is_file():
                    cdrom_key_files.append(resolved)

    trusted_dir = rootfs / "etc/apt/trusted.gpg.d"
    if trusted_dir.is_dir():
        for ext in ("*.gpg", "*.asc"):
            for kf in sorted(trusted_dir.glob(ext)):
                if kf.is_file():
                    trusted_files.append(kf)

    return inline_blocks, cdrom_key_files, trusted_files


def _dearmor_or_raw(gpg_bin: str, raw_bytes: bytes) -> bytes:
    """Convert ASCII-armored OpenPGP bytes to binary packets, or return binary bytes as-is."""
    if b"-----BEGIN PGP PUBLIC KEY BLOCK-----" in raw_bytes:
        res = subprocess.run(
            [gpg_bin, "--dearmor"],
            input=raw_bytes,
            capture_output=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout
        return b""
    return raw_bytes


def _build_keyring(
    work_dir: Path,
    inline_blocks: list[str],
    key_files: list[Path],
) -> Path | None:
    """Assemble candidate OpenPGP public keys into a single binary keyring without gpg-agent."""
    gpg_bin = shutil.which("gpg")
    if not gpg_bin:
        raise RuntimeError("gpg is required to assemble temporary verification keyring")

    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[bytes] = []

    for block in inline_blocks:
        raw = _dearmor_or_raw(gpg_bin, block.encode("utf-8"))
        if raw:
            chunks.append(raw)

    for kf in key_files:
        try:
            data = kf.read_bytes()
        except OSError:
            continue
        if not data:
            continue
        raw = _dearmor_or_raw(gpg_bin, data)
        if raw:
            chunks.append(raw)

    if not chunks:
        return None

    keyring_path = work_dir / "combined.gpg"
    keyring_path.write_bytes(b"".join(chunks))
    if not keyring_path.is_file() or keyring_path.stat().st_size == 0:
        return None
    return keyring_path


def _verify_detached(keyring: Path, sig_file: Path, data_file: Path, work_dir: Path) -> tuple[bool, str]:
    """Verify ``sig_file`` over ``data_file`` using ``sqv``, ``gpgv``, or ``gpg``."""
    sqv_bin = shutil.which("sqv")
    if sqv_bin:
        res = subprocess.run(
            [sqv_bin, "--keyring", str(keyring), str(sig_file), str(data_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0, (res.stdout + res.stderr).strip()

    gpgv_bin = shutil.which("gpgv")
    if gpgv_bin:
        res = subprocess.run(
            [gpgv_bin, "--keyring", str(keyring), str(sig_file), str(data_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0, (res.stdout + res.stderr).strip()

    gpg_bin = shutil.which("gpg")
    if gpg_bin:
        verify_home = work_dir / "verify_gnupg"
        verify_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        env = os.environ.copy()
        env["GNUPGHOME"] = str(verify_home)
        res = subprocess.run(
            [
                gpg_bin,
                "--batch",
                "--no-tty",
                "--no-default-keyring",
                "--keyring",
                str(keyring),
                "--verify",
                str(sig_file),
                str(data_file),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0, (res.stdout + res.stderr).strip()

    raise RuntimeError("Neither sqv, gpgv, nor gpg found on PATH")


def verify_iso_cdrom_signatures(iso_tree: Path, rootfs: Path) -> int:
    """Verify all ``dists/*/Release.gpg`` files in ``iso_tree`` against ``rootfs`` keys.

    Returns the number of verified Release signatures, or raises ``RuntimeError``.
    """
    dists_dir = iso_tree / "dists"
    if not dists_dir.is_dir():
        return 0

    sig_pairs: list[tuple[Path, Path]] = []
    for sig in sorted(dists_dir.glob("*/Release.gpg")):
        rel = sig.with_name("Release")
        if sig.is_file() and rel.is_file():
            sig_pairs.append((sig, rel))

    if not sig_pairs:
        return 0

    inline_blocks, cdrom_key_files, trusted_files = collect_rootfs_keys(rootfs)
    cdrom_sources = rootfs / "etc/apt/sources.list.d/cdrom.sources"

    with tempfile.TemporaryDirectory(prefix="ncdv-", dir="/tmp" if Path("/tmp").is_dir() else None) as td:
        work = Path(td)
        trusted_ring = _build_keyring(work / "trusted", [], trusted_files)
        all_ring = _build_keyring(
            work / "all",
            inline_blocks,
            cdrom_key_files + trusted_files,
        )

        if all_ring is None:
            raise RuntimeError(
                "No APT verification keys found in rootfs "
                "(both etc/apt/sources.list.d/cdrom.sources and etc/apt/trusted.gpg.d are empty)."
            )

        verified = 0
        for sig, rel in sig_pairs:
            ok_trusted = False
            if trusted_ring is not None:
                ok_trusted, _ = _verify_detached(trusted_ring, sig, rel, work / f"t-{verified}")

            ok_all, detail = _verify_detached(all_ring, sig, rel, work / f"a-{verified}")
            if not ok_all:
                raise RuntimeError(
                    f"ISO pool signature verification FAILED for {sig.relative_to(iso_tree)} "
                    f"(cdrom.sources present={cdrom_sources.is_file()}, "
                    f"inline_keys={len(inline_blocks)}, trusted_keys={len(trusted_files)}): "
                    f"{detail}"
                )

            if not ok_trusted and not cdrom_sources.is_file():
                raise RuntimeError(
                    f"{sig.relative_to(iso_tree)} is signed with an ephemeral ISO key not in "
                    f"etc/apt/trusted.gpg.d, but etc/apt/sources.list.d/cdrom.sources is missing!"
                )
            verified += 1

    return verified


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify ISO dists/*/Release.gpg against rootfs APT keyrings and cdrom.sources."
    )
    parser.add_argument("--iso-tree", type=Path, required=True, help="Extracted ISO directory containing dists/")
    parser.add_argument("--rootfs", type=Path, required=True, help="Customized rootfs directory containing etc/apt/")
    args = parser.parse_args()

    try:
        count = verify_iso_cdrom_signatures(args.iso_tree.resolve(), args.rootfs.resolve())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if count == 0:
        print("No ISO dists/*/Release.gpg found; skipping cdrom signature verification.")
    else:
        print(f"Verified {count} ISO pool Release.gpg signature(s) against rootfs APT keys.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
