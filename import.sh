#!/usr/bin/env bash
# Build the emulator runtime from your own BMW packages (PSdZData or a folder of SWFL/SWFK files).
# Usage: bash import.sh /path/to/psdzdata-or-folder [--version 007_023_004]
# See docs/GETTING_BMW_FILES.md for which packages are needed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
source "$here/scripts/common.sh"
source_folder="${1:?Usage: bash import.sh /path/to/extracted-or-packages [--version X_Y_Z]}"
shift
install_root="${BMW_IMPORT_INTO:-/srv/bmw-idrive85}"   # test mode: build somewhere else
plan="$ROOT/.local/import-plan.json"

bash "$here/doctor.sh"
printf '\n== 1/5 Finding and verifying packages\n'
extracted="$(python3 "$ROOT/tools/prepare_extracted.py" "$source_folder" --detect)"
if [[ -n "$extracted" ]]; then
    python3 "$ROOT/tools/prepare_extracted.py" "$extracted" --plan "$plan" "$@"
else
    python3 "$ROOT/tools/find_packages.py" "$source_folder" --plan "$plan" "$@"
fi

printf '\n== 2/5 Preparing the VM (%s)\n' "$VM"
if ! grep -Fxq "$VM" <<< "$(limactl list --format '{{.Name}}')"; then
    lima_start --name="$VM" --tty=false "$ROOT/config/lima.yaml"
elif [[ "$(limactl list --format '{{.Status}}' "$VM")" != Running ]]; then
    lima_start "$VM" --tty=false
fi
if limactl shell --workdir=/tmp "$VM" -- sudo test -e "$install_root/state"; then
    printf '%s/state already exists. This importer never overwrites coding or saved state.\n' "$install_root" >&2
    exit 2
fi
limactl shell --workdir=/tmp "$VM" -- sudo sh -ec '
    if ! command -v docker >/dev/null; then dnf install -y moby-engine docker-cli; fi
    systemctl enable --now docker
    modprobe uinput
    test -c /dev/dri/renderD128
    test -c /dev/uinput
'
staging=/var/tmp/bmw-idrive85-import
limactl shell --workdir=/tmp "$VM" -- sudo sh -c "rm -rf $staging && mkdir -p $staging/source && chmod 700 $staging"

printf '\n== 3/5 Copying verified package files into the VM\n'
python3 - "$plan" <<'PY' > "$ROOT/.local/import-items.tsv"
import json, sys
for item in json.load(open(sys.argv[1]))['items']:
    print('\t'.join([item['container'], item['kind'], item['sgbm'], item['version'], item['artifact'], str(item['size'])]))
PY
while IFS=$'\t' read -r container kind sgbm version artifact size; do
    printf '  %s %s (%d MB)\n' "$(printf %s "$sgbm" | tr a-f A-F)" "$artifact" "$((size / 1000000))"
    if [[ "$kind" == extracted ]]; then
        limactl shell --workdir=/tmp "$VM" -- sudo sh -c "cat > $staging/$sgbm.gz" < "$artifact"
    else
        python3 "$ROOT/tools/stream_artifact.py" "$container" "$kind" "$sgbm" "$version" "$artifact" |
            limactl shell --workdir=/tmp "$VM" -- sudo sh -c "cat > $staging/$sgbm.gz"
    fi
done < "$ROOT/.local/import-items.tsv"
python3 "$ROOT/container/updates.py" --write "$ROOT/container/build-version.json"
COPYFILE_DISABLE=1 tar --no-xattrs --exclude='__pycache__' --exclude='*.pyc' -czf "$ROOT/.local/source.tar.gz" \
    -C "$ROOT" Dockerfile .dockerignore LICENSE NOTICE runtime container web config scripts tools docs/swe_names.tsv
limactl copy "$ROOT/.local/source.tar.gz" "$VM:/tmp/bmw-source.tar.gz"
limactl copy "$plan" "$VM:/tmp/bmw-import-plan.json"
limactl shell --workdir=/tmp "$VM" -- sudo tar -xzf /tmp/bmw-source.tar.gz -C "$staging/source"

printf '\n== 4/5 Building the runtime inside the VM (takes a few minutes)\n'
limactl shell --workdir=/tmp "$VM" -- sudo python3 "$staging/source/tools/build_runtime_guest.py" \
    /tmp/bmw-import-plan.json "$staging" "$staging/source" --into "$install_root"

printf '\n== 5/5 Installing source, graphics driver and tools image\n'
limactl shell --workdir=/tmp "$VM" -- sudo sh -c "mkdir -p $install_root/source && cp -a $staging/source/. $install_root/source/"
if [[ -n "${BMW_IMPORT_INTO:-}" ]]; then
    printf 'Test import finished in %s (driver and tools image skipped).\n' "$install_root"
else
    driver="$ROOT/private/driver.tar.gz"
    [[ -f "$driver" ]] || { printf 'Missing %s (graphics driver download, see README).\n' "$driver" >&2; exit 1; }
    limactl copy "$driver" "$VM:/tmp/bmw-driver.tar.gz"
    limactl shell --workdir=/tmp "$VM" -- sudo tar -xzf /tmp/bmw-driver.tar.gz -C "$install_root"
    if [[ -f "$ROOT/private/tools-image.tar.gz" ]]; then
        limactl copy "$ROOT/private/tools-image.tar.gz" "$VM:/tmp/bmw-tools-image.tar.gz"
        limactl shell --workdir=/tmp "$VM" -- sudo docker load -i /tmp/bmw-tools-image.tar.gz
    else
        limactl shell --workdir=/tmp "$VM" -- sudo docker build -t bmw-idrive85-tools:local "$install_root/source"
        printf 'bmw-idrive85-tools:local\n' > "$ROOT/.local/tools-image-id"
    fi
fi
limactl shell --workdir=/tmp "$VM" -- sudo sh -c "rm -rf $staging /tmp/bmw-source.tar.gz /tmp/bmw-import-plan.json /tmp/bmw-driver.tar.gz /tmp/bmw-tools-image.tar.gz"
if [[ -n "$extracted" ]]; then rm -rf "$ROOT/.local/extracted-import"; fi
printf '\nImport complete. Start with: bash start.sh ix40          (BMW iX xDrive40, experimental)\n                            bash start.sh g26            (G26)\n                            bash start.sh g80-m          (G80 M, if A6B2 and A691 were imported)\n'
