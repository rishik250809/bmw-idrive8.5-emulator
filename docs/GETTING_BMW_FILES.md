# Getting the BMW files

## Already have an extracted filesystem backup?

You do not need the original package containers. Run
`bash install.sh "/path/to/mgu22filesystem/extracted"`, or put the backup at
`bmw-files/extracted/` and run `bash install.sh`. Keep any
`emulator-filesystem-metadata.json` with it to preserve the repair information
for Windows-flattened symlinks/permissions. See `docs/EXTRACTED_FILES.md` for
the supported layout, local validation, and how files reach Hmi8. The rest of
this document describes the alternative original-package import.

This project contains **no BMW software**. The emulator runs BMW's original
MGU22 (iDrive 8.5) HMI, so you need your own copy of a few firmware packages.
`import.sh` checks them against BMW's own manifests and builds the runtime
inside the VM. Do not share the packages or the built runtime with others.

## What you need

A **full** PSdZData (with flash data), or the same package files from another
source. PSdZData *Lite* is not enough: its `swe/swfl` and `swe/swfk` folders
are empty.

All packages must come from **the same release** (the version suffix, e.g.
`007_023_004`). Tested release: `007_023_004` (HMI build `ID8_2507-2446ZC`).
Other releases may work but are untested; `import.sh` refuses mixed versions.

| SGBM | Name in PSdZData | Used for | Needed |
|---|---|---|---|
| `9FF7` | ROOT | root filesystem (system libraries) | **required** |
| `9FC6` | CONTL1 | IVI filesystem, contains `Hmi8` | **required** |
| `9FF9` | CONT3 | EXT filesystem (shared libraries) | **required** |
| `A6B0` | Data HMI ID8 BMW-shared | brand data, shared | **required** |
| `A6B1` | Data HMI ID8 BMW | BMW brand look | **required** |
| `AED4` | Data HMI ID8 Vehicle Graphics G26 BEV | G26 (i4) car graphics | **required** |
| `A6B2` | Data HMI ID8 BMW M | BMW M look (`HMI_BRAND: bmw_m`) | for M mode |
| `A691` | Data HMI ID8 Vehicle Graphics G80 LCI | G80 LCI (M3) car graphics | for `g80-m` |
| `A6AA` | Data HMI ID8 Vehicle Graphics G82 LCI | G82 LCI (M4) car graphics | optional |

About 1.9 GB for the required set, 2.2 GB with everything. Not needed:
`9FC4` (CONT1), `9FC5` (CONT2), `9FC7` (CONTL2), navigation maps, IBA
(owner's manual) packages.

`docs/swe_names.tsv` lists the names of all known SGBM numbers (25,000 rows:
`SGBM_NR`, newest `VERSION`, `NAME`). Look something up with:

```sh
grep -i "Vehicle Graphics" docs/swe_names.tsv   # all car graphics packages
grep -i "^0000A6B2" docs/swe_names.tsv           # one package by number
```

## Where the files are

In a full PSdZData the packages are under `psdzdata/swe/swfl/` and
`psdzdata/swe/swfk/`, named like:

```text
swfl_00009fc6.bin.007_023_004
swfk_0000a6b1.bin.007_023_004
```

`import.sh` also accepts unpacked package folders, as some tools produce them:

```text
swfk_0000a6b1_007_023_004/full/swfk_0000a6b1_007_023_004.json
swfk_0000a6b1_007_023_004/full/swfk-0000a6b1.hmi-data.bmw.ext4.bin.gz
```

You can point `import.sh` at the whole PSdZData folder, or copy just the
packages above into one folder first (keep the original file names).

To see which release your car runs: read the SVT of the MGU in E-Sys; the
SWFL/SWFK lines show the versions (for example `SWFL_00009FC6 007_023_004`).
Matching your car is not required; the emulator only needs one consistent set.

## Import

```sh
bash import.sh /path/to/psdzdata              # or a folder with the packages
bash import.sh /path/to/folder --version 007_023_004   # pick a release
bash start.sh ix40                            # BMW iX xDrive40 (default; experimental)
bash start.sh                                  # iX40 when no argument is provided
bash start.sh g26                             # G26
bash start.sh g80-m                            # G80 M (needs A6B2 + A691)
```

What it does:

1. Finds the packages, picks the newest release that has every required
   package, and checks each artifact's size and SHA-256 against the BMW
   manifest inside the package. Nothing is modified.
2. Creates the Lima VM if needed and copies only the needed artifacts into it.
3. Unpacks the disk images (Android sparse → ext4), mounts them read-only and
   copies them into the runtime layout. Private key files (.key, .p12, .pfx)
   and SSH material are removed.
4. Checks that all brand and car packages carry the same HMI build stamp.
5. Installs the graphics driver (`private/driver.tar.gz`) and the tools image.

The importer never overwrites an existing runtime or saved coding. To start
over, delete the VM (`limactl delete bmw-idrive85`) and import again.

These checks prove the files are the ones BMW's manifests describe; they are
not a signature check of BMW's signing chain.
