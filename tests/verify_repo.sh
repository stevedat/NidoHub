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
  "${ROOT_DIR}/iso-builder/build.sh"
  "${ROOT_DIR}/iso-builder/scripts/config.sh"
  "${ROOT_DIR}/iso-builder/scripts/utils.sh"
  "${ROOT_DIR}/iso-builder/scripts/extract.sh"
  "${ROOT_DIR}/iso-builder/scripts/boot_config.sh"
  "${ROOT_DIR}/iso-builder/scripts/overlay.sh"
  "${ROOT_DIR}/iso-builder/scripts/customize.sh"
  "${ROOT_DIR}/iso-builder/scripts/chroot_shell.sh"
  "${ROOT_DIR}/iso-builder/scripts/repack.sh"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0100-nido-gnome-setup.hook.chroot"
  "${ROOT_DIR}/iso-builder/config/hooks/live/0150-vielotus-im.hook.chroot"
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
print("Audited all 3 .deb archives: valid ar format, zero secret files.")
PY
pass "Built and audited 3 .deb packages (zero secrets)"

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
        for pkg in ("nidoai-gateway", "nidoagent-cli", "nido-desktop-settings"):
            assert f"Package: {pkg}" in pkg_text, f"{pkg} missing in {suite}/{arch}"
        digest = hashlib.sha256(pkg_gz.read_bytes()).hexdigest()
        assert digest in rel_text, f"SHA256 mismatch for {pkg_gz} in Release"
print("Verified static APT repo Release & Packages.gz checksums for resolute & noble (amd64/arm64).")
PY
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

for deb_name in ("nidoai-gateway_0.1.0_all.deb", "nidoagent-cli_0.1.0_all.deb"):
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

env \
  HOME="${SANDBOX}/home" \
  NIDOAI_HOME="${SANDBOX}/home/.nidoai" \
  NIDOAI_PORT="${TEST_PORT}" \
  NIDOAI_CLIENT_TOKEN="${TEST_TOKEN}" \
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.ts" \
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
  NIDOAI_ENTRY="${SANDBOX}/rootfs/usr/lib/nidoai/src/cli.ts" \
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
pass "Live nidoai-gateway <-> nidoagent-cli smoke test passed"

echo "=== 6. Layered Casper install-sources.yaml Updater Unit Test ==="
python3 - "${ROOT_DIR}/iso-builder/scripts/update_install_sources.py" <<'PY'
import importlib.util, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("upd", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fixture = """- default: true
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
out = mod.update_install_sources(fixture, "minimal.squashfs", 987654321, "Ubuntu-Nido Desktop")
assert "987654321" in out, "Updated size missing"
assert "fsimage-layered" not in out, "Layered entry should be pruned"
assert "Ubuntu-Nido Desktop" in out, "Display name not updated"
print("Verified update_install_sources.py transforms layered Ubuntu install-sources.yaml cleanly.")
PY
pass "update_install_sources.py unit test passed"

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
