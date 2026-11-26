#!/usr/bin/env python3
"""Update casper/install-sources.yaml after remastering Ubuntu 24.04/26.04 ISOs.

When Ubuntu-Nido flattens layered squashfs images (`minimal.squashfs` +
`minimal.standard*.squashfs`) into one remastered `minimal.squashfs`, Subiquity
checks `casper/install-sources.yaml` for exact `size` (bytes) and layer paths.
This pure-Python helper updates `size` and removes superseded layered entries
without requiring third-party YAML packages.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def split_yaml_document(yaml_text: str) -> tuple[list[str], list[list[str]], list[str]]:
    """Split YAML into header lines, top-level `- ...` entry blocks, and trailing footer lines."""
    header: list[str] = []
    entries: list[list[str]] = []
    footer: list[str] = []
    current: list[str] | None = None
    in_footer = False

    for raw_line in yaml_text.splitlines():
        is_top_level_mapping = (
            bool(raw_line)
            and not raw_line[0].isspace()
            and not raw_line.startswith("- ")
            and not raw_line.startswith("#")
        )
        if raw_line.startswith("- "):
            in_footer = False
            if current is not None:
                entries.append(current)
            current = [raw_line]
        elif current is not None and is_top_level_mapping:
            entries.append(current)
            current = None
            in_footer = True
            footer.append(raw_line)
        elif in_footer:
            footer.append(raw_line)
        elif current is None:
            header.append(raw_line)
        else:
            current.append(raw_line)

    if current is not None:
        entries.append(current)

    return header, entries, footer


def split_top_level_entries(yaml_text: str) -> tuple[list[str], list[list[str]]]:
    """Split a top-level YAML list (`- ...`) into header lines and entry blocks."""
    header, entries, _ = split_yaml_document(yaml_text)
    return header, entries


def _entry_field(block: list[str], key: str) -> str | None:
    for idx, line in enumerate(block):
        stripped = line[2:] if idx == 0 and line.startswith("- ") else line.lstrip()
        if idx > 0 and line.startswith("    "):
            continue
        if stripped.startswith(f"{key}:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
    return None


def _set_top_field(block: list[str], key: str, value: str) -> list[str]:
    out: list[str] = []
    replaced = False
    for idx, line in enumerate(block):
        if idx == 0 and line.startswith("- "):
            prefix = "- "
            content = line[2:]
        elif not line.startswith("    "):
            prefix = "  "
            content = line.lstrip()
        else:
            out.append(line)
            continue

        if content.startswith(f"{key}:"):
            out.append(f"{prefix}{key}: {value}")
            replaced = True
        else:
            out.append(line)

    if not replaced:
        out.append(f"  {key}: {value}")
    return out


def _strip_nested_keys(
    block: list[str], keys: tuple[str, ...] = ("preinstalled_langs", "variations")
) -> list[str]:
    """Remove specified 2-space keys and their nested child or list lines."""
    out: list[str] = []
    skipping = False
    for idx, line in enumerate(block):
        if idx == 0 and line.startswith("- "):
            skipping = False
            out.append(line)
            continue
        is_top_key = (
            line.startswith("  ")
            and not line.startswith("   ")
            and not line.startswith("  - ")
            and line.rstrip() != "  -"
        )
        if is_top_key:
            key_name = line[2:].split(":", 1)[0].strip()
            if key_name in keys:
                skipping = True
                continue
            skipping = False
            out.append(line)
            continue
        if skipping and (line.startswith("    ") or line.startswith("  - ") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return out


def _set_localized_section(
    block: list[str], section: str, en_value: str, vi_value: str | None = None
) -> list[str]:
    out: list[str] = []
    in_section = False
    section_indent = "    "
    saw_vi = False

    def flush_vi_if_needed() -> None:
        nonlocal saw_vi
        if in_section and vi_value is not None and not saw_vi:
            out.append(f"{section_indent}vi: {vi_value}")
            saw_vi = True

    for idx, line in enumerate(block):
        stripped = line[2:] if idx == 0 and line.startswith("- ") else line.lstrip()
        is_top = idx == 0 or (
            line.startswith("  ") and not line.startswith("   ") and not line.startswith("  - ")
        )
        if is_top:
            flush_vi_if_needed()
            in_section = stripped.startswith(f"{section}:")
            saw_vi = False
            out.append(line)
        elif in_section and stripped.startswith("en:"):
            section_indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{section_indent}en: {en_value}")
        elif in_section and stripped.startswith("vi:"):
            section_indent = line[: len(line) - len(line.lstrip())]
            if vi_value is not None:
                out.append(f"{section_indent}vi: {vi_value}")
                saw_vi = True
            else:
                out.append(line)
        else:
            out.append(line)
    flush_vi_if_needed()
    return out


def update_install_sources(
    yaml_text: str,
    squashfs_filename: str,
    squashfs_size: int,
    display_name: str = "NidoOS Desktop",
    display_description: str = "NidoOS AI-Native Minimal Desktop.",
    display_name_vi: str | None = None,
    display_description_vi: str = "Hệ điều hành NidoOS tinh gọn, tích hợp AI và bộ gõ tiếng Việt Sen Việt.",
) -> str:
    """Update install-sources.yaml for a flattened remastered squashfs image."""
    vi_name = display_name_vi or display_name
    header, entries, footer = split_yaml_document(yaml_text)
    if not entries:
        return (
            f"- default: true\n"
            f"  description:\n"
            f"    en: {display_description}\n"
            f"    vi: {display_description_vi}\n"
            f"  id: ubuntu-nido-desktop\n"
            f"  locale_support: locale-only\n"
            f"  name:\n"
            f"    en: {display_name}\n"
            f"    vi: {vi_name}\n"
            f"  path: {squashfs_filename}\n"
            f"  size: {squashfs_size}\n"
            f"  type: fsimage\n"
        )

    kept: list[list[str]] = []
    updated_primary = False

    for block in entries:
        path_val = _entry_field(block, "path") or ""
        type_val = _entry_field(block, "type") or ""

        # Drop secondary stacked layers that were flattened into minimal.squashfs
        if path_val != squashfs_filename and type_val == "fsimage-layered":
            continue

        if path_val == squashfs_filename or not updated_primary:
            block = _strip_nested_keys(block, keys=("preinstalled_langs", "variations"))
            block = _set_top_field(block, "path", squashfs_filename)
            block = _set_top_field(block, "size", str(squashfs_size))
            block = _set_top_field(block, "type", "fsimage")
            block = _set_top_field(block, "default", "true")
            block = _set_top_field(block, "locale_support", "locale-only")
            block = _set_localized_section(block, "name", display_name, vi_name)
            block = _set_localized_section(block, "description", display_description, display_description_vi)
            updated_primary = True
        kept.append(block)

    has_sources = any(line.startswith("sources:") for line in header + footer)
    has_version = any(line.startswith("version:") for line in header + footer)
    if has_sources and not has_version:
        footer.append("version: 2")

    lines = list(header)
    for block in kept:
        lines.extend(block)
    lines.extend(footer)
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Update casper/install-sources.yaml sizes.")
    parser.add_argument("--yaml", required=True, type=Path, help="Path to install-sources.yaml")
    parser.add_argument("--squashfs", required=True, type=Path, help="Path to remastered squashfs")
    parser.add_argument("--name", default="Ubuntu-Nido Desktop", help="English selection name")
    parser.add_argument(
        "--size-bytes",
        type=int,
        default=None,
        help="Optional uncompressed rootfs size in bytes (preferred by Subiquity for disk checks & progress)",
    )
    args = parser.parse_args()

    yaml_path = args.yaml.resolve()
    squashfs_path = args.squashfs.resolve()
    if not squashfs_path.is_file():
        raise FileNotFoundError(f"SquashFS file not found: {squashfs_path}")

    size_bytes = args.size_bytes if (args.size_bytes is not None and args.size_bytes > 0) else squashfs_path.stat().st_size
    original = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else ""
    updated = update_install_sources(
        original,
        squashfs_filename=squashfs_path.name,
        squashfs_size=size_bytes,
        display_name=args.name,
    )
    yaml_path.write_text(updated, encoding="utf-8")
    print(f"Updated {yaml_path} ({squashfs_path.name} -> {size_bytes} bytes)")


if __name__ == "__main__":
    main()
