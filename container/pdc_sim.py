"""Park Distance Control simulator for the native Hmi8 PARKING page.

Runs two helpers next to the firmware Hmi8 process, inside its mount and network
namespaces:

* ``parkmenu_server``: a small vsomeip 3.5 application that offers the parking
  SOME/IP services Hmi8/DidUi expect from the parking ECU (ParkmenuAndLayout
  0x3059, ParkDistanceInformationProvision 0x3020, PDCFunctions 0x300e and a
  few zero-filled companions).  Interface definitions come from the firmware's
  ``oap_fs/etc/oap/someip/someip-interfaces.json``.
* ``DidUi``: the firmware's parking/assist-view renderer, started with its JSON
  codings.  Optionally patched to play one of its built-in demo parking scenes.

State is written to ``/tmp/pdc.txt`` inside Hmi8's namespace and polled by the
server: ``<on> <layout> f1..f8 r1..r8 l1..l6 r1..r6``.
"""
import json
import math
import os
import pathlib
import re
import shutil
import signal
import struct
import subprocess
import tarfile
import threading
import time
import urllib.request

STATE = pathlib.Path('/srv/hmi8-lab')
HOME = STATE / 'pdc'
SOURCE = pathlib.Path('/mnt/hmi8-lab/pdc/parkmenu_server.cpp')
VIEW_SOURCE = SOURCE.with_name('parking_view.cpp')
VSOMEIP_URL = 'https://codeload.github.com/COVESA/vsomeip/tar.gz/refs/tags/3.5.0'
ROOT = STATE / 'rootfs'
LOADER = '/opt/lab-ubuntu/lib/ld-linux-aarch64.so.1'
DEMO_PATCH_ADDRESS = 0xa307c0           # mov w3,#-1 -> default of did.sab.demo.parking.sceneNumber
DEMO_PATCH_ORIGINAL = 0x12800003
DIDUI_SHA256 = '485edcaf1ad9cbec26cf25a8a72977e58f87b443dfcb58fd85ab1bf451545c0a'

LAYOUTS = {
    0: 'Park Distance Control only',
    1: 'Park Distance Control (Parking Assistant)',
    2: 'Rear view only',
    3: 'Rear view + sensors',
    4: 'Rear view + sensors (Assistant)',
    5: 'Park View (Vision)',
    6: 'Park View (Assistant)',
    7: 'Panorama view',
}


def default_state():
    return {'on': True, 'layout': 0, 'zoom': 1.25, 'front': [255] * 8, 'rear': [255] * 8,
            'left': [255] * 6, 'right': [255] * 6}


def validate(document, base=None):
    state = dict(base or default_state())
    if 'on' in document:
        state['on'] = bool(document['on'])
    if 'layout' in document:
        layout = int(document['layout'])
        if layout not in LAYOUTS:
            raise ValueError('Unknown parkmenu layout')
        state['layout'] = layout
    if 'zoom' in document:
        zoom = float(document['zoom'])
        if not math.isfinite(zoom) or not 1.0 <= zoom <= 1.5:
            raise ValueError('Car size must be between 1.0 and 1.5')
        state['zoom'] = zoom
    for name, count in (('front', 8), ('rear', 8), ('left', 6), ('right', 6)):
        if name in document:
            values = document[name]
            if not isinstance(values, list) or len(values) != count:
                raise ValueError(f'{name}: {count} values required')
            state[name] = [max(0, min(255, int(value))) for value in values]
    return state


def hmi_process():
    found = []
    for path in pathlib.Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if b'/opt/ivi/rootfs/usr/bin/Hmi8' in (path / 'cmdline').read_bytes().split(b'\0'):
                found.append(path)
        except OSError:
            pass
    if len(found) != 1:
        raise RuntimeError('Hmi8 is not running (or is ambiguous); start the HMI first')
    return found[0]


def hmi_environment(process):
    return dict(pair.decode(errors='replace').split('=', 1)
                for pair in (process / 'environ').read_bytes().split(b'\0') if b'=' in pair)


