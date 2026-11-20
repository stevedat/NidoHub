---
name: ubuntu-nido-box
description: >-
  Use Ubuntu-Nido system utilities via NidoBox (safe Vietnamese/CJK archive
  extraction, NidoDrop LAN QR file sharing, Capture Studio) and offline
  Vietnamese/English OCR via Tesseract.
---

# Ubuntu-Nido Utilities (`nidobox` & Offline OCR)

Use this skill when the user on Ubuntu-Nido needs safe archive extraction
preserving Vietnamese/CJK filenames, LAN QR file sharing, screenshot capture,
or offline Vietnamese/English OCR.

## Preflight

Verify NidoBox and OCR availability first:

```bash
nidoagent capabilities --json
nidobox --self-test
```

`nidobox --self-test` prints JSON (`{"ok": true, "selfTest": "passed", "version": "1.4.7", ...}`).

## Core Workflows

### 1. Safe Archive Extraction (Vietnamese / CJK filename safe)

Extract one or more archives (`.zip`, `.7z`, `.tar.*`, `.rar`) without mojibake
or zip-slip traversal:

```bash
nidobox --extract /path/to/archive.zip
```

### 2. Share Files Over Local LAN via NidoDrop QR

Open NidoBox directly in NidoDrop LAN sharing mode for selected files:

```bash
nidobox --share /path/to/file1.pdf /path/to/file2.png
```

### 3. Open Capture Studio

Trigger the GTK4 Capture Studio (same action as `Super+Shift+S`):

```bash
nidobox --capture
```

### 4. Headless Offline Vietnamese / English OCR

Ubuntu-Nido ships `tesseract` with `vie` and `eng` traineddata for offline OCR.
When extracting text from screenshots or scanned invoices in headless pipelines:

```bash
tesseract /path/to/image.png stdout -l vie+eng --psm 6
```

Combine with `nidooffice create` to turn OCR output into structured `.docx` or `.xlsx` reports.

## Guardrails

- Run `nidobox --self-test` before assuming NidoBox is healthy.
- GUI actions (`--capture`, `--share`) require an active Wayland/X11 session (`WAYLAND_DISPLAY` or `DISPLAY`).
- Never invoke NTFS repair or destructive disk operations without explicit user confirmation.
