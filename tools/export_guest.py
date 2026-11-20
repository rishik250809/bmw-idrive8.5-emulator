import gzip
import io
import json
import pathlib
import sys
import tarfile


state = pathlib.Path('/srv/hmi8-gpu-session')
driver = pathlib.Path('/srv/hmi8-gpu-driver')
packages = (
    'swfk_0000a6b0_007_023_004', 'swfk_0000a6b1_007_023_004',
    'swfk_0000aed4_007_023_004', 'swfk_0000a6b2_007_023_004',
    'swfk_0000a691_007_023_004',
)
excluded = (
    'rootfs/root', 'rootfs/home', 'rootfs/dev', 'rootfs/proc', 'rootfs/sys',
    'rootfs/run', 'rootfs/tmp', 'rootfs/mnt', 'rootfs/lost+found',
    'rootfs/var/opt', 'rootfs/var/log', 'rootfs/var/ncd',
    'rootfs/etc/ssh', 'rootfs/opt/ivi/rootfs/etc/ssh',
)
statistics = {'regular_files': 0, 'uncompressed_file_bytes': 0, 'excluded_paths': []}


def filter_member(member):
    relative = member.name.removeprefix('state/')
    path = pathlib.PurePosixPath(relative)
    private_name = path.name.startswith(('ssh_host_', 'id_rsa', 'id_ed25519', 'id_ecdsa'))
    private_name |= path.suffix.lower() in ('.key', '.p12', '.pfx')
    private_name |= path.name in ('.bash_history', '.zsh_history', '.DS_Store')
    if private_name or any(relative == prefix or relative.startswith(prefix + '/') for prefix in excluded):
        statistics['excluded_paths'].append(member.name)
        return None
    if member.islnk():
        source = state / relative if member.name.startswith('state/') else driver / member.name.removeprefix('driver/')
        member.type = tarfile.REGTYPE
        member.linkname = ''
        member.size = source.stat().st_size
    if not (member.isfile() or member.isdir() or member.issym()):
        statistics['excluded_paths'].append(member.name)
        return None
    if member.isfile():
        statistics['regular_files'] += 1
        statistics['uncompressed_file_bytes'] += member.size
    return member


def add_json(archive, name, document):
    payload = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode()
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    member.mode = 0o600
    archive.addfile(member, io.BytesIO(payload))


with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=1, mtime=0) as compressed:
    with tarfile.open(fileobj=compressed, mode='w|', dereference=False) as archive:
        archive.add(state / 'rootfs', arcname='state/rootfs', filter=filter_member)
        for package in packages:
            archive.add(state / 'resources' / package / 'rootfs',
                        arcname=f'state/resources/{package}/rootfs', filter=filter_member)
        archive.add(driver, arcname='driver', filter=filter_member)
        coding = json.loads((state / 'config/coding.json').read_text())
        if coding.get('SERIES') == 'I20':
            current = 'ix20'
        else:
            current = 'g80-m' if coding.get('HMI_BRAND') == 'bmw_m' else 'g26'
        add_json(archive, 'state/config/coding.json', coding)
        for profile, runtime in (('g26', 'g26'), ('g80-m', 'g80lci-m'), ('ix20', 'ix20')):
            saved = coding if profile == current else json.loads((state / f'config/profiles/{profile}.json').read_text())
            add_json(archive, f'state/config/profiles/{profile}.json', saved)
            add_json(archive, f'state/config/presets/gpu-{profile}.json', saved)
            for filename in ('state.json', 'state.txt'):
                source = state / 'profiles' / runtime / 'mock' / filename
                if source.is_file():
                    archive.add(source, arcname=f'state/profiles/{runtime}/mock/{filename}', filter=filter_member)
pathlib.Path('/tmp/bmw-export-statistics.json').write_text(json.dumps(statistics, indent=2) + '\n')
