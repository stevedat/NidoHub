#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS_COUNT=0

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[PASS] %s\n' "$1"
}

echo "=== 1. Static Analysis (bash/sh syntax & ShellCheck) ==="
SHELL_SCRIPTS=(
  "${ROOT_DIR}/packages/build-all.sh"
  "${ROOT_DIR}/packages/nidoai-gateway/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nidoai-gateway/DEBIAN/prerm"
  "${ROOT_DIR}/packages/nidoai-gateway/files/usr/bin/nidoai"
  "${ROOT_DIR}/packages/nidoai-gateway/files/usr/libexec/nidoai/gateway-daemon"
  "${ROOT_DIR}/packages/nidoagent-cli/files/usr/bin/nidoagent"
  "${ROOT_DIR}/packages/nido-desktop-settings/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/bind-installer-fonts"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/configure-firewall"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/local/bin/nido-welcome"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/local/bin/nidoagent-quicklaunch"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giao-cho-NidoAgent"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Gui-qua-NidoDrop-LAN"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giai-nen-Chuan-Tieng-Viet"
  "${ROOT_DIR}/packages/nido-hub/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nido-hub/DEBIAN/prerm"
  "${ROOT_DIR}/packages/nido-hub/files/usr/bin/nido-hub"
  "${ROOT_DIR}/packages/nidooffice-cli/files/usr/bin/nidooffice"
  "${ROOT_DIR}/packages/nidooffice-desktop/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nidooffice-desktop/DEBIAN/postrm"
  "${ROOT_DIR}/packages/nidooffice-desktop/files/usr/bin/nidooffice-desktop"
  "${ROOT_DIR}/packages/nidooffice-desktop/files/usr/lib/nidooffice-desktop/install-runtime.sh"
  "${ROOT_DIR}/packages/nido-desktop/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nido-desktop/DEBIAN/postrm"
  "${ROOT_DIR}/packages/nido-desktop/files/usr/libexec/ubuntu-nido/showtime-launch"
  "${ROOT_DIR}/packages/nido-desktop/files/usr/libexec/ubuntu-nido/native-first-cleanup"
  "${ROOT_DIR}/scripts/validate_native_first.sh"
  "${ROOT_DIR}/iso-builder/build.sh"
  "${ROOT_DIR}/iso-builder/scripts/config.sh"
  "${ROOT_DIR}/iso-builder/scripts/utils.sh"
  "${ROOT_DIR}/iso-builder/scripts/extract.sh"
  "${ROOT_DIR}/iso-builder/scripts/boot_config.sh"
  "${ROOT_DIR}/iso-builder/scripts/overlay.sh"
  "${ROOT_DIR}/iso-builder/scripts/customize.sh"
  "${ROOT_DIR}/iso-builder/scripts/chroot_shell.sh"
  "${ROOT_DIR}/iso-builder/scripts/repack.sh"
  "${ROOT_DIR}/iso-builder/scripts/local-arm64.sh"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0100-nido-gnome-setup.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0110-nido-minimal-prune.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0120-nido-native-first.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0150-vielotus-im.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0160-nidobox.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0170-nidooffice-desktop.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0180-nido-email-stack.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0200-nido-ai-stack.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0900-nido-plymouth.hook.chroot"
  "${ROOT_DIR}/scripts/native-build/build_gateway_sea.sh"
  "${ROOT_DIR}/scripts/native-build/build_native_debs.sh"
  "${ROOT_DIR}/tests/verify_repo.sh"
)

for script in "${SHELL_SCRIPTS[@]}"; do
  first_line="$(head -n 1 "$script")"
  if [[ "$first_line" == *"bash"* ]]; then
    bash -n "$script"
  else
    sh -n "$script"
  fi
done
pass "Syntax check (bash -n / sh -n) passed for ${#SHELL_SCRIPTS[@]} scripts"

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -S warning -x -P "${ROOT_DIR}/iso-builder" "${SHELL_SCRIPTS[@]}"
  pass "ShellCheck (-S warning) passed with 0 warnings across ${#SHELL_SCRIPTS[@]} scripts"
else
  echo "ERROR: shellcheck is required for make check" >&2
  exit 1
fi

echo "=== 2. Branding Asset Generation & PNG Validation ==="
python3 "${ROOT_DIR}/branding/generate-assets.py"
python3 - "${ROOT_DIR}/iso-builder/config/includes.chroot/usr/share/backgrounds/ubuntu-nido" <<'PY'
import struct, sys
from pathlib import Path

asset_dir = Path(sys.argv[1])
expected = {
    "watermark.png": (256, 256),
    "splash.png": (640, 480),
    "wallpaper.png": (1920, 1080),
    "nidoos-logo.png": (160, 160),
}
for name, dims in expected.items():
    p = asset_dir / name
    data = p.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name}: bad PNG header"
    assert data[12:16] == b"IHDR", f"{name}: missing IHDR"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == dims, f"{name}: expected {dims}, got {(w, h)}"
print("Verified PNG headers and exact dimensions for all 4 NidoOS branding assets.")
PY
pass "Branding PNGs generated and verified"

echo "=== 3. Portable .deb Build & Secret Audit ==="
bash "${ROOT_DIR}/packages/build-all.sh"
python3 - "${ROOT_DIR}/packages/dist" <<'PY'
import gzip, io, lzma, subprocess, sys, tarfile
from pathlib import Path

dist = Path(sys.argv[1])
expected = [
    "nidoai-gateway_0.2.0_all.deb",
    "nidoagent-cli_0.2.0_all.deb",
    "nido-desktop-settings_0.2.0_all.deb",
    "nido-hub_0.2.0_all.deb",
    "nidooffice-cli_0.2.0_all.deb",
    "nidooffice-desktop_0.13.1_all.deb",
    "nido-desktop_0.2.0_all.deb",
]

def parse_ar(raw: bytes) -> dict[str, bytes]:
    assert raw.startswith(b"!<arch>\n"), "Missing !<arch> magic"
    pos = 8
    out = {}
    while pos + 60 <= len(raw):
        hdr = raw[pos : pos + 60]
        pos += 60
        name = hdr[:16].decode("ascii").strip().rstrip("/")
        size = int(hdr[48:58].decode("ascii").strip())
        out[name] = raw[pos : pos + size]
        pos += size + (size % 2)
    return out

for fname in expected:
    p = dist / fname
    assert p.is_file(), f"Missing .deb: {p}"
    members = parse_ar(p.read_bytes())
    assert members.get("debian-binary") == b"2.0\n", f"{fname}: bad debian-binary"
    assert any(k.startswith("control.tar") for k in members), f"{fname}: missing control archive"
    data_key = next((k for k in members if k.startswith("data.tar")), None)
    assert data_key is not None, f"{fname}: missing data archive"

    raw_data = members[data_key]
    if data_key.endswith(".gz"):
        tar_bytes = gzip.decompress(raw_data)
    elif data_key.endswith(".xz"):
        tar_bytes = lzma.decompress(raw_data)
    elif data_key.endswith(".zst"):
        tar_bytes = subprocess.run(["zstd", "-dc"], input=raw_data, check=True, capture_output=True).stdout
    else:
        tar_bytes = raw_data

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        names = [m.name for m in tf.getmembers()]
        leaked = [n for n in names if n.endswith("/.env") or n.endswith("/.env.local") or n == "./.env" or n == "./.env.local"]
        assert not leaked, f"{fname}: SECRET LEAKED: {leaked}"
        if fname == "nidoai-gateway_0.2.0_all.deb":
            assert "./usr/lib/nidoai/src/cli.mjs" in names, f"{fname}: missing pure JS cli.mjs entry"
            assert "./usr/lib/nidoai/src/gateway/anthropic-adapter.js" in names, f"{fname}: missing unified anthropic-adapter.js"
            assert "./usr/lib/nidoai/src/gateway/nido-account-adapter.js" in names, f"{fname}: missing nido-account-adapter.js"
            assert "./usr/lib/nidoai/src/security/secret-store.js" in names, f"{fname}: missing unified secret-store.js"
            assert "./usr/libexec/nidoai/gateway-daemon" in names, f"{fname}: missing gateway-daemon"
            assert "./etc/apparmor.d/nidoai-gateway" in names, f"{fname}: missing AppArmor profile"
            assert "./usr/share/nido-security/manifests/nidoai-gateway.sha256" in names, f"{fname}: missing integrity manifest"
            assert "./usr/lib/nidoai/src/cli.ts" not in names, f"{fname}: stale cli.ts found (breaks stock Ubuntu nodejs)"
            srv_js = tf.extractfile("./usr/lib/nidoai/src/gateway/server.js").read().decode("utf-8")
            assert "// Rule 1:" not in srv_js, f"{fname}: Stage 1 JS hardening did not strip comments"
        elif fname == "nido-desktop-settings_0.2.0_all.deb":
            for req_cfg in (
                "./etc/dconf/profile/user",
                "./etc/dconf/profile/gdm",
                "./etc/dconf/db/local.d/00-ubuntu-nido-settings",
                "./etc/dconf/db/gdm.d/01-nidoos-logo",
                "./etc/sysctl.d/90-nidoos-hardening.conf",
                "./etc/ufw/applications.d/nidoos",
                "./usr/share/glib-2.0/schemas/90_nidoos-input-sources.gschema.override",
                "./etc/fonts/conf.d/52-ubuntu-nido-vietnamese.conf",
                "./usr/share/backgrounds/ubuntu-nido/wallpaper.png",
                "./usr/share/pixmaps/nidoos-logo.png",
                "./usr/share/desktop-provision/whitelabel.yaml",
                "./usr/share/desktop-provision/images/nidoos-logo-light.svg",
                "./usr/share/desktop-provision/images/nidoos-logo-dark.svg",
                "./usr/share/desktop-provision/images/nidoos-mascot-dark.svg",
                "./usr/share/desktop-provision/images/nidoos-storage-icon.svg",
                "./usr/share/desktop-provision/slides/1/slide_vi_VN.html",
                "./usr/share/desktop-provision/slides/4/slide_en_US.html",
                "./etc/skel/.config/gnome-initial-setup-done",
                "./usr/local/share/applications/thunderbird_thunderbird.desktop",
                "./usr/local/share/applications/gnome-initial-setup.desktop",
                "./usr/local/share/applications/snap-store_snap-store.desktop",
                "./usr/local/share/applications/desktop-security-center_desktop-security-center.desktop",
                "./usr/local/share/applications/yelp.desktop",
                "./usr/local/share/applications/org.gnome.Yelp.desktop",
                "./usr/lib/systemd/system/nido-installer-fonts.service",
                "./usr/bin/nido-doctor",
                "./usr/bin/nido-security-verify",
                "./usr/lib/systemd/user-environment-generators/60-nido-vm-gsk",
                "./usr/libexec/ubuntu-nido/bind-installer-fonts",
                "./usr/libexec/ubuntu-nido/configure-firewall",
                "./usr/libexec/ubuntu-nido/ensure-vietnamese-ime",
                "./usr/libexec/ubuntu-nido/nidobox-tray",
                "./etc/xdg/autostart/nidobox-tray.desktop",
                "./usr/share/icons/hicolor/scalable/status/nidobox-symbolic.svg",
                "./usr/share/icons/hicolor/scalable/apps/nidobox-symbolic.svg",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-office/SKILL.md",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-box/SKILL.md",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-agent/SKILL.md",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-email/SKILL.md",
                "./usr/share/nido-security/manifests/nido-desktop-settings.sha256",
            ):
                assert req_cfg in names, f"{fname}: missing {req_cfg}"
            wl_txt = tf.extractfile("./usr/share/desktop-provision/whitelabel.yaml").read().decode("utf-8")
            assert 'app-name: "NidoOS"' in wl_txt, f"{fname}: whitelabel.yaml missing app-name: \"NidoOS\""
        elif fname == "nido-hub_0.2.0_all.deb":
            for req_hub in (
                "./usr/lib/nido-hub/agent_accounts.py",
                "./usr/lib/nido-hub/ai_discovery.py",
                "./usr/lib/nido-hub/nido_account.py",
                "./usr/lib/nido-hub/app_store.py",
                "./usr/lib/nido-hub/markdown_render.py",
                "./usr/lib/nido-hub/printer_probe.py",
                "./usr/lib/nido-hub/self_test.py",
                "./usr/libexec/nido-hub/nido-pkg-helper",
                "./etc/apparmor.d/nido-pkg-helper",
                "./usr/share/nido-hub/vendors.json",
                "./usr/share/polkit-1/actions/com.nidoholdings.nidohub.pkg.policy",
                "./usr/share/nido-security/manifests/nido-hub.sha256",
                "./usr/share/icons/hicolor/scalable/apps/com.nidoholdings.NidoHub.svg",
                "./usr/share/icons/hicolor/256x256/apps/com.nidoholdings.NidoHub.png",
                "./usr/share/icons/hicolor/scalable/apps/nidoos-logo.svg",
                "./usr/share/icons/hicolor/scalable/apps/distributor-logo.svg",
                "./usr/share/icons/hicolor/scalable/apps/nidoos-symbolic.svg",
                "./usr/share/icons/hicolor/scalable/actions/nidoos-app-grid-symbolic.svg",
            ):
                assert req_hub in names, f"{fname}: missing {req_hub}"
            acct_py = tf.extractfile("./usr/lib/nido-hub/nido_account.py").read().decode("utf-8")
            assert "Unified Nido Account (SSO), AI quota inspection" not in acct_py, (
                f"{fname}: Stage 1 Python hardening did not strip docstring in nido_account.py"
            )
        elif fname == "nido-desktop_0.2.0_all.deb":
            ctrl_key = next((k for k in members if k.startswith("control.tar")), None)
            ctrl_raw = members[ctrl_key]
            if ctrl_key.endswith(".gz"):
                ctrl_tar = gzip.decompress(ctrl_raw)
            elif ctrl_key.endswith(".xz"):
                ctrl_tar = lzma.decompress(ctrl_raw)
            elif ctrl_key.endswith(".zst"):
                ctrl_tar = subprocess.run(["zstd", "-dc"], input=ctrl_raw, check=True, capture_output=True).stdout
            else:
                ctrl_tar = ctrl_raw
            with tarfile.open(fileobj=io.BytesIO(ctrl_tar), mode="r:") as ctf:
                ctrl_txt = ctf.extractfile("./control").read().decode("utf-8")
                for dep in ("nido-desktop-settings", "nido-hub", "nidoagent-cli", "nidoai-gateway", "nidooffice-cli", "nidooffice-desktop", "vielotus (>= 2.0.8)", "nidobox (>= 1.4.7)", "geary", "showtime | totem"):
                    assert dep in ctrl_txt, f"{fname}: missing dependency {dep}"
            for req_st in (
                "./usr/libexec/ubuntu-nido/showtime-launch",
                "./usr/libexec/ubuntu-nido/org.gnome.Showtime.service",
                "./usr/libexec/ubuntu-nido/native-first-cleanup",
                "./usr/lib/systemd/system/nido-native-first-cleanup.service",
            ):
                assert req_st in names, f"{fname}: missing {req_st}"
        elif fname == "nidooffice-desktop_0.13.1_all.deb":
            for req_od in (
                "./etc/apparmor.d/nidooffice",
                "./usr/bin/nidooffice-desktop",
                "./usr/bin/pkcs11-helper",
                "./usr/lib/nidooffice-desktop/install-runtime.sh",
                "./usr/share/applications/nidooffice.desktop",
                "./usr/share/polkit-1/actions/com.nidoholdings.nidooffice.update.policy",
                "./usr/share/nido-security/manifests/nidooffice-desktop.sha256",
            ):
                assert req_od in names, f"{fname}: missing {req_od}"
