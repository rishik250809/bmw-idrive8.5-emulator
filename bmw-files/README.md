# Put your extracted BMW backup here

The simplest installation uses your existing extracted filesystem, not the
original SWFL/SWFK containers. Copy the **whole `extracted` folder** here:

```text
bmw-files/extracted/
  emulator-filesystem-metadata.json
  ROOT_rootfs/
  ivi_fs/rootfs/
  ext_fs/rootfs/
  swfk_0000a6b0_007_023_004/
  swfk_0000a6b1_007_023_004/
  swfk_0000aed4_007_023_004/
  swfk_0000a6b2_007_023_004/    (BMW M)
  swfk_0000a691_007_023_004/    (G80 LCI)
  oap_fs/rootfs/                (optional, only for custom apps: ARM OAP partition, SWFL 00009FC7; download from the shared drive)
```

Then run `bash install.sh` from the project folder. 

The installer automatically looks for `bmw-files/extracted/`, so no path argument is needed. If you keep the
backup somewhere else, you can optionally pass the path to its **`extracted`**
folder as the first argument:

```sh
bash install.sh "/path/to/extracted"
```

Keep the metadata JSON with a Windows-flattened extraction: it repairs Linux
symlinks and permissions in the import archive, without changing the backup.
No maps or original firmware containers are needed. The installer copies only
the runtime partitions and supported resources into the VM; the backup does
not need to remain connected afterwards. Existing VM state is never overwritten.
Details and validation command: `docs/EXTRACTED_FILES.md`.

## Alternative: original package import

Copy these files from your own PSdZData (`psdzdata/swe/swfl/` and
`psdzdata/swe/swfk/`) into this folder, keeping their names. All must have the
same version suffix (tested: `007_023_004`).

Required:

    swfl_00009ff7.bin.<version>    ROOT
    swfl_00009fc6.bin.<version>    CONTL1  (contains Hmi8)
    swfl_00009ff9.bin.<version>    CONT3
    swfk_0000a6b0.bin.<version>    Data HMI ID8 BMW-shared
    swfk_0000a6b1.bin.<version>    Data HMI ID8 BMW
    swfk_0000aed4.bin.<version>    Data HMI ID8 Vehicle Graphics G26 BEV
    swfk_0000a6b2.bin.<version>    Data HMI ID8 BMW M  (M interface)
    swfk_0000a691.bin.<version>    Data HMI ID8 Vehicle Graphics G80 LCI (M3)

Unpacked package folders (`swfk_0000a6b1_<version>/full/...`) work too.
You can also skip copying and run `bash install.sh /path/to/psdzdata`.

Then run `bash install.sh` in the project folder. Everything here is ignored by
git; never upload or share these files. Details: docs/GETTING_BMW_FILES.md
