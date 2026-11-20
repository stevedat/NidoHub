#!/usr/bin/env bash
# Ubuntu-Nido ISO builder configuration

NIDO_VERSION="${NIDO_VERSION:-0.2.0}"
UBUNTU_VERSION="${UBUNTU_VERSION:-26.04}"
TARGET_ARCH="${TARGET_ARCH:-amd64}"

case "$UBUNTU_VERSION" in
  26.04*)
    UBUNTU_CODENAME="resolute"
    ;;
  24.04*)
    UBUNTU_CODENAME="noble"
    ;;
  *)
    UBUNTU_CODENAME="${UBUNTU_CODENAME:-resolute}"
    ;;
esac

case "$TARGET_ARCH" in
  amd64|arm64)
    ;;
  *)
    echo "Unsupported TARGET_ARCH=${TARGET_ARCH}. Expected amd64 or arm64." >&2
    exit 1
    ;;
esac

UBUNTU_ISO_NAME="ubuntu-${UBUNTU_VERSION}-desktop-${TARGET_ARCH}.iso"
USE_VN_MIRROR="${USE_VN_MIRROR:-1}"

if [[ "$TARGET_ARCH" == "arm64" ]]; then
  UBUNTU_ISO_URL="https://cdimage.ubuntu.com/releases/${UBUNTU_VERSION}/release/${UBUNTU_ISO_NAME}"
elif [[ "${GITHUB_ACTIONS:-}" == "true" || "$USE_VN_MIRROR" != "1" ]]; then
  UBUNTU_ISO_URL="https://releases.ubuntu.com/${UBUNTU_VERSION}/${UBUNTU_ISO_NAME}"
else
  UBUNTU_ISO_URL="https://mirror.bizflycloud.vn/ubuntu-releases/${UBUNTU_VERSION}/${UBUNTU_ISO_NAME}"
fi

OUTPUT_ISO="NidoOS-desktop-${TARGET_ARCH}.iso"
OUTPUT_DIR="${OUTPUT_DIR:-$SCRIPT_DIR}"
BASE_ISO_CACHE_DIR="${BASE_ISO_CACHE_DIR:-$SCRIPT_DIR}"
WORK_DIR="${WORK_DIR:-$SCRIPT_DIR/build}"
FAST_ISO="${FAST_ISO:-0}"

# Dev builds favor fast compression; FAST_ISO=1 uses zstd -1; --release switches to xz
SQUASHFS_COMP="${SQUASHFS_COMP:-zstd}"
if [ "$FAST_ISO" = "1" ]; then
  if [ "$SQUASHFS_COMP" = "zstd" ]; then
    # shellcheck disable=SC2034
    SQUASHFS_OPTS=(-Xcompression-level 1 -noappend)
  else
    # shellcheck disable=SC2034
    SQUASHFS_OPTS=(-noappend)
  fi
else
  if [ "$SQUASHFS_COMP" = "zstd" ]; then
    # shellcheck disable=SC2034
    SQUASHFS_OPTS=(-Xcompression-level 6 -noappend)
  else
    # shellcheck disable=SC2034
    SQUASHFS_OPTS=(-noappend)
  fi
fi

export NIDO_VERSION UBUNTU_VERSION UBUNTU_CODENAME TARGET_ARCH UBUNTU_ISO_NAME UBUNTU_ISO_URL OUTPUT_ISO OUTPUT_DIR BASE_ISO_CACHE_DIR WORK_DIR USE_VN_MIRROR FAST_ISO SQUASHFS_COMP
