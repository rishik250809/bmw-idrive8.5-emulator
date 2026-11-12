"""Optional, local-only runner for a single compatible OAP/RHMI test app."""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

sys.path.insert(0, '/opt/hmi8-container')
import mock_state
import pdc_sim

STATE = pathlib.Path('/srv/hmi8-lab')
ROOT = STATE / 'rootfs'
SOURCE = pathlib.Path(__file__).resolve().parent
UID = 4017223
NODE = '/opt/oap/rootfs/usr/bin/node'
NODE_HASH = 'fdbf88b24bce95b76c768d2dd76805ebde9d37f5034ca2f92abbbe203267cf48'
HMI_HASH = '9913ca569bfc78eb2b6174d0660855dc324a7b261667e0b9d20bc9352308062c'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unpack(archive, destination, prefix, links=False):
    """Extract a bounded private archive into a new directory, without traversal."""
    with tarfile.open(archive) as stream:
        members = [m for m in stream.getmembers() if pathlib.PurePosixPath(m.name).parts[:1] == (prefix,)]
        names, symlinks, total = set(), set(), 0
        for member in members:
            path = pathlib.PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or path in names
                    or not (member.isdir() or member.isfile() or (links and member.issym()))):
                raise ValueError('Invalid archive member: ' + member.name)
            names.add(path)
            if member.issym():
                symlinks.add(path)
            total += member.size
        if not members or len(members) > 20000 or total > 1024 ** 3:
            raise ValueError('Empty or oversized app/runtime archive')
        if any(parent in symlinks for path in names for parent in path.parents):
            raise ValueError('Archive contains a path beneath a symlink')
        for member in sorted(members, key=lambda m: len(pathlib.PurePosixPath(m.name).parts)):
            relative = pathlib.PurePosixPath(member.name).parts[1:]
            if not relative:
                continue
            target = destination.joinpath(*relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.issym():
                target.symlink_to(member.linkname)
            else:
                with stream.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)


def profile_home():
    _, directory = mock_state.active_directory(STATE)
    home = directory / 'oap'
    home.mkdir(parents=True, exist_ok=True)
    return home


def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def identity(pid):
    try:
        fields = pathlib.Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def alive(record):
    return record and identity(record['pid']) is not None and identity(record['pid']) == record['started']


