import argparse
import hashlib
import json
import os
import pathlib
import re
import struct
import tempfile


WORKSPACE = pathlib.Path(__file__).resolve().parent
HOST_METADATA = pathlib.Path('/mnt/firmware-source/extracted/ROOT_rootfs/usr/share/sysfunc/coding/cafd_mgu_02_a.bin')
GUEST_METADATA = pathlib.Path('/srv/hmi8-lab/rootfs/usr/share/sysfunc/coding/cafd_mgu_02_a.bin')
BASE_CODING = {
    'SERIES': 'G026BEV',
    'DISPLAY_RES_WIDTH': 2880,
    'DISPLAY_RES_HEIGHT': 960,
    'DISPLAY_SIZE': 1490,
    'CID_TOUCH': 'enabled',
    'CID_HMI_TOUCH': 'enabled',
    'CID_HMI_ACTIVATION_VIA_TOUCH': 'enabled',
}
MEANINGS = {
    'DEVELOPER_MENU': 'Exposes developer app entries. Confirmed to reveal Developer Info and Color Configuration; their backends are not all simulated.',
    'DISPLAY_RES_WIDTH': 'Native scene width in pixels. The validated lab profile uses 2880.',
    'DISPLAY_RES_HEIGHT': 'Native scene height in pixels. The validated lab profile uses 960.',
    'DISPLAY_SIZE': 'Display diagonal in hundredths of an inch: 1490 means 14.90 inches.',
    'SERIES': 'Vehicle series selection. G026BEV=46, G080=21 (original M3), G080LCI=73. I20 is a tentative iX/ i20 profile label; verify it against CAFD metadata and vehicle assets.',
    'HMI_BRAND': 'HMI brand selection: bmw=0, bmw_i=1, mini=2, rolls_royce=3, bmw_m=4. BMW M also requires matching bmwm brand resources.',
    'CID_TOUCH': 'CID touchscreen availability. This lab supplies a virtual Linux touchscreen.',
    'CID_HMI_TOUCH': 'Enables HMI touch interaction.',
    'CID_HMI_ACTIVATION_VIA_TOUCH': 'Allows activation through touch; it does not implement the missing display service.',
    'SPORT_DISPLAYS': 'Selects sport-display layout: disabled=0, old_layout=1, new_layout=2, id8_layout=3. Does not generate engine telemetry.',
    'DISTANCE_UNIT': 'Distance-unit coding: miles/yards, miles/feet, or kilometres. Personal preferences can also affect the UI.',
    'AMB_AVAILABILITY_MENU_AND_CHECKBOX_AMBIENTLIGHT': 'Availability of the ambient-light menu and checkbox; not the current lamp state.',
    'CLIMATE_IONIZER': 'Equipment availability for the climate ionizer; does not supply the HVAC service.',
    'CLIMATE_AUTORECIRCULATION': 'Equipment availability for automatic air recirculation; not a current HVAC state.',
    'STARTUP_TYPE': 'Startup presentation: black=0, splash=1, animation=2, pwf_welcome=3. Required animation services remain separate.',
    'IS_CODED': 'Coding-status flag. Setting coded does not reproduce the donor vehicle NCD or a complete backend.',
    'VAR_ENABLE': 'Video Augmented Reality availability. Firmware videoard.service checks that this is enabled; camera, navigation and rendering services are still required.',
    'TYPE_OF_FUEL': 'Powertrain fuel category. Does not change the supplied meshes, control units or drivetrain simulation.',
    'TYPE_OF_CONSTRUCTION': 'Body-style category used by HMI feature selection.',
    'M_DRIVELOGIC': 'M Drivelogic feature availability. Requires compatible M UI assets and drivetrain services.',
    'MDRIVE': 'Enables the M driving setup entry under Driving settings. M_MENU and BMW M branding alone do not expose this entry.',
    'M_VDP': 'M chassis setup availability. The G80 mock uses 3_values for Comfort, Sport and Sport Plus; coding does not supply real suspension control.',
}


