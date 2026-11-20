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

  local brand_name="NidoOS"
  local brand_title="${brand_name} ${NIDO_VERSION} (${TARGET_ARCH})"
  local boot_splash="$SCRIPT_DIR/config/includes.chroot/usr/share/backgrounds/ubuntu-nido/splash.png"

  if [ -f "$boot_splash" ]; then
    mkdir -p "$iso_dir/boot/grub"
    cp "$boot_splash" "$iso_dir/boot/grub/splash.png"
  fi

  if [ -f "$iso_dir/.disk/info" ]; then
    # Ubuntu Desktop's ubuntu-desktop-bootstrap (ProductInfo) expects canonical
    # "<Name> <UbuntuVersion> LTS \"<Codename>\" - Release <Arch> (<YYYYMMDD>)" format.
    local release_codename=""
    release_codename="$(sed -n 's/.*"\([^"]*\)".*/\1/p' "$iso_dir/.disk/info" | head -n1)"
    if [ -z "$release_codename" ]; then
      case "${UBUNTU_CODENAME:-}" in
        resolute) release_codename="Resolute Raccoon" ;;
        noble)    release_codename="Noble Numbat" ;;
        plucky)   release_codename="Plucky Puffin" ;;
        oracular) release_codename="Oracular Oriole" ;;
        questing) release_codename="Questing Quokka" ;;
        *)        release_codename="${UBUNTU_CODENAME:-LTS}" ;;
      esac
    fi
    local build_stamp
    build_stamp="$(date -u +%Y%m%d)"
    printf 'NidoOS %s LTS "%s" - Release %s (%s)\n' \
      "${UBUNTU_VERSION:-26.04}" "$release_codename" "$TARGET_ARCH" "$build_stamp" > "$iso_dir/.disk/info"
    ok "  Updated .disk/info release label ($(cat "$iso_dir/.disk/info"))"
  fi

  local cfg
  while IFS= read -r -d '' cfg; do
    python3 - "$cfg" "$brand_name" "$brand_title" "$NIDO_VERSION" "${DEBUG_BOOT:-0}" <<'PY'
import re
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
brand_name, brand_title, nido_ver, debug_flag = sys.argv[2:6]
is_debug = debug_flag == "1"

raw = cfg_path.read_text(encoding="utf-8", errors="replace")
out_lines: list[str] = []

for line in raw.splitlines():
    stripped = line.lstrip()
    low = stripped.lower()

    if low.startswith(("menuentry ", "menuentry\t", "menu label ")):
        # Normalize idempotently so repeated runs never duplicate brand tokens
        norm = re.sub(
            rf"(?:{re.escape(brand_name)}|Ubuntu-Nido)(?:-Nido)*\s+{re.escape(nido_ver)}\s+\([^)]+\)",
            "Try or Install Ubuntu",
            line,
        )
        norm = re.sub(rf"(?:{re.escape(brand_name)}|Ubuntu-Nido)(?:-Nido)*", "Ubuntu", norm)
        norm = re.sub(r"Ubuntu(?:-Nido)+", "Ubuntu", norm)

        def _brand_repl(m: re.Match[str]) -> str:
            tok = m.group(0)
            if tok == "Try or Install Ubuntu":
                return brand_title
            if "safe graphics" in tok:
                return f"{brand_name} (safe graphics)"
            return brand_name

        line = re.sub(
            r"Try or Install Ubuntu|Ubuntu \(safe graphics\)|\bUbuntu\b",
            _brand_repl,
            norm,
        )
    elif low.startswith(("linux ", "linux\t", "linuxefi ", "linuxefi\t", "append ", "append\t")) and (
        "/casper/vmlinuz" in line or "boot=casper" in line
    ):
        if is_debug:
            indent = line[: len(line) - len(stripped)]
            cleaned = re.sub(r"(?<!\S)(?:quiet|splash)(?!\S)", "", stripped)
            cleaned = re.sub(r" {2,}", " ", cleaned).strip()
            line = f"{indent}{cleaned}"
        if not re.search(r"(?<!\S)locale=", line):
            if re.search(r"(?<!\S)---(?=\s|$)", line):
                line = re.sub(r"\s+---(?=\s|$)", " locale=vi_VN.UTF-8 ---", line, count=1)
            else:
                line = f"{line.rstrip()} locale=vi_VN.UTF-8"

    out_lines.append(line)

cfg_path.write_text("\n".join(out_lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
PY
    ok "  Updated boot config: $(basename "$cfg")"
  done < <(find "$iso_dir" \( -path "*/grub/*.cfg" -o -path "*/EFI/boot/*.cfg" -o -path "*/isolinux/*.cfg" -o -path "*/syslinux/*.cfg" \) -type f -print0 2>/dev/null)

  ok "[2.5/7] Boot config complete."
}
