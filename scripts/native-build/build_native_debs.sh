#!/usr/bin/env bash
# Stage 2 Multi-Arch Native Package Builder for NidoOS commercial/protected binaries.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET_ARCH="all"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      TARGET_ARCH="${2:-all}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: build_native_debs.sh [--arch amd64|arm64|all] [--dry-run]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$TARGET_ARCH" in
  amd64|arm64) ARCHES=("$TARGET_ARCH") ;;
  all) ARCHES=("amd64" "arm64") ;;
  *)
    echo "Unsupported --arch: $TARGET_ARCH (expected amd64|arm64|all)" >&2
    exit 2
    ;;
esac

if [[ "$DRY_RUN" -eq 1 ]]; then
  for a in "${ARCHES[@]}"; do
    echo "=== Stage 2 Native Build Dry-Run (${a}) ==="
    python3 "${SCRIPT_DIR}/compile_hub_cython.py" \
      --arch "$a" \
      --out-deb "${REPO_ROOT}/packages/dist/nido-hub_0.2.0_${a}.deb" \
      --dry-run
    bash "${SCRIPT_DIR}/build_gateway_sea.sh" "$a" "${REPO_ROOT}/packages/dist/native" --dry-run
  done
  exit 0
fi

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: Docker with buildx is required to build Stage 2 native Linux packages from macOS." >&2
  exit 1
}

mkdir -p "${REPO_ROOT}/packages/dist/native"
for a in "${ARCHES[@]}"; do
  PLATFORM="linux/${a}"
  IMAGE_TAG="nidoos-native-builder:${a}"
  echo "==> Building Stage 2 native packages for ${PLATFORM}..."
  docker build --platform "$PLATFORM" -t "$IMAGE_TAG" -f "${SCRIPT_DIR}/Dockerfile.native" "$REPO_ROOT"
  docker run --rm --platform "$PLATFORM" \
    -v "${REPO_ROOT}:/workspace" \
    -e "APT_GPG_KEY=${APT_GPG_KEY:-}" \
    "$IMAGE_TAG" \
    bash -c "
      set -euo pipefail
      python3 /workspace/scripts/native-build/compile_hub_cython.py \
        --repo-root /workspace \
        --arch ${a} \
        --out-deb /workspace/packages/dist/nido-hub_0.2.0_${a}.deb
      bash /workspace/scripts/native-build/build_gateway_sea.sh ${a} /workspace/packages/dist/native
    "
done
