#!/usr/bin/env python3
"""Standalone OOXML document engine for Ubuntu-Nido (nidooffice-cli).

Supports reading DOCX, XLSX, PPTX, and plain text files as well as creating
valid OOXML DOCX and XLSX documents using only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

VERSION = "0.1.0"
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024  # 32 MiB zip-bomb guard

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

PIPE_SEP_RE = re.compile(r"^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{0,}:?\s*$")
CELL_COL_RE = re.compile(r"^([A-Za-z]+)")
NUMBER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"([0-9]+)", name)]


def _col_letters_to_index(col: str) -> int:
    idx = 0
    for ch in col.upper():
        if "A" <= ch <= "Z":
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return max(0, idx - 1)


def _index_to_col_letters(idx: int) -> str:
    n = idx + 1
    chars: list[str] = []
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def _safe_zip_read(zf: zipfile.ZipFile, member_name: str, budget_left: list[int]) -> bytes:
    info = zf.getinfo(member_name)
    if info.file_size < 0 or info.file_size > budget_left[0]:
        raise ValueError(
            f"Archive entry '{member_name}' exceeds uncompressed safety limit ({MAX_UNCOMPRESSED_BYTES} bytes)"
        )
    data = zf.read(member_name)
    if len(data) > budget_left[0]:
        raise ValueError(
            f"Decompressed entry '{member_name}' exceeds safety limit ({MAX_UNCOMPRESSED_BYTES} bytes)"
        )
    budget_left[0] -= len(data)
    return data


def _extract_w_p_text(p_elem: ET.Element) -> str:
    parts: list[str] = []
    for node in p_elem.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts)


def read_docx(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("Invalid DOCX: missing word/document.xml")
        budget = [MAX_UNCOMPRESSED_BYTES]
        xml_bytes = _safe_zip_read(zf, "word/document.xml", budget)

    root = ET.fromstring(xml_bytes)
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        return ""

    blocks: list[str] = []
    for child in body:
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            txt = _extract_w_p_text(child).strip()
            if txt:
                blocks.append(txt)
        elif tag == "tbl":
            rows: list[list[str]] = []
            for tr in child.findall(f"{{{W_NS}}}tr"):
                row_cells: list[str] = []
                for tc in tr.findall(f"{{{W_NS}}}tc"):
                    cell_paras = [_extract_w_p_text(p).strip() for p in tc.findall(f"{{{W_NS}}}p")]
                    cell_txt = " ".join(p for p in cell_paras if p).replace("|", "\\|")
                    row_cells.append(cell_txt)
                if row_cells:
                    rows.append(row_cells)
            if rows:
                width = max(len(r) for r in rows)
                norm = [r + [""] * (width - len(r)) for r in rows]
                table_lines = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
                for r in norm[1:]:
                    table_lines.append("| " + " | ".join(r) + " |")
                blocks.append("\n".join(table_lines))
    return "\n\n".join(blocks)


def read_xlsx(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        sheet_names = sorted(
            [n for n in names if re.match(r"^xl/worksheets/sheet[0-9]+\.xml$", n)],
            key=_natural_key,
        )
        if not sheet_names:
            raise ValueError("Invalid XLSX: no worksheets found")
        budget = [MAX_UNCOMPRESSED_BYTES]

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            ss_root = ET.fromstring(_safe_zip_read(zf, "xl/sharedStrings.xml", budget))
            for si in ss_root.findall(f"{{{S_NS}}}si"):
                t_parts = [t.text or "" for t in si.iter(f"{{{S_NS}}}t")]
                shared_strings.append("".join(t_parts))

        workbook_titles: list[str] = []
        if "xl/workbook.xml" in names:
            wb_root = ET.fromstring(_safe_zip_read(zf, "xl/workbook.xml", budget))
            for sh in wb_root.iter(f"{{{S_NS}}}sheet"):
                if sh.attrib.get("name"):
                    workbook_titles.append(sh.attrib["name"])

        sections: list[str] = []
        for idx, sname in enumerate(sheet_names):
            sroot = ET.fromstring(_safe_zip_read(zf, sname, budget))
            rows_dict: list[dict[int, str]] = []
            max_col = 0
            for row_el in sroot.iter(f"{{{S_NS}}}row"):
                row_map: dict[int, str] = {}
                fallback_col = 0
                for cell in row_el.findall(f"{{{S_NS}}}c"):
                    ref = cell.attrib.get("r", "")
                    match = CELL_COL_RE.match(ref)
                    col_idx = _col_letters_to_index(match.group(1)) if match else fallback_col
                    fallback_col = col_idx + 1
                    max_col = max(max_col, col_idx)

                    ctype = cell.attrib.get("t", "")
                    val = ""
                    if ctype == "s":
                        v_el = cell.find(f"{{{S_NS}}}v")
                        if v_el is not None and v_el.text is not None:
                            try:
                                ss_idx = int(v_el.text.strip())
                                val = shared_strings[ss_idx] if 0 <= ss_idx < len(shared_strings) else v_el.text
                            except ValueError:
                                val = v_el.text
                    elif ctype == "inlineStr":
                        is_el = cell.find(f"{{{S_NS}}}is")
                        if is_el is not None:
                            val = "".join(t.text or "" for t in is_el.iter(f"{{{S_NS}}}t"))
                    elif ctype == "b":
                        v_el = cell.find(f"{{{S_NS}}}v")
                        val = "TRUE" if (v_el is not None and v_el.text == "1") else "FALSE"
                    else:
                        v_el = cell.find(f"{{{S_NS}}}v")
                        if v_el is not None and v_el.text is not None:
                            val = v_el.text
                    row_map[col_idx] = val.replace("\n", " ").replace("|", "\\|")
                if any(v != "" for v in row_map.values()):
                    rows_dict.append(row_map)

            if not rows_dict:
                continue
            width = max_col + 1
            table = [[rmap.get(c, "") for c in range(width)] for rmap in rows_dict]
            sheet_title = workbook_titles[idx] if idx < len(workbook_titles) else f"Sheet {idx + 1}"
            lines = [
                f"## {sheet_title}",
                "| " + " | ".join(table[0]) + " |",
                "| " + " | ".join("---" for _ in range(width)) + " |",
            ]
            for r in table[1:]:
                lines.append("| " + " | ".join(r) + " |")
            sections.append("\n".join(lines))
    return "\n\n".join(sections)


def read_pptx(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as zf:
        slides = sorted(
            [n for n in zf.namelist() if re.match(r"^ppt/slides/slide[0-9]+\.xml$", n)],
            key=_natural_key,
        )
        if not slides:
            raise ValueError("Invalid PPTX: no slides found")
        budget = [MAX_UNCOMPRESSED_BYTES]
        sections: list[str] = []
        for idx, sname in enumerate(slides, 1):
            sroot = ET.fromstring(_safe_zip_read(zf, sname, budget))
            texts = [t.text.strip() for t in sroot.iter(f"{{{A_NS}}}t") if t.text and t.text.strip()]
            if texts:
                sections.append(f"## Slide {idx}\n" + "\n".join(texts))
    return "\n\n".join(sections)


def read_document(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext == ".docx":
        return "docx", read_docx(path)
    if ext == ".xlsx":
        return "xlsx", read_xlsx(path)
    if ext == ".pptx":
        return "pptx", read_pptx(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return ext.lstrip(".") or "text", text


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


def _docx_paragraph_xml(text: str, style_id: str | None = None) -> str:
    ppr = ""
    if style_id:
        ppr = f'<w:pPr><w:pStyle w:val="{xml_escape(style_id)}"/></w:pPr>'
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r></w:p>'


def _docx_table_xml(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    borders = (
        '<w:tblBorders>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="E0E0E0"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="E0E0E0"/>'
        '</w:tblBorders>'
    )
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>{borders}</w:tblPr>']
    for r_idx, row in enumerate(rows):
        padded = row + [""] * (width - len(row))
        cells_xml: list[str] = []
        for cell in padded:
            run_pr = "<w:rPr><w:b/></w:rPr>" if r_idx == 0 else ""
            cells_xml.append(
                f'<w:tc><w:p><w:r>{run_pr}<w:t xml:space="preserve">{xml_escape(cell)}</w:t></w:r></w:p></w:tc>'
            )
        out.append(f"<w:tr>{''.join(cells_xml)}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def _markdown_to_docx_body(content: str, title: str | None = None) -> str:
    elements: list[str] = []
    if title:
        first_non_empty = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
        if first_non_empty != f"# {title.strip()}":
            elements.append(_docx_paragraph_xml(title.strip(), "Heading1"))

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue

        if _is_pipe_row(stripped):
            table_rows: list[list[str]] = []
            while i < len(lines) and _is_pipe_row(lines[i].strip()):
                cur = lines[i].strip()
                if not PIPE_SEP_RE.match(cur):
                    table_rows.append(_parse_pipe_cells(cur))
                i += 1
            if table_rows:
                elements.append(_docx_table_xml(table_rows))
            continue

        if stripped.startswith("### "):
            elements.append(_docx_paragraph_xml(stripped[4:].strip(), "Heading3"))
        elif stripped.startswith("## "):
            elements.append(_docx_paragraph_xml(stripped[3:].strip(), "Heading2"))
        elif stripped.startswith("# "):
            elements.append(_docx_paragraph_xml(stripped[2:].strip(), "Heading1"))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            elements.append(_docx_paragraph_xml("• " + stripped[2:].strip(), None))
        else:
            elements.append(_docx_paragraph_xml(raw.rstrip(), None))
        i += 1

    if not elements:
        elements.append(_docx_paragraph_xml("", None))
    return "".join(elements)


def write_docx(out_path: Path, content: str, title: str | None = None) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body_xml = _markdown_to_docx_body(content, title=title)

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Be Vietnam Pro" w:hAnsi="Be Vietnam Pro" w:cs="Liberation Sans"/>'
        '<w:sz w:val="22"/>'
        "</w:rPr></w:rPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:pPr><w:spacing w:after="120"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1">'
        '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="34"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2">'
        '<w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="200" w:after="80"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3">'
        '<w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="160" w:after="60"/></w:pPr>'
        '<w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
        "</w:styles>"
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body_xml}<w:sectPr/></w:body>"
        "</w:document>"
    )

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/document.xml", document_xml)


def _extract_xlsx_rows(content: str, src_ext: str = "") -> list[list[Any]]:
    stripped = content.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list) and parsed:
                if all(isinstance(r, list) for r in parsed):
                    return parsed
                if all(isinstance(r, dict) for r in parsed):
                    headers: list[str] = []
                    for obj in parsed:
                        for k in obj.keys():
                            ks = str(k)
                            if ks not in headers:
                                headers.append(ks)
                    rows: list[list[Any]] = [headers]
                    for obj in parsed:
                        rows.append([obj.get(h, "") for h in headers])
                    return rows
        except json.JSONDecodeError:
            pass

    pipe_rows: list[list[str]] = []
    for line in content.splitlines():
        s = line.strip()
        if _is_pipe_row(s) and not PIPE_SEP_RE.match(s):
            pipe_rows.append(_parse_pipe_cells(s))
    if len(pipe_rows) >= 2:
        return pipe_rows

    non_empty = [ln for ln in content.splitlines() if ln.strip()]
    if src_ext in (".csv", ".tsv") or (
        len(non_empty) >= 2 and all("\t" in ln for ln in non_empty)
    ):
        delim = "\t" if all("\t" in ln for ln in non_empty) else ","
        return list(csv.reader(io.StringIO("\n".join(non_empty)), delimiter=delim))

    if len(non_empty) >= 2:
        csv_rows = list(csv.reader(io.StringIO("\n".join(non_empty))))
        widths = {len(r) for r in csv_rows}
        if len(widths) == 1 and next(iter(widths)) >= 2:
            return csv_rows

    rows_fallback: list[list[Any]] = [["Nội dung"]]
    for ln in non_empty:
        rows_fallback.append([ln.strip()])
    if len(rows_fallback) == 1:
        rows_fallback.append([""])
    return rows_fallback


def _is_numeric_cell(val: Any) -> float | int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return val if math.isfinite(val) else None
    if isinstance(val, str):
        s = val.strip()
        if NUMBER_RE.match(s):
            try:
                num = float(s) if "." in s else int(s)
                if isinstance(num, int) or math.isfinite(num):
                    return num
            except ValueError:
                return None
    return None


def write_xlsx(out_path: Path, content: str, title: str | None = None, src_ext: str = "") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _extract_xlsx_rows(content, src_ext=src_ext)
    sheet_name = (title or "Bao-cao")[:31]
    sheet_name = re.sub(r"[:\\/?*\[\]]", "-", sheet_name) or "Bao-cao"

    shared_strings: list[str] = []
    ss_index: dict[str, int] = {}

    def intern_str(text: str) -> int:
        if text not in ss_index:
            ss_index[text] = len(shared_strings)
            shared_strings.append(text)
        return ss_index[text]

    row_xml_parts: list[str] = []
    for r_idx, row in enumerate(rows, 1):
        cells_xml: list[str] = []
        for c_idx, val in enumerate(row):
            coord = f"{_index_to_col_letters(c_idx)}{r_idx}"
            num = _is_numeric_cell(val)
            if num is not None and r_idx > 1:
                cells_xml.append(f'<c r="{coord}"><v>{num}</v></c>')
            else:
                text = "" if val is None else str(val)
                # Always store strings in sharedStrings (t="s") so leading '=' is literal text, never a formula.
                sid = intern_str(text)
                cells_xml.append(f'<c r="{coord}" t="s"><v>{sid}</v></c>')
        row_xml_parts.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    wb_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Liberation Sans"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )
    si_xml = "".join(
        f'<si><t xml:space="preserve">{xml_escape(s)}</t></si>' for s in shared_strings
    )
    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">{si_xml}</sst>'
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml_parts)}</sheetData>'
        "</worksheet>"
    )

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/sharedStrings.xml", shared_strings_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="nidooffice",
        description="Ubuntu-Nido headless Office document CLI (DOCX / XLSX / PPTX)",
    )
    parser.add_argument("--version", "-v", action="version", version=f"nidooffice {VERSION}")
    sub = parser.add_subparsers(dest="command")

    p_read = sub.add_parser("read", help="Extract readable text/tables from a document")
    p_read.add_argument("file", type=Path, help="Path to .docx, .xlsx, .pptx, or text file")
    p_read.add_argument("--json", action="store_true", help="Output structured JSON")

    p_create = sub.add_parser("create", help="Create a DOCX or XLSX file from Markdown/JSON/CSV/stdin")
    p_create.add_argument("--type", required=True, choices=("docx", "xlsx"), help="Output document type")
    p_create.add_argument("--out", required=True, type=Path, help="Output file path")
    p_create.add_argument("--from", dest="src", default="-", help="Input file path or '-' for stdin")
    p_create.add_argument("--title", default=None, help="Optional document title or worksheet name")

    args = parser.parse_args()
    if args.command == "read":
        target = args.file.resolve()
        if not target.is_file():
            print(f"nidooffice read: file not found: {target}", file=sys.stderr)
            return 1
        doc_type, text = read_document(target)
        if args.json:
            print(json.dumps({"ok": True, "path": str(target), "type": doc_type, "text": text}, ensure_ascii=False))
        else:
            print(text)
        return 0

    if args.command == "create":
        src_ext = ""
        if args.src == "-":
            if sys.stdin.isatty():
                print("nidooffice create: missing input (--from <file> or piped stdin)", file=sys.stderr)
                return 2
            raw_content = sys.stdin.read()
        else:
            src_path = Path(args.src).resolve()
            if not src_path.is_file():
                print(f"nidooffice create: input file not found: {src_path}", file=sys.stderr)
                return 1
            src_ext = src_path.suffix.lower()
            raw_content = src_path.read_text(encoding="utf-8", errors="replace")

        out_path = args.out.resolve()
        if args.type == "docx":
            write_docx(out_path, raw_content, title=args.title)
        else:
            write_xlsx(out_path, raw_content, title=args.title, src_ext=src_ext)
        print(json.dumps({"ok": True, "type": args.type, "out": str(out_path)}, ensure_ascii=False))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
