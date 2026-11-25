#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
STAGE_DIR="${SCRIPT_DIR}/nidoai-gateway/stage"
NIDOHUB_STAGE_DIR="${SCRIPT_DIR}/nido-hub/stage"
NIDOAGENT_STAGE_DIR="${SCRIPT_DIR}/nidoagent-cli/stage"
NIDOOFFICE_CLI_STAGE_DIR="${SCRIPT_DIR}/nidooffice-cli/stage"
SETTINGS_STAGE_DIR="${SCRIPT_DIR}/nido-desktop-settings/stage"
NIDOAI_SRC_DIR="${NIDOAI_SRC_DIR:-}"
VIELOTUS_BIN_DIR="${VIELOTUS_BIN_DIR:-}"
NIDOBOX_BIN_DIR="${NIDOBOX_BIN_DIR:-}"
DEB_VERSION_SUFFIX="${DEB_VERSION_SUFFIX:-}"
NIDO_HARDEN_DIST="${NIDO_HARDEN_DIST:-1}"
APT_GPG_KEY="${APT_GPG_KEY:-AC980631CB8BA9D2602A0990528AF2083F61DC2F}"
DEB_BUILDER="${ROOT_DIR}/scripts/build_deb_portable.py"

rm -f "${DIST_DIR}"/*.deb
rm -rf "$STAGE_DIR" "$NIDOHUB_STAGE_DIR" "$NIDOAGENT_STAGE_DIR" "$NIDOOFFICE_CLI_STAGE_DIR" "$SETTINGS_STAGE_DIR"
mkdir -p "$DIST_DIR" "${STAGE_DIR}/usr/lib/nidoai" "${NIDOHUB_STAGE_DIR}/usr/share/icons/hicolor" \
  "${NIDOHUB_STAGE_DIR}/usr/lib/nido-hub" \
  "${NIDOAGENT_STAGE_DIR}/usr/lib/nidoagent-cli" \
  "${NIDOOFFICE_CLI_STAGE_DIR}/usr/lib/nidooffice-cli" \
  "${SETTINGS_STAGE_DIR}/usr/share/backgrounds/ubuntu-nido" \
  "${SETTINGS_STAGE_DIR}/usr/share/pixmaps" \
  "${SETTINGS_STAGE_DIR}/usr/share/icons/hicolor" \
  "${SETTINGS_STAGE_DIR}/usr/share/desktop-provision" \
  "${SETTINGS_STAGE_DIR}/usr/local/share/applications"
python3 "${ROOT_DIR}/branding/generate-assets.py"
python3 "${ROOT_DIR}/branding/generate-assets.py" --hub-icon-dir "${NIDOHUB_STAGE_DIR}/usr/share/icons/hicolor"
python3 "${ROOT_DIR}/branding/generate-assets.py" --desktop-provision-dir "${SETTINGS_STAGE_DIR}/usr/share/desktop-provision"
python3 "${ROOT_DIR}/branding/generate-assets.py" --desktop-icon-dir "${SETTINGS_STAGE_DIR}/usr/share/icons/hicolor"
cp "${ROOT_DIR}/iso-builder/config/includes.chroot/usr/share/backgrounds/ubuntu-nido/wallpaper.png" \
  "${SETTINGS_STAGE_DIR}/usr/share/backgrounds/ubuntu-nido/wallpaper.png"
cp "${ROOT_DIR}/iso-builder/config/includes.chroot/usr/share/backgrounds/ubuntu-nido/nidoos-logo.png" \
  "${SETTINGS_STAGE_DIR}/usr/share/pixmaps/nidoos-logo.png"

# Hide unwanted Snap/Ubuntu/duplicate clutter in /usr/local/share/applications
# (highest XDG priority across both Live ISO and installed NidoOS).
for hidden_desktop in \
  thunderbird_thunderbird.desktop \
  snap-store_snap-store.desktop \
  desktop-security-center_desktop-security-center.desktop \
  firefox_firefox.desktop \
  yelp.desktop \
  org.gnome.Yelp.desktop \
  software-properties-gtk.desktop \
  software-properties-drivers.desktop \
  software-properties-livepatch.desktop \
  update-manager.desktop \
  apport-gtk.desktop \
  gnome-initial-setup.desktop \
  ubuntu-report-on-upgrade.desktop \
  ibus-setup-Unikey.desktop \
  ibus-setup-hangul.desktop \
  org.freedesktop.IBus.Setup.desktop \
  im-config.desktop; do
  cat > "${SETTINGS_STAGE_DIR}/usr/local/share/applications/${hidden_desktop}" <<'HDESK'
[Desktop Entry]
Type=Application
Hidden=true
NoDisplay=true
HDESK
done

if [[ -n "$NIDOAI_SRC_DIR" && -f "${NIDOAI_SRC_DIR}/build/cli.mjs" ]]; then
  echo "==> Staging compiled external NidoAI build from ${NIDOAI_SRC_DIR}/build (strict secret exclusion)"
  cp "${NIDOAI_SRC_DIR}/package.json" "${STAGE_DIR}/usr/lib/nidoai/package.json"
  [[ -f "${NIDOAI_SRC_DIR}/README.md" ]] && cp "${NIDOAI_SRC_DIR}/README.md" "${STAGE_DIR}/usr/lib/nidoai/README.md"
  if [[ -f "${NIDOAI_SRC_DIR}/.env.example" ]]; then
    sed 's|^NIDOAI_CODEX_BIN=.*|NIDOAI_CODEX_BIN=/usr/local/bin/codex|' \
      "${NIDOAI_SRC_DIR}/.env.example" > "${STAGE_DIR}/usr/lib/nidoai/.env.example"
  fi
  cp -R "${NIDOAI_SRC_DIR}/build" "${STAGE_DIR}/usr/lib/nidoai/src"
elif [[ -n "$NIDOAI_SRC_DIR" && -f "${NIDOAI_SRC_DIR}/src/cli.mjs" ]]; then
  echo "==> Staging external pure-JS NidoAI source from ${NIDOAI_SRC_DIR}/src (strict secret exclusion)"
  cp "${NIDOAI_SRC_DIR}/package.json" "${STAGE_DIR}/usr/lib/nidoai/package.json"
  [[ -f "${NIDOAI_SRC_DIR}/README.md" ]] && cp "${NIDOAI_SRC_DIR}/README.md" "${STAGE_DIR}/usr/lib/nidoai/README.md"
  cp -R "${NIDOAI_SRC_DIR}/src" "${STAGE_DIR}/usr/lib/nidoai/src"
else
  echo "==> Staging unified pure-JS NidoAI Gateway (packages/nidoai-gateway/src)"
  mkdir -p "${STAGE_DIR}/usr/lib/nidoai/src"
  cp -R "${SCRIPT_DIR}/nidoai-gateway/src/." "${STAGE_DIR}/usr/lib/nidoai/src/"
  cat > "${STAGE_DIR}/usr/lib/nidoai/package.json" <<'PKG'
{
  "name": "nidoai-gateway",
  "version": "0.2.0",
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
  "${SCRIPT_DIR}/nidoai-gateway/files/usr/libexec/nidoai/gateway-daemon" \
  "${SCRIPT_DIR}/nidoagent-cli/files/usr/bin/nidoagent" \
  "${SCRIPT_DIR}/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs" \
  "${SCRIPT_DIR}/nido-desktop-settings/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/libexec/ubuntu-nido/bind-installer-fonts" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/libexec/ubuntu-nido/configure-firewall" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/libexec/ubuntu-nido/ensure-vietnamese-ime" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/libexec/ubuntu-nido/nidobox-tray" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/local/bin/nido-welcome" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/local/bin/nidoagent-quicklaunch" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giao-cho-NidoAgent" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Gui-qua-NidoDrop-LAN" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/bin/nido-doctor" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/bin/nido-security-verify" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/usr/lib/systemd/user-environment-generators/60-nido-vm-gsk" \
  "${SCRIPT_DIR}/nido-desktop-settings/files/etc/skel/.local/share/nautilus/scripts/Giai-nen-Chuan-Tieng-Viet" \
  "${SCRIPT_DIR}/nido-hub/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nido-hub/DEBIAN/prerm" \
  "${SCRIPT_DIR}/nido-hub/files/usr/bin/nido-hub" \
  "${SCRIPT_DIR}/nido-hub/files/usr/lib/nido-hub/app.py" \
  "${SCRIPT_DIR}/nido-hub/files/usr/libexec/nido-hub/nido-pkg-helper" \
  "${SCRIPT_DIR}/nidooffice-cli/files/usr/bin/nidooffice" \
  "${SCRIPT_DIR}/nidooffice-cli/files/usr/lib/nidooffice-cli/engine.py" \
  "${SCRIPT_DIR}/nidooffice-desktop/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nidooffice-desktop/DEBIAN/postrm" \
  "${SCRIPT_DIR}/nidooffice-desktop/files/usr/bin/nidooffice-desktop" \
  "${SCRIPT_DIR}/nidooffice-desktop/files/usr/bin/pkcs11-helper" \
  "${SCRIPT_DIR}/nidooffice-desktop/files/usr/lib/nidooffice-desktop/install-runtime.sh" \
  "${SCRIPT_DIR}/nido-desktop/DEBIAN/postinst" \
  "${SCRIPT_DIR}/nido-desktop/DEBIAN/postrm" \
  "${SCRIPT_DIR}/nido-desktop/files/usr/libexec/ubuntu-nido/showtime-launch" \
  "${SCRIPT_DIR}/nido-desktop/files/usr/libexec/ubuntu-nido/native-first-cleanup"

# Stage 1 Distribution Hardening (obfuscate/minify staged copies only; raw Git sources stay untouched)
cp -R "${SCRIPT_DIR}/nido-hub/files/usr/lib/nido-hub/." "${NIDOHUB_STAGE_DIR}/usr/lib/nido-hub/"
cp "${SCRIPT_DIR}/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs" "${NIDOAGENT_STAGE_DIR}/usr/lib/nidoagent-cli/cli.mjs"
cp "${SCRIPT_DIR}/nidooffice-cli/files/usr/lib/nidooffice-cli/engine.py" "${NIDOOFFICE_CLI_STAGE_DIR}/usr/lib/nidooffice-cli/engine.py"
chmod 755 "${NIDOAGENT_STAGE_DIR}/usr/lib/nidoagent-cli/cli.mjs" "${NIDOOFFICE_CLI_STAGE_DIR}/usr/lib/nidooffice-cli/engine.py"

if [[ "$NIDO_HARDEN_DIST" == "1" ]]; then
  echo "==> Applying Stage 1 distribution hardening (AST/zlib Python + lexical JS compaction)..."
  PY_TARGETS=()
  while IFS= read -r pyf; do
    PY_TARGETS+=("$pyf")
  done < <(find "${NIDOHUB_STAGE_DIR}/usr/lib/nido-hub" "${NIDOOFFICE_CLI_STAGE_DIR}/usr/lib/nidooffice-cli" -name '*.py' | sort)
  python3 "${ROOT_DIR}/scripts/harden_dist.py" "${PY_TARGETS[@]}"

  JS_TARGETS=()
  while IFS= read -r jsf; do
    JS_TARGETS+=("$jsf")
  done < <(find "${STAGE_DIR}/usr/lib/nidoai/src" "${NIDOAGENT_STAGE_DIR}/usr/lib/nidoagent-cli" \( -name '*.js' -o -name '*.mjs' \) | sort)
  node "${ROOT_DIR}/scripts/harden_js.mjs" "${JS_TARGETS[@]}"
fi

MANIFEST_ARGS=(--integrity-manifest)
if [[ -n "$APT_GPG_KEY" ]]; then
  MANIFEST_ARGS+=(--gpg-key "$APT_GPG_KEY")
fi

VER_ARGS=()
if [[ -n "$DEB_VERSION_SUFFIX" ]]; then
  VER_ARGS=(--version-suffix "$DEB_VERSION_SUFFIX")
  echo "==> Building dev packages with version suffix: ${DEB_VERSION_SUFFIX}"
fi

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidoai-gateway" \
  --extra-files "$STAGE_DIR" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidoai-gateway_0.2.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidoagent-cli" \
  --extra-files "$NIDOAGENT_STAGE_DIR" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidoagent-cli_0.2.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-desktop-settings" \
  --extra-files "$SETTINGS_STAGE_DIR" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nido-desktop-settings_0.2.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-hub" \
  --extra-files "$NIDOHUB_STAGE_DIR" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nido-hub_0.2.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidooffice-cli" \
  --extra-files "$NIDOOFFICE_CLI_STAGE_DIR" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidooffice-cli_0.2.0${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nidooffice-desktop" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nidooffice-desktop_0.13.1${DEB_VERSION_SUFFIX}_all.deb"

python3 "$DEB_BUILDER" \
  --source-dir "${SCRIPT_DIR}/nido-desktop" \
  "${MANIFEST_ARGS[@]}" \
  ${VER_ARGS[@]+"${VER_ARGS[@]}"} \
  --output "${DIST_DIR}/nido-desktop_0.2.0${DEB_VERSION_SUFFIX}_all.deb"

if [[ -n "$VIELOTUS_BIN_DIR" && -d "$VIELOTUS_BIN_DIR" ]]; then
  mkdir -p "${SCRIPT_DIR}/vendor"
  shopt -s nullglob
  vl_debs=("${VIELOTUS_BIN_DIR}"/vielotus_*.deb)
  shopt -u nullglob
  if [[ "${#vl_debs[@]}" -gt 0 ]]; then
    rm -f "${SCRIPT_DIR}/vendor"/vielotus_*.deb
    for deb in "${vl_debs[@]}"; do
      cp "$deb" "${SCRIPT_DIR}/vendor/"
      echo "==> Staged vendor package: $(basename "$deb")"
    done
  fi
fi

if [[ -n "$NIDOBOX_BIN_DIR" && -d "$NIDOBOX_BIN_DIR" ]]; then
  mkdir -p "${SCRIPT_DIR}/vendor"
  shopt -s nullglob
  nb_debs=("${NIDOBOX_BIN_DIR}"/nidobox_*.deb)
  shopt -u nullglob
  if [[ "${#nb_debs[@]}" -gt 0 ]]; then
    rm -f "${SCRIPT_DIR}/vendor"/nidobox_*.deb
    for deb in "${nb_debs[@]}"; do
      cp "$deb" "${SCRIPT_DIR}/vendor/"
      echo "==> Staged vendor package: $(basename "$deb")"
    done
  fi
fi

echo "==> Built packages in ${DIST_DIR}:"
ls -lh "$DIST_DIR"
