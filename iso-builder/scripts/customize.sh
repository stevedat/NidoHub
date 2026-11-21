#!/usr/bin/env bash
# Chroot customization: APT packages, Node.js, Nido .deb installation, overlay, and hooks

cleanup_chroot_package_state() {
  info "  → Cleaning stale apt/dpkg locks inside chroot..."
  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    export DEBIAN_FRONTEND=noninteractive
    rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock 2>/dev/null || true
    timeout 60 dpkg --configure -a --force-confdef --force-confold >/dev/null 2>&1 || true
  '
}

sanitize_chroot_apt_sources() {
  info "  → Sanitizing chroot APT sources (removing file:/cdrom & enabling universe)..."
  chroot "$WORK_DIR/squashfs" python3 - <<'PY'
import glob
from pathlib import Path

for p in glob.glob("/etc/apt/sources.list.d/*cdrom*"):
    Path(p).unlink(missing_ok=True)

for list_path_str in ["/etc/apt/sources.list", *glob.glob("/etc/apt/sources.list.d/*.list")]:
    lp = Path(list_path_str)
    if not lp.is_file():
        continue
    out_lines = []
    for line in lp.read_text(encoding="utf-8", errors="replace").splitlines():
        if "file:/cdrom" in line or "cdrom:" in line:
            if not line.lstrip().startswith("#"):
                out_lines.append("# disabled-in-chroot: " + line)
                continue
        out_lines.append(line)
    lp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

for src_path_str in glob.glob("/etc/apt/sources.list.d/*.sources"):
    sp = Path(src_path_str)
    if not sp.is_file():
        continue
    raw = sp.read_text(encoding="utf-8", errors="replace")
    stanzas = [s.strip() for s in raw.split("\n\n") if s.strip()]
    kept = []
    for stanza in stanzas:
        low = stanza.lower()
        if "file:/cdrom" in low or "cdrom:" in low:
            continue
        lines = stanza.splitlines()
        is_ubuntu = any(
            ("ubuntu.com" in ln.lower() or "bizflycloud.vn" in ln.lower())
            and ln.lower().startswith("uris:")
            for ln in lines
        )
        if is_ubuntu:
            new_lines = []
            for ln in lines:
                if ln.startswith("Components:"):
                    comps = ln.split(":", 1)[1].split()
                    if "universe" not in comps:
                        comps.append("universe")
                    new_lines.append("Components: " + " ".join(comps))
                else:
                    new_lines.append(ln)
            lines = new_lines
        kept.append("\n".join(lines))
    sp.write_text("\n\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
PY
}

ensure_chroot_nodejs_22() {
  info "  → Ensuring Node.js >= 22 inside chroot..."
  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
    if command -v node >/dev/null 2>&1; then
      NODE_MAJOR="$(node -p "process.versions.node.split(\".\")[0]" 2>/dev/null || echo 0)"
      if [ "$NODE_MAJOR" -ge 22 ]; then
        exit 0
      fi
    fi

    CAND="$(apt-cache policy nodejs 2>/dev/null | awk "/Candidate:/ {print \$2}" || true)"
    CAND_MAJOR="${CAND%%.*}"
    CAND_MAJOR="${CAND_MAJOR#*:}"
    if [[ "$CAND_MAJOR" =~ ^[0-9]+$ ]] && [ "$CAND_MAJOR" -ge 22 ]; then
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends nodejs
    else
      curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends nodejs
    fi
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

  sanitize_chroot_apt_sources

  # Rewrite archive mirror ONLY on amd64; NEVER rewrite security.ubuntu.com (keep upstream security direct)
  # and NEVER touch ports.ubuntu.com on arm64.
  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    ARCH="$(dpkg --print-architecture)"
    if [ "$ARCH" = "amd64" ] && [ "${USE_VN_MIRROR:-1}" = "1" ]; then
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do
        if [ -f "$f" ]; then
          sed -i "s|http://archive.ubuntu.com/ubuntu|https://mirror.bizflycloud.vn/ubuntu|g" "$f"
        fi
      done
    fi
  '

  chroot "$WORK_DIR/squashfs" /bin/bash -c '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
    apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" update
  '

  if [ -f "$SCRIPT_DIR/config/packages.txt" ]; then
    info "  → Installing base packages from config/packages.txt..."
    cp "$SCRIPT_DIR/config/packages.txt" "$WORK_DIR/squashfs/tmp/packages.txt"
    chroot "$WORK_DIR/squashfs" /bin/bash -c '
      set -e
      export DEBIAN_FRONTEND=noninteractive
      APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
      grep -v "^#" /tmp/packages.txt | grep -v "^[[:space:]]*$" | \
        xargs apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends
      rm -f /tmp/packages.txt
    '
    ok "Base packages installed."
  fi

  ensure_chroot_nodejs_22

  # Stage and install Ubuntu-Nido .deb packages (plus matching arch vendor debs)
  local pkg_dist="$SCRIPT_DIR/../packages/dist"
  local pkg_vendor="$SCRIPT_DIR/../packages/vendor"
  if [ -x "$SCRIPT_DIR/../packages/build-all.sh" ]; then
    info "  → Building Ubuntu-Nido .deb packages..."
    bash "$SCRIPT_DIR/../packages/build-all.sh"
  fi

  mkdir -p "$WORK_DIR/squashfs/tmp/nido-debs" "$WORK_DIR/squashfs/tmp/nido-meta-debs"
  local deb_count=0
  local meta_count=0
  local deb
  for deb in "$pkg_dist"/*.deb; do
    if [ -f "$deb" ]; then
      case "$(basename "$deb")" in
        nido-desktop_*.deb)
          cp "$deb" "$WORK_DIR/squashfs/tmp/nido-meta-debs/"
          meta_count=$((meta_count + 1))
          ;;
        *)
          cp "$deb" "$WORK_DIR/squashfs/tmp/nido-debs/"
          deb_count=$((deb_count + 1))
          ;;
      esac
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
    info "  → Installing $deb_count base Ubuntu-Nido .deb package(s) into chroot..."
    chroot "$WORK_DIR/squashfs" /bin/bash -c '
      set -e
      export DEBIAN_FRONTEND=noninteractive
      APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends /tmp/nido-debs/*.deb
      rm -rf /tmp/nido-debs
    '
    ok "Base Ubuntu-Nido packages installed."
  else
    rm -rf "$WORK_DIR/squashfs/tmp/nido-debs"
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
      HOME=/root \
      NIDO_VERSION="$NIDO_VERSION" \
      UBUNTU_VERSION="$UBUNTU_VERSION" \
      UBUNTU_CODENAME="$UBUNTU_CODENAME" \
      TARGET_ARCH="$TARGET_ARCH" \
      chroot "$WORK_DIR/squashfs" /bin/bash "/tmp/$hook_name"
      rm -f "$WORK_DIR/squashfs/tmp/$hook_name"
      ok "Hook $hook_name completed."
    fi
  done

  if [ "$meta_count" -gt 0 ]; then
    info "  → Installing $meta_count Nido Minimal Desktop metapackage(s) into chroot..."
    HOME=/root chroot "$WORK_DIR/squashfs" /bin/bash -c '
      set -e
      export DEBIAN_FRONTEND=noninteractive
      APT_LOCK_TIMEOUT="${APT_LOCK_TIMEOUT:-600}"
      apt-get -o DPkg::Lock::Timeout="$APT_LOCK_TIMEOUT" install -y --no-install-recommends /tmp/nido-meta-debs/*.deb
      apt-mark manual nido-desktop >/dev/null 2>&1 || true
      rm -rf /tmp/nido-meta-debs
    '
    ok "Nido Minimal Desktop metapackage installed."
  else
    rm -rf "$WORK_DIR/squashfs/tmp/nido-meta-debs"
  fi

  info "[6/7] Verifying customized rootfs and cleaning apt cache..."
  HOME=/root chroot "$WORK_DIR/squashfs" /bin/bash -c '
    set -e
    test -f /etc/dconf/db/local
    test -f /etc/fonts/conf.d/52-ubuntu-nido-vietnamese.conf
    test -f /usr/share/fonts/truetype/be-vietnam-pro/BeVietnamPro-Regular.ttf
    ! ls /usr/share/fonts/truetype/be-vietnam-pro/*\[wght\]*.ttf >/dev/null 2>&1
    fc-match -f "%{family}\n" ":charset=1ec7" | grep -Eq "Be Vietnam Pro|Noto Sans"
    fc-match -f "%{family}\n" "Ubuntu:charset=1ec7" | grep -Eq "Be Vietnam Pro|Noto Sans"
    test -x /usr/libexec/ubuntu-nido/ensure-vietnamese-ime
    /usr/libexec/ubuntu-nido/ensure-vietnamese-ime --self-test >/dev/null
    test -x /usr/libexec/ubuntu-nido/bind-installer-fonts
    /usr/libexec/ubuntu-nido/bind-installer-fonts --self-test >/dev/null
    python3 -c "import struct; from pathlib import Path; data=Path(\"/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf\").read_bytes(); nt=struct.unpack(\">H\",data[4:6])[0]; off=next(o for i in range(nt) for tag,_,o,_ in [struct.unpack(\">4sIII\",data[12+i*16:28+i*16])] if tag==b\"cmap\"); ns=struct.unpack(\">H\",data[off+2:off+4])[0]; chars=set(); [chars.update(range(s,e+1)) for i in range(ns) for _,_,so in [struct.unpack(\">HHI\",data[off+4+i*8:off+12+i*8])] for so2 in [off+so] if struct.unpack(\">H\",data[so2:so2+2])[0]==4 for sc in [struct.unpack(\">H\",data[so2+6:so2+8])[0]//2] for s,e in zip(struct.unpack(f\">{sc}H\",data[so2+16+sc*2:so2+16+sc*4]),struct.unpack(f\">{sc}H\",data[so2+14:so2+14+sc*2])) if s!=0xFFFF]; [chars.update(range(s,e+1)) for i in range(ns) for _,_,so in [struct.unpack(\">HHI\",data[off+4+i*8:off+12+i*8])] for so2 in [off+so] if struct.unpack(\">H\",data[so2:so2+2])[0]==12 for g in range(struct.unpack(\">I\",data[so2+12:so2+16])[0]) for s,e,_ in [struct.unpack(\">III\",data[so2+16+g*12:so2+28+g*12])]]; missing=[hex(cp) for cp in (0x1ECD,0x1EC7,0x1EA3,0x1EED,0x1EBF,0x1EEF) if cp not in chars]; assert not missing, f\"NotoSans missing {missing}\""
    test -f /usr/lib/systemd/system/nido-installer-fonts.service
    test -L /etc/systemd/system/graphical.target.wants/nido-installer-fonts.service
    test -f /usr/share/desktop-provision/whitelabel.yaml
    test -f /usr/share/desktop-provision/images/nidoos-logo-dark.svg
    test -f /usr/share/desktop-provision/images/nidoos-logo-light.svg
    test -f /usr/share/desktop-provision/slides/1/slide_vi_VN.html
    test -f /usr/share/ubuntu-nido/skills/ubuntu-nido-email/SKILL.md
    test -f /usr/local/share/applications/thunderbird_thunderbird.desktop
    test -f /usr/local/share/applications/gnome-initial-setup.desktop
    test -f /etc/skel/.config/gnome-initial-setup-done
    test ! -e /usr/share/applications/gnome-ubuntu-panel.desktop
    test ! -e /etc/xdg/autostart/update-notifier.desktop
    test ! -e /etc/xdg/autostart/ubuntu-advantage-notification.desktop
    test -L /etc/systemd/user/gnome-initial-setup-first-login.service
    test -L /etc/systemd/user/update-notifier-release.path
    grep -q "^Prompt=never$" /etc/update-manager/release-upgrades
    test -f /etc/xdg/autostart/ubuntu-nido-ime-sync.desktop
    grep -q "vielotus-vni" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    grep -q "accent-color='\''teal'\''" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    grep -q "dock-position='\''BOTTOM'\''" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    test -x /usr/local/bin/himalaya
    dpkg -s geary >/dev/null 2>&1
    test -f /usr/share/plymouth/themes/spinner/bgrt-fallback.png
    test -x /usr/bin/nidoai
    test -x /usr/bin/nidoagent
    test -x /usr/bin/nido-hub
    test -x /usr/bin/nidooffice
    test -x /usr/bin/nidooffice-desktop
    test -x /usr/bin/pkcs11-helper
    /usr/bin/pkcs11-helper --self-test >/dev/null
    test -x /usr/libexec/nido-hub/nido-pkg-helper
    test -f /usr/lib/nido-hub/ai_discovery.py
    test -f /usr/lib/nido-hub/agent_accounts.py
    test -f /usr/lib/nido-hub/app_store.py
    test -f /usr/lib/nido-hub/markdown_render.py
    test -f /usr/lib/systemd/system/nido-native-first-cleanup.service
    /usr/bin/nido-hub --self-test >/dev/null
    test -x /usr/local/bin/genoffice
    test -x /opt/NidoOffice/genoffice
    test -f /usr/share/applications/nidooffice.desktop
    grep -q "^StartupWMClass=nidooffice$" /usr/share/applications/nidooffice.desktop
    grep -q "{nidooffice,genoffice}" /etc/apparmor.d/nidooffice
    grep -q -- "--enable-wayland-ime" /usr/bin/nidooffice-desktop
    dpkg -s nido-desktop >/dev/null 2>&1
    dpkg -s nidooffice-desktop >/dev/null 2>&1
    dpkg -s pcscd >/dev/null 2>&1
    dpkg -s libccid >/dev/null 2>&1
    dpkg -s opensc >/dev/null 2>&1
    dpkg -s opensc-pkcs11 >/dev/null 2>&1
    dpkg -s libnss3-tools >/dev/null 2>&1
    dpkg -s libsecret-tools >/dev/null 2>&1
    dpkg -s ubuntu-desktop-minimal >/dev/null 2>&1
    dpkg -s unattended-upgrades >/dev/null 2>&1
    dpkg -s fwupd >/dev/null 2>&1
    dpkg -s flatpak >/dev/null 2>&1
    dpkg -s firefox >/dev/null 2>&1
    dpkg -s vielotus >/dev/null 2>&1
    dpkg -s nidobox >/dev/null 2>&1
    dpkg --compare-versions "$(dpkg-query -W -f="\${Version}" nidobox)" ge "1.4.7"
    dpkg -s showtime >/dev/null 2>&1 || dpkg -s totem >/dev/null 2>&1
    ! dpkg -s fcitx5 >/dev/null 2>&1
    ! dpkg -s libreoffice-core >/dev/null 2>&1
    ! dpkg -s rhythmbox >/dev/null 2>&1
    ! dpkg -s transmission-gtk >/dev/null 2>&1
    ! dpkg -s shotwell >/dev/null 2>&1
    test -x /usr/bin/nidobox
    test -x /usr/lib/nidobox/nidobox-engine
    test -f /usr/share/plymouth/themes/nidoos/nidoos.plymouth
    test -f /usr/share/pixmaps/nidoos-logo.png
    test -f /etc/dconf/db/gdm
    test -f /etc/apt/apt.conf.d/51nidoos-unattended-upgrades
    grep -q "^NAME=\"NidoOS\"\$" /etc/os-release
    grep -q "^LOGO=nidoos-logo\$" /etc/os-release
    apt-get clean
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    date -u +"%Y-%m-%dT%H:%M:%SZ" > /etc/ubuntu-nido-customized
  '

  umount_chroot
  ok "Customization verified and chroot unmounted."
}
