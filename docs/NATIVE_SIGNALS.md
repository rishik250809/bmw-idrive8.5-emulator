# Native vehicle, power and climate feedback

Tested locally on November 10, 2026, with the G80 profile and this exact Hmi8:

`9913ca569bfc78eb2b6174d0660855dc324a7b261667e0b9d20bc9352308062c`

This is a build-gated, in-process HMI adapter, **not** a complete BMW SOME/IP
server, electrical terminal emulator, or physical vehicle connection. It uses
the original native status handlers and the HMI application dispatcher. No
overlay renderer or replacement vehicle picture is used. Other firmware builds
must not reuse these offsets. G26 runtime behavior has not been verified here.

## Verified native inputs

- **Vehicle speed and gear:** the original `CDevCarVehicleState` callbacks
  receive speed (raw unsigned 16-bit, 1/64 km/h units) and selected gear.
  Actual native getters read back 42 km/h / gear D, and stationary / gear P.
- **Ignition-like vehicle power state:** `CDevCarPWF::onVehicleCondition`
  and `CDevCarPowerMode::onPxyVehicleCondition` receive the vehicle condition.
  Shipped `de_bmw_infrastructure_VehicleCondition.json` supplies the enum
  definitions: off maps to `PARKEN_BN_IO` (2), accessory to `WOHNEN` (5),
  ready/on to `FAHRBEREITSCHAFT_HERSTELLEN` (8), moving/on to `FAHREN` (10).
  Native PWF getters return Parken=0, Wohnen=1, Fahren=2 in the tested states.
  This does not emulate KL15 wiring. `EIgnitionKey` identifies the key/fob;
  changing its numeric value is **not** an ignition emulator.
- **Body state:** the original `Staging3dService` panel-status callback
  receives front-left/front-right/rear-left/rear-right door, bonnet and boot
  flags. Native model state is read back independently of the input file.
  The G80 Live Vehicle/Adaptive Content screen visibly opens its front-left
  door and boot when those controls change, and closes them again.
- **Climate footer:** native +/- callbacks now save temperatures and update
  their actual native bar immediately. Tested driver +, driver -, passenger +,
  and persistence/readback through the API. Values clamp to 16..28 Celsius.
  Native temperature notifications now use front operation zones 1 and 2
  (driver/passenger), correcting the previous 0/1 assumption.

Native readbacks appear in `/api/services` under `native_feedback` and in the
panel's live output. A returned callback/readback is evidence of native input
delivery, not proof that every downstream widget or backend works. Fields are
labelled individually as native or lab-only to avoid confusing stored JSON
with working HMI functionality.

## Native menus and unresolved contents

### Parking / PDC

The experimental **Open native parking menu** button opens the real PARKING
page. These two overrides were sufficient to instantiate the modern parking
service and open the shell in the tested G80 installation:

```json
{
  "MACRO_PDC": "enabled",
  "COMMUNICATION_PARKMASTER": "gen_2021"
}
```

They are a tested menu-opening configuration, not a full PDC coding recipe.
The tested opening case was stationary, speed 0 and gear P.
The local G80 was left with these two additions; all previous overrides were
preserved. Other profiles are not silently recoded. With no matching modern
service instance, readback reports `parking_not_coded_or_not_instantiated`.

**Native coloured sensor sectors now work through the separate `/pdc`
simulator.** It provides parking SOME/IP services, runs DidUi, and bypasses
the CID scene's intermediate rendering path. Front/rear distance changes were
verified on actual native frames. See `PDC_SIMULATOR.md` for implementation,
car-size settings and remaining limitations. The older menu-opening callback
alone does not supply this backend. No camera video is supplied; sensor-only
PDC does not require the camera player.

### Climate

**Toggle native climate menu** invokes the original climate controller. The
real CLIMATE CONTROL page opens with its interior/dashboard graphic. Repeating
the request uses the native close/toggle behavior.

**The full HVAC controls are still incomplete.** Front-zone availability and
AUTO/AC/fan status now reach native callbacks; AUTO changes the native artwork.
Seat heat, recirculation and defrost remain lab-only. Native AUTO/AC/fan touch
requests are not yet echoed back. See `REALTIME_SERVICES.md` for current limits.

## Implementation and persistence

`container/mock_state.py` emits the validated `native-services.txt` transport
when simulator state is committed. `runtime/native_services.cpp` is appended to
the existing preload by the normal entrypoint. Anonymous writable memory is
searched off the UI thread for exact vtables; absent or ambiguous objects are
skipped. Status callbacks are dispatched onto the original HMI application
thread. Readbacks are written atomically to `native-feedback.json`.

Footer requests are build-gated and update `climate-feedback.txt` in the active
profile's mock directory. Both the existing poller and Python status API read
that authoritative feedback; unrelated media edits do not reset a native
temperature change. Explicit editor temperature changes replace it normally.
These files reside under the same private per-profile mock mount as before.

Menu requests are one-shot and are not replayed after an HMI process restart.
The native worker periodically checks object validity and refreshes readbacks;
new data does not involve a separate renderer or physical vehicle network.

Validation includes 18 isolated state/API tests, Linux C++ compilation with
`-Wall -Wextra -Werror`, native getter checks and captured native HMI frames.
Private evidence is under `.local/native-services-20261110/`, excluded from Git.
One startup attempt returned the previously observed startup SIGTRAP 133
during initial shader uploads, before the native service worker started;
a bounded retry succeeded. Its underlying cause was not established here.
No claim is made that this startup issue or all backend dependencies are fixed.
