#!/usr/bin/env python3
"""Build a standards-compliant Debian (.deb) package portably on macOS and Linux.

Uses dpkg-deb when available (Ubuntu / CI runners), and falls back to a pure
Python Debian 2.0 `ar` + `tar.gz` builder when running on macOS without dpkg-deb.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

MAINTAINER_SCRIPTS = {"preinst", "postinst", "prerm", "postrm", "config"}
REQUIRED_CONTROL_FIELDS = {"Package", "Version", "Architecture", "Maintainer", "Description"}
SKIP_NAMES = {".DS_Store", "__pycache__"}


def parse_control_fields(control_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_key: str | None = None
    for raw_line in control_text.splitlines():
        if not raw_line.strip():
            continue
        if raw_line[0] in (" ", "\t") and current_key is not None:
            fields[current_key] = f"{fields[current_key]}\n{raw_line}"
        elif ":" in raw_line:
            key, val = raw_line.split(":", 1)
            current_key = key.strip()
            fields[current_key] = val.strip()
    return fields


def validate_control(debian_dir: Path) -> dict[str, str]:
    control_path = debian_dir / "control"
    if not control_path.is_file():
        raise FileNotFoundError(f"Missing required control file: {control_path}")
    text = control_path.read_text(encoding="utf-8")
    fields = parse_control_fields(text)
    missing = REQUIRED_CONTROL_FIELDS - set(fields.keys())
    if missing:
        raise ValueError(f"{control_path} missing required fields: {sorted(missing)}")
    return fields


def _should_skip(path: Path) -> bool:
    return path.name in SKIP_NAMES or any(part in SKIP_NAMES for part in path.parts)


def _copy_tree_filtered(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    for root, dirs, files in os.walk(src, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
        rel_root = Path(root).relative_to(src)
        target_root = dst / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            if name in SKIP_NAMES:
                continue
            sfile = Path(root) / name
            dfile = target_root / name
            if sfile.is_symlink():
                if dfile.exists() or dfile.is_symlink():
                    dfile.unlink()
                os.symlink(os.readlink(sfile), dfile)
            else:
                shutil.copy2(sfile, dfile)


def _normalize_permissions(stage_dir: Path) -> None:
    debian_dir = stage_dir / "DEBIAN"
    for item in debian_dir.iterdir():
        if item.name in MAINTAINER_SCRIPTS:
            item.chmod(0o755)
        elif item.is_file():
            item.chmod(0o644)

    for root, dirs, files in os.walk(stage_dir, followlinks=False):
        rel = Path(root).relative_to(stage_dir)
        if rel.parts and rel.parts[0] == "DEBIAN":
            continue
        for d in dirs:
            dp = Path(root) / d
            if not dp.is_symlink():
                dp.chmod(0o755)
        for f in files:
            fp = Path(root) / f
            if fp.is_symlink():
                continue
            mode = fp.stat().st_mode
            fp.chmod(0o755 if (mode & 0o111) else 0o644)


def _build_tar_gz_from_dir(root_dir: Path, force_exec_names: set[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
            root_info = tarfile.TarInfo(name="./")
            root_info.type = tarfile.DIRTYPE
            root_info.mode = 0o755
            root_info.uid = 0
            root_info.gid = 0
            root_info.uname = "root"
            root_info.gname = "root"
            root_info.mtime = 0
            tf.addfile(root_info)

            entries: list[Path] = []
            for cur_root, dirs, files in os.walk(root_dir, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
                for d in dirs:
                    entries.append(Path(cur_root) / d)
                for f in sorted(files):
                    if f not in SKIP_NAMES:
                        entries.append(Path(cur_root) / f)

            entries.sort(key=lambda p: str(p.relative_to(root_dir)))
            for entry in entries:
                rel = entry.relative_to(root_dir).as_posix()
                arcname = f"./{rel}"
                ti = tarfile.TarInfo(name=arcname)
                ti.uid = 0
                ti.gid = 0
                ti.uname = "root"
                ti.gname = "root"
                ti.mtime = 0

                if entry.is_symlink():
                    ti.type = tarfile.SYMTYPE
                    ti.linkname = os.readlink(entry)
                    ti.mode = 0o777
                    tf.addfile(ti)
                elif entry.is_dir():
                    ti.type = tarfile.DIRTYPE
                    ti.mode = 0o755
                    tf.addfile(ti)
                else:
                    ti.type = tarfile.REGTYPE
                    data = entry.read_bytes()
                    ti.size = len(data)
                    if force_exec_names is not None:
                        ti.mode = 0o755 if entry.name in force_exec_names else 0o644
                    else:
                        ti.mode = 0o755 if (entry.stat().st_mode & 0o111) else 0o644
                    tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _ar_member(name: str, payload: bytes) -> bytes:
    if len(name) > 16:
        raise ValueError(f"ar member name exceeds 16 chars: {name}")
    header = f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(payload):<10}`\n".encode("ascii")
    if len(header) != 60:
        raise AssertionError(f"Expected 60-byte ar header, got {len(header)}")
    pad = b"\n" if (len(payload) % 2) == 1 else b""
    return header + payload + pad


def _apply_version_suffix(control_path: Path, version_suffix: str) -> None:
    if not version_suffix:
        return
    if not re.fullmatch(r"\+[A-Za-z0-9.+~-]+", version_suffix):
        raise ValueError(
            f"Invalid --version-suffix {version_suffix!r}: must start with '+' (e.g. +dev20261118) "
            "so it sorts higher than base 0.1.0 and preserves (>= 0.1.0) dependencies."
        )
    lines = control_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.startswith("Version:"):
            base_ver = line.split(":", 1)[1].strip()
            updated.append(f"Version: {base_ver}{version_suffix}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        raise ValueError(f"Missing Version field in {control_path}")
    control_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


MANIFEST_INCLUDE_PREFIXES = (
    "usr/bin/",
    "usr/libexec/",
    "usr/lib/",
    "usr/share/polkit-1/",
    "etc/apparmor.d/",
    "etc/sysctl.d/",
    "etc/ufw/",
)


def generate_integrity_manifest(stage_dir: Path, pkg_name: str, gpg_key: str = "") -> None:
    """Write SHA-256 manifest (and optional detached GPG signature) of sensitive packaged files."""
    entries: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(stage_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_NAMES)
        rel_dir = Path(root).relative_to(stage_dir).as_posix()
        if rel_dir == "DEBIAN" or rel_dir.startswith("DEBIAN/"):
            continue
        if rel_dir.startswith("usr/share/nido-security"):
            continue
        for fname in sorted(files):
            if fname in SKIP_NAMES:
                continue
            fpath = Path(root) / fname
            if fpath.is_symlink() or not fpath.is_file():
                continue
            rel_posix = fpath.relative_to(stage_dir).as_posix()
            if not rel_posix.startswith(MANIFEST_INCLUDE_PREFIXES):
                continue
            digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
            entries.append((f"/{rel_posix}", digest))

    if not entries:
        return

    manifest_dir = stage_dir / "usr/share/nido-security/manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{pkg_name}.sha256"
    lines = [f"{digest}  {target_path}\n" for target_path, digest in sorted(entries)]
    manifest_path.write_text("".join(lines), encoding="utf-8")
    manifest_path.chmod(0o644)

    clean_key = gpg_key.strip()
    gpg = shutil.which("gpg")
    if clean_key and gpg:
        chk = subprocess.run(
            [gpg, "--batch", "--list-secret-keys", clean_key],
            capture_output=True,
            check=False,
        )
        if chk.returncode == 0:
            sig_path = manifest_dir / f"{pkg_name}.sha256.asc"
            subprocess.run(
                [
                    gpg,
                    "--batch",
                    "--yes",
                    "--armor",
                    "-u",
                    clean_key,
                    "--detach-sign",
                    "-o",
                    str(sig_path),
                    str(manifest_path),
                ],
                check=True,
                capture_output=True,
            )
            sig_path.chmod(0o644)


def build_deb(
    source_dir: Path,
    output_path: Path,
    extra_files_dir: Path | None = None,
    version_suffix: str = "",
    integrity_manifest: bool = False,
    gpg_key: str = "",
) -> None:
    debian_src = source_dir / "DEBIAN"
    files_src = source_dir / "files"
    fields = validate_control(debian_src)

    with tempfile.TemporaryDirectory(prefix="nidodeb-") as tmp:
        stage = Path(tmp) / "pkg"
        (stage / "DEBIAN").mkdir(parents=True, exist_ok=True)
        _copy_tree_filtered(debian_src, stage / "DEBIAN")
        _apply_version_suffix(stage / "DEBIAN" / "control", version_suffix)
        _copy_tree_filtered(files_src, stage)
        if extra_files_dir is not None:
            _copy_tree_filtered(extra_files_dir, stage)
        if integrity_manifest:
            generate_integrity_manifest(stage, fields["Package"], gpg_key=gpg_key)
        _normalize_permissions(stage)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        dpkg_deb = shutil.which("dpkg-deb")
        if dpkg_deb:
            subprocess.run(
                [dpkg_deb, "-Zgzip", "--root-owner-group", "--build", str(stage), str(output_path)],
                check=True,
            )
            return

        # Portable pure-Python Debian 2.0 archive
        control_tar = _build_tar_gz_from_dir(stage / "DEBIAN", force_exec_names=MAINTAINER_SCRIPTS)
        data_stage = Path(tmp) / "data"
        data_stage.mkdir(parents=True, exist_ok=True)
        for child in stage.iterdir():
            if child.name == "DEBIAN":
                continue
            dest = data_stage / child.name
            if child.is_dir():
                shutil.copytree(child, dest, symlinks=True)
            else:
                shutil.copy2(child, dest)
        data_tar = _build_tar_gz_from_dir(data_stage, force_exec_names=None)

        deb_bytes = (
            b"!<arch>\n"
            + _ar_member("debian-binary", b"2.0\n")
            + _ar_member("control.tar.gz", control_tar)
            + _ar_member("data.tar.gz", data_tar)
        )
        output_path.write_bytes(deb_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a .deb package portably.")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--extra-files", type=Path, default=None)
    parser.add_argument("--version-suffix", default="", help="Optional '+' suffix appended to Version during dev builds")
    parser.add_argument(
        "--integrity-manifest",
        action="store_true",
        help="Embed SHA-256 manifest under /usr/share/nido-security/manifests/",
    )
    parser.add_argument(
        "--gpg-key",
        default="",
        help="Optional GPG key ID/fingerprint used to detach-sign embedded integrity manifest",
    )
    args = parser.parse_args()

    build_deb(
        args.source_dir.resolve(),
        args.output.resolve(),
        args.extra_files.resolve() if args.extra_files else None,
        version_suffix=args.version_suffix,
        integrity_manifest=args.integrity_manifest,
        gpg_key=args.gpg_key,
    )
    print(f"Built {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
