#!/usr/bin/env bash
# Stage 2 Multi-Arch Native Package Builder for NidoOS commercial/protected binaries.
# Compiles Cython ELF .so extensions (nido-hub) + Node 22 SEA ELF binary (nidoai-gateway & nidoagent-cli)
# inside Ubuntu 26.04 Linux containers, then assembles & GPG-signs architecture-specific .deb packages on the host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET_ARCH="all"
DRY_RUN=0
ASSEMBLE_ONLY=0
APT_GPG_KEY="${APT_GPG_KEY:-AC980631CB8BA9D2602A0990528AF2083F61DC2F}"
DEB_VERSION_SUFFIX="${DEB_VERSION_SUFFIX:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      TARGET_ARCH="${2:-all}"
      shift 2
      ;;
    --gpg-key)
      APT_GPG_KEY="${2:-}"
      shift 2
      ;;
    --assemble-only)
      ASSEMBLE_ONLY=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: build_native_debs.sh [--arch amd64|arm64|all] [--gpg-key <fpr>] [--assemble-only] [--dry-run]"
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
      --out-deb "${REPO_ROOT}/packages/dist/native/nido-hub_0.2.0_${a}.deb" \
      --dry-run
    bash "${SCRIPT_DIR}/build_gateway_sea.sh" "$a" "${REPO_ROOT}/packages/dist/native/${a}" --dry-run
  done
  exit 0
fi

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: Docker is required to build Stage 2 native Linux packages." >&2
  exit 1
}

NATIVE_OUT="${REPO_ROOT}/packages/dist/native"
mkdir -p "$NATIVE_OUT"

