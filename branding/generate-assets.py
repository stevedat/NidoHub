#!/usr/bin/env python3
"""Generate deterministic PNG/SVG branding assets for NidoOS.

Uses Python standard library only (zlib + struct) so it runs cleanly on
macOS, Ubuntu chroot, and CI runners without Pillow or ImageMagick.
"""

from __future__ import annotations

import argparse
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

NIDOOS_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <defs>
    <linearGradient id="nidoBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1E293B" />
      <stop offset="100%" stop-color="#0B1120" />
    </linearGradient>
    <linearGradient id="nidoAmber" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FBBF24" />
      <stop offset="100%" stop-color="#EA580C" />
    </linearGradient>
    <linearGradient id="nidoEmerald" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#059669" />
      <stop offset="100%" stop-color="#34D399" />
    </linearGradient>
  </defs>
  <rect x="12" y="12" width="232" height="232" rx="54" fill="url(#nidoBg)" stroke="#334155" stroke-width="4" />
  <rect x="64" y="64" width="28" height="128" rx="14" fill="url(#nidoEmerald)" />
  <rect x="164" y="64" width="28" height="128" rx="14" fill="#E2E8F0" />
  <path d="M78 78 L178 178" stroke="url(#nidoAmber)" stroke-width="30" stroke-linecap="round" />
  <circle cx="178" cy="78" r="11" fill="#FBBF24" />
</svg>
"""


NIDOOS_SYMBOLIC_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
  <rect x="2.2" y="2" width="2.4" height="12" rx="1.2" fill="#bebebe" />
  <rect x="11.4" y="2" width="2.4" height="12" rx="1.2" fill="#bebebe" />
  <path d="M3.4 3.2 L12.6 12.8" stroke="#bebebe" stroke-width="2.4" stroke-linecap="round" />
</svg>
"""

NIDOOS_APP_GRID_SYMBOLIC_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
  <rect x="2" y="2" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="6.5" y="2" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="11" y="2" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="2" y="6.5" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="6.5" y="6.5" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="11" y="6.5" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="2" y="11" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="6.5" y="11" width="3" height="3" rx="1" fill="#bebebe" />
  <rect x="11" y="11" width="3" height="3" rx="1" fill="#bebebe" />
</svg>
"""

NIDOBOX_SYMBOLIC_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" fill="none">
  <path d="M8 1.6 L13.8 4.8 V11.2 L8 14.4 L2.2 11.2 V4.8 Z" stroke="#bebebe" stroke-width="1.5" stroke-linejoin="round" />
  <path d="M2.4 5.0 L8 8.1 L13.6 5.0 M8 8.1 V14.2" stroke="#bebebe" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
</svg>
"""


def _nidoos_wordmark_svg(dark: bool) -> str:
    """Pure-geometry NidoOS crest + vector path wordmark (zero <text> tags for flutter_svg)."""
    fg = "#F8FAFC" if dark else "#0F172A"
    accent = "#10B981" if dark else "#0D9488"
    badge_stroke = "#334155" if dark else "#CBD5E1"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 120" width="360" height="120">
  <defs>
    <linearGradient id="wlBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#1E293B" />
      <stop offset="100%" stop-color="#0B1120" />
    </linearGradient>
    <linearGradient id="wlAmber" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FBBF24" />
      <stop offset="100%" stop-color="#EA580C" />
    </linearGradient>
    <linearGradient id="wlEmerald" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#059669" />
      <stop offset="100%" stop-color="#34D399" />
    </linearGradient>
  </defs>
  <g transform="translate(8 10) scale(0.39)">
    <rect x="12" y="12" width="232" height="232" rx="54" fill="url(#wlBg)" stroke="{badge_stroke}" stroke-width="5" />
    <rect x="64" y="64" width="28" height="128" rx="14" fill="url(#wlEmerald)" />
    <rect x="164" y="64" width="28" height="128" rx="14" fill="#E2E8F0" />
    <path d="M78 78 L178 178" stroke="url(#wlAmber)" stroke-width="30" stroke-linecap="round" />
    <circle cx="178" cy="78" r="11" fill="#FBBF24" />
  </g>
  <!-- Pure vector geometric wordmark: NidoOS -->
  <g fill="none" stroke="{fg}" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round">
    <!-- N -->
    <path d="M128 82 V38 L156 82 V38" />
    <!-- i -->
    <path d="M174 52 V82" />
    <!-- d -->
    <path d="M214 38 V82" />
    <circle cx="201" cy="67" r="13.5" />
    <!-- o -->
    <circle cx="238" cy="67" r="13.5" />
  </g>
  <circle cx="174" cy="40" r="4.2" fill="{accent}" />
  <g fill="none" stroke="{accent}" stroke-width="6.8" stroke-linecap="round" stroke-linejoin="round">
    <!-- O -->
    <circle cx="282" cy="60" r="21" />
    <!-- S -->
    <path d="M342 46 C339 40 331 38 323 39 C315 40 311 45 312 51 C313 57 320 59 329 61 C338 63 344 66 344 73 C344 80 336 83 326 83 C317 83 311 79 309 74" />
  </g>
