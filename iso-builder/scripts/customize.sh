#!/usr/bin/env bash
# Chroot customization: APT packages, Nido .deb installation, overlay, and hooks

cleanup_chroot_package_state() {
  info "  → Cleaning stale apt/dpkg locks inside chroot..."
  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    export DEBIAN_FRONTEND=noninteractive
    rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock 2>/dev/null || true
    timeout 60 dpkg --configure -a --force-confdef --force-confold >/dev/null 2>&1 || true
  '
}

step_customize() {
  info "[4/7] Mounting chroot..."
  mount_chroot
  cleanup_chroot_package_state

  info "[5/7] Customizing Ubuntu-Nido rootfs..."

  # Ensure branding PNG assets exist before overlay runs
  if [ -f "$SCRIPT_DIR/../branding/generate-assets.py" ]; then
    python3 "$SCRIPT_DIR/../branding/generate-assets.py"
  fi

  # Rewrite APT mirror ONLY on amd64; arm64 must keep ports.ubuntu.com
  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    ARCH="$(dpkg --print-architecture)"
    if [ "$ARCH" = "amd64" ] && [ "${USE_VN_MIRROR:-1}" = "1" ]; then
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
        if [ -f "$f" ]; then
          sed -i "s|http://archive.ubuntu.com/ubuntu|https://mirror.bizflycloud.vn/ubuntu|g" "$f"
          sed -i "s|http://security.ubuntu.com/ubuntu|https://mirror.bizflycloud.vn/ubuntu|g" "$f"
        fi
      done
    fi
  '

  if [ -f "$SCRIPT_DIR/config/packages.txt" ]; then
    info "  → Installing base packages from config/packages.txt..."
    cp "$SCRIPT_DIR/config/packages.txt" "$WORK_DIR/squashfs/tmp/packages.txt"
    chroot "$WORK_DIR/squashfs" /bin/bash -c '
      set -e
      export DEBIAN_FRONTEND=noninteractive
      APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" update
      grep -v "^#" /tmp/packages.txt | grep -v "^[[:space:]]*$" | \
        xargs apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends
      rm -f /tmp/packages.txt
    '
    ok "Base packages installed."
  fi

  # Stage and install Ubuntu-Nido .deb packages (plus matching arch vendor debs)
  local pkg_dist="$SCRIPT_DIR/../packages/dist"
  local pkg_vendor="$SCRIPT_DIR/../packages/vendor"
  if [ -x "$SCRIPT_DIR/../packages/build-all.sh" ]; then
    info "  → Building Ubuntu-Nido .deb packages..."
    bash "$SCRIPT_DIR/../packages/build-all.sh"
  fi

  mkdir -p "$WORK_DIR/squashfs/tmp/nido-debs"
  local deb_count=0
  local deb
  for deb in "$pkg_dist"/*.deb; do
    if [ -f "$deb" ]; then
      cp "$deb" "$WORK_DIR/squashfs/tmp/nido-debs/"
      deb_count=$((deb_count + 1))
    fi
  done
  if [ -d "$pkg_vendor" ]; then
    for deb in "$pkg_vendor"/*_"${TARGET_ARCH}".deb "$pkg_vendor"/*_all.deb; do
      if [ -f "$deb" ]; then
        cp "$deb" "$WORK_DIR/squashfs/tmp/nido-debs/"
        deb_count=$((deb_count + 1))
      fi
    done
  fi

  if [ "$deb_count" -gt 0 ]; then
    info "  → Installing $deb_count Ubuntu-Nido .deb package(s) into chroot..."
    chroot "$WORK_DIR/squashfs" /bin/bash -c '
      set -e
      export DEBIAN_FRONTEND=noninteractive
      APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" update
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends /tmp/nido-debs/*.deb
      rm -rf /tmp/nido-debs
    '
    ok "Ubuntu-Nido packages installed."
  fi

  step_overlay

  local hook
  for hook in "$SCRIPT_DIR/config/hooks/live/"*.hook.chroot; do
    if [ -f "$hook" ]; then
      local hook_name
      hook_name="$(basename "$hook")"
      info "  → Running hook: $hook_name"
      cp "$hook" "$WORK_DIR/squashfs/tmp/$hook_name"
      chmod 755 "$WORK_DIR/squashfs/tmp/$hook_name"
      NIDO_VERSION="$NIDO_VERSION" \
      UBUNTU_VERSION="$UBUNTU_VERSION" \
      UBUNTU_CODENAME="$UBUNTU_CODENAME" \
      TARGET_ARCH="$TARGET_ARCH" \
      chroot "$WORK_DIR/squashfs" /bin/bash "/tmp/$hook_name"
      rm -f "$WORK_DIR/squashfs/tmp/$hook_name"
      ok "Hook $hook_name completed."
    fi
  done

  info "[6/7] Verifying customized rootfs and cleaning apt cache..."
  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    set -e
    test -f /etc/dconf/db/local
    test -x /usr/bin/nidoai
    test -x /usr/bin/nidoagent
    test -f /usr/share/plymouth/themes/ubuntu-nido/ubuntu-nido.plymouth
    apt-get clean
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    date -u +"%Y-%m-%dT%H:%M:%SZ" > /etc/ubuntu-nido-customized
  '

  umount_chroot
  ok "Customization verified and chroot unmounted."
}
