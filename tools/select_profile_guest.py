import argparse
import fcntl
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


STATE = pathlib.Path(os.environ.get('BMW_STATE', '/srv/bmw-idrive85/state'))
TOOLS = pathlib.Path(os.environ.get('BMW_RUNTIME', '/srv/bmw-idrive85/source/runtime'))
CONTAINER = os.environ.get('BMW_CONTAINER', 'bmw-idrive85')
PROFILES = {'g80-m': 'g80lci-m', 'g26': 'g26', 'ix20': 'ix20'}
PRESETS = {'g80-m': 'full-g80.json', 'g26': 'full-g26.json', 'ix20': 'full-ix20.json'}


def profile_defaults(name):
    return json.loads((TOOLS.parent / 'config' / PRESETS[name]).read_text())


def complete_profile(name, coding):
    defaults = profile_defaults(name)
    completed = dict(coding)
    if name == 'g80-m':
        for feature in ('MDRIVE', 'M_VDP'):
            completed.setdefault(feature, defaults[feature])
    return completed


def write_json(destination, document):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=destination.parent, delete=False) as stream:
        temporary = pathlib.Path(stream.name)
        json.dump(document, stream, indent=2)
        stream.write('\n')
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def identify(coding):
    if coding.get('SERIES') == 'I20' and coding.get('HMI_BRAND') == 'bmw_i':
        return 'ix20'
    if coding.get('SERIES') == 'G080LCI' and coding.get('HMI_BRAND') == 'bmw_m':
        return 'g80-m'
    if coding.get('SERIES', 'G026BEV') == 'G026BEV' and coding.get('HMI_BRAND', 'bmw') == 'bmw':
        return 'g26'
    return 'custom'


def main():
    parser = argparse.ArgumentParser(description='Switch isolated private HMI GPU profiles; one renderer at a time')
    parser.add_argument('profile', choices=['current', *PROFILES])
    options = parser.parse_args()
    sys.path.insert(0, str(TOOLS))
    from cafd import Cafd, load_coding

    STATE.mkdir(exist_ok=True)
    with (STATE / 'profile-switch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = STATE / 'config/coding.json'
        current_coding = json.loads(config.read_text())
        current = identify(current_coding)
        selected = current if options.profile == 'current' else options.profile
        saved = STATE / 'config/profiles'
        write_json(saved / f'{current}.json', current_coding)  # 'custom' keeps any other combination
        for name in PROFILES:
            destination = saved / f'{name}.json'
            if not destination.exists():
                write_json(destination, profile_defaults(name))
            else:
                existing = json.loads(destination.read_text())
                completed = complete_profile(name, existing)
                if completed != existing:
                    backup = destination.with_suffix('.before-m-setup.json')
                    if not backup.exists():
                        shutil.copy2(destination, backup)
                    write_json(destination, completed)
        candidate = saved / f'{selected}.json'
        metadata = Cafd(STATE / 'rootfs/usr/share/sysfunc/coding/cafd_mgu_02_a.bin')
        target_coding = load_coding(metadata, candidate)
        if identify(target_coding) != selected:
            raise ValueError('Saved profile coding does not match its name')
        required = ['a6b0', 'a6b1'] + (['aed4'] if selected == 'g26' else [])
        required += ['a6b2', 'a691'] if selected == 'g80-m' else []
        for sgbm in required:
            found = sorted((STATE / 'resources').glob(f'swfk_0000{sgbm}_*/rootfs/hmi_version'))
            if not found:
                raise FileNotFoundError(f'Missing imported package SWFK 0000{sgbm.upper()}; run import.sh with it')
        running = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.Running}}', CONTAINER], text=True).strip() == 'true'
        subprocess.run(['docker', 'stop', '-t', '15', CONTAINER], check=True)
        try:
            initialized = STATE / 'profiles/.initialized'
            if not initialized.exists():
                for area in ('public', 'personal'):
                    destination = STATE / 'profiles' / PROFILES[current] / area
                    if destination.exists():
                        raise FileExistsError(f'Refusing to overwrite unmigrated state: {destination}')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(['cp', '-a', str(STATE / 'rootfs/var/opt' / area), str(destination)], check=True)
                initialized.touch()
            for name, runtime in PROFILES.items():
                for area in ('public', 'personal'):
                    (STATE / 'profiles' / runtime / area).mkdir(parents=True, exist_ok=True)
            archive = STATE / 'profiles' / PROFILES[current] / 'last-logs'
            archive.mkdir(exist_ok=True)
            for name in ('mesa-g26.stdout.txt', 'mesa-g26.stderr.txt', 'mesa-g26.hmi-dlt.txt', 'mesa-g26.weston.txt', 'mesa-g26.persod.txt', 'mesa-g26.coding-overrides.json'):
                source = STATE / 'logs' / name
                if source.is_file():
                    shutil.copy2(source, archive / name)
            write_json(config, json.loads(candidate.read_text()))
            subprocess.run(['docker', 'update', '--restart=unless-stopped', CONTAINER], check=True)
            subprocess.run(['docker', 'start', CONTAINER], check=True)
        except Exception:
            write_json(config, current_coding)
            if running:
                subprocess.run(['docker', 'start', CONTAINER], check=False)
            raise
        print(f'Active profile: {selected}; state: {STATE / "profiles" / PROFILES[selected]}')
        print('Saved coding: ' + str(candidate))


if __name__ == '__main__':
    main()
