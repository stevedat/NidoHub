#!/usr/bin/env bash
# Build 100%-Vietnamese Flutter Linux release bundle for ubuntu-desktop-bootstrap
# (matching Ubuntu 26.04 rev 590 upstream commit & Flutter SDK) for Live ISO overlay.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
L10N_DIR="${REPO_ROOT}/iso-builder/config/installer-l10n"

# Exact upstream commit & Flutter version of Ubuntu 26.04 ubuntu-desktop-bootstrap rev 590
INSTALLER_COMMIT="${NIDO_INSTALLER_COMMIT:-0f280bdd96530a115ba0d0d7a72c8d418a189f8f}"
FLUTTER_VERSION="${NIDO_INSTALLER_FLUTTER:-3.38.10}"
FLUTTER_CACHE="${NIDO_FLUTTER_CACHE:-${HOME}/.cache/nido-flutter-${FLUTTER_VERSION}}"
TARGET_ARCH=""
OUT_DIR="${REPO_ROOT}/iso-builder/work/installer-vi-bundle"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      TARGET_ARCH="${2:-}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-}"
      shift 2
      ;;
    --commit)
      INSTALLER_COMMIT="${2:-}"
      shift 2
      ;;
    --flutter)
      FLUTTER_VERSION="${2:-}"
      shift 2
      ;;
    --flutter-cache)
      FLUTTER_CACHE="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: build_installer_vi.sh [--arch amd64|arm64] [--out-dir <dir>] [--commit <sha>] [--flutter <ver>] [--dry-run]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$TARGET_ARCH" ]]; then
  case "$(uname -m)" in
    x86_64|amd64) TARGET_ARCH="amd64" ;;
    aarch64|arm64) TARGET_ARCH="arm64" ;;
    *)
      echo "Unsupported host architecture: $(uname -m)" >&2
      exit 2
      ;;
  esac
fi

if [[ "$TARGET_ARCH" != "amd64" && "$TARGET_ARCH" != "arm64" ]]; then
  echo "Unsupported --arch: $TARGET_ARCH (expected amd64 or arm64)" >&2
  exit 2
fi

validate_arbs() {
  python3 - "$L10N_DIR" <<'PY'
import json, re, sys
from pathlib import Path

l10n = Path(sys.argv[1])
expected = {
    "ubuntu_bootstrap_vi.arb": (447, ("appTitle", "tryOrInstallTitle", "installationTypeErase", "confirmPageTitle", "installationCompleteTitle")),
    "ubuntu_provision_vi.arb": (116, ("accessibilityPageTitle", "identityPageTitle", "networkPageTitle", "timezonePageTitle")),
    "ubuntu_localizations_vi.arb": (121, ("countryCode", "languageName", "nextLabel", "backLabel", "doneLabel")),
}
total = 0
for fname, (min_keys, req_keys) in expected.items():
    p = l10n / fname
    assert p.is_file(), f"Missing installer l10n file: {p}"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("@@locale") == "vi", f"{fname}: @@locale must be 'vi'"
    keys = [k for k in data if not k.startswith("@")]
    assert len(keys) >= min_keys, f"{fname}: expected >= {min_keys} keys, got {len(keys)}"
    for rk in req_keys:
        assert rk in data and isinstance(data[rk], str) and data[rk].strip(), f"{fname}: missing or empty key {rk}"
    for k in keys:
        v = data[k]
        assert isinstance(v, str) and v.strip(), f"{fname}[{k}]: empty string"
        # Ensure braces are balanced for Flutter ICU message parser
        assert v.count("{") == v.count("}"), f"{fname}[{k}]: unbalanced braces in {v!r}"
        assert not re.search(r"\{\s*\}", v), f"{fname}[{k}]: empty placeholder in {v!r}"
    total += len(keys)
print(f"Validated 3 installer Vietnamese ARB files ({total} keys total).")
PY
}

validate_arbs

ARB_SHA="$(
  python3 - "$L10N_DIR" <<'PY'
import hashlib, sys
from pathlib import Path
h = hashlib.sha256()
for name in ("ubuntu_bootstrap_vi.arb", "ubuntu_provision_vi.arb", "ubuntu_localizations_vi.arb"):
    h.update((Path(sys.argv[1]) / name).read_bytes())
print(h.hexdigest())
PY
)"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] Target arch       : ${TARGET_ARCH}"
  echo "[DRY-RUN] Upstream commit   : ${INSTALLER_COMMIT}"
  echo "[DRY-RUN] Flutter SDK       : ${FLUTTER_VERSION}"
  echo "[DRY-RUN] ARB bundle SHA256 : ${ARB_SHA}"
  echo "[DRY-RUN] Output bundle dir : ${OUT_DIR}"
  exit 0