class Cafd:
    def __init__(self, path):
        self.path = path
        self.payload = path.read_bytes()
        self.features = {}
        for table in range(4, len(self.payload) - 8, 4):
            try:
                name = self.string(self.field(table, 0))
                if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,100}', name):
                    continue
                identifier = self.field(table, 12)
                group = self.field(table, 5)
                if not identifier or not group:
                    continue
                uuid = self.unpack('<Q', identifier)
                if (uuid >> 32) & 0xffff != self.unpack('<Q', group):
                    continue
                values = {}
                for entry in self.vector(self.field(table, 7)):
                    label = self.string(self.field(entry, 0))
                    number = self.field(entry, 1)
                    values[label] = self.unpack('<Q', number) if number else 0
                self.features[name] = {
                    'uuid': f'0x{uuid:016X}',
                    'values': values,
                    'conditional_default': self.string(self.field(table, 4)) or None,
                    'minimum': self.string(self.field(table, 9)) or None,
                    'maximum': self.string(self.field(table, 8)) or None,
                    'unit': self.string(self.field(table, 11)) or None,
                    'meaning': MEANINGS.get(name, 'No verified prose definition is available. The enum labels and bounds below come from the shipped metadata; do not infer full behavior from the name alone.'),
                    'meaning_source': 'Lab explanation based on supplied artifacts/runtime evidence' if name in MEANINGS else 'Unknown; metadata labels only',
                }
            except (ValueError, struct.error, UnicodeDecodeError):
                continue
        if not all(name in self.features for name in BASE_CODING):
            raise ValueError('Unsupported or incomplete CAFD metadata')

    def unpack(self, kind, offset):
        if offset < 0:
            raise ValueError('Negative offset')
        return struct.unpack_from(kind, self.payload, offset)[0]

    def field(self, table, index):
        vtable = table - self.unpack('<i', table)
        size = self.unpack('<H', vtable)
        object_size = self.unpack('<H', vtable + 2)
        if not 4 <= size <= 128 or size % 2 or not 4 <= object_size <= 256:
            raise ValueError('Invalid table')
        if 4 + index * 2 >= size:
            return 0
        offset = self.unpack('<H', vtable + 4 + index * 2)
        if offset >= object_size:
            raise ValueError('Invalid field')
        return table + offset if offset else 0

    def string(self, pointer):
        if not pointer:
            return ''
        start = pointer + self.unpack('<I', pointer)
        size = self.unpack('<I', start)
        if size > 4096 or start + 4 + size >= len(self.payload):
            raise ValueError('Invalid string')
        if self.payload[start + 4 + size] != 0:
            raise ValueError('Unterminated string')
        return self.payload[start + 4:start + 4 + size].decode('utf-8')

    def vector(self, pointer):
        if not pointer:
            return []
        start = pointer + self.unpack('<I', pointer)
        count = self.unpack('<I', start)
        if count > 4096 or start + 4 + count * 4 > len(self.payload):
            raise ValueError('Invalid vector')
        return [pointer + self.unpack('<I', pointer)
                for pointer in range(start + 4, start + 4 + count * 4, 4)]

    def normalize(self, name, value):
        if name not in self.features:
            raise ValueError(f'Unknown coding name: {name}')
        feature = self.features[name]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(f'{name}: use an enum label or integer, not {value!r}')
        if feature['values']:
            if isinstance(value, int):
                labels = [label for label, number in feature['values'].items() if number == value]
                if not labels:
                    raise ValueError(f'{name}: invalid enum number {value}')
                value = labels[0]
            if value not in feature['values']:
                raise ValueError(f'{name}: allowed labels: {", ".join(feature["values"])}')
        else:
            if not re.fullmatch(r'-?[0-9]+', str(value)):
                raise ValueError(f'{name}: decimal integer required')
            value = int(value)
            for limit, comparator in [('minimum', lambda bound: value < bound),
                                      ('maximum', lambda bound: value > bound)]:
                if feature[limit] is not None and comparator(int(feature[limit])):
                    raise ValueError(f'{name}: value outside metadata {limit} {feature[limit]}')
        return str(value)