class PdcSimulator:
    def __init__(self):
        self.lock = threading.Lock()
        self.children = {}
        self.hmi_pid = None
        self.mode = 'stopped'
        self.error = None
        HOME.mkdir(parents=True, exist_ok=True)
        try:
            self.state = validate(json.loads((HOME / 'state.json').read_text()))
        except (OSError, ValueError, TypeError, KeyError):
            self.state = default_state()

    # ---------- build ----------
    def build(self):
        binary = HOME / 'parkmenu_server'
        if binary.exists() and binary.stat().st_mtime >= SOURCE.stat().st_mtime:
            return binary
        headers = HOME / 'vsomeip-3.5.0' / 'interface'
        if not headers.exists():
            archive = HOME / 'vsomeip-3.5.0.tar.gz'
            with urllib.request.urlopen(VSOMEIP_URL, timeout=60) as response:
                archive.write_bytes(response.read())
            with tarfile.open(archive) as tar:
                members = [m for m in tar.getmembers()
                           if m.name.startswith('vsomeip-3.5.0/interface/') and '..' not in m.name]
                tar.extractall(HOME, members=members)
            archive.unlink()
        result = subprocess.run(
            ['g++', '-std=c++17', '-O1', '-I', str(headers), str(SOURCE), '-o', str(binary),
             '-Wl,--allow-shlib-undefined', str(ROOT / 'usr/lib/libvsomeip3.so.3.5.0'), '-lpthread'],
            capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('parkmenu_server build failed: ' + result.stderr[-2000:])
        return binary

    def didui_binary(self, process, demo):
        original = (process / 'root/opt/ivi/rootfs/usr/bin/DidUi').read_bytes()
        import hashlib
        if hashlib.sha256(original).hexdigest() != DIDUI_SHA256:
            raise RuntimeError('Unsupported DidUi build (hash mismatch)')
        target = HOME / ('DidUi-demo%d' % demo if demo else 'DidUi')
        if target.exists():
            return target
        data = bytearray(original)
        if demo:
            phoff = struct.unpack_from('<Q', original, 32)[0]
            size, count = struct.unpack_from('<HH', original, 54)
            for index in range(count):
                kind, _, offset, address, _, length = struct.unpack_from('<IIQQQQ', original, phoff + index * size)
                if kind == 1 and address <= DEMO_PATCH_ADDRESS < address + length:
                    position = offset + DEMO_PATCH_ADDRESS - address
                    if struct.unpack_from('<I', original, position)[0] != DEMO_PATCH_ORIGINAL:
                        raise RuntimeError('Unexpected DidUi demo instruction')
                    struct.pack_into('<I', data, position, 0x52800003 | ((demo & 0xffff) << 5))
        target.write_bytes(data)
        target.chmod(0o755)
        return target

    def build_view(self):
        binary = HOME / 'parking_view.so'
        if binary.exists() and binary.stat().st_mtime >= VIEW_SOURCE.stat().st_mtime:
            return binary
        temporary = binary.with_suffix('.so.tmp')
        result = subprocess.run(
            ['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
             '-O2', str(VIEW_SOURCE), '-o', str(temporary), '-ldl'], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError('parking view build failed: ' + result.stderr[-2000:])
        temporary.replace(binary)
        return binary

    def didui_assets(self):
        assets = HOME / 'didui-assets'
        if not (assets / 'baureihePDC').exists():
            temporary = HOME / 'didui-assets.tmp'
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.copytree(ROOT / 'opt/ivi/rootfs/usr/share/didui', temporary, symlinks=True)
            temporary.rename(assets)
        (assets / 'didconfig.json').write_text(json.dumps({'enableModuleSichtAbsicht': True}))
        (assets / 'sabconfig.json').write_text(json.dumps({'ui': {'renderSizeValidDefaultValue': True}}))
        try:
            profile = (STATE / 'logs/active-profile.txt').read_text().strip()
        except OSError:
            profile = ''
        series, brand = ('va_BAUREIHE_G26', 'HMI_BRAND_bmw_i') if profile.startswith('g26') else \
            ('va_BAUREIHE_G80LCI', 'HMI_BRAND_bmw_m')
        for filename, changes in (
                ('didcodings.json', {'baureiheCoding': series, 'hmiBrandCoding': brand,
                                     'distanceUnitCoding': 'DISTANCE_UNIT_km_only'}),
                ('sabcodings.json', {'cameraSystemCoding': 'CAMERA_SYSTEM_none', 'servicePackCoding': 'SP_2021'})):
            source = ROOT / 'opt/ivi/rootfs/usr/share/didui' / filename
            document = json.loads(re.sub(r'//[^\n]*', '', source.read_text()))
            document['codings'].update(changes)
            (assets / filename).write_text(json.dumps(document))
        return assets

    # ---------- process control ----------
    def _spawn(self, name, process, inner, binds, log):
        bind_lines = ''.join(
            f"subprocess.run(['mount','--bind',{str(src)!r},root+{dst!r}],check=True);" for src, dst in binds)
        script = ("import os,subprocess,sys; root='/srv/hmi8-lab/rootfs';"
                  "subprocess.run(['mount','--make-rprivate','/'],check=True);" + bind_lines +
                  "os.execv('/usr/sbin/chroot',['chroot',root]+sys.argv[1:])")
        command = ['nsenter', '--target', process.name, '--mount', '--net',
                   'unshare', '--mount', 'python3', '-c', script] + inner
        output = open(HOME / log, 'w')
        child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        output.close()
        self.children[name] = child

    def _write_control(self, process=None):
        process = process or hmi_process()
        state = self.state
        line = ' '.join(str(v) for v in [int(state['on']), state['layout']] + state['front'] +
                        state['rear'] + state['left'] + state['right'])
        (process / 'root/tmp/pdc.txt').write_text(line + '\n')

    def start(self, mode='live', demo=0):
        with self.lock:
            self._stop_locked()
            process = hmi_process()
            environment = hmi_environment(process)
            library_path = environment.get('LD_LIBRARY_PATH', '/opt/ivi/rootfs/usr/lib:/opt/ext/rootfs/usr/lib:/usr/lib:/lib')
            server = self.build()
            self._write_control(process)
            # Placeholders inside Hmi8's private /tmp so the bind mounts have a target.
            for placeholder in ('pdc-parkmenu', 'pdc-didui'):
                (process / 'root/tmp' / placeholder).write_bytes(b'')
            self._spawn('parkmenu', process,
                        ['/usr/bin/env', '-i', 'HOME=/tmp', 'LD_LIBRARY_PATH=' + library_path,
                         'VSOMEIP_APPLICATION_NAME=parkmenu_fake', LOADER, '--library-path', library_path,
                         '/tmp/pdc-parkmenu', '/tmp/pdc.txt'],
                        [(server, '/tmp/pdc-parkmenu')], 'parkmenu.log')
            time.sleep(1.5)
            didui = self.didui_binary(process, int(demo) if mode == 'demo' else 0)
            view = self.build_view()
            assets = self.didui_assets()
            environment = dict(environment)
            environment['HOME'] = '/tmp'
            environment['LD_PRELOAD'] = '/tmp/pdc-view:/usr/lib/libfakecod.so.0:/opt/venus/libvulkan_virtio.so:/opt/venus/angle/libegl-wayland-shim.so'
            environment['PDC_CAR_ZOOM'] = str(self.state['zoom'])
            debug = {}
            try:   # optional research switches, e.g. PDC_HOOK=1, PDC_NO_CAMLINK=1
                for line in (HOME / 'debug.env').read_text().splitlines():
                    if '=' in line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        debug[key.strip()] = value.strip()
            except OSError:
                pass
            if debug.get('PDC_HOOK') and (HOME / 'pdchook.so').exists():
                environment['LD_PRELOAD'] = '/tmp/pdc-hook:' + environment['LD_PRELOAD']
                environment.update({k: v for k, v in debug.items() if k.startswith('PDC_')})
            inner = ['/usr/bin/env', '-i'] + [k + '=' + v for k, v in environment.items()]
            inner += [LOADER, '--library-path', library_path, '/tmp/pdc-didui', '--also-log-to-stdout', '--use-json-codings']
            binds = [(didui, '/tmp/pdc-didui'), (assets, '/opt/ivi/rootfs/usr/share/didui')]
            (process / 'root/tmp/pdc-view').write_bytes(b'')
            binds.append((view, '/tmp/pdc-view'))
            if '/tmp/pdc-hook' in environment['LD_PRELOAD']:
                (process / 'root/tmp/pdc-hook').write_bytes(b'')
                binds.append((HOME / 'pdchook.so', '/tmp/pdc-hook'))
            self._spawn('didui', process, inner, binds, 'didui.log')
            self.hmi_pid = process.name
            self.mode = 'demo %d' % int(demo) if mode == 'demo' else 'live'
            self.error = None
            return self.snapshot_locked()

    def _stop_locked(self):
        for name, child in list(self.children.items()):
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        self.children.clear()
        self.mode = 'stopped'

    def stop(self):
        with self.lock:
            self._stop_locked()
            return self.snapshot_locked()

    def set(self, document):
        with self.lock:
            self.state = validate(document, self.state)
            (HOME / 'state.json').write_text(json.dumps(self.state))
            if self.mode != 'stopped':
                self._write_control()
            return self.snapshot_locked()

    def snapshot_locked(self):
        alive = {name: child.poll() is None for name, child in self.children.items()}
        try:
            current = hmi_process().name
        except RuntimeError:
            current = None
        stale = self.mode != 'stopped' and current != self.hmi_pid
        return {'mode': self.mode, 'helpers': alive, 'stale': stale, 'state': self.state,
                'layouts': LAYOUTS, 'error': self.error}

    def snapshot(self):
        with self.lock:
            return self.snapshot_locked()

    def command(self, document):
        action = document.get('action')
        if action == 'start':
            return self.start(document.get('mode', 'live'), int(document.get('demo', 0)))
        if action == 'stop':
            return self.stop()
        if action == 'set':
            return self.set(document)
        raise ValueError('Unsupported PDC action')
