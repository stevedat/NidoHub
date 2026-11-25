#!/usr/bin/env bash
# Stage 2 native builder for Node 22+ Single Executable Application (SEA) ELF binary:
# bundles both nidoai-gateway and nidoagent-cli into ONE unified multi-call ELF binary
# (saving ~100 MiB disk & keeping compressed .deb small), with V8 bytecode cache enabled.
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
  echo "[DRY-RUN] Bundle packages/nidoai-gateway/src/cli.mjs + packages/nidoagent-cli/.../cli.mjs -> nidoai-sea (${ARCH}, multi-call Node 22 SEA ELF)"
  echo "[DRY-RUN] Verify live SEA gateway serve <-> SEA agent status smoke test"
  echo "[DRY-RUN] Output directory: ${OUT_DIR}"
  exit 0
fi

NODE_BIN="${NIDO_SEA_NODE_BIN:-/opt/node-sea/bin/node}"
if [[ ! -x "$NODE_BIN" ]]; then
  NODE_BIN="$(command -v node || true)"
fi
[[ -x "$NODE_BIN" ]] || { echo "Static node binary with NODE_SEA_FUSE is required" >&2; exit 1; }

ESBUILD_BIN="${NIDO_SEA_ESBUILD_BIN:-/opt/node-sea/bin/esbuild}"
POSTJECT_BIN="${NIDO_SEA_POSTJECT_BIN:-/opt/node-sea/bin/postject}"
if [[ ! -x "$ESBUILD_BIN" ]]; then ESBUILD_BIN="$(command -v esbuild || true)"; fi
if [[ ! -x "$POSTJECT_BIN" ]]; then POSTJECT_BIN="$(command -v postject || true)"; fi
[[ -x "$ESBUILD_BIN" ]] || { echo "esbuild binary is required (installed in Dockerfile.native)" >&2; exit 1; }
[[ -x "$POSTJECT_BIN" ]] || { echo "postject binary is required (installed in Dockerfile.native)" >&2; exit 1; }

if ! grep -a -q "NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2" "$NODE_BIN"; then
  echo "ERROR: ${NODE_BIN} does not contain NODE_SEA_FUSE (Ubuntu distro libnode.so stub cannot build SEA; use /opt/node-sea/bin/node)." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT_DIR"

echo "==> Bundling nidoai-gateway and nidoagent-cli into unified multi-call CJS (${ARCH})..."
"$ESBUILD_BIN" "${REPO_ROOT}/packages/nidoai-gateway/src/cli.mjs" \
  --bundle \
  --platform=node \
  --target=node22 \
  --format=cjs \
  --minify \
  --outfile="${WORK}/gateway.cjs"

"$ESBUILD_BIN" "${REPO_ROOT}/packages/nidoagent-cli/files/usr/lib/nidoagent-cli/cli.mjs" \
  --bundle \
  --platform=node \
  --target=node22 \
  --format=cjs \
  --minify \
  --outfile="${WORK}/agent.cjs"

cat > "${WORK}/multi-entry.cjs" <<'EOF'
'use strict';
const path = require('node:path');
const argv0Base = path.basename(process.argv[0] || '');
if (process.argv[2] === '--nido-sea-mode=agent') {
  process.argv.splice(2, 1);
  require('./agent.cjs');
} else if (argv0Base === 'nidoagent' || argv0Base.startsWith('nidoagent-sea')) {
  require('./agent.cjs');
} else {
  require('./gateway.cjs');
}
EOF

"$ESBUILD_BIN" "${WORK}/multi-entry.cjs" \
  --bundle \
  --platform=node \
  --target=node22 \
  --format=cjs \
  --minify \
  --outfile="${WORK}/nidoai-sea.cjs"

cat > "${WORK}/nidoai-sea.json" <<EOF
{
  "main": "${WORK}/nidoai-sea.cjs",
  "output": "${WORK}/nidoai-sea.blob",
  "disableExperimentalSEAWarning": true,
  "useCodeCache": true
}
EOF

