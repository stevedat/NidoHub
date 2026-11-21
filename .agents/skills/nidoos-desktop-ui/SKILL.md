---
name: nidoos-desktop-ui
description: >-
  Design, layout, and end-user copywriting guidelines for NidoOS native GTK4/Libadwaita
  desktop applications (NidoHub, NidoBox, onboarding flows). Enforces spacious horizontal
  and vertical geometry, responsive Adw.Clamp sizing, progressive disclosure, Teal-Slate
  visual tokens, and clear jargon-free Vietnamese end-user copy.
---

# NidoOS Desktop UI & End-User Copywriting Skill

Apply this skill whenever creating, reviewing, or refactoring native GTK4 / Libadwaita desktop interfaces or user-facing copy in NidoOS applications (such as `NidoHub`, `NidoBox`, and first-login onboarding).

---

## 1. Spatial Geometry & Responsive Window Budget

Design for breathing room, scannability, and balanced aspect ratios — avoid narrow 500px-feeling columns trapped inside large windows or tall endless scroll lists.

- **Default & Minimum Window Size:**
  - Control centers (multi-tab apps like `NidoHub`): default **`980 × 700`**, minimum **`740 × 540`** via `win.set_default_size(980, 700)` and `win.set_size_request(740, 540)`.
  - Never allow primary controls or side-by-side status tiles to clip horizontally at `740px` width.
- **Content Column Width (`Adw.Clamp`) & Toolbar Alignment:**
  - Wrap main vertical content in `Adw.Clamp(maximum_size=860, tightening_threshold=680)` (or `maximum_size=760` for focused onboarding wizards).
  - **CRITICAL:** Top action toolbars (`apps_ctrl`, search/filter bars, progress bars `op_box`, pinned footers `ob_footer`) **MUST** share the exact same `Adw.Clamp` width (`860` / `760`) as the card stack below them so left/right edges align pixel-for-pixel.
- **Scrolling Discipline (No Double Scroll):**
  - Never wrap `Adw.PreferencesPage` inside a `Gtk.ScrolledWindow` (`Adw.PreferencesPage` already owns an internal `ScrolledWindow` — nesting two creates competing scrollbars and broken mouse-wheel behavior).
  - Instead, build each scrollable tab via one clean helper: `Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)` → `Adw.Clamp(maximum_size=..., tightening_threshold=...)` → `Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)` with generous margins (`top=18, bottom=24, start=20, end=20`), adding `Adw.PreferencesGroup` widgets inside.
- **Horizontal Density vs. Vertical Bloat:**
  - Replace 3–4 stacked summary rows at the top of a page with a **horizontal 3-tile status strip** (`Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)`), each card showing Title + semantic status pill + 1-line clipped detail (`ellipsize=END`).
  - Keep vertical spacing structured: `18px` between major groups, `10–12px` inside compact tiles, `6–8px` between tightly coupled buttons.
- **Progressive Disclosure (`Adw.ExpanderRow`):**
  - Collapse power-user or infrequent settings (manual API keys, local runtime endpoints, model pullers) inside `Adw.ExpanderRow` (`set_expanded(False)` by default) so first-time users see only primary actions immediately.

---

## 2. NidoOS Teal-Slate Visual Tokens (GTK4 / Libadwaita)

Stay native to GNOME Libadwaita (`Yaru-prussiangreen-dark` / `prefer-dark`, `Be Vietnam Pro`) rather than fighting the platform theme with heavy custom chrome.

- **Palette & Surfaces:**
  - Rely on native `@window_bg_color`, `@card_bg_color`, `@window_fg_color`.
  - Brand Teal Accent: `#0D9488` (light) / `#10B981` (dark).
  - Borders & Separators: crisp `1px solid alpha(@window_fg_color, 0.08)` hairlines (especially around status tiles, output frames, and pinned footer bars). Avoid heavy drop shadows or glowing gradients.
- **Semantic Status Badges (`.nido-status-badge`):**
  - **Ready / Active (`.nido-badge-ok`):** background `alpha(#10B981, 0.16)`, text `#34D399` (or `@success_fg_color`).
  - **Needs Attention / Setup (`.nido-badge-warn`):** background `alpha(#F59E0B, 0.16)`, text `#FBBF24`.
  - **Inactive / Muted (`.nido-badge-muted`):** background `alpha(@window_fg_color, 0.08)`, text `alpha(@window_fg_color, 0.72)`.
- **Hierarchy Restraint:**
  - **No fake numbering** (`1.`, `2.`, `3.`) on group titles unless the user must complete strict sequential steps.
  - **No ALL-CAPS eyebrows**, no `[Bracketed]` inline status tags inside row titles (use badges or clean subtitles), and limit **one primary `.suggested-action` CTA** per focal area.
  - Use clear symbolic icons (`preferences-system-symbolic`, `system-software-install-symbolic`, `printer-symbolic`, `system-run-symbolic`).

---

## 3. End-User Vietnamese Copywriting Standard

Write for everyday desktop users (students, office staff, teachers, business owners), **never** for Linux/DevOps engineers.

- **Forbidden Jargon in End-User Titles, Subtitles & Toasts:**
  - Never expose internal plumbing: `127.0.0.1:18090`, `BYOK`, `SHA-512`, `SHA256`, `không cần root`, `Native-First`, `APT Pinning`, `GPG`, `Adapter-First`, `Không bloat ISO`, `CLI chính chủ`, `nido-pkg-helper`, `Socket 9100`, `pkexec`, `--user`.
  - Translate intent directly:
    - *"NidoAI Gateway — Ứng dụng nền (127.0.0.1:18090, BYOK)"* → **`Khóa API cá nhân`**
    - *"Cài 1 chạm CLI chính chủ kèm xác thực SHA-512 (không cần root)"* → **`Đăng nhập một chạm qua trình duyệt bằng tài khoản Google, ChatGPT hoặc Claude`**
    - *"Kho Ứng dụng Native-First (Ưu tiên gói .deb chính chủ)"* → **`Kho ứng dụng`** (*"Ứng dụng được kiểm duyệt và tối ưu cho NidoOS"*)
    - *"Máy in & Máy quét (Adapter-First · Không bloat ISO)"* → **`Máy in đang kết nối`** (*"Tự nhận máy in USB và Wi-Fi hiện đại; chỉ tải thêm trình điều khiển khi dòng máy cần"*)
- **Concise, Scannable Formatting:**
  - **Sentence case** everywhere (`Bật Trợ lý AI`, `Cài đặt nâng cao`, `Thêm máy in mạng văn phòng`).
  - Keep row titles <= 5–7 words and subtitles to **1 clean sentence** (avoid packing 3 facts joined by ` · ` middle dots or nested parentheses).
  - Use **active voice CTAs** that match outcome toasts (`Đăng nhập`, `Đăng xuất`, `Lưu & kiểm tra`, `Cài trình điều khiển`, `Kết nối ngay`, `Chạy tác vụ`).
  - Errors & empty states must state **what happened + next step** plainly without blame or raw stack traces.

---

## 4. Verification Checklist (Before Completing Any UI Change)

1. Verify Python syntax (`python3 -m py_compile ...`) and run static AST checks for undefined names inside GTK callbacks / `Adw` builder blocks.
2. Run headless backend self-tests (`nido-hub --self-test`) verifying constants, markup safety (`escape_markup` / `set_use_markup(False)`), and allowlists.
3. Run repository gates (`make check`) to confirm 0 packaging, security, or hygiene regressions.
4. On live desktop / VM if available, launch the app at both `980×700` and resized down to `740×540` to verify no clipped buttons, double scrollbars, or horizontal overflow.
