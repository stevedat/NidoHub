#!/usr/bin/env bash
# Shared idempotent runtime installer & updater for NidoOffice Desktop
# (used by .deb postinst, `nidooffice-desktop --update`, and ISO hook 0170).
# Resolves the latest release from GitHub Releases by default and verifies SHA256.
set -euo pipefail

NIDOOFFICE_REPO="${NIDOOFFICE_REPO:-stevedat/nidooffice-releases}"
NIDOOFFICE_VERSION="${NIDOOFFICE_VERSION:-latest}"
NIDOOFFICE_FALLBACK_VERSION="${NIDOOFFICE_FALLBACK_VERSION:-0.13.0}"
NIDOOFFICE_INSTALL_DIR="${NIDOOFFICE_INSTALL_DIR:-/opt/NidoOffice}"
FORCE=0
CHECK_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force)
      FORCE=1
      shift
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --version)
      if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
        echo "ERROR: --version requires a value (e.g. latest or 0.13.0)" >&2
        exit 2
      fi
      NIDOOFFICE_VERSION="$2"
      shift 2
      ;;
    --help|-h)
      cat <<'EOF'
Usage: install-runtime.sh [--check] [--force] [--version <latest|X.Y.Z>]
  --check            Check installed vs latest GitHub Release without modifying disk
  --force            Re-download and re-extract even if already on target version
  --version <ver>    Install a specific version (default: latest)
EOF
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

detect_arch() {
  if [ -n "${NIDOOFFICE_ARCH:-}" ]; then
    printf '%s\n' "$NIDOOFFICE_ARCH"
    return
  fi
  if command -v dpkg >/dev/null 2>&1; then
    dpkg --print-architecture
    return
  fi
  case "$(uname -m)" in
    x86_64|amd64) printf 'amd64\n' ;;
    aarch64|arm64) printf 'arm64\n' ;;
    *) uname -m ;;
  esac
}

is_valid_version() {
  printf '%s' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.+-]+)?$'
}

read_installed_version() {
  if [ -f "${NIDOOFFICE_INSTALL_DIR}/.installed-version" ]; then
    tr -d '[:space:]' < "${NIDOOFFICE_INSTALL_DIR}/.installed-version"
  fi
}

parse_yaml_version() {
  awk '/^[[:space:]]*version:[[:space:]]*/ {
    val = $0
    sub(/^[[:space:]]*version:[[:space:]]*/, "", val)
    gsub(/["'\''[:space:]]/, "", val)
    sub(/^v/, "", val)
    print val
    exit
  }'
}

resolve_latest_version() {
  local yml_name="$1"
  local effective_url=""
  local tag=""
  local ver=""

  # 1. Zero-rate-limit GitHub releases/latest redirect (skipped when custom base URL is set)
  if [ -z "${NIDOOFFICE_RELEASE_BASE_URL:-}" ]; then
    effective_url="$(curl -fsSLI --connect-timeout 10 --max-time 20 -o /dev/null -w '%{url_effective}' \
      "https://github.com/${NIDOOFFICE_REPO}/releases/latest" 2>/dev/null || true)"
    case "$effective_url" in
      */releases/tag/*)
        tag="${effective_url##*/releases/tag/}"
        tag="${tag%%\?*}"
        ver="${tag#v}"
        if is_valid_version "$ver"; then
          printf '%s\n' "$ver"
          return 0
        fi
        ;;
    esac
  fi

  # 2. Fallback: electron-builder latest-linux*.yml metadata
  local yml_base="${NIDOOFFICE_RELEASE_BASE_URL:-https://github.com/${NIDOOFFICE_REPO}/releases/latest/download}"
  yml_base="${yml_base%/}"
  ver="$(curl -fsSL --connect-timeout 10 --max-time 20 "${yml_base}/${yml_name}" 2>/dev/null | parse_yaml_version || true)"
  if is_valid_version "$ver"; then
    printf '%s\n' "$ver"
    return 0
  fi

  return 1
}

ARCH="$(detect_arch)"
case "$ARCH" in
  amd64)
    APPIMAGE_NAME="NidoOffice-linux-x64.AppImage"
    LATEST_YML="latest-linux.yml"
    ;;
  arm64)
    APPIMAGE_NAME="NidoOffice-linux-arm64.AppImage"
    LATEST_YML="latest-linux-arm64.yml"
    ;;
  *)
    echo "ERROR: Unsupported architecture for NidoOffice Desktop: ${ARCH}" >&2
    exit 1
    ;;
esac

INSTALLED_VERSION="$(read_installed_version)"
REQ_VERSION="${NIDOOFFICE_VERSION#v}"
RESOLVED_ONLINE=1

