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
  "${ROOT_DIR}/packages/nidoagent-cli/files/usr/bin/nidoagent"
  "${ROOT_DIR}/packages/nido-desktop-settings/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/local/bin/nido-welcome"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/local/bin/nidoagent-quicklaunch"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giao-cho-NidoAgent"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Gui-qua-NidoDrop-LAN"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giai-nen-Chuan-Tieng-Viet"
  "${ROOT_DIR}/packages/nido-hub/DEBIAN/postinst"
  "${ROOT_DIR}/packages/nido-hub/files/usr/bin/nido-hub"
  "${ROOT_DIR}/packages/nidooffice-cli/files/usr/bin/nidooffice"
  "${ROOT_DIR}/packages/nido-desktop/DEBIAN/postinst"
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
  "${ROOT_DIR}/iso-builder/config/hooks/live/0150-vielotus-im.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0160-nidobox.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0170-nidooffice-desktop.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0200-nido-ai-stack.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0900-nido-plymouth.hook.chroot"
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
}
for name, dims in expected.items():
    p = asset_dir / name
    data = p.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{name}: bad PNG header"
    assert data[12:16] == b"IHDR", f"{name}: missing IHDR"
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == dims, f"{name}: expected {dims}, got {(w, h)}"
print("Verified PNG headers and exact dimensions for all 3 branding assets.")
PY
pass "Branding PNGs generated and verified"

echo "=== 3. Portable .deb Build & Secret Audit ==="
bash "${ROOT_DIR}/packages/build-all.sh"
python3 - "${ROOT_DIR}/packages/dist" <<'PY'
import gzip, io, lzma, subprocess, sys, tarfile
from pathlib import Path

