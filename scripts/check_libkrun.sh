#!/usr/bin/env bash
# Checks that the installed libkrun can load this project's Mac GPU driver.
#
# The project replaces libvirglrenderer.1.dylib by name (DYLD_LIBRARY_PATH -> private/host-runtime).
# That only works if libkrun links a separate libvirglrenderer.1.dylib. The libkrun in Homebrew's core
# tap (1.19.8 and newer) links MoltenVK directly instead, so the project driver is silently ignored and
# the HMI fails with "Extension not supported: VK_KHR_swapchain". The libkrun/krun tap build is fine.
#
# Usage: check_libkrun.sh [--quiet]     exit 0 = OK, 1 = wrong libkrun, 2 = cannot determine
# Test override: BMW_LIBKRUN_LIB=/path/to/libkrun.1.dylib
set -uo pipefail
quiet=false
[[ "${1:-}" == --quiet ]] && quiet=true
say() { $quiet || printf '%s\n' "$*" >&2; }

library="${BMW_LIBKRUN_LIB:-}"
if [[ -z "$library" ]]; then
    krunkit_path="$(command -v krunkit 2>/dev/null || true)"
    if [[ -z "$krunkit_path" ]]; then say 'krunkit is not installed.'; exit 2; fi
    krunkit_real="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$krunkit_path")"
    krunkit_links="$(otool -L "$krunkit_real" 2>/dev/null || true)"
    library="$(awk '$1 ~ /\/libkrun\.[0-9]+\.dylib$/ {print $1; exit}' <<< "$krunkit_links")"
    if [[ -z "$library" ]]; then say "Could not find the libkrun that $krunkit_real uses."; exit 2; fi
fi
if [[ ! -f "$library" ]]; then say "libkrun library not found: $library"; exit 2; fi

# Capture first: "otool | grep -q" would trip pipefail through SIGPIPE.
links="$(otool -L "$library" 2>/dev/null || true)"
if [[ "$links" == *libvirglrenderer* ]]; then
    exit 0
fi

if ! $quiet; then
    version="$(brew list --versions libkrun 2>/dev/null || true)"
    cat >&2 <<EOF

The installed libkrun cannot load this project's graphics driver.
  libkrun: $library ${version:+($version)}
  It does not link a separate libvirglrenderer.1.dylib (it uses MoltenVK directly), so the project's
  driver in private/host-runtime is ignored and the HMI would fail with
  "Extension not supported: VK_KHR_swapchain" ("HMI output is taking longer than expected").
  This is the libkrun from Homebrew's core tap. This project needs the libkrun/krun tap build.

To fix it (this replaces Homebrew's libkrun; stop other libkrun-based VMs first):
  bash stop.sh --vm
  brew uninstall --ignore-dependencies libkrun
  brew install libkrun/krun/libkrun libkrun/krun/virglrenderer-krun
  (If Homebrew says the tap is untrusted: brew trust --formula libkrun/krun/libkrun libkrun/krun/virglrenderer-krun)
Then run this script again.
EOF
fi
exit 1
