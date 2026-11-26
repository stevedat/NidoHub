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
  info "  → Sanitizing chroot APT sources (stashing cdrom.sources & enabling universe)..."
  # Ubuntu 26.04+ embeds the per-ISO ephemeral Ed25519 signing key inline inside
  # /etc/apt/sources.list.d/cdrom.sources. Stash it on the host while chroot
  # apt-get runs (when /cdrom is unmounted) and restore it before repacking so
  # Subiquity can verify file:/cdrom without NO_PUBKEY failures.
  local cdrom_stash="$WORK_DIR/stashed-cdrom-sources"
  mkdir -p "$cdrom_stash"
  local cdrom_src
  shopt -s nullglob
  for cdrom_src in "$WORK_DIR/squashfs/etc/apt/sources.list.d"/*cdrom*; do
    if [ -f "$cdrom_src" ]; then
      cp -a "$cdrom_src" "$cdrom_stash/$(basename "$cdrom_src")"
      rm -f "$cdrom_src"
      info "  → Stashed ISO pool APT source for Subiquity: $(basename "$cdrom_src")"
    fi
  done
  shopt -u nullglob

  chroot "$WORK_DIR/squashfs" python3 - <<'PY'
import glob
from pathlib import Path

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

restore_and_verify_cdrom_sources() {
  local cdrom_stash="$WORK_DIR/stashed-cdrom-sources"
  if [ -d "$cdrom_stash" ]; then
    local stashed_src
    shopt -s nullglob
    for stashed_src in "$cdrom_stash"/*; do
      if [ -f "$stashed_src" ]; then
        cp -a "$stashed_src" "$WORK_DIR/squashfs/etc/apt/sources.list.d/$(basename "$stashed_src")"
        chmod 0644 "$WORK_DIR/squashfs/etc/apt/sources.list.d/$(basename "$stashed_src")"
        info "  → Restored ISO pool APT source: $(basename "$stashed_src")"
      fi
    done
    shopt -u nullglob
  fi

  if [ -d "$WORK_DIR/custom/dists" ] && [ -f "$SCRIPT_DIR/scripts/verify_cdrom_signature.py" ]; then
    info "  → Verifying ISO dists/*/Release.gpg against rootfs APT keyrings..."
    python3 "$SCRIPT_DIR/scripts/verify_cdrom_signature.py" \
      --iso-tree "$WORK_DIR/custom" \
      --rootfs "$WORK_DIR/squashfs"
  fi

  local has_cdrom_sources=0
  local src_candidate
  shopt -s nullglob
  for src_candidate in "$WORK_DIR/squashfs/etc/apt/sources.list.d"/*cdrom*; do
    if [ -f "$src_candidate" ]; then
      has_cdrom_sources=1
      break
    fi
  done
  shopt -u nullglob

  if [ -d "$WORK_DIR/custom/dists" ] && [ "$has_cdrom_sources" -eq 1 ]; then
    info "  → Verifying offline chroot apt-get update against file:/cdrom..."
    local cdrom_mount="$WORK_DIR/squashfs/cdrom"
    local cdrom_existed=0
    [ -d "$cdrom_mount" ] && cdrom_existed=1
    mkdir -p "$cdrom_mount"
    mount --bind "$WORK_DIR/custom" "$cdrom_mount"
    mount -o remount,ro,bind "$cdrom_mount" 2>/dev/null || true

    local apt_rc=0
    local apt_out=""
    set +e
    apt_out="$(HOME=/root chroot "$WORK_DIR/squashfs" /bin/bash -c '
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      TMP_APT="$(mktemp -d /tmp/nido-cdrom-apt.XXXXXX)"
      trap '\''rm -rf "${TMP_APT:-}"'\'' EXIT
      mkdir -p "$TMP_APT/sources.list.d" "$TMP_APT/lists/partial"
      cp -a /etc/apt/sources.list.d/*cdrom* "$TMP_APT/sources.list.d/"
      chmod 0755 "$TMP_APT" "$TMP_APT/sources.list.d" "$TMP_APT/lists" "$TMP_APT/lists/partial"
      chown _apt:root "$TMP_APT/lists/partial" 2>/dev/null || true
      apt-get \
        -o APT::Update::Error-Mode=any \
        -o Dir::Etc::sourcelist=/dev/null \
        -o Dir::Etc::sourceparts="$TMP_APT/sources.list.d" \
        -o Dir::State::Lists="$TMP_APT/lists" \
        -o Dir::Cache::pkgcache="$TMP_APT/pkgcache.bin" \
        -o Dir::Cache::srcpkgcache="$TMP_APT/srcpkgcache.bin" \
        update 2>&1
    ')"
    apt_rc=$?
    set -e

    umount "$cdrom_mount" 2>/dev/null || umount -lf "$cdrom_mount" 2>/dev/null || true
    if [ "$cdrom_existed" -eq 0 ]; then
      rmdir "$cdrom_mount" 2>/dev/null || true
    fi

    if [ "$apt_rc" -ne 0 ]; then
      printf '%s\n' "$apt_out" >&2
      error "Offline chroot apt-get update against file:/cdrom failed (exit ${apt_rc})."
    fi
    if printf '%s\n' "$apt_out" | grep -qiE "NO_PUBKEY|is not signed|OpenPGP signature verification failed|UnitMasked"; then
      printf '%s\n' "$apt_out" >&2
      error "Offline chroot apt-get update against file:/cdrom emitted signature/DBus errors."
    fi
    ok "Offline chroot apt-get update verified against file:/cdrom (0 signature/UnitMasked errors)."
  fi
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

  # Stage and install Ubuntu-Nido .deb packages (prefer Stage 2 native debs on resolute when present)
  local pkg_dist="$SCRIPT_DIR/../packages/dist"
  local pkg_native="$SCRIPT_DIR/../packages/dist/native"
  local pkg_vendor="$SCRIPT_DIR/../packages/vendor"
  if [ -x "$SCRIPT_DIR/../packages/build-all.sh" ]; then
    info "  → Building Ubuntu-Nido .deb packages..."
    bash "$SCRIPT_DIR/../packages/build-all.sh"
  fi

  mkdir -p "$WORK_DIR/squashfs/tmp/nido-debs" "$WORK_DIR/squashfs/tmp/nido-meta-debs"
  local deb_count=0
  local meta_count=0
  local native_used=0
  local deb
  for deb in "$pkg_dist"/*.deb; do
    if [ -f "$deb" ]; then
      local base pkg_name native_match=""
      base="$(basename "$deb")"
      pkg_name="${base%%_*}"
      if [ "$UBUNTU_CODENAME" = "resolute" ] && [ -d "$pkg_native" ]; then
        for cand in "$pkg_native/${pkg_name}_"*"_${TARGET_ARCH}.deb"; do
          if [ -f "$cand" ]; then
            native_match="$cand"
            break
          fi
        done
      fi
      local chosen="$deb"
      if [ -n "$native_match" ]; then
        chosen="$native_match"
        native_used=$((native_used + 1))
        info "  → Using Stage 2 native package: $(basename "$chosen")"
      fi
      case "$base" in
        nido-desktop_*.deb)
          cp "$chosen" "$WORK_DIR/squashfs/tmp/nido-meta-debs/"
          meta_count=$((meta_count + 1))
          ;;
        *)
          cp "$chosen" "$WORK_DIR/squashfs/tmp/nido-debs/"
          deb_count=$((deb_count + 1))
          ;;
      esac
    fi
  done
  if [ "$native_used" -gt 0 ]; then
    ok "Selected $native_used Stage 2 native (${TARGET_ARCH}) package(s) for ISO."
  fi
  if [ -d "$pkg_vendor" ]; then
    mkdir -p "$WORK_DIR/squashfs/tmp/nido-ime-debs"
    for deb in "$pkg_vendor"/*_"${TARGET_ARCH}".deb "$pkg_vendor"/*_all.deb; do
      if [ -f "$deb" ]; then
        case "$(basename "$deb")" in
          nido-ime-bridge_*.deb)
            # Installed by hook 0150 right after upstream `vielotus` is present
            cp "$deb" "$WORK_DIR/squashfs/tmp/nido-ime-debs/"
            ;;
          *)
            cp "$deb" "$WORK_DIR/squashfs/tmp/nido-debs/"
            deb_count=$((deb_count + 1))
            ;;
        esac
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
    fc-match -f "%{family}\n" "Ubuntu Mono" | grep -Eq "Noto Mono|Liberation Mono"
    fc-match -f "%{family}\n" "Ubuntu Mono:charset=1ec3" | grep -Eq "Noto Mono|Liberation Mono"
    fc-match -f "%{family}\n" "Ubuntu Mono:charset=1ecd" | grep -Eq "Noto Mono|Liberation Mono"
    fc-match -f "%{family}\n" "monospace:charset=1ec3" | grep -Eq "Noto Mono|Liberation Mono"
    ! fc-list : file | grep -q "/usr/share/fonts/truetype/ubuntu/"
    test -x /usr/libexec/ubuntu-nido/ensure-vietnamese-ime
    /usr/libexec/ubuntu-nido/ensure-vietnamese-ime --self-test >/dev/null
    test -x /usr/libexec/ubuntu-nido/bind-installer-fonts
    /usr/libexec/ubuntu-nido/bind-installer-fonts --self-test >/dev/null
    /usr/libexec/ubuntu-nido/bind-installer-fonts --verify-system >/dev/null
    python3 -c "import struct; from pathlib import Path
def check_vi(p):
    data=Path(p).read_bytes(); nt=struct.unpack(\">H\",data[4:6])[0]
    off=next(o for i in range(nt) for tag,_,o,_ in [struct.unpack(\">4sIII\",data[12+i*16:28+i*16])] if tag==b\"cmap\")
    ns=struct.unpack(\">H\",data[off+2:off+4])[0]; chars=set()
    for i in range(ns):
        _,_,so=struct.unpack(\">HHI\",data[off+4+i*8:off+12+i*8]); so2=off+so; fmt=struct.unpack(\">H\",data[so2:so2+2])[0]
        if fmt==4:
            sc=struct.unpack(\">H\",data[so2+6:so2+8])[0]//2
            for s,e in zip(struct.unpack(f\">{sc}H\",data[so2+16+sc*2:so2+16+sc*4]),struct.unpack(f\">{sc}H\",data[so2+14:so2+14+sc*2])):
                if s!=0xFFFF: chars.update(range(s,e+1))
        elif fmt==12:
            for g in range(struct.unpack(\">I\",data[so2+12:so2+16])[0]):
                s,e,_=struct.unpack(\">III\",data[so2+16+g*12:so2+28+g*12]); chars.update(range(s,e+1))
    missing=[hex(cp) for cp in (0x1ECD,0x1EC3,0x1EC7,0x1EA3,0x1EED,0x1EBF,0x1EEF,0x1EB5) if cp not in chars]
    assert not missing, f\"{p} missing {missing}\"
check_vi(\"/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf\")
check_vi(\"/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf\")"
    test -f /usr/lib/systemd/system/nido-installer-fonts.service
    test -L /etc/systemd/system/graphical.target.wants/nido-installer-fonts.service
    test -f /usr/share/desktop-provision/whitelabel.yaml
    grep -q "^app-name: \"NidoOS\"\$" /usr/share/desktop-provision/whitelabel.yaml
    test -f /usr/share/desktop-provision/images/nidoos-logo-dark.svg
    test -f /usr/share/desktop-provision/images/nidoos-logo-light.svg
    test -f /usr/share/desktop-provision/slides/1/slide_vi_VN.html
    test -f /usr/share/desktop-provision/slides/1/slide_vi.html
    test -f /usr/share/ubuntu-nido/skills/ubuntu-nido-email/SKILL.md
    test -f /usr/local/share/applications/thunderbird_thunderbird.desktop
    test -f /usr/local/share/applications/gnome-initial-setup.desktop
    test -f /usr/local/share/applications/org.gnome.Yelp.desktop
    test -f /etc/skel/.config/gnome-initial-setup-done
    test ! -e /usr/share/applications/gnome-ubuntu-panel.desktop
    test ! -e /etc/xdg/autostart/update-notifier.desktop
    test ! -e /etc/xdg/autostart/ubuntu-advantage-notification.desktop
    test ! -e /etc/xdg/autostart/localsearch-3.desktop
    test ! -e /etc/xdg/autostart/org.gnome.Evolution-alarm-notify.desktop
    test ! -e /etc/xdg/autostart/snap-userd-autostart.desktop
    test ! -e /etc/apt/apt.conf.d/20packagekit
    test ! -e /etc/apt/apt.conf.d/20packagekit.nido-disabled
    test ! -e /usr/share/dbus-1/system-services/org.freedesktop.PackageKit.service
    test -L /etc/systemd/system/packagekit.service
    test -L /etc/systemd/user/localsearch-3.service
    test -L /etc/systemd/user/gnome-initial-setup-first-login.service
    test -L /etc/systemd/user/update-notifier-release.path
    test -f /etc/systemd/zram-generator.conf
    test -f /usr/lib/systemd/zram-generator.conf.d/50-nidoos.conf
    test -f /usr/lib/systemd/system/earlyoom.service.d/10-nidoos.conf
    test -f /etc/udev/rules.d/60-nido-io-scheduler.rules
    test -f /etc/default/grub.d/60-nidoos.cfg
    dpkg -s earlyoom >/dev/null 2>&1
    dpkg -s systemd-zram-generator >/dev/null 2>&1
    grep -q "^Prompt=never$" /etc/update-manager/release-upgrades
    test -f /etc/xdg/autostart/ubuntu-nido-ime-sync.desktop
    grep -q "vielotus-vni" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    grep -q "accent-color='\''teal'\''" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    grep -q "dock-position='\''BOTTOM'\''" /etc/dconf/db/local.d/00-ubuntu-nido-settings
    test -x /usr/local/bin/himalaya
    dpkg -s geary >/dev/null 2>&1
    test -f /usr/share/plymouth/themes/spinner/bgrt-fallback.png
    test -x /usr/bin/nidoai
    test -x /usr/libexec/nidoai/gateway-daemon
    test -f /usr/lib/systemd/user/nidoai-gateway.socket
    test -L /etc/systemd/user/sockets.target.wants/nidoai-gateway.socket
    test ! -e /etc/systemd/user/default.target.wants/nidoai-gateway.service
    test -f /etc/apparmor.d/nidoai-gateway
    test -f /etc/apparmor.d/nido-pkg-helper
    test -f /etc/sysctl.d/90-nidoos-hardening.conf
    test -f /etc/sysctl.d/91-nidoos-memory.conf
    test -f /etc/ufw/applications.d/nidoos
    test -x /usr/libexec/ubuntu-nido/configure-firewall
    /usr/libexec/ubuntu-nido/configure-firewall --self-test >/dev/null
    test -x /usr/bin/nido-security-verify
    /usr/bin/nido-security-verify --self-test >/dev/null
    /usr/bin/nido-security-verify --verify >/dev/null
    test -x /usr/bin/nidoagent
    test -x /usr/bin/nido-hub
    if [ -x /usr/libexec/nidoai/nidoai-sea ]; then
      test ! -d /usr/lib/nidoai/src
      test ! -d /usr/lib/nidoagent-cli
      ls /usr/lib/nido-hub/_*_native*.so >/dev/null
      /usr/bin/nidoagent --version | grep -q "^nidoagent "
    fi
    test -x /usr/bin/nido-doctor
    /usr/bin/nido-doctor --self-test >/dev/null
    test -x /usr/lib/systemd/user-environment-generators/60-nido-vm-gsk
    /usr/lib/systemd/user-environment-generators/60-nido-vm-gsk --self-test >/dev/null
    test -x /usr/bin/nidooffice
    test -x /usr/bin/nidooffice-desktop
    test -x /usr/bin/pkcs11-helper
    /usr/bin/pkcs11-helper --self-test >/dev/null
    test -x /usr/libexec/nido-hub/nido-pkg-helper
    test -f /usr/lib/nido-hub/ai_discovery.py
    test -f /usr/lib/nido-hub/agent_accounts.py
    test -f /usr/lib/nido-hub/app_store.py
    test -f /usr/lib/nido-hub/markdown_render.py
    test -f /usr/lib/nido-hub/printer_probe.py
    test -f /usr/lib/nido-hub/self_test.py
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
    dpkg --compare-versions "$(dpkg-query -W -f="\${Version}" vielotus)" ge "2.0.11"
    dpkg -s nido-ime-bridge >/dev/null 2>&1
    test -x /usr/lib/ibus/nido-ibus-vielotus
    test -x /usr/bin/nido-vielotus-settings
    /usr/lib/ibus/nido-ibus-vielotus --self-test >/dev/null
    dpkg -s nidobox >/dev/null 2>&1
    dpkg --compare-versions "$(dpkg-query -W -f="\${Version}" nidobox)" ge "1.5.0"
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
    test -f /usr/share/ubuntu-nido/51nidoos-unattended-upgrades.conf
    ! grep -q -- "-updates" /usr/share/ubuntu-nido/51nidoos-unattended-upgrades.conf
    test ! -e /etc/apt/apt.conf.d/51nidoos-unattended-upgrades
    grep -q "^NAME=\"NidoOS\"\$" /etc/os-release
    grep -q "^LOGO=nidoos-logo\$" /etc/os-release
    apt-get clean
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    date -u +"%Y-%m-%dT%H:%M:%SZ" > /etc/ubuntu-nido-customized
  '

  restore_and_verify_cdrom_sources
  umount_chroot
  ok "Customization verified and chroot unmounted."
}
