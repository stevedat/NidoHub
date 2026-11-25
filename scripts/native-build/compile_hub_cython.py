#!/usr/bin/env python3
"""Stage 2 native compiler for nido-hub: compiles core Python modules into stripped ELF .so extensions.

Produces architecture-specific (amd64 / arm64) nido-hub .deb binaries inside Ubuntu Linux
containers while keeping lightweight .py re-export shims so callers and self-tests work identically.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HUB_CORE_MODULES = (
    "nido_account.py",
    "agent_accounts.py",
    "app_store.py",
    "ai_discovery.py",
    "markdown_render.py",
    "printer_probe.py",
)


def compile_modules_to_so(src_dir: Path, out_dir: Path, dry_run: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_sos: list[Path] = []

    if dry_run:
        for mod_file in HUB_CORE_MODULES:
            mod_name = mod_file[:-3]
            native_name = f"_{mod_name}_native"
            print(f"[DRY-RUN] Cythonize {mod_file} -> {native_name}.cpython-*.so (stripped ELF)")
            print(f"[DRY-RUN] Generate thin loader shim {mod_file} -> from {native_name} import *")
        return generated_sos

    try:
        from Cython.Build import cythonize  # type: ignore
        from setuptools import Extension, setup  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Cython & setuptools are required for Stage 2 native compilation. Run inside Dockerfile.native."
        ) from exc

    with tempfile.TemporaryDirectory(prefix="nido-cython-") as td:
        build_root = Path(td)
        ext_modules = []
        for mod_file in HUB_CORE_MODULES:
            mod_src = src_dir / mod_file
            mod_name = mod_file[:-3]
            native_name = f"_{mod_name}_native"
            pyx_path = build_root / f"{native_name}.pyx"
            pyx_path.write_text(mod_src.read_text(encoding="utf-8"), encoding="utf-8")
            ext_modules.append(
                Extension(
                    name=native_name,
                    sources=[str(pyx_path)],
                    extra_compile_args=["-O2", "-fvisibility=hidden"],
                )
            )

        orig_argv = sys.argv[:]
        try:
            sys.argv = [
                "compile_hub_cython.py",
                "build_ext",
                "--build-lib",
                str(out_dir),
                "--build-temp",
                str(build_root / "tmp"),
            ]
            setup(
                name="nido_hub_native",
                ext_modules=cythonize(
                    ext_modules,
                    compiler_directives={"language_level": "3", "docstrings": False},
                ),
            )
        finally:
            sys.argv = orig_argv

    strip_bin = shutil.which("strip")
    for so_path in sorted(out_dir.glob("_*_native*.so")):
        if strip_bin:
            subprocess.run([strip_bin, "--strip-unneeded", str(so_path)], check=True)
        generated_sos.append(so_path)

    for mod_file in HUB_CORE_MODULES:
        mod_name = mod_file[:-3]
        native_name = f"_{mod_name}_native"
        shim_path = out_dir / mod_file
        shim_path.write_text(f"from {native_name} import *  # noqa: F403\n", encoding="utf-8")
        shim_path.chmod(0o644)

    return generated_sos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile nido-hub core modules into native ELF .so extensions.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--out-deb", type=Path, required=True)
    parser.add_argument("--gpg-key", default=os.environ.get("APT_GPG_KEY", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    hub_pkg = repo / "packages/nido-hub"
    hub_lib = hub_pkg / "files/usr/lib/nido-hub"

    if args.dry_run:
        compile_modules_to_so(hub_lib, repo / "packages/nido-hub/stage", dry_run=True)
        print(f"[DRY-RUN] Would assemble Architecture={args.arch} native .deb -> {args.out_deb}")
        return 0

    with tempfile.TemporaryDirectory(prefix="nido-hub-native-") as td:
        work_pkg = Path(td) / "nido-hub-native"
        shutil.copytree(hub_pkg, work_pkg, ignore=shutil.ignore_patterns("stage", "__pycache__"))
        ctrl = work_pkg / "DEBIAN/control"
        ctrl_text = ctrl.read_text(encoding="utf-8").replace("Architecture: all", f"Architecture: {args.arch}")
        ctrl.write_text(ctrl_text, encoding="utf-8")

        extra_stage = Path(td) / "stage"
        lib_out = extra_stage / "usr/lib/nido-hub"
        compile_modules_to_so(hub_lib, lib_out, dry_run=False)

        # Remove raw .py copies for Cython-compiled modules inside work_pkg/files so only .so + thin shim exist
        for mod_file in HUB_CORE_MODULES:
            raw_py = work_pkg / "files/usr/lib/nido-hub" / mod_file
            if raw_py.exists():
                raw_py.unlink()

        # Copy branding icons if already generated
        hub_stage_icons = hub_pkg / "stage/usr/share/icons"
        if hub_stage_icons.is_dir():
            shutil.copytree(hub_stage_icons, extra_stage / "usr/share/icons", dirs_exist_ok=True)

        deb_builder = repo / "scripts/build_deb_portable.py"
        cmd = [
            sys.executable,
            str(deb_builder),
            "--source-dir",
            str(work_pkg),
            "--extra-files",
            str(extra_stage),
            "--integrity-manifest",
            "--output",
            str(args.out_deb),
        ]
        if args.gpg_key:
            cmd.extend(["--gpg-key", args.gpg_key])
        subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