assemble_native_debs() {
  local arch="$1"
  local arch_dir="${NATIVE_OUT}/${arch}"
  local hub_stage="${arch_dir}/nido-hub-stage"
  local sea_bin="${arch_dir}/nidoai-sea"
  local work_tmp
  work_tmp="$(mktemp -d)"

  local py_abi="3.14"
  if [[ -f "${hub_stage}/.python-abi" ]]; then
    py_abi="$(tr -d '[:space:]' < "${hub_stage}/.python-abi")"
  fi
  local py_maj="${py_abi%%.*}"
  local py_min="${py_abi#*.}"
  local py_next="${py_maj}.$((py_min + 1))"

  local manifest_args=(--integrity-manifest)
  if [[ -n "$APT_GPG_KEY" ]]; then
    manifest_args+=(--gpg-key "$APT_GPG_KEY")
  fi
  local ver_args=()
  if [[ -n "$DEB_VERSION_SUFFIX" ]]; then
    ver_args=(--version-suffix "$DEB_VERSION_SUFFIX")
  fi

  # 1. Native nido-hub_<ver>_<arch>.deb (Cython .so + hardened app.py, zero raw core .py)
  local hub_work="${work_tmp}/nido-hub"
  local hub_extra="${work_tmp}/nido-hub-extra"
  cp -R "${REPO_ROOT}/packages/nido-hub" "$hub_work"
  cp -R "$hub_stage" "$hub_extra"
  rm -f "${hub_extra}/.python-abi"
  rm -rf "${hub_work}/stage" "${hub_work}/files/usr/lib/nido-hub"
  mkdir -p "${hub_work}/files/usr/lib/nido-hub"
  python3 - "$hub_work/DEBIAN/control" "$arch" "$py_abi" "$py_next" <<'PY'
import sys
from pathlib import Path
p, arch, py_abi, py_next = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
txt = p.read_text(encoding="utf-8")
txt = txt.replace("Architecture: all", f"Architecture: {arch}")
txt = txt.replace("Depends: python3,", f"Depends: python3 (>= {py_abi}), python3 (<< {py_next}),")
p.write_text(txt, encoding="utf-8")
PY
  python3 "${REPO_ROOT}/scripts/build_deb_portable.py" \
    --source-dir "$hub_work" \
    --extra-files "$hub_extra" \
    --compression xz \
    "${manifest_args[@]}" \
    ${ver_args[@]+"${ver_args[@]}"} \
    --output "${NATIVE_OUT}/nido-hub_0.2.0${DEB_VERSION_SUFFIX}_${arch}.deb"

  # 2. Native nidoai-gateway_<ver>_<arch>.deb (Node 22 SEA multi-call ELF binary, zero raw JS source)
  local gw_work="${work_tmp}/nidoai-gateway"
  local gw_extra="${work_tmp}/nidoai-gateway-extra"
  cp -R "${REPO_ROOT}/packages/nidoai-gateway" "$gw_work"
  rm -rf "${gw_work}/stage" "${gw_work}/src"
  mkdir -p "${gw_extra}/usr/libexec/nidoai"
  cp "$sea_bin" "${gw_extra}/usr/libexec/nidoai/nidoai-sea"
  chmod 755 "${gw_extra}/usr/libexec/nidoai/nidoai-sea"
  python3 - "$gw_work/DEBIAN/control" "$arch" <<'PY'
import sys
from pathlib import Path
p, arch = Path(sys.argv[1]), sys.argv[2]
txt = p.read_text(encoding="utf-8")
txt = txt.replace("Architecture: all", f"Architecture: {arch}")
txt = txt.replace("Depends: nodejs (>= 18.0.0) | nodejs", "Depends: libc6 (>= 2.28)")
p.write_text(txt, encoding="utf-8")
PY
  python3 "${REPO_ROOT}/scripts/build_deb_portable.py" \
    --source-dir "$gw_work" \
    --extra-files "$gw_extra" \
    --compression xz \
    "${manifest_args[@]}" \
    ${ver_args[@]+"${ver_args[@]}"} \
    --output "${NATIVE_OUT}/nidoai-gateway_0.2.0${DEB_VERSION_SUFFIX}_${arch}.deb"

  # 3. Native nidoagent-cli_<ver>_<arch>.deb (delegates to unified SEA binary, zero raw JS source)
  local ag_work="${work_tmp}/nidoagent-cli"
  cp -R "${REPO_ROOT}/packages/nidoagent-cli" "$ag_work"
  rm -rf "${ag_work}/stage" "${ag_work}/files/usr/lib/nidoagent-cli"
  python3 - "$ag_work/DEBIAN/control" "$arch" <<'PY'
import sys
from pathlib import Path
p, arch = Path(sys.argv[1]), sys.argv[2]
txt = p.read_text(encoding="utf-8")
txt = txt.replace("Architecture: all", f"Architecture: {arch}")
p.write_text(txt, encoding="utf-8")
PY
  python3 "${REPO_ROOT}/scripts/build_deb_portable.py" \
    --source-dir "$ag_work" \
    --compression xz \
    "${manifest_args[@]}" \
    ${ver_args[@]+"${ver_args[@]}"} \
    --output "${NATIVE_OUT}/nidoagent-cli_0.2.0${DEB_VERSION_SUFFIX}_${arch}.deb"

  rm -rf "$work_tmp"
}