dist = Path(sys.argv[1])
expected = [
    "nidoai-gateway_0.1.0_all.deb",
    "nidoagent-cli_0.1.0_all.deb",
    "nido-desktop-settings_0.1.0_all.deb",
    "nido-hub_0.1.0_all.deb",
    "nidooffice-cli_0.1.0_all.deb",
    "nido-desktop_0.1.0_all.deb",
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
        if fname == "nidoai-gateway_0.1.0_all.deb":
            assert "./usr/lib/nidoai/src/cli.mjs" in names, f"{fname}: missing pure JS cli.mjs entry"
            assert "./usr/lib/nidoai/src/cli.ts" not in names, f"{fname}: stale cli.ts found (breaks stock Ubuntu nodejs)"
        elif fname == "nido-hub_0.1.0_all.deb":
            assert "./usr/share/icons/hicolor/scalable/apps/com.nidoholdings.NidoHub.svg" in names, (
                f"{fname}: missing scalable NidoHub SVG icon"
            )
            assert "./usr/share/icons/hicolor/256x256/apps/com.nidoholdings.NidoHub.png" in names, (
                f"{fname}: missing 256x256 NidoHub PNG icon"
            )
        elif fname == "nido-desktop_0.1.0_all.deb":
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
                for dep in ("nido-desktop-settings", "nido-hub", "nidoagent-cli", "nidoai-gateway", "nidooffice-cli", "vielotus", "nidobox"):
                    assert dep in ctrl_txt, f"{fname}: missing dependency {dep}"
print(f"Audited all {len(expected)} .deb archives: valid ar format, zero secret files.")
PY
pass "Built and audited 6 .deb packages (zero secrets)"

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
        for pkg in ("nidoai-gateway", "nidoagent-cli", "nido-desktop-settings", "nido-hub", "nidooffice-cli", "nido-desktop"):
            assert f"Package: {pkg}" in pkg_text, f"{pkg} missing in {suite}/{arch}"
        if (repo.parent / "packages" / "vendor" / f"nidobox_1.4.0_{arch}.deb").is_file():
            assert "Package: nidobox" in pkg_text, f"nidobox missing in {suite}/{arch}"
        digest = hashlib.sha256(pkg_gz.read_bytes()).hexdigest()
        assert digest in rel_text, f"SHA256 mismatch for {pkg_gz} in Release"
print("Verified static APT repo Release & Packages.gz checksums for resolute & noble (amd64/arm64).")
PY

DEV_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nidohub-devver.XXXXXX")"
python3 "${ROOT_DIR}/scripts/build_deb_portable.py" \
  --source-dir "${ROOT_DIR}/packages/nido-hub" \
  --extra-files "${ROOT_DIR}/packages/nido-hub/stage" \
  --version-suffix "+dev1" \
  --output "${DEV_TEST_DIR}/nido-hub_0.1.0+dev1_all.deb"
python3 "${ROOT_DIR}/scripts/build_apt_repo.py" \
  --dist-dir "${DEV_TEST_DIR}" \
  --out-dir "${DEV_TEST_DIR}/apt" >/dev/null
python3 - "${DEV_TEST_DIR}" "${ROOT_DIR}/packages/nido-hub/DEBIAN/control" <<'PY'
import gzip, sys
from pathlib import Path
tmp = Path(sys.argv[1])
ctrl = Path(sys.argv[2]).read_text(encoding="utf-8")
assert "Version: 0.1.0\n" in ctrl, "Source DEBIAN/control must stay untouched at 0.1.0"
pkg_txt = gzip.decompress((tmp / "apt/dists/resolute/main/binary-arm64/Packages.gz").read_bytes()).decode("utf-8")
assert "Version: 0.1.0+dev1\n" in pkg_txt, f"Expected dev suffix in APT metadata: {pkg_txt}"
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
    "nidoai-gateway_0.1.0_all.deb",
    "nidoagent-cli_0.1.0_all.deb",
    "nido-hub_0.1.0_all.deb",
    "nidooffice-cli_0.1.0_all.deb",
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

mkdir -p "${SANDBOX}/home/.nidoai"
chmod 700 "${SANDBOX}/home/.nidoai"
TEST_PORT=18991
TEST_TOKEN="ubuntu_nido_smoke_test_token_0123456789abcdef"

node --check "${ROOT_DIR}/packages/nidoai-gateway/src/cli.mjs"

env \
  HOME="${SANDBOX}/home" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.mjs" \
  "${SANDBOX}/rootfs/usr/bin/nidoai" doctor >/dev/null

env \
  HOME="${SANDBOX}/home" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_PORT="${TEST_PORT}" \
  NIDOAI_CLIENT_TOKEN="${TEST_TOKEN}" \
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.mjs" \
  "${SANDBOX}/rootfs/usr/bin/nidoai" serve > "${SANDBOX}/gateway.log" 2>&1 &
GW_PID=$!

python3 - "${TEST_PORT}" <<'PY'
import sys, time, urllib.error, urllib.request

port = sys.argv[1]
url = f"http://127.0.0.1:{port}/healthz"
for _ in range(50):
    try:
        with urllib.request.urlopen(url, timeout=0.5) as resp:
            if resp.status == 200:
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                assert hdrs.get("x-content-type-options") == "nosniff", f"Missing nosniff header: {hdrs}"
                assert hdrs.get("x-frame-options") == "DENY", f"Missing X-Frame-Options: {hdrs}"
                assert hdrs.get("cache-control") == "no-store", f"Missing Cache-Control: {hdrs}"
                print(f"Gateway healthy with security headers at {url}")
                break
    except Exception:
        time.sleep(0.1)
else:
    raise SystemExit(f"Gateway did not respond at {url}")

try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=1.0)
    raise SystemExit("Expected 401 on unauthenticated /v1/models")
except urllib.error.HTTPError as err:
    assert err.code == 401, f"Expected 401 on /v1/models, got {err.code}"
PY

