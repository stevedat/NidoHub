#!/usr/bin/env bash
# Shared logging, chroot, and dependency helpers for Ubuntu-Nido ISO builder

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

HAS_GUM=false
command -v gum &>/dev/null && HAS_GUM=true

APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
APT_GET=(apt-get -o "DPkg::Lock::Timeout=${APT_LOCK_TIMEOUT}")

preseed_noninteractive_packages() {
  export DEBIAN_FRONTEND=noninteractive
  echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections 2>/dev/null || true
  echo "ttf-mscorefonts-installer msttcorefonts/present-mscorefonts-eula note" | debconf-set-selections 2>/dev/null || true
}

info() {
  if $HAS_GUM; then
    gum log --level info "$1"
  else
    echo -e "${CYAN}[Ubuntu-Nido]${NC} $1"
  fi
}

ok() {
  if $HAS_GUM; then
    gum log --level info "✅ $1"
  else
    echo -e "${GREEN}[  OK  ]${NC} $1"
  fi
}

warn() {
  if $HAS_GUM; then
    gum log --level warn "$1"
  else
    echo -e "${YELLOW}[ WARN ]${NC} $1"
  fi
}

error() {
  if $HAS_GUM; then
    gum log --level error "$1"
  else
    echo -e "${RED}[ERROR ]${NC} $1" >&2
  fi
  exit 1
}

print_header() {
  echo ""
  echo "========================================================"
  echo -e "  ${CYAN}Ubuntu-Nido ${NIDO_VERSION}${NC} (Ubuntu ${UBUNTU_VERSION} ${UBUNTU_CODENAME} / ${TARGET_ARCH})"
  echo "========================================================"
  echo "  Input ISO : $BASE_ISO"
  echo "  Work Dir  : $WORK_DIR"
  echo "  Output ISO: $OUTPUT_DIR/$OUTPUT_ISO"
  echo "  SquashFS  : $SQUASHFS_COMP (${SQUASHFS_OPTS[*]})"
  echo ""
}

print_result() {
  local iso_path="$OUTPUT_DIR/$OUTPUT_ISO"
  local size
  size="$(du -h "$iso_path" | cut -f1)"
  echo ""
  echo "========================================================"
  echo -e "  ${GREEN}Build succeeded!${NC}"
  echo "  ISO : $iso_path"
  echo "  Size: $size"
  echo "  SHA256:"
  sha256sum "$iso_path" 2>/dev/null || shasum -a 256 "$iso_path"
  echo "========================================================"
}

check_root() {
  if [ "$(id -u)" -ne 0 ]; then
    error "Please run as root: sudo ./build.sh"
  fi
}

install_deps() {
  local deps=(squashfs-tools xorriso rsync wget curl dpkg-dev python3 p7zip-full)
  if [ "$TARGET_ARCH" = "amd64" ]; then
    deps+=(isolinux syslinux-utils grub-pc-bin)
  fi
  local missing=""
  for cmd in unsquashfs mksquashfs xorriso rsync wget curl dpkg-deb python3 7z; do
    command -v "$cmd" &>/dev/null || missing="yes"
  done
  if [ -n "$missing" ]; then
    preseed_noninteractive_packages
    info "Installing host build dependencies..."
    "${APT_GET[@]}" update -qq
    "${APT_GET[@]}" install -y "${deps[@]}"
    ok "Dependencies installed."
  fi
}

install_gum() {
  if [ -n "${CI:-}" ] || [ ! -t 1 ]; then
    return 0
  fi
  command -v gum &>/dev/null && HAS_GUM=true
}

resolve_iso() {
  local provided="${1:-}"
  if [ -n "$provided" ] && [ -f "$provided" ]; then
    BASE_ISO="$(cd "$(dirname "$provided")" && pwd)/$(basename "$provided")"
    info "Using local ISO: $BASE_ISO"
  elif [ -f "$BASE_ISO_CACHE_DIR/$UBUNTU_ISO_NAME" ]; then
    BASE_ISO="$BASE_ISO_CACHE_DIR/$UBUNTU_ISO_NAME"
    info "Found cached ISO: $BASE_ISO"
  elif [ -f "$SCRIPT_DIR/$UBUNTU_ISO_NAME" ]; then
    BASE_ISO="$SCRIPT_DIR/$UBUNTU_ISO_NAME"
    info "Found cached ISO: $BASE_ISO"
  else
    mkdir -p "$BASE_ISO_CACHE_DIR"
    info "Downloading ${UBUNTU_ISO_NAME} from ${UBUNTU_ISO_URL}..."
    if command -v aria2c >/dev/null 2>&1; then
      aria2c -x 4 -s 4 -c -d "$BASE_ISO_CACHE_DIR" -o "$UBUNTU_ISO_NAME" "$UBUNTU_ISO_URL"
    else
      wget --progress=bar:force -c "$UBUNTU_ISO_URL" -O "$BASE_ISO_CACHE_DIR/$UBUNTU_ISO_NAME"
    fi
    BASE_ISO="$BASE_ISO_CACHE_DIR/$UBUNTU_ISO_NAME"
    ok "Downloaded: $BASE_ISO"
  fi
  mkdir -p "$WORK_DIR"
  printf '%s\n' "$BASE_ISO" > "$WORK_DIR/source-iso.path"
  export BASE_ISO
}

ensure_work_tree() {
  if [ ! -d "$WORK_DIR/squashfs" ] || [ ! -d "$WORK_DIR/custom" ]; then
    warn "Work tree missing. Running extract step first..."
    step_extract
  fi
}

