<p align="center">
  <img src="branding/logo-nidoos.svg" width="96" alt="NidoOS" />
</p>

<h1 align="center">NidoOS</h1>

<p align="center">
  <strong>English</strong> · <a href="docs/README.vi.md">Tiếng Việt</a>
</p>

**NidoOS** is an AI-native Linux desktop operating system built on Ubuntu 26.04 LTS with **Sen Việt** Vietnamese input (native IBus Telex & VNI), **NidoOffice**, **NidoBox**, and **NidoAgent** built in.

## Downloads

| Architecture | Installer | Target Hardware |
| :--- | :--- | :--- |
| **`amd64` (`x86_64`)** | [**NidoOS-desktop-amd64.iso**](https://github.com/stevedat/NidoHub/releases/latest/download/NidoOS-desktop-amd64.iso) | Intel / AMD PCs & Laptops (Recommended) |
| **`arm64` (`aarch64`)** | [**NidoOS-desktop-arm64.iso**](https://github.com/stevedat/NidoHub/releases/latest/download/NidoOS-desktop-arm64.iso) | Apple Silicon VMs & ARM64 systems (Beta) |

Verify checksums with [`SHA256SUMS.txt`](https://github.com/stevedat/NidoHub/releases/latest/download/SHA256SUMS.txt):

```sh
sha256sum -c SHA256SUMS.txt
```

## Highlights

- **Nido Minimal Desktop (Ubuntu 26.04 LTS Base + GNOME Minimal):** Clean GNOME Wayland desktop (GDM, Nautilus, Settings, PipeWire) with full Vietnamese diacritic typography (`Be Vietnam Pro` + `Noto Sans`) and zero preinstalled bloat — installable via ISO or directly onto existing LTS systems with `nido-desktop`.
- **Sen Việt Input (IBus Telex & VNI):** Pre-activated Vietnamese Telex and VNI over native GNOME IBus across Wayland and X11 on first login — zero manual setup required.
- **Mandatory AI ON/OFF Setup:** First-login guided onboarding lets users turn AI ON (signing in to at least one provider: Google/Gemini, ChatGPT, or Claude, plus optional API keys) or turn AI OFF completely.
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
