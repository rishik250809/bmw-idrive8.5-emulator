#!/usr/bin/env bash
# One-command installer for the BMW iDrive 8.5 (MGU22) emulator on Apple Silicon Macs.
#
#   1. Put your own BMW packages into ./bmw-files  (see bmw-files/README.md)
#   2. bash install.sh
#
# Safe to run again: finished steps are skipped. Optional: bash install.sh /path/to/psdzdata
set -euo pipefail
input_folder="${1:-}"
if [[ -n "$input_folder" && "$input_folder" != /* ]]; then input_folder="$PWD/$input_folder"; fi
cd "$(dirname "$0")"
ROOT="$(pwd)"
VM="${BMW_VM:-bmw-idrive85}"
PORT="${BMW_VIEWER_PORT:-5011}"
FILES="${input_folder:-$ROOT/bmw-files}"
explicit_files=false
[[ $# -eq 0 ]] || explicit_files=true
step=0
say()  { step=$((step + 1)); printf '\n\033[1;34m[%s/7] %s\033[0m\n' "$step" "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '\n\033[1;31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

say 'Checking this Mac'
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || fail 'An Apple Silicon Mac (M1 or newer) is required.'
[[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 26 ]] || fail 'macOS 26 or newer is required (the GPU driver needs Metal 4).'
command -v brew >/dev/null || fail 'Homebrew is required. Install it from https://brew.sh, then run install.sh again.'
free_gb=$(df -g "$ROOT" | awk 'NR==2 {print $4}')
[[ "$free_gb" -ge 25 ]] || fail "At least 25 GiB of free disk space is needed before installation (found ${free_gb} GiB). Keep additional space for BMW source files or extracted backups; navigation maps are not included."
ok "$(sw_vers -productName) $(sw_vers -productVersion), $(sysctl -n machdep.cpu.brand_string), ${free_gb} GiB free"

say 'Installing tools (Homebrew)'
# Always "brew install": no-op when present, and marks each formula as wanted so
# Homebrew's automatic cleanup never removes it (the Mac GPU driver links against them).
formulas=(lima vulkan-loader libepoxy spirv-tools molten-vk)
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || formulas+=(python@3.13)
mkdir -p "$ROOT/.local"
brew_log="$ROOT/.local/brew-install.log"
: > "$brew_log"
brew_install() { HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 brew install --quiet "$@" >> "$brew_log" 2>&1 || true; }
brew_install "${formulas[@]}"
if ! command -v krunkit >/dev/null; then
    # The project's GPU driver needs the libkrun from the libkrun/krun tap (it links a separate libvirglrenderer;
    # the core tap's libkrun 1.19.8+ does not). Install it first, and name every formula explicitly, so that
    # krunkit does not pull in the core libkrun as a dependency and Homebrew trusts the tap formulae.
    brew_install libkrun/krun/libkrun libkrun/krun/virglrenderer-krun
    brew_install libkrun/krun/krunkit
fi
grep -v 'already installed' "$brew_log" || true
for formula in lima vulkan-loader libepoxy spirv-tools molten-vk libkrun; do
    brew list --formula "$formula" >/dev/null 2>&1 || fail "Homebrew could not install $formula. Details: $brew_log"
done
command -v krunkit >/dev/null || fail "Homebrew could not install krunkit. Details: $brew_log"
libkrun_status=0
bash scripts/check_libkrun.sh || libkrun_status=$?
[[ "$libkrun_status" -ne 1 ]] || fail "The installed libkrun cannot load the project graphics driver (see above). Homebrew output: $brew_log"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || fail 'Python 3.11 or newer is required; check that the Homebrew Python is on your PATH.'
ok 'lima, krunkit, python, Vulkan loader and libraries present'

say 'Getting the open-source graphics pieces'
fetch() {
    python3 "$ROOT/tools/fetch_asset.py" "$1"
}
mkdir -p "$ROOT/private"
if [[ ! -f private/host-runtime/libvulkan_kosmickrisp.dylib || ! -f private/host-runtime/libvirglrenderer.1.dylib ]]; then
    if fetch host-runtime.tar.gz; then
        tar -xzf private/host-runtime.tar.gz -C private
    else
        printf '  download not available; building the Mac GPU driver from source (15-30 minutes)\n'
        bash tools/build_host_gpu.sh
    fi
fi
ok 'Mac GPU driver (KosmicKrisp + virglrenderer)'
fetch driver.tar.gz || fail 'Could not verify/download driver.tar.gz. Check your connection and that the project release has been published, then run this script again.'
printf '  The open-source tools image will be built inside the VM automatically (or loaded from your own local cache).\n'
bash doctor.sh
source "$ROOT/scripts/common.sh"

installed=false
if grep -Fxq "$VM" <<< "$(limactl list --format '{{.Name}}' 2>/dev/null)"; then
    [[ "$(limactl list --format '{{.Status}}' "$VM")" == Running ]] || lima_start "$VM" --tty=false >/dev/null 2>&1
    limactl shell --workdir=/tmp "$VM" -- sudo test -e /srv/bmw-idrive85/state/rootfs && installed=true
fi

say 'Finding your BMW files'
use_bundle=false
extracted="$(python3 tools/prepare_extracted.py "$FILES" --detect)"
if $installed; then
    ok 'Already installed in the VM; skipping import (delete the VM to reinstall)'
elif ! $explicit_files && [[ -z "$extracted" && -f private/runtime.tar.gz ]]; then
    use_bundle=true
    ok 'Using prepared private/runtime.tar.gz'
else
    [[ -d "$FILES" ]] || fail "Folder not found: $FILES"
    if [[ -n "$extracted" ]]; then
        python3 tools/prepare_extracted.py "$extracted" --check-only || fail 'Extracted backup is incomplete; see docs/EXTRACTED_FILES.md.'
        ok "Extracted layout and Hmi8 match the supported release in $extracted"
        printf '  Linux filesystem repair, resource build checks and transfer verification run during import.\n'
    else
        python3 tools/find_packages.py "$FILES" --plan .local/import-plan.json >/dev/null 2>.local/find-errors.txt || {
        cat .local/find-errors.txt >&2
        fail "The BMW packages were not found or incomplete in $FILES. See bmw-files/README.md."; }
        ok "Found a complete, verified package release in $FILES"
    fi
fi

say 'Creating the VM and importing (first time: 10-20 minutes)'
if $installed; then
    ok 'Already done'
elif $use_bundle; then
    bash setup.sh
else
    bash import.sh "$FILES"
fi

say 'Starting the HMI'
if $installed; then bash scripts/sync_source.sh; fi
bash scripts/ensure_tools.sh
bash start.sh >/dev/null

say 'Waiting for the HMI to appear (about 1-2 minutes)'
deadline=$((SECONDS + 600))
boot_retries=0
stable_since=0
while true; do
if python3 - "$PORT" <<'PY' 2>/dev/null
import json, sys, urllib.request
port = sys.argv[1]
status = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=3))
exit_code = status.get('hmi_exit_code')
if exit_code is not None:
    sys.exit(75 if exit_code == 133 else 76)
frame = urllib.request.urlopen(f'http://127.0.0.1:{port}/frame.jpg', timeout=5).read()
sys.exit(0 if status.get('ready') and len(frame) > 30000 else 1)
PY
then
    if [[ "$stable_since" -eq 0 ]]; then stable_since=$SECONDS; fi
    if [[ $((SECONDS - stable_since)) -ge 10 ]]; then break; fi
else
    status=$?
    stable_since=0
    if [[ "$status" -eq 75 && "$boot_retries" -lt 2 ]]; then
        boot_retries=$((boot_retries + 1))
        printf '\n  G26 startup trapped during shader initialization; saving logs and retrying (%s/2).\n' "$boot_retries"
        limactl shell --workdir=/tmp "$VM" -- sudo bash -s -- "$boot_retries" <<'GUEST'
set -euo pipefail
logs=/srv/bmw-idrive85/state/logs
destination="$logs/installer-startup-retry-$1-$(date +%s)"
mkdir -p "$destination"
for file in "$logs"/mesa-g26.*; do
    [[ ! -f "$file" ]] || cp -a "$file" "$destination/"
done
docker restart -t 15 bmw-idrive85
GUEST
    elif [[ "$status" -eq 75 || "$status" -eq 76 ]]; then
        fail "Hmi8 stopped during startup. Retry limit reached or a different failure occurred. Inspect: limactl shell $VM -- sudo ls /srv/bmw-idrive85/state/logs"
    fi
fi
    [[ $SECONDS -lt $deadline ]] || fail "The HMI did not appear within 10 minutes. Logs: limactl shell $VM -- sudo ls /srv/bmw-idrive85/state/logs"
    printf '.'; sleep 5
done
printf '\n'
ok "Running. Viewer: http://localhost:$PORT   Settings: http://localhost:$((PORT + 1))"
if [[ "${BMW_OPEN_BROWSER:-1}" != 0 ]]; then open "http://localhost:$PORT" 2>/dev/null || true; fi
printf '\nNext time just run: bash start.sh   (stop with: bash stop.sh --vm)\n'