</svg>
"""


NIDOOS_STORAGE_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <rect x="4" y="4" width="56" height="56" rx="14" fill="#0F172A" stroke="#10B981" stroke-width="2.5" />
  <rect x="17" y="17" width="7" height="30" rx="3.5" fill="#10B981" />
  <rect x="40" y="17" width="7" height="30" rx="3.5" fill="#E2E8F0" />
  <path d="M20.5 20.5 L43.5 43.5" stroke="#F59E0B" stroke-width="7.5" stroke-linecap="round" />
</svg>
"""


INSTALLER_SLIDES = [
    (
        "Chào mừng đến với NidoOS",
        "Hệ điều hành tối giản, hiện đại trên nền tảng Ubuntu LTS 26.04.",
        "Giao diện GNOME tinh gọn với thanh Dock nổi ở đáy, bảng màu Xanh ngọc – Tối dịu mắt và tối ưu sẵn cho người dùng Việt Nam.",
        "Welcome to NidoOS",
        "A clean, modern AI-native desktop built on Ubuntu 26.04 LTS.",
        "Thoughtfully arranged GNOME layout with floating dock, emerald accents, and zero preinstalled bloat.",
    ),
    (
        "Tiếng Việt chuẩn ngay từ lần mở máy",
        "Bộ gõ Sen Việt (Telex & VNI) và font Be Vietnam Pro được cấu hình sẵn.",
        "Chuyển nhanh Telex / VNI / Tiếng Anh bằng phím Super+Space trên cả Wayland và X11 mà không cần cài đặt thêm.",
        "Vietnamese ready out of the box",
        "Sen Viet (Telex & VNI) input method and Be Vietnam Pro typography pre-configured.",
        "Switch seamlessly with Super+Space across native desktop apps, browsers, and terminals.",
    ),
    (
        "NidoAgent AI & Email thông minh",
        "Bật hoặc tắt AI hoàn toàn theo quyền kiểm soát của bạn.",
        "Tích hợp NidoAI Gateway cục bộ, Geary Mail gọn nhẹ cùng Himalaya CLI Skill giúp AI hỗ trợ tóm tắt và soạn thảo thư an toàn.",
        "NidoAgent AI & lightweight Email",
        "Full user control over AI ON/OFF and personal provider accounts.",
        "Includes local NidoAI Gateway, lightweight Geary Mail, and Himalaya CLI skill for AI-assisted inbox workflows.",
    ),
    (
        "NidoOffice & NidoBox sẵn sàng làm việc",
        "Soạn thảo tài liệu Word, Excel, Slide, PDF và công cụ hệ thống Việt hóa.",
        "Hoàn thiện với NidoHub để quản lý ứng dụng bản địa .deb, cập nhật hệ thống và đồng bộ kỹ năng cho trợ lý AI.",
        "NidoOffice & NidoBox ready to work",
        "Native Office documents, OCR capture, LAN sharing, and Vietnamese archive tools.",
        "Manage native apps, updates, and AI skills from one place in NidoHub.",
    ),
]


