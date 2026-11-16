#!/usr/bin/env bash
# Apply config/includes.chroot overlay safely into squashfs rootfs

step_overlay() {
  ensure_work_tree
  local overlay_dir="$SCRIPT_DIR/config/includes.chroot"

  if [ ! -d "$overlay_dir" ]; then
    warn "Overlay directory missing: $overlay_dir"
    return 0
  fi

  info "Applying rootfs overlay..."
  local through=()
  local rel
  while IFS= read -r -d '' rel; do
    [ -L "$WORK_DIR/squashfs/$rel" ] && through+=("$rel")
  done < <(cd "$overlay_dir" && find . -type f -printf '%P\0')

  local excludes=()
  for rel in "${through[@]}"; do
    excludes+=(--exclude="$rel")
  done

  rsync -a --chown=root:root --chmod=go-w "${excludes[@]}" "$overlay_dir/" "$WORK_DIR/squashfs/"

  for rel in "${through[@]}"; do
    chroot "$WORK_DIR/squashfs" /bin/sh -c 'cat > "$1" && chown 0:0 "$1" && chmod go-w "$1"' \
      sh "/$rel" < "$overlay_dir/$rel"
    info "  → Wrote through symlink /$rel"
  done

  if [ -d "$WORK_DIR/squashfs/etc/dconf/db/local.d" ]; then
    chroot "$WORK_DIR/squashfs" /bin/bash -c 'dconf compile /etc/dconf/db/local /etc/dconf/db/local.d || dconf update || true'
  fi
  if [ -d "$WORK_DIR/squashfs/usr/share/glib-2.0/schemas" ]; then
    chroot "$WORK_DIR/squashfs" /bin/bash -c 'glib-compile-schemas /usr/share/glib-2.0/schemas/ || true'
  fi

  ok "Overlay applied."
}