run_compile_in_container() {
  local arch="$1"
  local platform="linux/${arch}"
  local image_tag="nidoos-native-builder:${arch}"
  local cur_ctx=""
  cur_ctx="$(docker context show 2>/dev/null || true)"

  mkdir -p "${NATIVE_OUT}/${arch}"

  if [[ "${NIDO_SINGLE_CONTAINER:-0}" == "1" || "$cur_ctx" == "kiri" ]]; then
    local cache_dir="/tmp/nidoos-native-cache/${arch}"
    mkdir -p "${cache_dir}/apt" "${cache_dir}/node"
    echo "==> Running single-container Stage 2 build (${platform}, cached in ${cache_dir})..."
    docker run --rm --platform "$platform" \
      -v "${REPO_ROOT}:/workspace" \
      -v "${cache_dir}:/cache" \
      ubuntu:26.04 \
      bash -euxc "
        export DEBIAN_FRONTEND=noninteractive
        rm -f /etc/apt/apt.conf.d/docker-clean
        apt-get update
        apt-get -o dir::cache::archives=/cache/apt install -y --no-install-recommends \
          binutils build-essential ca-certificates curl cython3 dpkg-dev \
          file gcc gnupg python3-dev python3-setuptools xz-utils
        case '${arch}' in
          amd64) node_arch='x64'; expected_sha='cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8' ;;
          arm64) node_arch='arm64'; expected_sha='2babb9eb5264860d540849f4b064a6fbdebcdc323657d9d382ce23d313fd5e09' ;;
          *) echo 'Unsupported arch: ${arch}' >&2; exit 1 ;;
        esac
        tarball=\"node-v22.23.5-linux-\${node_arch}.tar.xz\"
        if [ ! -f \"/cache/node/\${tarball}\" ] || ! echo \"\${expected_sha}  /cache/node/\${tarball}\" | sha256sum -c - >/dev/null 2>&1; then
          curl -fsSL \"https://nodejs.org/dist/v22.23.5/\${tarball}\" -o \"/cache/node/\${tarball}\"
          echo \"\${expected_sha}  /cache/node/\${tarball}\" | sha256sum -c -
        fi
        mkdir -p /opt/node-sea
        python3 - \"/cache/node/\${tarball}\" /opt/node-sea <<'PYTAR'
import os, sys, tarfile
from pathlib import Path
src, dst = sys.argv[1], Path(sys.argv[2])
with tarfile.open(src, 'r:xz') as tf:
    for m in tf.getmembers():
        parts = m.name.split('/', 1)
        if len(parts) < 2 or not parts[1]:
            continue
        rel = parts[1]
        if rel.startswith('include/') or rel.startswith('share/'):
            continue
        out = dst / rel
        if m.isdir():
            out.mkdir(parents=True, exist_ok=True)
        elif m.issym():
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists() or out.is_symlink():
                out.unlink()
            os.symlink(m.linkname, out)
        elif m.isfile():
            out.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(m) as fsrc, open(out, 'wb') as fdst:
                fdst.write(fsrc.read())
            out.chmod(m.mode & 0o777)
PYTAR
        export PATH=\"/opt/node-sea/bin:\${PATH}\"
        grep -a -q 'NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2' /opt/node-sea/bin/node
        mkdir -p /cache/npm-tools
        if [ ! -x /cache/npm-tools/node_modules/.bin/esbuild ] || [ ! -x /cache/npm-tools/node_modules/.bin/postject ]; then
          /opt/node-sea/bin/npm install --prefix /cache/npm-tools --no-audit --no-fund esbuild postject
        fi
        export NIDO_SEA_ESBUILD_BIN=/cache/npm-tools/node_modules/.bin/esbuild
        export NIDO_SEA_POSTJECT_BIN=/cache/npm-tools/node_modules/.bin/postject
        python3 /workspace/scripts/native-build/compile_hub_cython.py \
          --repo-root /workspace \
          --arch '${arch}' \
          --stage-only '/workspace/packages/dist/native/${arch}/nido-hub-stage'
        bash /workspace/scripts/native-build/build_gateway_sea.sh '${arch}' '/workspace/packages/dist/native/${arch}'
      "
    return 0
  fi

  if [[ "${NIDO_REBUILD_IMAGE:-0}" == "1" ]] || ! docker image inspect "$image_tag" >/dev/null 2>&1; then
    echo "==> Building Stage 2 native toolchain image (${platform})..."
    docker build --progress=plain --platform "$platform" -t "$image_tag" -f "${SCRIPT_DIR}/Dockerfile.native" "$SCRIPT_DIR"
  else
    echo "==> Reusing cached Stage 2 builder image (${image_tag})"
  fi

  echo "==> Compiling & verifying Stage 2 Cython .so + Node 22 SEA ELF (${platform})..."
  docker run --rm --platform "$platform" \
    -v "${REPO_ROOT}:/workspace" \
    "$image_tag" \
    bash -c "
      set -euo pipefail
      python3 /workspace/scripts/native-build/compile_hub_cython.py \
        --repo-root /workspace \
        --arch ${arch} \
        --stage-only /workspace/packages/dist/native/${arch}/nido-hub-stage
      bash /workspace/scripts/native-build/build_gateway_sea.sh ${arch} /workspace/packages/dist/native/${arch}
    "
}

for a in "${ARCHES[@]}"; do
  if [[ "$ASSEMBLE_ONLY" -ne 1 ]]; then
    run_compile_in_container "$a"
  fi
  echo "==> Assembling GPG-signed native .deb packages for ${a} on host..."
  assemble_native_debs "$a"
done

echo "==> Stage 2 Native .deb Artifacts (${NATIVE_OUT}):"
ls -lh "${NATIVE_OUT}"/*.deb
