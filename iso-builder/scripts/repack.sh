#!/usr/bin/env bash
# Repack remastered SquashFS, sync install-sources.yaml, and build bootable hybrid ISO

clean_virtual_dirs() {
  local sfs="$WORK_DIR/squashfs"
  rm -rf "$sfs/tmp"/* "$sfs/var/tmp"/* 2>/dev/null || true

  local dir
  for dir in proc sys dev run; do
    if [ -d "$sfs/$dir" ]; then
      find "$sfs/$dir" -mindepth 1 -delete 2>/dev/null || true
    else
      mkdir -p "$sfs/$dir"
    fi
  done
}

step_repack_squashfs() {
  ensure_work_tree
  umount_chroot
  clean_virtual_dirs

  local casper_mode="classic"
  local squashfs_target="filesystem.squashfs"
  if [ -f "$WORK_DIR/casper-layout.env" ]; then
    # shellcheck disable=SC1090
    source "$WORK_DIR/casper-layout.env"
    casper_mode="${CASPER_MODE:-classic}"
    squashfs_target="${SQUASHFS_TARGET:-filesystem.squashfs}"
  fi

  local out_sfs="$WORK_DIR/custom/casper/${squashfs_target}"
  info "  → Packing ${squashfs_target} (${SQUASHFS_COMP})..."
  mksquashfs "$WORK_DIR/squashfs" "$out_sfs" -comp "$SQUASHFS_COMP" "${SQUASHFS_OPTS[@]}"
  ok "SquashFS packed."

  local root_bytes
  root_bytes="$(du -sx --block-size=1 "$WORK_DIR/squashfs" | cut -f1)"
  if [ "$casper_mode" = "layered" ]; then
    printf '%s' "$root_bytes" > "$WORK_DIR/custom/casper/minimal.size"
    if [ -f "$WORK_DIR/custom/casper/install-sources.yaml" ]; then
      python3 "$SCRIPT_DIR/scripts/update_install_sources.py" \
        --yaml "$WORK_DIR/custom/casper/install-sources.yaml" \
        --squashfs "$out_sfs" \
        --name "Ubuntu-Nido Desktop"
    fi
  else
    printf '%s' "$root_bytes" > "$WORK_DIR/custom/casper/filesystem.size"
  fi

  # Update live initrd so Plymouth theme changes take effect on boot
  local latest_initrd=""
  local cand
  for cand in "$WORK_DIR"/squashfs/boot/initrd.img-*; do
    if [ -f "$cand" ]; then
      latest_initrd="$cand"
    fi
  done
  if [ -n "$latest_initrd" ]; then
    local target_initrd
    for target_initrd in initrd initrd.lz initrd.gz; do
      if [ -f "$WORK_DIR/custom/casper/$target_initrd" ]; then
        cp "$latest_initrd" "$WORK_DIR/custom/casper/$target_initrd"
        ok "Updated live initrd -> casper/$target_initrd"
      fi
    done
  fi
}

step_repack_iso() {
  ensure_work_tree

  local source_iso=""
  [ -f "$WORK_DIR/source-iso.path" ] && source_iso="$(cat "$WORK_DIR/source-iso.path")"

  cd "${WORK_DIR:?}/custom" || return 1
  find . -type f ! -name 'md5sum.txt' \
    ! -path './isolinux/isolinux.bin' ! -path './isolinux/boot.cat' -print0 \
    | sort -z | xargs -0 md5sum > md5sum.txt 2>/dev/null || true

  info "  → Building hybrid bootable ISO ($TARGET_ARCH)..."

  local xorriso_args=()
  if [ -n "$source_iso" ] && [ -f "$source_iso" ]; then
    local eltorito_dump
    eltorito_dump="$(xorriso -indev "$source_iso" -report_el_torito as_mkisofs 2>/dev/null || true)"
    if [ -n "$eltorito_dump" ]; then
      mapfile -d '' -t xorriso_args < <(python3 -c '
import shlex, sys
tokens = shlex.split(sys.stdin.read())
out = []
i = 0
while i < len(tokens):
    tok = tokens[i]
    if tok in ("-V", "-volid") and i + 1 < len(tokens):
        out.extend([tok, "Ubuntu-Nido"])
        i += 2
        continue
    out.append(tok)
    i += 1
sys.stdout.buffer.write(b"\0".join(t.encode() for t in out) + b"\0")
' <<< "$eltorito_dump")
    fi
  fi

  if [ "${#xorriso_args[@]}" -eq 0 ]; then
    xorriso_args=(-as mkisofs -r -J -joliet-long -iso-level 3 -full-iso9660-filenames -volid "Ubuntu-Nido")
    if [ -f "boot/grub/i386-pc/eltorito.img" ]; then
      xorriso_args+=(-b boot/grub/i386-pc/eltorito.img -no-emul-boot -boot-load-size 4 -boot-info-table)
    elif [ -f "isolinux/isolinux.bin" ]; then
      xorriso_args+=(-b isolinux/isolinux.bin -c isolinux/boot.cat -no-emul-boot -boot-load-size 4 -boot-info-table)
    fi
    if [ -f "EFI/boot/efiboot.img" ]; then
      xorriso_args+=(-eltorito-alt-boot -e EFI/boot/efiboot.img -no-emul-boot -append_partition 2 0xef EFI/boot/efiboot.img)
    fi
  else
    xorriso_args=(-as mkisofs "${xorriso_args[@]}")
  fi

  xorriso "${xorriso_args[@]}" -output "$SCRIPT_DIR/$OUTPUT_ISO" .
  cd "$SCRIPT_DIR" || return 1
}

step_repack() {
  info "[7/7] Repacking SquashFS and ISO..."
  step_repack_squashfs
  step_repack_iso
}

step_repack_and_clean() {
  step_repack
  safe_remove_work_dirs
}
