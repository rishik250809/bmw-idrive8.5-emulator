# Park distance control simulator (experimental)

Settings page → **Park distance control** (`http://localhost:5012/pdc`).

## What it does

Hmi8's modern parking stack waits for the parking ECU, which is not part of any
SWFL. The simulator supplies that side and runs the firmware `DidUi` renderer:

- `runtime/pdc/parkmenu_server.cpp` — a vsomeip 3.5 application (built on first
  start inside the container against the firmware `libvsomeip3`, headers fetched
  from the COVESA 3.5.0 tag) offering:
  - `ParkmenuAndLayout` 0x3059/1 — `requestParkmenu` (layout, visibility);
  - `ParkDistanceInformationProvision` 0x3020/1,2 — front/rear gaps, PDC status;
  - `PDCFunctions` 0x300e/1,2 — left/right flank protection;
  - zero-filled companions 0x3064, 0x3077, 0x3558, 0x3076, 0x300c so DidUi's
    proxies become available.
  Interface IDs/structs come from `oap_fs/etc/oap/someip/someip-interfaces.json`.
- `container/pdc_sim.py` — starts both helpers in Hmi8's mount/network namespace,
  copies `usr/share/didui` once to `/srv/hmi8-lab/pdc/didui-assets` (with JSON
  codings for the active profile), and writes `/tmp/pdc.txt` in Hmi8's namespace.
  Optional DidUi demo scenes patch one instruction (`did.sab.demo.parking.sceneNumber`
  default) in a private copy; the firmware file is not modified.
- `runtime/pdc/parking_view.cpp` — DidUi-only rendering compatibility adapter.
  Directs the CID scene's `RenderPass_default` to its output and disables the
  intermediate `offscreenbuffer render pass`. This makes the native sensor
  meshes visible; their geometry, shaders, textures and distance logic remain
  unchanged. The original extracted scenes are not edited.
  It also adjusts top-down car-view distance in Ramses metadata. Hmi8 renders
  the vehicle and shares the camera transform with DidUi, keeping both aligned.

**Car & sensor size** selects original, 125% (default), or 150%. Press
**Start / restart** to apply a size change. The value is saved with PDC state;
150% can crop the outer sensor zones in the fixed parking content area.

Not wired into `start.sh`; press **Start** after the HMI is up and again after an
HMI restart.

## Verified

- Starting the simulator opens the native PARKING page from Home; Hmi8 logs
  `EParkmenuLayout = ePdcOnly` and reports `eVisible`.
- Layout switching works live (e.g. `PARKSENSORS_ONLY_ASSIST` = 1 for the
  non-Pro Parking Assistant, `PARKVIEW_ASSIST` = 6).
- DidUi receives the gaps: an `LD_PRELOAD` probe on `rlogic::Property::setInternal`
  showed `PDC_visible=true`, `PDC_FrontSector*Distance` = the sent values,
  `Opacity=1`, `Disabled=0` in the CID scene.
- Demo scene 2 draws DidUi's red cross-traffic-alert corners (not PDC sectors).

## Native sensor rendering

The native coloured front/rear sectors and flank graphics are visible with the
rendering adapter. All-clear (255) removes the zones; obstacle values show them.
The fault was narrowed to the intermediate rendering path: a solid fragment
and then a clip-space rectangle remained invisible through that path, while
drawing directly to the scene output made the rectangle and the original sensor
artwork visible. Forcing camera projection, culling, depth or node visibility
is not required. The exact fault inside the intermediate path remains unknown;
the adapter bypasses it without a viewer overlay or shader replacement.

The earlier inference that the visible car proved DidUi's 3-D pass was working
was incorrect: the car comes from Hmi8's separate vehicle content, positioned
using DidUi metadata. A visible car alone does not verify the sensor pass.

## Remaining limitations

- `on=false` sends `CLEANUP_REQUESTED`; Hmi8 receives `eCleanup` but keeps the
  page open (use Back).
- Gap units (cm?) and sector ordering are unverified.
