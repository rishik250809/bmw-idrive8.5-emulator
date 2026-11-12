import json
import math
import os
import pathlib
import re
import tempfile
import copy
import time


M_FIELDS = {
    'm_engine': ('Engine', ('Efficient', 'Sport', 'Sport Plus')),
    'm_damper': ('Chassis', ('Comfort', 'Sport', 'Sport Plus')),
    'm_steering': ('Steering', ('Comfort', 'Sport')),
    'm_brake': ('Brake', ('Comfort', 'Sport')),
    'm_xdrive': ('M xDrive', ('4WD', '4WD Sport', '2WD')),
}

DEFAULTS = {
    'title': 'Midnight Drive',
    'artist': 'HMI Lab Artist',
    'album': '',
    'source': 'Bluetooth (mock)',
    'left_temperature': 21.0,
    'right_temperature': 22.0,
    'climate_on': True,
    **{name: 1 for name in M_FIELDS},
}


def validate(document):
    if (not isinstance(document, dict) or set(document) - set(DEFAULTS)
            or set(DEFAULTS) - set(M_FIELDS) - set(document)):
        raise ValueError('Supply the supported climate/media fields and optional M selections')
    document = {**DEFAULTS, **document}
    for name in ('title', 'artist', 'album', 'source'):
        value = document[name]
        if not isinstance(value, str) or len(value) > 128 or any(ord(character) < 32 or 127 <= ord(character) < 160 for character in value):
            raise ValueError(f'{name}: at most 128 characters, without control characters')
    for name in ('left_temperature', 'right_temperature'):
        value = document[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 16 <= value <= 28:
            raise ValueError(f'{name}: a finite number from 16 to 28 Celsius is required')
    if type(document['climate_on']) is not bool:
        raise ValueError('climate_on: a JSON boolean is required')
    for name, (label, choices) in M_FIELDS.items():
        if type(document[name]) is not int or not 1 <= document[name] <= len(choices):
            raise ValueError(f'{label}: select an option from 1 to {len(choices)}')
    return dict(document)


def m_values(directory):
    defaults = {name: DEFAULTS[name] for name in M_FIELDS}
    saved = directory / 'm-state.txt'
    if not saved.is_file():
        return defaults
    try:
        values = [int(value) for value in saved.read_text().split()]
    except ValueError:
        return defaults
    if len(values) != len(M_FIELDS):
        return defaults
    if any(not 1 <= value <= len(choices) for value, (_, choices) in zip(values, M_FIELDS.values())):
        return defaults
    return dict(zip(M_FIELDS, values))


def page(path):
    controls = ''.join(
        f'<label>{label}<select id="{name}" style="font:inherit;padding:10px">'
        + ''.join(f'<option value="{index}">{choice}</option>' for index, choice in enumerate(choices, 1))
        + '</select></label>' for name, (label, choices) in M_FIELDS.items())
    section = ('<section><h2>M Setup</h2>' + controls
               + '<p class="muted">G80 M Setup simulation. Touch selections and these values are saved per profile across HMI restarts. '
               'This does not control a vehicle or configure M1/M2.</p></section>')
    return (path.read_text().replace('<form id="controls">', '<p><a href="/services">Vehicle & service simulator →</a></p><form id="controls">', 1)
            .replace('<button id="apply"', section + '<button id="apply"', 1)
            .replace("'climate_on'];", "'climate_on'," + ','.join(json.dumps(name) for name in M_FIELDS) + '];', 1)
            .replace("input.type==='number'?Number(input.value)",
                     "(input.type==='number'||input.tagName==='SELECT')?Number(input.value)", 1)).encode()


def boot_console(root, vm, started_ns):
    home = pathlib.Path(os.environ.get('LIMA_HOME', str(pathlib.Path.home() / '.lima')))
    candidates = sorted((home / vm).glob('serial*.log'), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    candidates.append(root / '.local/linux-dmesg.log')
    serial_text = ''
    for path in candidates:
        if not path.is_file() or path.stat().st_mtime_ns < started_ns:
            continue
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 131072))
            text = stream.read(131072).decode('utf-8', errors='replace')
        text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
        text = re.sub(r'\x1bP.*?\x1b\\', '', text, flags=re.S)
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b.', '', text)
        text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
        if text.strip():
            if path == root / '.local/linux-dmesg.log':
                return '\n'.join((text + '\n' + serial_text).splitlines()[-100:])
            if re.search(r'^\[\s*\d+\.\d+\]', text, re.M):
                return '\n'.join(text.splitlines()[-100:])
            if not serial_text:
                serial_text = text
    return '\n'.join(serial_text.splitlines()[-100:])


def configure_boot(root=pathlib.Path('/')):
    def arguments(text):
        values = [value for value in text.split() if value not in ('quiet', 'rhgb', 'console=hvc0')
                  and not value.startswith(('loglevel=', 'systemd.show_status='))]
        return ' '.join(values + ['console=hvc0', 'loglevel=7', 'systemd.show_status=1'])

    entries = sorted((root / 'boot/loader/entries').glob('*.conf'))
    if not entries:
        raise ValueError('No Fedora boot-loader entries found; refusing to guess the boot configuration')
    configured = 0
    for entry in entries:
        text = entry.read_text()
        if not re.search(r'^options\s+', text, re.M):
            continue
        updated = re.sub(r'^options[^\n]*', lambda match: 'options ' + arguments(match[0][7:]), text, flags=re.M)
        if updated != text:
            atomic_write(entry, updated)
        configured += 1
    if not configured:
        raise ValueError('Fedora boot-loader entries have no kernel options')
    command_line = root / 'etc/kernel/cmdline'
    if command_line.is_file():
        atomic_write(command_line, arguments(command_line.read_text()) + '\n')
    return configured


