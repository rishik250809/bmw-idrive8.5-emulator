import os
import pathlib
import sys
import tarfile


destination = pathlib.Path(os.environ.get('BMW_INSTALL_ROOT', '/srv/bmw-idrive85')).resolve()
if (destination / 'state').exists() or (destination / 'driver').exists():
    raise SystemExit('Runtime already installed. Refusing to overwrite coding or saved state.')
destination.mkdir(parents=True, exist_ok=True)
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    members = archive.getmembers()
    names = set()
    link_paths = set()
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] not in ('state', 'driver'):
            raise SystemExit(f'Unsafe archive path: {member.name}')
        canonical = path.as_posix()
        if canonical in names or not (member.isfile() or member.isdir() or member.issym()):
            raise SystemExit(f'Duplicate or unsupported archive entry: {member.name}')
        names.add(canonical)
        member.name = canonical
        if member.issym():
            link_paths.add(path)
    for member in members:
        if any(parent in link_paths for parent in pathlib.PurePosixPath(member.name).parents):
            raise SystemExit(f'Archive entry beneath symlink: {member.name}')
    for member in members:
        if not member.issym():
            archive.extract(member, destination, filter='fully_trusted')
    for member in members:
        if member.issym():
            archive.extract(member, destination, filter='fully_trusted')
for profile in ('g26', 'g80lci-m', 'ix20', 'ix40'):
    for area in ('public', 'personal', 'mock'):
        target = destination / 'state/profiles' / profile / area
        if not target.resolve().is_relative_to(destination):
            raise SystemExit(f'Unsafe writable profile path: {target}')
        target.mkdir(parents=True, exist_ok=True)
marker = destination / 'state/profiles/.initialized'
if not marker.resolve().is_relative_to(destination):
    raise SystemExit('Unsafe profile initialization marker')
marker.touch()
for area in ('public', 'personal'):
    target = destination / 'state/rootfs/var/opt' / area
    if not target.resolve().is_relative_to(destination):
        raise SystemExit(f'Unsafe writable runtime path: {target}')
    target.mkdir(parents=True, exist_ok=True)
logs = destination / 'state/logs'
if not logs.resolve().is_relative_to(destination):
    raise SystemExit('Unsafe logs directory')
logs.mkdir(exist_ok=True)
print('Imported extracted runtime; donor personalization and SSH keys are not included.')