echo "==> Generating Node 22 SEA blob with V8 code cache..."
"$NODE_BIN" --experimental-sea-config "${WORK}/nidoai-sea.json"

OUT_BIN="${OUT_DIR}/nidoai-sea"
cp "$NODE_BIN" "$OUT_BIN"
chmod 755 "$OUT_BIN"

if command -v strip >/dev/null 2>&1; then
  echo "==> Stripping DWARF debug_info & symbol tables before SEA injection..."
  strip --strip-all "$OUT_BIN"
fi

echo "==> Injecting NODE_SEA_BLOB into ELF binary (${OUT_BIN})..."
"$POSTJECT_BIN" "$OUT_BIN" NODE_SEA_BLOB "${WORK}/nidoai-sea.blob" \
  --sentinel-fuse NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2

echo "==> Smoke-testing native SEA ELF binary (gateway serve <-> agent status)..."
AGENT_VER="$("$OUT_BIN" --nido-sea-mode=agent --version)"
echo "    SEA agent --version: ${AGENT_VER}"
[[ "$AGENT_VER" == nidoagent* ]] || { echo "ERROR: Unexpected SEA agent --version output: ${AGENT_VER}" >&2; exit 1; }

SMOKE_HOME="${WORK}/smoke-home"
mkdir -p "${SMOKE_HOME}/.nidoai" "${SMOKE_HOME}/.config/ubuntu-nido"
printf '{"aiEnabled": true}\n' > "${SMOKE_HOME}/.config/ubuntu-nido/ai-policy.json"
cat > "${SMOKE_HOME}/.nidoai/.env.local" <<'ENVEOF'
NIDOAI_ENABLED=1
NIDOAI_PORT=18996
NIDOAI_HOST=127.0.0.1
NIDOAI_CLIENT_TOKEN=nidoai_sea_smoke_token_0123456789
NIDOAI_PRIMARY_BACKEND=nido
ENVEOF
chmod 600 "${SMOKE_HOME}/.nidoai/.env.local"

HOME="$SMOKE_HOME" NIDOAI_HOME="${SMOKE_HOME}/.nidoai" NIDOAI_SECRET_STORE=file \
  "$OUT_BIN" serve > "${WORK}/gw.log" 2>&1 &
GW_PID=$!
cleanup_gw() {
  kill "$GW_PID" 2>/dev/null || true
  wait "$GW_PID" 2>/dev/null || true
}
trap 'cleanup_gw; rm -rf "$WORK"' EXIT

for _ in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:18996/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

HEALTH_JSON="$(curl -fsS http://127.0.0.1:18996/healthz)"
echo "    SEA gateway /healthz: ${HEALTH_JSON}"
echo "$HEALTH_JSON" | grep -q '"status":"ok"' || { echo "ERROR: SEA /healthz failed" >&2; cat "${WORK}/gw.log" >&2; exit 1; }

UI_CODE="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18996/)"
[[ "$UI_CODE" == "404" ]] || { echo "ERROR: Expected 404 on default-off Web UI, got ${UI_CODE}" >&2; exit 1; }

AGENT_STATUS="$(HOME="$SMOKE_HOME" NIDOAI_HOME="${SMOKE_HOME}/.nidoai" "$OUT_BIN" --nido-sea-mode=agent status)"
echo "$AGENT_STATUS" | grep -q "127.0.0.1:18996" || { echo "ERROR: SEA agent status did not detect live SEA gateway" >&2; echo "$AGENT_STATUS" >&2; exit 1; }
echo "    SEA live E2E smoke test passed (gateway /healthz=200, GET /=404, agent status=connected)!"

cleanup_gw
if command -v file >/dev/null 2>&1; then
  echo "    ELF info: $(file -b "$OUT_BIN")"
fi
ls -lh "$OUT_BIN"
