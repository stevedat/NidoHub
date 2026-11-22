---
name: ubuntu-nido-box
description: >-
  Use Ubuntu-Nido system utilities via NidoBox headless CLI (--json) and GUI:
  offline/Agent OCR with PII detection, batch image conversion/resize,
  Vietnamese/CJK-safe archive inspect/extract/create, NidoDrop LAN QR sharing,
  Capture Studio, and read-only external/NTFS drive inspection.
---

# Ubuntu-Nido Utilities (`nidobox`)

Use this skill when the user on Ubuntu-Nido needs offline or Agent OCR, batch
image conversion/resizing, safe archive inspection/extraction/creation
preserving Vietnamese/CJK filenames, LAN QR file sharing, screenshot capture,
or read-only inspection of external/NTFS drives.

All headless subcommands run directly via `/usr/bin/nidobox` without opening a
GUI window and support `--json` for structured output (returning
`{"ok": false, "error": "..."}` on stderr with non-zero exit on failure).

## Preflight

Verify NidoBox engine, distro, and OCR availability first:

```bash
nidobox capabilities --json
nidobox version --json
nidobox --self-test
```

`nidobox capabilities --json` reports `engineAvailable`, `ecosystem`, and
`ocr` (`mode`: `system` | `agent` | `unavailable`, installed Tesseract
languages, and configured NidoAI vision fallback).

## Headless Agent-Native Workflows (`--json`)

### 1. Offline / Agent OCR with Sensitive-Data Detection

Extract text from screenshots, scanned invoices, or photos (`--mode auto`
prefers offline Tesseract `vie+eng`, falling back to NidoAI Gateway Vision when
configured):

```bash
# Structured JSON (includes ocrText, ocrRegions, sensitiveCount, ocrProvider)
nidobox ocr /path/to/image.png --mode auto --json

# Plain text for shell pipelines (e.g. pipe into nidooffice create)
nidobox ocr /path/to/image.png --mode system
```

### 2. Batch Image Conversion & Resizing

Convert and optionally resize one or more images (`webp`, `jpg`, `png`, `bmp`)
using the Rust engine (`--max-dim 0` preserves original dimensions):

```bash
nidobox image convert --format webp --quality 85 --max-dim 0 --json /path/a.png /path/b.jpg
nidobox image convert --format jpg --quality 82 --max-dim 1600 --json /path/photo.png
```

### 3. Safe Archive Inspect, Extract, and Create (Vietnamese / CJK Safe)

Inspect, extract, or create archives (`.zip`, `.7z`, `.tar.*`, `.rar`) without
mojibake or zip-slip traversal:

```bash
# Inspect archive entries safely without extracting
nidobox archive inspect /path/to/archive.zip --json

# Extract one or more archives (optional --dest directory)
nidobox archive extract --dest /path/to/output --json /path/to/archive.zip

# Create a .zip or .7z archive from files or directories
nidobox archive create --out /path/to/bundle.zip --format zip --json /path/dir_or_file
```

### 4. NidoDrop LAN Station Status & Control

Check whether a local NidoDrop QR station is running or stop it headlessly:

```bash
nidobox drop status --json
nidobox drop stop --json
```

### 5. External & NTFS Drives (Read-Only)

List removable/NTFS partitions without modifying mount or filesystem state:

```bash
nidobox drives --json
```

## Desktop GUI Shortcuts

When a graphical Wayland/X11 session (`WAYLAND_DISPLAY` or `DISPLAY`) is active:

```bash
# Open Capture Studio and take a screenshot (same as Super+Shift+S)
nidobox --capture

# Open NidoDrop LAN QR sharing directly with selected files
nidobox --share /path/to/file1.pdf /path/to/file2.png
```

## Guardrails

- Run `nidobox capabilities --json` before invoking `nidobox ocr` so you know
  whether offline `system` OCR or `agent` OCR is available.
- Always inspect untrusted archives with `nidobox archive inspect <file> --json`
  before extracting into shared directories.
- GUI actions (`--capture`, `--share`) require an active Wayland/X11 session.
- `nidobox drives --json` is strictly read-only; never invoke NTFS repair or
  destructive disk operations without explicit user confirmation.
