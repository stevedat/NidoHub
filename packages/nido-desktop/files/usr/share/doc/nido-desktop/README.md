# Nido Minimal Desktop (`nido-desktop`)

Architecture: **Ubuntu 26.04 LTS (`ubuntu-desktop-minimal`) + GNOME Wayland + Nautilus + NidoAgent**.

Included packages:
- `nidoai-gateway`: Localhost OpenAI-compatible AI gateway (`127.0.0.1:18090`)
- `nidoagent-cli`: NidoAgent CLI (`Super+Shift+A` quicklaunch & Nautilus scripts)
- `nido-hub`: Native GTK4/Libadwaita AI setup, package hub & document workflows
- `nido-desktop-settings`: GNOME minimal branding, dconf defaults, and welcome autostart
- `nidooffice-cli`: NidoOffice CLI & OOXML engine
- `nidooffice-desktop`: NidoOffice Desktop AI Office Suite (`/opt/NidoOffice`, auto-resolves latest verified GitHub Release; `nidooffice-desktop --check-update` / `--update`)
- `vielotus`: Sen Viet Vietnamese input over native GNOME IBus (`Super+Space`)
- `nidobox`: Native GTK4/Rust utility suite v1.4.7+ (NidoDrop LAN, Capture Studio + OCR, Batch Image, Archive, NTFS repair)
- `showtime | totem`: Native GNOME Video Player (`showtime` on Ubuntu 26.04, `totem` fallback on 24.04)