print(f"Audited all {len(expected)} .deb archives: valid ar format, zero secret files.")
PY
pass "Built and audited 7 .deb packages (zero secrets)"

echo "=== 4. Static APT Repository Verification ==="
python3 "${ROOT_DIR}/scripts/build_apt_repo.py" \
  --dist-dir "${ROOT_DIR}/packages/dist" \
  --out-dir "${ROOT_DIR}/apt-repo"

python3 - "${ROOT_DIR}/apt-repo" <<'PY'
import gzip, hashlib, sys
from pathlib import Path

repo = Path(sys.argv[1])
for suite in ("resolute", "noble"):
    rel_file = repo / "dists" / suite / "Release"
    assert rel_file.is_file(), f"Missing Release: {rel_file}"
    rel_text = rel_file.read_text(encoding="utf-8")
    for arch in ("amd64", "arm64"):
        pkg_gz = repo / "dists" / suite / "main" / f"binary-{arch}" / "Packages.gz"
        assert pkg_gz.is_file(), f"Missing {pkg_gz}"
        pkg_text = gzip.decompress(pkg_gz.read_bytes()).decode("utf-8")
        for pkg in ("nidoai-gateway", "nidoagent-cli", "nido-desktop-settings", "nido-hub", "nidooffice-cli", "nidooffice-desktop", "nido-desktop"):
            assert f"Package: {pkg}" in pkg_text, f"{pkg} missing in {suite}/{arch}"
        def _ver_tuple(v: str) -> tuple[int, ...]:
            base = v.split("-", 1)[0].split("+", 1)[0]
            return tuple(int(x) for x in base.split(".") if x.isdigit())

        for v_pkg, min_ver in (("nidobox", (1, 4, 7)), ("vielotus", (2, 0, 8))):
            vendor_debs = sorted((repo.parent / "packages" / "vendor").glob(f"{v_pkg}_*_{arch}.deb"))
            if not vendor_debs:
                continue
            assert len(vendor_debs) == 1, f"Duplicate {v_pkg} vendor debs for {arch}: {[p.name for p in vendor_debs]}"
            assert f"Package: {v_pkg}" in pkg_text, f"{v_pkg} missing in {suite}/{arch}"
            v_ver = ""
            for stanza in pkg_text.strip().split("\n\n"):
                lines = stanza.splitlines()
                if f"Package: {v_pkg}" in lines:
                    for ln in lines:
                        if ln.startswith("Version:"):
                            v_ver = ln.split(":", 1)[1].strip()
                            break
            min_str = ".".join(str(x) for x in min_ver)
            assert _ver_tuple(v_ver) >= min_ver, f"Expected {v_pkg} >= {min_str} in {suite}/{arch}, got {v_ver!r}"
        digest = hashlib.sha256(pkg_gz.read_bytes()).hexdigest()
        assert digest in rel_text, f"SHA256 mismatch for {pkg_gz} in Release"
print("Verified static APT repo Release & Packages.gz checksums for resolute & noble (amd64/arm64).")
PY

DEV_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nidohub-devver.XXXXXX")"
python3 "${ROOT_DIR}/scripts/build_deb_portable.py" \
  --source-dir "${ROOT_DIR}/packages/nido-hub" \
  --extra-files "${ROOT_DIR}/packages/nido-hub/stage" \
  --version-suffix "+dev1" \
  --output "${DEV_TEST_DIR}/nido-hub_0.2.0+dev1_all.deb"
python3 "${ROOT_DIR}/scripts/build_apt_repo.py" \
  --dist-dir "${DEV_TEST_DIR}" \
  --out-dir "${DEV_TEST_DIR}/apt" >/dev/null
python3 - "${DEV_TEST_DIR}" "${ROOT_DIR}/packages/nido-hub/DEBIAN/control" <<'PY'
import gzip, sys
from pathlib import Path
tmp = Path(sys.argv[1])
ctrl = Path(sys.argv[2]).read_text(encoding="utf-8")
assert "Version: 0.2.0\n" in ctrl, "Source DEBIAN/control must stay untouched at 0.2.0"
pkg_txt = gzip.decompress((tmp / "apt/dists/resolute/main/binary-arm64/Packages.gz").read_bytes()).decode("utf-8")
assert "Version: 0.2.0+dev1\n" in pkg_txt, f"Expected dev suffix in APT metadata: {pkg_txt}"
print("Verified --version-suffix +dev1 non-mutating dev APT build.")
PY
rm -rf "${DEV_TEST_DIR}"
pass "Static APT repository generated and verified"

echo "=== 5. Live Loopback Smoke Test (nidoai serve <-> nidoagent status) ==="
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/nidohub-smoke.XXXXXX")"
GW_PID=""
cleanup_smoke() {
  if [[ -n "${GW_PID:-}" ]]; then
    kill "$GW_PID" 2>/dev/null || true
    wait "$GW_PID" 2>/dev/null || true
  fi
  rm -rf "$SANDBOX"
}
trap cleanup_smoke EXIT

python3 - "${ROOT_DIR}/packages/dist" "$SANDBOX" <<'PY'
import gzip, io, lzma, os, subprocess, sys, tarfile
from pathlib import Path

dist = Path(sys.argv[1])
sandbox = Path(sys.argv[2])
extract_root = sandbox / "rootfs"
extract_root.mkdir(parents=True, exist_ok=True)

for deb_name in (
    "nidoai-gateway_0.2.0_all.deb",
    "nidoagent-cli_0.2.0_all.deb",
    "nido-desktop-settings_0.2.0_all.deb",
    "nido-hub_0.2.0_all.deb",
    "nidooffice-cli_0.2.0_all.deb",
    "nidooffice-desktop_0.13.1_all.deb",
):
    raw = (dist / deb_name).read_bytes()
    pos = 8
    while pos + 60 <= len(raw):
        hdr = raw[pos : pos + 60]
        pos += 60
        name = hdr[:16].decode("ascii").strip().rstrip("/")
        size = int(hdr[48:58].decode("ascii").strip())
        payload = raw[pos : pos + size]
        pos += size + (size % 2)
        if name.startswith("data.tar"):
            if name.endswith(".gz"):
                tar_bytes = gzip.decompress(payload)
            elif name.endswith(".xz"):
                tar_bytes = lzma.decompress(payload)
            elif name.endswith(".zst"):
                tar_bytes = subprocess.run(["zstd", "-dc"], input=payload, check=True, capture_output=True).stdout
            else:
                tar_bytes = payload
            with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
                tf.extractall(extract_root, filter="tar")
PY

mkdir -p "${SANDBOX}/home/.nidoai" "${SANDBOX}/home/.config"
chmod 700 "${SANDBOX}/home/.nidoai" "${SANDBOX}/home/.config"
TEST_PORT=18991
TEST_TOKEN="ubuntu_nido_smoke_test_token_0123456789abcdef"

node --check "${ROOT_DIR}/packages/nidoai-gateway/src/cli.mjs"
node --check "${ROOT_DIR}/packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs"

env \
  HOME="${SANDBOX}/home" \
  XDG_CONFIG_HOME="${SANDBOX}/home/.config" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_SECRET_STORE="file" \
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.mjs" \
  "${SANDBOX}/rootfs/usr/bin/nidoai" doctor >/dev/null

env \
  HOME="${SANDBOX}/home" \
  XDG_CONFIG_HOME="${SANDBOX}/home/.config" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_SECRET_STORE="file" \
  NIDOAI_PORT="${TEST_PORT}" \
  NIDOAI_CLIENT_TOKEN="${TEST_TOKEN}" \
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.mjs" \
  "${SANDBOX}/rootfs/usr/bin/nidoai" serve > "${SANDBOX}/gateway.log" 2>&1 &
GW_PID=$!

python3 - "${TEST_PORT}" "${TEST_TOKEN}" <<'PY'
import json, sys, time, urllib.error, urllib.request

port, token = sys.argv[1], sys.argv[2]
url = f"http://127.0.0.1:{port}/healthz"
for _ in range(50):
    try:
        with urllib.request.urlopen(url, timeout=0.5) as resp:
            if resp.status == 200:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                assert hdrs.get("x-content-type-options") == "nosniff", f"Missing nosniff header: {hdrs}"
                assert hdrs.get("x-frame-options") == "DENY", f"Missing X-Frame-Options: {hdrs}"
                assert hdrs.get("cache-control") == "no-store", f"Missing Cache-Control: {hdrs}"
                health = json.loads(resp.read().decode("utf-8"))
                caps = health.get("capabilities", {})
                assert caps.get("vision") is True, f"Expected vision capability=true: {health}"
                assert "anthropic" in caps.get("backends", []), f"Expected anthropic backend support: {health}"
                assert "nido" in caps.get("backends", []), f"Expected nido backend support: {health}"
                print(f"Gateway healthy with security headers & vision/anthropic/nido capabilities at {url}")
                break
    except Exception:
        time.sleep(0.1)
else:
    raise SystemExit(f"Gateway did not respond at {url}")

for protected in ("/v1/models", "/v1/account"):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{protected}", timeout=1.0)
        raise SystemExit(f"Expected 401 on unauthenticated {protected}")
    except urllib.error.HTTPError as err:
        assert err.code == 401, f"Expected 401 on {protected}, got {err.code}"

# Verify SSRF image_url rejection (400) and valid multimodal base64 image_url acceptance (passes schema -> 503 unconfigured)
bad_img_req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=json.dumps({
        "model": "auto",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}]}],
    }).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    method="POST",
)
try:
    urllib.request.urlopen(bad_img_req, timeout=2.0)
    raise SystemExit("Expected 400 for file:// image_url")
except urllib.error.HTTPError as err:
    assert err.code == 400, f"Expected 400 for file:// image_url, got {err.code}"

good_img_req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=json.dumps({
        "model": "auto",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "OCR"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="}},
            ],
        }],
    }).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    method="POST",
)
try:
    urllib.request.urlopen(good_img_req, timeout=2.0)
    raise SystemExit("Expected 503 for unconfigured backend on valid vision payload")
except urllib.error.HTTPError as err:
    assert err.code == 503, f"Expected 503 (schema accepted vision payload), got {err.code}"
print("Verified multimodal Vision image_url schema & SSRF guard on live unified NidoAI gateway.")
PY

NIDOAI_STATUS_OUT="$(env \
  HOME="${SANDBOX}/home" \
  XDG_CONFIG_HOME="${SANDBOX}/home/.config" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_SECRET_STORE="file" \
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.mjs" \
  "${SANDBOX}/rootfs/usr/bin/nidoai" status)"

python3 - "$NIDOAI_STATUS_OUT" "$TEST_TOKEN" <<'PY'
import json, sys
out, raw_token = sys.argv[1], sys.argv[2]
assert raw_token not in out, "Raw NIDOAI_CLIENT_TOKEN leaked in nidoai status output!"
data = json.loads(out)
assert data.get("activated") is True, f"Expected activated=true, got {data}"
assert "••••" in data.get("maskedToken", ""), f"Expected maskedToken, got {data}"
print("Verified nidoai status masks clientToken.")
PY

STATUS_OUT="$(env \
  HOME="${SANDBOX}/home" \
  XDG_CONFIG_HOME="${SANDBOX}/home/.config" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_SECRET_STORE="file" \
  NIDOAI_GATEWAY_PORT="${TEST_PORT}" \
  NIDOAI_CLIENT_TOKEN="${TEST_TOKEN}" \
  NIDOAGENT_CLI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoagent-cli/cli.mjs" \
  NIDO_SKILLS_DIR="${SANDBOX}/rootfs/usr/share/ubuntu-nido/skills" \
  NIDOAGENT_STANDALONE=1 \
  "${SANDBOX}/rootfs/usr/bin/nidoagent" status)"

python3 - "$STATUS_OUT" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
assert data["ok"] is True, f"Expected ok=true, got {data}"
assert data["tokenConfigured"] is True, "Expected tokenConfigured=true"
assert data["gateway"]["status"] == "ok", f"Unexpected gateway payload: {data}"
caps = data.get("capabilities") or {}
assert caps.get("skills", {}).get("count") == 4, f"Expected 4 OS skills in capabilities: {caps}"
assert set(caps.get("agents", {}).keys()) == {"agy", "codex", "claude"}, f"Unexpected agents cap: {caps}"
assert isinstance(caps.get("nidoAccount"), dict), f"Missing capabilities.nidoAccount: {caps}"
print("Verified nidoagent status output + capabilities against live nidoai-gateway.")
PY

NIDOHUB_APP_ENTRY="${SANDBOX}/rootfs/usr/lib/nido-hub/app.py" \
  "${SANDBOX}/rootfs/usr/bin/nido-hub" --self-test

# Isolated Nido Account SSO + Unified AI Proxy E2E verification (zero keychain writes)
python3 - "${SANDBOX}" <<'PY'
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sandbox = Path(sys.argv[1])
e2e_root = sandbox / "nido-e2e"
e2e_home = e2e_root / "home"
e2e_nidoai = e2e_home / ".nidoai"
e2e_cfg = e2e_root / "config"
for d in (e2e_home, e2e_nidoai, e2e_cfg):
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)

def make_jwt(sub: str, exp_offset: int) -> str:
    hdr = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    pay = base64.urlsafe_b64encode(
        json.dumps({"sub": sub, "exp": int(time.time()) + exp_offset}).encode()
    ).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(b"nido_mock_sig").decode().rstrip("=")
    return f"{hdr}.{pay}.{sig}"

TOK_1 = make_jwt("uid-nido-e2e", 3600)
TOK_2 = make_jwt("uid-nido-e2e", 7200)
REF_1 = "nido_mock_refresh_token_one_0001"
REF_2 = "nido_mock_refresh_token_two_0002"

