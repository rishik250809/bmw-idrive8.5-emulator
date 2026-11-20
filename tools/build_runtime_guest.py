"""Build the emulator runtime inside the VM from verified BMW package artifacts.

Usage (root, inside the VM):
  python3 build_runtime_guest.py PLAN.json STAGING_DIR SOURCE_DIR [--into /srv/bmw-idrive85]

STAGING_DIR holds one file per plan item, named <sgbm>.gz (the artifact exactly
as listed in BMW's manifest). Each is re-checked by SHA-256, unpacked to an
ext4 image (Android sparse images are converted first), mounted read-only and
copied into the runtime layout. Private keys, SSH material and logs are left
out, matching the curated archive. Staged files are deleted after use.
"""
import argparse
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument('plan', type=pathlib.Path)
parser.add_argument('staging', type=pathlib.Path)
parser.add_argument('source', type=pathlib.Path, help='package source folder (runtime/ and config/ live here)')
parser.add_argument('--into', type=pathlib.Path, default=pathlib.Path('/srv/bmw-idrive85'))
options = parser.parse_args()

sys.path.insert(0, str(options.source / 'runtime'))
from sparse_image import convert  # noqa: E402

plan = json.loads(options.plan.read_text())
install = options.into.resolve()
state = install / 'state'
if state.exists():
    raise SystemExit(f'{state} already exists; refusing to overwrite coding or saved state.')

EXCLUDED = ('root', 'home', 'dev', 'proc', 'sys', 'run', 'tmp', 'mnt', 'lost+found',
            'var/opt', 'var/log', 'var/ncd', 'etc/ssh', 'opt/ivi/rootfs/etc/ssh')