def _render_slide_html(title: str, lead: str, body: str) -> str:
    return f"""<html>
  <head>
    <style>
body {{
  height: 600px;
  font-family: "Noto Sans", "Be Vietnam Pro", sans-serif;
}}
img {{
  height: 280px;
  width: 280px;
}}
table {{
  padding: 16% 80px;
  height: 600px;
}}
p {{
  margin-bottom: 12px;
  line-height: 1.5;
}}
.h1 {{
  font-size: 34px;
  font-weight: 700;
}}
.lead {{
  font-size: 18px;
  font-weight: 600;
}}
    </style>
  </head>
  <body>
    <table>
      <tr>
        <td style="padding-right: 36px;">
          <p class="h1">{title}</p>
          <p class="lead">{lead}</p>
          <p>{body}</p>
        </td>
        <td>
          <img src="nidoos-emblem.svg" />
        </td>
      </tr>
    </table>
  </body>
</html>
"""


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
        raw.append(0)
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


def write_rgba_png(path: Path, width: int, height: int, rgba: bytearray) -> None:
    """Write an RGBA32 (color type 6) PNG with valid headers and scanlines."""
    stride = width * 4
    if len(rgba) != stride * height:
        raise ValueError(f"Expected {stride * height} bytes, got {len(rgba)}")

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(rgba[start : start + stride])

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
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


def draw_rounded_box(
    buf: bytearray,
    width: int,
    height: int,
    cx: float,
    cy: float,
    half_w: float,
    half_h: float,
    corner_r: float,
    fill_color: tuple[int, int, int] | None = None,
    fill_opacity: float = 1.0,
    stroke_color: tuple[int, int, int] | None = None,
    stroke_width: float = 0.0,
    stroke_opacity: float = 1.0,
) -> None:
    pad = stroke_width + 3.0
    x0 = max(0, int(math.floor(cx - half_w - pad)))
    x1 = min(width - 1, int(math.ceil(cx + half_w + pad)))
    y0 = max(0, int(math.floor(cy - half_h - pad)))
    y1 = min(height - 1, int(math.ceil(cy + half_h + pad)))
    bx = max(0.0, half_w - corner_r)
    by = max(0.0, half_h - corner_r)
    half_sw = stroke_width * 0.5
    for y in range(y0, y1 + 1):
        dy = abs(y - cy) - by
        for x in range(x0, x1 + 1):
            dx = abs(x - cx) - bx
            dist = math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - corner_r
            if fill_color is not None and dist <= 1.0:
                cov = max(0.0, min(1.0, 0.5 - dist)) * fill_opacity
                blend_pixel(buf, width, x, y, fill_color, cov)
            if stroke_color is not None and stroke_width > 0.0:
                sdist = abs(dist) - half_sw
                if sdist <= 1.0:
                    scov = max(0.0, min(1.0, 0.5 - sdist)) * stroke_opacity
                    blend_pixel(buf, width, x, y, stroke_color, scov)