if [ "$REQ_VERSION" = "latest" ]; then
  if TARGET_VERSION="$(resolve_latest_version "$LATEST_YML")"; then
    RESOLVED_ONLINE=1
  else
    RESOLVED_ONLINE=0
    if [ -n "$INSTALLED_VERSION" ] && is_valid_version "$INSTALLED_VERSION"; then
      echo "[Ubuntu-Nido] WARN: Could not reach GitHub Releases; keeping installed NidoOffice v${INSTALLED_VERSION}." >&2
      TARGET_VERSION="$INSTALLED_VERSION"
    else
      echo "[Ubuntu-Nido] WARN: Could not resolve latest NidoOffice release; falling back to v${NIDOOFFICE_FALLBACK_VERSION}." >&2
      TARGET_VERSION="${NIDOOFFICE_FALLBACK_VERSION#v}"
    fi
  fi
else
  TARGET_VERSION="$REQ_VERSION"
fi

if ! is_valid_version "$TARGET_VERSION"; then
  echo "ERROR: Invalid NidoOffice version string: ${TARGET_VERSION}" >&2
  exit 1
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  UPDATE_AVAIL="true"
  if [ -n "$INSTALLED_VERSION" ] && [ "$INSTALLED_VERSION" = "$TARGET_VERSION" ]; then
    UPDATE_AVAIL="false"
  fi
  printf 'installed=%s\nlatest=%s\narch=%s\nresolved_online=%s\nupdate_available=%s\n' \
    "${INSTALLED_VERSION:-none}" "$TARGET_VERSION" "$ARCH" "$RESOLVED_ONLINE" "$UPDATE_AVAIL"
  exit 0
fi

HAVE_RUNTIME=0
if [ -n "$INSTALLED_VERSION" ] && \
   [ "$INSTALLED_VERSION" = "$TARGET_VERSION" ] && \
   { [ -x "${NIDOOFFICE_INSTALL_DIR}/nidooffice" ] || [ -x "${NIDOOFFICE_INSTALL_DIR}/genoffice" ]; }; then
  HAVE_RUNTIME=1
fi

if [ "$FORCE" -eq 1 ] || [ "$HAVE_RUNTIME" -eq 0 ]; then
  if [ -n "${NIDOOFFICE_RELEASE_BASE_URL:-}" ]; then
    BASE_URL="${NIDOOFFICE_RELEASE_BASE_URL%/}"
  else
    BASE_URL="https://github.com/${NIDOOFFICE_REPO}/releases/download/v${TARGET_VERSION}"
  fi

  TMP_PARENT="/var/tmp"
  [ -d "$TMP_PARENT" ] || TMP_PARENT="${TMPDIR:-/tmp}"
  WORK_TMP="$(mktemp -d "${TMP_PARENT}/nidooffice-install.XXXXXX")"
  cleanup_tmp() {
    rm -rf "$WORK_TMP"
  }
  trap cleanup_tmp EXIT

  DL_DIR="${WORK_TMP}/dl"
  EXTRACT_DIR="${WORK_TMP}/extract"
  mkdir -p "$DL_DIR" "$EXTRACT_DIR"

  echo "[Ubuntu-Nido] Downloading NidoOffice Desktop v${TARGET_VERSION} metadata (${BASE_URL})..."
  curl -fsSL --retry 3 --retry-delay 2 "${BASE_URL}/SHA256SUMS.txt" -o "${DL_DIR}/SHA256SUMS.txt"

  # Prefer evergreen asset name, fall back to versioned AppImage asset if listed in SHA256SUMS.txt
  SELECTED_ASSET="$APPIMAGE_NAME"
  if ! grep -Eq "^[0-9a-fA-F]{64}[[:space:]]+\*?${SELECTED_ASSET}\$" "${DL_DIR}/SHA256SUMS.txt"; then
    if [ "$ARCH" = "amd64" ]; then
      ALT_ASSET="NidoOffice-${TARGET_VERSION}.AppImage"
    else
      ALT_ASSET="NidoOffice-${TARGET_VERSION}-arm64.AppImage"
    fi
    if grep -Eq "^[0-9a-fA-F]{64}[[:space:]]+\*?${ALT_ASSET}\$" "${DL_DIR}/SHA256SUMS.txt"; then
      SELECTED_ASSET="$ALT_ASSET"
    else
      echo "ERROR: Neither ${APPIMAGE_NAME} nor ${ALT_ASSET} found in SHA256SUMS.txt for v${TARGET_VERSION}" >&2
      exit 1
    fi
  fi

  echo "[Ubuntu-Nido] Downloading NidoOffice Desktop v${TARGET_VERSION} (${SELECTED_ASSET})..."
  curl -fsSL --retry 3 --retry-delay 2 "${BASE_URL}/${SELECTED_ASSET}" -o "${DL_DIR}/${SELECTED_ASSET}"
  (
    cd "$DL_DIR"
    grep -E "^[0-9a-fA-F]{64}[[:space:]]+\*?${SELECTED_ASSET}\$" SHA256SUMS.txt | sha256sum -c -
  )

  chmod +x "${DL_DIR}/${SELECTED_ASSET}"
  echo "[Ubuntu-Nido] Extracting ${SELECTED_ASSET} into ${NIDOOFFICE_INSTALL_DIR}..."
  SQFS_OFFSET="$(python3 - "${DL_DIR}/${SELECTED_ASSET}" <<'PY'
