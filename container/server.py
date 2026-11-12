import http.server
import json
import math
import os
import pathlib
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, '/mnt/hmi8-lab')
from cafd import Cafd, GUEST_METADATA, load_coding, save_coding
import mock_state
import pdc_sim
from updates import UpdateChecker


STATE = pathlib.Path('/srv/hmi8-lab')
CONFIG = pathlib.Path(os.environ['HMI_CODING_CONFIG'])
WEB = pathlib.Path('/opt/hmi8-container/web')
WIDTH, HEIGHT = 2880, 960


def read_exact(stream, length):
    output = bytearray()
    while len(output) < length:
        chunk = stream.read(length - len(output))
        if not chunk:
            raise EOFError('Capture ended')
        output.extend(chunk)
    return bytes(output)


class Runtime:
    def __init__(self):
        self.condition = threading.Condition()
        self.input_lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.frame = None
        self.sequence = 0
        self.frame_time = 0
        self.capture = None
        self.input_process = None
        self.restart_requested = threading.Event()
        self.session = str(time.time_ns())
        self.error = 'Waiting for firmware renderer; first startup can take 90 seconds'
        self.stopping = threading.Event()
        self.fps = int(os.environ.get('HMI_STREAM_FPS', '30'))
        self.scale = int(os.environ.get('HMI_PREVIEW_SCALE', '2'))
        if not 1 <= self.fps <= 60 or self.scale not in (1, 2, 4):
            raise ValueError('HMI_STREAM_FPS must be 1..60 and HMI_PREVIEW_SCALE must be 1, 2 or 4')
        self.metadata = Cafd(GUEST_METADATA)
        self.updates = UpdateChecker()
        self.services = mock_state.Services(STATE)
        self.pdc = pdc_sim.PdcSimulator()
        self.hmi = subprocess.Popen(['bash', '/mnt/hmi8-lab/launch_guest.sh', 'mesa-g26'], start_new_session=True)
        self.thread = threading.Thread(target=self.capture_frames, daemon=True)
        self.thread.start()
        self.service_thread = threading.Thread(target=self.simulate_services, daemon=True)
        self.service_thread.start()

    def simulate_services(self):
        while not self.stopping.wait(1):
            with self.config_lock:
                try:
                    self.services.tick()
                    self.services.error = None
                except (OSError, ValueError, KeyError, TypeError) as error:
                    self.services.error = str(error)

    def capture_frames(self):
        with (STATE / 'logs/browser-capture.txt').open('ab', buffering=0) as errors:
            while not self.stopping.is_set() and self.hmi.poll() is None:
                capture = subprocess.Popen(
                    ['bash', '/mnt/hmi8-lab/stream_guest.sh', str(self.fps), '75', str(self.scale)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, start_new_session=True,
                )
                with self.input_lock:
                    self.capture = capture
                try:
                    while not self.stopping.is_set():
                        magic, length, sequence, width, height = struct.unpack('<IIIII', read_exact(capture.stdout, 20))
                        if magic != 0x38494D48 or not 0 < length < 16000000 or (width, height) != (WIDTH, HEIGHT):
                            raise ValueError('Unexpected capture header')
                        frame = read_exact(capture.stdout, length)
                        with self.condition:
                            self.frame = frame
                            self.sequence += 1
                            self.frame_time = time.monotonic()
                            self.error = None
                            self.condition.notify_all()
                        with self.input_lock:
                            if self.input_process is None or self.input_process.poll() is not None:
                                if self.input_process is not None:
                                    self.input_process.stdin.close()
                                    self.input_process.wait()
                                self.input_process = subprocess.Popen(
                                    ['bash', '/mnt/hmi8-lab/stream_guest.sh'],
                                    env={**os.environ, 'HMI_INPUT_ONLY': '1'}, stdin=subprocess.PIPE,
                                    stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True)
                except (EOFError, OSError, ValueError) as error:
                    with self.condition:
                        self.error = str(error)
                        self.condition.notify_all()
                finally:
                    with self.input_lock:
                        self.capture = None
                        capture.stdin.close()
                    if capture.poll() is None:
                        os.killpg(capture.pid, signal.SIGTERM)
                    capture.wait()
                    capture.stdout.close()
                self.stopping.wait(2)
        with self.condition:
            self.error = 'Hmi8 stopped; inspect container logs and private logs volume'
            self.condition.notify_all()

    def touch(self, command):
        action = command.get('action')
        if action not in ('down', 'move', 'up', 'display-on', 'display-off'):
            raise ValueError('Unsupported input action')
        normalized = {'action': action}
        if action in ('down', 'move'):
            for key, limit in (('x', WIDTH), ('y', HEIGHT)):
                value = command.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Touch coordinates must be finite numbers')
                normalized[key] = max(0, min(limit - 1, int(value)))
        with self.input_lock:
            if self.input_process is None or self.input_process.poll() is not None:
                raise ValueError('Renderer input is not ready')
            self.input_process.stdin.write((json.dumps(normalized) + '\n').encode())
            self.input_process.stdin.flush()

    def status(self):
        with self.condition:
            age = time.monotonic() - self.frame_time if self.frame_time else None
            return {'ready': age is not None and age < 10 and self.hmi.poll() is None,
                    'frames': self.sequence, 'frame_age_seconds': age, 'error': self.error,
                    'width': WIDTH, 'height': HEIGHT, 'target_fps': self.fps,
                    'preview_scale': self.scale, 'maps': False,
                    'session': self.session, 'restart_requested': self.restart_requested.is_set(),
                    'hmi_exit_code': self.hmi.poll()}

    def stop(self):
        self.stopping.set()
        try:
            self.pdc.stop()
        except Exception:
            pass
        self.service_thread.join(timeout=3)
        with self.condition:
            self.condition.notify_all()
        with self.input_lock:
            if self.input_process is not None:
                self.input_process.stdin.close()
                if self.input_process.poll() is None:
                    os.killpg(self.input_process.pid, signal.SIGTERM)
                self.input_process.wait(timeout=3)
            if self.capture is not None and self.capture.poll() is None:
                os.killpg(self.capture.pid, signal.SIGTERM)
        if self.hmi.poll() is None:
            os.killpg(self.hmi.pid, signal.SIGTERM)
            try:
                self.hmi.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(self.hmi.pid, signal.SIGKILL)
                self.hmi.wait()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, format, *args):
        if args and str(args[1]) not in ('200', '304'):
            super().log_message(format, *args)

    def allowed(self):
        try:
            host = urllib.parse.urlsplit('http://' + self.headers.get('Host', '')).hostname
        except ValueError:
            return False
        return host in ('localhost', '127.0.0.1', '::1')

    def respond(self, status, body, content_type='application/json'):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            self.respond(403, {'error': 'Only localhost access is allowed'})
            return
        request = urllib.parse.urlsplit(self.path)
        runtime = self.server.runtime
        settings = self.server.settings
        if not settings and request.path in ('/api/coding', '/api/cafd', '/api/presets', '/api/mock', '/mocks', '/api/updates', '/services', '/api/services', '/pdc', '/api/pdc'):
            self.respond(404, {'error': 'Use the settings page (viewer port + 1, normally 5012)'})
            return
        if request.path == '/':
            self.respond(200, (WEB / ('settings.html' if settings else 'index.html')).read_bytes(), 'text/html; charset=utf-8')
        elif request.path == '/mocks' and settings:
            self.respond(200, mock_state.page(WEB / 'mocks.html'), 'text/html; charset=utf-8')
        elif request.path == '/pdc' and settings:
            self.respond(200, (WEB / 'pdc.html').read_bytes(), 'text/html; charset=utf-8')
        elif request.path == '/api/pdc' and settings:
            self.respond(200, runtime.pdc.snapshot())
        elif request.path == '/services' and settings:
            self.respond(200, mock_state.services_page(), 'text/html; charset=utf-8')
        elif request.path == '/api/services' and settings:
            try:
                with runtime.config_lock:
                    self.respond(200, runtime.services.snapshot())
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.respond(400, {'error': str(error)})
        elif request.path == '/api/mock' and settings:
            try:
                with runtime.config_lock:
                    self.respond(200, mock_state.status(STATE))
            except (OSError, ValueError) as error:
                self.respond(400, {'error': str(error)})
        elif request.path == '/api/updates' and settings:
            self.respond(200, runtime.updates.snapshot())
        elif request.path in ('/api/status', '/healthz'):
            status = runtime.status()
            self.respond(503 if request.path == '/healthz' and not status['ready'] else 200, status)
        elif request.path == '/api/coding':
            with runtime.config_lock:
                self.respond(200, {'overrides': json.loads(CONFIG.read_text()), 'effective': load_coding(runtime.metadata, CONFIG)})
        elif request.path == '/api/cafd':
            query = urllib.parse.parse_qs(request.query)
            search = query.get('search', [''])[0].upper()[:100]
            try:
                offset = int(query.get('offset', ['0'])[0])
                limit = int(query.get('limit', ['100'])[0])
                if offset < 0 or not 1 <= limit <= 200:
                    raise ValueError('offset must be nonnegative; limit must be 1..200')
            except ValueError as error:
                self.respond(400, {'error': str(error)})
                return
            features = [(name, value) for name, value in sorted(runtime.metadata.features.items()) if search in name]
            page = features[offset:offset + limit]
            following = offset + len(page)
            self.respond(200, {'total': len(features), 'offset': offset,
                               'next_offset': following if following < len(features) else None,
                               'features': dict(page)})
        elif request.path == '/api/presets':
            with runtime.config_lock:
                self.respond(200, {'presets': presets()})
        elif request.path == '/frame.jpg':
            if settings:
                self.respond(404, {'error': 'Use the viewer service'})
                return
            since = urllib.parse.parse_qs(request.query).get('after', ['-1'])[0]
            try:
                since = int(since)
            except ValueError:
                self.respond(400, {'error': 'Invalid frame sequence'})
                return
            with runtime.condition:
                runtime.condition.wait_for(lambda: runtime.sequence != since or runtime.stopping.is_set(), timeout=2)
                frame = runtime.frame
                sequence = runtime.sequence
            self.send_frame(sequence, frame)
        elif request.path == '/stream.mjpg':
            self.respond(404, {'error': 'Use the viewer service'}) if settings else self.stream_frames(runtime)
        else:
            self.respond(404, {'error': 'Not found'})

    def send_frame(self, sequence, frame):
        if not frame:
            self.respond(503, {'error': 'No frame yet'})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(frame)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Hmi8-Sequence', str(sequence))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(frame)

    def stream_frames(self, runtime):
        self.connection.settimeout(10)
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=hmi8frame')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        last_sequence = -1
        try:
            while not runtime.stopping.is_set():
                with runtime.condition:
                    runtime.condition.wait_for(lambda: runtime.sequence != last_sequence or runtime.stopping.is_set(), timeout=5)
                    frame, sequence = runtime.frame, runtime.sequence
                if frame is None or sequence == last_sequence:
                    last_sequence = sequence
                    if runtime.hmi.poll() is not None:
                        break
                    continue
                self.wfile.write(b'--hmi8frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
                self.wfile.flush()
                last_sequence = sequence
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_POST(self):
        origin = self.headers.get('Origin')
        expected_origin = 'http://' + self.headers.get('Host', '')
        if not self.allowed() or self.headers.get('X-Hmi8-Request') != '1' or (origin and origin != expected_origin):
            self.respond(403, {'error': 'Local same-origin request required'})
            self.close_connection = True
            return
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('JSON content type required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('Invalid body length')
            self.connection.settimeout(5)
            command = json.loads(read_exact(self.rfile, length))
            if not isinstance(command, dict):
                raise ValueError('JSON object required')
            runtime = self.server.runtime
            if self.path == '/api/touch' and not self.server.settings:
                self.server.runtime.touch(command)
                self.respond(200, {'ok': True})
            elif self.path == '/api/coding' and self.server.settings:
                with self.server.runtime.config_lock:
                    save_coding(self.server.runtime.metadata, CONFIG, command)
                self.respond(200, {'ok': True, 'restart_required': True})
            elif self.path == '/api/mock' and self.server.settings:
                with runtime.config_lock:
                    self.respond(200, mock_state.save(STATE, command))
            elif self.path == '/api/pdc' and self.server.settings:
                try:
                    self.respond(200, runtime.pdc.command(command))
                except RuntimeError as error:
                    self.respond(409, {'error': str(error)})
            elif self.path == '/api/services' and self.server.settings:
                with runtime.config_lock:
                    self.respond(200, runtime.services.command(command))
            elif self.path == '/api/updates' and self.server.settings:
                self.respond(200, runtime.updates.check())
            elif self.path == '/api/convert' and self.server.settings:
                self.respond(200, {'overrides': convert_coding(runtime.metadata, command),
                                   'note': 'Explicit JSON values only; no binary NCD decoding or inferred vehicle defaults.'})
            elif self.path == '/api/presets' and self.server.settings:
                name = preset_name(command.get('name'))
                with runtime.config_lock:
                    action = command.get('action')
                    if action in ('save', 'delete') and (pathlib.Path('/opt/hmi8-container/presets') / (name + '.json')).exists():
                        raise ValueError('Built-in presets are read-only; save under a new name')
                    if action == 'save':
                        (STATE / 'config/presets').mkdir(parents=True, exist_ok=True)
                        save_coding(runtime.metadata, STATE / 'config/presets' / (name + '.json'), command['overrides'])
                    elif action == 'activate':
                        available = presets()
                        if name not in available:
                            raise ValueError('Unknown preset')
                        save_coding(runtime.metadata, CONFIG, available[name])
                    elif action == 'delete':
                        (STATE / 'config/presets' / (name + '.json')).unlink()
                    else:
                        raise ValueError('Unsupported preset action')
                self.respond(200, {'ok': True, 'restart_required': action == 'activate'})
            elif self.path == '/api/restart' and self.server.settings:
                self.respond(200, {'ok': True, 'message': 'Restarting this HMI container, not the Docker engine'})
                self.wfile.flush()
                if not runtime.restart_requested.is_set():
                    runtime.restart_requested.set()
                    threading.Timer(1, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            else:
                self.respond(404, {'error': 'Not found'})
        except (OSError, EOFError, ValueError, TypeError, KeyError) as error:
            self.respond(400, {'error': str(error)})
            self.close_connection = True


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16


def preset_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise ValueError('Preset name: 1..64 letters, numbers, underscores or hyphens')
    if name == 'assets':
        raise ValueError('The name assets is reserved for the installer manifest, not a coding preset')
    return name


def presets():
    directory = STATE / 'config/presets'
    directory.mkdir(parents=True, exist_ok=True)
    output = {}
    for parent in (pathlib.Path('/opt/hmi8-container/presets'), directory):
        for path in sorted(parent.glob('*.json')):
            if path.stem == 'assets':
                continue
            output[path.stem] = json.loads(path.read_text())
    for name, canonical in (('gpu-g26', 'full-g26'), ('gpu-g80-m', 'full-g80')):
        if name in output and canonical in output and output[name] == output[canonical]:
            del output[name]
    return output


def convert_coding(metadata, document):
    source = document.get('entries', document.get('overrides', document))
    identifiers = {feature['uuid'].lower(): name for name, feature in metadata.features.items()}
    if isinstance(source, list):
        if any(not isinstance(entry, dict) or 'value' not in entry for entry in source):
            raise ValueError('Entries require name or uuid, and value')
        entries = [(entry.get('name', entry.get('uuid')), entry['value']) for entry in source]
    elif isinstance(source, dict):
        entries = list(source.items())
    else:
        raise ValueError('Supply a name/value object, overrides object, or entries array')
    output = {}
    for identifier, value in entries:
        if not isinstance(identifier, str):
            raise ValueError('Entry identifiers must be coding names or UUID strings')
        name = identifier if identifier in metadata.features else identifiers.get(identifier.lower())
        if name is None:
            raise ValueError(f'Unknown CAFD name/UUID: {identifier}')
        if name in output:
            raise ValueError(f'Duplicate coding entry: {name}')
        output[name] = metadata.normalize(name, value)
    return output


def main():
    runtime = Runtime()
    servers = []
    for port, settings in ((5001, False), (5002, True)):
        server = Server(('0.0.0.0', port), Handler)
        server.runtime = runtime
        server.settings = settings
        servers.append(server)

    def shutdown(signum, frame):
        for server in servers:
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print('Viewer :5001; settings :5002; no maps or vehicle-network connection', flush=True)
    try:
        threading.Thread(target=servers[1].serve_forever, daemon=True).start()
        servers[0].serve_forever()
    finally:
        runtime.stop()
        for server in servers:
            server.server_close()


if __name__ == '__main__':
    main()