def draw_capsule(
    buf: bytearray,
    width: int,
    height: int,
    x_a: float,
    y_a: float,
    x_b: float,
    y_b: float,
    radius: float,
    color_a: tuple[int, int, int],
    color_b: tuple[int, int, int] | None = None,
    opacity: float = 1.0,
) -> None:
    """Draw an anti-aliased rounded line segment (capsule) with optional linear gradient."""
    c_b = color_b or color_a
    vx = x_b - x_a
    vy = y_b - y_a
    len_sq = vx * vx + vy * vy
    pad = radius + 2.0
    x0 = max(0, int(math.floor(min(x_a, x_b) - pad)))
    x1 = min(width - 1, int(math.ceil(max(x_a, x_b) + pad)))
    y0 = max(0, int(math.floor(min(y_a, y_b) - pad)))
    y1 = min(height - 1, int(math.ceil(max(y_a, y_b) + pad)))
    for y in range(y0, y1 + 1):
        py = y - y_a
        for x in range(x0, x1 + 1):
            px = x - x_a
            t = 0.0 if len_sq <= 1e-6 else max(0.0, min(1.0, (px * vx + py * vy) / len_sq))
            dist = math.hypot(px - vx * t, py - vy * t) - radius
            if dist <= 1.0:
                cov = max(0.0, min(1.0, 0.5 - dist)) * opacity
                col = (
                    int(color_a[0] + (c_b[0] - color_a[0]) * t),
                    int(color_a[1] + (c_b[1] - color_a[1]) * t),
                    int(color_a[2] + (c_b[2] - color_a[2]) * t),
                )
                blend_pixel(buf, width, x, y, col, cov)


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
    buf: bytearray,
    width: int,
    height: int,
    cx: float,
    cy: float,
    scale: float,
    with_badge: bool = True,
) -> None:
    """Draw the distinct geometric NidoOS 'N' crest emblem (zero Ubuntu resemblance)."""
    emerald_bot = (5, 150, 105)
    emerald_top = (52, 211, 153)
    slate_light = (226, 232, 240)
    amber_top = (251, 191, 36)
    amber_bot = (234, 88, 12)
    gold = (251, 191, 36)

    if with_badge:
        draw_rounded_box(
            buf,
            width,
            height,
            cx,
            cy,
            82.0 * scale,
            82.0 * scale,
            22.0 * scale,
            fill_color=(17, 24, 39),
            fill_opacity=0.96,
            stroke_color=(51, 65, 85),
            stroke_width=2.5 * scale,
            stroke_opacity=0.95,
        )

    dx = 36.0 * scale
    dy = 36.0 * scale
    stem_r = 10.0 * scale
    diag_r = 10.8 * scale

    # Left vertical pillar (emerald gradient)
    draw_capsule(buf, width, height, cx - dx, cy + dy, cx - dx, cy - dy, stem_r, emerald_bot, emerald_top, 1.0)
    # Right vertical pillar (crisp silver-slate)
    draw_capsule(buf, width, height, cx + dx, cy - dy, cx + dx, cy + dy, stem_r, slate_light, slate_light, 1.0)
    # Diagonal N crossbar (warm amber-to-orange)
    draw_capsule(buf, width, height, cx - dx, cy - dy, cx + dx, cy + dy, diag_r, amber_top, amber_bot, 1.0)
    # Top-right spark accent
    draw_disc(buf, width, height, cx + dx, cy - dy, 6.2 * scale, gold, 1.0)


def render_nidoos_badge_rgba(size: int) -> bytearray:
    """Render a transparent RGBA32 rounded squircle NidoOS crest icon."""
    w = h = size
    rgb = make_gradient_canvas(w, h, (30, 41, 59), (11, 17, 32))
    scale = size / 256.0
    draw_nido_emblem(rgb, w, h, w * 0.5, h * 0.5, scale * 1.38, with_badge=False)

    cx, cy = (w - 1) * 0.5, (h - 1) * 0.5
    half_box = 114.0 * scale
    corner_r = 27.0 * scale
    rgba = bytearray(w * h * 4)
    for y in range(h):
        dy = abs(y - cy) - (half_box - corner_r)
        for x in range(w):
            dx = abs(x - cx) - (half_box - corner_r)
            dist = math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - corner_r
            cov = max(0.0, min(1.0, 0.5 - dist))
            r_val = rgb[(y * w + x) * 3]
            g_val = rgb[(y * w + x) * 3 + 1]
            b_val = rgb[(y * w + x) * 3 + 2]
            if -2.5 * scale <= dist <= 0.6 * scale:
                rim = max(0.0, 1.0 - abs(dist + 0.9 * scale) / max(1.4 * scale, 0.5)) * 0.6
                r_val = int(r_val * (1.0 - rim) + 51 * rim)
                g_val = int(g_val * (1.0 - rim) + 65 * rim)
                b_val = int(b_val * (1.0 - rim) + 85 * rim)
            oi = (y * w + x) * 4
            rgba[oi] = r_val
            rgba[oi + 1] = g_val
            rgba[oi + 2] = b_val
            rgba[oi + 3] = int(cov * 255)
    return rgba


