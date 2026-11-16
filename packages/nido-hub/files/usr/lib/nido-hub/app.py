#!/usr/bin/env python3
"""NidoHub Desktop — Native GTK4 / Libadwaita Welcome, App Manager & Agent UI."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any

PROVIDERS = [
    ("gemini", "Google Gemini API (BYOK)"),
    ("openai", "OpenAI API (BYOK)"),
    ("local", "Local Ollama (127.0.0.1:11434)"),
]


class NidoHubBackend:
    """Headless-testable state and process backend for NidoHub."""

    def __init__(
        self,
        home_dir: Path | None = None,
        catalog_path: Path | None = None,
    ) -> None:
        self.home_dir = home_dir or Path(os.environ.get("HOME", str(Path.home())))
        self.nidoai_dir = Path(os.environ.get("NIDOAI_HOME", str(self.home_dir / ".nidoai")))
        self.config_dir = Path(
            os.environ.get("XDG_CONFIG_HOME", str(self.home_dir / ".config"))
        ) / "ubuntu-nido"
        self.catalog_path = catalog_path or Path(
            os.environ.get("NIDOHUB_CATALOG", "/etc/nido-hub/catalog.json")
        )

    @property
    def env_file(self) -> Path:
        return self.nidoai_dir / ".env.local"

    @property
    def welcome_done_file(self) -> Path:
        return self.config_dir / "welcome-done"

    def load_env(self) -> dict[str, str]:
        if not self.env_file.is_file():
            return {}
        data: dict[str, str] = {}
        for raw in self.env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip("'\"")
        return data

    def save_ai_settings(
        self,
        primary_backend: str,
        gemini_key: str = "",
        openai_key: str = "",
        local_base_url: str = "",
        restart_service: bool = True,
    ) -> None:
        if primary_backend not in {"gemini", "openai", "local"}:
            raise ValueError(f"Invalid backend: {primary_backend}")

        self.nidoai_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.nidoai_dir, 0o700)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        env = self.load_env()
        if len(env.get("NIDOAI_CLIENT_TOKEN", "")) < 16:
            env["NIDOAI_CLIENT_TOKEN"] = f"nidoai_{secrets.token_hex(24)}"

        env["NIDOAI_PRIMARY_BACKEND"] = primary_backend
        if gemini_key.strip():
            env["GEMINI_API_KEY"] = gemini_key.strip()
        if openai_key.strip():
            env["OPENAI_API_KEY"] = openai_key.strip()
        if local_base_url.strip():
            env["NIDOAI_LOCAL_BASE_URL"] = local_base_url.strip()

        lines = [f"{k}={v}" for k, v in sorted(env.items())]
        fd, tmp_name = tempfile.mkstemp(dir=str(self.nidoai_dir), prefix=".env.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.env_file)
            os.chmod(self.env_file, 0o600)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

        self.welcome_done_file.write_text(
            datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n",
            encoding="utf-8",
        )

        if restart_service and shutil.which("systemctl"):
            subprocess.run(
                ["systemctl", "--user", "restart", "nidoai-gateway.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def probe_gateway_health(self) -> tuple[bool, str]:
        env = self.load_env()
        port = os.environ.get("NIDOAI_PORT") or env.get("NIDOAI_PORT") or "18090"
        url = f"http://127.0.0.1:{port}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                pb = payload.get("primaryBackend", {})
                kind = pb.get("kind", "gemini")
                configured = pb.get("configured", False)
                state_txt = "đã cấu hình khóa" if configured else "chưa nhập khóa API"
                return True, f"NidoAI Gateway đang chạy ({kind} — {state_txt})"
        except Exception:
            return False, "NidoAI Gateway chưa phản hồi (sẽ tự bật sau khi lưu cấu hình)"

    def is_package_installed(self, pkg_name: str) -> bool:
        dpkg_query = shutil.which("dpkg-query")
        if not dpkg_query:
            return False
        res = subprocess.run(
            [dpkg_query, "-W", "-f=${Status}", pkg_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return res.returncode == 0 and "install ok installed" in res.stdout

    def load_catalog_items(self) -> list[dict[str, Any]]:
        if not self.catalog_path.is_file():
            return []
        raw = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        editions = raw.get("editions", [])
        items: list[dict[str, Any]] = []
        for ed in editions:
            pkgs = [str(p) for p in ed.get("packages", []) if p]
            installed = bool(pkgs) and all(self.is_package_installed(p) for p in pkgs)
            items.append(
                {
                    "id": str(ed.get("id", "")),
                    "name": str(ed.get("name", "")),
                    "description": str(ed.get("description", "")),
                    "packages": pkgs,
                    "installed": installed,
                }
            )
        return items

    def install_packages(self, packages: list[str]) -> tuple[bool, str]:
        safe_pkgs = [p for p in packages if p and all(c.isalnum() or c in "-+." for c in p)]
        if not safe_pkgs:
            return False, "Danh sách gói không hợp lệ."
        pkexec = shutil.which("pkexec")
        apt_get = shutil.which("apt-get")
        if not pkexec or not apt_get:
            return False, "Cần pkexec và apt-get trên hệ thống Ubuntu để cài đặt gói."
        cmd = [
            pkexec,
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            apt_get,
            "install",
            "-y",
            *safe_pkgs,
        ]
        res = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if res.returncode == 0:
            return True, f"Đã cài đặt/cập nhật thành công: {', '.join(safe_pkgs)}"
        return False, (res.stderr or res.stdout or "Lỗi cài đặt APT").strip()

    def run_agent_task(
        self,
        prompt: str,
        files: list[str] | None = None,
        out_path: str | None = None,
    ) -> tuple[bool, str]:
        agent_bin = os.environ.get("NIDOAGENT_BIN") or shutil.which("nidoagent") or "/usr/bin/nidoagent"
        argv = [agent_bin, "ask"]
        for fpath in files or []:
            argv.extend(["--file", fpath])
        if out_path:
            argv.extend(["--out", out_path])
        argv.append(prompt.strip() or "Tóm tắt nội dung và tạo báo cáo tổng hợp.")

        res = subprocess.run(argv, check=False, capture_output=True, text=True)
        output = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        return res.returncode == 0, output.strip()


def run_self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="nidohub-selftest-") as tmp:
        root = Path(tmp)
        cat_file = root / "catalog.json"
        cat_file.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "editions": [
                        {
                            "id": "core",
                            "name": "Ubuntu-Nido Core",
                            "packages": ["nidoai-gateway", "nidoagent-cli"],
                            "description": "Core packages",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        backend = NidoHubBackend(home_dir=root / "home", catalog_path=cat_file)
        backend.save_ai_settings(
            primary_backend="gemini",
            gemini_key="test_gemini_key_placeholder",
            restart_service=False,
        )
        assert backend.env_file.is_file(), "Missing .env.local"
        mode = backend.env_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600 on .env.local, got {oct(mode)}"
        loaded = backend.load_env()
        assert loaded["NIDOAI_PRIMARY_BACKEND"] == "gemini"
        assert loaded["GEMINI_API_KEY"] == "test_gemini_key_placeholder"
        assert len(loaded["NIDOAI_CLIENT_TOKEN"]) >= 32
        assert backend.welcome_done_file.is_file()
        items = backend.load_catalog_items()
        assert len(items) == 1 and items[0]["id"] == "core"
    print("nido-hub self-test passed.")
    return 0


def can_launch_gtk() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi  # type: ignore

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401

        return True
    except Exception:
        return False


def run_gtk_app(backend: NidoHubBackend, initial_page: str) -> int:
    import gi  # type: ignore

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk  # type: ignore

    class NidoHubApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="com.nidoholdings.NidoHub",
                flags=Gio.ApplicationFlags.NON_UNIQUE,
            )
            self.selected_files: list[str] = []

        def do_activate(self) -> None:
            win = Adw.ApplicationWindow(application=self)
            win.set_title("NidoHub — Ubuntu-Nido")
            win.set_default_size(860, 600)

            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            stack = Adw.ViewStack()

            switcher = Adw.ViewSwitcher()
            switcher.set_stack(stack)
            switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
            header.set_title_widget(switcher)
            toolbar.add_top_bar(header)

            toast_overlay = Adw.ToastOverlay()
            toast_overlay.set_child(stack)
            toolbar.set_content(toast_overlay)

            # --- Page 1: AI Setup ---
            ai_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            banner = Adw.Banner()
            ok_gw, gw_msg = backend.probe_gateway_health()
            banner.set_title(gw_msg)
            banner.set_revealed(True)
            ai_box.append(banner)

            ai_page = Adw.PreferencesPage()
            env = backend.load_env()

            prov_group = Adw.PreferencesGroup(
                title="Cấu hình NidoAI Gateway",
                description="Khóa API được lưu cục bộ tại ~/.nidoai/.env.local (quyền 0600).",
            )
            model_list = Gtk.StringList.new([label for _, label in PROVIDERS])
            prov_row = Adw.ComboRow(title="Nguồn AI mặc định", model=model_list)
            cur_prov = env.get("NIDOAI_PRIMARY_BACKEND", "gemini")
            prov_idx = next((i for i, (k, _) in enumerate(PROVIDERS) if k == cur_prov), 0)
            prov_row.set_selected(prov_idx)
            prov_group.add(prov_row)

            gemini_row = Adw.PasswordEntryRow(title="Google Gemini API Key")
            gemini_row.set_text(env.get("GEMINI_API_KEY", ""))
            prov_group.add(gemini_row)

            openai_row = Adw.PasswordEntryRow(title="OpenAI API Key")
            openai_row.set_text(env.get("OPENAI_API_KEY", ""))
            prov_group.add(openai_row)

            ollama_row = Adw.EntryRow(title="Local Ollama Endpoint")
            ollama_row.set_text(env.get("NIDOAI_LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"))
            prov_group.add(ollama_row)

            save_btn = Gtk.Button(label="Lưu & Kích hoạt NidoAI")
            save_btn.add_css_class("suggested-action")
            save_btn.add_css_class("pill")
            save_btn.set_halign(Gtk.Align.CENTER)
            save_btn.set_margin_top(16)

            def on_save_clicked(_btn: Gtk.Button) -> None:
                sel = prov_row.get_selected()
                kind = PROVIDERS[sel][0] if 0 <= sel < len(PROVIDERS) else "gemini"
                backend.save_ai_settings(
                    primary_backend=kind,
                    gemini_key=gemini_row.get_text(),
                    openai_key=openai_row.get_text(),
                    local_base_url=ollama_row.get_text(),
                    restart_service=True,
                )
                _, msg = backend.probe_gateway_health()
                banner.set_title(msg)
                toast_overlay.add_toast(Adw.Toast.new("Đã lưu cấu hình NidoAI!"))

            btn_group = Adw.PreferencesGroup()
            btn_group.add(save_btn)
            save_btn.connect("clicked", on_save_clicked)

            ai_page.add(prov_group)
            ai_page.add(btn_group)
            ai_box.append(ai_page)
            stack.add_titled_with_icon(ai_box, "welcome", "Thiết lập AI", "preferences-system-symbolic")

            # --- Page 2: Ecosystem App Manager ---
            apps_page = Adw.PreferencesPage()
            apps_group = Adw.PreferencesGroup(
                title="Thành phần & Ứng dụng Ubuntu-Nido",
                description="Quản lý cài đặt và cập nhật trực tiếp qua hệ thống gói APT.",
            )

            def populate_apps() -> None:
                for item in backend.load_catalog_items():
                    row = Adw.ActionRow(
                        title=item["name"],
                        subtitle=f"{item['description']} ({'Đã cài đặt' if item['installed'] else 'Chưa cài đặt'})",
                    )
                    act_btn = Gtk.Button(
                        label="Cập nhật" if item["installed"] else "Cài đặt",
                        valign=Gtk.Align.CENTER,
                    )
                    pkgs = list(item["packages"])

                    def on_pkg_action(_b: Gtk.Button, target_pkgs: list[str] = pkgs) -> None:
                        _b.set_sensitive(False)

                        def worker() -> None:
                            ok_res, msg_res = backend.install_packages(target_pkgs)
                            GLib.idle_add(
                                lambda: (
                                    _b.set_sensitive(True),
                                    toast_overlay.add_toast(Adw.Toast.new(msg_res[:120])),
                                )
                            )

                        threading.Thread(target=worker, daemon=True).start()

                    act_btn.connect("clicked", on_pkg_action)
                    row.add_suffix(act_btn)
                    apps_group.add(row)

            populate_apps()
            apps_page.add(apps_group)
            stack.add_titled_with_icon(apps_page, "apps", "Ứng dụng", "system-software-install-symbolic")

            # --- Page 3: Quick NidoAgent & Document Report ---
            agent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            agent_box.set_margin_top(16)
            agent_box.set_margin_bottom(16)
            agent_box.set_margin_start(20)
            agent_box.set_margin_end(20)

            ctrl_group = Adw.PreferencesGroup(
                title="Trợ lý Điều phối NidoAgent",
                description="Đính kèm hợp đồng / tài liệu (.docx, .xlsx, .pdf, .md) và xuất báo cáo tự động.",
            )
            prompt_row = Adw.EntryRow(title="Yêu cầu công việc")
            prompt_row.set_text("Tổng hợp các thông tin và giá trị chính từ tài liệu đính kèm.")
            ctrl_group.add(prompt_row)

            files_row = Adw.ActionRow(title="Tệp đính kèm", subtitle="Chưa chọn tệp nào")
            pick_btn = Gtk.Button(label="Chọn tệp...", valign=Gtk.Align.CENTER)
            clear_btn = Gtk.Button(label="Xóa", valign=Gtk.Align.CENTER)
            files_row.add_suffix(pick_btn)
            files_row.add_suffix(clear_btn)
            ctrl_group.add(files_row)

            export_modes = [
                ("", "Chỉ hiển thị câu trả lời"),
                (".docx", "Xuất báo cáo Word (.docx)"),
                (".xlsx", "Xuất bảng tổng hợp Excel (.xlsx)"),
            ]
            export_row = Adw.ComboRow(
                title="Định dạng kết quả",
                model=Gtk.StringList.new([m[1] for m in export_modes]),
            )
            ctrl_group.add(export_row)
            agent_box.append(ctrl_group)

            run_btn = Gtk.Button(label="Thực thi với NidoAgent")
            run_btn.add_css_class("suggested-action")
            run_btn.add_css_class("pill")
            run_btn.set_halign(Gtk.Align.START)
            agent_box.append(run_btn)

            scroller = Gtk.ScrolledWindow(vexpand=True)
            out_view = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            out_view.set_monospace(False)
            out_buf = out_view.get_buffer()
            scroller.set_child(out_view)
            agent_box.append(scroller)

            def on_pick_files(_b: Gtk.Button) -> None:
                dialog = Gtk.FileDialog(title="Chọn tài liệu cho NidoAgent")

                def on_done(dlg: Any, res: Any) -> None:
                    try:
                        gfiles = dlg.open_multiple_finish(res)
                        self.selected_files = [
                            gf.get_path() for gf in gfiles if gf.get_path()
                        ]
                        files_row.set_subtitle(
                            f"Đã chọn {len(self.selected_files)} tệp: "
                            + ", ".join(Path(p).name for p in self.selected_files[:3])
                        )
                    except Exception:
                        pass

                dialog.open_multiple(win, None, on_done)

            def on_clear_files(_b: Gtk.Button) -> None:
                self.selected_files = []
                files_row.set_subtitle("Chưa chọn tệp nào")

            def on_run_agent(_b: Gtk.Button) -> None:
                _b.set_sensitive(False)
                out_buf.set_text("Đang xử lý qua NidoAgent...")
                ext = export_modes[export_row.get_selected()][0]
                out_file = ""
                if ext:
                    docs_dir = backend.home_dir / "Documents"
                    docs_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    out_file = str(docs_dir / f"Bao-cao-NidoAgent-{stamp}{ext}")

                prompt_txt = prompt_row.get_text()
                chosen = list(self.selected_files)

                def worker() -> None:
                    _, result_txt = backend.run_agent_task(
                        prompt=prompt_txt,
                        files=chosen,
                        out_path=out_file or None,
                    )
                    GLib.idle_add(
                        lambda: (
                            _b.set_sensitive(True),
                            out_buf.set_text(result_txt),
                        )
                    )

                threading.Thread(target=worker, daemon=True).start()

            pick_btn.connect("clicked", on_pick_files)
            clear_btn.connect("clicked", on_clear_files)
            run_btn.connect("clicked", on_run_agent)

            stack.add_titled_with_icon(agent_box, "agent", "NidoAgent", "system-run-symbolic")

            if initial_page in {"welcome", "apps", "agent"}:
                stack.set_visible_child_name(initial_page)

            win.set_content(toolbar)
            win.present()

    app = NidoHubApp()
    return int(app.run([]))


def main() -> int:
    parser = argparse.ArgumentParser(description="NidoHub Desktop")
    parser.add_argument(
        "--page",
        choices=("welcome", "apps", "agent"),
        default="welcome",
        help="Initial NidoHub tab to open",
    )
    parser.add_argument(
        "--first-login",
        action="store_true",
        help="Exit immediately if initial welcome setup was already completed",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run headless backend self-tests and exit",
    )
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    backend = NidoHubBackend()
    if args.first_login and backend.welcome_done_file.is_file():
        return 0

    if not can_launch_gtk():
        return 0

    return run_gtk_app(backend, args.page)


if __name__ == "__main__":
    sys.exit(main())