def active_directory(state):
    profile = (state / 'logs/active-profile.txt').read_text().strip()
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', profile):
        raise ValueError('Invalid active runtime profile')
    return profile, state / 'profiles' / profile / 'mock'


def status(state):
    profile, directory = active_directory(state)
    saved = directory / 'state.json'
    document = validate(json.loads(saved.read_text())) if saved.is_file() else dict(DEFAULTS)
    document.update(m_values(directory))
    document.update(climate_feedback(directory))
    return {
        'profile': profile,
        'enabled': (state / 'native-mock/enabled').is_file(),
        'available': (state / 'native-mock/libnative-mock.so').is_file(),
        'configured': saved.is_file(),
        'state': document,
        'note': 'Experimental native display-state simulation; not Bluetooth pairing, audio playback, or a vehicle HVAC service.',
    }


def atomic_write(destination, content):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent, delete=False) as stream:
        temporary = pathlib.Path(stream.name)
        stream.write(content)
    try:
        temporary.chmod(0o644)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def climate_feedback(directory):
    path = directory / 'climate-feedback.txt'
    if not path.is_file():
        return {}
    try:
        values = path.read_text().split()
        if len(values) != 3 or values[2] not in ('0', '1'):
            return {}
        left, right = map(float, values[:2])
        if not all(math.isfinite(value) and 16 <= value <= 28 for value in (left, right)):
            return {}
        return {'left_temperature': left, 'right_temperature': right, 'climate_on': values[2] == '1'}
    except ValueError:
        return {}