fi

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: build_installer_vi.sh requires a Linux host (ubuntu-24.04 / ubuntu-24.04-arm) to compile the Flutter Linux bundle." >&2
  exit 1
fi

# Reuse cached bundle immediately if commit + flutter + arch + ARB SHA all match
if [[ -x "${OUT_DIR}/ubuntu_bootstrap" && -f "${OUT_DIR}/lib/libapp.so" && -f "${OUT_DIR}/manifest.json" ]]; then
  if python3 - "${OUT_DIR}/manifest.json" "$TARGET_ARCH" "$INSTALLER_COMMIT" "$FLUTTER_VERSION" "$ARB_SHA" <<'PY'
import json, sys
from pathlib import Path
m = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
ok = (
    m.get("arch") == sys.argv[2]
    and m.get("commit") == sys.argv[3]
    and m.get("flutter") == sys.argv[4]
    and m.get("arbSha256") == sys.argv[5]
)
raise SystemExit(0 if ok else 1)
PY
  then
    echo "==> Reusing verified cached Vietnamese installer bundle in ${OUT_DIR} (arb=${ARB_SHA:0:12})."
    exit 0
  fi
fi

# 1. Ensure exact Flutter SDK is available
if [[ ! -x "${FLUTTER_CACHE}/bin/flutter" ]]; then
  echo "==> Cloning Flutter ${FLUTTER_VERSION} into ${FLUTTER_CACHE}..."
  rm -rf "$FLUTTER_CACHE"
  mkdir -p "$(dirname "$FLUTTER_CACHE")"
  git clone --branch "${FLUTTER_VERSION}" --depth 1 https://github.com/flutter/flutter.git "$FLUTTER_CACHE"
fi

export PATH="${FLUTTER_CACHE}/bin:${FLUTTER_CACHE}/bin/cache/dart-sdk/bin:${PATH}"
flutter config --enable-linux-desktop --no-analytics >/dev/null 2>&1 || true
flutter precache --linux --no-android --no-ios --no-web --no-macos --no-windows --no-fuchsia

WORK="$(mktemp -d "${TMPDIR:-/tmp}/nido-installer-vi.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

export PUB_CACHE="${WORK}/pub-cache"
mkdir -p "$PUB_CACHE"

# 2. Fetch exact upstream canonical/ubuntu-desktop-provision commit
SRC_DIR="${WORK}/ubuntu-desktop-provision"
echo "==> Fetching canonical/ubuntu-desktop-provision @ ${INSTALLER_COMMIT}..."
mkdir -p "$SRC_DIR"
git -C "$SRC_DIR" init -q
git -C "$SRC_DIR" remote add origin https://github.com/canonical/ubuntu-desktop-provision.git
git -C "$SRC_DIR" fetch --depth 1 origin "$INSTALLER_COMMIT"
git -C "$SRC_DIR" checkout -q FETCH_HEAD

# 3. Bootstrap monorepo lockfile cleanly BEFORE patching pub-cache ubuntu_localizations
echo "==> Bootstrapping Dart/Flutter workspace via melos..."
dart pub global activate melos 6.3.2 >/dev/null
export MELOS_SDK_PATH=auto
(cd "$SRC_DIR" && dart pub global run melos bootstrap)

# 4. Patch ALL cached ubuntu_localizations-* versions in-place inside isolated PUB_CACHE & regenerate Dart l10n
PATCHED_LOC_COUNT=0
shopt -s nullglob
for ub_loc_dir in "${PUB_CACHE}/hosted/pub.dev"/ubuntu_localizations-*; do
  [[ -d "$ub_loc_dir" ]] || continue
  echo "==> Patching Vietnamese l10n (121/121) in ${ub_loc_dir}..."
  cp "${L10N_DIR}/ubuntu_localizations_vi.arb" "${ub_loc_dir}/lib/src/l10n/ubuntu_vi.arb"
  (cd "$ub_loc_dir" && flutter gen-l10n)
  grep -q "Tiếp theo" "${ub_loc_dir}"/lib/src/l10n/ubuntu_localizations_vi*.dart || {
    echo "ERROR: ubuntu_localizations Vietnamese codegen did not emit expected strings in ${ub_loc_dir}" >&2
    exit 1
  }
  PATCHED_LOC_COUNT=$((PATCHED_LOC_COUNT + 1))