state = {
    "active_token": TOK_1,
    "force_401_once": False,
    "refresh_calls": 0,
    "proxy_calls": 0,
    "logout_calls": 0,
    "quota_remaining": 48,
}

class MockNidoCloudHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send_json(self, code: int, obj: dict):
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/functions/v1/ai-proxy/quota":
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {state['active_token']}":
                self._send_json(401, {"error": "Invalid bearer token"})
                return
            self._send_json(200, {
                "userId": "uid-nido-e2e",
                "planCode": "companion_pro",
                "isCompanionPro": True,
                "source": "nido",
                "quotaSnapshot": {
                    "remaining": state["quota_remaining"],
                    "limit": 50,
                },
            })
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/auth/v1/token":
            qs = urllib.parse.parse_qs(parsed.query)
            grant = (qs.get("grant_type") or [""])[0]
            body = self._read_json()
            if grant == "password":
                if body.get("email") == "e2e@nido.vn" and body.get("password") == "NidoPass123!":
                    state["active_token"] = TOK_1
                    self._send_json(200, {
                        "access_token": TOK_1,
                        "refresh_token": REF_1,
                        "user": {
                            "id": "uid-nido-e2e",
                            "email": "e2e@nido.vn",
                            "user_metadata": {"full_name": "Nido Tester"},
                        },
                    })
                    return
                self._send_json(400, {"error_description": "Invalid login credentials"})
                return
            if grant == "refresh_token":
                if body.get("refresh_token") in {REF_1, REF_2}:
                    state["refresh_calls"] += 1
                    state["active_token"] = TOK_2
                    self._send_json(200, {
                        "access_token": TOK_2,
                        "refresh_token": REF_2,
                    })
                    return
                self._send_json(401, {"error": "Invalid refresh token"})
                return
        if parsed.path == "/auth/v1/logout":
            state["logout_calls"] += 1
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/functions/v1/ai-proxy":
            auth = self.headers.get("Authorization", "")
            if state["force_401_once"] and auth == f"Bearer {TOK_1}":
                state["force_401_once"] = False
                self._send_json(401, {"error": "jwt expired"})
                return
            if auth != f"Bearer {state['active_token']}":
                self._send_json(401, {"error": "unauthorized"})
                return
            req_body = self._read_json()
            state["proxy_calls"] += 1
            state["quota_remaining"] = max(0, state["quota_remaining"] - 1)
            self._send_json(200, {
                "ok": True,
                "model": req_body.get("model") or "nido-llama",
                "raw": f"NidoProxyOK:{req_body.get('prompt', '')}",
                "quotaSnapshot": {
                    "remaining": state["quota_remaining"],
                    "limit": 50,
                },
            })
            return
        self._send_json(404, {"error": "Not found"})

mock_srv = ThreadingHTTPServer(("127.0.0.1", 0), MockNidoCloudHandler)
mock_port = mock_srv.server_address[1]
threading.Thread(target=mock_srv.serve_forever, daemon=True).start()

e2e_port = 18992
e2e_token = "ubuntu_nido_e2e_client_token_0123456789"
e2e_env = {
    **os.environ,
    "HOME": str(e2e_home),
    "XDG_CONFIG_HOME": str(e2e_cfg),
    "NIDOAI_HOME": str(e2e_nidoai),
    "NIDOAI_SECRET_STORE": "file",
    "NIDOAI_PORT": str(e2e_port),
    "NIDOAI_GATEWAY_PORT": str(e2e_port),
    "NIDOAI_CLIENT_TOKEN": e2e_token,
    "NIDOAI_ENTRY": str(sandbox / "rootfs/usr/lib/nidoai/src/cli.mjs"),
    "NIDOAGENT_CLI_ENTRY": str(sandbox / "rootfs/usr/lib/nidoagent-cli/cli.mjs"),
    "NIDO_SKILLS_DIR": str(sandbox / "rootfs/usr/share/ubuntu-nido/skills"),
    "NIDOAGENT_STANDALONE": "1",
    "NIDO_ACCOUNT_AUTH_URL": f"http://127.0.0.1:{mock_port}",
    "NIDO_ACCOUNT_AI_PROXY_URL": f"http://127.0.0.1:{mock_port}/functions/v1/ai-proxy",
}

gw_log = open(e2e_root / "gateway-e2e.log", "w", encoding="utf-8")
gw_proc = subprocess.Popen(
    [str(sandbox / "rootfs/usr/bin/nidoai"), "serve"],
    env=e2e_env,
    stdout=gw_log,
    stderr=subprocess.STDOUT,
)

