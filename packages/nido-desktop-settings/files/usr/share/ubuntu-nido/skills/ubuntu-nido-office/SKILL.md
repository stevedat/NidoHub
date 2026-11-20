---
name: ubuntu-nido-office
description: >-
  Read, inspect, and generate Word (.docx), Excel (.xlsx), PowerPoint (.pptx),
  and text documents on Ubuntu-Nido using the native nidooffice CLI with
  structured JSON output and full Vietnamese UTF-8 support.
---

# Ubuntu-Nido Office CLI (`nidooffice`)

Use `nidooffice` whenever the user asks to inspect, summarize, extract tables from,
or create Office documents (`.docx`, `.xlsx`, `.pptx`) on Ubuntu-Nido.

## Preflight

Check availability before running:

```bash
nidooffice --version
```

## Core Commands

### 1. Inspect metadata before reading large files

```bash
nidooffice info /path/to/file.docx --json
nidooffice info /path/to/file.xlsx --json
```

Returns JSON:

```json
{"ok": true, "path": "...", "type": "docx", "bytes": 12345, "stats": {"paragraphs": 42, "tables": 3}}
```

### 2. Extract readable text & Markdown tables

```bash
nidooffice read /path/to/document.docx --json
nidooffice read /path/to/sheet.xlsx --json
nidooffice read /path/to/slides.pptx --json
```

Returns JSON with `"ok": true`, `"path"`, `"type"`, `"bytes"`, and `"text"`.
Prefer `--json` when parsing programmatically.

### 3. Create DOCX or XLSX documents

From stdin (UTF-8 Markdown for `.docx`, Markdown pipe tables or CSV for `.xlsx`):

```bash
nidooffice create --type docx --out /path/to/report.docx --title "Bao cao" --from - --json <<'EOF'
# Tieu de

Noi dung tieng Viet day du dau.

| Hang muc | Gia tri |
| --- | --- |
| Doanh thu | 1200 |
EOF
```

From a source file:

```bash
nidooffice create --type xlsx --out /path/to/summary.xlsx --from /path/to/data.csv --json
```

Returns JSON:

```json
{"ok": true, "type": "xlsx", "out": "/path/to/summary.xlsx", "bytes": 6120}
```

## Guardrails

- Always pass `--json` when chaining commands from an AI agent; on errors, stderr contains `{"ok": false, "error": "..."}` with a non-zero exit code.
- For `.xlsx` exports from Markdown, format tabular data as standard Markdown pipe tables (`| Col A | Col B |`) so numeric values and headers convert cleanly.
- If the standalone Python OOXML engine is explicitly needed instead of the desktop CLI delegation, set `NIDOOFFICE_FORCE_PYTHON=1`.
