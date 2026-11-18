<p align="center">
  <img src="branding/logo-ubuntu-nido.svg" width="96" alt="Ubuntu-Nido" />
</p>

<h1 align="center">Ubuntu-Nido</h1>

<p align="center">
  <strong>English</strong> · <a href="docs/README.vi.md">Tiếng Việt</a>
</p>

**Ubuntu-Nido** is an Ubuntu LTS desktop distribution with **Sen Việt** Vietnamese input (native IBus), **NidoOffice**, **NidoBox**, and **NidoAgent** built in.

## Downloads

| Architecture | Installer | Target Hardware |
| :--- | :--- | :--- |
| **`amd64` (`x86_64`)** | [**Ubuntu-Nido-desktop-amd64.iso**](https://github.com/stevedat/NidoHub/releases/latest/download/Ubuntu-Nido-desktop-amd64.iso) | Intel / AMD PCs & Laptops (Recommended) |
| **`arm64` (`aarch64`)** | [**Ubuntu-Nido-desktop-arm64.iso**](https://github.com/stevedat/NidoHub/releases/latest/download/Ubuntu-Nido-desktop-arm64.iso) | Apple Silicon VMs & ARM64 systems (Beta) |

Verify checksums with [`SHA256SUMS.txt`](https://github.com/stevedat/NidoHub/releases/latest/download/SHA256SUMS.txt):

```sh
sha256sum -c SHA256SUMS.txt
```

## Highlights

- **Nido Minimal Desktop (Ubuntu 26.04 LTS + GNOME Minimal):** Clean GNOME Wayland desktop (GDM, Nautilus, Settings, PipeWire) without preinstalled games or bloat — installable as an ISO or directly on official Ubuntu LTS via the `nido-desktop` APT metapackage.
- **Sen Việt Input (IBus):** Ready-to-type Vietnamese Telex & VNI over native GNOME IBus across Wayland and X11.
- **NidoOffice Suite:** Pre-installed default AI-native office suite for Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), PDF, and Markdown — replacing LibreOffice out of the box.
- **NidoBox Suite:** Native GTK4/Libadwaita & Rust utilities (`Super+Shift+S` Capture Studio + offline Vietnamese OCR, NidoDrop LAN QR transfer, Batch Image, Vietnamese/CJK-safe Archive, and 1-click NTFS dirty-bit unlock).
- **NidoAgent:** Trigger AI-assisted tasks from keyboard (`Super+Space`), terminal (`nidoagent`), dock/hub, or Files.
- **On-Device Control:** Bring your own API keys or connect local models.

## Build

```sh
make check && make packages && make iso
# Fast local ARM64 build (cached base ISO + ext4 workspace + zstd level 1)
EXT_DIR=/path/to/external-workspace make iso-arm64-fast
```

## License

Licensed under the [GNU General Public License v3.0](LICENSE).