done
shopt -u nullglob
if [[ "$PATCHED_LOC_COUNT" -eq 0 ]]; then
  echo "ERROR: ubuntu_localizations not found in isolated PUB_CACHE (${PUB_CACHE})" >&2
  exit 1
fi

# 5. Overlay 100% Vietnamese ARBs into ubuntu_provision & ubuntu_bootstrap and regenerate l10n
echo "==> Applying Vietnamese l10n into ubuntu_provision (116/116) & ubuntu_bootstrap (447/447)..."
cp "${L10N_DIR}/ubuntu_provision_vi.arb" "${SRC_DIR}/packages/ubuntu_provision/lib/src/l10n/ubuntu_provision_vi.arb"
cp "${L10N_DIR}/ubuntu_bootstrap_vi.arb" "${SRC_DIR}/apps/ubuntu_bootstrap/lib/l10n/ubuntu_bootstrap_vi.arb"

(cd "${SRC_DIR}/packages/ubuntu_provision" && flutter gen-l10n)
(cd "${SRC_DIR}/apps/ubuntu_bootstrap" && flutter gen-l10n)

grep -q "Tạo tài khoản của bạn" "${SRC_DIR}/packages/ubuntu_provision/lib/src/l10n/ubuntu_provision_localizations_vi.dart" || {
  echo "ERROR: ubuntu_provision Vietnamese codegen verification failed" >&2
  exit 1
}
grep -q "Sẵn sàng cài đặt" "${SRC_DIR}/apps/ubuntu_bootstrap/lib/l10n/ubuntu_bootstrap_localizations_vi.dart" || {
  echo "ERROR: ubuntu_bootstrap Vietnamese codegen verification failed" >&2
  exit 1
}

# 6. Compile Flutter Linux release bundle
echo "==> Building Flutter Linux release bundle (${TARGET_ARCH})..."
(cd "${SRC_DIR}/apps/ubuntu_bootstrap" && flutter build linux --release)

FLUTTER_ARCH="x64"
[[ "$TARGET_ARCH" == "arm64" ]] && FLUTTER_ARCH="arm64"
BUNDLE_SRC="${SRC_DIR}/apps/ubuntu_bootstrap/build/linux/${FLUTTER_ARCH}/release/bundle"
if [[ ! -x "${BUNDLE_SRC}/ubuntu_bootstrap" || ! -f "${BUNDLE_SRC}/lib/libapp.so" ]]; then
  echo "ERROR: Compiled Flutter bundle missing in ${BUNDLE_SRC}" >&2
  exit 1
fi

# 7. Verify compiled Dart AOT libapp.so contains live Vietnamese strings from all 3 l10n packages
python3 - "${BUNDLE_SRC}/lib/libapp.so" <<'PY'
import sys
from pathlib import Path

blob = Path(sys.argv[1]).read_bytes()
required = [
    # ubuntu_bootstrap
    "Thiết lập ổ đĩa",
    "Sẵn sàng cài đặt",
    "Cài đặt hoàn tất",
    # ubuntu_provision
    "Trợ năng",
    "Tạo tài khoản của bạn",
    "Kết nối mạng Internet",
    # ubuntu_localizations
    "Tiếp theo",
    "Quay lại",
]
missing = [
    p for p in required
    if p.encode("utf-16le") not in blob and p.encode("utf-8") not in blob
]
if missing:
    raise SystemExit(f"ERROR: Vietnamese phrases missing from compiled libapp.so: {missing}")
print(f"Verified all {len(required)} Vietnamese phrases compiled into AOT libapp.so ({len(blob)} bytes).")
PY

# 8. Stage output bundle atomically
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp -a "${BUNDLE_SRC}/ubuntu_bootstrap" "${BUNDLE_SRC}/lib" "${BUNDLE_SRC}/data" "${OUT_DIR}/"
chmod 755 "${OUT_DIR}/ubuntu_bootstrap"

python3 - "${OUT_DIR}/manifest.json" "$TARGET_ARCH" "$INSTALLER_COMMIT" "$FLUTTER_VERSION" "$ARB_SHA" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(
    json.dumps(
        {
            "arch": sys.argv[2],
            "commit": sys.argv[3],
            "flutter": sys.argv[4],
            "arbSha256": sys.argv[5],
            "localeCoverage": {"ubuntu_bootstrap": 447, "ubuntu_provision": 116, "ubuntu_localizations": 121},
        },
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
PY

echo "==> Built 100% Vietnamese installer bundle -> ${OUT_DIR}"
du -sh "$OUT_DIR"
