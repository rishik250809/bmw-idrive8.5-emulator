# Local service simulator

Open `/services` on the **settings** port (normally `localhost:5012`). The
settings and native mock pages link to it. Implementation lives in
`container/mock_state.py`; no additional Python packages are required.

This is an offline simulation state store and event loop, **not** a replacement
for the firmware's SOME/IP, joynr, Bluetooth, navigation or camera services.
The portable build now connects a verified subset of vehicle signals and
native climate touch feedback. See `NATIVE_SIGNALS.md` for tested results and
remaining blockers. Native parking sensors have a separate simulator at `/pdc`;
see `PDC_SIMULATOR.md`. Personalization and GNSS remain development state.

The Services page exposes vehicle power/body/lights, climate, media and M
controls. Speed, gear, odometer, fuel/battery, GPS, driver personalization,
clock and parking controls are omitted. Internal state/API fields below remain
for compatibility; their presence does not mean they have page controls.

## What works

- Shared ignition, gear, speed, odometer, doors, lights and charge/fuel state.
  Moving requires ignition on, a non-P gear and released parking brake.
  Native adapters receive PWF power state, gear, speed and six door/hood/boot
  states, plus native Live Vehicle lights and flashing indicators/hazards.
  Odometer, brake and charge/fuel remain lab-only.
- Climate editor and half-degree step API. Setpoints/on-off use the existing
  native climate-bar adapter when installed. AUTO/AC/fan status reaches native
  callbacks; the full climate menu remains incomplete and seat controls are
  simulator-only. Native footer +/- touch now saves setpoints as well.
- A simulated phone, playlist, play/pause/resume/stop, previous/next, volume,
  mute and playback position. Track text uses the existing native media
  adapter; playback is silent and the connection is not real Bluetooth.
- Save/recall M1 and M2 snapshots. The existing native M feedback receives
  engine/chassis/steering/brake/xDrive selections. DSC, gearbox, traction and
  M Mode remain simulator-only. Native M1/M2 buttons are not intercepted.
- Reverse-triggered parking state, four front/rear distances in centimetres
  (`null` = no obstacle), and a camera-selection value. Synthetic zone labels
  use red below 40 cm, yellow below 100 cm, otherwise green. These are test
  conventions, not reverse-engineered BMW sensor units or physical sensors.
  Native sensor visualization now works through the separate `/pdc` simulator;
  see `PDC_SIMULATOR.md`. Camera video is not supplied. These older Services
  parking inputs are not connected to that simulator and are omitted from
  the page.
- Profile-specific driver label and simulated layout; this does not replace
  the existing native personalization daemon or change coding.
- Finite latitude/longitude, heading and a bounded route of up to 256 points.
  Movement uses shared vehicle speed and great-circle distances. A synthetic
  Munich-coordinate scenario is included, not a map database or road router.
  Route motion stops at the final point unless heading movement is enabled
  with `move_with_vehicle`. No native GNSS, navigation or AR camera is fed.

## Clock and persistence

Time starts paused. Choose **Start simulated time** for roughly one-second
updates, **Pause time**, or **Advance 1 second** for deterministic steps.
Playback, odometer and GNSS consume the same elapsed simulation time. A delayed
runtime tick advances at most five seconds; an explicit advance accepts up to
60 seconds. Process restarts and profile switches pause automatic simulation;
there is no wall-clock catch-up. The route scenario does not start the clock.

State is atomically saved under
`/srv/hmi8-lab/profiles/<active-profile>/mock/services.json`, separately for G26
and G80. The last 100 explicit actions are retained, with revision numbers;
tick events are not added to the history. Existing climate/media and native M
feedback are read back rather than overwritten by unrelated simulator edits.
The existing firmware hash checks and native mock enablement are unchanged.

## API

`GET /api/services` returns state, revision, profile token, field schema,
capability limitations, derived parking zones and recent events. It is available
only on the settings port. `POST /api/services` uses the existing localhost,
same-origin, JSON, 64 KiB and `X-Hmi8-Request: 1` checks.

Every command must include the `profile` token returned by GET. An optional
integer `expected_revision` rejects stale writes. The browser sends only edited
fields so an edit does not reset unrelated advancing state.

```json
{"profile":"g80lci-m","action":"patch","changes":{"vehicle":{"ignition":"on","gear":"D","parking_brake":false,"speed_kmh":30}}}
```

Actions: `patch`, `connect`, `disconnect`, `play`, `pause`, `stop`, `next`,
`previous`, `climate_step` (`zone`: `left_temperature`/`right_temperature`,
`delta`: -0.5/0.5), `save_m`/`recall_m` (`slot`: `m1`/`m2`), `start_clock`,
`pause_clock`, `advance` (`seconds`: 0.01..60), `scenario_reverse`,
`scenario_drive`, `load_demo_route`, `native_parking_menu` and
`native_climate_menu` (the native toggle behavior). Unknown fields and invalid scenarios
are rejected before writing state or projecting native values.

No network packets are sent to a headunit, no cloud services are contacted, and
no original firmware, maps, VM disks or coding presets are modified by these
commands. Keep generated simulator state in the private runtime, not Git.
