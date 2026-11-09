#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bash "$here/doctor.sh"
source "$here/scripts/common.sh"
profile="${1:-current}"
case "$profile" in current|g26|g80-m) ;; *) echo 'Usage: bash start.sh [g26|g80-m]' >&2; exit 2 ;; esac
python3 "$ROOT/tools/boot_splash.py" stop "$ROOT" "$VIEWER_PORT"
if [[ -f "$ROOT/.local/tunnel.pid" ]]; then
    previous="$(cat "$ROOT/.local/tunnel.pid")"
    if [[ "$previous" =~ ^[0-9]+$ ]] && ps -p "$previous" -o args= | grep -Fq "lima-$VM"; then kill "$previous"; fi
fi
for port in "$VIEWER_PORT" "$SETTINGS_PORT"; do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        printf 'Port %s is in use. Stop the other viewer or set BMW_VIEWER_PORT to a free port pair.\n' "$port" >&2
        exit 1
    fi
done
python3 "$ROOT/tools/boot_splash.py" start "$ROOT" "$VIEWER_PORT" --vm "$VM"
trap 'python3 "$ROOT/tools/boot_splash.py" status "$ROOT" "$VIEWER_PORT" "Startup failed. Check the terminal output and .local/tunnel.log."' ERR
if [[ "${BMW_OPEN_BROWSER:-1}" != 0 ]]; then open "http://localhost:$VIEWER_PORT" 2>/dev/null || true; fi
if [[ "$(limactl list --format '{{.Status}}' "$VM")" == Running ]]; then
    vm_directory="$(limactl list --format '{{.Dir}}' "$VM")"
    krunkit_pid="$(cat "$vm_directory/krunkit.pid" 2>/dev/null || true)"
    loaded_libraries=''
    if [[ "$krunkit_pid" =~ ^[0-9]+$ ]]; then
        loaded_libraries="$(lsof -a -p "$krunkit_pid" -d txt -Fn 2>/dev/null || true)"
    fi
    project_driver="$(python3 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' \
        "$ROOT/private/host-runtime/libvirglrenderer.1.dylib")"
    if ! grep -Fxq "n$project_driver" <<< "$loaded_libraries"; then
        python3 "$ROOT/tools/boot_splash.py" status "$ROOT" "$VIEWER_PORT" 'Restarting the VM with the project GPU driver…'
        printf 'Restarting VM to load the project GPU driver instead of the system driver.\n'
        limactl shell --workdir=/tmp "$VM" -- sudo sync
        limactl stop "$VM"
    fi
fi
if [[ "$(limactl list --format '{{.Status}}' "$VM")" != Running ]]; then lima_start "$VM" --tty=false; fi
limactl shell --workdir=/tmp "$VM" -- true
if ! limactl shell --workdir=/tmp "$VM" -- sh -c 'grep -qw console=hvc0 /proc/cmdline && grep -qw systemd.show_status=1 /proc/cmdline && ! grep -qw quiet /proc/cmdline'; then
    python3 "$ROOT/tools/boot_splash.py" status "$ROOT" "$VIEWER_PORT" 'Enabling the Linux serial boot console (one-time VM restart)…'
    limactl shell --workdir=/tmp "$VM" -- sudo python3 /srv/bmw-idrive85/source/container/mock_state.py configure-boot
    limactl shell --workdir=/tmp "$VM" -- sudo sync
    limactl stop "$VM"
    lima_start "$VM" --tty=false
fi
# Final check: krunkit must really have loaded the project GPU driver. If it did not (for example because
# the installed libkrun links MoltenVK directly), the HMI later fails with "Extension not supported:
# VK_KHR_swapchain" and the viewer only says "taking longer than expected".
krunkit_pid="$(cat "$(limactl list --format '{{.Dir}}' "$VM")/krunkit.pid" 2>/dev/null || true)"
if [[ "$krunkit_pid" =~ ^[0-9]+$ ]]; then
    loaded_libraries="$(lsof -a -p "$krunkit_pid" -d txt -Fn 2>/dev/null || true)"
    project_driver="$(python3 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' \
        "$ROOT/private/host-runtime/libvirglrenderer.1.dylib")"
    if [[ -n "$loaded_libraries" ]] && ! grep -Fxq "n$project_driver" <<< "$loaded_libraries"; then
        python3 "$ROOT/tools/boot_splash.py" status "$ROOT" "$VIEWER_PORT" 'The VM did not load the project GPU driver. See the terminal output.'
        {
            printf '\nThe VM is running, but krunkit did NOT load the project GPU driver:\n  %s\n' "$project_driver"
            printf 'GPU libraries it loaded instead:\n'
            grep -iE 'virgl|kosmic|vulkan|moltenvk' <<< "$loaded_libraries" | sed 's/^n/  /' || true
            printf 'The HMI would fail with "Extension not supported: VK_KHR_swapchain".\n'
            printf 'Check the libkrun build with: bash scripts/check_libkrun.sh\n'
        } >&2
        exit 1
    fi
fi
limactl shell --workdir=/tmp "$VM" -- sudo dmesg --color=never > "$ROOT/.local/linux-dmesg.log.tmp"
mv "$ROOT/.local/linux-dmesg.log.tmp" "$ROOT/.local/linux-dmesg.log"
python3 "$ROOT/tools/boot_splash.py" status "$ROOT" "$VIEWER_PORT" 'Fedora is ready. Preparing BMW HMI…'
limactl shell --workdir=/tmp "$VM" -- sudo env "BMW_TOOLS_IMAGE=${BMW_TOOLS_IMAGE:-$(cat "$ROOT/.local/tools-image-id" 2>/dev/null || echo sha256:d7d89513b9fa8c2631b6ebfbdcf60fa3a38af605ab995440330f4820e10e58e1)}" \
    bash /srv/bmw-idrive85/source/scripts/run_guest.sh "$profile"
python3 "$ROOT/tools/boot_splash.py" stop "$ROOT" "$VIEWER_PORT"
python3 "$ROOT/tools/open_tunnel.py" "$ROOT" "$VM" "$VIEWER_PORT"
trap - ERR
printf 'Viewer: http://localhost:%s\nSettings and mocks: http://localhost:%s\nAllow about one minute for HMI initialization.\n' "$VIEWER_PORT" "$SETTINGS_PORT"
