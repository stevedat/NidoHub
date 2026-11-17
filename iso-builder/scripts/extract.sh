#!/usr/bin/env bash
# Extract Ubuntu Desktop ISO (supports both classic filesystem.squashfs and modern layered minimal.squashfs)

extract_layered_casper() {
  local dest_rootfs="$1"
  local casper_dir="$WORK_DIR/mnt/casper"
  local layer_files=("$casper_dir/minimal.squashfs")
  local layer
  for layer in minimal.standard.squashfs minimal.standard.en.squashfs; do
    if [ -f "$casper_dir/$layer" ]; then
      layer_files+=("$casper_dir/$layer")
    fi
  done

  if [ "${#layer_files[@]}" -gt 1 ]; then
    local layers_root="$WORK_DIR/layers"
    local merged_dir="$layers_root/merged"
    rm -rf "${layers_root:?}"
    mkdir -p "$merged_dir"

    local mount_ok=1
    local lower_list=()
    local idx=0
    local lf
    for lf in "${layer_files[@]}"; do
      local mdir="$layers_root/layer_${idx}"
      mkdir -p "$mdir"
      if mount -o loop,ro "$lf" "$mdir" 2>/dev/null; then
        lower_list=("$mdir" "${lower_list[@]}")
        idx=$((idx + 1))
      else
        mount_ok=0
        break
      fi
    done

    if [ "$mount_ok" -eq 1 ]; then
      local lower_spec
      lower_spec="$(IFS=:; echo "${lower_list[*]}")"
      if mount -t overlay overlay -o "ro,lowerdir=${lower_spec}" "$merged_dir" 2>/dev/null; then
        info "  → Merging ${#layer_files[@]} SquashFS layers via kernel overlayfs..."
        mkdir -p "$dest_rootfs"
        rsync -aAX "$merged_dir/" "$dest_rootfs/"
        umount_layers
        rm -rf "${layers_root:?}"
        return 0
      fi
    fi
    umount_layers
    rm -rf "${layers_root:?}"
    warn "Kernel overlayfs unavailable; falling back to unsquashfs + tar merge..."
  fi

  unsquashfs -d "$dest_rootfs" "$casper_dir/minimal.squashfs"
  for layer in minimal.standard.squashfs minimal.standard.en.squashfs; do
    if [ -f "$casper_dir/$layer" ]; then
      info "  → Merging Ubuntu layer: $layer"
      local tmp_layer
      tmp_layer="$WORK_DIR/tmp_layer_$$"
      rm -rf "${tmp_layer:?}"
      unsquashfs -d "$tmp_layer" "$casper_dir/$layer"
      tar -C "$tmp_layer" -cf - . | tar -C "$dest_rootfs" --recursive-unlink -xf -
      rm -rf "${tmp_layer:?}"
    fi
  done
}