def save(state, document):
    supplied_m = {name: document[name] for name in M_FIELDS if isinstance(document, dict) and name in document}
    document = validate(document)
    if not (state / 'native-mock/enabled').is_file() or not (state / 'native-mock/libnative-mock.so').is_file():
        raise ValueError('Native mock is not installed/enabled. Run hmi8_gpu_lab/install_native_mock.sh in the prepared GPU lab first.')
    profile, directory = active_directory(state)
    directory.mkdir(parents=True, exist_ok=True)
    document.update({**m_values(directory), **supplied_m})
    secondary = ' · '.join(value for value in (document['artist'], document['album']) if value)
    transport = '\n'.join((document['title'], secondary, document['source'],
                           f"{document['left_temperature']} {document['right_temperature']} {int(document['climate_on'])}", ''))
    atomic_write(directory / 'state.txt', transport)
    atomic_write(directory / 'climate-feedback.txt', f"{document['left_temperature']} {document['right_temperature']} {int(document['climate_on'])}\n")
    if supplied_m:
        atomic_write(directory / 'm-state.txt', ' '.join(str(document[name]) for name in M_FIELDS) + '\n')
    atomic_write(directory / 'state.json', json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    return {'ok': True, 'profile': profile, 'state': document,
            'note': 'Saved for the active profile. Allow about one minute after boot, then approximately one second for updates. No audio is played.'}


def field(default, *, choices=None, minimum=None, maximum=None):
    return {'default': default, 'type': type(default).__name__, 'choices': choices, 'minimum': minimum, 'maximum': maximum}


SERVICE_FIELDS = {
    'vehicle': {
        'ignition': field('off', choices=['off', 'accessory', 'on']),
        'gear': field('P', choices=['P', 'R', 'N', 'D']),
        'speed_kmh': field(0.0, minimum=0, maximum=330),
        'odometer_km': field(0.0, minimum=0, maximum=2000000),
        'parking_brake': field(True),
        'lights': field('off', choices=['off', 'auto', 'parking', 'low_beam', 'high_beam']),
        'indicator': field('off', choices=['off', 'left', 'right', 'hazard']),
        **{name: field(False) for name in ('door_front_left', 'door_front_right', 'door_rear_left',
                                         'door_rear_right', 'boot_open', 'bonnet_open')},
        'fuel_percent': field(75.0, minimum=0, maximum=100),
        'battery_percent': field(80.0, minimum=0, maximum=100),
    },
    'climate': {
        'left_temperature': field(21.0, minimum=16, maximum=28),
        'right_temperature': field(22.0, minimum=16, maximum=28),
        'climate_on': field(True),
        'auto': field(True), 'ac': field(True),
        'fan': field(3, minimum=0, maximum=7),
        'recirculation': field(False), 'rear_defrost': field(False),
        'driver_seat_heat': field(0, minimum=0, maximum=3),
        'passenger_seat_heat': field(0, minimum=0, maximum=3),
    },
    'media': {
        'connected': field(False), 'device_name': field('Bench phone'),
        'playback': field('stopped', choices=['stopped', 'paused', 'playing']),
        'volume_percent': field(40, minimum=0, maximum=100), 'muted': field(False),
        **{name: field(DEFAULTS[name]) for name in ('title', 'artist', 'album', 'source')},
        'track_index': field(0, minimum=0, maximum=99),
        'position_seconds': field(0.0, minimum=0, maximum=86400),
        'playlist': field([{'title': 'Midnight Drive', 'artist': 'HMI Lab Artist',
                            'album': '', 'duration_seconds': 180.0}]),
    },
    'm_drive': {
        **{name: field(1, minimum=1, maximum=len(choices)) for name, (_, choices) in M_FIELDS.items()},
        'mode': field('road', choices=['road', 'sport', 'track']),
        'dsc': field('on', choices=['on', 'mdm', 'off']),
        'transmission': field('D1', choices=['D1', 'D2', 'D3', 'S1', 'S2', 'S3']),
        'traction': field(10, minimum=0, maximum=10),
        'active_slot': field('custom', choices=['custom', 'm1', 'm2']),
        'presets': field({'m1': None, 'm2': None}),
    },
    'parking': {
        'active': field(False), 'automatic_reverse': field(True),
        'camera': field('off', choices=['off', 'rear', 'surround']),
        'front_cm': field([None, None, None, None]),
        'rear_cm': field([None, None, None, None]),
    },
    'personalization': {
        'driver_name': field('Bench Driver'), 'welcome': field(True),
        'home_layout': field('standard', choices=['standard', 'M']),
    },
    'gnss': {
        'fix': field(False), 'latitude': field(48.137154, minimum=-90, maximum=90),
        'longitude': field(11.576124, minimum=-180, maximum=180),
        'heading_degrees': field(0.0, minimum=0, maximum=360),
        'move_with_vehicle': field(False),
        'following_route': field(False), 'route_index': field(0, minimum=0, maximum=256),
        'route': field([]),
    },
    'clock': {'running': field(False), 'elapsed_seconds': field(0.0, minimum=0, maximum=1e12)},
}

SERVICE_CAPABILITIES = {
    'vehicle': 'Native power, body panels, lights and indicators. Hazards flash every 500 ms. Auto lights simulate daytime running lights; parking lights use the running-light model. These affect the vehicle model, not cluster telltales.',
    'climate': 'Native footer temperatures/on-off and +/- touch feedback. AUTO, AC and fan status reach ClimateMaster; AUTO switches the native automatic/manual artwork. Availability is seeded, but full climate menu buttons/sliders remain missing. Seats, recirculation and defrost remain lab-only; native AUTO/AC/fan touch requests are not echoed yet.',
    'media': 'Native title/artist/album/source text. Connection, playback, volume and skip controls are simulated; no audio or real Bluetooth.',
    'm_drive': 'Existing native feedback: engine/chassis/steering/brake/xDrive. M1/M2 recall applies these five settings; DSC, M Mode, gearbox and traction are simulator-only.',
    'parking': 'Legacy Services parking state is lab-only. Use the separate /pdc simulator for native sensor visualization and layout. Camera video is not supplied.',
    'personalization': 'Simulator driver label/layout only. Does not replace persod or switch native HMI users.',
    'gnss': 'Simulator position/route feed only; no native GNSS service, maps, route guidance or AR-camera feed.',
    'clock': 'Explicitly start or advance the simulation clock. Pauses on process restart; no wall-clock catch-up.',
}

SERVICE_NATIVE_FIELDS = {
    'vehicle': ['ignition', 'gear', 'speed_kmh', 'door_front_left', 'door_front_right',
                'door_rear_left', 'door_rear_right', 'bonnet_open', 'boot_open', 'lights', 'indicator'],
    'climate': ['left_temperature', 'right_temperature', 'climate_on', 'auto', 'ac', 'fan'],
    'media': ['title', 'artist', 'album', 'source'],
    'm_drive': list(M_FIELDS),
}


def service_defaults():
    return {group: {name: copy.deepcopy(specification['default']) for name, specification in fields.items()}
            for group, fields in SERVICE_FIELDS.items()}


def bounded_number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def validate_services(document):
    if not isinstance(document, dict) or set(document) != set(SERVICE_FIELDS):
        raise ValueError('Supply only the supported simulator domains')
    for group, fields in SERVICE_FIELDS.items():
        section = document[group]
        if not isinstance(section, dict) or set(section) != set(fields):
            raise ValueError(f'{group}: unsupported or missing fields')
        for name, specification in fields.items():
            value, default = section[name], specification['default']
            valid = True
            if type(default) is bool:
                valid = type(value) is bool
            elif type(default) in (int, float):
                valid = bounded_number(value, specification['minimum'], specification['maximum'])
                if type(default) is int:
                    valid = valid and type(value) is int
            elif isinstance(default, str):
                valid = (isinstance(value, str) and len(value) <= 128
                         and not any(ord(character) < 32 or 127 <= ord(character) < 160 for character in value))
            elif isinstance(default, list):
                valid = isinstance(value, list)
            elif isinstance(default, dict):
                valid = isinstance(value, dict)
            if specification['choices'] is not None:
                valid = valid and value in specification['choices']
            if not valid:
                raise ValueError(f'{group}.{name}: invalid value')
    vehicle, media, gnss = document['vehicle'], document['media'], document['gnss']
    if vehicle['speed_kmh'] and (vehicle['ignition'] != 'on' or vehicle['gear'] == 'P' or vehicle['parking_brake']):
        raise ValueError('Moving requires ignition on, gear other than P, and parking brake released')
    playlist = media['playlist']
    if not 1 <= len(playlist) <= 100 or media['track_index'] >= len(playlist):
        raise ValueError('Use 1..100 playlist tracks and a valid track_index')
    for track in playlist:
        if not isinstance(track, dict) or set(track) != {'title', 'artist', 'album', 'duration_seconds'}:
            raise ValueError('Each track needs title, artist, album and duration_seconds')
        validate({**DEFAULTS, **{name: track[name] for name in ('title', 'artist', 'album')}})
        if not bounded_number(track['duration_seconds'], 1, 86400):
            raise ValueError('Track duration must be 1..86400 seconds')
    if media['position_seconds'] > playlist[media['track_index']]['duration_seconds']:
        raise ValueError('Media position exceeds the selected track duration')
    if media['playback'] == 'playing' and not media['connected']:
        raise ValueError('Connect the simulated phone before starting playback')
    for name in ('front_cm', 'rear_cm'):
        distances = document['parking'][name]
        if len(distances) != 4 or any(value is not None and not bounded_number(value, 0, 500) for value in distances):
            raise ValueError(f'{name}: four distances in cm (0..500), or null for no obstacle')
    route = gnss['route']
    if len(route) > 256 or gnss['route_index'] > len(route):
        raise ValueError('Use at most 256 route points and a valid route_index')
    for point in route:
        if (not isinstance(point, dict) or set(point) != {'latitude', 'longitude'}
                or not bounded_number(point['latitude'], -90, 90) or not bounded_number(point['longitude'], -180, 180)):
            raise ValueError('Route points need finite latitude and longitude')
    if gnss['following_route'] and (not gnss['fix'] or gnss['route_index'] >= len(route)):
        raise ValueError('Following a route requires a fix and an unfinished route')
    selections = set(SERVICE_FIELDS['m_drive']) - {'active_slot', 'presets'}
    presets = document['m_drive']['presets']
    if set(presets) != {'m1', 'm2'}:
        raise ValueError('M presets must contain m1 and m2')
    for preset in presets.values():
        if preset is None:
            continue
        if not isinstance(preset, dict) or set(preset) != selections:
            raise ValueError('Invalid M preset fields')
        for name, value in preset.items():
            specification = SERVICE_FIELDS['m_drive'][name]
            if specification['choices']:
                valid = isinstance(value, str) and value in specification['choices']
            else:
                valid = type(value) is int and specification['minimum'] <= value <= specification['maximum']
            if not valid:
                raise ValueError(f'Invalid M preset {name}')
    return copy.deepcopy(document)


def geo_distance_and_heading(start, end):
    latitude, target_latitude = map(math.radians, (start['latitude'], end['latitude']))
    difference = math.radians(end['longitude'] - start['longitude'])
    chord = math.sin((target_latitude - latitude) / 2) ** 2 + math.cos(latitude) * math.cos(target_latitude) * math.sin(difference / 2) ** 2
    distance = 6371000 * 2 * math.asin(math.sqrt(min(1, max(0, chord))))
    heading = math.degrees(math.atan2(math.sin(difference) * math.cos(target_latitude),
                                     math.cos(latitude) * math.sin(target_latitude)
                                     - math.sin(latitude) * math.cos(target_latitude) * math.cos(difference))) % 360
    return distance, heading


def move_position(position, distance):
    latitude, longitude, heading = map(math.radians, (position['latitude'], position['longitude'], position['heading_degrees']))
    angle = distance / 6371000
    target = math.asin(max(-1, min(1, math.sin(latitude) * math.cos(angle) + math.cos(latitude) * math.sin(angle) * math.cos(heading))))
    longitude += math.atan2(math.sin(heading) * math.sin(angle) * math.cos(latitude), math.cos(angle) - math.sin(latitude) * math.sin(target))
    position.update(latitude=math.degrees(target), longitude=(math.degrees(longitude) + 180) % 360 - 180)


class Services:
    def __init__(self, state):
        self.state = pathlib.Path(state)
        self.last_tick = None
        self.last_profile = None
        self.running_profiles = set()
        self.error = None

    def read(self):
        profile, directory = active_directory(self.state)
        path = directory / 'services.json'
        if path.is_file():
            record = json.loads(path.read_text())
            if (not isinstance(record, dict) or record.get('version') != 1
                    or type(record.get('revision')) is not int or record['revision'] < 0):
                raise ValueError('Unsupported simulator state version/revision')
            document = validate_services(record['state'])
        else:
            record = {'version': 1, 'revision': 0, 'events': []}
            document = service_defaults()
        if profile != self.last_profile:
            self.running_profiles.discard(self.last_profile)
            self.last_profile, self.last_tick = profile, time.monotonic()
        if profile not in self.running_profiles:
            document['clock']['running'] = False
        native_status = status(self.state)
        native_state = native_status['state']
        for group in ('climate', 'media', 'm_drive'):
            if native_status['enabled'] and native_status['available'] and (
                    native_status['configured'] or group == 'm_drive' and (directory / 'm-state.txt').is_file()):
                for name in document[group].keys() & native_state.keys():
                    document[group][name] = native_state[name]
        settings = document['m_drive']
        slot = settings['active_slot']
        if slot != 'custom' and settings['presets'][slot] != {name: settings[name] for name in settings if name not in ('active_slot', 'presets')}:
            settings['active_slot'] = 'custom'
        record['state'] = validate_services(document)
        return profile, directory, record

    def snapshot(self):
        profile, directory, record = self.read()
        document = record['state']
        parking = document['parking']
        native_state = status(self.state)
        return {'profile': profile, 'revision': record['revision'], 'state': document,
                'schema': SERVICE_FIELDS, 'capabilities': SERVICE_CAPABILITIES, 'native_fields': SERVICE_NATIVE_FIELDS,
                'native_bridge_enabled': native_state['enabled'] and native_state['available'],
                'events': record.get('events', [])[-20:], 'clock_error': self.error,
                'native_feedback': json.loads((directory / 'native-feedback.json').read_text()) if (directory / 'native-feedback.json').is_file() else {'status': 'waiting_for_native_bridge'},
                'derived': {'parking_active': parking['active'] or (parking['automatic_reverse'] and document['vehicle']['gear'] == 'R'
                                                                   and document['vehicle']['ignition'] == 'on'),
                            'front_zones': self.zones(parking['front_cm']), 'rear_zones': self.zones(parking['rear_cm'])}}

    @staticmethod
    def zones(distances):
        return ['clear' if value is None else 'red' if value < 40 else 'yellow' if value < 100 else 'green' for value in distances]

    def commit(self, directory, record, before, action):
        document = validate_services(record['state'])
        native_state = status(self.state)
        projected = dict(native_state['state'])
        changed = set()
        for group in ('climate', 'media', 'm_drive'):
            for name in document[group].keys() & projected.keys():
                if document[group][name] != before[group][name]:
                    projected[name] = document[group][name]
                    changed.add(name)
        if changed and native_state['enabled'] and native_state['available']:
            save(self.state, {name: value for name, value in projected.items() if name not in M_FIELDS or name in changed})
        directory.mkdir(parents=True, exist_ok=True)
        record['state'] = document
        record['revision'] += 1
        if action != 'tick':
            record['events'] = (record.get('events', []) + [{'revision': record['revision'], 'action': action}])[-100:]
        atomic_write(directory / 'services.json', json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        request = record.get('native_request', {'id': 0, 'menu': 'none'})
        vehicle = document['vehicle']
        ignition = {'off': 2, 'accessory': 5, 'on': 10 if vehicle['speed_kmh'] else 8}[vehicle['ignition']]
        body = ('door_front_left', 'door_front_right', 'door_rear_left', 'door_rear_right', 'bonnet_open', 'boot_open')
        transport = (f"1 {record['revision']} {ignition} {vehicle['speed_kmh']} {vehicle['gear']}\n"
                     + ' '.join(str(int(vehicle[name])) for name in body) + '\n'
                     + f"{request['id']} {request['menu']}\n"
                     + f"{vehicle['indicator']} {vehicle['lights']}\n"
                     + f"{int(document['climate']['auto'])} {int(document['climate']['ac'])} {document['climate']['fan']}\n")
        atomic_write(directory / 'native-services.txt', transport)

    def command(self, command):
        if not isinstance(command, dict) or set(command) - {'action', 'profile', 'expected_revision', 'changes', 'slot', 'zone', 'delta', 'seconds'}:
            raise ValueError('Invalid simulator command fields')
        profile, directory, record = self.read()
        if command.get('profile') != profile:
            raise ValueError('Active profile changed; reload the simulator before applying values')
        if 'expected_revision' in command and (type(command['expected_revision']) is not int or command['expected_revision'] != record['revision']):
            raise ValueError('Simulator revision changed; reload before applying values')
        document = record['state']
        before = copy.deepcopy(document)
        action = command.get('action')
        arguments = {'patch': {'changes'}, 'climate_step': {'zone', 'delta'}, 'advance': {'seconds'},
                     'save_m': {'slot'}, 'recall_m': {'slot'},
                     **{name: set() for name in ('play', 'pause', 'stop', 'next', 'previous', 'connect', 'disconnect',
                                                 'start_clock', 'pause_clock', 'scenario_reverse', 'scenario_drive', 'load_demo_route',
                                                 'native_parking_menu', 'native_climate_menu')}}
        if not isinstance(action, str) or action not in arguments or set(command) - {'action', 'profile', 'expected_revision'} - arguments[action]:
            raise ValueError('Unsupported simulator action or action arguments')
        if action == 'patch':
            changes = command.get('changes')
            if not isinstance(changes, dict) or not changes or set(changes) - set(SERVICE_FIELDS):
                raise ValueError('Supply supported simulator domains in changes')
            for group, updates in changes.items():
                if not isinstance(updates, dict) or set(updates) - set(SERVICE_FIELDS[group]):
                    raise ValueError(f'{group}: unsupported fields')
                document[group].update(copy.deepcopy(updates))
            if 'm_drive' in changes:
                document['m_drive']['active_slot'] = 'custom'
        elif action in ('play', 'pause', 'stop', 'next', 'previous', 'connect', 'disconnect'):
            media = document['media']
            if action in ('connect', 'disconnect'):
                media['connected'] = action == 'connect'
                if action == 'disconnect':
                    media['playback'] = 'stopped'
            elif action in ('play', 'pause', 'stop'):
                media['playback'] = {'play': 'playing', 'pause': 'paused', 'stop': 'stopped'}[action]
                if action == 'stop':
                    media['position_seconds'] = 0.0
                elif action == 'play':
                    position = media['position_seconds']
                    self.select_track(media, media['track_index'])
                    media['position_seconds'] = position
            else:
                self.select_track(media, (media['track_index'] + (1 if action == 'next' else -1)) % len(media['playlist']))
        elif action == 'climate_step':
            zone, delta = command.get('zone'), command.get('delta')
            if zone not in ('left_temperature', 'right_temperature') or type(delta) not in (int, float) or delta not in (-0.5, 0.5):
                raise ValueError('Use a temperature zone and delta of -0.5 or 0.5')
            document['climate'][zone] = min(28, max(16, document['climate'][zone] + delta))
        elif action in ('save_m', 'recall_m'):
            slot = command.get('slot')
            if slot not in ('m1', 'm2'):
                raise ValueError('Select m1 or m2')
            settings = document['m_drive']
            if action == 'save_m':
                settings['presets'][slot] = {name: settings[name] for name in settings if name not in ('active_slot', 'presets')}
            else:
                if settings['presets'][slot] is None:
                    raise ValueError('Save this M preset before recalling it')
                settings.update(settings['presets'][slot])
                settings['active_slot'] = slot
        elif action in ('start_clock', 'pause_clock'):
            document['clock']['running'] = action == 'start_clock'
            self.last_tick = time.monotonic()
        elif action in ('native_parking_menu', 'native_climate_menu'):
            native_state = status(self.state)
            if not native_state['enabled'] or not native_state['available']:
                raise ValueError('The native mock library is not installed/enabled')
            record['native_request'] = {'id': time.time_ns(), 'menu': 'parking' if action == 'native_parking_menu' else 'climate'}
        elif action in ('scenario_reverse', 'scenario_drive', 'load_demo_route'):
            document['vehicle'].update(ignition='on', gear='R' if action == 'scenario_reverse' else 'D',
                                       speed_kmh=0.0 if action == 'scenario_reverse' else 30.0, parking_brake=False)
            document['parking'].update(active=action == 'scenario_reverse', camera='rear' if action == 'scenario_reverse' else 'off')
            if action == 'scenario_reverse':
                document['parking'].update(front_cm=[None, 160, 110, None], rear_cm=[90, 55, 30, 90])
            else:
                document['gnss'].update(fix=True, move_with_vehicle=action == 'scenario_drive', following_route=False)
            if action == 'load_demo_route':
                route = [{'latitude': 48.137154, 'longitude': 11.576124},
                         {'latitude': 48.137154, 'longitude': 11.578000},
                         {'latitude': 48.138000, 'longitude': 11.578000}]
                document['gnss'].update(**route[0], route=route, route_index=1, following_route=True)
        elif action == 'advance':
            seconds = command.get('seconds')
            if not bounded_number(seconds, 0.01, 60):
                raise ValueError('Advance between 0.01 and 60 seconds')
            self.advance(document, seconds)
        else:
            raise ValueError('Unsupported simulator action')
        self.commit(directory, record, before, action)
        if document['clock']['running']:
            self.running_profiles.add(profile)
        else:
            self.running_profiles.discard(profile)
        return {'ok': True, **self.snapshot()}

    @staticmethod
    def select_track(media, index):
        media['track_index'] = index
        media['position_seconds'] = 0.0
        media.update({name: media['playlist'][index][name] for name in ('title', 'artist', 'album')})

    @staticmethod
    def advance(document, seconds):
        document['clock']['elapsed_seconds'] += seconds
        vehicle, media, gnss = document['vehicle'], document['media'], document['gnss']
        distance = vehicle['speed_kmh'] * seconds / 3.6
        vehicle['odometer_km'] += distance / 1000
        if media['playback'] == 'playing':
            position = media['position_seconds'] + seconds
            while position >= media['playlist'][media['track_index']]['duration_seconds']:
                position -= media['playlist'][media['track_index']]['duration_seconds']
                Services.select_track(media, (media['track_index'] + 1) % len(media['playlist']))
            media['position_seconds'] = position
        if not gnss['fix'] or distance == 0:
            return
        if not gnss['following_route']:
            if gnss['move_with_vehicle']:
                move_position(gnss, distance)
            return
        while distance > 0 and gnss['route_index'] < len(gnss['route']):
            point = gnss['route'][gnss['route_index']]
            remaining, heading = geo_distance_and_heading(gnss, point)
            gnss['heading_degrees'] = heading
            if remaining <= distance:
                gnss.update(point)
                distance -= remaining
                gnss['route_index'] += 1
            else:
                move_position(gnss, distance)
                distance = 0
        if gnss['route_index'] == len(gnss['route']):
            gnss['following_route'] = False

    def tick(self, now=None):
        profile, directory, record = self.read()
        now = time.monotonic() if now is None else now
        elapsed = min(5, max(0, now - (self.last_tick if self.last_tick is not None else now)))
        self.last_tick = now
        if not record['state']['clock']['running'] or elapsed == 0:
            return
        before = copy.deepcopy(record['state'])
        self.advance(record['state'], elapsed)
        self.commit(directory, record, before, 'tick')


def services_page():
    # Keep internal transport defaults and saved state compatible, while showing
    # only controls useful for this emulator. PDC has its own dedicated page.
    vehicle_excluded = {'gear', 'speed_kmh', 'odometer_km', 'fuel_percent',
                        'battery_percent', 'parking_brake'}
    fields = {group: {name: spec for name, spec in SERVICE_FIELDS[group].items()
                      if group != 'vehicle' or name not in vehicle_excluded}
              for group in ('vehicle', 'climate', 'media', 'm_drive')}
    return (SERVICE_PAGE.replace('__M_FIELDS__', json.dumps(M_FIELDS))
            .replace('__SERVICE_FIELDS__', json.dumps(fields))).encode()


SERVICE_PAGE = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local service simulator</title>
<style>
:root{color-scheme:dark;font:16px system-ui;background:#10131a;color:#e4e9f3}body{max-width:1100px;margin:24px auto;padding:0 18px}section{background:#171d29;border-radius:10px;padding:18px;margin:16px 0}label{display:block;margin:10px 0}input,select,textarea,button,a{font:inherit;color:inherit;background:#202838;border:1px solid #40516e;border-radius:5px;padding:8px}input:not([type=checkbox]),select{margin-left:10px}textarea{display:block;width:95%;min-height:95px;margin-top:8px}button{cursor:pointer;margin:4px}p,pre{white-space:pre-wrap;overflow-wrap:anywhere}.muted{color:#acb9cc}#message{color:#f3c982}.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:4px 20px}label.wide{grid-column:1/-1}
</style>
<h1>Local service simulator</h1><p><a href="/">Coding & settings</a> <a href="/mocks">Native climate, media & M controls</a></p>
<p class="muted">Vehicle, climate, media and M controls. Fields marked “lab-only” do not affect the HMI. The full climate menu remains incomplete.</p>
<p id="message" role="status">Connecting…</p><p id="status"></p>
<div id="actions">
<button data-action="native_climate_menu">Toggle native climate menu (experimental)</button>
<button data-action="connect">Connect mock phone</button><button data-action="disconnect">Disconnect</button>
<button data-action="play">Play (silent)</button><button data-action="pause">Pause</button><button data-action="stop">Stop</button><button data-action="previous">Previous track</button><button data-action="next">Next track</button>
<button data-action="save_m" data-slot="m1">Save M1</button><button data-action="recall_m" data-slot="m1">Recall M1</button><button data-action="save_m" data-slot="m2">Save M2</button><button data-action="recall_m" data-slot="m2">Recall M2</button>
</div>
<form id="controls"><div id="groups"></div><button type="submit">Apply edited fields</button><button id="reload" type="button">Discard edits / reload</button></form>
<section><h2>Live simulation output (not native HMI)</h2><pre id="output"></pre></section>
<script>
const mFields=__M_FIELDS__,serviceFields=__SERVICE_FIELDS__,inputs=new Map(),dirty=new Set(),message=document.querySelector('#message');
let current=null,busy=false,initialized=false,timer=null,editSequence=0;
const generations=new Map();
function edited(key,immediate=false){dirty.add(key);generations.set(key,++editSequence);message.textContent='Saving changes…';clearTimeout(timer);timer=setTimeout(flush,immediate?0:300);}
function valueOf(key){const {input,spec}=inputs.get(key);if(typeof spec.default==='boolean')return input.checked;if(typeof spec.default==='number'){if(input.value.trim()===''||!input.checkValidity())throw Error('Complete '+key+' before saving.');const value=Number(input.value);if(!Number.isFinite(value))throw Error('Invalid '+key);return value;}if(spec.default!==null&&typeof spec.default==='object')return JSON.parse(input.value);return input.value;}
async function flush(){clearTimeout(timer);if(!current||busy||!dirty.size)return;const profile=current.profile,batch=new Map(),changes={};try{for(const key of dirty){const [group,name]=key.split('.');(changes[group]??={})[name]=valueOf(key);batch.set(key,generations.get(key));}}catch(error){message.textContent=error.message;return;}busy=true;let saved=false;try{const result=await request({profile,action:'patch',changes});if(result.profile===profile){for(const [key,generation] of batch)if(generations.get(key)===generation)dirty.delete(key);saved=true;}show(result);message.textContent='Saved. Native fields update automatically; lab-only fields remain simulation state.';}catch(error){message.textContent=error.message+' — edits retained; use Apply to retry.';}finally{busy=false;if(saved&&dirty.size)timer=setTimeout(flush,0);}}
const labelFor=name=>mFields[name]?mFields[name][0]:name.replaceAll('_',' ');
async function request(body){const response=await fetch('/api/services',body===undefined?{cache:'no-store',signal:AbortSignal.timeout(10000)}:{method:'POST',headers:{'Content-Type':'application/json','X-Hmi8-Request':'1'},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});const result=await response.json();if(!response.ok)throw Error(result.error||response.status);return result;}
function build(result){for(const [group,fields] of Object.entries(serviceFields)){const section=document.createElement('section'),heading=document.createElement('h2'),note=document.createElement('p'),holder=document.createElement('div');heading.textContent=labelFor(group);note.textContent=result.capabilities[group];note.className='muted';holder.className='fields';section.append(heading,note,holder);for(const [name,spec] of Object.entries(fields)){const label=document.createElement('label'),key=group+'.'+name;label.append(document.createTextNode(labelFor(name)+((result.native_fields?.[group]||[]).includes(name)?' · native':' · lab-only')));let input;
if(spec.choices||mFields[name]){input=document.createElement('select');const choices=spec.choices||mFields[name][1].map((text,index)=>index+1);choices.forEach((value,index)=>{const option=document.createElement('option');option.value=String(value);option.textContent=mFields[name]?mFields[name][1][index]:String(value);input.append(option);});}
else if(spec.default!==null&&typeof spec.default==='object'){input=document.createElement('textarea');label.className='wide';}
else{input=document.createElement('input');input.type=typeof spec.default==='boolean'?'checkbox':typeof spec.default==='number'?'number':'text';if(input.type==='number'){input.step=spec.type==='int'?'1':'any';if(spec.minimum!==null)input.min=spec.minimum;if(spec.maximum!==null)input.max=spec.maximum;}if(input.type==='text')input.maxLength=128;}
if(name==='presets'||name==='active_slot')input.disabled=true;
input.oninput=()=>edited(key,input.type==='checkbox'||input.tagName==='SELECT');input.onchange=()=>edited(key,true);inputs.set(key,{input,spec});label.append(input);holder.append(label);}document.querySelector('#groups').append(section);}initialized=true;}
function show(result){if(current&&current.profile===result.profile&&result.revision<current.revision)return;if(!initialized)build(result);const switched=current&&current.profile!==result.profile;if(current&&current.profile!==result.profile){clearTimeout(timer);dirty.clear();generations.clear();message.textContent='Active profile changed; reloaded its saved simulator values.';}current=result;for(const [key,{input,spec}] of inputs){if(!switched&&(dirty.has(key)||document.activeElement===input))continue;const [group,name]=key.split('.'),value=result.state[group][name];if(typeof spec.default==='boolean')input.checked=value;else input.value=value!==null&&typeof value==='object'?JSON.stringify(value,null,2):value;}document.querySelector('#status').textContent='Emulator preset: '+result.profile+' · revision '+result.revision+' · native display adapter '+(result.native_bridge_enabled?'installed/enabled':'not installed/enabled');const feedback=Object.fromEntries(Object.entries(result.native_feedback||{}).filter(([key])=>!['speed_kmh','gear_code','parking_present'].includes(key)));document.querySelector('#output').textContent=JSON.stringify({native_feedback:feedback,media:{title:result.state.media.title,playback:result.state.media.playback,position_seconds:result.state.media.position_seconds}},null,2);}
async function load(){try{show(await request());}catch(error){message.textContent=error.message;}}
async function send(command){if(busy)return;busy=true;try{show(await request({profile:current.profile,...command}));message.textContent='Saved locally for '+current.profile+'. Check section notes for native HMI support.';}catch(error){message.textContent=error.message;}finally{busy=false;if(dirty.size)timer=setTimeout(flush,0);}}
document.querySelector('#controls').onsubmit=async event=>{event.preventDefault();await flush();};
document.querySelector('#actions').onclick=async event=>{const button=event.target.closest('button[data-action]');if(!button||!current)return;if(busy){message.textContent='Saving changes; try the action again when saved.';return;}await flush();if(dirty.size)return;const command={action:button.dataset.action};if(button.dataset.slot)command.slot=button.dataset.slot;if(button.dataset.seconds)command.seconds=Number(button.dataset.seconds);await send(command);};
document.querySelector('#reload').onclick=()=>{if(busy){message.textContent='Wait for the current save before reloading.';return;}clearTimeout(timer);dirty.clear();load();};
async function poll(){if(!busy)await load();setTimeout(poll,1500);}poll();
</script></html>'''


M_NATIVE = r'''
#include <sys/mman.h>
#include <cstdlib>

namespace m_mock {
static constexpr std::array<unsigned, 5> limits = {3, 3, 2, 2, 3};
static std::atomic<void *> service(nullptr);
using Values = std::array<unsigned, 5>;

static Values read_values() {
    Values values = {1, 1, 1, 1, 1};
    std::ifstream input("/run/lab-mock/m-state.txt");
    Values saved;
    for (size_t index = 0; index < saved.size(); ++index) {
        if (!(input >> saved[index]) || saved[index] < 1 || saved[index] > limits[index]) return values;
    }
    std::string extra;
    if (input >> extra) return values;
    return saved;
}

static bool write_values(const Values &values) {
    char path[] = "/run/lab-mock/m-state.XXXXXX";
    int descriptor = mkstemp(path);
    if (descriptor < 0) return false;
    std::ostringstream output;
    for (unsigned value : values) output << value << ' ';
    output << '\n';
    std::string text = output.str();
    bool saved = write(descriptor, text.data(), text.size()) == static_cast<ssize_t>(text.size());
    if (fsync(descriptor) != 0) saved = false;
    if (close(descriptor) != 0) saved = false;
    if (saved) saved = std::rename(path, "/run/lab-mock/m-state.txt") == 0;
    if (!saved) unlink(path);
    return saved;
}

struct Description {
    virtual ~Description() = default;
    unsigned selection;
};
static_assert(sizeof(Description) == 16);

static void refresh(void *instance) {
    if (instance && *static_cast<uintptr_t *>(instance) == image_base + 0x104314c0)
        native<void (*)(void *)>(0x9303fcc)(instance);
}

template <size_t Index> static std::unique_ptr<Description> get(void *instance) {
    service = instance;
    auto description = std::make_unique<Description>();
    native<void (*)(void *, unsigned)>(0x9339fe0)(description.get(), read_values()[Index] + 1);
    return description;
}

template <size_t Index> static void set(void *instance, unsigned value) {
    if (value < 1 || value > limits[Index]) return;
    Values values = read_values();
    values[Index] = value;
    if (!write_values(values)) {
        std::fprintf(stderr, "lab-mock: unable to persist M Setup selection\n");
        return;
    }
    service = instance;
    refresh(instance);
}

static void *watch(void *) {
    Values previous = read_values();
    Snapshot snapshot;
    std::atomic<bool> queued(false);
    for (;;) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        Values current = read_values();
        if (current == previous || !service.load() || queued.exchange(true)) continue;
        if (!object(snapshot, 0)) snapshot = find_objects();
        if (void *application = object(snapshot, 0)) {
            auto dispatch = native<void (*)(void *, std::function<void ()>, std::shared_ptr<void>)>(0x510c568);
            dispatch(application, [&queued] { refresh(service.load()); queued = false; }, {});
            previous = current;
        } else {
            queued = false;
        }
    }
    return nullptr;
}

__attribute__((constructor)) static void install() {
    if (!image_base) return;
    struct Hook { uintptr_t slot; uintptr_t original; uintptr_t replacement; };
    const std::array<Hook, 10> hooks = {{
        {0x104314c0, 0x9300c40, reinterpret_cast<uintptr_t>(&get<0>)},
        {0x104314d8, 0x9300a00, reinterpret_cast<uintptr_t>(&get<1>)},
        {0x104314d0, 0x9300e80, reinterpret_cast<uintptr_t>(&get<2>)},
        {0x104314f0, 0x9301300, reinterpret_cast<uintptr_t>(&get<3>)},
        {0x104314f8, 0x9301540, reinterpret_cast<uintptr_t>(&get<4>)},
        {0x10431530, 0x9301d8c, reinterpret_cast<uintptr_t>(&set<0>)},
        {0x10431550, 0x93029e0, reinterpret_cast<uintptr_t>(&set<1>)},
        {0x10431558, 0x9302e00, reinterpret_cast<uintptr_t>(&set<2>)},
        {0x10431560, 0x9303220, reinterpret_cast<uintptr_t>(&set<3>)},
        {0x10431568, 0x9303640, reinterpret_cast<uintptr_t>(&set<4>)}
    }};
    for (const auto &hook : hooks) {
        if (*reinterpret_cast<uintptr_t *>(image_base + hook.slot) != image_base + hook.original) {
            std::fprintf(stderr, "lab-mock: M Setup signature mismatch; disabled\n");
            return;
        }
    }
    uintptr_t page_size = static_cast<uintptr_t>(sysconf(_SC_PAGESIZE));
    uintptr_t page = (image_base + hooks.front().slot) & ~(page_size - 1);
    if (mprotect(reinterpret_cast<void *>(page), page_size, PROT_READ | PROT_WRITE) != 0) return;
    for (const auto &hook : hooks) *reinterpret_cast<uintptr_t *>(image_base + hook.slot) = hook.replacement;
    if (mprotect(reinterpret_cast<void *>(page), page_size, PROT_READ) != 0)
        std::fprintf(stderr, "lab-mock: unable to restore M Setup table protection\n");
    pthread_t thread;
    if (!pthread_create(&thread, nullptr, watch, nullptr)) pthread_detach(thread);
    std::fprintf(stderr, "lab-mock: persistent M Setup feedback enabled\n");
}
}
'''


if __name__ == '__main__':
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == 'configure-boot':
        print(f'Enabled Linux boot messages in {configure_boot()} boot entries')
    elif len(sys.argv) == 3 and sys.argv[1] == 'native-source':
        source = pathlib.Path(sys.argv[2])
        print(source.read_text() + M_NATIVE + source.with_name('native_services.cpp').read_text())
    else:
        raise SystemExit('Usage: mock_state.py configure-boot | native-source /path/to/native_mock.cpp')
