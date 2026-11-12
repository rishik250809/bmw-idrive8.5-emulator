# BMW IDrive 8.5 Emulator

## WARNING: It will only work on a Mac with M Chips, macOS 26+.


### A standalone IDrive8.5 Emulator which runs on ARM linux based VM locally on a Mac. No connection to anything else.
### Featuring a G80 M3 and a i4 G26 emulator preset.


### Main Viewer (http://127.0.0.1:5011):
![cover.png](img/cover.png)

### Setting/Coding Page (http://127.0.0.1:5012):
![cover2.png](img/cover2.png)

#### Output will show on a website, use the mouse to control the Emulator.

## Architecture

Mac → Lima/Krunkit Fedora VM → privileged ARM64 Docker container → firmware chroot.

Docker alone cannot replace the VM's host-GPU bridge.  The bundle contains a tooling-only
Docker image, not a VM image disguised as a Docker image.

Setup copies the original MGU22 firmware files into the new VM. Docker bind mounts guest directories:

- `/srv/bmw-idrive85/state` → `/srv/hmi8-lab`: private extracted runtime and state.
- `/srv/bmw-idrive85/driver` → `/opt/venus`: guest driver libraries, read-only.
- `/srv/bmw-idrive85/source/{runtime,container,web,config}` → tool/UI directories,
  read-only.

Neither original research VM is modified by setup. 
The default new VM in `limactl`(lima) is `bmw-idrive85`, 6 vCPUs, 4 GiB RAM, 20 GiB sparse disk. 


## Requirements and compatibility

### Download the necessary original MGU22 Firmware folders from this [shared drive](https://mega.nz/folder/fU0nUYRa#B0T0UnUgVRaTKrpcO-1A2A) 

I cannot upload the files to github because of their big file size.

Files needed from the shared drive:

```aiignore
SWFL:
swfl_00009FC6(ivi_fs.zip) --> contains the actual HMI8 binary and it's supporting libraries and services
swfl_00009FF7(ROOT_rootfs.zip) ---> Base linux filesystem, of a MGU22 Headunit. 
swfl_00009FF9(ext_fs.zip) --> additional libraries for ROOT and IVI

SWFK:
swfk_0000A6B0 --> Shared BMW interface resources
swfk_0000A6B1 --> BMW interface resources(non M models)
swfk_0000A6B2 --> M chassis BMW interface resources
swfk_0000AED4 --> G26 i4 graphics
swfk_0000A691 --> G80 M3 graphics

```

- Apple Silicon Mac, macOS **26.0 or newer**
  It uses the Metal 4 API, which has been introduced in macOS 26.
- Tested on a M1 Pro, macOS 26.7.1, Lima 2.1.4, Krunkit 1.3.2. 
- Also tested on M5 Pro, macOS 26.7.2 and 27.2 (Lima 2.3.0, Krunkit 1.3.2, libkrun 1.19.8 from the
  `libkrun/krun` tap), using the Hmi8 BTI compatibility library described below.
