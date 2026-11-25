# nido-ime-bridge

Native zero-underline IBus bridge for **Sen Việt (VieLotusIM)** on NidoOS.

## Architecture
- Dynamically loads `/usr/lib/libVieLotusCAPI.so.0` from the unmodified upstream `vielotus` package via `dlopen()`.
- Never invokes IBus preedit APIs (`update_preedit_text` / `show_preedit_text`), eliminating Wayland `zwp_text_input_v3` underlines across GTK4, Firefox, Chromium/Electron (NidoOffice), and terminals.
- Shares `~/.config/vielotus/config.env`, `~/.config/vielotus/macros.json`, and `/usr/bin/vielotus-settings` directly with upstream `vielotus`.

## Optional NidoOS Tuning (`~/.config/vielotus/config.env`)
- `NIDO_BS_BACKEND=auto|forward|uinput` (default: `auto` — verified `delete_surrounding_text` first, then ordered `forward_key_event` fallback; `uinput` uses `/dev/uinput` with echo-barrier synchronization).
- `NIDO_BS_DELAY_US=8000` (microseconds between synthetic BackSpace burst and queued commit on non-surrounding-text clients; default `8000`, range `0..50000`).
