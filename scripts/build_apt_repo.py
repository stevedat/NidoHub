#!/usr/bin/env python3
"""Generate a static Debian APT repository tree in `apt-repo/`."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import lzma
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

SUITES = ("resolute", "noble")
ARCHES = ("amd64", "arm64")


def _parse_ar_members(deb_bytes: bytes) -> dict[str, bytes]:
    if not deb_bytes.startswith(b"!<arch>\n"):
        raise ValueError("Invalid .deb file: missing !<arch> magic header")
    pos = 8
    members: dict[str, bytes] = {}
    while pos + 60 <= len(deb_bytes):
        header = deb_bytes[pos : pos + 60]
        pos += 60
        name = header[0:16].decode("ascii", errors="replace").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        payload = deb_bytes[pos : pos + size]
        members[name] = payload
        pos += size + (size % 2)
    return members


def extract_control_text(deb_path: Path) -> str:
    members = _parse_ar_members(deb_path.read_bytes())
    raw_tar: bytes | None = None
    if "control.tar.gz" in members:
        raw_tar = gzip.decompress(members["control.tar.gz"])
    elif "control.tar.xz" in members:
        raw_tar = lzma.decompress(members["control.tar.xz"])
    elif "control.tar.zst" in members:
        raw_tar = subprocess.run(
            ["zstd", "-dc"], input=members["control.tar.zst"], check=True, capture_output=True
        ).stdout
    elif "control.tar" in members:
        raw_tar = members["control.tar"]
    else:
        raise ValueError(f"Unsupported control archive format in {deb_path}: {list(members.keys())}")

    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as tf:
        for member in tf.getmembers():
            if member.name in ("./control", "control"):
                fobj = tf.extractfile(member)
                if fobj is None:
                    break
                return fobj.read().decode("utf-8")
    raise ValueError(f"No ./control found inside {deb_path}")


def _hashes(data: bytes) -> tuple[str, str, str]:
    return (
        hashlib.md5(data).hexdigest(),
        hashlib.sha1(data).hexdigest(),
        hashlib.sha256(data).hexdigest(),
    )


def build_apt_repo(dist_dir: Path, out_dir: Path) -> None:
    deb_files = sorted(dist_dir.glob("*.deb"))
    if not deb_files:
        raise FileNotFoundError(f"No .deb files found in {dist_dir}")

    if out_dir.exists():
        shutil.rmtree(out_dir)

    pool_main = out_dir / "pool" / "main"
    pool_main.mkdir(parents=True, exist_ok=True)

    stanzas_by_arch: dict[str, list[str]] = {arch: [] for arch in ARCHES}

    for deb_path in deb_files:
        control_text = extract_control_text(deb_path).strip()
        pkg_name = ""
        pkg_arch = "all"
        for line in control_text.splitlines():
            if line.startswith("Package:"):
                pkg_name = line.split(":", 1)[1].strip()
            elif line.startswith("Architecture:"):
                pkg_arch = line.split(":", 1)[1].strip()

        subdir = pool_main / (pkg_name[:1] or "n") / (pkg_name or deb_path.stem)
        subdir.mkdir(parents=True, exist_ok=True)
        dest_deb = subdir / deb_path.name
        shutil.copy2(deb_path, dest_deb)

        raw_bytes = deb_path.read_bytes()
        md5, sha1, sha256 = _hashes(raw_bytes)
        rel_filename = dest_deb.relative_to(out_dir).as_posix()

        stanza = (
            f"{control_text}\n"
            f"Filename: {rel_filename}\n"
            f"Size: {len(raw_bytes)}\n"
            f"MD5sum: {md5}\n"
            f"SHA1: {sha1}\n"
            f"SHA256: {sha256}\n"
        )

        target_arches = ARCHES if pkg_arch == "all" else (pkg_arch,) if pkg_arch in ARCHES else ()
        for arch in target_arches:
            stanzas_by_arch[arch].append(stanza)

    now_rfc2822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

    for suite in SUITES:
        suite_dir = out_dir / "dists" / suite
        release_entries: list[tuple[str, int, str, str, str]] = []

        for arch in ARCHES:
            bin_dir = suite_dir / "main" / f"binary-{arch}"
            bin_dir.mkdir(parents=True, exist_ok=True)
            pkg_content = "\n".join(stanzas_by_arch[arch])
            if pkg_content and not pkg_content.endswith("\n"):
                pkg_content += "\n"
            pkg_bytes = pkg_content.encode("utf-8")
            pkg_gz_bytes = gzip.compress(pkg_bytes, mtime=0)

            (bin_dir / "Packages").write_bytes(pkg_bytes)
            (bin_dir / "Packages.gz").write_bytes(pkg_gz_bytes)

            for fname, fbytes in (("Packages", pkg_bytes), ("Packages.gz", pkg_gz_bytes)):
                rel = f"main/binary-{arch}/{fname}"
                md5, sha1, sha256 = _hashes(fbytes)
                release_entries.append((rel, len(fbytes), md5, sha1, sha256))

        lines = [
            "Origin: Ubuntu-Nido",
            "Label: Ubuntu-Nido",
            f"Suite: {suite}",
            f"Codename: {suite}",
            f"Date: {now_rfc2822}",
            f"Architectures: {' '.join(ARCHES)}",
            "Components: main",
            "Description: Ubuntu-Nido APT repository",
            "MD5Sum:",
        ]
        for rel, size, md5, _, _ in release_entries:
            lines.append(f" {md5} {size:>16} {rel}")
        lines.append("SHA1:")
        for rel, size, _, sha1, _ in release_entries:
            lines.append(f" {sha1} {size:>16} {rel}")
        lines.append("SHA256:")
        for rel, size, _, _, sha256 in release_entries:
            lines.append(f" {sha256} {size:>16} {rel}")

        (suite_dir / "Release").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Generated static APT repository in {out_dir} ({len(deb_files)} package(s))")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static APT repository from .deb artifacts.")
    parser.add_argument("--dist-dir", type=Path, default=Path("packages/dist"))
    parser.add_argument("--out-dir", type=Path, default=Path("apt-repo"))
    args = parser.parse_args()
    build_apt_repo(args.dist_dir.resolve(), args.out_dir.resolve())


if __name__ == "__main__":
    main()
