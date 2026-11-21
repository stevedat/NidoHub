<p align="center">
  <img src="branding/logo-nidoos.svg" width="96" alt="NidoOS" />
</p>

<h1 align="center">NidoOS</h1>

<p align="center">
  <strong>English</strong> · <a href="docs/README.vi.md">Tiếng Việt</a>
</p>

**NidoOS** is an AI-native Linux desktop operating system built on Ubuntu 26.04 LTS with **Sen Việt** Vietnamese input (native IBus Telex & VNI), **NidoOffice**, **NidoBox**, **Geary + Himalaya AI Email**, and **NidoAgent** built in.

## Downloads

| Architecture | Installer (Fast Edge CDN) | Target Hardware |
| :--- | :--- | :--- |
| **`amd64` (`x86_64`)** | [**NidoOS-desktop-amd64.iso**](https://pub-1972d169061247c2aec05aa7ba69026f.r2.dev/latest/NidoOS-desktop-amd64.iso) | Intel / AMD PCs & Laptops (Recommended) |
| **`arm64` (`aarch64`)** | [**NidoOS-desktop-arm64.iso**](https://pub-1972d169061247c2aec05aa7ba69026f.r2.dev/latest/NidoOS-desktop-arm64.iso) | Apple Silicon VMs & ARM64 systems (Beta) |

Verify checksums with [`SHA256SUMS.txt`](https://pub-1972d169061247c2aec05aa7ba69026f.r2.dev/latest/SHA256SUMS.txt):

```sh
sha256sum -c SHA256SUMS.txt
```

## System Requirements

| Component | Official Minimum | Recommended |
| :--- | :--- | :--- |
| **Intel CPU** | Core i3/i5/i7 Gen 6 (Skylake)+ | Core i Gen 8+ / Core Ultra |
| **AMD CPU** | Ryzen 1000+ | Ryzen 2000+ |
| **RAM** | 6 GB (4 GB experimental only) | 8–16 GB+ |
| **Storage** | 64 GB SSD | 128 GB+ SSD |
| **Graphics** | Compatible integrated GPU | Hardware-accelerated GPU |
| **Architecture & Firmware** | 64-bit `x86_64` or standard UEFI `ARM64` (Apple Silicon VM, Snapdragon X) | UEFI + TPM 2.0 |

- **NidoOS Standard (`Gen 6+ / Ryzen 1000+`, `6–8 GB RAM`):** Designed for offices, schools, NidoOffice, Geary Email, and everyday desktop workflows — includes full Cloud AI (`NidoAgent`, `NidoOffice AI`, `NidoBox OCR`) via OAuth/API.
- **NidoOS AI — Local Offline (`Gen 8+ / Ryzen 2000+`, `16 GB+ RAM`):** Recommended for running local Ollama models offline (model size and speed depend on available RAM and GPU/NPU).
- **Older `x86_64` CPUs:** Intel Core i Gen 1–4 are not officially supported; Gen 5 is experimental only.

## Highlights

- **NidoOS Minimal Desktop (Teal/Emerald GNOME):** Clean GNOME Wayland desktop (floating bottom dock, 1-page organized App Grid, Teal accent theme, GDM, Nautilus, PipeWire) with full Vietnamese diacritic typography across both the Live Installer (`Noto Sans`) and Desktop (`Be Vietnam Pro`), zero Canonical/Ubuntu desktop branding or telemetry clutter, and full package compatibility via `nido-desktop`.
- **Sen Việt Input (IBus Telex & VNI):** Pre-activated Vietnamese Telex and VNI over native GNOME IBus across Wayland and X11 on first login — zero manual setup required (`Super+Space`).
- **Mandatory AI ON/OFF Setup:** First-login guided onboarding (runs automatically after installation, staying unobtrusive during the Live ISO installer) lets users turn AI ON (signing in to at least one provider: Google/Gemini, ChatGPT, or Claude, plus optional API keys) or turn AI OFF completely.
- **Lightweight AI-Ready Email (`Geary` + `Himalaya CLI`):** Replaces heavy Thunderbird Snap with native GTK **Geary** plus the **Himalaya CLI** and `ubuntu-nido-email` OS skill so NidoAgent can summarize inboxes, inspect attachments with NidoOffice, and prepare safe Drafts.
- **NidoOffice Suite:** Pre-installed default AI-native office suite for Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), PDF (including PAdES PKCS#11 USB Token signing), and Markdown — replacing LibreOffice out of the box.
- **NidoBox Suite:** Native GTK4/Libadwaita & Rust utilities (`Super+Shift+S` Capture Studio + offline Vietnamese OCR, NidoDrop LAN QR transfer, Batch Image, Vietnamese/CJK-safe Archive, and 1-click NTFS dirty-bit unlock).
- **NidoAgent:** Trigger AI-assisted tasks from keyboard (`Super+Shift+A`), terminal (`nidoagent`), dock/hub, or Files.

## Build

```sh
make check && make packages && make iso
# Fast local ARM64 build (cached base ISO + ext4 workspace + zstd level 1)
EXT_DIR=/path/to/external-workspace make iso-arm64-fast
```

## Trademark & License

- Licensed under the [GNU General Public License v3.0](LICENSE).
- NidoOS is an independent distribution built on Ubuntu LTS packages and is not affiliated with or endorsed by Canonical Ltd. Ubuntu is a registered trademark of Canonical Ltd.
