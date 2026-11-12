"""Prepare private OAP inputs and control the local emulator's test-app runner."""
import argparse
import hashlib
import pathlib
import runpy
import subprocess
import tarfile

from prepare_extracted import archive_tree

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHARED_DRIVE = 'https://mega.nz/folder/fU0nUYRa#B0T0UnUgVRaTKrpcO-1A2A'


def missing(reason, searched):
    raise SystemExit(
        '%s\n\nCustom apps need the ARM OAP partition (SWFL 00009FC7, "oap_fs"). Download it from the shared drive:\n'
        '  %s\nthen unpack it so that this exists:\n'
        '  bmw-files/extracted/oap_fs/rootfs/usr/bin/node\n'
        'and run `bash apps.sh import-extracted` again (or pass the path of your extracted folder).\n'
        'Looked in: %s' % (reason, SHARED_DRIVE, searched))
PRIVATE = ROOT / '.local/custom-apps'


def command(*args):
    subprocess.run([str(value) for value in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['import-extracted', 'install', 'start', 'stop', 'status'])
    parser.add_argument('path', nargs='?', type=pathlib.Path)
    args = parser.parse_args()
    if args.action == 'install' and args.path is None:
        parser.error('Supply the app repository directory')
    if args.action == 'import-extracted' and args.path is None:
        args.path = ROOT / 'bmw-files/extracted'   # the same default location install.sh uses
    import os
    vm = os.environ.get('BMW_VM', 'bmw-idrive85')
    guest = ['limactl', 'shell', '--workdir=/tmp', vm, '--', 'sudo', 'docker']
    PRIVATE.mkdir(parents=True, exist_ok=True)
    # Runtime source is mounted read-only into the container; sync new helpers
    # without restarting Hmi8 or disturbing the viewer.
    if args.action != 'status':
        command('bash', ROOT / 'scripts/sync_source.sh')
    action, archive = args.action, None
    if action == 'import-extracted':
        searched = args.path.expanduser().resolve()
        source = searched
        candidates = [source, source / 'oap_fs/rootfs', source / 'extracted/oap_fs/rootfs']
        source = next((p for p in candidates if (p / 'usr/bin/node').is_file()), None)
        if source is None:
            missing('The oap_fs folder was not found.', searched)
        if hashlib.sha256((source / 'usr/bin/node').read_bytes()).hexdigest() != 'fdbf88b24bce95b76c768d2dd76805ebde9d37f5034ca2f92abbbe203267cf48':
            missing('This oap_fs has an unsupported Node.js runtime (only release 007_023_004 is supported).', searched)
        bundled = source / 'usr/lib/oap-mainapp/preinstalled_apps/oap-cdc-2018@3.43.71.tgz'
        if not bundled.is_file():
            missing('The OAP SDK bundle (oap-cdc-2018@3.43.71.tgz) is missing from this oap_fs.', searched)
        with tarfile.open(bundled) as stream:
            member = stream.getmember('bundle.js')
            if not member.isfile() or member.size > 10 * 1024 * 1024:
                raise ValueError('Invalid SDK bundle')
            bundle = PRIVATE / 'cdc-bundle.js'
            bundle.write_bytes(stream.extractfile(member).read())
        command('node', ROOT / 'tools/oap_sdk.js', bundle, PRIVATE / 'sdk')
        archive = PRIVATE / 'oap-runtime.tar.gz'
        archive_tree(source, archive, {}, False)
        action = 'import-runtime'
    elif action == 'install':
        repository = args.path.expanduser().resolve()
        sdk = PRIVATE / 'sdk'
        if not (sdk / 'index.js').is_file():
            raise SystemExit('Run apps.sh import-extracted first')
        build = repository / 'tools/build.py'
        if not build.is_file():
            raise SystemExit('Expected a compatible app repository with tools/build.py')
        # The selected repository is user-supplied code. Its build runs locally;
        # SDK-bearing results go into this project's ignored private directory.
        namespace = runpy.run_path(str(build))
        archive = namespace['build'](sdk, PRIVATE / 'build')
    if archive:
        command('limactl', 'copy', archive, vm + ':/tmp/bmw-oap-input.tar.gz')
        command(*guest, 'cp', '/tmp/bmw-oap-input.tar.gz', 'bmw-idrive85:/tmp/bmw-oap-input.tar.gz')
    command(*guest, 'exec', 'bmw-idrive85', 'python3', '/mnt/hmi8-lab/oap/app_runner.py', action,
            *(['/tmp/bmw-oap-input.tar.gz'] if archive else []))


if __name__ == '__main__':
    main()
