# Private GPU HMI profiles

This document describes the original research installation. For the portable
bundle's paths and commands use `../README.md`: its VM/container is
`bmw-idrive85`, and its guest state is `/srv/bmw-idrive85/state`.

Optional per-profile climate/media state controls are documented in `MOCKS.md`.

Validated on November 4, 2026 against the already prepared local GPU VM.
These commands switch one renderer between two isolated setups; they do not
build a fresh installation or run two renderers concurrently.

From the research directory:

```sh
bash hmi8_gpu_lab/start_gpu_session.sh g80-m
bash hmi8_gpu_lab/start_gpu_session.sh g26
```

No argument restarts the currently selected profile. The viewer/touch endpoint
is `http://localhost:5011`; coding/settings is `http://localhost:5012`.
Allow approximately one minute for startup. A black initial display may need
Display On and a Home tap after initialization; API readiness alone does not
prove visible rendering.

## What stays separate

| Setup | Coding | Brand resources | Vehicle graphics | Guest persistent state |
| --- | --- | --- | --- | --- |
| G80 M | G080LCI / bmw_m | A6B2 007_023_004, plus BMW/shared resources | A691 007_023_004 / G80LCI | profiles/g80lci-m |
| G26 BMW | G026BEV / bmw | A6B1 007_023_004 / BMW shared | AED4 007_023_004 / G26BEV | profiles/g26 |
| iX xDrive40 i20 (experimental) | I20 / bmw_i | not verified; requires iX-specific resources | not verified | profiles/ix40 |

Both retain the working ANGLE/Venus/Metal rendering path and native 2880x960
geometry. Preview scale is 2; target capture rate is 60, not a verified 60 fps
of changing application content. No maps are mounted by this session.

Private state lives under `/srv/hmi8-gpu-session` inside `mgu22-gpu-test`:

- `config/profiles/g80-m.json` and `config/profiles/g26.json`: saved coding.
- `profiles/<runtime-profile>/public` and `personal`: separate persistence.
- `profiles/<runtime-profile>/last-logs`: logs archived at profile switching.

The switcher saves active coding edits before switching, validates resources,
and mounts the selected persistence directories. Original pre-isolation state
is retained, not deleted. Shared firmware/resources are mounted read-only;
they are not duplicated per profile. G26 does not mount the M brand package.

Host JSON files in `hmi8_lab` seed a profile only when its saved guest config
does not already exist. Later changes should use settings or the saved guest
profile, not assume editing the host seed overwrites saved coding.

Settings includes `barestock`, `full-g26` and `full-g80` presets. Prefer the shell switcher
to preserve outgoing coding edits and archive logs; preset activation alone
does not perform that save/archive step. The settings Restart action restarts
this container, not the VM or Docker engine. Its restart policy is
`unless-stopped`; an explicit Docker stop remains stopped.

## Why Home had no tiles

The widget registry waited for a personalization user-change event. Without
`persod`, `startWidgets` remained inactive and default Home configuration was
never created. The root configuration also pointed to `/var/data` instead of
the IVI `/var/opt` personalization paths.

The launcher mounts the IVI personalization configuration and runs the actual
firmware `persod` with its native libraries. It uses the daemon's `rse`
guest-only mode for this offline lab, avoiding the unavailable MGU PIA profile
backend. This is a personalization workaround, not an emulation of PIA,
ConnectedDrive, a complete RSE, or the rest of the vehicle. HMI coding remains
MGU/G80 or MGU/G26; it is not changed to RSE.

HMI logs now show `CStartWidgetRegistry::restoreSettingsFromKeyValueStorage`
and `createDefaultConfiguration`. Genuine Home tiles were visually verified
on G26 and G80 M. Empty navigation/media content is still expected without
their backend services. No tiles were painted into the viewer.

## Validation and remaining limitation

Shell syntax, Python compilation, profile validation, switching, separate
persistence mounts, and settings-triggered container restart were checked.
Evidence is in `hmi8_lab/logs/home-tiles-20261104`.

One first G26 launch exited with SIGTRAP (133) during shader uploads. A plain
container restart succeeded and produced tiles; the cause of that isolated
failure is not established. Its original logs are preserved in
`g26-rse-crash.tar`. The tile initialization fix is demonstrated, but cold
startup reliability is not yet proven. Do not mistake a running HTTP server
or its captured-frame counter for a healthy HMI process.

Keep firmware, private coding/personalization data, captures, maps and keys
out of a public repository. This profile helper is for the existing private
installation; it does not make BMW firmware redistributable.