import sys
data = open(sys.argv[1], "rb").read(4 * 1024 * 1024)
print(data.rfind(b"hsqs"))
PY
)"
  if [ "$SQFS_OFFSET" -gt 0 ] 2>/dev/null && command -v unsquashfs >/dev/null 2>&1; then
    unsquashfs -o "$SQFS_OFFSET" -d "${EXTRACT_DIR}/squashfs-root" "${DL_DIR}/${SELECTED_ASSET}" >/dev/null
  fi
  if [ ! -d "${EXTRACT_DIR}/squashfs-root" ]; then
    (cd "$EXTRACT_DIR" && "${DL_DIR}/${SELECTED_ASSET}" --appimage-extract >/dev/null 2>&1)
  fi
  test -d "${EXTRACT_DIR}/squashfs-root"

  mkdir -p "$(dirname "$NIDOOFFICE_INSTALL_DIR")"
  rm -rf "$NIDOOFFICE_INSTALL_DIR" /opt/GenOffice
  mv "${EXTRACT_DIR}/squashfs-root" "$NIDOOFFICE_INSTALL_DIR"
  if [ "$NIDOOFFICE_INSTALL_DIR" = "/opt/NidoOffice" ]; then
    ln -sfn /opt/NidoOffice /opt/GenOffice
  fi
  printf '%s\n' "$TARGET_VERSION" > "${NIDOOFFICE_INSTALL_DIR}/.installed-version"
fi

chmod -R a+rX "$NIDOOFFICE_INSTALL_DIR"
if [ ! -x "${NIDOOFFICE_INSTALL_DIR}/genoffice" ] && [ -x "${NIDOOFFICE_INSTALL_DIR}/nidooffice" ]; then
  ln -sf nidooffice "${NIDOOFFICE_INSTALL_DIR}/genoffice"
fi
if [ -x "${NIDOOFFICE_INSTALL_DIR}/nidooffice" ]; then
  NIDOOFFICE_BIN="${NIDOOFFICE_INSTALL_DIR}/nidooffice"
else
  NIDOOFFICE_BIN="${NIDOOFFICE_INSTALL_DIR}/genoffice"
fi
test -x "$NIDOOFFICE_BIN"

if [ -f "${NIDOOFFICE_INSTALL_DIR}/chrome-sandbox" ]; then
  chown root:root "${NIDOOFFICE_INSTALL_DIR}/chrome-sandbox"
  chmod 4755 "${NIDOOFFICE_INSTALL_DIR}/chrome-sandbox"
fi

if [ -x "${NIDOOFFICE_INSTALL_DIR}/resources/cli/nidooffice" ] && [ ! -e "${NIDOOFFICE_INSTALL_DIR}/resources/cli/genoffice" ]; then
  ln -sf nidooffice "${NIDOOFFICE_INSTALL_DIR}/resources/cli/genoffice"
fi
if [ -x "${NIDOOFFICE_INSTALL_DIR}/resources/cli/genoffice" ]; then
  mkdir -p /usr/local/bin
  ln -sfn "${NIDOOFFICE_INSTALL_DIR}/resources/cli/genoffice" /usr/local/bin/genoffice
fi

if [ -d "${NIDOOFFICE_INSTALL_DIR}/usr/share/icons/hicolor" ]; then
  mkdir -p /usr/share/icons/hicolor
  cp -a "${NIDOOFFICE_INSTALL_DIR}/usr/share/icons/hicolor/"* /usr/share/icons/hicolor/
  while IFS= read -r -d '' app_icon_dir; do
    if [ -f "${app_icon_dir}/genoffice.png" ] && [ ! -e "${app_icon_dir}/nidooffice.png" ]; then
      cp -f "${app_icon_dir}/genoffice.png" "${app_icon_dir}/nidooffice.png"
    elif [ -f "${app_icon_dir}/nidooffice.png" ] && [ ! -e "${app_icon_dir}/genoffice.png" ]; then
      cp -f "${app_icon_dir}/nidooffice.png" "${app_icon_dir}/genoffice.png"
    fi
  done < <(find /usr/share/icons/hicolor -type d -name apps -print0 2>/dev/null)
