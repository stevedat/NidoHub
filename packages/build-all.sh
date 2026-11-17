#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
STAGE_DIR="${SCRIPT_DIR}/nidoai-gateway/stage"
NIDOAI_SRC_DIR="${NIDOAI_SRC_DIR:-}"
VIELOTUS_BIN_DIR="${VIELOTUS_BIN_DIR:-}"
NIDOBOX_BIN_DIR="${NIDOBOX_BIN_DIR:-}"
DEB_BUILDER="${ROOT_DIR}/scripts/build_deb_portable.py"

rm -rf "$DIST_DIR" "$STAGE_DIR"
mkdir -p "$DIST_DIR" "${STAGE_DIR}/usr/lib/nidoai"

if [[ -n "$NIDOAI_SRC_DIR" && -f "${NIDOAI_SRC_DIR}/src/cli.ts" ]]; then
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
  cp "${SCRIPT_DIR}/nidoai-gateway/src/cli.ts" "${STAGE_DIR}/usr/lib/nidoai/src/cli.ts"
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
  "${SCRIPT_DIR}/nido-hub/files/usr/bin/nido-hub" \
  "${SCRIPT_DIR}/nido-hub/files/usr/lib/nido-hub/app.py" \
  "${SCRIPT_DIR}/nidooffice-cli/files/usr/bin/nidooffice" \
  "${SCRIPT_DIR}/nidooffice-cli/files/usr/lib/nidooffice-cli/engine.py"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidoai-gateway" \
  --extra-files "$STAGE_DIR" \
  --output "${DIST_DIR}/nidoai-gateway_0.1.0_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidoagent-cli" \
  --output "${DIST_DIR}/nidoagent-cli_0.1.0_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-desktop-settings" \
  --output "${DIST_DIR}/nido-desktop-settings_0.1.0_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-hub" \
  --output "${DIST_DIR}/nido-hub_0.1.0_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidooffice-cli" \
  --output "${DIST_DIR}/nidooffice-cli_0.1.0_all.deb"

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
