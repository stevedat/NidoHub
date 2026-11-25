#!/usr/bin/env bash
# Repack remastered SquashFS, sync install-sources.yaml, and build bootable hybrid ISO

clean_virtual_dirs() {
  local sfs="$WORK_DIR/squashfs"
  rm -rf "$sfs/tmp"/* "$sfs/var/tmp"/* 2>/dev/null || true
  # Restore systemd-resolved symlink so host CI DNS config never leaks into the ISO
  ln -snf ../run/systemd/resolve/stub-resolv.conf "$sfs/etc/resolv.conf"

  local dir
  for dir in proc sys dev run; do
    if [ -d "$sfs/$dir" ]; then
      find "$sfs/$dir" -mindepth 1 -delete 2>/dev/null || true
    else
      mkdir -p "$sfs/$dir"
    fi
  done
}

repack_layered_live_overlays() {
  local casper_dir="$WORK_DIR/custom/casper"
  info "  → Creating pass-through minimal.standard.squashfs layer..."
  rm -rf "${WORK_DIR:?}/empty_standard" "${WORK_DIR:?}/live_delta" \
    "${WORK_DIR:?}/live_work" "${WORK_DIR:?}/live_merged"
  mkdir -p "$WORK_DIR/empty_standard" "$WORK_DIR/live_work" "$WORK_DIR/live_merged"

  mksquashfs "$WORK_DIR/empty_standard" "$casper_dir/minimal.standard.squashfs" \
    -comp "$SQUASHFS_COMP" "${SQUASHFS_OPTS[@]}"
  printf '0' > "$casper_dir/minimal.standard.size"
  rm -rf "${WORK_DIR:?}/empty_standard"

  info "  → Reconciling minimal.standard.live.squashfs against customized rootfs..."
  unsquashfs -d "$WORK_DIR/live_delta" "$WORK_DIR/orig_live.squashfs"
  python3 "$SCRIPT_DIR/scripts/reconcile_live_layer.py" \
    --base-rootfs "$WORK_DIR/squashfs" \
    --live-delta "$WORK_DIR/live_delta"

  mount -t overlay overlay \
    -o "lowerdir=$WORK_DIR/squashfs,upperdir=$WORK_DIR/live_delta,workdir=$WORK_DIR/live_work" \
    "$WORK_DIR/live_merged"
  mount --bind /dev "$WORK_DIR/live_merged/dev"
  mount --bind /dev/pts "$WORK_DIR/live_merged/dev/pts"
  mount -t proc proc "$WORK_DIR/live_merged/proc"
  mount -t sysfs sysfs "$WORK_DIR/live_merged/sys"

  chroot "$WORK_DIR/live_merged" /bin/bash -c '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    mapfile -t LIVE_PURGE < <(
      dpkg-query -W -f="\${db:Status-Abbrev}\t\${Package}\n" \
        "libreoffice*" "python3-uno" "uno-libs-private" "ure" "ure-java" "liblibreoffice*" "mythes-*" "hyphen-*" \
        "fcitx5" "fcitx5-*" \
        "aisleriot" "gnome-mahjongg" "gnome-mines" "gnome-sudoku" "quadrapassel" \
        "transmission-gtk" "transmission-common" "rhythmbox" "rhythmbox-data" "rhythmbox-plugins" \
        "shotwell" "shotwell-common" "remmina" "remmina-common" "remmina-plugin-*" "deja-dup" "thunderbird" "ibus-unikey" 2>/dev/null \
        | awk "\$1 ~ /^i/ && \$2 != \"vielotus\" { print \$2 }" \
        | sort -u || true
    )
    if [ "${#LIVE_PURGE[@]}" -gt 0 ]; then
      apt-get purge -y "${LIVE_PURGE[@]}" || true
      apt-get autoremove -y --purge || true
    fi
    rm -rf /usr/lib/libreoffice /etc/libreoffice /usr/share/libreoffice \
      /usr/share/applications/libreoffice*.desktop \
      /usr/share/applications/*fcitx*.desktop \
      /etc/xdg/autostart/*fcitx*.desktop \
      /usr/share/applications/thunderbird*.desktop \
      /usr/share/applications/ibus-setup-Unikey.desktop \
      /var/lib/snapd/desktop/applications/thunderbird_*.desktop \
      /var/lib/snapd/desktop/applications/snap-store_*.desktop \
      /var/lib/snapd/desktop/applications/desktop-security-center_*.desktop \
      /var/lib/snapd/desktop/applications/firefox_*.desktop \
      /usr/share/applications/transmission*.desktop \
      /usr/share/applications/org.gnome.Rhythmbox3*.desktop \
      /usr/share/applications/org.gnome.Shotwell*.desktop \
      /usr/share/applications/org.remmina.Remmina*.desktop \
      /usr/share/applications/org.gnome.DejaDup*.desktop \
      /etc/xdg/autostart/org.gnome.DejaDup*.desktop \
      /etc/xdg/autostart/ubuntu-report-on-upgrade.desktop
    if [ -f /etc/casper.conf ]; then
      sed -i \
        -e "s/^export HOST=.*/export HOST=\"nidoos\"/" \
        -e "s/^export BUILD_SYSTEM=.*/export BUILD_SYSTEM=\"NidoOS\"/" \
        -e "s/^export USERFULLNAME=.*/export USERFULLNAME=\"NidoOS Live\"/" \
        /etc/casper.conf || true
    fi
    for sys_mf in /usr/share/applications/ubuntu-mimeapps.list /usr/share/applications/gnome-mimeapps.list; do
      if [ -f "$sys_mf" ]; then
        sed -i -E \
          -e "s/libreoffice-(writer|calc|impress)\.desktop/nidooffice.desktop/g" \
          -e "s/libreoffice-[A-Za-z0-9_.-]+\.desktop;?//g" \
          -e "s/thunderbird_thunderbird\.desktop/org.gnome.Geary.desktop/g" \
          -e "s/thunderbird\.desktop/org.gnome.Geary.desktop/g" \
          -e "/^[^=[]+=$/d" \
          "$sys_mf" || true
        ! grep -q "libreoffice-" "$sys_mf"
        ! grep -q "thunderbird" "$sys_mf"
      fi
    done
    ldconfig
    if [ -f /usr/lib/systemd/system/nido-installer-fonts.service ]; then
      mkdir -p /etc/systemd/system/graphical.target.wants
      ln -snf /usr/lib/systemd/system/nido-installer-fonts.service \
        /etc/systemd/system/graphical.target.wants/nido-installer-fonts.service
    fi
    if [ -f /var/lib/snapd/desktop/applications/ubuntu-desktop-bootstrap_ubuntu-desktop-bootstrap.desktop ]; then
      sed -i "s/^Name=Install RELEASE/Name=Cài đặt NidoOS/" \
        /var/lib/snapd/desktop/applications/ubuntu-desktop-bootstrap_ubuntu-desktop-bootstrap.desktop || true
    fi
    if [ -d /usr/share/glib-2.0/schemas ]; then
      glib-compile-schemas /usr/share/glib-2.0/schemas/
    fi
    if [ -d /etc/dconf/db/local.d ]; then
      dconf compile /etc/dconf/db/local /etc/dconf/db/local.d || dconf update || true
    fi
    if [ -d /etc/dconf/db/gdm.d ]; then
      dconf compile /etc/dconf/db/gdm /etc/dconf/db/gdm.d || dconf update || true
    fi
    if command -v ibus >/dev/null 2>&1; then
      ibus write-cache --system >/dev/null 2>&1 || true
    fi
    if command -v fc-cache >/dev/null 2>&1; then
      fc-cache -rf >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
      gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
      update-desktop-database -q /usr/share/applications || true
    fi
    apt-get check
    test -x /sbin/init
    test -f /etc/fonts/conf.d/52-ubuntu-nido-vietnamese.conf
    test -f /usr/share/fonts/truetype/be-vietnam-pro/BeVietnamPro-Regular.ttf
    ! ls /usr/share/fonts/truetype/be-vietnam-pro/*\[wght\]*.ttf >/dev/null 2>&1
    fc-match -f "%{family}\n" ":charset=1ec7" | grep -Eq "Be Vietnam Pro|Noto Sans"
    fc-match -f "%{family}\n" "Ubuntu:charset=1ec7" | grep -Eq "Be Vietnam Pro|Noto Sans"
    fc-match -f "%{family}\n" "Ubuntu Mono" | grep -Eq "Noto Mono|Liberation Mono"
    fc-match -f "%{family}\n" "Ubuntu Mono:charset=1ec3" | grep -Eq "Noto Mono|Liberation Mono"
    fc-match -f "%{family}\n" "monospace:charset=1ec3" | grep -Eq "Noto Mono|Liberation Mono"
    ! fc-list : file | grep -q "/usr/share/fonts/truetype/ubuntu/"
    test -x /usr/libexec/ubuntu-nido/ensure-vietnamese-ime
    /usr/libexec/ubuntu-nido/ensure-vietnamese-ime --self-test >/dev/null
    test -x /usr/libexec/ubuntu-nido/bind-installer-fonts
    /usr/libexec/ubuntu-nido/bind-installer-fonts --self-test >/dev/null
    /usr/libexec/ubuntu-nido/bind-installer-fonts --verify-system >/dev/null
    test -L /etc/systemd/system/graphical.target.wants/nido-installer-fonts.service
    test -f /usr/share/desktop-provision/whitelabel.yaml
    test -f /usr/share/desktop-provision/images/nidoos-logo-dark.svg
    test -f /usr/share/desktop-provision/slides/1/slide_vi_VN.html
    test -x /usr/local/bin/himalaya
    dpkg -s geary >/dev/null
    test -f /etc/xdg/autostart/ubuntu-nido-ime-sync.desktop
    grep -q "vielotus-vni" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    grep -q "vielotus" /usr/share/glib-2.0/schemas/90_nidoos-input-sources.gschema.override
    test -x /usr/bin/nidoai
    test -x /usr/bin/nidoagent
    test -x /usr/bin/nido-hub
    test -x /usr/bin/nido-security-verify
    test -f /etc/apparmor.d/nidoai-gateway
    if [ -x /usr/libexec/nidoai/nidoai-sea ]; then
      test ! -d /usr/lib/nidoai/src
      ls /usr/lib/nido-hub/_*_native*.so >/dev/null
    fi
    test -x /usr/bin/nidobox
    test -x /usr/bin/nidooffice
    test -x /usr/bin/nidooffice-desktop
    test -x /usr/local/bin/genoffice
    test -x /opt/NidoOffice/genoffice
    grep -q "^StartupWMClass=nidooffice$" /usr/share/applications/nidooffice.desktop
    grep -q "{nidooffice,genoffice}" /etc/apparmor.d/nidooffice
    grep -q -- "--enable-wayland-ime" /usr/bin/nidooffice-desktop
    ! grep -q "libreoffice-" /etc/xdg/ubuntu-mimeapps.list
    dpkg -s nido-desktop >/dev/null
    dpkg -s nidooffice-desktop >/dev/null
    dpkg -s ubuntu-desktop-minimal >/dev/null
    dpkg -s vielotus >/dev/null
    dpkg -s nido-ime-bridge >/dev/null
    test -x /usr/lib/ibus/nido-ibus-vielotus
    /usr/lib/ibus/nido-ibus-vielotus --self-test >/dev/null
    dpkg -s nidobox >/dev/null
    dpkg --compare-versions "$(dpkg-query -W -f="\${Version}" nidobox)" ge "1.5.0"
    dpkg -s showtime >/dev/null 2>&1 || dpkg -s totem >/dev/null 2>&1
    dpkg -s casper >/dev/null
    ! dpkg -s fcitx5 >/dev/null 2>&1
    ! dpkg -s libreoffice-core >/dev/null 2>&1
    ! dpkg -s rhythmbox >/dev/null 2>&1
    ! dpkg -s transmission-gtk >/dev/null 2>&1
    ! dpkg -s shotwell >/dev/null 2>&1
  '

  umount_layers
  python3 "$SCRIPT_DIR/scripts/reconcile_live_layer.py" \
    --strip-overlay-xattrs "$WORK_DIR/live_delta"

  rm -rf "${WORK_DIR:?}/live_delta/tmp/"* "${WORK_DIR:?}/live_delta/var/tmp/"* 2>/dev/null || true

  mksquashfs "$WORK_DIR/live_delta" "$casper_dir/minimal.standard.live.squashfs" \
    -comp "$SQUASHFS_COMP" "${SQUASHFS_OPTS[@]}"
  local live_bytes
  live_bytes="$(du -sx --block-size=1 "$WORK_DIR/live_delta" | cut -f1)"
  printf '%s' "$live_bytes" > "$casper_dir/minimal.standard.live.size"

  rm -rf "${WORK_DIR:?}/live_delta" "${WORK_DIR:?}/live_work" \
    "${WORK_DIR:?}/live_merged" "$WORK_DIR/orig_live.squashfs"
  ok "Layered Casper stack packed and verified cleanly."
}

step_repack_squashfs() {
  ensure_work_tree
  umount_chroot
  clean_virtual_dirs

  local casper_mode="classic"
  local squashfs_target="filesystem.squashfs"
  local has_live_layer=0
  if [ -f "$WORK_DIR/casper-layout.env" ]; then
    # shellcheck disable=SC1090
    source "$WORK_DIR/casper-layout.env"
    casper_mode="${CASPER_MODE:-classic}"
    squashfs_target="${SQUASHFS_TARGET:-filesystem.squashfs}"
    has_live_layer="${HAS_LIVE_LAYER:-0}"
  fi

  local out_sfs="$WORK_DIR/custom/casper/${squashfs_target}"
  info "  → Packing ${squashfs_target} (${SQUASHFS_COMP})..."
  mksquashfs "$WORK_DIR/squashfs" "$out_sfs" -comp "$SQUASHFS_COMP" "${SQUASHFS_OPTS[@]}"
  ok "SquashFS packed."

  local root_bytes
  root_bytes="$(du -sx --block-size=1 "$WORK_DIR/squashfs" | cut -f1)"
  if [ "$casper_mode" = "layered" ]; then
    printf '%s' "$root_bytes" > "$WORK_DIR/custom/casper/minimal.size"
    if [ "$has_live_layer" = "1" ] && [ -f "$WORK_DIR/orig_live.squashfs" ]; then
      repack_layered_live_overlays
    fi
    if [ -f "$WORK_DIR/custom/casper/install-sources.yaml" ]; then
      python3 "$SCRIPT_DIR/scripts/update_install_sources.py" \
        --yaml "$WORK_DIR/custom/casper/install-sources.yaml" \
        --squashfs "$out_sfs" \
        --name "NidoOS Desktop"
    fi
  else
    printf '%s' "$root_bytes" > "$WORK_DIR/custom/casper/filesystem.size"
  fi

  # IMPORTANT: Never replace casper/initrd with /boot/initrd.img-* from the
  # rootfs (that strips Casper live-boot hooks). Instead, preserve 100% of
  # casper/initrd intact and append a tiny 4-byte-aligned uncompressed CPIO
  # overlay replacing Ubuntu's Plymouth boot logos with NidoOS assets.
  local nido_logo="$SCRIPT_DIR/config/includes.chroot/usr/share/backgrounds/ubuntu-nido/nidoos-logo.png"
  if [ -f "$WORK_DIR/custom/casper/initrd" ] && [ -f "$nido_logo" ] && [ -f "$SCRIPT_DIR/scripts/patch_initrd_branding.py" ]; then
    info "  → Appending NidoOS Plymouth branding CPIO overlay to casper/initrd..."
    python3 "$SCRIPT_DIR/scripts/patch_initrd_branding.py" \
      --initrd "$WORK_DIR/custom/casper/initrd" \
      --logo "$nido_logo"
  else
    warn "Skipping casper/initrd branding overlay (missing initrd or nidoos-logo.png)."
  fi

  if [ "${CLEAN_OUTPUT:-false}" = "true" ]; then
    rm -rf "${WORK_DIR:?}/squashfs"
    info "Removed unpacked squashfs tree to free disk space before ISO mastering."
  fi
}

step_repack_iso() {
  if [ ! -d "$WORK_DIR/custom" ]; then
    error "Missing $WORK_DIR/custom directory for ISO repack."
  fi

  local source_iso=""
  [ -f "$WORK_DIR/source-iso.path" ] && source_iso="$(cat "$WORK_DIR/source-iso.path")"

  cd "${WORK_DIR:?}/custom" || return 1
  if [ -f ".disk/info" ]; then
    grep -Eq '^NidoOS [0-9]+\.[0-9]+(\.[0-9]+)? LTS "[^"]+" - Release (amd64|arm64) \([0-9]{8}\)$' ".disk/info" || {
      err "Invalid .disk/info format (breaks ubuntu-desktop-bootstrap): $(cat .disk/info)"
      return 1
    }
  fi
  find . -type f ! -name 'md5sum.txt' \
    ! -path './isolinux/isolinux.bin' ! -path './isolinux/boot.cat' \
    ! -path './boot/boot.cat' ! -path './boot.catalog' \
    ! -path './boot/grub/i386-pc/eltorito.img' -print0 \
    | sort -z | xargs -0 md5sum > md5sum.txt 2>/dev/null || true

  info "  → Building hybrid bootable ISO ($TARGET_ARCH)..."

  local eltorito_dump=""
  if [ -s "$WORK_DIR/eltorito.args" ]; then
    eltorito_dump="$(cat "$WORK_DIR/eltorito.args")"
  elif [ -n "$source_iso" ] && [ -f "$source_iso" ]; then
    eltorito_dump="$(xorriso -indev "$source_iso" -report_el_torito as_mkisofs 2>/dev/null || true)"
  fi

  local xorriso_args=()
  if [ -n "$eltorito_dump" ]; then
    mapfile -d '' -t xorriso_args < <(python3 -c '
import shlex, sys
tokens = shlex.split(sys.stdin.read())
out = []
i = 0
while i < len(tokens):
    tok = tokens[i]
    if tok in ("-V", "-volid") and i + 1 < len(tokens):
        out.extend([tok, "NidoOS"])
        i += 2
        continue
    out.append(tok)
    i += 1
sys.stdout.buffer.write(b"\0".join(t.encode() for t in out) + b"\0")
' <<< "$eltorito_dump")
  fi

  if [ "${#xorriso_args[@]}" -eq 0 ]; then
    xorriso_args=(-as mkisofs -r -J -joliet-long -iso-level 3 -full-iso9660-filenames -volid "NidoOS")
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

  mkdir -p "$OUTPUT_DIR"
  xorriso "${xorriso_args[@]}" -output "$OUTPUT_DIR/$OUTPUT_ISO" .
  cd "$SCRIPT_DIR" || return 1

  if [ "${CI:-}" = "true" ] && [ "${KEEP_BASE_ISO:-0}" != "1" ] && [ -n "$source_iso" ] && [ -f "$source_iso" ]; then
    rm -f "$source_iso"
    info "Removed base ISO after xorriso to free runner disk space."
  fi
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
