#!/usr/bin/env bash
# Reproducible Native-First Validation & Benchmark Harness for Ubuntu-Nido (AMD64 & ARM64)
# Collects measured OS metrics & regression checks — never estimates.
set -euo pipefail

ISO_PATH=""
JSON_OUT=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --iso)
      ISO_PATH="${2:-}"
      shift 2
      ;;
    --json-out)
      JSON_OUT="${2:-}"
      shift 2
      ;;
    --help|-h)
      cat <<'EOF'
Usage: validate_native_first.sh [--iso <path/to/Ubuntu-Nido.iso>] [--json-out <report.json>]
Runs live Native-First validation and system benchmarks on an installed Ubuntu-Nido (or stock Ubuntu) host.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
OS_PRETTY="$(awk -F= '/^PRETTY_NAME=/{gsub(/"/,"",$2); print $2}' /etc/os-release 2>/dev/null || uname -sr)"
KERNEL="$(uname -r)"
SESSION_TYPE="${XDG_SESSION_TYPE:-headless}"

ISO_BYTES=0
if [ -n "$ISO_PATH" ] && [ -f "$ISO_PATH" ]; then
  ISO_BYTES="$(stat -c '%s' "$ISO_PATH" 2>/dev/null || stat -f '%z' "$ISO_PATH" 2>/dev/null || echo 0)"
fi

BOOT_TIME="$(systemd-analyze 2>/dev/null | head -n 1 || echo 'unavailable')"
IDLE_RAM_MB="$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if(t>0) printf "%.1f", (t-a)/1024; else print "0"}' /proc/meminfo 2>/dev/null || echo 0)"
ROOT_USED_MB="$(df -BM --output=used / 2>/dev/null | tail -n 1 | tr -dc '0-9' || echo 0)"

check_cmd_pass() {
  if "$@" >/dev/null 2>&1; then
    printf 'pass'
  else
    printf 'fail'
  fi
}

DPKG_AUDIT="$(check_cmd_pass bash -c 'test -z "$(dpkg --audit 2>/dev/null)"')"
APT_CHECK="$(check_cmd_pass apt-get check)"
SEC_PKG="$(check_cmd_pass dpkg -s unattended-upgrades)"
SEC_DRYRUN="skipped"
if command -v unattended-upgrade >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
  SEC_DRYRUN="$(check_cmd_pass unattended-upgrade --dry-run)"
fi
FWUPD_OK="$(check_cmd_pass dpkg -s fwupd)"

FIREFOX_NATIVE="not-installed"
if [ -x /usr/bin/firefox ]; then
  if grep -qs "snap install firefox" /usr/bin/firefox 2>/dev/null; then
    FIREFOX_NATIVE="snap-wrapper"
  else
    FIREFOX_NATIVE="native-deb"
  fi
fi

CHROME_STATUS="not-installed"
if command -v google-chrome-stable >/dev/null 2>&1; then
  CHROME_STATUS="$(google-chrome-stable --version 2>/dev/null || echo installed)"
fi

SNAP_STORE_ABSENT="pass"
if command -v snap >/dev/null 2>&1 && snap list snap-store >/dev/null 2>&1; then
  SNAP_STORE_ABSENT="fail"
fi
SNAPD_INTACT="$(check_cmd_pass dpkg -s snapd)"

VIELOTUS_OK="$(check_cmd_pass dpkg -s vielotus)"
FLATPAK_OK="$(check_cmd_pass command -v flatpak)"
FLATPAK_COUNT=0
if command -v flatpak >/dev/null 2>&1; then
  FLATPAK_COUNT="$(flatpak list --app 2>/dev/null | wc -l | tr -d ' ')"
fi

NIDOHUB_OK="$(check_cmd_pass command -v nido-hub)"
NIDOOFFICE_OK="$(check_cmd_pass test -x /opt/NidoOffice/nidooffice)"
NIDOBOX_OK="$(check_cmd_pass command -v nidobox)"
NIDOAI_OK="$(check_cmd_pass command -v nidoai)"
SMARTCARD_OK="$(check_cmd_pass bash -c 'dpkg -s pcscd libccid opensc-pkcs11 >/dev/null 2>&1')"
NSS_TOOLS_OK="$(check_cmd_pass bash -c 'command -v certutil >/dev/null 2>&1 && command -v pk12util >/dev/null 2>&1')"
PKCS11_HELPER_OK="$(check_cmd_pass pkcs11-helper --self-test)"

measure_ms() {
  local t0 t1
  t0="$(python3 -c 'import time; print(int(time.monotonic()*1000))')"
  "$@" >/dev/null 2>&1 || true
  t1="$(python3 -c 'import time; print(int(time.monotonic()*1000))')"
  printf '%d' $((t1 - t0))
}

NIDOHUB_STARTUP_MS="$(measure_ms nido-hub --self-test)"
NIDOAI_DOCTOR_MS="$(measure_ms nidoai doctor)"

REPORT_JSON="$(python3 - \
  "$OS_PRETTY" "$ARCH" "$KERNEL" "$SESSION_TYPE" "$ISO_BYTES" \
  "$BOOT_TIME" "$IDLE_RAM_MB" "$ROOT_USED_MB" \
  "$DPKG_AUDIT" "$APT_CHECK" "$SEC_PKG" "$SEC_DRYRUN" "$FWUPD_OK" \
  "$FIREFOX_NATIVE" "$CHROME_STATUS" "$SNAP_STORE_ABSENT" "$SNAPD_INTACT" \
  "$VIELOTUS_OK" "$FLATPAK_OK" "$FLATPAK_COUNT" \
  "$NIDOHUB_OK" "$NIDOOFFICE_OK" "$NIDOBOX_OK" "$NIDOAI_OK" \
  "$SMARTCARD_OK" "$NSS_TOOLS_OK" "$PKCS11_HELPER_OK" \
  "$NIDOHUB_STARTUP_MS" "$NIDOAI_DOCTOR_MS" <<'PY'
import json, sys
keys = [
    "os", "arch", "kernel", "sessionType", "isoBytes",
    "bootTime", "idleRamMiB", "rootDiskUsedMiB",
    "dpkgAudit", "aptCheck", "unattendedUpgradesPkg", "unattendedUpgradesDryRun", "fwupdPkg",
    "firefoxMode", "chromeStatus", "snapStoreRemoved", "snapdIntact",
    "vielotusPkg", "flatpakAvailable", "preloadedFlatpakAppCount",
    "nidoHubBin", "nidoOfficeOpt", "nidoBoxBin", "nidoAiBin",
    "smartcardStack", "nssTools", "pkcs11Helper",
    "nidoHubSelfTestMs", "nidoAiDoctorMs",
]
raw = dict(zip(keys, sys.argv[1:]))
for num_k in ("isoBytes", "rootDiskUsedMiB", "preloadedFlatpakAppCount", "nidoHubSelfTestMs", "nidoAiDoctorMs"):
    raw[num_k] = int(raw[num_k] or 0)
raw["idleRamMiB"] = float(raw["idleRamMiB"] or 0.0)
print(json.dumps(raw, indent=2, ensure_ascii=False))
PY
)"

printf '%s\n' "$REPORT_JSON"
if [ -n "$JSON_OUT" ]; then
  printf '%s\n' "$REPORT_JSON" > "$JSON_OUT"
  echo "Saved measured report to ${JSON_OUT}" >&2
fi
