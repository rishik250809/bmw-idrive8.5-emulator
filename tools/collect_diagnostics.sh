#!/usr/bin/env bash
# Read-only diagnostics for "HMI stuck / VK_KHR_swapchain not supported" reports.
# Changes nothing, installs nothing, uploads nothing. Output is also saved to
# diagnostics.txt in the repo folder; paste that file into the GitHub issue.
# Usage (from the repository folder):  bash tools/collect_diagnostics.sh
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VM="${BMW_VM:-bmw-idrive85}"
OUT="$ROOT/diagnostics.txt"
HOST_RUNTIME="$ROOT/private/host-runtime"

section() { printf '\n===== %s =====\n' "$1"; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1; }
guest() { limactl shell --workdir=/tmp "$VM" -- sudo sh -c "$1" 2>&1; }

collect() {
    section 'Mac'
    sw_vers
    printf 'chip: %s\nmodel: %s\nRAM bytes: %s\n' "$(sysctl -n machdep.cpu.brand_string)" "$(sysctl -n hw.model)" "$(sysctl -n hw.memsize)"

    section 'Tool versions'
    run limactl --version
    run krunkit --version
    for formula in lima krunkit libkrun libkrun-efi vulkan-loader libepoxy spirv-tools molten-vk virglrenderer vulkan-tools; do
        brew list --versions "$formula" 2>/dev/null || printf '%s: not installed\n' "$formula"
    done
    printf 'repo commit: %s\n' "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null)"

    section 'Host GPU driver files (expected hashes are in docs/graphics-provenance.json)'
    expected_virgl=5075fb84009e7e1adeaa12d8e84a819814b33b11ba617244229cd73fc5b01d77
    expected_kosmic=40bdce0ac05adc975bf6710742258c61fbf550ea4dacc7c6a802732942b2408c
    for pair in "libvirglrenderer.1.dylib:$expected_virgl" "libvulkan_kosmickrisp.dylib:$expected_kosmic"; do
        name="${pair%%:*}"; want="${pair##*:}"
        if [[ -f "$HOST_RUNTIME/$name" ]]; then
            have="$(shasum -a 256 "$HOST_RUNTIME/$name" | cut -d' ' -f1)"
            [[ "$have" == "$want" ]] && verdict=MATCH || verdict=DIFFERENT
            printf '%s  %s  [%s]\n' "$have" "$name" "$verdict"
        else
            printf 'MISSING  %s\n' "$name"
        fi
    done

    section 'Host GPU driver files: signature, architecture, dependencies, quarantine'
    for name in libvirglrenderer.1.dylib libvulkan_kosmickrisp.dylib; do
        printf '\n--- %s\n' "$name"
        if [[ -f "$HOST_RUNTIME/$name" ]]; then
            codesign -v "$HOST_RUNTIME/$name" 2>&1; printf 'codesign -v exit: %s (0 = valid signature)\n' "$?"
            file "$HOST_RUNTIME/$name"
            xattr -l "$HOST_RUNTIME/$name" 2>&1 | sed 's/^/xattr: /'
            otool -L "$HOST_RUNTIME/$name" 2>&1 | tail -n +2
            # Every non-system dependency must exist, or dyld silently skips this file.
            otool -L "$HOST_RUNTIME/$name" 2>/dev/null | tail -n +2 | awk '{print $1}' | grep -E '^/opt/|^/usr/local/' |
                while read -r dep; do [[ -e "$dep" ]] && printf 'dependency OK:      %s\n' "$dep" || printf 'dependency MISSING: %s\n' "$dep"; done
        else
            printf 'file missing\n'
        fi
    done
    printf '\nGPU-related Homebrew libraries:\n'
    ls -l /opt/homebrew/opt/vulkan-loader/lib/libvulkan.1.dylib /opt/homebrew/opt/libepoxy/lib/libepoxy.0.dylib 2>&1
    printf 'Install origin (poured from bottle vs built from source):\n'
    for formula in krunkit libkrun virglrenderer-krun molten-vk vulkan-loader; do
        receipt="$(ls -d /opt/homebrew/Cellar/$formula/*/INSTALL_RECEIPT.json 2>/dev/null | tail -n 1)"
        if [[ -n "$receipt" ]]; then
            printf '%s: ' "$formula"
            python3 -I - "$receipt" <<'PY' 2>&1
import json, sys
d = json.load(open(sys.argv[1]))
print('poured_from_bottle=%s built_as_bottle=%s homebrew=%s built_on_os=%s' % (
    d.get('poured_from_bottle'), d.get('built_as_bottle'), d.get('homebrew_version'),
    (d.get('built_on') or {}).get('os_version')))
PY
        else
            printf '%s: no install receipt found\n' "$formula"
        fi
    done

    section 'krunkit binary'
    krunkit_real="$(readlink -f "$(command -v krunkit)" 2>/dev/null)"
    printf 'path: %s\n' "$krunkit_real"
    codesign -dv --entitlements - "$krunkit_real" 2>&1 | grep -iE 'flags|Signature|runtime|dyld|hypervisor|library-validation'
    otool -L "$krunkit_real" 2>&1 | tail -n +2 | awk '{print $1}'

    section 'dyld test: which virglrenderer does krunkit load? (runs "krunkit --version" only; the VM is not touched)'
    printf '[with the project override - the expected case: project libvirglrenderer.1.dylib]\n'
    DYLD_PRINT_LIBRARIES=1 DYLD_LIBRARY_PATH="$HOST_RUNTIME" krunkit --version 2>&1 |
        grep -iE 'virgl|vulkan|moltenvk|epoxy|kosmic|krunkit [0-9]|error|cannot|not valid|signature|library not loaded'
    printf '\n[without the override - shows what krunkit falls back to]\n'
    DYLD_PRINT_LIBRARIES=1 krunkit --version 2>&1 |
        grep -iE 'virgl|vulkan|moltenvk|epoxy|kosmic|krunkit [0-9]|error|cannot|signature|library not loaded'

    section 'VM'
    run limactl list
    printf 'Lima pid files:\n'; ls -l "$HOME/.lima/$VM/"*.pid 2>&1
    pgrep -fl 'krunkit|limactl' 2>&1 | cut -c1-140
    krunkit_pid="$(cat "$HOME/.lima/$VM/krunkit.pid" 2>/dev/null)"
    printf 'krunkit pid file: %s\n' "${krunkit_pid:-<none>}"
    if [[ "$krunkit_pid" =~ ^[0-9]+$ ]] && ps -p "$krunkit_pid" >/dev/null 2>&1; then
        printf 'Environment krunkit was started with (DYLD_*/VK_*):\n'
        ps eww -p "$krunkit_pid" 2>/dev/null | tr ' ' '\n' | grep -E '^(DYLD_|VK_)'
        printf 'GPU libraries loaded by krunkit (project path = good, MoltenVK/Cellar virglrenderer = wrong driver):\n'
        lsof -a -p "$krunkit_pid" -d txt -Fn 2>/dev/null | grep -iE 'virgl|kosmic|vulkan|moltenvk|epoxy'
    else
        printf 'krunkit is not running (start the VM with: bash start.sh g80-m, wait 2 minutes, then rerun this script)\n'
    fi

    if [[ "$(limactl list --format '{{.Status}}' "$VM" 2>/dev/null)" == Running ]]; then
        section 'Guest: driver files in /srv/bmw-idrive85/driver'
        guest 'cd /srv/bmw-idrive85/driver && sha256sum venus.json libvulkan_virtio.so angle/libvulkan.so.1 angle/libEGL.so angle/libGLESv2.so 2>&1'

        section 'Guest: container and images'
        guest 'docker ps -a --format "{{.Names}} {{.Image}} {{.Status}}"; docker images --no-trunc --format "{{.Repository}}:{{.Tag}} {{.ID}}"'

        section 'Guest: HMI result'
        guest 'cat /srv/bmw-idrive85/state/logs/mesa-g26.exit-code.txt; ls -l --time-style=full-iso /srv/bmw-idrive85/state/logs/mesa-g26.exit-code.txt; date'
        guest 'printf "VK_KHR_swapchain error lines: "; grep -c VK_KHR_swapchain /srv/bmw-idrive85/state/logs/mesa-g26.stderr.txt'
        guest 'grep -E "hmi-compat" /srv/bmw-idrive85/state/logs/mesa-g26.stderr.txt | sort | uniq -c'

        section 'Guest: last 40 lines of HMI stderr'
        guest 'tail -n 40 /srv/bmw-idrive85/state/logs/mesa-g26.stderr.txt'

        section 'Guest: Weston log (last 25 lines)'
        guest 'tail -n 25 /srv/bmw-idrive85/state/logs/mesa-g26.weston.txt'
    else
        printf '\nVM is not running, so guest checks were skipped.\n'
    fi

    section 'Viewer status'
    curl -s --max-time 5 "http://localhost:${BMW_VIEWER_PORT:-5011}/api/status" || printf 'viewer not reachable\n'
    printf '\n'

    section 'Host Vulkan (only if vulkaninfo is already installed; nothing is installed for you)'
    if command -v vulkaninfo >/dev/null 2>&1 && [[ -f "$ROOT/.local/kosmickrisp_icd.json" ]]; then
        DYLD_LIBRARY_PATH="$HOST_RUNTIME" VK_DRIVER_FILES="$ROOT/.local/kosmickrisp_icd.json" \
            vulkaninfo 2>&1 | grep -iE 'deviceName|apiVersion|driverName|VK_KHR_swapchain|VK_EXT_metal_surface' | sort | uniq -c
    else
        printf 'vulkaninfo not installed (optional: brew install vulkan-tools, then rerun)\n'
    fi
}

# Hide the home folder / user name before the report is shared.
collect 2>&1 | sed "s#$HOME#~#g; s#/Users/[^/ ]*#/Users/<user>#g" | tee "$OUT"
printf '\nSaved to %s - paste its contents into the GitHub issue.\n' "$OUT"