mount_chroot() {
  ensure_work_tree
  mountpoint -q "$WORK_DIR/squashfs/dev" || mount --bind /dev "$WORK_DIR/squashfs/dev"
  mountpoint -q "$WORK_DIR/squashfs/dev/pts" || mount --bind /dev/pts "$WORK_DIR/squashfs/dev/pts"
  mountpoint -q "$WORK_DIR/squashfs/proc" || mount -t proc proc "$WORK_DIR/squashfs/proc"
  mountpoint -q "$WORK_DIR/squashfs/sys" || mount -t sysfs sysfs "$WORK_DIR/squashfs/sys"

  # Replace dangling systemd-resolved symlink inside chroot so apt works
  rm -f "$WORK_DIR/squashfs/etc/resolv.conf"
  cp /etc/resolv.conf "$WORK_DIR/squashfs/etc/resolv.conf"
}

umount_layers() {
  if [ -d "$WORK_DIR/live_merged" ]; then
    local sub
    for sub in proc sys dev/pts dev; do
      if [ -d "$WORK_DIR/live_merged/$sub" ] && mountpoint -q "$WORK_DIR/live_merged/$sub" 2>/dev/null; then
        umount "$WORK_DIR/live_merged/$sub" 2>/dev/null || umount -lf "$WORK_DIR/live_merged/$sub" 2>/dev/null || true
      fi
    done
    if mountpoint -q "$WORK_DIR/live_merged" 2>/dev/null; then
      umount "$WORK_DIR/live_merged" 2>/dev/null || umount -lf "$WORK_DIR/live_merged" 2>/dev/null || true
    fi
  fi
  if [ -d "$WORK_DIR/layers" ]; then
    if mountpoint -q "$WORK_DIR/layers/merged" 2>/dev/null; then
      umount "$WORK_DIR/layers/merged" 2>/dev/null || umount -lf "$WORK_DIR/layers/merged" 2>/dev/null || true
    fi
    local mp
    for mp in "$WORK_DIR/layers"/layer_*; do
      if [ -d "$mp" ] && mountpoint -q "$mp" 2>/dev/null; then
        umount "$mp" 2>/dev/null || umount -lf "$mp" 2>/dev/null || true
      fi
    done
  fi
}

umount_chroot() {
  sync 2>/dev/null || true
  if [ -d "$WORK_DIR/squashfs/cdrom" ] && mountpoint -q "$WORK_DIR/squashfs/cdrom" 2>/dev/null; then
    umount "$WORK_DIR/squashfs/cdrom" 2>/dev/null || umount -lf "$WORK_DIR/squashfs/cdrom" 2>/dev/null || true
  fi
  umount "$WORK_DIR/squashfs/proc"    2>/dev/null || umount -lf "$WORK_DIR/squashfs/proc"    2>/dev/null || true
  umount "$WORK_DIR/squashfs/sys"     2>/dev/null || umount -lf "$WORK_DIR/squashfs/sys"     2>/dev/null || true
  umount "$WORK_DIR/squashfs/dev/pts" 2>/dev/null || umount -lf "$WORK_DIR/squashfs/dev/pts" 2>/dev/null || true
  umount "$WORK_DIR/squashfs/dev"     2>/dev/null || umount -lf "$WORK_DIR/squashfs/dev"     2>/dev/null || true
  umount_layers
}

safe_remove_work_dirs() {
  sync 2>/dev/null || true
  umount_chroot
  umount "$WORK_DIR/mnt" 2>/dev/null || true

  if grep -q " $WORK_DIR/squashfs/" /proc/mounts 2>/dev/null \
    || grep -q " $WORK_DIR/layers/" /proc/mounts 2>/dev/null \
    || grep -q " $WORK_DIR/live_merged" /proc/mounts 2>/dev/null; then
    error "Active mounts remain under $WORK_DIR. Run: sudo umount -Rlf $WORK_DIR/squashfs $WORK_DIR/layers $WORK_DIR/live_merged"
  fi

  rm -rf \
    "${WORK_DIR:?}/squashfs" \
    "${WORK_DIR:?}/custom" \
    "${WORK_DIR:?}/mnt" \
    "${WORK_DIR:?}/layers" \
    "${WORK_DIR:?}/live_delta" \
    "${WORK_DIR:?}/live_work" \
    "${WORK_DIR:?}/live_merged" \
    "${WORK_DIR:?}/empty_standard" \
    "${WORK_DIR:?}/stashed-cdrom-sources" \
    "$WORK_DIR/orig_live.squashfs" 2>/dev/null || true
}

print_dev_help() {
  cat <<EOF
Ubuntu-Nido ISO Builder (${TARGET_ARCH}, Ubuntu ${UBUNTU_VERSION}):
  sudo ./build.sh                  Full dev build (${SQUASHFS_COMP})
  sudo ./build.sh --fast           Fast local build (zstd level 1; or FAST_ISO=1)
  sudo ./build.sh --release        Full release build (xz)
  sudo ./build.sh --prepare        Extract ISO + squashfs layers into build/
  sudo ./build.sh --boot-only      Update GRUB/ISOLINUX config
  sudo ./build.sh --overlay-only   Apply config/includes.chroot overlay
  sudo ./build.sh --customize-only Run packages + overlay + hooks
  sudo ./build.sh --shell          Enter interactive chroot shell
  sudo ./build.sh --repack-only    Repack squashfs + install-sources.yaml + ISO
  sudo ./build.sh --iso-only       Repack ISO only from build/custom
  sudo ./build.sh --quick          Overlay + repack quickly
  sudo ./build.sh --clean-work     Remove work tree, keep extracted cache
  sudo ./build.sh --clean-cache    Remove work tree and extracted cache
EOF
}
