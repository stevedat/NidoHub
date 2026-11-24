# Third-Party & Upstream Provenance (NidoOS v0.2.0)

This ledger records every upstream base system, first-party external package, bundled binary/asset, and curated vendor repository consumed by NidoOS (`amd64` and `arm64`).

## 1. Base OS & First-Party Packages

| Component | Version / Suite | Upstream Source | License | Integrity Verification |
| --- | --- | --- | --- | --- |
| Ubuntu Desktop LTS | `26.04` (`resolute`) / `24.04` (`noble`) | `https://ubuntu.com` | Mixed OSI | Official Ubuntu archive GPG keys (`archive.ubuntu.com`, `ports.ubuntu.com`, `security.ubuntu.com`; optional `mirror.bizflycloud.vn` on `amd64` only) |
| Sen Việt (`vielotus`) | `>= 2.0.8` | `https://github.com/stevedat/VieLotus-IM` | MIT | GPG-signed NidoOS APT repo + release SHA-256 check (`0150-vielotus-im.hook.chroot`) |
| NidoBox (`nidobox`) | `>= 1.4.7` | `https://ubuntu-nido-apt.pages.dev` | NidoOS First-Party | GPG-signed NidoOS APT repo (`0160-nidobox.hook.chroot`) |
| NidoOffice Desktop | `0.13.1` (tracks latest release) | `https://github.com/stevedat/nidooffice-releases` | NidoOS First-Party | Release `SHA256SUMS.txt` verification in `install-runtime.sh` (`0170-nidooffice-desktop.hook.chroot`) |

## 2. Bundled Upstream Tools, Fonts & Artwork

| Component | Version / Ref | Upstream Source | License | Integrity / Hygiene Rule |
| --- | --- | --- | --- | --- |
| Geary | Ubuntu archive | `https://gitlab.gnome.org/GNOME/geary` | LGPL-2.1-or-later | Ubuntu APT signed archive (`0180-nido-email-stack.hook.chroot`) |
| Himalaya CLI | `v2.2.1` | `https://github.com/pimalaya/himalaya` | MIT | Pinned `amd64` & `arm64` SHA-256 digests in `0180-nido-email-stack.hook.chroot` |
| Node.js | `>= 22.x` | `https://nodejs.org` / `https://deb.nodesource.com` | MIT | Ubuntu APT preferred; NodeSource 22.x fallback (`customize.sh`, `0200-nido-ai-stack.hook.chroot`) |
| Be Vietnam Pro | Static TTF (`main`) | `https://github.com/bettergui/BeVietnamPro` | SIL OFL 1.1 | Static `fonts/ttf/*.ttf` only; variable `*[wght].ttf` excluded (`0100-nido-gnome-setup.hook.chroot`) |
| Noto Sans & Liberation Fonts | Ubuntu archive | `https://fonts.google.com/noto` / `https://github.com/liberationfonts/liberation-fonts` | SIL OFL 1.1 | Ubuntu APT signed packages + Vietnamese cmap audit (`customize.sh`, `bind-installer-fonts`) |
| Bibata Modern Classic Cursor | `v2.0.6` | `https://github.com/ful1e5/Bibata_Cursor` | GPL-3.0 | Version-pinned GitHub Release tarball (`0100-nido-gnome-setup.hook.chroot`) |

## 3. Curated Vendor Repositories (`vendors.json` & Flatpak)

All third-party APT vendors configured through `nido-hub` (`packages/nido-hub/files/usr/share/nido-hub/vendors.json`) require explicit OpenPGP fingerprint pinning and APT package allowlist pinning:

| Vendor | Repository URI | License | Pinned GPG Fingerprint(s) / Verification |
| --- | --- | --- | --- |
| Mozilla Firefox DEB | `https://packages.mozilla.org/apt` | MPL-2.0 | `35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3` (priority 1000, blocks Snap stub) |
| Google Chrome | `https://dl.google.com/linux/chrome/deb` | Proprietary | `EB4C1BFD4F042F6DDDCCEC917721F63BD38B4796`, `3B068FB4789ABE4AEFA3BB491397BC53640DB551` |
| Brave Browser | `https://brave-browser-apt-release.s3.brave.com` | MPL-2.0 | `DBF1A116C220B8C7164F98230686B78420038257`, `47D32A74E9A9E013A4B4926C68D513D36A73CD96` |
| Visual Studio Code | `https://packages.microsoft.com/repos/code` | Proprietary | `BC528686B50D79E339D3721CEB3E94ADBE1229CF` |
| Docker CE | `https://download.docker.com/linux/ubuntu` | Apache-2.0 | `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`, `D3306A018370199E527AE7997EA0A9C3F273FCD8` |
| Flathub (fallback remote) | `https://dl.flathub.org/repo/flathub.flatpakrepo` | Mixed | Flatpak GPG repo verification + host-arch filtering in `app_store.py` |