NIDOAI_STATUS_OUT="$(env \
  HOME="${SANDBOX}/home" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
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
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_GATEWAY_PORT="${TEST_PORT}" \
  NIDOAI_CLIENT_TOKEN="${TEST_TOKEN}" \
  NIDOAGENT_CLI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoagent-cli/cli.mjs" \
  NIDOAGENT_STANDALONE=1 \
  "${SANDBOX}/rootfs/usr/bin/nidoagent" status)"

python3 - "$STATUS_OUT" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
assert data["ok"] is True, f"Expected ok=true, got {data}"
assert data["tokenConfigured"] is True, "Expected tokenConfigured=true"
assert data["gateway"]["status"] == "ok", f"Unexpected gateway payload: {data}"
print("Verified nidoagent status output against live nidoai-gateway.")
PY

NIDOHUB_APP_ENTRY="${SANDBOX}/rootfs/usr/lib/nido-hub/app.py" \
  "${SANDBOX}/rootfs/usr/bin/nido-hub" --self-test

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
grep -Eq '^Ubuntu-Nido 26\.04 LTS "Resolute Raccoon" - Release arm64 \([0-9]{8}\)$' "${BOOT_TMP}/custom/.disk/info" || {
  echo "ERROR: boot_config.sh produced invalid .disk/info: $(cat "${BOOT_TMP}/custom/.disk/info")" >&2
  rm -rf "${BOOT_TMP}"
  exit 1
}
rm -rf "${BOOT_TMP}"
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
md_in = "# Báo cáo kiểm thử\n\nNội dung: Báo cáo Ubuntu-Nido — Tiếng Việt chuẩn\n\n| Hạng mục | Giá trị |\n| --- | --- |\n| GPU | 100 |\n"
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

xlsx_path = sandbox / "summary.xlsx"
table_in = "| Thành phần | Trạng thái |\n| --- | --- |\n| NidoAgent | 200 |\n| Sen Việt | =TEST |\n"
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
print("Verified nido-hub --self-test, nidooffice DOCX/XLSX round-trip, and desktop CLI delegation.")
PY
pass "Live nidoai-gateway <-> nidoagent-cli, nido-hub, and nidooffice smoke tests passed"

echo "=== 6. Layered Casper Updater & Live-Layer Reconciliation Unit Tests ==="
python3 - "${ROOT_DIR}/iso-builder/scripts/update_install_sources.py" \
           "${ROOT_DIR}/iso-builder/scripts/reconcile_live_layer.py" <<'PY'
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
        "Package: vielotus\nStatus: install ok installed\nVersion: 2.0.4\n\n"
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
    stale_ld.write_bytes(b"stale-ld")
    stale_schemas.write_bytes(b"stale-schemas")

    rem, add, unlinked = rec.reconcile_live_layer(
        base_dir,
        live_dir,
        whiteout_predicate=lambda p: p.name in ("dracut.list", "dracut-core.list"),
    )
    assert rem == 2 and add == 2 and unlinked == 2, f"Unexpected counts: {(rem, add, unlinked)}"
    assert not stale_ld.exists(), "Stale ld.so.cache was not unlinked"
    assert not stale_schemas.exists(), "Stale gschemas.compiled was not unlinked"

    merged = (live_dir / "var/lib/dpkg/status").read_text(encoding="utf-8")
    assert "Version: 2.39-nido" in merged and "2.39-stale" not in merged, "Base libc6 should win"
    assert "Package: vielotus" in merged and "Package: nido-hub" in merged, "Custom pkgs lost"
    assert "Package: casper" in merged and "Package: libaio1t64" in merged, "Live pkgs missing"
    assert "Package: dracut\n" not in merged and "Package: dracut-core\n" not in merged, "Whiteout pkgs not pruned"

    # Verify CLI --strip-overlay-xattrs runs cleanly
    subprocess.run(
        [sys.executable, sys.argv[2], "--strip-overlay-xattrs", str(live_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

print("Verified update_install_sources.py (24.04 & 26.04) and reconcile_live_layer.py cleanly.")
PY
pass "update_install_sources.py and reconcile_live_layer.py unit tests passed"

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