def stop(home):
    record = load(home / 'running.json', {})
    for name in ('app', 'broker'):
        child = record.get(name)
        if not alive(child):
            continue
        os.killpg(child['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        if alive(child):
            os.killpg(child['pid'], signal.SIGKILL)
    (home / 'running.json').unlink(missing_ok=True)
    return {'running': False}


def import_runtime(archive):
    if digest(ROOT / 'opt/ivi/rootfs/usr/bin/Hmi8') != HMI_HASH:
        raise ValueError('Unsupported Hmi8 build')
    destination = ROOT / 'opt/oap/rootfs'
    if destination.is_dir():
        if digest(destination / 'usr/bin/node') != NODE_HASH:
            raise ValueError('Existing OAP runtime does not match the tested build')
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.oap-import-') as temp:
            staging = pathlib.Path(temp) / 'rootfs'
            staging.mkdir()
            unpack(archive, staging, 'payload', links=True)
            if digest(staging / 'usr/bin/node') != NODE_HASH:
                raise ValueError('Unsupported OAP Node runtime')
            staging.rename(destination)
    for path in (destination / 'usr/bin').iterdir():
        if path.is_file() and path.stat().st_size:
            path.chmod(0o755)
    libraries = destination / 'usr/lib'
    for path in libraries.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.stat().st_size == 0:
            candidates = [p for p in path.parent.glob(path.name + '*')
                          if p != path and p.is_file() and p.stat().st_size]
            if not candidates and path.suffix == '.node':
                candidates = [p for p in (libraries / path.name, ROOT / 'usr/lib' / path.name)
                              if p.is_file() and p.stat().st_size]
            if len(candidates) == 1:
                path.unlink()
                path.symlink_to(os.path.relpath(candidates[0], path.parent))
    return {'runtime_imported': True}


def install(archive, home):
    process = pdc_sim.hmi_process()
    root = process / 'root'
    parent = root / 'var/opt/public/oap'
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent, prefix='.app-import-') as temp:
        staging = pathlib.Path(temp) / 'app'
        staging.mkdir()
        unpack(archive, staging, 'app')
        description = load(staging / 'rhmiData/application.json')['application']
        package = load(staging / 'source/package.json')
        entries = description.get('entryPoints', [])
        if len(entries) != 1 or not isinstance(description.get('appletId'), int):
            raise ValueError('This experimental runner supports one entry point per app')
        if not (staging / 'source/javascript/app.js').is_file():
            raise ValueError('Missing app JavaScript entry point')
        sdk = staging / 'source/node_modules/oap-sdk'
        if load(sdk / 'package.json', {}).get('firmwareBundleSha256') != 'da979a4ea8a5e1034e2068d84eaaaa45ff9dbab84ea3cf59feb554450cc71dcf':
            raise ValueError('Build the app with the matching local ARM SDK first')
        config_path = staging / 'source/config/base.json'
        config = load(config_path, {})
        config.update(joynrLoadingEnabled=False, rhmiLoadingEnabled=True,
                      entryPointLoadingEnabled=True, mguGeneration=22, pu='Mainline')
        config.setdefault('someip', {})['instanceIdOffset'] = 4005000
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_text(json.dumps(config))
        schemas = ROOT / 'opt/oap/rootfs/usr/lib/oap-mainapp/src-gen/someip'
        shutil.copytree(schemas, staging / 'someipInterfaces', dirs_exist_ok=True)
        destination = parent / ('oap_' + str(UID))
        stop(home)
        if destination.exists():
            if (destination / 'data').is_dir():
                shutil.copytree(destination / 'data', staging / 'data', dirs_exist_ok=True)
            backup = parent / ('.oap-backup-' + str(time.time_ns()))
            destination.rename(backup)
        (staging / 'data').mkdir(exist_ok=True)
        for path in (staging, *staging.rglob('*')):
            os.chown(path, UID, UID)
        staging.rename(destination)
        info = {'id': package['name'], 'title': load(destination / 'meta.json', {}).get('title', package['name']),
                'appRoot': '/var/opt/public/oap/oap_' + str(UID), 'uid': UID,
                'appletId': description['appletId'], 'uuid': description['uuid']}
        mock_state.atomic_write(home / 'installed.json', json.dumps(info))
    return info


def spawn(process, uid, arguments, log_path):
    original = pdc_sim.hmi_environment(process)
    environment = {'PATH': '/opt/oap/rootfs/usr/bin:/usr/bin:/bin', 'HOME': '/var/opt/public/oap',
                   'INSTANCE_TYPE': 'HU', 'VIN': '0000000000H000007', 'LC_ALL': 'C',
                   'NODE_PATH': '/opt/oap/rootfs/usr/lib:/usr/lib',
                   'LD_LIBRARY_PATH': '/opt/oap/rootfs/usr/lib:/opt/ivi/rootfs/usr/lib:/opt/ext/rootfs/usr/lib:/usr/lib:/lib',
                   'LD_PRELOAD': '/usr/lib/libfakecod.so.0', 'FLAGS_CONFIG': original['FLAGS_CONFIG'],
                   'LOAD_DEFAULTS': '1', 'ECU': 'mgu_02_a', 'DLT_USER_IPC_PATH': '/run/dlt-files'}
    # PID entry is essential: /proc was mounted inside Hmi8's PID namespace.
    # Without it Node's memoryUsage() fails with uv_resident_set_memory ENOENT.
    inner = ['chroot', '--userspec=%d:%d' % (uid, uid), '--groups=5001,5002,5512,4010201',
             str(ROOT), '/usr/bin/env', '-i'] + [k + '=' + v for k, v in environment.items()] + arguments
    script = ("import os,subprocess,sys; r='/srv/hmi8-lab/rootfs';"
              "subprocess.run(['mount','--make-rprivate','/'],check=True);"
              "subprocess.run(['mount','--bind',r+'/opt/oap/rootfs/etc/oap',r+'/etc/oap'],check=True);"
              "os.execvp('chroot',sys.argv[1:])")
    command = ['nsenter', '--target', process.name, '--mount', '--net', '--pid',
               'unshare', '--mount', 'python3', '-c', script] + inner
    with log_path.open('w') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {'pid': child.pid, 'started': identity(child.pid)}


def start(home):
    process = pdc_sim.hmi_process()
    info = load(home / 'installed.json')
    if not info:
        raise ValueError('Install a compatible app first')
    if digest(ROOT / NODE.lstrip('/')) != NODE_HASH or digest(ROOT / 'opt/ivi/rootfs/usr/bin/Hmi8') != HMI_HASH:
        raise ValueError('Unsupported OAP/Hmi8 firmware combination')
    stop(home)
    root = process / 'root'
    (ROOT / 'etc/oap').mkdir(exist_ok=True)
    variant = root / 'run/etc/variant.env'
    if not variant.exists():
        variant.parent.mkdir(parents=True, exist_ok=True)
        variant.write_text('ECU=MGU_02_L\nHWVAR=MGU_02_L\nREV=1\n')
    runtime = ROOT / 'opt/lab-oap'
    supervisor = runtime / 'supervisor'
    (supervisor / 'config').mkdir(parents=True, exist_ok=True)
    (supervisor / 'node_modules').mkdir(exist_ok=True)
    shutil.copy2(SOURCE / 'broker.js', supervisor / 'broker.js')
    shutil.copytree(root / info['appRoot'].lstrip('/') / 'source/node_modules/oap-sdk',
                    supervisor / 'node_modules/oap-sdk', dirs_exist_ok=True)
    shutil.copytree(ROOT / 'opt/oap/rootfs/usr/lib/oap-mainapp/src-gen/someip',
                    runtime / 'someipInterfaces', dirs_exist_ok=True)
    (supervisor / 'package.json').write_text(json.dumps({'name': 'emulator-oap-supervisor', 'version': '1.0.0',
                                                     'oapAppSettings': {'loggingId': 'OLAB'}}))
    (supervisor / 'config/base.json').write_text(json.dumps({'joynrLoadingEnabled': False,
        'mguGeneration': 22, 'pu': 'Mainline', 'appFolders': {'mainAppSomeipInterfaces': 'someipInterfaces'}}))
    native_home = root / 'var/opt/public/oap/oap_4017221/data'
    native_home.mkdir(parents=True, exist_ok=True)
    os.chown(native_home, 4017221, 4017221)
    ready = native_home / 'emulator-oap-ready.json'
    ready.unlink(missing_ok=True)
    configuration = {**info, 'schemaDir': '/opt/lab-oap/someipInterfaces',
                     'readyFile': '/var/opt/public/oap/oap_4017221/data/emulator-oap-ready.json'}
    (supervisor / 'runner.json').write_text(json.dumps(configuration))
    record = {'hmi_pid': process.name, 'hmi_started': identity(int(process.name))}
    record['broker'] = spawn(process, 4017221, [NODE, '/opt/lab-oap/supervisor/broker.js',
                                               '/opt/lab-oap/supervisor/runner.json'], home / 'broker.log')
    mock_state.atomic_write(home / 'running.json', json.dumps(record))
    deadline = time.monotonic() + 12
    while not ready.exists():
        if not alive(record['broker']) or time.monotonic() >= deadline:
            stop(home)
            raise RuntimeError('OAP supervisor did not become ready; see ' + str(home / 'broker.log'))
        time.sleep(0.1)
    domain = load(ready)['domain']
    record['app'] = spawn(process, UID, [NODE, '--expose-gc', info['appRoot'] + '/source/javascript/app.js',
                         '--entryPointDomain', domain, '--startReason', 'UNKNOWN'], home / 'app.log')
    mock_state.atomic_write(home / 'running.json', json.dumps(record))
    deadline = time.monotonic() + 15
    while True:
        text = (home / 'app.log').read_text(errors='replace')
        if ' INITIALIZED' in text:
            return {'running': True, 'title': info['title'], 'entry_domain': domain}
        if not alive(record['app']) or time.monotonic() >= deadline:
            stop(home)
            raise RuntimeError('App did not initialize; see ' + str(home / 'app.log'))
        time.sleep(0.1)


def status(home):
    record = load(home / 'running.json', {})
    return {'installed': load(home / 'installed.json'), 'app_running': bool(alive(record.get('app'))),
            'broker_running': bool(alive(record.get('broker'))), 'logs': str(home)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['import-runtime', 'install', 'start', 'stop', 'status'])
    parser.add_argument('archive', nargs='?', type=pathlib.Path)
    args = parser.parse_args()
    home = profile_home()
    if args.action in ('import-runtime', 'install') and args.archive is None:
        parser.error('Archive required')
    if args.action == 'import-runtime':
        result = import_runtime(args.archive)
    elif args.action == 'install':
        result = install(args.archive, home)
    elif args.action == 'start':
        result = start(home)
    elif args.action == 'stop':
        result = stop(home)
    else:
        result = status(home)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
