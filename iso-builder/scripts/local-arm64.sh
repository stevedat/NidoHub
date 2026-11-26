#!/usr/bin/env bash
# Fast local ARM64 ISO builder with external ext4 image + cached base ISO support
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ISO_BUILDER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$ISO_BUILDER_DIR/.." && pwd)"

EXT_DIR="${EXT_DIR:-$ISO_BUILDER_DIR/local-arm64}"
EXT4_IMG_SIZE="${EXT4_IMG_SIZE:-18G}"
UBUNTU_VERSION="${UBUNTU_VERSION:-26.04}"
NIDO_VERSION="${NIDO_VERSION:-0.1.0}"
FAST_ISO="${FAST_ISO:-1}"
SQUASHFS_COMP="${SQUASHFS_COMP:-zstd}"
BASE_ISO_PATH="${BASE_ISO_PATH:-}"
ENGINE="${ENGINE:-auto}"
BUILDER_IMAGE="${BUILDER_IMAGE:-ubuntu-nido-builder:arm64}"
REBUILD_IMAGE=0
DRY_RUN=0
PASS_ARGS=()

usage() {
  cat <<'EOF'
Usage: iso-builder/scripts/local-arm64.sh [options] [-- extra build.sh args]

Fast local ARM64 Ubuntu-Nido ISO builder. Caches the base Ubuntu arm64 ISO once
and compiles on a real Linux ext4 workspace (supporting POSIX permissions and
overlayfs even when the host/external SSD is exFAT or macOS APFS).

Options:
  --ext-dir DIR        External workspace directory for cache/, out/, and ext4 .img
                       (default: iso-builder/local-arm64 or $EXT_DIR)
  --img-size SIZE      ext4 workspace image size (default: 18G or $EXT4_IMG_SIZE)
  --iso PATH           Path to pre-downloaded ubuntu-*-desktop-arm64.iso
  --fast               Enable FAST_ISO=1 (zstd level 1; default)
  --release            Use release xz compression instead of FAST_ISO=1
  --comp COMP          Override SQUASHFS_COMP (zstd or lz4)
  --engine MODE        Runtime engine: auto, host, docker, podman (default: auto)
  --rebuild-image      Rebuild the local ARM64 builder container image
  --dry-run            Validate paths, engine, and free disk space without building
  -h, --help           Show this help message
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ext-dir)
      EXT_DIR="${2:?--ext-dir requires a path}"
      shift 2
      ;;
    --img-size)
      EXT4_IMG_SIZE="${2:?--img-size requires a size like 18G}"
      shift 2
      ;;
    --iso)
      BASE_ISO_PATH="${2:?--iso requires a file path}"
      shift 2
      ;;
    --fast)
      FAST_ISO=1
      shift
      ;;
    --release)
      FAST_ISO=0
      PASS_ARGS+=(--release)
      shift
      ;;
    --comp)
      SQUASHFS_COMP="${2:?--comp requires zstd or lz4}"
      shift 2
      ;;
    --engine)
      ENGINE="${2:?--engine requires auto, host, docker, or podman}"
      shift 2
      ;;
    --rebuild-image)
      REBUILD_IMAGE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      PASS_ARGS+=("$@")
      break
      ;;
    *)
      PASS_ARGS+=("$1")
      shift
      ;;
  esac
done

probe_cmd() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 3 "$@" >/dev/null 2>&1
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout 3 "$@" >/dev/null 2>&1
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import subprocess, sys; sys.exit(subprocess.run(sys.argv[1:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3).returncode)' "$@" 2>/dev/null
  else
    "$@" >/dev/null 2>&1
  fi
}

detect_engine() {
  if [ "$ENGINE" != "auto" ]; then
    echo "$ENGINE"
    return 0
  fi
  if [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" -eq 0 ]; then
    echo "host"
  elif command -v podman >/dev/null 2>&1 && probe_cmd podman info; then
    echo "podman"
  elif command -v docker >/dev/null 2>&1 && probe_cmd docker info; then
    echo "docker"
  elif command -v podman >/dev/null 2>&1; then
    echo "podman"
  elif command -v docker >/dev/null 2>&1; then
    echo "docker"
  else
    echo "host"
  fi
}

parse_size_gib() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
  case "$raw" in
    *G) echo "${raw%G}" ;;
    *M) echo $(( (${raw%M} + 1023) / 1024 )) ;;
    *)  echo 18 ;;
  esac
}

