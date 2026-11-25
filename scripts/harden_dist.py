#!/usr/bin/env python3
"""Stage 1 distribution hardener for NidoOS Python modules.

Strips docstrings/comments via AST and packs staged .py files into a compact,
zlib+XOR+b85 wrapped payload without touching original source files in Git.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path


def _strip_docstrings(node: ast.AST) -> ast.AST:
    for child in ast.walk(node):
        body = getattr(child, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(child, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ):
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                if len(body) == 1:
                    body[0] = ast.Pass()
                else:
                    body.pop(0)
    return node


def strip_python_source(source: str) -> str:
    tree = ast.parse(source)
    _strip_docstrings(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def harden_python_source(source: str, label: str = "<nido>") -> str:
    shebang = ""
    if source.startswith("#!"):
        nl = source.find("\n")
        if nl != -1:
            shebang = source[: nl + 1]
        else:
            shebang = source + "\n"

    stripped = strip_python_source(source)
    compile(stripped, label, "exec")

    compressed = zlib.compress(stripped.encode("utf-8"), level=9)
    key = hashlib.sha256(f"nido-stage1:{label}".encode("utf-8")).digest()
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(compressed))
    encoded = base64.b85encode(xored).decode("ascii")

    out = (
        f"{shebang}"
        "import base64 as _b,zlib as _z\n"
        f"_K={key!r}\n"
        f"_P={encoded!r}\n"
        'exec(compile(_z.decompress(bytes(v^_K[i%32]for i,v in enumerate(_b.b85decode(_P)))).decode(),"'
        + label.replace("\\", "\\\\").replace('"', '\\"')
        + '","exec"),globals())\n'
    )
    compile(out, label, "exec")
    return out


def harden_file(src: Path, dst: Path | None = None) -> None:
    target = dst or src
    raw = src.read_text(encoding="utf-8")
    hardened = harden_python_source(raw, label=src.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(hardened, encoding="utf-8")


def run_self_test() -> int:
    sample = (
        "#!/usr/bin/env python3\n"
        '"""Sensitive module docstring that must be stripped."""\n'
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "# Sensitive inline comment that must be stripped\n"
        "@dataclass\n"
        "class Item:\n"
        '    """Class docstring."""\n'
        "    name: str\n"
        "    note: str | None = None\n"
        "def greet(item: Item) -> str:\n"
        '    """Func docstring."""\n'
        "    return f'hello:{item.name}'\n"
        'if __name__ == "__main__":\n'
        "    import sys\n"
        "    print(greet(Item(sys.argv[1])))\n"
    )
    out = harden_python_source(sample, label="sample_mod.py")
    assert out.startswith("#!/usr/bin/env python3\n")
    assert "Sensitive module docstring" not in out
    assert "Sensitive inline comment" not in out
    assert "Func docstring" not in out

    with tempfile.TemporaryDirectory(prefix="nido-harden-py-") as td:
        mod_path = Path(td) / "sample_mod.py"
        mod_path.write_text(out, encoding="utf-8")
        # 1. Execute as __main__
        res = subprocess.run(
            [sys.executable, str(mod_path), "NidoOS"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.stdout.strip() == "hello:NidoOS", res.stdout
        # 2. Import and verify dataclass + helper export
        imp = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {td!r}); from sample_mod import Item, greet; print(greet(Item('Sec')))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert imp.stdout.strip() == "hello:Sec", imp.stdout

    print("harden_dist.py self-test passed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 1 Python distribution hardener.")
    parser.add_argument("paths", nargs="*", type=Path, help="Python files to harden in-place.")
    parser.add_argument("--self-test", action="store_true", help="Run self-test and exit.")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if not args.paths:
        parser.error("Provide at least one .py file path or --self-test")
    for p in args.paths:
        harden_file(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
