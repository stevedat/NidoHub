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
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/bind-installer-fonts"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/local/bin/nido-welcome"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/usr/local/bin/nidoagent-quicklaunch"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giao-cho-NidoAgent"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Gui-qua-NidoDrop-LAN"
  "${ROOT_DIR}/packages/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giai-nen-Chuan-Tieng-Viet"
  "${ROOT_DIR}/packages/nido-hub/DEBIAN/postinst"
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
            assert "./usr/lib/nidoai/src/security/secret-store.js" in names, f"{fname}: missing unified secret-store.js"
            assert "./usr/lib/nidoai/src/cli.ts" not in names, f"{fname}: stale cli.ts found (breaks stock Ubuntu nodejs)"
        elif fname == "nido-desktop-settings_0.2.0_all.deb":
            for req_cfg in (
                "./etc/dconf/profile/user",
                "./etc/dconf/profile/gdm",
                "./etc/dconf/db/local.d/00-ubuntu-nido-settings",
                "./etc/dconf/db/gdm.d/01-nidoos-logo",
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
                "./usr/local/share/applications/thunderbird_thunderbird.desktop",
                "./usr/local/share/applications/snap-store_snap-store.desktop",
                "./usr/local/share/applications/desktop-security-center_desktop-security-center.desktop",
                "./usr/local/share/applications/yelp.desktop",
                "./usr/lib/systemd/system/nido-installer-fonts.service",
                "./usr/libexec/ubuntu-nido/bind-installer-fonts",
                "./usr/libexec/ubuntu-nido/ensure-vietnamese-ime",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-office/SKILL.md",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-box/SKILL.md",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-agent/SKILL.md",
                "./usr/share/ubuntu-nido/skills/ubuntu-nido-email/SKILL.md",
            ):
                assert req_cfg in names, f"{fname}: missing {req_cfg}"
        elif fname == "nido-hub_0.2.0_all.deb":
            for req_hub in (
                "./usr/lib/nido-hub/agent_accounts.py",
                "./usr/lib/nido-hub/ai_discovery.py",
                "./usr/lib/nido-hub/app_store.py",
                "./usr/lib/nido-hub/markdown_render.py",
                "./usr/libexec/nido-hub/nido-pkg-helper",
                "./usr/share/nido-hub/vendors.json",
                "./usr/share/polkit-1/actions/com.nidoholdings.nidohub.pkg.policy",
                "./usr/share/icons/hicolor/scalable/apps/com.nidoholdings.NidoHub.svg",
                "./usr/share/icons/hicolor/256x256/apps/com.nidoholdings.NidoHub.png",
                "./usr/share/icons/hicolor/scalable/apps/nidoos-logo.svg",
                "./usr/share/icons/hicolor/scalable/apps/distributor-logo.svg",
                "./usr/share/icons/hicolor/scalable/apps/nidoos-symbolic.svg",
                "./usr/share/icons/hicolor/scalable/actions/nidoos-app-grid-symbolic.svg",
            ):
                assert req_hub in names, f"{fname}: missing {req_hub}"
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
                for dep in ("nido-desktop-settings", "nido-hub", "nidoagent-cli", "nidoai-gateway", "nidooffice-cli", "nidooffice-desktop", "vielotus", "nidobox (>= 1.4.7)", "geary", "showtime | totem"):
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
        nidobox_vendor = sorted((repo.parent / "packages" / "vendor").glob(f"nidobox_*_{arch}.deb"))
        if nidobox_vendor:
            assert len(nidobox_vendor) == 1, f"Duplicate nidobox vendor debs for {arch}: {[p.name for p in nidobox_vendor]}"
            assert "Package: nidobox" in pkg_text, f"nidobox missing in {suite}/{arch}"
            nb_ver = ""
            for stanza in pkg_text.strip().split("\n\n"):
                lines = stanza.splitlines()
                if "Package: nidobox" in lines:
                    for ln in lines:
                        if ln.startswith("Version:"):
                            nb_ver = ln.split(":", 1)[1].strip()
                            break
            def _ver_tuple(v: str) -> tuple[int, ...]:
                base = v.split("-", 1)[0].split("+", 1)[0]
                return tuple(int(x) for x in base.split(".") if x.isdigit())
            assert _ver_tuple(nb_ver) >= (1, 4, 7), f"Expected nidobox >= 1.4.7 in {suite}/{arch}, got {nb_ver!r}"
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