mkdir -p "$EXT_DIR" "$EXT_DIR/cache" "$EXT_DIR/out"
EXT_DIR="$(cd "$EXT_DIR" && pwd)"
EXT4_IMG="$EXT_DIR/nido-arm64-work.img"
CACHE_ISO="$EXT_DIR/cache/ubuntu-${UBUNTU_VERSION}-desktop-arm64.iso"

if [ -n "$BASE_ISO_PATH" ]; then
  if [ ! -f "$BASE_ISO_PATH" ]; then
    echo "ERROR: Provided base ISO not found: $BASE_ISO_PATH" >&2
    exit 1
  fi
  BASE_ISO_PATH="$(cd "$(dirname "$BASE_ISO_PATH")" && pwd)/$(basename "$BASE_ISO_PATH")"
fi

RESOLVED_ENGINE="$(detect_engine)"
EXT_AVAIL_KB="$(df -Pk "$EXT_DIR" | awk 'NR==2 {print $4}')"
EXT_AVAIL_GIB=$(( EXT_AVAIL_KB / 1024 / 1024 ))
HOST_ROOT_AVAIL_KB="$(df -Pk / | awk 'NR==2 {print $4}')"
HOST_ROOT_FREE_GIB=$(( HOST_ROOT_AVAIL_KB / 1024 / 1024 ))
IMG_GIB="$(parse_size_gib "$EXT4_IMG_SIZE")"

# Minimum free space in EXT_DIR: ~5 GiB for output ISO + ~4 GiB for cached base ISO (if absent)
REQUIRED_EXT_GIB=5
if [ ! -f "$CACHE_ISO" ] && [ -z "$BASE_ISO_PATH" ]; then
  REQUIRED_EXT_GIB=$((REQUIRED_EXT_GIB + 4))
fi

echo "[Ubuntu-Nido] Local ARM64 Fast ISO Builder Configuration:"
echo "  Engine          : $RESOLVED_ENGINE (host=$(uname -s)/$(uname -m), host_root_free=${HOST_ROOT_FREE_GIB} GiB)"
echo "  External Dir    : $EXT_DIR (${EXT_AVAIL_GIB} GiB free)"
echo "  ext4 Image      : $EXT4_IMG (size=${EXT4_IMG_SIZE})"
echo "  Base ISO Cache  : ${BASE_ISO_PATH:-$CACHE_ISO}"
echo "  Output Dir      : $EXT_DIR/out"
echo "  Ubuntu / Nido   : ${UBUNTU_VERSION} / ${NIDO_VERSION}"
echo "  FAST_ISO / Comp : ${FAST_ISO} / ${SQUASHFS_COMP}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[Ubuntu-Nido] Dry run verified cleanly."
  exit 0
fi

if [ -f "$REPO_ROOT/branding/generate-assets.py" ]; then
  python3 "$REPO_ROOT/branding/generate-assets.py"
fi
if command -v dpkg-deb >/dev/null 2>&1 && [ -x "$REPO_ROOT/packages/build-all.sh" ]; then
  bash "$REPO_ROOT/packages/build-all.sh"
fi

