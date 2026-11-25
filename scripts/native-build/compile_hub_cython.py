#!/usr/bin/env python3
"""Stage 2 native compiler for nido-hub: compiles core Python modules into stripped ELF .so extensions.

Produces architecture-specific (amd64 / arm64) nido-hub native modules inside Ubuntu 26.04 Linux
containers while keeping transparent module-proxy .py shims and Stage 1 hardened entry points so
callers, imports (including private helpers), and --self-test work identically.
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

HUB_ENTRY_SCRIPTS = (
    "app.py",
    "self_test.py",
)


def compile_modules_to_so(src_dir: Path, out_dir: Path, dry_run: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_sos: list[Path] = []

    if dry_run:
        for mod_file in HUB_CORE_MODULES:
            mod_name = mod_file[:-3]
            native_name = f"_{mod_name}_native"
            print(f"[DRY-RUN] Cythonize {mod_file} -> {native_name}.cpython-*.so (stripped ELF)")
            print(f"[DRY-RUN] Generate thin loader shim {mod_file} -> sys.modules[__name__] = {native_name}")
        return generated_sos

    try:
        from Cython.Build import cythonize  # type: ignore
        from Cython.Compiler import Options as CythonOptions  # type: ignore
        from setuptools import Extension, setup  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Cython & setuptools are required for Stage 2 native compilation. Run inside Dockerfile.native."
        ) from exc

    CythonOptions.docstrings = False
    CythonOptions.emit_code_comments = False

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
                    compiler_directives={
                        "language_level": "3",
                        "embedsignature": False,
                    },
                ),
            )
        finally:
            sys.argv = orig_argv

    strip_bin = shutil.which("strip")
    for so_path in sorted(out_dir.glob("_*_native*.so")):
        if strip_bin:
            subprocess.run([strip_bin, "--strip-unneeded", str(so_path)], check=True)
        so_path.chmod(0o644)
        generated_sos.append(so_path)

    for mod_file in HUB_CORE_MODULES:
        mod_name = mod_file[:-3]
        native_name = f"_{mod_name}_native"
        shim_path = out_dir / mod_file
        # Transparent module-replacement shim preserves 100% of public and _private attributes
        shim_path.write_text(
            f"import importlib as _i, sys as _s\n"
            f"_s.modules[__name__] = _i.import_module({native_name!r})\n",
            encoding="utf-8",
        )
        shim_path.chmod(0o644)

    return generated_sos


def populate_native_stage(repo: Path, extra_stage: Path, verify: bool = True) -> list[Path]:
    hub_pkg = repo / "packages/nido-hub"
    hub_lib = hub_pkg / "files/usr/lib/nido-hub"
    lib_out = extra_stage / "usr/lib/nido-hub"
    generated_sos = compile_modules_to_so(hub_lib, lib_out, dry_run=False)

    # Stage & apply Stage 1 AST/zlib hardening to app.py and self_test.py so no plaintext Python ships
    entry_targets: list[str] = []
    for entry_name in HUB_ENTRY_SCRIPTS:
        src = hub_lib / entry_name
        if src.is_file():
            dst = lib_out / entry_name
            shutil.copy2(src, dst)
            entry_targets.append(str(dst))
    if entry_targets:
        subprocess.run([sys.executable, str(repo / "scripts/harden_dist.py"), *entry_targets], check=True)
    if (lib_out / "app.py").is_file():
        (lib_out / "app.py").chmod(0o755)

    # Ensure branding icons are generated into stage
    icons_dir = extra_stage / "usr/share/icons/hicolor"
    icons_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(repo / "branding/generate-assets.py"), "--hub-icon-dir", str(icons_dir)],
        check=True,
    )

    # Record target Python minor ABI so host-side .deb assembly can pin exact python3 ABI range
    abi_file = extra_stage / ".python-abi"
    abi_file.write_text(f"{sys.version_info.major}.{sys.version_info.minor}\n", encoding="utf-8")

    if verify:
        print(f"==> Verifying {len(generated_sos)} compiled Cython .so modules via app.py --self-test...")
        subprocess.run([sys.executable, str(lib_out / "app.py"), "--self-test"], check=True)
        for so_path in generated_sos:
            file_bin = shutil.which("file")
            if file_bin:
                info = subprocess.check_output([file_bin, "-b", str(so_path)], text=True).strip()
                print(f"    {so_path.name} ({so_path.stat().st_size} bytes): {info}")

    return generated_sos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile nido-hub core modules into native ELF .so extensions.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--out-deb", type=Path, default=None)
    parser.add_argument(
        "--stage-only",
        type=Path,
        default=None,
        help="Output compiled native stage tree here and exit (for host-side GPG-signed .deb assembly).",
    )
    parser.add_argument("--gpg-key", default=os.environ.get("APT_GPG_KEY", ""))
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    hub_pkg = repo / "packages/nido-hub"
    hub_lib = hub_pkg / "files/usr/lib/nido-hub"

    if args.dry_run:
        compile_modules_to_so(hub_lib, repo / "packages/nido-hub/stage", dry_run=True)
        target_label = args.out_deb or args.stage_only or (repo / f"packages/dist/native/nido-hub_0.2.0_{args.arch}.deb")
        print(f"[DRY-RUN] Would assemble Architecture={args.arch} native .deb -> {target_label}")
        return 0

    if args.stage_only is not None:
        stage_dir = args.stage_only.resolve()
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        stage_dir.mkdir(parents=True, exist_ok=True)
        populate_native_stage(repo, stage_dir, verify=not args.skip_verify)
        return 0

    if args.out_deb is None:
        parser.error("Either --out-deb or --stage-only is required")

    with tempfile.TemporaryDirectory(prefix="nido-hub-native-") as td:
        work_pkg = Path(td) / "nido-hub-native"
        shutil.copytree(hub_pkg, work_pkg, ignore=shutil.ignore_patterns("stage", "__pycache__"))
        ctrl = work_pkg / "DEBIAN/control"
        py_maj, py_min = sys.version_info.major, sys.version_info.minor
        ctrl_text = ctrl.read_text(encoding="utf-8").replace("Architecture: all", f"Architecture: {args.arch}")
        ctrl_text = ctrl_text.replace(
            "Depends: python3,",
            f"Depends: python3 (>= {py_maj}.{py_min}), python3 (<< {py_maj}.{py_min + 1}),",
        )
        ctrl.write_text(ctrl_text, encoding="utf-8")

        extra_stage = Path(td) / "stage"
        populate_native_stage(repo, extra_stage, verify=not args.skip_verify)
        abi_marker = extra_stage / ".python-abi"
        if abi_marker.exists():
            abi_marker.unlink()

        for mod_file in (*HUB_CORE_MODULES, *HUB_ENTRY_SCRIPTS):
            raw_py = work_pkg / "files/usr/lib/nido-hub" / mod_file
            if raw_py.exists():
                raw_py.unlink()

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
