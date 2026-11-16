#!/usr/bin/env bash
# Extract Ubuntu Desktop ISO (supports both classic filesystem.squashfs and modern layered minimal.squashfs)

step_extract() {
  info "[1/7] Preparing ISO build directories..."
  sync 2>/dev/null || true
  umount "$WORK_DIR/squashfs/proc"    2>/dev/null || true
  umount "$WORK_DIR/squashfs/sys"     2>/dev/null || true
  umount "$WORK_DIR/squashfs/dev/pts" 2>/dev/null || true
  umount "$WORK_DIR/squashfs/dev"     2>/dev/null || true
  umount "$WORK_DIR/mnt"              2>/dev/null || true
  rm -rf "$WORK_DIR/squashfs" "$WORK_DIR/custom"
  mkdir -p "$WORK_DIR/mnt" "$WORK_DIR/custom"

  local cache_dir="$WORK_DIR/cache"
  local layout_env="$WORK_DIR/casper-layout.env"

  if [ -d "$cache_dir" ] && [ -d "$WORK_DIR/cache_iso" ] && [ -f "$layout_env" ]; then
    info "[2/7] Using cached rootfs and ISO tree..."
    ok "Cache found."
  else
    rm -rf "$cache_dir" "$WORK_DIR/cache_iso"
    info "[2/7] Extracting base Ubuntu ISO..."
    local mounted=0
    if mount -o loop,ro "$BASE_ISO" "$WORK_DIR/mnt" 2>/dev/null; then
      mounted=1
    else
      warn "Loop mount unavailable; falling back to xorriso extraction..."
      xorriso -osirrox on -indev "$BASE_ISO" -extract / "$WORK_DIR/mnt" >/dev/null
    fi

    rsync -a \
      --exclude='casper/filesystem.squashfs' \
      --exclude='casper/minimal.squashfs' \
      --exclude='casper/minimal.standard.squashfs' \
      --exclude='casper/minimal.standard.en.squashfs' \
      "$WORK_DIR/mnt/" "$WORK_DIR/custom/"
    ok "Extracted ISO metadata and boot structure."

    info "[3/7] Unpacking SquashFS root filesystem..."
    if [ -f "$WORK_DIR/mnt/casper/minimal.squashfs" ]; then
      unsquashfs -d "$cache_dir" "$WORK_DIR/mnt/casper/minimal.squashfs"
      for layer in minimal.standard.squashfs minimal.standard.en.squashfs; do
        if [ -f "$WORK_DIR/mnt/casper/$layer" ]; then
          info "  → Merging Ubuntu layer: $layer"
          local tmp_layer="$WORK_DIR/tmp_layer_$$"
          rm -rf "$tmp_layer"
          unsquashfs -d "$tmp_layer" "$WORK_DIR/mnt/casper/$layer"
          rsync -a --remove-source-files "$tmp_layer/" "$cache_dir/"
          rm -rf "$tmp_layer"
        fi
      done
      cat > "$layout_env" <<'LAYOUT'
CASPER_MODE=layered
SQUASHFS_TARGET=minimal.squashfs
LAYOUT
    elif [ -f "$WORK_DIR/mnt/casper/filesystem.squashfs" ]; then
      unsquashfs -d "$cache_dir" "$WORK_DIR/mnt/casper/filesystem.squashfs"
      cat > "$layout_env" <<'LAYOUT'
CASPER_MODE=classic
SQUASHFS_TARGET=filesystem.squashfs
LAYOUT
    else
      [ "$mounted" -eq 1 ] && umount "$WORK_DIR/mnt" || true
      error "Unsupported ISO layout: neither casper/minimal.squashfs nor casper/filesystem.squashfs found."
    fi

    if [ "$mounted" -eq 1 ]; then
      umount "$WORK_DIR/mnt"
    else
      rm -rf "${WORK_DIR:?}/mnt"
      mkdir -p "${WORK_DIR:?}/mnt"
    fi

    cp -a "$WORK_DIR/custom" "$WORK_DIR/cache_iso"
    ok "Rootfs extracted and cached."
  fi

  info "  → Copying cached rootfs to active work tree..."
  cp -a "$cache_dir" "$WORK_DIR/squashfs"
  cp -a "$WORK_DIR/cache_iso/." "$WORK_DIR/custom/"
  ok "Ready for customization."
}
