#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
STAGE_DIR="${SCRIPT_DIR}/nidoai-gateway/stage"
NIDOHUB_STAGE_DIR="${SCRIPT_DIR}/nido-hub/stage"
NIDOAI_SRC_DIR="${NIDOAI_SRC_DIR:-}"
VIELOTUS_BIN_DIR="${VIELOTUS_BIN_DIR:-}"
NIDOBOX_BIN_DIR="${NIDOBOX_BIN_DIR:-}"
DEB_VERSION_SUFFIX="${DEB_VERSION_SUFFIX:-}"
DEB_BUILDER="${ROOT_DIR}/scripts/build_deb_portable.py"

rm -rf "$DIST_DIR" "$STAGE_DIR" "$NIDOHUB_STAGE_DIR"
mkdir -p "$DIST_DIR" "${STAGE_DIR}/usr/lib/nidoai" "${NIDOHUB_STAGE_DIR}/usr/share/icons/hicolor"
python3 "${ROOT_DIR}/branding/generate-assets.py" --hub-icon-dir "${NIDOHUB_STAGE_DIR}/usr/share/icons/hicolor"

if [[ -n "$NIDOAI_SRC_DIR" && ( -f "${NIDOAI_SRC_DIR}/src/cli.mjs" || -f "${NIDOAI_SRC_DIR}/src/cli.ts" ) ]]; then
  echo "==> Staging external NidoAI source from ${NIDOAI_SRC_DIR} (strict secret exclusion)"
  cp "${NIDOAI_SRC_DIR}/package.json" "${STAGE_DIR}/usr/lib/nidoai/package.json"
  [[ -f "${NIDOAI_SRC_DIR}/README.md" ]] && cp "${NIDOAI_SRC_DIR}/README.md" "${STAGE_DIR}/usr/lib/nidoai/README.md"
  if [[ -f "${NIDOAI_SRC_DIR}/.env.example" ]]; then
    sed 's|^NIDOAI_CODEX_BIN=.*|NIDOAI_CODEX_BIN=/usr/local/bin/codex|' \
      "${NIDOAI_SRC_DIR}/.env.example" > "${STAGE_DIR}/usr/lib/nidoai/.env.example"
  fi
  cp -R "${NIDOAI_SRC_DIR}/src" "${STAGE_DIR}/usr/lib/nidoai/src"
else
  echo "==> Staging built-in open-source NidoAI Gateway (packages/nidoai-gateway/src)"
  mkdir -p "${STAGE_DIR}/usr/lib/nidoai/src"
  cp "${SCRIPT_DIR}/nidoai-gateway/src/cli.mjs" "${STAGE_DIR}/usr/lib/nidoai/src/cli.mjs"
  cat > "${STAGE_DIR}/usr/lib/nidoai/package.json" <<'PKG'
{
  "name": "nidoai-gateway",
  "version": "0.1.0",
  "type": "module"
}
PKG
fi

# Strip OS junk and assert zero secret env files exist anywhere in stage
find "$STAGE_DIR" -name '.DS_Store' -delete
LEAKED="$(find "$STAGE_DIR" \( -name '.env' -o -name '.env.local' -o -name '.env.*.local' \) -print)"
if [[ -n "$LEAKED" ]]; then
  echo "ERROR: Secret env file detected in staging directory: $LEAKED" >&2
  exit 1
fi

# Ensure executable permissions on package scripts before packaging
chmod 755 \
  "${SCRIPT_DIR}/nidoai-gateway/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nidoai-gateway/DEBIAN/prerm" \
  "${SCRIPT_DIR}/nidoai-gateway/files/usr/bin/nidoai" \
  "${SCRIPT_DIR}/nidoagent-cli/files/usr/bin/nidoagent" \
  "${SCRIPT_DIR}/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs" \
  "${SCRIPT_DIR}/nido-desktop-settings/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/local/bin/nido-welcome" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/local/bin/nidoagent-quicklaunch" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giao-cho-NidoAgent" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Gui-qua-NidoDrop-LAN" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giai-nen-Chuan-Tieng-Viet" \
  "${SCRIPT_DIR}/nido-hub/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nido-hub/files/usr/bin/nido-hub" \
  "${SCRIPT_DIR}/nido-hub/files/usr/lib/nido-hub/app.py" \
  "${SCRIPT_DIR}/nidooffice-cli/files/usr/bin/nidooffice" \
  "${SCRIPT_DIR}/nidooffice-cli/files/usr/lib/nidooffice-cli/engine.py" \
  "${SCRIPT_DIR}/nido-desktop/DEBIAN/postinst"

VER_ARGS=()
if [[ -n "$DEB_VERSION_SUFFIX" ]]; then
  VER_ARGS=(--version-suffix "$DEB_VERSION_SUFFIX")
  echo "==> Building dev packages with version suffix: ${DEB_VERSION_SUFFIX}"
fi

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidoai-gateway" \
  --extra-files "$STAGE_DIR" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidoai-gateway_0.1.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidoagent-cli" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidoagent-cli_0.1.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-desktop-settings" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nido-desktop-settings_0.1.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-hub" \
  --extra-files "$NIDOHUB_STAGE_DIR" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nido-hub_0.1.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidooffice-cli" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidooffice-cli_0.1.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-desktop" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nido-desktop_0.1.0${DEB_VERSION_SUFFIX}_all.deb"

if [[ -n "$VIELOTUS_BIN_DIR" && -d "$VIELOTUS_BIN_DIR" ]]; then
  mkdir -p "${SCRIPT_DIR}/vendor"
  for deb in "${VIELOTUS_BIN_DIR}"/vielotus_*.deb; do
    if [[ -f "$deb" ]]; then
      cp "$deb" "${SCRIPT_DIR}/vendor/"
      echo "==> Staged vendor package: $(basename "$deb")"
    fi
  done
fi

if [[ -n "$NIDOBOX_BIN_DIR" && -d "$NIDOBOX_BIN_DIR" ]]; then
  mkdir -p "${SCRIPT_DIR}/vendor"
  for deb in "${NIDOBOX_BIN_DIR}"/nidobox_*.deb; do
    if [[ -f "$deb" ]]; then
      cp "$deb" "${SCRIPT_DIR}/vendor/"
      echo "==> Staged vendor package: $(basename "$deb")"
    fi
  done
fi

echo "==> Built packages in ${DIST_DIR}:"
ls -lh "$DIST_DIR"