mkdir -p "${SANDBOX}/home/.nidoai"
chmod 700 "${SANDBOX}/home/.nidoai"
TEST_PORT=18991
TEST_TOKEN="ubuntu_nido_smoke_test_token_0123456789abcdef"

node --check "${ROOT_DIR}/packages/nidoai-gateway/src/cli.mjs"
node --check "${ROOT_DIR}/packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs"

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
                print(f"Gateway healthy with security headers & vision/anthropic capabilities at {url}")
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
print("Verified nidoagent status output + capabilities against live nidoai-gateway.")
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
hook_120 = Path(sys.argv[3]).parents[1] / "config/hooks/live/0120-nido-native-first.hook.chroot"
assert hook_120.is_file() and "--allow-downgrades" in hook_120.read_text(encoding="utf-8"), (
    "0120-nido-native-first.hook.chroot must pass --allow-downgrades for Firefox DEB"
)
print("Verified nido-pkg-helper GPG fingerprint enforcement, DEB822 .sources, package-specific APT pinning, --allow-downgrades, and protected-package guard.")
PY

# Verify Vietnamese fontconfig XML, Sen Viet Telex+VNI IME sync, and AI OFF CLI policy
python3 - "${ROOT_DIR}" <<'PY'
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(sys.argv[1])

# 1. Validate Vietnamese Fontconfig rules XML
fc_xml = root / "packages/nido-desktop-settings/files/etc/fonts/conf.d/52-ubuntu-nido-vietnamese.conf"
assert fc_xml.is_file(), "Missing 52-ubuntu-nido-vietnamese.conf"
fc_text = fc_xml.read_text(encoding="utf-8")
ET.fromstring(fc_text)
for token in ("Ubuntu", "Ubuntu Sans", "Be Vietnam Pro", "Noto Sans", "Noto Sans Mono"):
    assert token in fc_text, f"Missing expected font family {token!r} in 52-ubuntu-nido-vietnamese.conf"

# 2. Validate dconf defaults, installer white-label/font bind, Live welcome suppression & IME sync
dconf_txt = (root / "packages/nido-desktop-settings/files/etc/dconf/db/local.d/00-ubuntu-nido-settings").read_text(encoding="utf-8")
for expected_dconf in (
    "('ibus', 'vielotus')",
    "('ibus', 'vielotus-vni')",
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

bind_fonts_script = root / "packages/nido-desktop-settings/files/usr/libexec/ubuntu-nido/bind-installer-fonts"
subprocess.run(["bash", str(bind_fonts_script), "--self-test"], check=True)

# Verify nido-welcome suppresses auto-opening NidoHub during Live ISO session
welcome_script = root / "packages/nido-desktop-settings/files/usr/local/bin/nido-welcome"
with tempfile.TemporaryDirectory() as wtmp:
    fake_bin = Path(wtmp) / "bin"
    fake_bin.mkdir()
    sentinel = Path(wtmp) / "hub-launched"
    fake_hub = fake_bin / "nido-hub"
    fake_hub.write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
    fake_hub.chmod(0o755)
    wenv = dict(os.environ)
    wenv["DISPLAY"] = ":99"
    wenv["HOME"] = wtmp
    wenv["PATH"] = f"{fake_bin}:{wenv.get('PATH', '')}"
    wenv["NIDO_TEST_LIVE_SESSION"] = "1"
    subprocess.run(["bash", str(welcome_script)], env=wenv, check=True)
    assert not sentinel.exists(), "nido-welcome must NOT auto-launch nido-hub during a Live ISO session!"

# 3. Verify nidoagent respects NIDOAI_ENABLED=0 (AI OFF policy)
cli_mjs = root / "packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs"
env_off = dict(os.environ, NIDOAI_ENABLED="0")
st_off = subprocess.run(["node", str(cli_mjs), "status"], env=env_off, capture_output=True, text=True, check=True)
st_json = json.loads(st_off.stdout)
assert st_json.get("ok") is True and st_json.get("aiEnabled") is False and st_json.get("aiReady") is False, st_json
ask_off = subprocess.run(["node", str(cli_mjs), "ask", "Xin chào"], env=env_off, capture_output=True, text=True, check=False)
assert ask_off.returncode != 0 and "đang TẮT" in ask_off.stderr, (ask_off.returncode, ask_off.stderr)
print("Verified Vietnamese Fontconfig XML, Sen Viet Telex+VNI sync self-test, and AI OFF CLI guard.")
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
        "Package: vielotus\nStatus: install ok installed\nVersion: 2.0.4\n\n"
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
