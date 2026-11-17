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
    test -x /usr/bin/nido-hub
    test -x /usr/bin/nidooffice
    dpkg -s vielotus >/dev/null 2>&1
    test -f /usr/share/plymouth/themes/ubuntu-nido/ubuntu-nido.plymouth
    apt-get clean
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*
    date -u +"%Y-%m-%dT%H:%M:%SZ" > /etc/ubuntu-nido-customized
  '

  umount_chroot
  ok "Customization verified and chroot unmounted."
}