- Reported working on an M2 Pro with macOS 27.3 (after the libkrun fix below, see
  [issue #3](https://github.com/rishik250809/bmw-idrive8.5-emulator/issues/3)).
  Other Apple Silicon models are not yet verified.
- At least 4 GiB available guest RAM recommended, 16 GiB total host RAM recommended.
- At least 25 GiB of free disk space before installation (enforced by the installer).
- Homebrew, Python 3.11+, SSH
- Network access

Install missing prerequisites yourself, after reviewing their upstream sources:

```sh
brew install lima python vulkan-loader libepoxy spirv-tools molten-vk
# libkrun must come from the libkrun/krun tap (not Homebrew's core tap) and be installed before krunkit:
brew install libkrun/krun/libkrun libkrun/krun/virglrenderer-krun
brew install libkrun/krun/krunkit
```

**Important:** `libkrun` must come from the `libkrun/krun` tap, not from Homebrew's core tap. The core
`libkrun` (1.19.8 and newer) links MoltenVK directly, so the project's Mac GPU driver is silently ignored.
`bash doctor.sh` (run by every script) checks this; you can also run `bash scripts/check_libkrun.sh`.
See [Troubleshooting](#troubleshooting). Homebrew 7 may ask you to trust the tap formulae first:
`brew trust --formula libkrun/krun/libkrun libkrun/krun/virglrenderer-krun`.

## First run

The emulator needs BMW firmware packages that this project needs, located in the [Shared drive](https://mega.nz/folder/fU0nUYRa#B0T0UnUgVRaTKrpcO-1A2A).

**A. Clone the repo**
```sh
git clone https://github.com/rishik250809/bmw-idrive8.5-emulator
cd bmw-idrive8.5-emulator
```

**B. Run the install script.** 

**Before running**, Put the extraced BMW packages from the shared drive into `bmw-files/`
(the list is in [`bmw-files/README.md`](bmw-files/README.md)), then:

```sh
bash install.sh                       # or: bash install.sh /path/to/copyofshareddrive
```

It installs the Homebrew tools, downloads and verifies the open-source graphics
pieces, creates the VM, verifies and imports your BMW files, starts the HMI,
waits until it is on screen and opens http://localhost:5011. Allow 10–20 minutes
for a first installation, depending on downloads and image building. Running it
again skips finished steps. Afterwards use `bash start.sh g26` (G26),
`bash start.sh g80-m` (G80), or `bash start.sh` to resume the current profile.

**C. From a prepared private bundle** (`private/runtime.tar.gz`, made from your
own files; never share it):

```sh
bash doctor.sh
bash setup.sh
bash start.sh g80-m
```

First ensure no other viewer uses ports 5011/5012, or choose another port pair.

Open `http://localhost:5011` for viewer/touch and `http://localhost:5012` for
coding/settings. Music/climate controls are at `http://localhost:5012/mocks`.
Allow around one minute for initialization.

Switch to the ordinary BMW electric setup with `bash start.sh g26`.
No argument restarts the current profile. One renderer runs at a time.
`bash stop.sh` stops the container; `bash stop.sh --vm` also shuts down the VM.
The settings Restart button restarts only the container.

If 5011/5012 are occupied, use `BMW_VIEWER_PORT=5021 bash start.sh g80-m`.
This forwards guest ports 5011/5012 to Mac ports 5021/5022. Hyperlinks follow
the adjacent port pair. Set `BMW_VM` consistently on all commands if choosing
a different VM name.

Setup refuses to overwrite an existing runtime. Never delete the guest state
directory without backing up coding and mock values. To update UI/source
without reimporting firmware, use `bash scripts/sync_source.sh`, then restart
the selected profile.

To follow native HMI touch/input logs live (the log name is shared by the G26
and G80 profiles; individual events depend on the HMI's logging verbosity):

```sh
limactl shell bmw-idrive85 -- sudo tail -n 50 -F /srv/bmw-idrive85/state/logs/mesa-g26.hmi-dlt.txt | grep --line-buffered -Ei 'hmitouch|hmiinput|touch.*event|onTouch'
```

`mesa-g26.touch.txt` contains touch-bridge startup and diagnostic output, not a
record of every touch.

## Custom apps (experimental)

The emulator can run one JavaScript **OAP / RHMI app** (for example
[idrive8-app-test](https://github.com/rishik250809/idrive8-app-test)) on the
original firmware: the app appears as a native tile in the HMI's **Apps** menu,
Hmi8 draws its screen from the app's RHMI description, and touch presses reach
the app's code. It uses the firmware's own ARM Node.js and an SDK extracted
locally from the given OAP_FS BMW firmware (no BMW SDK is in this repository), and no headunit
or SSH access.

Start the emulator first. Custom apps need one extra firmware partition, the
ARM **OAP** filesystem (SWFL 00009FC7), next to the others in
`bmw-files/extracted/oap_fs/rootfs/` (see `bmw-files/README.md`). Then:

```sh
bash apps.sh import-extracted                                      # once: adds the ARM oap_fs partition from bmw-files/extracted/
bash apps.sh install /path/to/custom-app                     # installs/replaces the app for the current profile
bash apps.sh start                                                 # run again after every HMI/container restart
bash apps.sh status                                                # also: bash apps.sh stop
```

Limits for now: one compatible app at a time, only the counter app's package layout is
tested, and only a small local adapter replaces the stock MainApp (no
bookmarks, downloads, app store, cloud or hardware resources). An app that
reads vehicle data logs a timeout, because there is no vehicle backend.
Details: [`docs/CUSTOM_APPS.md`](docs/CUSTOM_APPS.md).

## Simulator pages

Besides coding (`http://localhost:5012`), the settings port serves:

- `/mocks` — climate bar, media text and M Setup selections (saved per preset).
- `/services` — vehicle and service simulator ([docs](docs/SERVICE_SIMULATOR.md)).
- `/pdc` — experimental park distance control: opens the native PARKING page
  with the PDC-only layout and sends sensor values. The coloured sensor
  sectors do not render yet ([docs](docs/PDC_SIMULATOR.md)).

## Troubleshooting

**The viewer sits on "HMI output is taking longer than expected", or shows a black screen.**

Check the HMI log:

```sh
limactl shell bmw-idrive85 -- sudo tail -n 40 /srv/bmw-idrive85/state/logs/mesa-g26.stderr.txt
```

If it contains `Extension not supported: VK_KHR_swapchain` (Vulkan error `-7`), krunkit is not using the
project's Mac GPU driver. The usual cause is a `libkrun` from Homebrew's core tap (1.19.8 and newer), which
does not load a separate `libvirglrenderer` that the project driver could replace. `bash doctor.sh` reports
this. Fix it by switching to the `libkrun/krun` tap build (this replaces Homebrew's `libkrun`; quit other
libkrun-based VM tools first):

```sh
bash stop.sh --vm
brew uninstall --ignore-dependencies libkrun
brew trust --formula libkrun/krun/libkrun libkrun/krun/virglrenderer-krun || true   # newer Homebrew only
brew install libkrun/krun/libkrun libkrun/krun/virglrenderer-krun
bash doctor.sh
bash start.sh g80-m
```

To confirm the project driver is loaded, krunkit must list the project's own `libvirglrenderer.1.dylib` and
`libvulkan_kosmickrisp.dylib` (and no MoltenVK):

```sh
lsof -a -p "$(cat ~/.lima/bmw-idrive85/krunkit.pid)" -d txt -Fn | grep -iE 'virgl|kosmic|vulkan|moltenvk'
```

Always start and stop the VM with `start.sh` / `stop.sh`; they set the environment that makes krunkit load
the project driver. Starting it directly with `limactl` does not.

## Migrating to another Mac

Keep the existing VM and firmware imports. If Homebrew was copied from another
Mac, rerun `bash install.sh` to restore the explicit host dependencies, including
MoltenVK. Use `bash start.sh` rather than a direct `limactl restart`: the project
requires its bundled host graphics driver. `start.sh` detects a running VM using
the system driver and restarts it with the project driver.

On BTI-capable CPUs, the legacy Hmi8 binary can exit with SIGILL (code 132) at a
PLT entry that lacks a BTI landing instruction despite the binary advertising
BTI support. A preload compatibility library removes BTI enforcement only from
Hmi8's executable mappings in memory; it does not change the firmware files,
disable PAC, or disable BTI for other libraries or processes. It is inactive on
CPUs without BTI. After updating these sources, run `bash scripts/sync_source.sh`
and `bash start.sh` to rebuild and load the compatibility library in the VM.

## Presets given

- `barestock`: only developer menu, sport displays and km units coded; everything else
  uses the firmware defaults (G26 BEV, BMW).
- `full-g26`: G26 BEV (i4), BMW brand, electric, navigation, home tiles. (**proper coding**)
- `full-g80`: G80 LCI (M3), BMW M brand, petrol, Isle of Man Green and the
  tested M feature flags. (**proper coding**)
- More in the future!

If the brand/model is specified as `HMI_BRAND`: `bmw_m`, it mounts the BMW M brand package
(SWFK 0000A6B2), otherwise `bmw` uses the base BMW package (A6B1). The car graphics follow
`SERIES` (G026BEV, G080LCI, G082LCI). 
Each series/brand combination keeps its own
saved HMI state, created on first use.
Use `start.sh` to switch complete profiles and preserve outgoing coding.

Mock features are display-state simulations, not actual Bluetooth/audio/HVAC (for now).
Navigation, AR camera, ConnectedDrive and other full vehicle services are not provided (for now).

If you want one more model to be added, hit me up a request in **Issue**.



Thanks to [vdoto4554872](https://github.com/vdoto4554872) for reporting and testing the libkrun problem ([#3](https://github.com/rishik250809/bmw-idrive8.5-emulator/issues/3)).

### Credits go to BMW AG, BMW Car IT GmbH, DENSO Corporation, Snapdragon and other Companies. This is a research experiment, it is not meant for redistribution, at all.