step_extract() {
  info "[1/7] Preparing ISO build directories..."
  sync 2>/dev/null || true
  umount_chroot
  umount "$WORK_DIR/mnt" 2>/dev/null || true
  rm -rf "${WORK_DIR:?}/squashfs" "${WORK_DIR:?}/custom" "${WORK_DIR:?}/layers" \
    "${WORK_DIR:?}/live_delta" "${WORK_DIR:?}/live_work" "${WORK_DIR:?}/live_merged" \
    "${WORK_DIR:?}/empty_standard" "$WORK_DIR/orig_live.squashfs"
  mkdir -p "$WORK_DIR/mnt" "$WORK_DIR/custom"

  local use_cache=1
  if [ "${CI:-}" = "true" ] || [ "${CLEAN_OUTPUT:-false}" = "true" ] || [ "${ISO_BUILD_CACHE:-1}" = "0" ]; then
    use_cache=0
  fi

  local cache_dir="$WORK_DIR/cache"
  local layout_env="$WORK_DIR/casper-layout.env"

  if [ "$use_cache" -eq 1 ] && [ -d "$cache_dir" ] && [ -d "$WORK_DIR/cache_iso" ] && [ -f "$layout_env" ]; then
    info "[2/7] Using cached rootfs and ISO tree..."
    ok "Cache found."
    info "  → Copying cached rootfs to active work tree..."
    cp -a "$cache_dir" "$WORK_DIR/squashfs"
    cp -a "$WORK_DIR/cache_iso/." "$WORK_DIR/custom/"
    if [ -f "$WORK_DIR/cache_live.squashfs" ]; then
      cp -a "$WORK_DIR/cache_live.squashfs" "$WORK_DIR/orig_live.squashfs"
    fi
    ok "Ready for customization."
    return 0
  fi

  rm -rf "${cache_dir:?}" "${WORK_DIR:?}/cache_iso" "$WORK_DIR/cache_live.squashfs"
  info "[2/7] Extracting base Ubuntu ISO..."
  xorriso -indev "$BASE_ISO" -report_el_torito as_mkisofs > "$WORK_DIR/eltorito.args" 2>/dev/null || true

  local mounted=0
  if mount -o loop,ro "$BASE_ISO" "$WORK_DIR/mnt" 2>/dev/null; then
    mounted=1
  else
    warn "Loop mount unavailable; falling back to xorriso extraction..."
    xorriso -osirrox on -indev "$BASE_ISO" -extract / "$WORK_DIR/mnt" >/dev/null
  fi

  local iso_target="$WORK_DIR/custom"
  local rootfs_target="$WORK_DIR/squashfs"
  if [ "$use_cache" -eq 1 ]; then
    iso_target="$WORK_DIR/cache_iso"
    rootfs_target="$cache_dir"
    mkdir -p "$iso_target"
  fi

  rsync -a \
    --include='casper/minimal.manifest' \
    --include='casper/minimal.standard.live.manifest' \
    --include='casper/filesystem.manifest' \
    --exclude='casper/*.squashfs' \
    --exclude='casper/*.size' \
    --exclude='casper/*.manifest*' \
    "$WORK_DIR/mnt/" "$iso_target/"
  ok "Extracted ISO metadata and boot structure."

  info "[3/7] Unpacking SquashFS root filesystem..."
  if [ -f "$WORK_DIR/mnt/casper/minimal.squashfs" ]; then
    extract_layered_casper "$rootfs_target"
    local has_live_layer=0
    if [ -f "$WORK_DIR/mnt/casper/minimal.standard.live.squashfs" ]; then
      has_live_layer=1
      if [ "$use_cache" -eq 1 ]; then
        cp -a "$WORK_DIR/mnt/casper/minimal.standard.live.squashfs" "$WORK_DIR/cache_live.squashfs"
        cp -a "$WORK_DIR/cache_live.squashfs" "$WORK_DIR/orig_live.squashfs"
      else
        cp -a "$WORK_DIR/mnt/casper/minimal.standard.live.squashfs" "$WORK_DIR/orig_live.squashfs"
      fi
    fi
    cat > "$layout_env" <<LAYOUT
CASPER_MODE=layered
SQUASHFS_TARGET=minimal.squashfs
HAS_LIVE_LAYER=${has_live_layer}
LAYOUT
  elif [ -f "$WORK_DIR/mnt/casper/filesystem.squashfs" ]; then
    unsquashfs -d "$rootfs_target" "$WORK_DIR/mnt/casper/filesystem.squashfs"
    cat > "$layout_env" <<'LAYOUT'
CASPER_MODE=classic
SQUASHFS_TARGET=filesystem.squashfs
HAS_LIVE_LAYER=0
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

  if [ "$use_cache" -eq 1 ]; then
    cp -a "$iso_target/." "$WORK_DIR/custom/"
    ok "Rootfs extracted and cached."
    info "  → Copying cached rootfs to active work tree..."
    cp -a "$cache_dir" "$WORK_DIR/squashfs"
  else
    ok "Rootfs extracted directly to active work tree."
  fi
  ok "Ready for customization."
}
