# Local OAP / RHMI test apps

Experimental support for one compatible JavaScript OAP app at a time, tested
with [idrive8-app-test](https://github.com/rishik250809/idrive8-app-test). The
original ARM SDK and Hmi8 render its native tile, screen, and button feedback.
This does not run Intel binaries or replace the HMI with an HTML app.

Start the emulator normally first. Download the extra **ARM** `oap_fs`
partition (SWFL 00009FC7) from the [shared
drive](https://mega.nz/folder/fU0nUYRa#B0T0UnUgVRaTKrpcO-1A2A), unpack it so
`bmw-files/extracted/oap_fs/rootfs/` exists (see `bmw-files/README.md`) and import it.
If the folder or required files are missing, `apps.sh` stops and says so:

```sh
bash apps.sh import-extracted                  # reads bmw-files/extracted/
# or: bash apps.sh import-extracted "/path/to/extracted"   (another backup location)
bash apps.sh install /path/to/idrive8-app-test
bash apps.sh start
```

The import can also take the `oap_fs/rootfs` directory itself. The ordinary
three-partition emulator installation still works without OAP; custom apps
need this additional partition. Node.js is required on the Mac for local SDK
extraction. The import currently supports the tested OAP Node v16.20.2 and CDC
bundle only, verified by SHA-256. Source firmware is never modified.

Open **ID8 Bench Test** in the HMI's Apps menu, then press **Test button**.
Use the existing viewer's mouse/touch controls. No SSH headunit access is used.

```sh
bash apps.sh status
bash apps.sh stop
```

Installed files and app data are saved per emulator preset. App processes are
not automatically restarted with Hmi8; run `bash apps.sh start` after an HMI or
container restart. Reinstalling keeps the prior private app folder as a hidden
`.oap-backup-*` directory and preserves its data folder. The original app
JavaScript is unchanged; the local runner adjusts SDK startup configuration
for ARM and supplies the generated native interface schemas.

## What runs

- The firmware's ARM Node and native SOME/IP bindings, plus an SDK extracted
  locally from the user's firmware. No BMW SDK is included in Git.
- A small local lifecycle adapter for app registration, private entry-point
  lookup, and participant registration. It prestarts the app; native tile
  execute events reach the SDK directly. Bookmarks, downloads, app-store
  provisioning, cloud/joynr, and hardware resource management are unsupported.
- The original Hmi8 EntryPointManager2, ResourceManager2, and RemoteHmi2 services.
  Native application/resource creation and counter-button feedback were tested.

The complete stock MainApp/controller stack is not running. Other apps can
require services that this adapter does not provide. Vehicle-unit discovery
may log a timeout with no vehicle backend; that does not prevent the counter
screen from working. Only the counter app's source-package layout and single
entry point have been tested; this is not a general app-package installer.

Generated SDKs and app bundles remain under `.local/custom-apps/`. Imported OAP
runtime and per-preset app data remain in the private VM state. Do not publish
these generated firmware-bearing files. Logs are under
`/srv/hmi8-lab/profiles/<preset>/mock/oap/` inside the container.