def gw_req(path: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"Authorization": f"Bearer {e2e_token}", **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{e2e_port}{path}", data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=8.0) as resp:
        raw = resp.read().decode("utf-8")
        return resp.status, dict(resp.headers), raw

try:
    for _ in range(50):
        try:
            st_code, _, _ = gw_req("/healthz")
            if st_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise SystemExit("Isolated E2E nidoai-gateway did not start")

    # 1. Login via admin API -> account.json 0600 & zero tokens in JSON/file
    code, _, raw_login = gw_req(
        "/api/admin/nido-account/login",
        method="POST",
        payload={"email": "e2e@nido.vn", "password": "NidoPass123!", "setAsPrimary": True},
    )
    assert code == 200
    for tok in (TOK_1, TOK_2, REF_1, REF_2):
        assert tok not in raw_login, "Raw auth token leaked in login response!"
    login_data = json.loads(raw_login)
    assert login_data["ok"] is True
    assert login_data["account"]["signedIn"] is True
    assert login_data["account"]["isPro"] is True
    assert login_data["account"]["planCode"] == "companion_pro"

    acct_file = e2e_cfg / "ubuntu-nido/account.json"
    assert acct_file.is_file(), f"Missing {acct_file}"
    assert (acct_file.stat().st_mode & 0o777) == 0o600, f"account.json mode mismatch: {oct(acct_file.stat().st_mode)}"
    acct_raw = acct_file.read_text("utf-8")
    for tok in (TOK_1, TOK_2, REF_1, REF_2):
        assert tok not in acct_raw, "Raw auth token persisted in account.json!"

    # 2. Loopback SSO GET /v1/account (verify normalized quota & zero leaked tokens)
    _, _, raw_acct = gw_req("/v1/account")
    for tok in (TOK_1, TOK_2, REF_1, REF_2):
        assert tok not in raw_acct, "Raw auth token leaked in /v1/account!"
    acct_res = json.loads(raw_acct)
    assert acct_res["signedIn"] is True
    assert acct_res["backendConfigured"] is True
    assert acct_res["planCode"] == "companion_pro"
    assert acct_res["quota"]["aiCreditsQuota"] == 50 and acct_res["quota"]["aiCreditsRemaining"] == 48, acct_res

    # 3. Text chat completion (stream: false)
    _, _, raw_chat = gw_req(
        "/v1/chat/completions",
        method="POST",
        payload={"model": "nido-llama", "stream": False, "messages": [{"role": "user", "content": "Xin chao"}]},
    )
    chat_res = json.loads(raw_chat)
    content = chat_res["choices"][0]["message"]["content"]
    assert "NidoProxyOK:Xin chao" in content, f"Unexpected chat reply: {chat_res}"

    # 3b. NidoOffice Companion SSO model ("nido-llama") MUST route to nido backend even when primaryBackend=gemini
    gw_req("/api/admin/config", method="POST", payload={"primaryBackend": "gemini"})
    _, hdrs_nido_route, raw_nido_route = gw_req(
        "/v1/chat/completions",
        method="POST",
        payload={"model": "nido-llama", "stream": False, "messages": [{"role": "user", "content": "Office SSO route"}]},
    )
    assert hdrs_nido_route.get("x-nidoai-backend") == "nido", f"Expected nido-llama routed to nido: {hdrs_nido_route}"
    assert "NidoProxyOK:Office SSO route" in json.loads(raw_nido_route)["choices"][0]["message"]["content"]
    gw_req("/api/admin/config", method="POST", payload={"primaryBackend": "nido"})

    # 4. Streaming chat completion (stream: true)
    _, sse_hdrs, raw_sse = gw_req(
        "/v1/chat/completions",
        method="POST",
        payload={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "Stream test"}]},
    )
    assert "text/event-stream" in sse_hdrs.get("Content-Type", "").lower(), f"Expected SSE Content-Type: {sse_hdrs}"
    assert "data: [DONE]" in raw_sse and "NidoProxyOK:Stream test" in raw_sse, f"Invalid SSE output: {raw_sse}"

    # 5. Auto-refresh on 401 -> rotates TOK_1 -> TOK_2 & succeeds 200
    state["force_401_once"] = True
    _, _, raw_refresh = gw_req(
        "/v1/chat/completions",
        method="POST",
        payload={"model": "nido-llama", "stream": False, "messages": [{"role": "user", "content": "After refresh"}]},
    )
    assert state["refresh_calls"] == 1, f"Expected 1 refresh call, got {state['refresh_calls']}"
    refresh_res = json.loads(raw_refresh)
    assert "NidoProxyOK:After refresh" in refresh_res["choices"][0]["message"]["content"]

    # 6. Vision rejection (422) when only text Nido Account proxy is configured
    vis_req = urllib.request.Request(
        f"http://127.0.0.1:{e2e_port}/v1/chat/completions",
        data=json.dumps({
            "model": "auto",
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSkZJRg=="}}]}],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {e2e_token}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(vis_req, timeout=4.0)
        raise SystemExit("Expected 422 on image_url when only nido backend is configured")
    except urllib.error.HTTPError as err:
        assert err.code == 422, f"Expected 422 for image_url on text-only Nido backend, got {err.code}"

    # 7. nidoagent CLI SSO integration
    nidoagent_bin = str(sandbox / "rootfs/usr/bin/nidoagent")
    for sub_args in (["account", "status", "--json"], ["account", "sync", "--json"], ["status"]):
        out = subprocess.run([nidoagent_bin, *sub_args], env=e2e_env, check=True, text=True, capture_output=True).stdout
        for tok in (TOK_1, TOK_2, REF_1, REF_2):
            assert tok not in out, f"Token leaked in nidoagent {' '.join(sub_args)}"
        parsed_cli = json.loads(out)
        assert parsed_cli["ok"] is True
        if sub_args[0] == "status":
            assert parsed_cli["aiReady"] is True
            assert parsed_cli["nidoAccount"]["signedIn"] is True
        else:
            assert parsed_cli["signedIn"] is True

    # 8. Logout clears session, profile, tokens in .env.local, and 'nido' in ai-policy.json
    policy_file = e2e_cfg / "ubuntu-nido/ai-policy.json"
    policy_file.write_text(
        json.dumps({"aiEnabled": True, "setupCompleted": True, "signedInProviders": ["nido", "agy"]}) + "\n",
        encoding="utf-8",
    )
    code_out, _, _ = gw_req("/api/admin/nido-account/logout", method="POST", payload={})
    assert code_out == 200
    assert state["logout_calls"] >= 1
    _, _, raw_after = gw_req("/v1/account")
    assert json.loads(raw_after)["signedIn"] is False
    pol_after = json.loads(policy_file.read_text("utf-8"))
    assert pol_after["signedInProviders"] == ["agy"], f"Expected 'nido' removed from ai-policy.json: {pol_after}"
    env_local = (e2e_nidoai / ".env.local").read_text("utf-8") if (e2e_nidoai / ".env.local").is_file() else ""
    assert "NIDO_ACCOUNT_ACCESS_TOKEN=" not in env_local, "Access token still present in .env.local after logout!"
    assert "NIDO_ACCOUNT_REFRESH_TOKEN=" not in env_local, "Refresh token still present in .env.local after logout!"

    # 9. Hot-reload without restart: external write to .env.local + account.json (simulating NidoHub)
    # must be picked up immediately by GET /v1/account so NidoOffice SSO never sees stale unconfigured state.
    time.sleep(0.05)
    (e2e_nidoai / ".env.local").write_text(
        f"NIDOAI_CLIENT_TOKEN={e2e_token}\nNIDOAI_PRIMARY_BACKEND=nido\nNIDO_ACCOUNT_ACCESS_TOKEN={TOK_2}\nNIDO_ACCOUNT_REFRESH_TOKEN={REF_2}\n",
        encoding="utf-8",
    )
    acct_file.write_text(
        json.dumps({"schemaVersion": 1, "signedIn": True, "userId": "uid-nido-e2e", "email": "hotreload@nido.vn", "planCode": "companion", "isPro": True}) + "\n",
        encoding="utf-8",
    )
    _, _, raw_hot = gw_req("/v1/account")
    hot_res = json.loads(raw_hot)
    assert hot_res["signedIn"] is True and hot_res["backendConfigured"] is True and hot_res["email"] == "hotreload@nido.vn", hot_res
    print("Verified Nido Account SSO, OpenAI JSON/SSE Proxy, auto-refresh, nido-llama routing, hot-reload, 422 vision guard, and nidoagent account E2E.")

    # 10. Security hardening E2E tests (DNS rebinding, default-off Web UI, pairing-token disabled,
    #     Codex CWD sandbox, PII-free /healthz, auth rate-limiting, ephemeral token rotation)
    import http.client as _hc
    # 10a. Unauthenticated /healthz must NEVER expose user email
    _, _, h_raw = gw_req("/healthz")
    h_json = json.loads(h_raw)
    assert "email" not in (h_json.get("nidoAccount") or {}), f"/healthz leaked email: {h_json}"

    # 10b. Host header DNS rebinding & cross-origin Origin must be rejected with 403
    conn_reb = _hc.HTTPConnection("127.0.0.1", e2e_port, timeout=3)
    conn_reb.request("GET", "/healthz", headers={"Host": "evil.attacker.example:18992"})
    reb_resp = conn_reb.getresponse()
    reb_resp.read()
    assert reb_resp.status == 403, f"Expected 403 on DNS rebinding Host, got {reb_resp.status}"
    conn_reb.close()

    conn_orig = _hc.HTTPConnection("127.0.0.1", e2e_port, timeout=3)
    conn_orig.request("GET", "/healthz", headers={"Host": f"127.0.0.1:{e2e_port}", "Origin": "https://evil.attacker.example"})
    orig_resp = conn_orig.getresponse()
    orig_resp.read()
    assert orig_resp.status == 403, f"Expected 403 on cross-origin Origin, got {orig_resp.status}"
    conn_orig.close()

    # 10c. GET / (Web Control Panel) disabled by default (404, no Set-Cookie) & /api/admin/pairing-token blocked (403)
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{e2e_port}/", timeout=2.0)
        raise SystemExit("Expected 404 on default-disabled GET /")
    except urllib.error.HTTPError as err:
        assert err.code == 404, f"Expected 404 on GET /, got {err.code}"
        assert not err.headers.get("Set-Cookie"), "Disabled Web UI must NOT issue CSRF cookie"
    try:
        gw_req("/api/admin/pairing-token")
        raise SystemExit("Expected 403 on /api/admin/pairing-token")
    except urllib.error.HTTPError as err:
        assert err.code == 403, f"Expected 403 on /api/admin/pairing-token, got {err.code}"

    # 10d. Codex workspace CWD sandbox must reject /etc and ~/.ssh with 403
    for bad_cwd in ("/etc", str(e2e_home / ".ssh")):
        bad_cx = urllib.request.Request(
            f"http://127.0.0.1:{e2e_port}/api/admin/codex/thread/start",
            data=json.dumps({"cwd": bad_cwd}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {e2e_token}"},
            method="POST",
        )
        try:
            urllib.request.urlopen(bad_cx, timeout=3.0)
            raise SystemExit(f"Expected 403 for Codex CWD {bad_cwd}")
        except urllib.error.HTTPError as err:
            assert err.code == 403, f"Expected 403 for Codex CWD {bad_cwd}, got {err.code}"

    # 10e. Account provider pinning must ignore user-writable ~/.config/ubuntu-nido/account-provider.json
    (e2e_cfg / "ubuntu-nido/account-provider.json").write_text(
        json.dumps({"authUrl": "https://evil-phish.example", "aiProxyUrl": "https://evil-phish.example"}) + "\n",
        encoding="utf-8",
    )
    pin_js = subprocess.run(
        [
            "node",
            "-e",
            f"import({(sandbox / 'rootfs/usr/lib/nidoai/src/gateway/nido-account-adapter.js').as_uri()!r}).then(m=>console.log(JSON.stringify(m.resolveNidoAccountProviderConfig({{}}, {str(e2e_cfg / 'ubuntu-nido')!r}))))",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pin_js_obj = json.loads(pin_js)
    assert "evil-phish.example" not in pin_js_obj["authUrl"] and "supabase.co" in pin_js_obj["authUrl"], pin_js_obj
    pin_py = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(sandbox / 'rootfs/usr/lib/nido-hub')!r}); from pathlib import Path; import nido_account; c = nido_account.resolve_account_provider_config(Path({str(e2e_cfg / 'ubuntu-nido')!r}), env={{}}); print(c.auth_url + '|' + c.ai_proxy_url)",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "evil-phish.example" not in pin_py and "supabase.co" in pin_py, pin_py

    # 10f. NidoAgent CLI must block attaching ~/.nidoai/.env.local & ~/.ssh/* and ignore ~/.local/share/nidoagent/bin
    fake_ssh = e2e_home / ".ssh"
    fake_ssh.mkdir(parents=True, exist_ok=True)
    fake_key = fake_ssh / "id_ed25519"
    fake_key.write_text("PRIVATE-KEY-TEST\n", encoding="utf-8")
    for sensitive_file in (e2e_nidoai / ".env.local", fake_key):
        att_res = subprocess.run(
            [nidoagent_bin, "ask", "--file", str(sensitive_file), "summarize"],
            env=e2e_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert att_res.returncode != 0 and "nhạy cảm" in att_res.stderr, (sensitive_file, att_res.returncode, att_res.stderr)

    hijack_dir = e2e_home / ".local/share/nidoagent/bin"
    hijack_dir.mkdir(parents=True, exist_ok=True)
    hijack_bin = hijack_dir / "nidoagent"
    hijack_bin.write_text("#!/bin/sh\necho HIJACKED\nexit 99\n", encoding="utf-8")
    hijack_bin.chmod(0o755)
    clean_agent_out = subprocess.run(
        [nidoagent_bin, "--version"],
        env={k: v for k, v in e2e_env.items() if k != "NIDOAGENT_STANDALONE"},
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "HIJACKED" not in clean_agent_out and "nidoagent" in clean_agent_out, clean_agent_out

    # 10g. Isolated gateway instance: ephemeral token rotation across restarts & brute-force 429 rate-limit
    eph_home = sandbox / "eph-home"
    (eph_home / ".nidoai").mkdir(parents=True, exist_ok=True)
    (eph_home / ".config/ubuntu-nido").mkdir(parents=True, exist_ok=True)
    eph_env = {
        k: v for k, v in os.environ.items()
        if k not in ("NIDOAI_CLIENT_TOKEN", "NIDOAI_EPHEMERAL_TOKEN", "NIDOAI_BASE_URL")
    }
    eph_env.update({
        "HOME": str(eph_home),
        "XDG_CONFIG_HOME": str(eph_home / ".config"),
        "NIDOAI_HOME": str(eph_home / ".nidoai"),
        "NIDOAI_SECRET_STORE": "file",
        "NIDOAI_PORT": "18994",
        "NIDOAI_ENTRY": str(sandbox / "rootfs/usr/lib/nidoai/src/cli.mjs"),
    })
    seen_tokens = []
    for run_idx in (1, 2):
        ep = subprocess.Popen(
            [str(sandbox / "rootfs/usr/bin/nidoai"), "serve"],
            env=eph_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            rt_p = eph_home / ".nidoai/runtime.json"
            for _ in range(50):
                if rt_p.is_file():
                    break
                time.sleep(0.1)
            assert rt_p.is_file(), f"Ephemeral gateway run {run_idx} did not write runtime.json"
            rt_obj = json.loads(rt_p.read_text("utf-8"))
            seen_tokens.append(rt_obj["clientToken"])
            if run_idx == 2:
                # 20 failed auth attempts -> 21st returns 429 Retry-After
                for attempt in range(21):
                    r_bad = urllib.request.Request(
                        "http://127.0.0.1:18994/v1/models",
                        headers={"Authorization": "Bearer invalid_wrong_token_000000"},
                    )
                    try:
                        urllib.request.urlopen(r_bad, timeout=2.0)
                        raise SystemExit("Expected 401/429 on invalid token")
                    except urllib.error.HTTPError as err:
                        if attempt < 20:
                            assert err.code == 401, f"Attempt {attempt} expected 401, got {err.code}"
                        else:
                            assert err.code == 429, f"Attempt 21 expected 429 rate-limit, got {err.code}"
        finally:
            ep.terminate()
            ep.wait(timeout=5)
    assert len(seen_tokens[0]) >= 24 and seen_tokens[0] != seen_tokens[1], (
        f"Expected distinct ephemeral tokens across gateway restarts: {seen_tokens}"
    )

    # 10h. Signed package integrity manifest verification on unpacked .deb rootfs (PASS -> 1-byte tamper -> FAIL)
    sec_verify = str(sandbox / "rootfs/usr/bin/nido-security-verify")
    sv_ok = subprocess.run(
        [sys.executable, sec_verify, "--verify", "--json", "--root", str(sandbox / "rootfs")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    sv_obj = json.loads(sv_ok)
    assert sv_obj["ok"] is True and sv_obj["integrity"]["filesChecked"] >= 20, sv_obj
    victim = sandbox / "rootfs/usr/libexec/nidoai/gateway-daemon"
    orig_bytes = victim.read_bytes()
    victim.write_bytes(orig_bytes + b"\n# tampered\n")
    sv_bad = subprocess.run(
        [sys.executable, sec_verify, "--verify", "--json", "--root", str(sandbox / "rootfs")],
        check=False,
        capture_output=True,
        text=True,
    )
    victim.write_bytes(orig_bytes)
    assert sv_bad.returncode != 0 and "hash mismatch" in sv_bad.stdout, sv_bad.stdout

    print("Verified Gateway security guards (anti-rebinding, default-off UI, Codex CWD, rate-limit, ephemeral rotation), provider pinning, NidoAgent isolation, and signed integrity manifests.")
finally:
    gw_proc.terminate()
    try:
        gw_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        gw_proc.kill()
        gw_proc.wait(timeout=3)
    gw_log.close()
    mock_srv.shutdown()
    mock_srv.server_close()
PY

# Verify boot_config.sh generates canonical ProductInfo-compatible .disk/info and repack.sh skips boot/boot.cat
BOOT_TMP="$(mktemp -d "${TMPDIR:-/tmp}/nido-bootcfg.XXXXXX")"
mkdir -p "${BOOT_TMP}/custom/.disk" "${BOOT_TMP}/custom/boot/grub"
printf 'Ubuntu 26.04 LTS "Resolute Raccoon" - Release arm64 (20260420)\n' > "${BOOT_TMP}/custom/.disk/info"
bash -c '
  set -euo pipefail
  SCRIPT_DIR="$1" WORK_DIR="$4" TARGET_ARCH=arm64 UBUNTU_VERSION=26.04 UBUNTU_CODENAME=resolute NIDO_VERSION=0.1.0
  source "$2"
  source "$3"
  step_boot_config >/dev/null
' _ "${ROOT_DIR}/iso-builder" "${ROOT_DIR}/iso-builder/scripts/utils.sh" "${ROOT_DIR}/iso-builder/scripts/boot_config.sh" "${BOOT_TMP}"
grep -Eq '^NidoOS 26\.04 LTS "Resolute Raccoon" - Release arm64 \([0-9]{8}\)$' "${BOOT_TMP}/custom/.disk/info" || {
  echo "ERROR: boot_config.sh produced invalid .disk/info: $(cat "${BOOT_TMP}/custom/.disk/info")" >&2
  rm -rf "${BOOT_TMP}"
  exit 1
}
rm -rf "${BOOT_TMP}"
python3 "${ROOT_DIR}/iso-builder/scripts/patch_initrd_branding.py" --self-test
grep -Fq "! -path './boot/boot.cat'" "${ROOT_DIR}/iso-builder/scripts/repack.sh" || {
  echo "ERROR: repack.sh must exclude ./boot/boot.cat from md5sum.txt" >&2
  exit 1
}

python3 - "${SANDBOX}" <<'PY'
import os, subprocess, sys
from pathlib import Path

sandbox = Path(sys.argv[1])
nidooffice_bin = str(sandbox / "rootfs/usr/bin/nidooffice")
env = dict(os.environ)
env["NIDOOFFICE_PY_ENTRY"] = str(sandbox / "rootfs/usr/lib/nidooffice-cli/engine.py")
env["NIDOOFFICE_DESKTOP_CLI"] = str(sandbox / "nonexistent-desktop-cli")

docx_path = sandbox / "report.docx"
md_in = (
    "# Báo cáo kiểm thử\n\n"
    "Nội dung: Báo cáo Ubuntu-Nido — Tiếng Việt chuẩn\n\n"
    "* **Chuẩn ký số:** **PAdES** (`ETSI.CAdES.detached`)\n"
    "  * **Phiên bản:** NidoOffice `v0.13.1` (phép nhân 2 * 3 * 4)\n\n"
    "| **Hạng mục** | Giá trị |\n| --- | --- |\n| **GPU** | 100 |\n"
)
subprocess.run(
    [nidooffice_bin, "create", "--type", "docx", "--out", str(docx_path), "--from", "-"],
    input=md_in,
    text=True,
    check=True,
    env=env,
    capture_output=True,
)
docx_out = subprocess.run(
    [nidooffice_bin, "read", str(docx_path)],
    text=True,
    check=True,
    env=env,
    capture_output=True,
).stdout
assert "Báo cáo Ubuntu-Nido — Tiếng Việt chuẩn" in docx_out, f"DOCX round-trip mismatch: {docx_out}"
assert "**Chuẩn ký số:**" not in docx_out and "**PAdES**" not in docx_out and "`v0.13.1`" not in docx_out, (
    f"Raw markdown markers leaked into DOCX text: {docx_out}"
)
assert "Chuẩn ký số: PAdES (ETSI.CAdES.detached)" in docx_out and "2 * 3 * 4" in docx_out
import zipfile
with zipfile.ZipFile(docx_path, "r") as zf_doc:
    doc_xml = zf_doc.read("word/document.xml").decode("utf-8")
assert "<w:b/>" in doc_xml and "Liberation Mono" in doc_xml, "Expected native OOXML bold & monospace runs"

xlsx_path = sandbox / "summary.xlsx"
table_in = "| **Thành phần** | Trạng thái |\n| --- | --- |\n| **NidoAgent** | 200 |\n| Sen Việt (`v2`) | =TEST |\n"
subprocess.run(
    [nidooffice_bin, "create", "--type", "xlsx", "--out", str(xlsx_path), "--from", "-"],
    input=table_in,
    text=True,
    check=True,
    env=env,
    capture_output=True,
)
xlsx_out = subprocess.run(
    [nidooffice_bin, "read", str(xlsx_path)],
    text=True,
    check=True,
    env=env,
    capture_output=True,
).stdout
assert "NidoAgent" in xlsx_out and "200" in xlsx_out and "=TEST" in xlsx_out, f"XLSX round-trip mismatch: {xlsx_out}"
assert "**NidoAgent**" not in xlsx_out and "`v2`" not in xlsx_out, f"Raw markdown leaked into XLSX: {xlsx_out}"

mock_desktop_cli = sandbox / "mock-nidooffice-desktop"
mock_desktop_cli.write_text("#!/bin/sh\necho \"delegated:$*\"\n", encoding="utf-8")
mock_desktop_cli.chmod(0o755)
delegated_env = dict(env)
delegated_env["NIDOOFFICE_DESKTOP_CLI"] = str(mock_desktop_cli)
delegated_out = subprocess.run(
    [nidooffice_bin, "read", str(docx_path)],
    text=True,
    check=True,
    env=delegated_env,
    capture_output=True,
).stdout.strip()
assert delegated_out == f"delegated:read {docx_path}", f"Expected desktop CLI delegation, got: {delegated_out}"

# Verify nidooffice info/create/read --json contracts & structured error JSON
import json as _json
docx_info = _json.loads(
    subprocess.run(
        [nidooffice_bin, "info", str(docx_path), "--json"],
        text=True, check=True, env=env, capture_output=True,
    ).stdout
)
assert docx_info["ok"] is True and docx_info["type"] == "docx" and docx_info["bytes"] > 0
assert docx_info["stats"]["paragraphs"] >= 2 and docx_info["stats"]["tables"] == 1, f"Bad docx info: {docx_info}"

xlsx_info = _json.loads(
    subprocess.run(
        [nidooffice_bin, "info", str(xlsx_path), "--json"],
        text=True, check=True, env=env, capture_output=True,
    ).stdout
)
assert xlsx_info["ok"] is True and xlsx_info["type"] == "xlsx" and xlsx_info["stats"]["sheets"] == 1 and xlsx_info["stats"]["rows"] >= 3

create_json_out = _json.loads(
    subprocess.run(
        [nidooffice_bin, "create", "--type", "docx", "--out", str(sandbox / "json_test.docx"), "--from", "-", "--json"],
        input="# Test JSON\n\nOK\n", text=True, check=True, env=env, capture_output=True,
    ).stdout
)
assert create_json_out["ok"] is True and create_json_out["bytes"] > 0, f"Bad create --json output: {create_json_out}"

missing_res = subprocess.run(
    [nidooffice_bin, "info", str(sandbox / "does-not-exist.docx"), "--json"],
    text=True, check=False, env=env, capture_output=True,
)
assert missing_res.returncode != 0
missing_err = _json.loads(missing_res.stderr)
assert missing_err["ok"] is False and "not found" in missing_err["error"].lower()

# Verify nidoagent capabilities, skills list/sync (with user-override safety), and ask --json
nidoagent_bin = str(sandbox / "rootfs/usr/bin/nidoagent")
agent_env = dict(env)
agent_env["HOME"] = str(sandbox / "home")
agent_env["NIDOAI_HOME"] = str(sandbox / "home/.nidoai")
agent_env["NIDOAGENT_CLI_ENTRY"] = str(sandbox / "rootfs/usr/lib/nidoagent-cli/cli.mjs")
agent_env["NIDOAGENT_STANDALONE"] = "1"
agent_env["NIDO_SKILLS_DIR"] = str(sandbox / "rootfs/usr/share/ubuntu-nido/skills")
agent_env["NIDOOFFICE_BIN"] = nidooffice_bin

caps_out = _json.loads(
    subprocess.run(
        [nidoagent_bin, "capabilities", "--json"],
        text=True, check=True, env=agent_env, capture_output=True,
    ).stdout
)
assert caps_out["ok"] is True and caps_out["capabilities"]["office"]["cli"] is True
assert caps_out["capabilities"]["skills"]["count"] == 4

skills_list = _json.loads(
    subprocess.run(
        [nidoagent_bin, "skills", "list", "--json"],
        text=True, check=True, env=agent_env, capture_output=True,
    ).stdout
)
skill_names = {s["name"] for s in skills_list["skills"]}
assert skill_names == {"ubuntu-nido-office", "ubuntu-nido-box", "ubuntu-nido-agent", "ubuntu-nido-email"}, f"Unexpected skills: {skills_list}"
assert all(s["description"] for s in skills_list["skills"]), f"Missing frontmatter description: {skills_list}"

# Create a custom non-symlink user skill under ~/.claude/skills to verify sync NEVER overwrites user overrides
user_override_dir = sandbox / "home/.claude/skills/ubuntu-nido-office"
user_override_dir.mkdir(parents=True, exist_ok=True)
(user_override_dir / "SKILL.md").write_text("# User Custom Override\n", encoding="utf-8")

sync1 = _json.loads(
    subprocess.run(
        [nidoagent_bin, "skills", "sync", "--all", "--json"],
        text=True, check=True, env=agent_env, capture_output=True,
    ).stdout
)
assert sync1["ok"] is True and sync1["skillsCount"] == 4
by_target = {t["target"]: {i["skill"]: i["status"] for i in t["items"]} for t in sync1["targets"]}
assert by_target["agents"]["ubuntu-nido-office"] == "linked"
assert by_target["agy"]["ubuntu-nido-box"] == "linked"
assert by_target["codex"]["ubuntu-nido-agent"] == "linked"
assert by_target["claude"]["ubuntu-nido-office"] == "skipped_user_override"
assert (user_override_dir / "SKILL.md").read_text(encoding="utf-8") == "# User Custom Override\n"
assert (sandbox / "home/.gemini/config/skills/ubuntu-nido-office/SKILL.md").is_file()

sync2 = _json.loads(
    subprocess.run(
        [nidoagent_bin, "skills", "sync", "--all", "--json"],
        text=True, check=True, env=agent_env, capture_output=True,
    ).stdout
)
by_target2 = {t["target"]: {i["skill"]: i["status"] for i in t["items"]} for t in sync2["targets"]}
assert by_target2["agents"]["ubuntu-nido-office"] == "unchanged"
assert by_target2["claude"]["ubuntu-nido-office"] == "skipped_user_override"

# Verify nidoagent ask --json against a lightweight loopback OpenAI-compatible mock server + DOCX->XLSX pipeline
import http.server as _http_srv, threading as _threading
class _MockChatHandler(_http_srv.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        _ = self.rfile.read(length)
        payload = _json.dumps({
            "id": "chatcmpl-test",
            "model": "mock-nido-model",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "| Metric | Value |\n| --- | --- |\n| Status | 100 |"},
                "finish_reason": "stop",
            }],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass

_chat_srv = _http_srv.HTTPServer(("127.0.0.1", 0), _MockChatHandler)
_chat_port = _chat_srv.server_address[1]
_chat_thr = _threading.Thread(target=_chat_srv.serve_forever, daemon=True)
_chat_thr.start()
try:
    ask_env = dict(agent_env)
    ask_env["NIDOAI_BASE_URL"] = f"http://127.0.0.1:{_chat_port}"
    ask_env["NIDOAI_CLIENT_TOKEN"] = "ubuntu_nido_mock_chat_token_0123456789"
    ask_out_xlsx = sandbox / "agent_report.xlsx"
    ask_json = _json.loads(
        subprocess.run(
            [nidoagent_bin, "ask", "--json", "--file", str(docx_path), "--out", str(ask_out_xlsx), "Tong hop"],
            text=True, check=True, env=ask_env, capture_output=True,
        ).stdout
    )
    assert ask_json["ok"] is True and ask_json["backend"] == "gateway" and ask_json["model"] == "mock-nido-model"
    assert Path(ask_json["outPath"]).resolve() == ask_out_xlsx.resolve() and ask_out_xlsx.is_file()
    assert any(Path(p).resolve() == docx_path.resolve() for p in ask_json["attachedFiles"])
    disabled_env = dict(ask_env)
    disabled_env["NIDOAI_ENABLED"] = "0"
    dis_res = subprocess.run(
        [nidoagent_bin, "ask", "--json", "hello"],
        text=True, check=False, env=disabled_env, capture_output=True,
    )
    assert dis_res.returncode != 0
    assert _json.loads(dis_res.stderr)["ok"] is False
finally:
    _chat_srv.shutdown()
    _chat_srv.server_close()

# Verify nidooffice-desktop auto-latest resolver & --check-update using loopback mock metadata server
import http.server, threading
class _MockReleaseHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("latest-linux-arm64.yml") or self.path.endswith("latest-linux.yml"):
            body = b"version: 0.14.2\npath: NidoOffice-0.14.2-arm64.AppImage\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args):
        pass

srv = http.server.HTTPServer(("127.0.0.1", 0), _MockReleaseHandler)
srv_port = srv.server_address[1]
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()
try:
    fake_opt = sandbox / "opt-nidooffice"
    fake_opt.mkdir(parents=True, exist_ok=True)
    (fake_opt / ".installed-version").write_text("0.13.0\n", encoding="utf-8")
    desk_env = dict(env)
    desk_env["NIDOOFFICE_RUNTIME_INSTALLER"] = str(
        sandbox / "rootfs/usr/lib/nidooffice-desktop/install-runtime.sh"
    )
    desk_env["NIDOOFFICE_INSTALL_DIR"] = str(fake_opt)
    desk_env["NIDOOFFICE_ARCH"] = "arm64"
    desk_env["NIDOOFFICE_RELEASE_BASE_URL"] = f"http://127.0.0.1:{srv_port}"
    chk_out = subprocess.run(
        [str(sandbox / "rootfs/usr/bin/nidooffice-desktop"), "--check-update"],
        text=True,
        check=True,
        env=desk_env,
        capture_output=True,
    ).stdout
    assert "installed=0.13.0" in chk_out, f"Unexpected installed version: {chk_out}"
    assert "latest=0.14.2" in chk_out, f"Expected resolved latest=0.14.2: {chk_out}"
    assert "update_available=true" in chk_out, f"Expected update_available=true: {chk_out}"

    pin_out = subprocess.run(
        [str(sandbox / "rootfs/usr/bin/nidooffice-desktop"), "--check-update", "--version", "0.13.0"],
        text=True,
        check=True,
        env=desk_env,
        capture_output=True,
    ).stdout
    assert "latest=0.13.0" in pin_out and "update_available=false" in pin_out, f"Pinned check failed: {pin_out}"
finally:
    srv.shutdown()
    srv.server_close()

# Verify install-runtime.sh prefers native .deb over stale .AppImage and auto-refreshes on SHA-256 mismatch (INC-010)
import hashlib as _hashlib, io as _io, tarfile as _tarfile
deb_stage = sandbox / "mock-office-deb-stage"
(deb_stage / "opt/NidoOffice/resources").mkdir(parents=True, exist_ok=True)
(deb_stage / "opt/NidoOffice/nidooffice").write_text("#!/bin/sh\necho nidooffice-deb\n", encoding="utf-8")
(deb_stage / "opt/NidoOffice/nidooffice").chmod(0o755)
(deb_stage / "opt/NidoOffice/resources/app.asar").write_bytes(b"mock-asar-with-nidoos_gateway-sso")
deb_tar_buf = _io.BytesIO()
with _tarfile.open(fileobj=deb_tar_buf, mode="w:gz") as tf:
    tf.add(deb_stage / "opt", arcname="./opt")
deb_data = deb_tar_buf.getvalue()
def _ar_entry(name: str, data: bytes) -> bytes:
    hdr = f"{name:<16}{'0':<12}{'0':<6}{'0':<6}{'100644':<8}{len(data):<10}`\n".encode("ascii")
    return hdr + data + (b"\n" if len(data) % 2 else b"")
mock_deb_bytes = b"!<arch>\n" + _ar_entry("debian-binary", b"2.0\n") + _ar_entry("data.tar.gz", deb_data)
mock_deb_sha = _hashlib.sha256(mock_deb_bytes).hexdigest()
mock_appimg_bytes = b"stale-appimage-without-sso"
mock_appimg_sha = _hashlib.sha256(mock_appimg_bytes).hexdigest()
mock_sums = (
    f"{mock_appimg_sha}  NidoOffice-linux-arm64.AppImage\n"
    f"{mock_deb_sha}  NidoOffice-linux-arm64.deb\n"
).encode("utf-8")
dl_counts = {"deb": 0, "appimage": 0}

class _MockDebReleaseHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("SHA256SUMS.txt"):
            payload = mock_sums
        elif self.path.endswith("NidoOffice-linux-arm64.deb"):
            dl_counts["deb"] += 1
            payload = mock_deb_bytes
        elif self.path.endswith("NidoOffice-linux-arm64.AppImage"):
            dl_counts["appimage"] += 1
            payload = mock_appimg_bytes
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass

deb_srv = http.server.HTTPServer(("127.0.0.1", 0), _MockDebReleaseHandler)
deb_srv_port = deb_srv.server_address[1]
threading.Thread(target=deb_srv.serve_forever, daemon=True).start()
try:
    rt_dir = sandbox / "opt-nidooffice-deb-test"
    (rt_dir / "resources").mkdir(parents=True, exist_ok=True)
    # Seed legacy AppImage install with matching version 0.13.1 but missing .installed-sha256
    (rt_dir / "nidooffice").write_text("#!/bin/sh\necho legacy\n", encoding="utf-8")
    (rt_dir / "nidooffice").chmod(0o755)
    (rt_dir / "resources/app.asar").write_bytes(b"legacy-without-sso")
    (rt_dir / ".installed-version").write_text("0.13.1\n", encoding="utf-8")
    rt_env = dict(env)
    rt_env["NIDOOFFICE_INSTALL_DIR"] = str(rt_dir)
    rt_env["NIDOOFFICE_ARCH"] = "arm64"
    rt_env["NIDOOFFICE_VERSION"] = "0.13.1"
    rt_env["NIDOOFFICE_RELEASE_BASE_URL"] = f"http://127.0.0.1:{deb_srv_port}"
    rt_env["NIDOOFFICE_SKIP_SYSTEM_INTEGRATION"] = "1"
    installer = str(sandbox / "rootfs/usr/lib/nidooffice-desktop/install-runtime.sh")

    # 1st run: detects missing .installed-sha256, prefers .deb over .AppImage, extracts SSO runtime
    subprocess.run(["bash", installer], env=rt_env, check=True, capture_output=True, text=True)
    assert dl_counts["deb"] == 1 and dl_counts["appimage"] == 0, f"Expected .deb preferred over AppImage: {dl_counts}"
    assert b"nidoos_gateway" in (rt_dir / "resources/app.asar").read_bytes()
    assert (rt_dir / ".installed-sha256").read_text(encoding="utf-8").strip() == mock_deb_sha

    # 2nd run: matching version + matching SHA-256 -> idempotent (0 downloads)
    subprocess.run(["bash", installer], env=rt_env, check=True, capture_output=True, text=True)
    assert dl_counts["deb"] == 1, f"Expected idempotent skip on matching SHA-256: {dl_counts}"
finally:
    deb_srv.shutdown()
    deb_srv.server_close()

print("Verified nido-hub --self-test, nidooffice DOCX/XLSX round-trip, desktop CLI delegation, and auto-latest updater.")

# Verify packaged pkcs11-helper (for NidoOffice USB Token / SmartCard PKCS#11 signing)
p11_helper = sandbox / "rootfs/usr/bin/pkcs11-helper"
assert p11_helper.is_file(), f"Missing packaged pkcs11-helper at {p11_helper}"
subprocess.run([sys.executable, str(p11_helper), "--self-test"], check=True, capture_output=True, text=True)
r_missing = subprocess.run(
    [sys.executable, str(p11_helper), "pkcs11-list"],
    capture_output=True,
    text=True,
    env={k: v for k, v in os.environ.items() if not k.startswith("GENOFFICE_")},
)
assert r_missing.returncode == 2 and "PKCS11_ERR:MISSING_MODULE_PATH" in r_missing.stderr, (
    f"Unexpected pkcs11-list missing-env result: rc={r_missing.returncode} stderr={r_missing.stderr}"
)
print("Verified packaged pkcs11-helper ABI layout and error protocol.")
PY

# Verify nido-pkg-helper narrowly scoped security, GPG fingerprint enforcement, DEB822 & package-specific APT pinning
python3 - "${SANDBOX}" "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/apt/keyrings/ubuntu-nido.asc" "${ROOT_DIR}/iso-builder/scripts/customize.sh" <<'PY'
import json, os, subprocess, sys
from pathlib import Path

sandbox = Path(sys.argv[1])
sample_key = Path(sys.argv[2])
customize_sh = Path(sys.argv[3]).read_text(encoding="utf-8")
helper = sandbox / "rootfs/usr/libexec/nido-hub/nido-pkg-helper"
assert helper.is_file(), f"Missing packaged nido-pkg-helper at {helper}"

# 1. Verify upstream Ubuntu security updates are NEVER rewritten to third-party mirrors
assert "security.ubuntu.com/ubuntu|https://mirror.bizflycloud.vn" not in customize_sh, (
    "customize.sh must NEVER rewrite security.ubuntu.com to a third-party mirror!"
)

# 2. Protected core packages cannot be removed
for protected in ("systemd", "fwupd", "unattended-upgrades", "ubuntu-desktop-minimal", "nido-hub", "snapd"):
    r = subprocess.run(
        [sys.executable, str(helper), "--dry-run", "remove-apt", protected],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode != 0, f"Expected protected package removal to fail for {protected}"

# 3. Verify GPG fingerprint check + DEB822 + package-specific APT pinning in isolated --root
fake_root = sandbox / "fake-root"
fake_root.mkdir(parents=True, exist_ok=True)
real_fpr = "AC980631CB8BA9D2602A0990528AF2083F61DC2F"
test_vendors = sandbox / "test-vendors.json"
test_vendors.write_text(json.dumps({
    "schemaVersion": 1,
    "vendors": {
        "mozilla": {
            "id": "mozilla",
            "name": "Mozilla Official APT",
            "uri": "https://packages.mozilla.org/apt",
            "suites": ["mozilla"],
            "components": ["main"],
            "keyUrl": "https://packages.mozilla.org/apt/repo-signing-key.gpg",
            "expectedFingerprints": [real_fpr],
            "pinOrigin": "packages.mozilla.org",
            "allowedPackages": ["firefox", "firefox-l10n-*"],
            "pinPriority": 1000,
            "blockUbuntuPackage": "firefox",
        },
        "badvendor": {
            "id": "badvendor",
            "name": "Bad Key Vendor",
            "uri": "https://example.invalid/apt",
            "suites": ["stable"],
            "components": ["main"],
            "keyUrl": "https://example.invalid/key.gpg",
            "expectedFingerprints": ["0000000000000000000000000000000000000000"],
            "pinOrigin": "example.invalid",
            "allowedPackages": ["demo"],
            "pinPriority": 500,
        },
    }
}), encoding="utf-8")

env = dict(os.environ)
env["NIDO_PKG_ARCH"] = "arm64"
env["NIDO_UBUNTU_CODENAME"] = "resolute"
env["HOME"] = "/nonexistent-nido-chroot-home"

# Wrong GPG fingerprint MUST fail hard
bad_r = subprocess.run(
    [sys.executable, str(helper), "--vendors-file", str(test_vendors), "--root", str(fake_root), "--key-file", str(sample_key), "setup-vendor", "badvendor"],
    env=env, capture_output=True, text=True, check=False,
)
assert bad_r.returncode != 0 and "fingerprint" in bad_r.stderr.lower(), f"Expected GPG fingerprint mismatch failure: {bad_r.stderr}"

# Non-allowlisted package for mozilla MUST fail hard
unauth_pkg = subprocess.run(
    [sys.executable, str(helper), "--vendors-file", str(test_vendors), "--root", str(fake_root), "--key-file", str(sample_key), "--dry-run", "install-vendor", "mozilla", "libc6"],
    env=env, capture_output=True, text=True, check=False,
)
assert unauth_pkg.returncode != 0, "Vendor install must reject non-allowlisted package libc6"

# Valid fingerprint & allowlisted package succeeds and writes DEB822 + package-specific pin
ok_r = subprocess.run(
    [sys.executable, str(helper), "--vendors-file", str(test_vendors), "--root", str(fake_root), "--key-file", str(sample_key), "--dry-run", "install-vendor", "mozilla", "firefox"],
    env=env, capture_output=True, text=True, check=True,
)
assert "NIDO_PROGRESS:done:100:0:" in ok_r.stdout
src_txt = (fake_root / "etc/apt/sources.list.d/nido-vendor-mozilla.sources").read_text(encoding="utf-8")
assert "Architectures: arm64" in src_txt and "Signed-By: /etc/apt/keyrings/nido-vendor-mozilla.gpg" in src_txt
pin_txt = (fake_root / "etc/apt/preferences.d/nido-vendor-mozilla").read_text(encoding="utf-8")
assert "Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 100" in pin_txt
assert "Package: firefox firefox-l10n-*\nPin: origin packages.mozilla.org\nPin-Priority: 1000" in pin_txt
assert "Package: firefox\nPin: release o=Ubuntu*\nPin-Priority: -1" in pin_txt
assert (fake_root / "etc/apt/keyrings/nido-vendor-mozilla.gpg").is_file()
helper_src = helper.read_text(encoding="utf-8")
assert "--allow-downgrades" in helper_src, "nido-pkg-helper must pass --allow-downgrades for pinPriority >= 1000 vendors (e.g. Mozilla DEB replacing Ubuntu 1:1snap1)"
pol_src = (helper.parents[2] / "share/polkit-1/actions/com.nidoholdings.nidohub.pkg.policy").read_text(encoding="utf-8")
assert "<allow_active>auth_admin</allow_active>" in pol_src and "auth_admin_keep" not in pol_src, "Polkit policy must enforce auth_admin"

# Verify root security guards prevent unprivileged --vendors-file / --key-file / --root overrides & core deb replacement
import importlib.machinery as _im, importlib.util as _iu, types as _tp
_hloader = _im.SourceFileLoader("nidopkghelper_test", str(helper))
_hspec = _iu.spec_from_loader("nidopkghelper_test", _hloader)
_hmod = _iu.module_from_spec(_hspec)
_hloader.exec_module(_hmod)
for bad_ns in (
    _tp.SimpleNamespace(vendors_file=test_vendors, key_file=None, root=Path("/")),
    _tp.SimpleNamespace(vendors_file=_hmod.DEFAULT_VENDORS_PATH, key_file=sample_key, root=Path("/")),
    _tp.SimpleNamespace(vendors_file=_hmod.DEFAULT_VENDORS_PATH, key_file=None, root=fake_root),
):
    denied = False
    try:
        _hmod.enforce_root_security_guards(bad_ns, effective_uid=0)
    except PermissionError:
        denied = True
    assert denied, f"Root security guard failed to block override: {bad_ns}"

hook_120 = Path(sys.argv[3]).parents[1] / "config/hooks/live/0120-nido-native-first.hook.chroot"
assert hook_120.is_file() and "--allow-downgrades" in hook_120.read_text(encoding="utf-8"), (
    "0120-nido-native-first.hook.chroot must pass --allow-downgrades for Firefox DEB"
)
# Verify on-demand printer APT install & configure-printer dry-run + URI guard
pr_apt = subprocess.run(
    [sys.executable, str(helper), "--dry-run", "install-apt", "printer-driver-escpr", "printer-driver-gutenprint"],
    env=env, capture_output=True, text=True, check=True,
)
assert "Dry-run OK: install-apt printer-driver-escpr printer-driver-gutenprint" in pr_apt.stdout
pr_cfg = subprocess.run(
    [
        sys.executable, str(helper), "--dry-run", "configure-printer",
        "--name", "NidoLAN_192_168_1_50",
        "--uri", "socket://192.168.1.50:9100",
        "--model", "drv:///sample.drv/pxlmono.ppd",
    ],
    env=env, capture_output=True, text=True, check=True,
)
assert "Dry-run OK: configure-printer NidoLAN_192_168_1_50" in pr_cfg.stdout
pr_bad = subprocess.run(
    [
        sys.executable, str(helper), "--dry-run", "configure-printer",
        "--name", "bad;rm",
        "--uri", "file:///etc/passwd",
        "--model", "../../evil.ppd",
    ],
    env=env, capture_output=True, text=True, check=False,
)
assert pr_bad.returncode != 0, "configure-printer must reject unsafe queue name/URI/model"
print("Verified nido-pkg-helper GPG fingerprint enforcement, DEB822 .sources, package-specific APT pinning, --allow-downgrades, protected-package guard, and printer adapter dry-run.")
PY

# Verify Vietnamese fontconfig XML, Sen Viet Telex+VNI IME sync, and AI OFF CLI policy
python3 - "${ROOT_DIR}" <<'PY'
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(sys.argv[1])

# 1. Validate Vietnamese Fontconfig rules XML
fc_xml = root / "packages/nido-desktop-settings/files/etc/fonts/conf.d/52-ubuntu-nido-vietnamese.conf"
assert fc_xml.is_file(), "Missing 52-ubuntu-nido-vietnamese.conf"
fc_text = fc_xml.read_text(encoding="utf-8")
ET.fromstring(fc_text)
for token in (
    "Ubuntu",
    "Ubuntu Sans",
    "Ubuntu Mono",
    "UbuntuMono",
    "Be Vietnam Pro",
    "Noto Sans",
    "Noto Mono",
    "Liberation Mono",
    "<rejectfont>",
    "/usr/share/fonts/truetype/ubuntu/*",
):
    assert token in fc_text, f"Missing expected font rule {token!r} in 52-ubuntu-nido-vietnamese.conf"

# 2. Validate dconf defaults, installer white-label/font bind, Live welcome suppression & IME sync
dconf_txt = (root / "packages/nido-desktop-settings/files/etc/dconf/db/local.d/00-ubuntu-nido-settings").read_text(encoding="utf-8")
for expected_dconf in (
    "('ibus', 'vielotus')",
    "('ibus', 'vielotus-vni')",
    "monospace-font-name='Noto Mono 10'",
    "accent-color='teal'",
    "gtk-theme='Yaru-prussiangreen-dark'",
    "icon-theme='Yaru-prussiangreen-dark'",
    "dock-position='BOTTOM'",
    "dock-fixed=true",
    "extend-height=false",
    "show-home=false",
    "button-layout=':minimize,maximize,close'",
    "center-new-windows=true",
    "org.gnome.Geary.desktop",
    "folder-children=['System', 'Utilities', 'Media']",
    "report-technical-problems=false",
):
    assert expected_dconf in dconf_txt, f"Missing expected dconf setting: {expected_dconf}"

# Verify PackageKit APT/D-Bus hooks are diverted wherever packagekit.service is masked
for pk_file in (
    root / "iso-builder/config/hooks/live/0110-nido-minimal-prune.hook.chroot",
    root / "packages/nido-desktop-settings/DEBIAN/postinst",
):
    pk_txt = pk_file.read_text(encoding="utf-8")
    assert "/usr/share/dbus-1/system-services/org.freedesktop.PackageKit.service" in pk_txt, f"Missing PackageKit D-Bus divert in {pk_file}"
    assert "/etc/apt/apt.conf.d/20packagekit" in pk_txt, f"Missing 20packagekit divert in {pk_file}"

# Verify real catalog.json hides x86_64-only apps on arm64/aarch64 while preserving arm64 apps
sys.dont_write_bytecode = True
sys.path.insert(0, str(root / "packages/nido-hub/files/usr/lib/nido-hub"))
from app_store import NidoAppStore
real_cat = root / "packages/nido-desktop-settings/files/etc/nido-hub/catalog.json"
real_vendors = root / "packages/nido-hub/files/usr/share/nido-hub/vendors.json"
arm64_ids = {it["id"] for it in NidoAppStore(real_cat, real_vendors, host_arch="aarch64").load_catalog_entries(include_arch_probe=False)}
amd64_ids = {it["id"] for it in NidoAppStore(real_cat, real_vendors, host_arch="amd64").load_catalog_entries(include_arch_probe=False)}
for x86_only in ("discord-flatpak", "spotify-flatpak", "blender-flatpak"):
    assert x86_only not in arm64_ids, f"{x86_only} must not be displayed on arm64/aarch64"
    assert x86_only in amd64_ids, f"{x86_only} should remain available on amd64"
for arm64_ok in ("core", "firefox-deb", "google-chrome", "brave-browser", "vscode", "docker-ce", "telegram-flatpak"):
    assert arm64_ok in arm64_ids, f"{arm64_ok} should be displayed on arm64"

unit_txt = (root / "packages/nido-desktop-settings/files/usr/lib/systemd/system/nido-installer-fonts.service").read_text(encoding="utf-8")
assert "ConditionPathExists=/snap/ubuntu-desktop-bootstrap" in unit_txt, "nido-installer-fonts.service must key off /snap/ubuntu-desktop-bootstrap, not boot=casper"
assert "ConditionKernelCommandLine=boot=casper" not in unit_txt

for svg_name in ("nidoos-logo-light.svg", "nidoos-logo-dark.svg"):
    svg_txt = (root / "packages/nido-desktop-settings/stage/usr/share/desktop-provision/images" / svg_name).read_text(encoding="utf-8")
    assert "<text" not in svg_txt, f"{svg_name} must use pure vector paths (no <text> for flutter_svg)"

gdm_txt = (root / "packages/nido-desktop-settings/files/etc/dconf/db/gdm.d/01-nidoos-logo").read_text(encoding="utf-8")
assert "accent-color='teal'" in gdm_txt and "logo='/usr/share/pixmaps/nidoos-logo.png'" in gdm_txt

wl_txt = (root / "packages/nido-desktop-settings/files/usr/share/desktop-provision/whitelabel.yaml").read_text(encoding="utf-8")
for wl_tok in ('accent-color: "#0D9488"', 'nidoos-logo-dark.svg', 'nidoos-logo-light.svg', 'nidoos-storage-icon.svg'):
    assert wl_tok in wl_txt, f"Missing expected whitelabel token: {wl_tok}"

ime_script = root / "packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/ensure-vietnamese-ime"
subprocess.run([sys.executable, str(ime_script), "--self-test"], check=True)

gschema_override = (root / "packages/nido-desktop-settings/files/usr/share/glib-2.0/schemas/90_nidoos-input-sources.gschema.override").read_text(encoding="utf-8")
for expected_gs in (
    "[org.gnome.desktop.input-sources]",
    "sources=[('ibus', 'vielotus'), ('ibus', 'vielotus-vni'), ('xkb', 'us')]",
    "mru-sources=[('ibus', 'vielotus'), ('xkb', 'us'), ('ibus', 'vielotus-vni')]",
    "per-window=false",
    "show-all-sources=true",
):
    assert expected_gs in gschema_override, f"Missing expected gschema override line: {expected_gs}"

bind_fonts_script = root / "packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/bind-installer-fonts"
subprocess.run(["bash", str(bind_fonts_script), "--self-test"], check=True)

tray_script = root / "packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/nidobox-tray"
subprocess.run([sys.executable, str(tray_script), "--self-test"], check=True)
doctor_script = root / "packages/nido-desktop-settings/files/usr/bin/nido-doctor"
subprocess.run([sys.executable, str(doctor_script), "--self-test"], check=True)
sec_verify_script = root / "packages/nido-desktop-settings/files/usr/bin/nido-security-verify"
subprocess.run([sys.executable, str(sec_verify_script), "--self-test"], check=True)
fw_script = root / "packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/configure-firewall"
subprocess.run(["bash", str(fw_script), "--self-test"], check=True)
subprocess.run([sys.executable, str(root / "scripts/harden_dist.py"), "--self-test"], check=True)
subprocess.run(["node", str(root / "scripts/harden_js.mjs"), "--self-test"], check=True)
subprocess.run(["bash", str(root / "scripts/native-build/build_native_debs.sh"), "--dry-run"], check=True)
vm_gsk_script = root / "packages/nido-desktop-settings/files/usr/lib/systemd/user-environment-generators/60-nido-vm-gsk"
subprocess.run(["bash", str(vm_gsk_script), "--self-test"], check=True)

# Verify nido-welcome suppresses NidoHub & nidoagent cold-start during Live ISO and stamps installed skills sync
gw_unit_txt = (root / "packages/nidoai-gateway/files/usr/lib/systemd/user/nidoai-gateway.service").read_text(encoding="utf-8")
assert "ConditionPathExists=%h/.config/ubuntu-nido/ai-policy.json" in gw_unit_txt, "nidoai-gateway.service must wait for ai-policy.json"
assert "After=dbus.socket gnome-keyring-daemon.service" in gw_unit_txt, "nidoai-gateway.service must order after dbus & gnome-keyring"
for bad_user_opt in ("PrivateTmp=", "ProtectKernelTunables=", "ProtectKernelModules=", "ProtectControlGroups="):
    assert bad_user_opt not in gw_unit_txt, f"Unprivileged user unit nidoai-gateway.service must not set root-only {bad_user_opt}"
gw_aa_txt = (root / "packages/nidoai-gateway/files/etc/apparmor.d/nidoai-gateway").read_text(encoding="utf-8")
for req_aa in ("/usr/bin/secret-tool ix,", "/usr/bin/mkdir ix,", "/usr/bin/chmod ix,", "unix,", "dbus (send, receive) bus=session,", "network inet dgram,"):
    assert req_aa in gw_aa_txt, f"nidoai-gateway AppArmor profile missing {req_aa}"
assert "/usr/bin/secret-tool PUx" not in gw_aa_txt, "PUx fails under NoNewPrivileges=yes; secret-tool must use ix"
cleanup_unit_txt = (root / "packages/nido-desktop/files/usr/lib/systemd/system/nido-native-first-cleanup.service").read_text(encoding="utf-8")
assert "Type=simple" in cleanup_unit_txt, "nido-native-first-cleanup.service must use Type=simple so first boot is non-blocking"
cleanup_txt = (root / "packages/nido-desktop/files/usr/libexec/ubuntu-nido/native-first-cleanup").read_text(encoding="utf-8")
assert "disabled-extensions=['ding@rastersoft.com']" in cleanup_txt, "native-first-cleanup must disable ding@rastersoft.com on installed systems"
assert "timedatectl set-ntp true" in cleanup_txt, "native-first-cleanup must enable NTP clock sync on first boot"

welcome_script = root / "packages/nido-desktop-settings/files/usr/local/bin/nido-welcome"
with tempfile.TemporaryDirectory() as wtmp:
    wtmp_path = Path(wtmp)
    fake_bin = wtmp_path / "bin"
    fake_bin.mkdir()
    sentinel = wtmp_path / "hub-launched"
    skills_log = wtmp_path / "skills-sync.log"
    fake_skills_src = wtmp_path / "skills-src"
    fake_skills_src.mkdir()
    fake_hub = fake_bin / "nido-hub"
    fake_hub.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
    fake_hub.chmod(0o755)
    fake_agent = fake_bin / "nidoagent"
    fake_agent.write_text(f"#!/bin/sh\necho \"$@\" >> {skills_log}\n", encoding="utf-8")
    fake_agent.chmod(0o755)
    wenv = dict(os.environ)
    wenv["DISPLAY"] = ":99"
    wenv["HOME"] = wtmp
    wenv["XDG_CONFIG_HOME"] = str(wtmp_path / ".config")
    wenv["NIDO_SKILLS_SRC"] = str(fake_skills_src)
    wenv["PATH"] = f"{fake_bin}:{wenv.get('PATH', '')}"
    wenv["NIDO_TEST_LIVE_SESSION"] = "1"
    subprocess.run(["bash", str(welcome_script)], env=wenv, check=True)
    assert not sentinel.exists(), "nido-welcome must NOT auto-launch nido-hub during a Live ISO session!"
    assert not skills_log.exists(), "nido-welcome must NOT spawn nidoagent during a Live ISO session!"

    cfg_dir = wtmp_path / ".config" / "ubuntu-nido"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai-policy.json").write_text('{"setupCompleted": true, "aiEnabled": true}\n', encoding="utf-8")
    os.utime(fake_skills_src, (time.time() - 60, time.time() - 60))
    wenv["NIDO_TEST_LIVE_SESSION"] = "0"
    subprocess.run(["bash", str(welcome_script)], env=wenv, check=True)
    stamp_file = cfg_dir / "skills-synced.stamp"
    for _ in range(30):
        if stamp_file.is_file():
            break
        time.sleep(0.05)
    assert stamp_file.is_file(), "nido-welcome should stamp skills-synced.stamp on first installed run"
    assert skills_log.read_text(encoding="utf-8").splitlines() == ["skills sync"]
    # Second login with unchanged skills dir must skip spawning Node/nidoagent completely
    subprocess.run(["bash", str(welcome_script)], env=wenv, check=True)
    time.sleep(0.15)
    assert skills_log.read_text(encoding="utf-8").splitlines() == ["skills sync"]

# 3. Verify nidoagent respects NIDOAI_ENABLED=0 (AI OFF policy) and unsigned-in preflight guard (zero fetch failed)
cli_mjs = root / "packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs"
nidoai_cli_js = root / "packages/nidoai-gateway/src/cli.js"
sandbox_empty_home = Path(tempfile.mkdtemp(prefix="nidohub-off-"))
env_off = dict(
    os.environ,
    NIDOAI_ENABLED="0",
    HOME=str(sandbox_empty_home),
    XDG_CONFIG_HOME=str(sandbox_empty_home / ".config"),
    NIDOAI_HOME=str(sandbox_empty_home / ".nidoai"),
)
try:
    os.makedirs(sandbox_empty_home, exist_ok=True)
    # AI OFF: nidoagent status & ask must refuse cleanly
    st_off = subprocess.run(["node", str(cli_mjs), "status"], env=env_off, capture_output=True, text=True, check=True)
    st_json = json.loads(st_off.stdout)
    assert st_json.get("ok") is True and st_json.get("aiEnabled") is False and st_json.get("aiReady") is False, st_json
    ask_off = subprocess.run(["node", str(cli_mjs), "ask", "Xin chào"], env=env_off, capture_output=True, text=True, check=False)
    assert ask_off.returncode != 0 and "đang TẮT" in ask_off.stderr, (ask_off.returncode, ask_off.stderr)

    # AI OFF: nidoai serve with ai-policy.json aiEnabled=false must remove stale runtime.json and exit 0 immediately
    off_cfg = Path(sandbox_empty_home) / ".config" / "ubuntu-nido"
    off_state = Path(sandbox_empty_home) / ".nidoai"
    off_cfg.mkdir(parents=True, exist_ok=True)
    off_state.mkdir(parents=True, exist_ok=True)
    (off_cfg / "ai-policy.json").write_text('{"aiEnabled":false,"setupCompleted":true}\n', encoding="utf-8")
    stale_rt = off_state / "runtime.json"
    stale_rt.write_text('{"baseUrl":"http://127.0.0.1:18090","clientToken":"stale"}\n', encoding="utf-8")
    env_policy_off = {
        k: v for k, v in os.environ.items() if k != "NIDOAI_ENABLED"
    }
    env_policy_off.update({
        "HOME": str(sandbox_empty_home),
        "XDG_CONFIG_HOME": str(Path(sandbox_empty_home) / ".config"),
        "NIDOAI_HOME": str(off_state),
        "NIDOAI_SECRET_STORE": "file",
    })
    gw_off = subprocess.run(["node", str(nidoai_cli_js), "serve"], env=env_policy_off, capture_output=True, text=True, check=True, timeout=5)
    assert "gateway_disabled" in gw_off.stdout and not stale_rt.exists(), (gw_off.stdout, stale_rt.exists())

    # AI ON (no policy disable), but 0 signed-in accounts & no running gateway -> status returns structured JSON (no fetch failed), ask & chat exit cleanly without fetch failed
    env_unsigned = {
        k: v for k, v in os.environ.items()
        if k not in ("NIDOAI_ENABLED", "NIDOAI_BASE_URL", "NIDOAI_GATEWAY_PORT", "NIDOAI_PORT", "NIDOAI_CLIENT_TOKEN")
    }
    unsigned_home = Path(sandbox_empty_home) / "unsigned"
    unsigned_cfg = unsigned_home / ".config"
    unsigned_nidoai = unsigned_home / ".nidoai"
    (unsigned_cfg / "ubuntu-nido").mkdir(parents=True, exist_ok=True)
    unsigned_nidoai.mkdir(parents=True, exist_ok=True)
    env_unsigned.update({
        "HOME": str(unsigned_home),
        "XDG_CONFIG_HOME": str(unsigned_cfg),
        "NIDOAI_HOME": str(unsigned_nidoai),
        "NIDOAI_PORT": "18999",
        "NIDOAI_GATEWAY_PORT": "18999",
    })
    st_unsigned = subprocess.run(["node", str(cli_mjs), "status"], env=env_unsigned, capture_output=True, text=True, check=True)
    st_u = json.loads(st_unsigned.stdout)
    assert st_u.get("ok") is True and st_u.get("gatewayReady") is False and st_u.get("aiReady") is False, st_u
    assert "fetch failed" not in (st_unsigned.stdout + st_unsigned.stderr).lower()

    ask_unsigned = subprocess.run(["node", str(cli_mjs), "ask", "--json", "hello"], env=env_unsigned, capture_output=True, text=True, check=False)
    assert ask_unsigned.returncode != 0
    ask_u_err = json.loads(ask_unsigned.stderr)
    assert ask_u_err.get("ok") is False and "chưa đăng nhập" in ask_u_err.get("error", "").lower()
    assert "fetch failed" not in ask_unsigned.stderr.lower()

    chat_unsigned = subprocess.run(["node", str(cli_mjs), "chat"], input="hello\nexit\n", env=env_unsigned, capture_output=True, text=True, check=False)
    assert chat_unsigned.returncode != 0
    assert "chưa đăng nhập" in chat_unsigned.stderr.lower() and "fetch failed" not in chat_unsigned.stderr.lower()

    # Verify SIGTERM to live nidoai serve cleans up runtime.json
    sig_proc = subprocess.Popen(
        ["node", str(nidoai_cli_js), "serve"],
        env={**env_unsigned, "NIDOAI_PORT": "18993", "NIDOAI_SECRET_STORE": "file"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        sig_rt = unsigned_nidoai / "runtime.json"
        import time as _t
        for _ in range(40):
            if sig_rt.is_file():
                break
            _t.sleep(0.1)
        assert sig_rt.is_file(), "Expected live nidoai serve to write runtime.json"
        sig_proc.terminate()
        sig_proc.wait(timeout=5)
        assert not sig_rt.exists(), "Expected SIGTERM shutdown to remove stale runtime.json"
    finally:
        if sig_proc.poll() is None:
            sig_proc.kill()
            sig_proc.wait(timeout=3)
finally:
    import shutil as _sh
    _sh.rmtree(sandbox_empty_home, ignore_errors=True)
print("Verified Vietnamese Fontconfig XML, Sen Viet Telex+VNI sync self-test, AI OFF & unsigned-in guards.")
PY
pass "Live nidoai-gateway <-> nidoagent-cli, nido-hub, and nidooffice smoke tests passed"

echo "=== 6. Layered Casper Updater, Live-Layer Reconciliation & CDROM Signature Tests ==="
python3 - "${ROOT_DIR}/iso-builder/scripts/update_install_sources.py" \
           "${ROOT_DIR}/iso-builder/scripts/reconcile_live_layer.py" \
           "${ROOT_DIR}/iso-builder/scripts/verify_cdrom_signature.py" \
           "${ROOT_DIR}/iso-builder/scripts/customize.sh" <<'PY'
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

upd_spec = importlib.util.spec_from_file_location("upd", sys.argv[1])
upd = importlib.util.module_from_spec(upd_spec)
upd_spec.loader.exec_module(upd)

# Fixture 1: Ubuntu 24.04 style
fixture_24 = """- default: true
  description:
    en: Minimal Ubuntu desktop
  id: ubuntu-desktop-minimal
  locale_support: locale-only
  name:
    en: Default selection
  path: minimal.squashfs
  size: 1111111
  type: fsimage
- description:
    en: Full desktop apps
  id: ubuntu-desktop
  name:
    en: Extended selection
  path: minimal.standard.squashfs
  size: 2222222
  type: fsimage-layered
"""
out_24 = upd.update_install_sources(fixture_24, "minimal.squashfs", 987654321, "Ubuntu-Nido Desktop")
assert "987654321" in out_24, "Updated size missing (24.04)"
assert "fsimage-layered" not in out_24, "Layered entry should be pruned (24.04)"
assert "Ubuntu-Nido Desktop" in out_24, "Display name not updated (24.04)"

# Fixture 2: Real Ubuntu 26.04 style with kernel header, trailing fsimage-layered entry, and version: 2 at EOF
fixture_26 = """kernel:
  default: linux-generic-hwe-24.04
sources:
- default: true
  description:
    en: A minimal but usable Ubuntu Desktop.
  id: ubuntu-desktop-minimal
  locale_support: langpack
  name:
    en: Ubuntu Desktop
  path: minimal.squashfs
  preinstalled_langs:
  - de
  - en
  - ''
  size: 1111111
  type: fsimage
  variant: desktop
  variations:
    minimal:
      path: minimal.squashfs
      size: 1111111
    minimal-enhanced-secureboot:
      path: minimal.enhanced-secureboot.squashfs
      size: 1111111
      snapd_system_label: enhanced-secureboot-desktop
- description:
    en: Full Ubuntu Desktop
  id: ubuntu-desktop-standard
  name:
    en: Standard Desktop
  path: minimal.standard.squashfs
  size: 2222222
  type: fsimage-layered
version: 2
"""
out_26 = upd.update_install_sources(fixture_26, "minimal.squashfs", 4080263168, "Ubuntu-Nido Desktop")
assert out_26.startswith("kernel:\n"), f"Header lost in 26.04 output: {out_26}"
assert "4080263168" in out_26, "Updated size missing (26.04)"
assert "fsimage-layered" not in out_26, "Layered entry must be pruned (26.04)"
assert "version: 2" in out_26.splitlines(), f"Top-level version: 2 lost in 26.04 output: {out_26}"
assert "locale_support: locale-only" in out_26, "locale_support must switch to locale-only"
assert "preinstalled_langs" not in out_26, "preinstalled_langs must be stripped"
assert "variations" not in out_26, "variations must be stripped"
assert "minimal.enhanced-secureboot.squashfs" not in out_26, "enhanced-secureboot variation must be stripped"
assert "Ubuntu-Nido Desktop" in out_26, "Display name not updated (26.04)"
assert "A minimal but usable Ubuntu Desktop." not in out_26, "Ubuntu description leaked in install-sources.yaml"
assert "NidoOS AI-Native Minimal Desktop." in out_26, "NidoOS description missing in install-sources.yaml"

# Unit test reconcile_live_layer.py without requiring root mknod
rec_spec = importlib.util.spec_from_file_location("rec", sys.argv[2])
rec = importlib.util.module_from_spec(rec_spec)
rec_spec.loader.exec_module(rec)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    base_dir = root / "base"
    live_dir = root / "live"
    (base_dir / "var/lib/dpkg").mkdir(parents=True)
    (live_dir / "var/lib/dpkg/info").mkdir(parents=True)
    (live_dir / "etc").mkdir(parents=True)
    (live_dir / "usr/share/glib-2.0/schemas").mkdir(parents=True)

    (base_dir / "var/lib/dpkg/status").write_text(
        "Package: libc6\nStatus: install ok installed\nVersion: 2.39-nido\n\n"
        "Package: dracut\nStatus: install ok installed\nVersion: 110-11\n\n"
        "Package: dracut-core\nStatus: install ok installed\nVersion: 110-11\n\n"
        "Package: vielotus\nStatus: install ok installed\nVersion: 2.0.8\n\n"
        "Package: nidobox\nStatus: install ok installed\nVersion: 1.4.7\n\n"
        "Package: nido-hub\nStatus: install ok installed\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (live_dir / "var/lib/dpkg/status").write_text(
        "Package: libc6\nStatus: install ok installed\nVersion: 2.39-stale\n\n"
        "Package: dracut\nStatus: install ok installed\nVersion: 110-11\n\n"
        "Package: casper\nStatus: install ok installed\nVersion: 26.04.2\n\n"
        "Package: libaio1t64\nStatus: install ok installed\nArchitecture: arm64\nVersion: 0.3.113\n",
        encoding="utf-8",
    )

    # Regular .list files represent packages added by minimal.standard.live.squashfs
    (live_dir / "var/lib/dpkg/info/casper.list").write_text("/usr/bin/casper\n", encoding="utf-8")
    (live_dir / "var/lib/dpkg/info/libaio1t64:arm64.list").write_text("/usr/lib/libaio.so\n", encoding="utf-8")
    # Simulate whiteout markers for dracut / dracut-core without needing root mknod
    wo_dracut = live_dir / "var/lib/dpkg/info/dracut.list"
    wo_dracut_core = live_dir / "var/lib/dpkg/info/dracut-core.list"
    wo_dracut.write_text("", encoding="utf-8")
    wo_dracut_core.write_text("", encoding="utf-8")

    # Stale monolithic caches inside live_delta
    stale_ld = live_dir / "etc/ld.so.cache"
    stale_schemas = live_dir / "usr/share/glib-2.0/schemas/gschemas.compiled"
    stale_locale = live_dir / "etc/default/locale"
    (live_dir / "etc/default").mkdir(parents=True)
    (live_dir / "var/cache/fontconfig").mkdir(parents=True)
    stale_fc = live_dir / "var/cache/fontconfig/deadbeef-le64.cache-9"
    stale_ld.write_bytes(b"stale-ld")
    stale_schemas.write_bytes(b"stale-schemas")
    stale_locale.write_text("LANG=C.UTF-8\n", encoding="utf-8")
    stale_fc.write_bytes(b"stale-fc")

    rem, add, unlinked = rec.reconcile_live_layer(
        base_dir,
        live_dir,
        whiteout_predicate=lambda p: p.name in ("dracut.list", "dracut-core.list"),
    )
    assert rem == 2 and add == 2 and unlinked == 4, f"Unexpected counts: {(rem, add, unlinked)}"
    assert not stale_ld.exists(), "Stale ld.so.cache was not unlinked"
    assert not stale_schemas.exists(), "Stale gschemas.compiled was not unlinked"
    assert not stale_locale.exists(), "Stale live etc/default/locale was not unlinked"
    assert not stale_fc.exists(), "Stale live var/cache/fontconfig cache was not unlinked"

    merged = (live_dir / "var/lib/dpkg/status").read_text(encoding="utf-8")
    assert "nidobox" in rec.PROTECTED_CUSTOM_PREFIXES, "nidobox missing from PROTECTED_CUSTOM_PREFIXES"
    assert "Version: 2.39-nido" in merged and "2.39-stale" not in merged, "Base libc6 should win"
    assert "Package: vielotus" in merged and "Package: nidobox" in merged and "Package: nido-hub" in merged, "Custom pkgs lost"
    assert "Package: casper" in merged and "Package: libaio1t64" in merged, "Live pkgs missing"
    assert "Package: dracut\n" not in merged and "Package: dracut-core\n" not in merged, "Whiteout pkgs not pruned"

    # Verify CLI --strip-overlay-xattrs runs cleanly
    subprocess.run(
        [sys.executable, sys.argv[2], "--strip-overlay-xattrs", str(live_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

# Unit test verify_cdrom_signature.py with real ephemeral Ed25519 Deb822 inline Signed-By key
vcs_spec = importlib.util.spec_from_file_location("vcs", sys.argv[3])
vcs = importlib.util.module_from_spec(vcs_spec)
vcs_spec.loader.exec_module(vcs)

customize_txt = Path(sys.argv[4]).read_text(encoding="utf-8")
assert "stashed-cdrom-sources" in customize_txt, "customize.sh must stash cdrom.sources instead of deleting it"
assert "restore_and_verify_cdrom_sources" in customize_txt, "customize.sh must restore and verify cdrom.sources"
assert "APT::Update::Error-Mode=any" in customize_txt, "customize.sh must run offline chroot apt-get update gate on /cdrom"
assert "UnitMasked" in customize_txt, "customize.sh offline apt-get update gate must fail on UnitMasked DBus errors"
assert "test -f /usr/lib/nido-hub/self_test.py" in customize_txt, "customize.sh must verify nido-hub self_test.py"
assert 'Path(p).unlink' not in customize_txt, "customize.sh must never unlink /etc/apt/sources.list.d/*cdrom*"
utils_txt = (Path(sys.argv[4]).parent / "utils.sh").read_text(encoding="utf-8")
assert "squashfs/cdrom" in utils_txt, "utils.sh umount_chroot must safely unmount squashfs/cdrom"

import os, shutil
if shutil.which("gpg"):
    with tempfile.TemporaryDirectory(prefix="ncdt-", dir="/tmp" if Path("/tmp").is_dir() else None) as td:
        troot = Path(td)
        gnupg = troot / "gnupg"
        gnupg.mkdir(mode=0o700)
        genv = os.environ.copy()
        genv["GNUPGHOME"] = str(gnupg)
        subprocess.run(
            ["gpg", "--batch", "--passphrase", "", "--quick-generate-key",
             "NidoOS Test CDROM Key <test@nido-holdings.com>", "ed25519", "sign", "never"],
            env=genv, check=True, capture_output=True,
        )
        pub_asc = subprocess.run(
            ["gpg", "--batch", "--armor", "--export", "test@nido-holdings.com"],
            env=genv, check=True, capture_output=True, text=True,
        ).stdout

        iso_tree = troot / "iso"
        rootfs = troot / "rootfs"
        rel_file = iso_tree / "dists/resolute/Release"
        sig_file = iso_tree / "dists/resolute/Release.gpg"
        rel_file.parent.mkdir(parents=True)
        rel_file.write_text("Suite: resolute\nComponents: main\n", encoding="utf-8")
        subprocess.run(
            ["gpg", "--batch", "--yes", "--detach-sign", "--output", str(sig_file), str(rel_file)],
            env=genv, check=True, capture_output=True,
        )

        # Format Deb822 inline Signed-By block exactly like Ubuntu 26.04 cdrom.sources
        deb822_key_lines = []
        for ln in pub_asc.strip().splitlines():
            deb822_key_lines.append(" ." if not ln.strip() else " " + ln)
        cdrom_sources = rootfs / "etc/apt/sources.list.d/cdrom.sources"
        cdrom_sources.parent.mkdir(parents=True)
        cdrom_sources.write_text(
            "Types: deb\n"
            "URIs: file:/cdrom\n"
            "Suites: resolute\n"
            "Components: main restricted\n"
            "Signed-By:\n"
            + "\n".join(deb822_key_lines)
            + "\n",
            encoding="utf-8",
        )
        (rootfs / "etc/apt/trusted.gpg.d").mkdir(parents=True)

        assert vcs.verify_iso_cdrom_signatures(iso_tree, rootfs) == 1, "Expected valid cdrom.sources signature to pass"

        # Simulate the exact NO_PUBKEY bug (deleting cdrom.sources) and ensure verifier catches it
        cdrom_sources.unlink()
        failed_as_expected = False
        try:
            vcs.verify_iso_cdrom_signatures(iso_tree, rootfs)
        except RuntimeError:
            failed_as_expected = True
        assert failed_as_expected, "verify_cdrom_signature must fail when cdrom.sources is missing"

print("Verified update_install_sources.py (24.04 & 26.04), reconcile_live_layer.py, and verify_cdrom_signature.py cleanly.")
PY
pass "update_install_sources.py, reconcile_live_layer.py, and verify_cdrom_signature.py unit tests passed"

# Verify FAST_ISO=1, OUTPUT_DIR, BASE_ISO_CACHE_DIR, and local-arm64.sh --dry-run
bash -c '
  set -e
  SCRIPT_DIR="${1}/iso-builder"
  export TARGET_ARCH=arm64 UBUNTU_VERSION=26.04 FAST_ISO=1
  unset SQUASHFS_COMP
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/scripts/config.sh"
  [ "$SQUASHFS_COMP" = "zstd" ]
  [[ "${SQUASHFS_OPTS[*]}" == *"-Xcompression-level 1"* ]]
  FAST_ISO=0
  unset SQUASHFS_COMP
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/scripts/config.sh"
  [[ "${SQUASHFS_OPTS[*]}" == *"-Xcompression-level 6"* ]]
' _ "${ROOT_DIR}"

LOCAL_TEST_EXT="$(mktemp -d)"
trap 'rm -rf "$LOCAL_TEST_EXT"' EXIT
bash "${ROOT_DIR}/iso-builder/scripts/local-arm64.sh" --help >/dev/null
DRY_OUT="$(bash "${ROOT_DIR}/iso-builder/scripts/local-arm64.sh" --ext-dir "$LOCAL_TEST_EXT" --dry-run)"
grep -q "FAST_ISO / Comp : 1 / zstd" <<< "$DRY_OUT"
rm -rf "$LOCAL_TEST_EXT"
trap - EXIT
pass "FAST_ISO=1 config and local-arm64.sh dry-run verification passed"

echo "=== 7. Public Repo Hygiene, Secret & Binary Leak Audit ==="
python3 - "${ROOT_DIR}" <<'PY'
import subprocess, sys
from pathlib import Path

root = Path(sys.argv[1])
assert (root / "LICENSE").is_file(), "Missing LICENSE file for public repo"
assert (root / "THIRD_PARTY_PROVENANCE.md").is_file(), "Missing THIRD_PARTY_PROVENANCE.md"
assert (root / "docs/INCIDENTS.md").is_file(), "Missing docs/INCIDENTS.md"
readme_text = (root / "README.md").read_text(encoding="utf-8")
assert '<a href="docs/README.vi.md">' in readme_text, "README.md must use pure HTML <a> language switcher"

# Query non-gitignored files (tracked + untracked-not-ignored)
res = subprocess.run(
    ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
    check=True,
    capture_output=True,
)
rel_paths = [p for p in res.stdout.decode("utf-8").split("\0") if p]
assert rel_paths, "No non-ignored files found in repo"
assert "AGENTS.md" not in rel_paths, "AGENTS.md must be gitignored from public repo"

forbidden_exts = (
    ".deb", ".dmg", ".pkg", ".iso", ".exe", ".msi", ".appimage",
    ".zip", ".tar.gz", ".p12", ".provisionprofile", ".mobileprovision",
    ".pem", ".key", ".pyc",
)
forbidden_tokens = (
    "/" + "Users/",
    "/" + "Volumes/",
    "SSD" + "128",
    "gh" + "o_",
    "gh" + "p_",
    "github_" + "pat_",
    "AIza" + "Sy",
    "sk-" + "proj-",
    "BEGIN " + "PRIVATE KEY",
    "BEGIN RSA " + "PRIVATE KEY",
)
forbidden_ci_tokens = (
    "cloud" + "flare",
    "zero-" + "cost",
    "0-" + "cost",
    "planned-" + "phase",
)

for rel in rel_paths:
    low = rel.lower()
    if any(low.endswith(ext) for ext in forbidden_exts):
        raise AssertionError(f"Forbidden binary/cert/artifact in git tree: {rel}")
    if low.endswith((".svg", ".png")):
        assert rel in readme_text, f"Orphan unembedded image in git tree: {rel}"
    p = root / rel
    if not p.is_file():
        continue
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for tok in forbidden_tokens:
        if tok in text:
            raise AssertionError(f"Forbidden local path or secret prefix '{tok}' found in {rel}")
    low_text = text.lower()
    for ctok in forbidden_ci_tokens:
        if ctok in low_text:
            raise AssertionError(f"Forbidden internal infra/roadmap detail '{ctok}' found in {rel}")

print(f"Audited {len(rel_paths)} non-ignored files: zero binaries, zero secrets, zero local paths, zero orphan assets.")
PY
pass "Public repo hygiene and leak audit passed"

echo ""
echo "✅ All ${PASS_COUNT} verification gates passed cleanly!"
