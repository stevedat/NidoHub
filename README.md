<p align="center">
  <img src="branding/logo-ubuntu-nido.svg" width="96" alt="Ubuntu-Nido" />
</p>

<h1 align="center">Ubuntu-Nido</h1>

<p align="center">
  <strong>English</strong> · <a href="docs/README.vi.md">Tiếng Việt</a>
</p>

**Ubuntu-Nido** is an Ubuntu LTS desktop distribution with **Sen Việt** Vietnamese input and **NidoAgent** built in.

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

- **Ubuntu LTS + GNOME:** Official Ubuntu hardware support, packages, and security updates.
- **Sen Việt Input:** Ready-to-type Vietnamese Telex & VNI across Wayland and X11.
- **NidoAgent:** Trigger AI-assisted tasks from keyboard (`Super+Space`), terminal (`nidoagent`), or Files.
- **On-Device Control:** Bring your own API keys or connect local models.

## Build

```sh
make check && make packages && make iso
```

## License

Licensed under the [GNU General Public License v3.0](LICENSE).
