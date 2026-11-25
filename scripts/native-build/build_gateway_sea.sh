#!/usr/bin/env bash
# Stage 2 native builder for Node 22+ Single Executable Application (SEA) binaries:
# bundles nidoai-gateway and nidoagent-cli into stripped native Linux ELF binaries.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ARCH="${1:-}"
OUT_DIR="${2:-${REPO_ROOT}/packages/dist/native}"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --help|-h)
      echo "Usage: build_gateway_sea.sh <amd64|arm64> [out_dir] [--dry-run]"
      exit 0
      ;;
  esac
done

if [[ "$ARCH" != "amd64" && "$ARCH" != "arm64" ]]; then
  echo "Usage: build_gateway_sea.sh <amd64|arm64> [out_dir] [--dry-run]" >&2
  exit 2
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] Bundle packages/nidoai-gateway/src/cli.mjs -> nidoai-sea (${ARCH})"
  echo "[DRY-RUN] Bundle packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs -> nidoagent-sea (${ARCH})"
  echo "[DRY-RUN] Output directory: ${OUT_DIR}"
  exit 0
fi

command -v node >/dev/null 2>&1 || { echo "node is required" >&2; exit 1; }
command -v npx >/dev/null 2>&1 || { echo "npx is required" >&2; exit 1; }

NODE_BIN="$(command -v node)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT_DIR"

build_sea_binary() {
  local name="$1"
  local entry_js="$2"
  local out_bin="${OUT_DIR}/${name}"
  local bundle="${WORK}/${name}.cjs"
  local blob="${WORK}/${name}.blob"
  local sea_cfg="${WORK}/${name}.sea.json"

  npx --yes esbuild "$entry_js" \
    --bundle \
    --platform=node \
    --format=cjs \
    --minify \
    --outfile="$bundle"

  cat > "$sea_cfg" <<EOF
{
  "main": "${bundle}",
  "output": "${blob}",
  "disableExperimentalSEAWarning": true,
  "useCodeCache": true
}
EOF

  "$NODE_BIN" --experimental-sea-config "$sea_cfg"
  cp "$NODE_BIN" "$out_bin"
  npx --yes postject "$out_bin" NODE_SEA_BLOB "$blob" --sentinel-fuse NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2
  chmod 755 "$out_bin"
  echo "Built native Node SEA binary: ${out_bin}"
}

build_sea_binary "nidoai-sea-${ARCH}" "${REPO_ROOT}/packages/nidoai-gateway/src/cli.mjs"
build_sea_binary "nidoagent-sea-${ARCH}" "${REPO_ROOT}/packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs"
