#!/usr/bin/env bash
# Configure GRUB / ISOLINUX branding, locale, and Plymouth splash flags

step_boot_config() {
  local iso_dir="$WORK_DIR/custom"
  local is_debug=false
  [ "${DEBUG_BOOT:-0}" = "1" ] && is_debug=true

  if $is_debug; then
    info "[2.5/7] Configuring debug boot (verbose kernel logs)..."
  else
    info "[2.5/7] Configuring Ubuntu-Nido bootloader branding..."
  fi

  local brand_name="Ubuntu-Nido"
  local brand_title="${brand_name} ${NIDO_VERSION} (${TARGET_ARCH})"
  local boot_splash="$SCRIPT_DIR/config/includes.chroot/usr/share/backgrounds/ubuntu-nido/splash.png"

  if [ -f "$boot_splash" ]; then
    mkdir -p "$iso_dir/boot/grub"
    cp "$boot_splash" "$iso_dir/boot/grub/splash.png"
  fi

  local cfg
  while IFS= read -r -d '' cfg; do
    sed -i \
      -e "s/Try or Install Ubuntu/${brand_title}/g" \
      -e "s/Ubuntu (safe graphics)/${brand_name} (safe graphics)/g" \
      -e "s/Ubuntu/${brand_name}/g" \
      "$cfg"

    if ! grep -q 'locale=vi_VN.UTF-8' "$cfg"; then
      sed -i 's/boot=casper/boot=casper locale=vi_VN.UTF-8/g' "$cfg"
    fi

    if $is_debug; then
      sed -i '/^[[:space:]]*linux/s/\bquiet\b//g; /^[[:space:]]*linux/s/\bsplash\b//g' "$cfg"
    fi
    ok "  Updated GRUB config: $(basename "$cfg")"
  done < <(find "$iso_dir" \( -path "*/grub/*.cfg" -o -path "*/EFI/boot/*.cfg" \) -type f -print0 2>/dev/null)

  while IFS= read -r -d '' cfg; do
    sed -i \
      -e "s/Try or Install Ubuntu/${brand_title}/g" \
      -e "s/Ubuntu/${brand_name}/g" \
      "$cfg"
    if ! grep -q 'locale=vi_VN.UTF-8' "$cfg"; then
      sed -i 's/boot=casper/boot=casper locale=vi_VN.UTF-8/g' "$cfg"
    fi
    if $is_debug; then
      sed -i '/^[[:space:]]*append/Is/\bquiet\b//g; /^[[:space:]]*append/Is/\bsplash\b//g' "$cfg"
    fi
    ok "  Updated ISOLINUX config: $(basename "$cfg")"
  done < <(find "$iso_dir" \( -path "*/isolinux/*.cfg" -o -path "*/syslinux/*.cfg" \) -type f -print0 2>/dev/null)

  ok "[2.5/7] Boot config complete."
}
