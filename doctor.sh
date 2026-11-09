#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo 'Requires an Apple Silicon Mac.' >&2; exit 1; }
[[ "$(sw_vers -productVersion | cut -d. -f1)" -ge 26 ]] || { echo 'This GPU driver requires macOS 26 or newer.' >&2; exit 1; }
for command in limactl krunkit python3 ssh otool; do
    command -v "$command" >/dev/null || { printf 'Missing prerequisite: %s\n' "$command" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'
# The project driver replaces libvirglrenderer by name, which needs a libkrun that links it (see the script).
libkrun_status=0
bash "$here/scripts/check_libkrun.sh" || libkrun_status=$?
[[ "$libkrun_status" -ne 1 ]] || exit 1
for library in libvirglrenderer.1.dylib libvulkan_kosmickrisp.dylib; do
    [[ -f "$here/private/host-runtime/$library" ]] || { echo "Missing private/host-runtime/$library" >&2; exit 1; }
    while IFS= read -r dependency; do
        [[ -f "$dependency" ]] || { echo "Missing linked dependency: $dependency" >&2; exit 1; }
    done < <(otool -L "$here/private/host-runtime/$library" | awk '$1 ~ /^\/opt\/homebrew\// {print $1}')
done
printf 'Host prerequisites present. Tested hardware: M1 Pro, M2 Pro and M5 Pro; other Apple Silicon models are not yet verified.\n'
