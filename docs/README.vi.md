<p align="center">
  <img src="../branding/logo-nidoos.svg" width="96" alt="NidoOS" />
</p>

<h1 align="center">NidoOS</h1>

<p align="center">
  <a href="../README.md">English</a> · <strong>Tiếng Việt</strong>
</p>

**NidoOS** là hệ điều hành máy tính để bàn AI-Native xây dựng trên nền tảng Ubuntu 26.04 LTS, tích hợp sẵn bộ gõ tiếng Việt **Sen Việt** (IBus Telex & VNI mặc định), **NidoOffice**, **NidoBox**, **Email Geary + Himalaya AI** và **NidoAgent**.

## Tải Bộ Cài

| Kiến trúc | Bộ cài (CDN tốc độ cao) | Phần cứng hỗ trợ |
| :--- | :--- | :--- |
| **`amd64` (`x86_64`)** | [**NidoOS-desktop-amd64.iso**](https://pub-1972d169061247c2aec05aa7ba69026f.r2.dev/latest/NidoOS-desktop-amd64.iso) | PC & Laptop Intel / AMD (Khuyên dùng) |
| **`arm64` (`aarch64`)** | [**NidoOS-desktop-arm64.iso**](https://pub-1972d169061247c2aec05aa7ba69026f.r2.dev/latest/NidoOS-desktop-arm64.iso) | Máy ảo Apple Silicon & hệ thống ARM64 (Beta) |

Kiểm tra toàn vẹn tệp bằng [`SHA256SUMS.txt`](https://pub-1972d169061247c2aec05aa7ba69026f.r2.dev/latest/SHA256SUMS.txt):

```sh
sha256sum -c SHA256SUMS.txt
```

## Điểm nổi bật

- **NidoOS Minimal Desktop (GNOME Teal/Emerald tinh gọn):** Giao diện GNOME Wayland hiện đại với thanh Dock nổi ở đáy màn hình, App Grid gom gọn 1 trang (`Hệ thống · Tiện ích · Đa phương tiện`), bảng màu Xanh ngọc – Tối dịu mắt, xử lý chuẩn font chữ tiếng Việt có dấu từ trình cài đặt Live ISO (`Noto Sans`) tới toàn hệ điều hành (`Be Vietnam Pro`), loại bỏ logo/telemetry Ubuntu và Thunderbird Snap mặc định.
- **Bộ gõ Sen Việt (IBus Telex & VNI):** Kích hoạt mặc định sẵn cả Telex và VNI (`Super+Space`) ngay từ lần đăng nhập đầu tiên, không cần thêm bàn phím thủ công.
- **Thiết lập AI ON/OFF chủ động:** Tự động mở ở lần đăng nhập đầu tiên sau khi cài đặt xong (không làm phiền khi đang chạy trình cài đặt Live ISO), cho phép Bật AI (đăng nhập ít nhất 1 tài khoản Google/Gemini, ChatGPT hoặc Claude, kèm tùy chọn nhập API Key) hoặc Tắt hoàn toàn AI.
- **Email nhẹ tích hợp AI (`Geary` + `Himalaya CLI`):** Thay thế Thunderbird nặng nề bằng **Geary** GTK nhẹ mượt kết hợp **Himalaya CLI** và OS Skill `ubuntu-nido-email` giúp NidoAgent đọc tóm tắt, lọc thư và lưu nháp trả lời an toàn.
- **Bộ văn phòng NidoOffice:** Cài sẵn làm ứng dụng văn phòng mặc định cho Word (`.docx`), Excel (`.xlsx`), PowerPoint (`.pptx`), PDF (hỗ trợ ký số PAdES USB Token PKCS#11) và Markdown.
- **Bộ tiện ích NidoBox:** Ứng dụng GTK4/Libadwaita + Rust nguyên bản (`Super+Shift+S` chụp màn hình + OCR Tiếng Việt ngoại tuyến, NidoDrop LAN QR, đổi ảnh hàng loạt, giải nén chuẩn tiếng Việt và mở khóa NTFS).
- **NidoAgent:** Trợ lý điều phối công việc qua phím tắt (`Super+Shift+A`), dòng lệnh (`nidoagent`), NidoHub hoặc chuột phải trong Files.

## Build từ mã nguồn

```sh
make check && make packages && make iso
# Build nhanh ARM64 tại máy (lưu cache ISO gốc + ext4 workspace + zstd cấp 1)
EXT_DIR=/duong/dan/o-ngoai make iso-arm64-fast
```

## Nhãn hiệu & Giấy phép

- Phát hành theo giấy phép [GNU General Public License v3.0](../LICENSE).
- NidoOS là bản phân phối độc lập xây dựng trên nền tảng gói Ubuntu LTS, không liên kết hay được bảo chứng bởi Canonical Ltd. Ubuntu là nhãn hiệu đã đăng ký của Canonical Ltd.
