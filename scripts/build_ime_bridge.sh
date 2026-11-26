#!/usr/bin/env bash
# Build multi-arch NidoOS zero-underline Sen Viet IBus bridge (`nido-ime-bridge`) .deb packages.
# Compiles on Ubuntu 24.04 (`noble`, glibc 2.39) so the resulting ELF binaries run cleanly on
# both Ubuntu 24.04 LTS (`noble`) and Ubuntu 26.04 LTS (`resolute`), verifies zero preedit
# symbol references, executes `--self-test` against upstream libVieLotusCAPI.so.0, and assembles
# GPG-signed .deb packages with embedded SHA-256 integrity manifests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PKG_SRC="${REPO_ROOT}/packages/nido-ime-bridge"
OUT_DIR="${REPO_ROOT}/packages/vendor"
TARGET_ARCH="all"
APT_GPG_KEY="${APT_GPG_KEY:-AC980631CB8BA9D2602A0990528AF2083F61DC2F}"
DEB_VERSION_SUFFIX="${DEB_VERSION_SUFFIX:-}"
DRY_RUN=0
NATIVE_HOST=0
BASE_IMAGE="${NIDO_IME_BUILD_IMAGE:-ubuntu:24.04}"
PKG_VERSION="0.2.2"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      TARGET_ARCH="${2:-all}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:-${OUT_DIR}}"
      shift 2
      ;;
    --gpg-key)
      APT_GPG_KEY="${2:-}"
      shift 2
      ;;
    --version-suffix)
      DEB_VERSION_SUFFIX="${2:-}"
      shift 2
      ;;
    --native)
      NATIVE_HOST=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: build_ime_bridge.sh [--arch amd64|arm64|all] [--out-dir DIR] [--gpg-key FPR] [--version-suffix +dev] [--native] [--dry-run]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$TARGET_ARCH" in
  amd64|arm64) ARCHES=("$TARGET_ARCH") ;;
  all) ARCHES=("amd64" "arm64") ;;
  *)
    echo "Unsupported --arch: $TARGET_ARCH (expected amd64|arm64|all)" >&2
    exit 2
    ;;
esac

if [[ ! -f "${PKG_SRC}/src/nido-ibus-vielotus.c" ]]; then
  echo "ERROR: Missing source file ${PKG_SRC}/src/nido-ibus-vielotus.c" >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  for a in "${ARCHES[@]}"; do
    echo "Dry-run OK: nido-ime-bridge_${PKG_VERSION}${DEB_VERSION_SUFFIX}_${a}.deb -> ${OUT_DIR} (base=${BASE_IMAGE}, native=${NATIVE_HOST})"
  done
  exit 0
fi

mkdir -p "$OUT_DIR"

assemble_deb() {
  local arch="$1"
  local bin_path="$2"
  local work_tmp
  work_tmp="$(mktemp -d)"

  cp -R "${PKG_SRC}/DEBIAN" "${work_tmp}/DEBIAN"
  cp -R "${PKG_SRC}/files" "${work_tmp}/files"
  mkdir -p "${work_tmp}/files/usr/lib/ibus"
  cp "$bin_path" "${work_tmp}/files/usr/lib/ibus/nido-ibus-vielotus"
  chmod 755 "${work_tmp}/files/usr/lib/ibus/nido-ibus-vielotus" \
    "${work_tmp}/files/usr/bin/nido-vielotus-settings" \
    "${work_tmp}/DEBIAN/postinst" "${work_tmp}/DEBIAN/postrm"

  python3 - "${work_tmp}/DEBIAN/control" "$arch" <<'PY'
import sys
from pathlib import Path
p, arch = Path(sys.argv[1]), sys.argv[2]
txt = p.read_text(encoding="utf-8")
txt = txt.replace("Architecture: @ARCH@", f"Architecture: {arch}")
p.write_text(txt, encoding="utf-8")
PY

  local manifest_args=(--integrity-manifest)
  if [[ -n "$APT_GPG_KEY" ]]; then
    manifest_args+=(--gpg-key "$APT_GPG_KEY")
  fi
  local ver_args=()
  if [[ -n "$DEB_VERSION_SUFFIX" ]]; then
    ver_args=(--version-suffix "$DEB_VERSION_SUFFIX")
  fi

  local out_deb="${OUT_DIR}/nido-ime-bridge_${PKG_VERSION}${DEB_VERSION_SUFFIX}_${arch}.deb"
  rm -f "${OUT_DIR}"/nido-ime-bridge_*_"${arch}".deb
  python3 "${REPO_ROOT}/scripts/build_deb_portable.py" \
    --source-dir "$work_tmp" \
    --compression xz \
    "${manifest_args[@]}" \
    ${ver_args[@]+"${ver_args[@]}"} \
    --output "$out_deb"

  rm -rf "$work_tmp"
  echo "==> Assembled ${out_deb}"
}