PRIVATE_SUFFIXES = ('.key', '.p12', '.pfx')
PRIVATE_PREFIXES = ('ssh_host_', 'id_rsa', 'id_ed25519', 'id_ecdsa')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(8 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def unpack(item, staged, image):
    with gzip.open(staged, 'rb') as source, image.open('wb') as destination:
        if item['sparse']:
            convert(source, destination)
        else:
            shutil.copyfileobj(source, destination, 8 << 20)


def validate_destination(target):
    if not target.resolve().is_relative_to(state.resolve()):
        raise ValueError(f'Partition destination escapes runtime: {target}')
    for path in (target, *target.parents):
        if path == state:
            break
        if path.is_symlink():
            raise ValueError(f'Partition destination beneath symlink: {target}')


def copy_tree(image, target):
    validate_destination(target)
    mountpoint = pathlib.Path(tempfile.mkdtemp(prefix='bmw-import-mnt-'))
    subprocess.run(['mount', '-o', 'loop,ro,noload', str(image), str(mountpoint)], check=True)
    try:
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run(['cp', '-a', f'{mountpoint}/.', str(target)], check=True)
    finally:
        subprocess.run(['umount', str(mountpoint)], check=True)
        mountpoint.rmdir()


def copy_archive(staged, target):
    validate_destination(target)
    with tarfile.open(staged, 'r:gz') as archive:
        members = archive.getmembers()
        names, links = set(), set()
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if (not path.parts or path.parts[0] != 'payload' or path.is_absolute()
                    or '..' in path.parts or path.as_posix() in names
                    or (len(path.parts) == 1 and not member.isdir())
                    or not (member.isdir() or member.isfile() or member.issym())):
                raise ValueError(f'Unsafe extracted archive member: {member.name}')
            member.name = path.as_posix()
            names.add(member.name)
            if member.issym():
                links.add(path)
        for member in members:
            if any(parent in links for parent in pathlib.PurePosixPath(member.name).parents):
                raise ValueError(f'Archive member beneath symlink: {member.name}')
        with tempfile.TemporaryDirectory(dir='/var/tmp') as directory:
            for symlinks in (False, True):
                for member in members:
                    if member.issym() == symlinks:
                        archive.extract(member, directory, filter='fully_trusted')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                raise ValueError(f'Unsafe partition destination: {target}')
            shutil.copytree(pathlib.Path(directory) / 'payload', target, symlinks=True, dirs_exist_ok=True)


def scrub_rootfs(rootfs):
    for relative in EXCLUDED:
        path = rootfs / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    removed = 0
    for directory, _, files in os.walk(rootfs):
        for name in files:
            if name.lower().endswith(PRIVATE_SUFFIXES) or name.startswith(PRIVATE_PREFIXES):
                pathlib.Path(directory, name).unlink()
                removed += 1
    return removed


# Root filesystem first, then the partitions that live under it.
order = {'rootfs': 0, 'rootfs/opt/ivi': 1, 'rootfs/opt/ivi/rootfs': 1,
         'rootfs/opt/ext': 2, 'rootfs/opt/ext/rootfs': 2}
items = sorted(plan['items'], key=lambda item: order.get(item['destination'], 9))
work = pathlib.Path(tempfile.mkdtemp(prefix='bmw-import-', dir='/var/tmp'))
try:
    for item in items:
        staged = options.staging / f"{item['sgbm']}.gz"
        print(f"{item['sgbm'].upper()} {item['role']}: checking", flush=True)
        if sha256(staged) != item['sha256']:
            raise SystemExit(f'{staged.name}: SHA-256 changed during transfer')
        image = work / f"{item['sgbm']}.ext4"
        print(f"{item['sgbm'].upper()} unpacking", flush=True)
        if item.get('format') == 'tar':
            copy_archive(staged, state / item['destination'])
            staged.unlink()
        elif item.get('format') == 'ext4':
            copy_tree(staged, state / item['destination'])
            staged.unlink()
        else:
            unpack(item, staged, image)
            staged.unlink()
            copy_tree(image, state / item['destination'])
            image.unlink()
finally:
    shutil.rmtree(work, ignore_errors=True)

removed = scrub_rootfs(state / 'rootfs')
print(f'Removed {removed} private key/certificate files and runtime-only folders.')
if plan.get('source_kind') == 'extracted':
    executable = state / 'rootfs/opt/ivi/rootfs/usr/bin/Hmi8'
    if sha256(executable) != '9913ca569bfc78eb2b6174d0660855dc324a7b261667e0b9d20bc9352308062c':
        raise SystemExit('Imported Hmi8 does not match the tested build.')

# Version check: brand and car data must belong to the same HMI build as Hmi8.
hmi_build = None
for candidate in (state / 'resources').glob('*/rootfs/hmi_version'):
    build = candidate.read_text().strip()
    hmi_build = hmi_build or build
    if build != hmi_build:
        raise SystemExit(f'HMI data build mismatch: {candidate.parent.parent.name} is {build}, expected {hmi_build}')
if plan.get('source_kind') == 'extracted' and hmi_build != 'ID8_2507-2446ZC':
    raise SystemExit(f'Unsupported extracted resource build: {hmi_build}')

# Starting coding and per-car profiles.
config = options.source / 'config'
imported = {item['sgbm'] for item in plan['items']}
(state / 'config/presets').mkdir(parents=True, exist_ok=True)
(state / 'config/profiles').mkdir(parents=True, exist_ok=True)
ix40 = json.loads((config / 'full-ix40.json').read_text())
(state / 'config/coding.json').write_text(json.dumps(ix40, indent=2) + '\n')
(state / 'config/profiles/ix40.json').write_text(json.dumps(ix40, indent=2) + '\n')
(state / 'config/profiles/g26.json').write_text(json.dumps(json.loads((config / 'full-g26.json').read_text()), indent=2) + '\n')
shutil.copy(config / 'full-ix20.json', state / 'config/profiles/ix20.json')
shutil.copy(config / 'full-ix40.json', state / 'config/profiles/ix40.json')
if {'a6b2', 'a691'} <= imported:
    shutil.copy(config / 'full-g80.json', state / 'config/profiles/g80-m.json')
for profile in ('g26', 'g80lci-m', 'ix20', 'ix40'):
    for area in ('public', 'personal', 'mock'):
        (state / 'profiles' / profile / area).mkdir(parents=True, exist_ok=True)
(state / 'profiles/.initialized').touch()
for area in ('public', 'personal'):
    (state / 'rootfs/var/opt' / area).mkdir(parents=True, exist_ok=True)
(state / 'logs').mkdir(exist_ok=True)
(state / 'imported-packages.json').write_text(json.dumps(
    {'release': plan['version'], 'hmi_build': hmi_build,
     'packages': [{k: item[k] for k in ('sgbm', 'version', 'role', 'artifact', 'sha256')} for item in plan['items']]},
    indent=2) + '\n')
print(f'Runtime built in {state} (release {plan["version"]}, HMI build {hmi_build}).')
