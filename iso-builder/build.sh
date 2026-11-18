#!/usr/bin/env bash
# ============================================================
# Ubuntu-Nido ISO Builder
# Remaster Ubuntu 26.04 / 24.04 LTS GNOME Desktop -> Ubuntu-Nido
#
# Usage:
#   sudo ./build.sh                          # Dev build (zstd fast)
#   sudo ./build.sh --release                # Release build (xz, smaller)
#   sudo ./build.sh /path/to/ubuntu.iso      # Use existing local ISO
#   sudo ./build.sh --clean                  # Clean build work/cache/ISO
#   sudo ./build.sh --quick                  # Overlay + repack quickly
#   sudo ./build.sh --shell                  # Enter chroot for debugging
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/config.sh
source "$SCRIPT_DIR/scripts/config.sh"
# shellcheck source=scripts/utils.sh
source "$SCRIPT_DIR/scripts/utils.sh"
# shellcheck source=scripts/extract.sh
source "$SCRIPT_DIR/scripts/extract.sh"
# shellcheck source=scripts/boot_config.sh
source "$SCRIPT_DIR/scripts/boot_config.sh"
# shellcheck source=scripts/overlay.sh
source "$SCRIPT_DIR/scripts/overlay.sh"
# shellcheck source=scripts/customize.sh
source "$SCRIPT_DIR/scripts/customize.sh"
# shellcheck source=scripts/chroot_shell.sh
source "$SCRIPT_DIR/scripts/chroot_shell.sh"
# shellcheck source=scripts/repack.sh
source "$SCRIPT_DIR/scripts/repack.sh"

MODE="full"
ISO_ARG=""
CLEAN_OUTPUT=false
DEBUG_BOOT=0
BUILD_OK=0

for arg in "$@"; do
  case "$arg" in
    --release)
      FAST_ISO=0
      SQUASHFS_COMP="xz"
      SQUASHFS_OPTS=(-b 1M -Xdict-size 100% -noappend)
      export FAST_ISO SQUASHFS_COMP
      info "Release mode: xz compression (smaller ISO)"
      ;;
    --fast)
      FAST_ISO=1
      SQUASHFS_COMP="${SQUASHFS_COMP:-zstd}"
      if [ "$SQUASHFS_COMP" = "zstd" ]; then
        SQUASHFS_OPTS=(-Xcompression-level 1 -noappend)
      else
        SQUASHFS_OPTS=(-noappend)
      fi
      export FAST_ISO SQUASHFS_COMP
      info "Fast local mode: ${SQUASHFS_COMP} (${SQUASHFS_OPTS[*]})"
      ;;
    --debug-boot)
      DEBUG_BOOT=1
      export DEBUG_BOOT
      info "Debug boot enabled: verbose kernel boot logs"
      ;;
    --prepare|--boot-only|--overlay-only|--customize-only|--shell|--repack-only|--iso-only|--quick|--clean|--clean-work|--clean-cache|--help|-h)
      MODE="${arg#--}"
      ;;
    *)
      ISO_ARG="$arg"
      ;;
  esac
done

case "$MODE" in
  help|-h)
    print_dev_help
    exit 0
    ;;
  clean)
    info "Cleaning Ubuntu-Nido build artifacts..."
    safe_remove_work_dirs
    rm -rf "$WORK_DIR/cache" "$WORK_DIR/cache_iso" "$WORK_DIR/casper-layout.env" "$WORK_DIR/source-iso.path" Ubuntu-Nido-*.iso "$OUTPUT_DIR"/Ubuntu-Nido-*.iso ./*.log
    ok "Clean completed."
    exit 0
    ;;
  clean-work)
    info "Cleaning work tree (preserving extracted cache)..."
    safe_remove_work_dirs
    ok "Work tree removed."
    exit 0
    ;;
  clean-cache)
    info "Cleaning entire build cache and work tree..."
    safe_remove_work_dirs
    rm -rf "$WORK_DIR/cache" "$WORK_DIR/cache_iso" "$WORK_DIR/casper-layout.env" "$WORK_DIR/source-iso.path"
    ok "Cache removed."
    exit 0
    ;;
esac

check_root
install_deps
install_gum

cleanup_on_fail() {
  [ "${BUILD_OK:-0}" = "1" ] && return 0
  echo ""
  echo "Build failed! Unmounting chroot safely..." >&2
  umount_chroot
  umount "$WORK_DIR/mnt" 2>/dev/null || true

  if [ "$CLEAN_OUTPUT" = true ]; then
    safe_remove_work_dirs || true
  else
    warn "Work dirs kept for inspection."
  fi
}
trap cleanup_on_fail EXIT

resolve_iso "$ISO_ARG"

validate_customized_rootfs() {
  [ -f "$WORK_DIR/squashfs/etc/ubuntu-nido-customized" ] || return 1
  chroot "$WORK_DIR/squashfs" /bin/bash -c "test -f /etc/dconf/db/local && test -x /usr/bin/nidoai && test -x /usr/bin/nidoagent && test -x /usr/bin/nido-hub && test -x /usr/bin/nidooffice && dpkg -s nido-desktop >/dev/null 2>&1 && dpkg -s vielotus >/dev/null 2>&1 && test -f /usr/share/plymouth/themes/ubuntu-nido/ubuntu-nido.plymouth"
}

print_header

case "$MODE" in
  full)
    CLEAN_OUTPUT=true
    step_extract
    step_boot_config
    step_customize
    step_repack_and_clean
    ;;
  prepare)
    step_extract
    ;;
  boot-only)
    ensure_work_tree
    step_boot_config
    ;;
  overlay-only)
    ensure_work_tree
    step_overlay
    ;;
  customize-only)
    ensure_work_tree
    step_customize
    ;;
  shell)
    step_chroot_shell
    ;;
  repack-only)
    ensure_work_tree
    step_repack
    ;;
  iso-only)
    ensure_work_tree
    step_repack_iso
    ;;
  quick)
    ensure_work_tree
    step_boot_config
    if ! validate_customized_rootfs; then
      warn "Work tree not fully customized. Running customize before repack..."
      rm -f "$WORK_DIR/squashfs/etc/ubuntu-nido-customized" 2>/dev/null || true
      step_customize
    else
      step_overlay
    fi
    step_repack
    ;;
  *)
    error "Unsupported mode: $MODE. Run sudo ./build.sh --help"
    ;;
esac

BUILD_OK=1

case "$MODE" in
  shell|prepare|boot-only|overlay-only|customize-only)
    ok "Completed mode: $MODE"
    ;;
  *)
    print_result
    ;;
esac