build_one_arch() {
  local arch="$1"
  local stage_root
  stage_root="$(mktemp -d "${TMPDIR:-/tmp}/nido-ime-build-${arch}.XXXXXX")"
  local out_bin="${stage_root}/nido-ibus-vielotus"

  local vl_deb=""
  shopt -s nullglob
  local vl_cands=("${REPO_ROOT}/packages/vendor"/vielotus_*_"${arch}".deb)
  shopt -u nullglob
  if [[ "${#vl_cands[@]}" -gt 0 ]]; then
    vl_deb="${vl_cands[0]}"
  fi

  if [[ "$NATIVE_HOST" -eq 1 ]]; then
    echo "==> Building nido-ibus-vielotus natively (${arch})..."
    make -C "${PKG_SRC}/src" clean >/dev/null 2>&1 || true
    make -C "${PKG_SRC}/src"
    cp "${PKG_SRC}/src/nido-ibus-vielotus" "$out_bin"
    strip --strip-unneeded "$out_bin" 2>/dev/null || true
    make -C "${PKG_SRC}/src" clean >/dev/null 2>&1 || true

    if nm -D -u "$out_bin" | grep -iq "preedit"; then
      echo "ERROR: Preedit symbol detected in ${out_bin}!" >&2
      rm -rf "$stage_root"
      exit 1
    fi
    if [[ -f /usr/lib/libVieLotusCAPI.so.0 ]]; then
      "$out_bin" --self-test --capi-path /usr/lib/libVieLotusCAPI.so.0
    fi
  else
    command -v docker >/dev/null 2>&1 || {
      echo "ERROR: Docker is required for cross-arch/container build (or pass --native on Linux)." >&2
      rm -rf "$stage_root"
      exit 1
    }
    local cache_dir="/tmp/nidoos-ime-cache/${arch}"
    mkdir -p "${cache_dir}/apt"
    local host_uid host_gid
    host_uid="$(id -u)"
    host_gid="$(id -g)"

    local vl_mount_args=()
    local vl_in_container=""
    if [[ -n "$vl_deb" && -f "$vl_deb" ]]; then
      cp "$vl_deb" "${stage_root}/vielotus.deb"
      vl_in_container="/work/vielotus.deb"
    fi
    vl_mount_args=(-v "${stage_root}:/work")

    echo "==> Compiling & testing nido-ibus-vielotus (${arch}) in ${BASE_IMAGE}..."
    docker run --rm --platform "linux/${arch}" \
      -v "${PKG_SRC}/src:/src:ro" \
      "${vl_mount_args[@]}" \
      -v "${cache_dir}:/cache" \
      "$BASE_IMAGE" \
      bash -euo pipefail -c "
        export DEBIAN_FRONTEND=noninteractive
        rm -f /etc/apt/apt.conf.d/docker-clean
        mkdir -p /cache/apt/partial
        apt-get update -qq
        apt-get -o APT::Sandbox::User=root -o dir::cache::archives=/cache/apt install -y --no-install-recommends \
          gcc make pkg-config binutils file libibus-1.0-dev libglib2.0-dev dpkg-dev >/dev/null
        cp -R /src /tmp/build-src
        make -C /tmp/build-src clean
        make -C /tmp/build-src
        cp /tmp/build-src/nido-ibus-vielotus /work/nido-ibus-vielotus
        strip --strip-unneeded /work/nido-ibus-vielotus
        file /work/nido-ibus-vielotus
        if nm -D -u /work/nido-ibus-vielotus | grep -iq 'preedit'; then
          echo 'ERROR: Forbidden preedit symbol found in compiled ELF!' >&2
          exit 1
        fi
        echo 'Verified: 0 preedit undefined dynamic symbols in nido-ibus-vielotus ELF.'
        if [ -n '${vl_in_container}' ] && [ -f '${vl_in_container}' ]; then
          dpkg-deb -x '${vl_in_container}' /tmp/vl-root
          /work/nido-ibus-vielotus --self-test --capi-path /tmp/vl-root/usr/lib/libVieLotusCAPI.so.0
        else
          /work/nido-ibus-vielotus --version
        fi
        chown '${host_uid}:${host_gid}' /work/nido-ibus-vielotus
      "
  fi

  assemble_deb "$arch" "$out_bin"
  rm -rf "$stage_root"
}

for a in "${ARCHES[@]}"; do
  build_one_arch "$a"
done
