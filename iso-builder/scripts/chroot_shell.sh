#!/usr/bin/env bash
# Open an interactive chroot shell inside build/squashfs

step_chroot_shell() {
  ensure_work_tree
  info "Mounting chroot..."
  mount_chroot

  info "Entering Ubuntu-Nido chroot. Type 'exit' to leave."
  chroot "$WORK_DIR/squashfs" /bin/bash || true

  info "Unmounting chroot..."
  umount_chroot
  ok "Exited chroot."
}
