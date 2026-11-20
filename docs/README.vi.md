<p align="center">
  <img src="../branding/logo-ubuntu-nido.svg" width="96" alt="Ubuntu-Nido" />
</p>

<h1 align="center">Ubuntu-Nido</h1>

<p align="center">
  <a href="../README.md">English</a> · <strong>Tiếng Việt</strong>
</p>

**Ubuntu-Nido** là bản phân phối Ubuntu LTS tích hợp sẵn bộ gõ tiếng Việt **Sen Việt** (thuần IBus), bộ văn phòng AI **NidoOffice**, bộ tiện ích hệ thống **NidoBox** và **NidoAgent**.

## Tải Bộ Cài

| Kiến trúc | Bộ cài | Phần cứng hỗ trợ |
| :--- | :--- | :--- |
| **`amd64` (`x86_64`)** | [**Ubuntu-Nido-desktop-amd64.iso**](https://github.com/stevedat/NidoHub/releases/latest/download/Ubuntu-Nido-desktop-amd64.iso) | PC & Laptop Intel / AMD (Khuyên dùng) |
| **`arm64` (`aarch64`)** | [**Ubuntu-Nido-desktop-arm64.iso**](https://github.com/stevedat/NidoHub/releases/latest/download/Ubuntu-Nido-desktop-arm64.iso) | Máy ảo Apple Silicon & hệ thống ARM64 (Beta) |

Kiểm tra toàn vẹn tệp bằng [`SHA256SUMS.txt`](https://github.com/stevedat/NidoHub/releases/latest/download/SHA256SUMS.txt):

```sh
sha256sum -c SHA256SUMS.txt
```

## Điểm nổi bật

- **Nido Minimal Desktop (Ubuntu 26.04 LTS + GNOME Minimal):** Giữ nguyên nền tảng Ubuntu LTS & GNOME Wayland (GDM, Nautilus, Settings, PipeWire), lược bỏ game/ứng dụng thừa — dùng qua ISO hoặc cài trực tiếp lên Ubuntu chính thức bằng gói `nido-desktop`.
- **Bộ gõ Sen Việt (IBus):** Sẵn sàng gõ tiếng Việt Telex & VNI qua GNOME IBus nguyên bản trên cả Wayland và X11 ngay sau khi đăng nhập.
- **Bộ văn phòng NidoOffice:** Cài sẵn làm ứng dụng văn phòng mặc định cho Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), PDF và Markdown (thay thế hoàn toàn LibreOffice).
- **Bộ tiện ích NidoBox:** Ứng dụng GTK4/Libadwaita + Rust nguyên bản (`Super+Shift+S` chụp màn hình + OCR Tiếng Việt ngoại tuyến, trạm truyền tệp LAN QR NidoDrop, đổi ảnh hàng loạt, giải nén giữ dấu Tiếng Việt và mở khoá ổ NTFS kẹt Windows).
- **NidoAgent:** Trung tâm điều khiển & trợ lý công việc nổi bật trên Dock, phím tắt (`Super+Shift+A`), dòng lệnh (`nidoagent`), hoặc trình quản lý tệp.
- **Kiểm soát cục bộ:** Tự chọn API key cá nhân hoặc kết nối mô hình AI chạy trên máy.

## Build từ mã nguồn

```sh
make check && make packages && make iso
# Build nhanh ARM64 tại máy (lưu cache ISO gốc + ext4 workspace + zstd cấp 1)
EXT_DIR=/duong/dan/o-ngoai make iso-arm64-fast
```

## Giấy phép

Phát hành theo giấy phép [GNU General Public License v3.0](../LICENSE).