run_host_build() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: --engine host requires root privileges (use sudo)." >&2
    exit 1
  fi
  local need_gib="$REQUIRED_EXT_GIB"
  if [ ! -f "$EXT4_IMG" ]; then
    need_gib=$((need_gib + IMG_GIB))
  fi
  if [ "$EXT_AVAIL_GIB" -lt "$need_gib" ]; then
    echo "ERROR: Not enough free space in $EXT_DIR: need ~${need_gib} GiB, available ${EXT_AVAIL_GIB} GiB." >&2
    exit 1
  fi

  local mnt_work
  mnt_work="$(mktemp -d /tmp/nido-arm64-ext4.XXXXXX)"
  cleanup_host_ext4() {
    sync 2>/dev/null || true
    umount "$mnt_work" 2>/dev/null || umount -lf "$mnt_work" 2>/dev/null || true
    rmdir "$mnt_work" 2>/dev/null || true
  }
  trap cleanup_host_ext4 EXIT

  if [ ! -f "$EXT4_IMG" ]; then
    echo "[Ubuntu-Nido] Creating ext4 workspace image ($EXT4_IMG_SIZE) at $EXT4_IMG..."
    truncate -s "$EXT4_IMG_SIZE" "$EXT4_IMG"
    mkfs.ext4 -F -q "$EXT4_IMG"
  fi
  mount -t ext4 -o loop "$EXT4_IMG" "$mnt_work"

  TARGET_ARCH=arm64 \
  UBUNTU_VERSION="$UBUNTU_VERSION" \
  NIDO_VERSION="$NIDO_VERSION" \
  FAST_ISO="$FAST_ISO" \
  SQUASHFS_COMP="$SQUASHFS_COMP" \
  KEEP_BASE_ISO=1 \
  BASE_ISO_CACHE_DIR="$EXT_DIR/cache" \
  OUTPUT_DIR="$EXT_DIR/out" \
  WORK_DIR="$mnt_work/build" \
  bash "$ISO_BUILDER_DIR/build.sh" ${BASE_ISO_PATH:+"$BASE_ISO_PATH"} ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
}

