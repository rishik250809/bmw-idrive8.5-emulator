#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bash "$here/doctor.sh"
source "$here/scripts/common.sh"
python3 "$ROOT/tools/verify_bundle.py"
if [[ "$(limactl list --format '{{.Status}}' "$VM" 2>/dev/null)" != Running ]]; then
    if grep -Fxq "$VM" <<< "$(limactl list --format '{{.Name}}')"; then
        lima_start "$VM" --tty=false
    else
        lima_start --name="$VM" --tty=false "$ROOT/config/lima.yaml"
    fi
fi
if limactl shell --workdir=/tmp "$VM" -- sudo test -e /srv/bmw-idrive85/state; then
    printf 'Runtime already exists. Use scripts/sync_source.sh for code updates; setup will not overwrite state.\n' >&2
    exit 2
fi
limactl shell --workdir=/tmp "$VM" -- sudo sh -ec '
    if ! command -v docker >/dev/null; then dnf install -y moby-engine docker-cli; fi
    systemctl enable --now docker
    modprobe uinput
    test -c /dev/dri/renderD128
    test -c /dev/uinput
    mkdir -p /srv/bmw-idrive85/source
'
staging=/var/tmp/bmw-idrive85-install
limactl shell --workdir=/tmp "$VM" -- mkdir -p "$staging"
limactl shell --workdir=/tmp "$VM" -- chmod 700 "$staging"
limactl copy "$ROOT/private/runtime.tar.gz" "$VM:$staging/bmw-runtime.tar.gz"
limactl copy "$ROOT/private/tools-image.tar.gz" "$VM:$staging/bmw-tools-image.tar.gz"
limactl copy "$ROOT/tools/import_guest.py" "$VM:$staging/bmw-import.py"
limactl shell --workdir=/tmp "$VM" -- sudo python3 "$staging/bmw-import.py" "$staging/bmw-runtime.tar.gz"
limactl shell --workdir=/tmp "$VM" -- sudo docker load -i "$staging/bmw-tools-image.tar.gz"
python3 "$ROOT/container/updates.py" --write "$ROOT/container/build-version.json"
COPYFILE_DISABLE=1 tar --no-xattrs --exclude='__pycache__' --exclude='*.pyc' -czf "$ROOT/.local/source.tar.gz" \
    -C "$ROOT" Dockerfile .dockerignore LICENSE NOTICE runtime container web config scripts tools
limactl copy "$ROOT/.local/source.tar.gz" "$VM:/tmp/bmw-source.tar.gz"
limactl shell --workdir=/tmp "$VM" -- sudo tar -xzf /tmp/bmw-source.tar.gz -C /srv/bmw-idrive85/source
limactl shell --workdir=/tmp "$VM" -- sudo python3 -c '
import pathlib
for name in ("bmw-runtime.tar.gz", "bmw-tools-image.tar.gz", "bmw-import.py"):
    (pathlib.Path("/var/tmp/bmw-idrive85-install") / name).unlink(missing_ok=True)
(pathlib.Path("/tmp") / "bmw-source.tar.gz").unlink(missing_ok=True)
'
printf 'Installed private runtime. Run: bash start.sh ix40 (default iX xDrive40).\n'
