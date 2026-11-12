# Immediate service controls

The settings service at `http://localhost:5012/services` saves checkboxes and
selections immediately. Text and number edits wait 300 ms; leaving a field
saves immediately. Structured fields require valid JSON. Apply retries failed
saves. Changes made during a request are retained and saved afterward; only
edited fields are submitted. Switching profiles discards pending edits for
the previous profile.

The page contains vehicle power/body/lights, climate, media and M controls.
Speed, gear, odometer, fuel/battery, GPS, driver personalization and clock
controls are omitted. Parking controls live only on `/pdc`. Internal saved
state and native transport defaults remain compatible with existing profiles.

The native service adapter checks its transport every 50 ms. Application-thread
dispatch and rendering add latency; this is not a hard real-time guarantee.
Heap object discovery remains on a ten-second interval, off the UI thread.
The separate existing media/temperature/M adapters still have their own polling
intervals. An automatically saved lab-only field still has no native effect.

## Vehicle lights

Build-gated inputs target the original Live Vehicle model. They do not create
instrument-cluster telltales or emulate a vehicle lighting ECU.

- Indicators: original `Staging3dService::onControlFunktionsIndicators2` at
  `0x8aaf700`, wrapper byte `+0x50`: off 0, left 1, right 2, hazards 8.
  The mock supplies alternating on/off phases every 500 ms.
- Low/high beams and running/reverse lights use the original LiveCar DMI
  setters called by `Staging3dService::updateLights`. The similarly named
  `onStatusLightStatus` is a no-op in this build and is not used.
- `auto` represents daytime running lights; `parking` uses the running-light
  representation. These are deliberate mock approximations.
- Feedback exposes native DMI low/high-beam and indicator getters in addition
  to staging's indicator flags. A command echoed in JSON alone is not proof
  that a native property changed.

## Climate progress and limits

The adapter now supplies original ClimateMaster availability messages for
the front zones, using enum objects copied from this binary's mapping tables.
It supplies AUTO, AC and fan status through original native callbacks. AUTO
mode 0 matches the request built by the original AUTO button. Fan status uses
a 32-byte status message and a network-byte-order level; the 48-byte fan
request is a different ABI.

Native AUTO and fan getters reflect edits, and AUTO switches the native
automatic/manual artwork. **The full menu controls remain incomplete.**
Seat heat, recirculation and defrost are not connected. Native AUTO/AC/fan
button requests are not echoed back into mock state. Temperature +/- touch
feedback remains supported by the existing temperature hooks.

## Separate native PDC simulator

The `/pdc` page now supplies the parking backend and native sensor visualization
through DidUi. Front/rear distance changes and all-clear were verified on native
frames. Its render adapter bypasses an intermediate CID rendering path and
offers shared vehicle/sensor zoom. See `PDC_SIMULATOR.md`. The older Services
parking inputs are separate and no longer shown on the Services page.

The actual parkmenu SOME/IP proxy is the nonempty
`libde.bmw.dass.parking.parkmenu.parkmenuandlayout.proxy.someip.so.3.2.3-54.1.1`;
the sibling ending in `54.1` in this extraction is empty. The native
`DevParkmenuAndLayout::onRequestParkmenuAttribute` consumes structured enum
objects and a layout handshake, not a boolean activation value.

The firmware's separate camera player describes its purpose as decoding and
displaying digital AVB camera video. Its systemd service depends on the
vehicle video/AVB stack and hardware device nodes. Neither a camera producer
nor its player backend runs in this emulator. The HMI has a separate
`SvcVideo`/`VideoConnectionHandler` path for that surface. Supplying layout,
activation and distance values does not supply video. DidUi renders the sensor
zones; Hmi8 composes the vehicle and menus.

No screenshot overlay or replacement HMI graphics were added. Rear/surround
camera video still requires a compatible producer for the native video path.

All offsets refer only to Hmi8 SHA-256
`9913ca569bfc78eb2b6174d0660855dc324a7b261667e0b9d20bc9352308062c`.
Evidence comes from symbols, LLVM disassembly and the local Lima runtime;
no physical headunit was modified.