run_container_build() {
  local ctr="$1"
  if ! command -v "$ctr" >/dev/null 2>&1; then
    echo "ERROR: Container CLI '$ctr' not found." >&2
    exit 1
  fi
  if ! "$ctr" info >/dev/null 2>&1; then
    echo "ERROR: Container daemon '$ctr' is not running. Start Docker/Podman VM first." >&2
    exit 1
  fi

  if [ "$REBUILD_IMAGE" -eq 1 ] || ! "$ctr" image inspect "$BUILDER_IMAGE" >/dev/null 2>&1; then
    echo "[Ubuntu-Nido] Building ARM64 container image '$BUILDER_IMAGE'..."
    "$ctr" build --platform linux/arm64 -t "$BUILDER_IMAGE" "$ISO_BUILDER_DIR"
  fi

  local extra_iso_mounts=()
  local container_iso_arg=""
  if [ -n "$BASE_ISO_PATH" ]; then
    extra_iso_mounts+=(-v "$(dirname "$BASE_ISO_PATH"):/mnt/input-iso:ro")
    container_iso_arg="/mnt/input-iso/$(basename "$BASE_ISO_PATH")"
  fi

  "$ctr" volume create nido_arm64_work >/dev/null 2>&1 || true

  "$ctr" run --rm --privileged \
    --platform linux/arm64 \
    -v "$REPO_ROOT:/workspace" \
    -v "$EXT_DIR:/mnt/ext" \
    -v /dev:/dev \
    -v nido_arm64_work:/mnt/vol \
    ${extra_iso_mounts[@]+"${extra_iso_mounts[@]}"} \
    -e TARGET_ARCH=arm64 \
    -e UBUNTU_VERSION="$UBUNTU_VERSION" \
    -e NIDO_VERSION="$NIDO_VERSION" \
    -e FAST_ISO="$FAST_ISO" \
    -e SQUASHFS_COMP="$SQUASHFS_COMP" \
    -e KEEP_BASE_ISO=1 \
    -e EXT4_IMG_SIZE="$EXT4_IMG_SIZE" \
    -e IMG_GIB="$IMG_GIB" \
    -e REQUIRED_EXT_GIB="$REQUIRED_EXT_GIB" \
    -e EXT_AVAIL_GIB="$EXT_AVAIL_GIB" \
    -e HOST_OS="$(uname -s)" \
    -e HOST_ROOT_FREE_GIB="$HOST_ROOT_FREE_GIB" \
    -e CONTAINER_ISO_ARG="$container_iso_arg" \
    "$BUILDER_IMAGE" \
    bash -s -- ${PASS_ARGS[@]+"${PASS_ARGS[@]}"} <<'CONTAINER_EOF'
set -euo pipefail

EXT4_IMG="/mnt/ext/nido-arm64-work.img"
WORK_MOUNT=""

cleanup_container_mounts() {
  sync 2>/dev/null || true
  if [ -n "$WORK_MOUNT" ] && mountpoint -q "$WORK_MOUNT" 2>/dev/null; then
    umount "$WORK_MOUNT" 2>/dev/null || umount -lf "$WORK_MOUNT" 2>/dev/null || true
  fi
}
trap cleanup_container_mounts EXIT

if losetup -f >/dev/null 2>&1; then
  NEED_GIB="$REQUIRED_EXT_GIB"
  if [ ! -f "$EXT4_IMG" ]; then
    NEED_GIB=$((NEED_GIB + IMG_GIB))
  fi
  if [ "$EXT_AVAIL_GIB" -lt "$NEED_GIB" ]; then
    echo "ERROR: Not enough free space in /mnt/ext ($EXT_AVAIL_GIB GiB free; need ~${NEED_GIB} GiB for ext4 image + base/output ISO)." >&2
    exit 1
  fi
  if [ ! -f "$EXT4_IMG" ]; then
    echo "[Ubuntu-Nido] Creating ext4 workspace image ($EXT4_IMG_SIZE) on external storage..."
    truncate -s "$EXT4_IMG_SIZE" "$EXT4_IMG"
    mkfs.ext4 -F -q "$EXT4_IMG"
  fi
  WORK_MOUNT="/mnt/ext4-work"
  mkdir -p "$WORK_MOUNT"
  mount -t ext4 -o loop "$EXT4_IMG" "$WORK_MOUNT"
  export WORK_DIR="$WORK_MOUNT/build"
else
  # Lightweight macOS runtimes without /dev/loop* can use the VM's native ext4 Docker volume
  VOL_FREE_KB="$(df -Pk /mnt/vol | awk 'NR==2 {print $4}')"
  VOL_FREE_GIB=$(( VOL_FREE_KB / 1024 / 1024 ))
  if [ "$EXT_AVAIL_GIB" -lt "$REQUIRED_EXT_GIB" ]; then
    echo "ERROR: Not enough free space in external dir ($EXT_AVAIL_GIB GiB free; need ~${REQUIRED_EXT_GIB} GiB for base/output ISO)." >&2
    exit 1
  fi
  if [ "$VOL_FREE_GIB" -lt 15 ] || { [ "$HOST_OS" = "Darwin" ] && [ "$HOST_ROOT_FREE_GIB" -lt 15 ]; }; then
    echo "ERROR: Runtime lacks /dev/loop* for ext4 .img and host/VM storage is low (host '/' free: ${HOST_ROOT_FREE_GIB} GiB, volume free: ${VOL_FREE_GIB} GiB; needs >= 15 GiB)." >&2
    echo "Free at least 16 GiB on '/' OR run with Podman/Linux VM supporting loop + ext4 .img on external storage." >&2
    exit 1
  fi
  echo "[Ubuntu-Nido] Loop devices absent in runtime; using container ext4 volume (/mnt/vol, ${VOL_FREE_GIB} GiB free) + external cache/out."
  export WORK_DIR="/mnt/vol/build"
fi

export BASE_ISO_CACHE_DIR="/mnt/ext/cache"
export OUTPUT_DIR="/mnt/ext/out"

cd /workspace/iso-builder
bash ./build.sh ${CONTAINER_ISO_ARG:+"$CONTAINER_ISO_ARG"} "$@"
CONTAINER_EOF
}

case "$RESOLVED_ENGINE" in
  host)
    run_host_build
    ;;
  docker|podman)
    run_container_build "$RESOLVED_ENGINE"
    ;;
  *)
    echo "ERROR: Unsupported engine '$RESOLVED_ENGINE'." >&2
    exit 1
    ;;
esac