def generate_all(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    # 1. Plymouth watermark.png (256x256, pure black bg matching BackgroundStartColor=0x000000)
    w, h = 256, 256
    wm = make_gradient_canvas(w, h, (0, 0, 0), (0, 0, 0))
    draw_nido_emblem(wm, w, h, w / 2.0, h / 2.0, 1.15, with_badge=True)
    p_wm = out_dir / "watermark.png"
    write_rgb_png(p_wm, w, h, wm)
    generated.append(p_wm)

    # 2. GRUB/ISOLINUX splash.png (640x480 required for vesamenu.c32 & GRUB padding)
    w, h = 640, 480
    sp = make_gradient_canvas(w, h, (11, 17, 32), (20, 30, 48))
    draw_rounded_box(
        sp, w, h, w / 2.0, h * 0.36, 118.0, 118.0, 32.0,
        stroke_color=(245, 158, 11), stroke_width=2.0, stroke_opacity=0.28,
    )
    draw_nido_emblem(sp, w, h, w / 2.0, h * 0.36, 0.96, with_badge=True)
    p_sp = out_dir / "splash.png"
    write_rgb_png(p_sp, w, h, sp)
    generated.append(p_sp)

    # 3. GNOME desktop wallpaper.png (1920x1080) with Teal/Emerald NidoOS glow
    w, h = 1920, 1080
    wp = make_gradient_canvas(w, h, (9, 17, 29), (15, 23, 42))
    draw_rounded_box(
        wp, w, h, w / 2.0, h / 2.0, 260.0, 260.0, 72.0,
        stroke_color=(16, 185, 129), stroke_width=2.5, stroke_opacity=0.22,
    )
    draw_rounded_box(
        wp, w, h, w / 2.0, h / 2.0, 380.0, 380.0, 102.0,
        stroke_color=(13, 148, 136), stroke_width=1.8, stroke_opacity=0.14,
    )
    draw_nido_emblem(wp, w, h, w / 2.0, h / 2.0, 1.65, with_badge=True)
    p_wp = out_dir / "wallpaper.png"
    write_rgb_png(p_wp, w, h, wp)
    generated.append(p_wp)

    # 4. Transparent RGBA32 NidoOS logo (160x160) for initrd Plymouth overlay, GDM & About
    p_logo = out_dir / "nidoos-logo.png"
    write_rgba_png(p_logo, 160, 160, render_nidoos_badge_rgba(160))
    generated.append(p_logo)

    return generated


def generate_hub_icons(hicolor_dir: Path) -> list[Path]:
    """Generate scalable SVG and 256x256 RGBA PNG app & OS brand icons."""
    generated: list[Path] = []
    rgba_256 = render_nidoos_badge_rgba(256)
    for icon_name in ("com.nidoholdings.NidoHub", "nidoos-logo", "distributor-logo"):
        svg_path = hicolor_dir / "scalable" / "apps" / f"{icon_name}.svg"
        png_path = hicolor_dir / "256x256" / "apps" / f"{icon_name}.png"
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        svg_path.write_text(NIDOOS_LOGO_SVG, encoding="utf-8")
        write_rgba_png(png_path, 256, 256, rgba_256)
        generated.extend([svg_path, png_path])

    sym_path = hicolor_dir / "scalable" / "apps" / "nidoos-symbolic.svg"
    sym_path.write_text(NIDOOS_SYMBOLIC_SVG, encoding="utf-8")
    grid_path = hicolor_dir / "scalable" / "actions" / "nidoos-app-grid-symbolic.svg"
    grid_path.parent.mkdir(parents=True, exist_ok=True)
    grid_path.write_text(NIDOOS_APP_GRID_SYMBOLIC_SVG, encoding="utf-8")
    generated.extend([sym_path, grid_path])
    return generated


def generate_desktop_icons(hicolor_dir: Path) -> list[Path]:
    """Generate monochrome desktop top-bar symbolic icons into hicolor icon theme."""
    generated: list[Path] = []
    for subdir in ("status", "apps"):
        icon_path = hicolor_dir / "scalable" / subdir / "nidobox-symbolic.svg"
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_path.write_text(NIDOBOX_SYMBOLIC_SVG, encoding="utf-8")
        generated.append(icon_path)
    return generated


def generate_desktop_provision_assets(provision_dir: Path) -> list[Path]:
    """Generate white-label SVG logos & localized HTML slides for ubuntu-desktop-bootstrap."""
    generated: list[Path] = []
    images_dir = provision_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    image_files = {
        "nidoos-logo-light.svg": _nidoos_wordmark_svg(dark=False),
        "nidoos-logo-dark.svg": _nidoos_wordmark_svg(dark=True),
        "nidoos-mascot-light.svg": NIDOOS_LOGO_SVG,
        "nidoos-mascot-dark.svg": NIDOOS_LOGO_SVG,
        "nidoos-storage-icon.svg": NIDOOS_STORAGE_ICON_SVG,
    }
    for fname, svg_text in image_files.items():
        p = images_dir / fname
        p.write_text(svg_text, encoding="utf-8")
        generated.append(p)

    for idx, (vi_title, vi_lead, vi_body, en_title, en_lead, en_body) in enumerate(
        INSTALLER_SLIDES, start=1
    ):
        sdir = provision_dir / "slides" / str(idx)
        sdir.mkdir(parents=True, exist_ok=True)
        emblem = sdir / "nidoos-emblem.svg"
        emblem.write_text(NIDOOS_LOGO_SVG, encoding="utf-8")
        vi_html_text = _render_slide_html(vi_title, vi_lead, vi_body)
        vi_html = sdir / "slide_vi_VN.html"
        vi_html.write_text(vi_html_text, encoding="utf-8")
        # Also write slide_vi.html because ubuntu-desktop-bootstrap's slides_provider
        # looks up slide_${Intl.defaultLocale}.html, which is 'vi' when selected in UI.
        vi_short_html = sdir / "slide_vi.html"
        vi_short_html.write_text(vi_html_text, encoding="utf-8")
        en_html = sdir / "slide_en_US.html"
        en_html.write_text(_render_slide_html(en_title, en_lead, en_body), encoding="utf-8")
        generated.extend([emblem, vi_html, vi_short_html, en_html])

    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate NidoOS branding PNG/SVG assets.")
    parser.add_argument(
        "--hub-icon-dir",
        type=Path,
        default=None,
        help="Optional hicolor icon theme root to write NidoHub & NidoOS icons into.",
    )
    parser.add_argument(
        "--desktop-icon-dir",
        type=Path,
        default=None,
        help="Optional hicolor icon theme root to write NidoOS desktop top-bar icons into.",
    )
    parser.add_argument(
        "--desktop-provision-dir",
        type=Path,
        default=None,
        help="Optional /usr/share/desktop-provision staging dir for installer white-label SVGs & slides.",
    )
    args = parser.parse_args()

    if args.hub_icon_dir is not None:
        files = generate_hub_icons(args.hub_icon_dir)
    elif args.desktop_icon_dir is not None:
        files = generate_desktop_icons(args.desktop_icon_dir)
    elif args.desktop_provision_dir is not None:
        files = generate_desktop_provision_assets(args.desktop_provision_dir)
    else:
        target = Path(os.environ.get("NIDO_ASSETS_DIR", str(OUT_DIR)))
        files = generate_all(target)
    for f in files:
        print(f"Generated {f} ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
