<p align="center">
  <img src="../branding/logo-ubuntu-nido.svg" width="96" alt="Ubuntu-Nido" />
</p>

<h1 align="center">Ubuntu-Nido</h1>

<p align="center">
  <a href="../README.md">English</a> · <strong>Tiếng Việt</strong>
</p>

**Ubuntu-Nido** là bản phân phối Ubuntu LTS tích hợp sẵn bộ gõ tiếng Việt **Sen Việt**, bộ tiện ích hệ thống **NidoBox** và **NidoAgent**.

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

- **Ubuntu LTS + GNOME:** Kế thừa hỗ trợ phần cứng, kho phần mềm và cập nhật bảo mật chính thức từ Ubuntu.
- **Bộ gõ Sen Việt:** Sẵn sàng gõ tiếng Việt Telex & VNI trên cả Wayland và X11 ngay sau khi đăng nhập.
- **Bộ tiện ích NidoBox:** Ứng dụng GTK4/Libadwaita + Rust nguyên bản (`Super+Shift+S` chụp màn hình + OCR Tiếng Việt ngoại tuyến, trạm truyền tệp LAN QR NidoDrop, đổi ảnh hàng loạt, giải nén giữ dấu Tiếng Việt và mở khoá ổ NTFS kẹt Windows).
- **NidoAgent:** Gọi trợ lý công việc từ phím tắt (`Super+Space`), dòng lệnh (`nidoagent`), hoặc trình quản lý tệp.
- **Kiểm soát cục bộ:** Tự chọn API key cá nhân hoặc kết nối mô hình AI chạy trên máy.

## Build từ mã nguồn

```sh
make check && make packages && make iso
```

## Giấy phép

Phát hành theo giấy phép [GNU General Public License v3.0](../LICENSE).
