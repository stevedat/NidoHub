#!/usr/bin/env python3
"""Zero-dependency Markdown block & inline renderer for NidoHub GTK4 TextView & plain text."""

from __future__ import annotations

import re
from typing import Any

# Ordered inline tokenizer: code & links take precedence over emphasis so math (2 * 3 * 4),
# globs (*.deb), identifiers (snake_case), and code (`**kwargs`) are never corrupted.
_INLINE_TOKEN_RE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\[(?P<link_label>[^\]\n]+)\]\((?P<link_url>https?://[^)\s]+)\)"
    r"|\*\*\*(?=\S)(?P<bold_italic>.+?)(?<=\S)\*\*\*"
    r"|\*\*(?=\S)(?P<bold>.+?)(?<=\S)\*\*"
    r"|(?<!\w)__(?=\S)(?P<bold_u>.+?)(?<=\S)__(?!\w)"
    r"|(?<!\*)\*(?=\S)(?!\*)(?P<italic>[^*\n]+?)(?<=\S)\*(?!\*)"
    r"|~~(?=\S)(?P<strike>.+?)(?<=\S)~~"
)

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^([ \t]*)[*\-+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^([ \t]*)(\d+[.)])\s+(.*)$")
_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_PIPE_SEP_RE = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{0,}:?\s*$")

_BULLET_GLYPHS = ("•", "◦", "▪")


def parse_inline_spans(text: str) -> list[tuple[str, str]]:
    """Tokenize Markdown inline markup into (kind, plain_text) spans."""
    if not text:
        return []
    spans: list[tuple[str, str]] = []
    pos = 0
    for match in _INLINE_TOKEN_RE.finditer(text):
        start, end = match.span()
        if start > pos:
            spans.append(("text", text[pos:start]))
        if match.group("code") is not None:
            spans.append(("code", match.group("code")))
        elif match.group("link_label") is not None:
            spans.append(("link", match.group("link_label")))
        elif match.group("bold_italic") is not None:
            spans.append(("bold_italic", _strip_nested_code(match.group("bold_italic"))))
        elif match.group("bold") is not None or match.group("bold_u") is not None:
            inner = match.group("bold") if match.group("bold") is not None else match.group("bold_u")
            if "`" in inner:
                for sub_kind, sub_txt in parse_inline_spans(inner):
                    spans.append(("code" if sub_kind == "code" else "bold", sub_txt))
            else:
                spans.append(("bold", inner))
        elif match.group("italic") is not None:
            spans.append(("italic", match.group("italic")))
        elif match.group("strike") is not None:
            spans.append(("strike", match.group("strike")))
        pos = end
    if pos < len(text):
        spans.append(("text", text[pos:]))
    return spans


def _strip_nested_code(text: str) -> str:
    return re.sub(r"`([^`\n]+)`", r"\1", text)


def _indent_level(raw_indent: str) -> int:
    expanded = raw_indent.replace("\t", "  ")
    return min(len(expanded) // 2, 3)


def _is_pipe_row(line: str) -> bool:
    s = line.strip()
    return "|" in s and (s.startswith("|") or s.count("|") >= 2)


def _parse_pipe_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_markdown_blocks(text: str) -> list[dict[str, Any]]:
    """Parse Markdown text into structured blocks preserving fenced code verbatim."""
    blocks: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    in_code = False
    code_lines: list[str] = []

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if stripped.startswith("```"):
            if in_code:
                blocks.append({"type": "code_block", "text": "\n".join(code_lines)})
                code_lines = []
                in_code = False
            else:
                in_code = True
                code_lines = []
            i += 1
            continue

        if in_code:
            code_lines.append(raw)
            i += 1
            continue

        if not stripped:
            if blocks and blocks[-1]["type"] != "blank":
                blocks.append({"type": "blank"})
            i += 1
            continue

        if _HR_RE.match(stripped) and not _BULLET_RE.match(raw):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # Markdown pipe table
        if _is_pipe_row(stripped):
            rows: list[list[str]] = []
            while i < len(lines) and _is_pipe_row(lines[i].strip()):
                cur = lines[i].strip()
                if not _PIPE_SEP_RE.match(cur):
                    rows.append(_parse_pipe_cells(cur))
                i += 1
            if rows:
                blocks.append({"type": "table", "rows": rows})
            continue

        h_match = _HEADING_RE.match(stripped)
        if h_match:
            level = min(len(h_match.group(1)), 3)
            blocks.append(
                {
                    "type": "heading",
                    "level": level,
                    "spans": parse_inline_spans(h_match.group(2).strip()),
                }
            )
            i += 1
            continue

        if stripped.startswith(">"):
            quote_text = stripped.lstrip(">").strip()
            blocks.append({"type": "quote", "spans": parse_inline_spans(quote_text)})
            i += 1
            continue

        b_match = _BULLET_RE.match(raw)
        if b_match:
            lvl = _indent_level(b_match.group(1))
            blocks.append(
                {
                    "type": "bullet",
                    "level": lvl,
                    "glyph": _BULLET_GLYPHS[min(lvl, len(_BULLET_GLYPHS) - 1)],
                    "spans": parse_inline_spans(b_match.group(2).strip()),
                }
            )
            i += 1
            continue

        o_match = _ORDERED_RE.match(raw)
        if o_match:
            lvl = _indent_level(o_match.group(1))
            blocks.append(
                {
                    "type": "ordered",
                    "level": lvl,
                    "marker": o_match.group(2),
                    "spans": parse_inline_spans(o_match.group(3).strip()),
                }
            )
            i += 1
            continue

        blocks.append({"type": "paragraph", "spans": parse_inline_spans(raw.rstrip())})
        i += 1

    if in_code and code_lines:
        blocks.append({"type": "code_block", "text": "\n".join(code_lines)})

    while blocks and blocks[-1]["type"] == "blank":
        blocks.pop()
    return blocks


def render_markdown_to_plain(text: str) -> str:
    """Render Markdown to clean human-readable plain text with zero raw ** or backticks."""
    out_lines: list[str] = []
    for blk in parse_markdown_blocks(text):
        btype = blk["type"]
        if btype == "blank":
            out_lines.append("")
        elif btype == "hr":
            out_lines.append("─" * 40)
        elif btype == "code_block":
            out_lines.append(blk["text"])
        elif btype == "heading":
            out_lines.append("".join(s for _, s in blk["spans"]))
        elif btype == "quote":
            out_lines.append("│ " + "".join(s for _, s in blk["spans"]))
        elif btype == "bullet":
            pad = "  " * blk["level"]
            out_lines.append(f"{pad}{blk['glyph']} " + "".join(s for _, s in blk["spans"]))
        elif btype == "ordered":
            pad = "  " * blk["level"]
            out_lines.append(f"{pad}{blk['marker']} " + "".join(s for _, s in blk["spans"]))
        elif btype == "table":
            clean_rows = [
                ["".join(s for _, s in parse_inline_spans(c)) for c in row]
                for row in blk["rows"]
            ]
            widths = [
                max((len(r[col]) for r in clean_rows if col < len(r)), default=0)
                for col in range(max(len(r) for r in clean_rows))
            ]
            for r_idx, row in enumerate(clean_rows):
                padded = [
                    (row[c] if c < len(row) else "").ljust(widths[c])
                    for c in range(len(widths))
                ]
                out_lines.append("  " + " │ ".join(padded).rstrip())
                if r_idx == 0 and len(clean_rows) > 1:
                    out_lines.append("  " + "─┼─".join("─" * max(w, 3) for w in widths))
        else:
            out_lines.append("".join(s for _, s in blk["spans"]))
    return "\n".join(out_lines)


def _ensure_gtk_tags(buf: Any) -> dict[str, Any]:
    from gi.repository import Pango  # type: ignore

    table = buf.get_tag_table()
    specs: dict[str, dict[str, Any]] = {
        "h1": {"weight": Pango.Weight.BOLD, "scale": 1.28, "pixels_above_lines": 8, "pixels_below_lines": 4},
        "h2": {"weight": Pango.Weight.BOLD, "scale": 1.15, "pixels_above_lines": 6, "pixels_below_lines": 3},
        "h3": {"weight": Pango.Weight.BOLD, "scale": 1.06, "pixels_above_lines": 4, "pixels_below_lines": 2},
        "bold": {"weight": Pango.Weight.BOLD},
        "italic": {"style": Pango.Style.ITALIC},
        "bold_italic": {"weight": Pango.Weight.BOLD, "style": Pango.Style.ITALIC},
        "code": {"family": "monospace", "scale": 0.94, "background": "rgba(128,128,128,0.18)"},
        "code_block": {
            "family": "monospace",
            "scale": 0.93,
            "left_margin": 16,
            "paragraph_background": "rgba(128,128,128,0.14)",
        },
        "link": {"underline": Pango.Underline.SINGLE},
        "strike": {"strikethrough": True},
        "quote": {"style": Pango.Style.ITALIC, "left_margin": 16},
        "indent_0": {"left_margin": 6, "indent": -12},
        "indent_1": {"left_margin": 24, "indent": -12},
        "indent_2": {"left_margin": 42, "indent": -12},
        "indent_3": {"left_margin": 60, "indent": -12},
        "table_hdr": {"weight": Pango.Weight.BOLD, "family": "monospace", "scale": 0.94},
        "table_row": {"family": "monospace", "scale": 0.94},
    }
    tags: dict[str, Any] = {}
    for name, props in specs.items():
        existing = table.lookup(name)
        if existing is None:
            existing = buf.create_tag(name, **props)
        tags[name] = existing
    return tags


def render_markdown_to_gtk_buffer(buf: Any, text: str) -> None:
    """Render Markdown natively into a GTK4 TextBuffer using Gtk.TextTag styling."""
    buf.set_text("")
    tags = _ensure_gtk_tags(buf)
    blocks = parse_markdown_blocks(text)

    def insert_spans(spans: list[tuple[str, str]], base_tags: list[Any]) -> None:
        for kind, content in spans:
            if not content:
                continue
            applied = list(base_tags)
            if kind in ("bold", "italic", "bold_italic", "code", "link", "strike"):
                applied.append(tags[kind])
            end_iter = buf.get_end_iter()
            if applied:
                buf.insert_with_tags(end_iter, content, *applied)
            else:
                buf.insert(end_iter, content)

    for idx, blk in enumerate(blocks):
        if idx > 0:
            buf.insert(buf.get_end_iter(), "\n")
        btype = blk["type"]
        if btype == "blank":
            continue
        if btype == "hr":
            buf.insert(buf.get_end_iter(), "─" * 44)
        elif btype == "code_block":
            buf.insert_with_tags(buf.get_end_iter(), blk["text"], tags["code_block"])
        elif btype == "heading":
            htag = tags.get(f"h{blk['level']}", tags["h3"])
            insert_spans(blk["spans"], [htag])
        elif btype == "quote":
            buf.insert_with_tags(buf.get_end_iter(), "│ ", tags["quote"])
            insert_spans(blk["spans"], [tags["quote"]])
        elif btype == "bullet":
            itag = tags[f"indent_{min(blk['level'], 3)}"]
            buf.insert_with_tags(buf.get_end_iter(), f"{blk['glyph']} ", itag)
            insert_spans(blk["spans"], [itag])
        elif btype == "ordered":
            itag = tags[f"indent_{min(blk['level'], 3)}"]
            buf.insert_with_tags(buf.get_end_iter(), f"{blk['marker']} ", itag)
            insert_spans(blk["spans"], [itag])
        elif btype == "table":
            clean_rows = [
                ["".join(s for _, s in parse_inline_spans(c)) for c in row]
                for row in blk["rows"]
            ]
            widths = [
                max((len(r[col]) for r in clean_rows if col < len(r)), default=0)
                for col in range(max(len(r) for r in clean_rows))
            ]
            for r_idx, row in enumerate(clean_rows):
                if r_idx > 0:
                    buf.insert(buf.get_end_iter(), "\n")
                padded = [
                    (row[c] if c < len(row) else "").ljust(widths[c])
                    for c in range(len(widths))
                ]
                line_txt = " │ ".join(padded).rstrip()
                buf.insert_with_tags(
                    buf.get_end_iter(),
                    line_txt,
                    tags["table_hdr"] if r_idx == 0 else tags["table_row"],
                )
                if r_idx == 0 and len(clean_rows) > 1:
                    buf.insert(buf.get_end_iter(), "\n")
                    sep_txt = "─┼─".join("─" * max(w, 3) for w in widths)
                    buf.insert_with_tags(buf.get_end_iter(), sep_txt, tags["table_row"])
        else:
            insert_spans(blk["spans"], [])
