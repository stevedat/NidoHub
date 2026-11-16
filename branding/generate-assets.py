#!/usr/bin/env python3
"""Generate deterministic 24-bit RGB PNG branding assets for Ubuntu-Nido.

Uses Python standard library only (zlib + struct) so it runs cleanly on
macOS, Ubuntu chroot, and CI runners without Pillow or ImageMagick.
"""

from __future__ import annotations

import math
import os
import struct
import zlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = (
    ROOT_DIR
    / "iso-builder"
    / "config"
    / "includes.chroot"
    / "usr"
    / "share"
    / "backgrounds"
    / "ubuntu-nido"
)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def write_rgb_png(path: Path, width: int, height: int, rgb: bytearray) -> None:
    """Write an RGB24 (color type 2) PNG with valid headers and scanlines."""
    stride = width * 3
    if len(rgb) != stride * height:
        raise ValueError(f"Expected {stride * height} bytes, got {len(rgb)}")

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # Filter type 0 (None)
        start = y * stride
        raw.extend(rgb[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=6)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def make_gradient_canvas(
    width: int,
    height: int,
    top_rgb: tuple[int, int, int],
    bot_rgb: tuple[int, int, int],
) -> bytearray:
    """Build a vertical gradient RGB buffer fast by repeating row patterns."""
    buf = bytearray(width * height * 3)
    stride = width * 3
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(top_rgb[0] + (bot_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bot_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bot_rgb[2] - top_rgb[2]) * t)
        row = bytes((r, g, b)) * width
        buf[y * stride : (y + 1) * stride] = row
    return buf


def blend_pixel(
    buf: bytearray, width: int, x: int, y: int, color: tuple[int, int, int], alpha: float
) -> None:
    if alpha <= 0.0:
        return
    if alpha >= 1.0:
        idx = (y * width + x) * 3
        buf[idx] = color[0]
        buf[idx + 1] = color[1]
        buf[idx + 2] = color[2]
        return
    idx = (y * width + x) * 3
    inv = 1.0 - alpha
    buf[idx] = int(buf[idx] * inv + color[0] * alpha)
    buf[idx + 1] = int(buf[idx + 1] * inv + color[1] * alpha)
    buf[idx + 2] = int(buf[idx + 2] * inv + color[2] * alpha)


def draw_ring(
    buf: bytearray,
    width: int,
    height: int,
    cx: float,
    cy: float,
    radius: float,
    thickness: float,
    color: tuple[int, int, int],
    opacity: float = 1.0,
) -> None:
    half = thickness * 0.5
    x0 = max(0, int(math.floor(cx - radius - half - 2)))
    x1 = min(width - 1, int(math.ceil(cx + radius + half + 2)))
    y0 = max(0, int(math.floor(cy - radius - half - 2)))
    y1 = min(height - 1, int(math.ceil(cy + radius + half + 2)))
    for y in range(y0, y1 + 1):
        dy = y - cy
        for x in range(x0, x1 + 1):
            dx = x - cx
            dist = abs(math.hypot(dx, dy) - radius)
            if dist <= half + 1.0:
                cov = max(0.0, min(1.0, half + 0.5 - dist)) * opacity
                blend_pixel(buf, width, x, y, color, cov)


def draw_disc(
    buf: bytearray,
    width: int,
    height: int,
    cx: float,
    cy: float,
    radius: float,
    color: tuple[int, int, int],
    opacity: float = 1.0,
) -> None:
    x0 = max(0, int(math.floor(cx - radius - 2)))
    x1 = min(width - 1, int(math.ceil(cx + radius + 2)))
    y0 = max(0, int(math.floor(cy - radius - 2)))
    y1 = min(height - 1, int(math.ceil(cy + radius + 2)))
    for y in range(y0, y1 + 1):
        dy = y - cy
        for x in range(x0, x1 + 1):
            dx = x - cx
            dist = math.hypot(dx, dy) - radius
            if dist <= 1.0:
                cov = max(0.0, min(1.0, 0.5 - dist)) * opacity
                blend_pixel(buf, width, x, y, color, cov)


def draw_nido_emblem(
    buf: bytearray, width: int, height: int, cx: float, cy: float, scale: float
) -> None:
    amber = (245, 158, 11)
    emerald = (16, 185, 129)
    gold = (251, 191, 36)
    dark = (20, 24, 32)

    r_ring = 68.0 * scale
    draw_ring(buf, width, height, cx, cy, r_ring, 10.0 * scale, amber, 0.95)
    draw_ring(buf, width, height, cx, cy, r_ring * 0.62, 6.0 * scale, emerald, 0.9)

    nodes = [
        (0.0, -r_ring, amber),
        (r_ring * math.cos(math.radians(30)), r_ring * math.sin(math.radians(30)), emerald),
        (-r_ring * math.cos(math.radians(30)), r_ring * math.sin(math.radians(30)), amber),
    ]
    for nx, ny, col in nodes:
        draw_disc(buf, width, height, cx + nx, cy + ny, 14.0 * scale, dark, 1.0)
        draw_disc(buf, width, height, cx + nx, cy + ny, 9.5 * scale, col, 1.0)

    draw_disc(buf, width, height, cx, cy, 10.0 * scale, gold, 1.0)


def generate_all(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    # 1. Plymouth watermark.png (256x256, pure black bg matching BackgroundStartColor=0x000000)
    w, h = 256, 256
    wm = make_gradient_canvas(w, h, (0, 0, 0), (0, 0, 0))
    draw_nido_emblem(wm, w, h, w / 2.0, h / 2.0, 1.25)
    p_wm = out_dir / "watermark.png"
    write_rgb_png(p_wm, w, h, wm)
    generated.append(p_wm)

    # 2. GRUB/ISOLINUX splash.png (640x480 required for vesamenu.c32 & GRUB padding)
    w, h = 640, 480
    sp = make_gradient_canvas(w, h, (15, 20, 27), (22, 32, 44))
    draw_ring(sp, w, h, w / 2.0, h * 0.35, 130.0, 2.0, (245, 158, 11), 0.25)
    draw_nido_emblem(sp, w, h, w / 2.0, h * 0.35, 1.0)
    p_sp = out_dir / "splash.png"
    write_rgb_png(p_sp, w, h, sp)
    generated.append(p_sp)

    # 3. GNOME desktop wallpaper.png (1920x1080)
    w, h = 1920, 1080
    wp = make_gradient_canvas(w, h, (15, 20, 27), (28, 25, 36))
    draw_ring(wp, w, h, w / 2.0, h / 2.0, 290.0, 2.5, (245, 158, 11), 0.20)
    draw_ring(wp, w, h, w / 2.0, h / 2.0, 430.0, 1.5, (16, 185, 129), 0.14)
    draw_nido_emblem(wp, w, h, w / 2.0, h / 2.0, 1.85)
    p_wp = out_dir / "wallpaper.png"
    write_rgb_png(p_wp, w, h, wp)
    generated.append(p_wp)

    return generated


def main() -> None:
    target = Path(os.environ.get("NIDO_ASSETS_DIR", str(OUT_DIR)))
    files = generate_all(target)
    for f in files:
        print(f"Generated {f} ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
