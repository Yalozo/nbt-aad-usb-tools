# Optional pf_aa support

[Русский](PF-AA.md)

Desktop installer 0.1.8 and USB installer 0.4.1 added support for optional `pf_aa` files supplied by an NBT-AAD release. Older packages without this file still work.

## Purpose

`pf_aa` configures the QNX packet filter. With the default daemon startup, `pfctl -d` disables filtering unless `NBT_KEEP_PF=1` is present. The optional mode keeps the filter active while allowing Android Auto on TCP port 5277 via the `uap0` and `wfd0` Wi-Fi interfaces. [Utility source](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto/blob/629fb372b648de8d50b842eae40f5b7f75d0bbf2/milestone-7/tools/pf_aa.cpp) · [Rules](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto/blob/629fb372b648de8d50b842eae40f5b7f75d0bbf2/milestone-7/tools/pf_aa.hpp)

With `--install`, the utility saves a copy without Android Auto rules in `/opt/aa/pf.bak`, adds its rules to `/fs/sda0/etc/pf.conf` or `/etc/pf.conf`, sets `NBT_KEEP_PF=1` in `/opt/aa/nbt-aad.env`, checks syntax with `pfctl -n`, and reads back the files. The plugin passes only the first 15 environment settings. The utility reports if the new entry is beyond that limit. The rules take effect after reloading them or restarting the head unit.

## What the installer does

It downloads `pf_aa` from the selected release, verifies the size, SHA-256, and ARM ELF32 header, caches it for offline use, and installs it as `/opt/aa/pf_aa` with mode 755. The previous executable and settings in `/opt/aa` are backed up on update. Automatic installation leaves `pf.conf` and `NBT_KEEP_PF` untouched. **Status** reads the embedded utility version and compares all six or seven files against cached packages. If `pf_aa` was added upstream after downloading the release, download it again. The previous local copy stays available.

## Applying and restoring rules

To enable the optional mode, run these commands via root SSH on `hu-omap` after installing the file. Restart only if `--install` succeeds.

```sh
/opt/aa/pf_aa --check
/opt/aa/pf_aa --install
OnOffDSICommander appreset
```

`--check` reports the rules, environment setting, and filter state. Exit code 7 indicates incomplete configuration.

To restore the previous settings:

```sh
/opt/aa/pf_aa --uninstall
```

The utility restores the entire `pf.bak` file and removes `NBT_KEEP_PF` from the environment file. Compare the configurations first if other rules changed after the backup. Restart to apply the restored configuration. If ordinary removal already removed the binary, find it in the relevant installer's `aa` backup or reinstall the package. Keep `pf.bak` until the configuration is restored.

## Removal and full cleanup

Ordinary removal deletes the executable along with the daemon. The keep-settings mode leaves `pf.bak` and the environment file in place. Archived CLI removal keeps them in the old `aa` folder.

Full cleanup searches the current folder and older archives for `pf.bak`. With a backup present, cleanup stops before making changes if `pass` rules for port 5277 remain or the system filter configuration cannot be read. Restore the rules, then retry. Cleanup does not restore the system filter from the backup itself.

The examined `cc-pf`, `volsliderfixforreal`, `winmgr`, and `Installer` releases contained the same 39,844-byte ARM ELF32 utility, version 1.1. Its SHA-256 is `5ae04c87476a041cad945e2afe6da7067ac3defdd9beea96407d8b3a3df339bb`. Version 1.1 handles ENOSYS from fsync on QNX `/dev/shmem`. The installer validates newer files against the digest in their own release.