def load_coding(metadata, config):
    overrides = json.loads(config.read_text())
    if not isinstance(overrides, dict):
        raise ValueError('Coding JSON must be an object of NAME: value pairs')
    coding = dict(BASE_CODING, **overrides)
    normalized = {name: metadata.normalize(name, value) for name, value in coding.items()}
    geometry = tuple(int(normalized[name]) for name in ('DISPLAY_RES_WIDTH', 'DISPLAY_RES_HEIGHT'))
    if geometry != (2880, 960) or normalized['DISPLAY_SIZE'] != '1490':
        raise ValueError('This lab currently supports only the verified 2880×960 / 14.9-inch profile')
    if normalized['SERIES'] not in ('G026BEV', 'G080', 'G080LCI', 'G082LCI', 'I20'):
        raise ValueError('Supported lab series: G026BEV, G080 (experimental), G080LCI, G082LCI or I20 (experimental)')
    if normalized['SERIES'] in ('G080', 'G080LCI', 'G082LCI') and normalized.get('HMI_BRAND') not in ('bmw', 'bmw_m'):
        raise ValueError('The G80 profiles require HMI_BRAND bmw or bmw_m')
    return normalized


def save_coding(metadata, config, overrides):
    previous = config.read_text() if config.exists() else None
    with tempfile.NamedTemporaryFile(mode='w', dir=config.parent, delete=False) as stream:
        temporary = pathlib.Path(stream.name)
        json.dump(overrides, stream, indent=2)
        stream.write('\n')
    try:
        load_coding(metadata, temporary)
        if previous is not None:
            config.with_suffix(config.suffix + '.bak').write_text(previous)
        os.replace(temporary, config)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Inspect shipped CAFD metadata and build VM-only fakecod overrides')
    parser.add_argument('command', choices=['list', 'show', 'export', 'flags', 'geometry', 'profile', 'effective', 'set', 'unset'])
    parser.add_argument('names', nargs='*')
    parser.add_argument('--metadata', type=pathlib.Path, default=GUEST_METADATA if GUEST_METADATA.exists() else HOST_METADATA)
    parser.add_argument('--config', type=pathlib.Path, default=WORKSPACE / 'coding.json')
    options = parser.parse_args()
    try:
        metadata = Cafd(options.metadata)
        if options.command in ('set', 'unset'):
            expected_count = 2 if options.command == 'set' else 1
            if len(options.names) != expected_count:
                raise ValueError(f'{options.command} requires NAME' + (' VALUE' if expected_count == 2 else ''))
            name = options.names[0]
            if name not in metadata.features:
                raise ValueError(f'Unknown coding name: {name}')
            overrides = json.loads(options.config.read_text())
            if options.command == 'set':
                value = options.names[1]
                if re.fullmatch(r'-?[0-9]+', value):
                    value = int(value)
                normalized = metadata.normalize(name, value)
                overrides[name] = normalized if metadata.features[name]['values'] else int(normalized)
            else:
                overrides.pop(name, None)
            save_coding(metadata, options.config, overrides)
            print(f'Saved {options.config}; restart Hmi8 with this preset to apply. Previous file: {options.config.name}.bak')
        elif options.command in ('flags', 'geometry', 'profile', 'effective'):
            coding = load_coding(metadata, options.config)
            if options.command == 'geometry':
                print(coding['DISPLAY_RES_WIDTH'], coding['DISPLAY_RES_HEIGHT'])
            elif options.command == 'profile':
                prefix = {'G080': 'g80', 'G080LCI': 'g80lci', 'G082LCI': 'g82lci', 'I20': 'ix20'}.get(coding['SERIES'])
                m = coding.get('HMI_BRAND') == 'bmw_m'
                print(f"{prefix}-{'m' if m else 'bmw'}" if prefix else ('g26-m' if m else 'g26'))
            elif options.command == 'effective':
                print(json.dumps(coding, indent=2))
            else:
                print(';'.join(f'{metadata.features[name]["uuid"]}={value}' for name, value in sorted(coding.items())))
        elif options.command == 'list':
            for name in sorted(metadata.features):
                if not options.names or any(fragment.upper() in name for fragment in options.names):
                    print(name)
        else:
            names = options.names if options.command == 'show' else sorted(metadata.features)
            for name in names:
                if name not in metadata.features:
                    raise ValueError(f'Unknown coding name: {name}')
            result = {name: metadata.features[name] for name in names}
            print(json.dumps({'source': str(metadata.path), 'sha256': hashlib.sha256(metadata.payload).hexdigest(),
                              'features': result}, indent=2))
    except (OSError, ValueError, struct.error) as error:
        parser.exit(2, f'Coding error: {error}\n')


if __name__ == '__main__':
    main()