fi
if [ ! -f /usr/share/icons/hicolor/256x256/apps/nidooffice.png ] && [ -f "${NIDOOFFICE_INSTALL_DIR}/.DirIcon" ]; then
  mkdir -p /usr/share/icons/hicolor/256x256/apps
  cp -Lf "${NIDOOFFICE_INSTALL_DIR}/.DirIcon" /usr/share/icons/hicolor/256x256/apps/nidooffice.png
  cp -Lf "${NIDOOFFICE_INSTALL_DIR}/.DirIcon" /usr/share/icons/hicolor/256x256/apps/genoffice.png
fi

if [ -d "${NIDOOFFICE_INSTALL_DIR}/resources/shell-new" ]; then
  mkdir -p /usr/share/templates /etc/skel/Templates
  for pair in \
    "docx:Tài liệu Word.docx" \
    "xlsx:Bảng tính Excel.xlsx" \
    "pptx:Trình chiếu PowerPoint.pptx"; do
    ext="${pair%%:*}"
    tname="${pair#*:}"
    if [ -f "${NIDOOFFICE_INSTALL_DIR}/resources/shell-new/blank.${ext}" ]; then
      install -m 0644 "${NIDOOFFICE_INSTALL_DIR}/resources/shell-new/blank.${ext}" "/usr/share/templates/${tname}"
      install -m 0644 "${NIDOOFFICE_INSTALL_DIR}/resources/shell-new/blank.${ext}" "/etc/skel/Templates/${tname}"
    fi
  done
fi

mkdir -p /etc/xdg /etc/skel/.config
python3 - <<'PY'
from pathlib import Path

OFFICE_MIMES = [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "application/vnd.ms-word.document.macroEnabled.12",
    "application/msword",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.text-template",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "application/vnd.ms-excel",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.spreadsheet-template",
    "text/csv",
    "text/tab-separated-values",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.presentationml.template",
    "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.presentation-template",
    "application/pdf",
    "text/markdown",
    "text/x-markdown",
    "application/hwp+zip",
    "application/x-hwp",
]

def update_mimeapps(path: Path, scrub_only: bool = False) -> None:
    sections: dict[str, dict[str, str]] = {}
    order: list[str] = []
    cur = None
    if path.is_file():
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("[") and s.endswith("]"):
                cur = s
                if cur not in sections:
                    sections[cur] = {}
                    order.append(cur)
                continue
            if cur and "=" in s:
                k, v = s.split("=", 1)
                orig_vals = [x.strip() for x in v.split(";") if x.strip()]
                vals = [x for x in orig_vals if not x.startswith("libreoffice-")]
                if not vals and any(
                    x.startswith(("libreoffice-writer", "libreoffice-calc", "libreoffice-impress"))
                    for x in orig_vals
                ):
                    vals = ["nidooffice.desktop"]
                if vals:
                    sections[cur][k.strip()] = ";".join(vals) + (";" if v.rstrip().endswith(";") else "")
    if not scrub_only:
        sec = "[Default Applications]"
        if sec not in sections:
            sections[sec] = {}
            order.insert(0, sec)
        for m in OFFICE_MIMES:
            sections[sec][m] = "nidooffice.desktop"
    out = []
    for sec in order:
        if not sections[sec]:
            continue
        out.append(sec)
        for k, v in sections[sec].items():
            out.append(f"{k}={v}")
        out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")

for mf in (
    Path("/etc/xdg/mimeapps.list"),
    Path("/etc/xdg/gnome-mimeapps.list"),
    Path("/etc/xdg/ubuntu-mimeapps.list"),
    Path("/etc/skel/.config/mimeapps.list"),
):
    update_mimeapps(mf, scrub_only=False)

for sys_mf in (
    Path("/usr/share/applications/ubuntu-mimeapps.list"),
    Path("/usr/share/applications/gnome-mimeapps.list"),
):
    if sys_mf.is_file():
        update_mimeapps(sys_mf, scrub_only=True)
PY

if [ -d /sys/kernel/security/apparmor ] && [ -f /etc/apparmor.d/nidooffice ] && command -v apparmor_parser >/dev/null 2>&1; then
  apparmor_parser -r /etc/apparmor.d/nidooffice >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database -q /usr/share/applications || true
fi

echo "[Ubuntu-Nido] NidoOffice Desktop v${TARGET_VERSION} ready at ${NIDOOFFICE_INSTALL_DIR}."
