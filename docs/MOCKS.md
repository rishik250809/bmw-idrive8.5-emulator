# Experimental native climate and music mocks

This document describes the original research installation. The portable
bundle enables the same mocks at startup; use `../README.md` for its commands.

The portable build has since gained native footer +/- feedback and a subset
of native vehicle signals. See `NATIVE_SIGNALS.md` for the current portable
implementation and limitations. The research-installation notes below describe
the earlier climate/music-only implementation.

Verified on the private GPU session on November 4, 2026. This is an opt-in
display/domain-state simulation for the supplied Hmi8 build, not Bluetooth,
an audio player, or a complete HVAC service.

## Use

With the prepared GPU session running:

```sh
bash hmi8_gpu_lab/install_native_mock.sh
```

Open `http://localhost:5012/mocks`, or choose **Climate & music mocks** from
settings. Set temperatures, climate on/off, title, artist, album and source
label, then click **Apply live mock values**. Allow approximately one minute
after boot; subsequent changes are polled once a second without restarting
HMI. Values persist separately for G80 M and G26. Click Apply once to
initialize a profile with no saved mock values.

The installer uses the container's existing compiler, installs the editor
and API, and restarts only the GPU container. It does not download software,
contact a headunit, or modify the firmware executable. Profile switching
through `start_gpu_session.sh` retains the installed mock.

To disable it and restart while retaining saved values:

```sh
bash hmi8_gpu_lab/install_native_mock.sh disable
```

## Scope

- Native climate-bar setpoints, simulated AUTO display, and on/off
  presentation; no image overlays or replacement renderer.
- Custom title/artist/album/source in native Home media bindings. Album is
  combined with artist on the secondary line.
- Climate **+/− touch buttons are not connected to this mock**; use the
  editor. Fan, seats, air distribution and the full climate screen are not
  simulated.
- The normal source picker still requires its original services. A
  `Bluetooth (mock)` label creates neither a paired phone nor a working
  Bluetooth player screen. Playback, skip and artwork are not implemented.
- No audio is played. No maps or vehicle-network access are required.

## Native implementation

`native_mock.cpp` is loaded only when the private guest marker
`/srv/hmi8-gpu-session/native-mock/enabled` exists. The launcher checks the
exact firmware SHA-256:

```text
9913ca569bfc78eb2b6174d0660855dc324a7b261667e0b9d20bc9352308062c
```

The preload checks the exported `CHmiProcUnit::dispatchCall` location. A
background worker discovers native objects by exact vtable, refreshing every
ten polling ticks. Vtables are checked before use; ambiguous or missing
objects are skipped. A single pending callback runs native updates through
HMI's application event queue, not a foreign rendering thread. Heap scans
stay off the UI thread. No instructions are patched in memory or on disk.

Climate data uses `DevClimateMasterMapper` and native status callbacks. The
bar also uses its native presentation setters because its ordinary update
path requires an unavailable MyModes plugin. Media uses native info adapters,
title updaters and the ArtistInfo text layout. Text updates without switching
the layout initially left the no-source placeholder visible.

These offsets and ABI layouts are specific to this binary. Do not remove the
hash gate for another build. Investigation used ordinary local symbols,
disassembly and runtime observations, not REA tool output.

## Editor and persistence

The localhost settings service exposes `GET /mocks`, `GET /api/mock`, and
`POST /api/mock`. POST accepts a complete state object and requires the
existing same-origin checks, JSON content type and `X-Hmi8-Request: 1`.
The viewer service cannot access this API. Strings are limited to 128
characters without control characters, temperatures to finite 16–28 °C,
and climate on/off to a JSON boolean. No commands or paths are accepted.

Per-profile files inside the VM:

```text
/srv/hmi8-gpu-session/profiles/<runtime-profile>/mock/state.json
/srv/hmi8-gpu-session/profiles/<runtime-profile>/mock/state.txt
```

JSON stores editor state; generated UTF-8 text is the native transport. Files
are atomically replaced and mounted read-only into HMI. A source backup is
retained in the private `native-mock/server-original.py` file.

Evidence: `hmi8_lab/logs/climate-media-20261104`. Captured-frame counters do
not prove 60 unique rendered frames per second; no performance improvement
is claimed. Keep private state, firmware, compiled artifacts and captures
out of any public source package.

## Validation performed

- Native library builds with `-Wall -Wextra -Werror`; shell and Python syntax
  checks and both settings pages' inline JavaScript syntax checks pass.
- Schema checks reject nonfinite/out-of-range temperatures, boolean-as-number,
  invalid field sets, oversized strings and C0/C1 control characters.
- Temporary-state checks cover the disabled gate, Unicode transport, file
  readability, separate profiles and invalid profile-path rejection.
- Live HTTP checks: editor 200; viewer mock API 404; POST without the required
  request header 403. Saving valid values works through the settings API.
- G80 M and G26 native captures show custom metadata and distinct temperature
  values. Switching back restores G80's separately saved values. G80 M is
  left running, with the original firmware executable hash unchanged.
- No browser automation/visual test of the editor itself was performed; its
  serving and script syntax were checked. Full climate/player backends and
  physical Bluetooth/audio remain outside this validation.
