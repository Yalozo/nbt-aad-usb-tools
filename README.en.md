# NBT-AAD USB Tools

[Русский](README.md)

Install, update, and remove [NBT-AAD](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto) on QNX-based NBT Evo ID5/ID6. USB installer **0.4.2** finds the flash drive and automatically forwards from `hu-jacinto` to `hu-omap` when needed.

## Desktop installer for Windows and macOS

[Download NBT-AAD PC for Windows x64, Mac Apple Silicon, or Mac Intel](https://github.com/Yalozo/nbt-aad-usb-tools/releases/latest)

Connect the ENET cable and turn on the ignition. Choose **English** or **Русский** in the app, then click **Install / Update**. The app opens SSH automatically if needed, verifies the selected package, and uses a downloaded copy when available. A stable internet connection is required to open closed SSH. Offline installation requires open SSH and a cached package.

[Windows guide](windows/README.en.md) · [macOS guide](macos/README.en.md) · [MINI F55 installation on Drive2](https://www.drive2.ru/l/747198416496362487/)

## USB installation requirements

The manual USB procedure starts from a root SSH session. It also requires working CarPlay with a Wi-Fi antenna and access to the head unit via Ethernet/OABR. The USB script can work offline once SSH is available and the package is on the drive. This project is provided for research use under the terms of the [MIT license](LICENSE).

## Prepare a flash drive

1. Use a FAT32 or exFAT flash drive. Back up its contents first if you need to format it.
2. Download `nbt-aad` and the plugin archive, such as `release.zip` or `Releases.zip`, from the same [upstream release](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto/releases).
3. Put `nbt-aad` and this repository's `nbt-aad.sh` in the drive root. Extract the plugin archive to `release/`.
4. If the release provides `pf_aa`, place it in the drive root next to `nbt-aad`. [Optional pf_aa utility](docs/PF-AA.en.md)

```text
USB/
├── nbt-aad.sh
├── nbt-aad
├── pf_aa             # optional, if supplied by this release
└── release/
    ├── libNbtAad.so
    ├── NbtAad.manifest
    ├── NbtAad.xml
    ├── NbtAad_ALL_Texts.zip
    └── NbtAad_ALL_Images.zip
```

Keep the inner text and image ZIPs intact. The script accepts `release` or `releases` in any case, or the five plugin files at the drive root. Use a single matching package and flash drive. The installer copies the six listed files and optional `pf_aa`, and leaves filter configuration untouched.

## Connect and check without installing

Insert the drive into the vehicle and log in as root over SSH. Run these commands **in the head unit's SSH shell**. The script forwards itself to `hu-omap` using `on -f` if needed and preserves its arguments.

```sh
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh install --dry-run
```

The script checks the node, required tools, package files, and ELF and ZIP headers. It creates and removes a small temporary header-check directory on the writable source. The dry run leaves installed files, filesystem mounts, and processes as they were. Header validation does not confirm compatibility with a particular firmware.

If the glob matches multiple scripts, leave one drive connected or use an exact path. If logged in directly to `hu-omap` and its `/net/hu-omap` path is unavailable, use `/bin/sh /mnt/umass*/nbt-aad.sh install --dry-run`.

## Install or update

Close the Android Auto menu and use **Force Stop** in iDrive if possible. If `nbt-aad` is still running, the script sends SIGTERM to the matching processes and waits up to 20 seconds before replacing any files.

```sh
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh install
```

The script checks the source, remounts `/fs/sda0` read-write, stages and verifies copies with `diff`, stops the daemon, moves the old folders to backup, and renames the staged folders into place. It calls `sync` when available and saves the operation log. Settings in `/opt/aa` and `/var/opt/aa`, including `nbt-aad.env`, autostart, and pairing, are kept. The old plugin is stored outside the live Plugins directory.

Restart the head unit after success or use `--restart-hmi`. Open **Media/Radio → Android Auto**, enable **Save logs to USB**, and start the connection. Pair the phone for calls and Bluetooth audio. If needed, remove the pairing on both devices and pair again.

## Remove NBT-AAD

For removal, the flash drive only needs `nbt-aad.sh`.

```sh
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh uninstall --dry-run
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh uninstall
```

The default USB removal archives `/opt/aa`, the plugin at `/opt/hmi/ID5/data/ro/Plugins/NbtAad`, and persistent settings in `/var/opt/aa` after stopping the daemon. Restart after removal. If those paths are already absent, another ordinary removal makes no changes. The next installation uses default settings unless you restore the old configuration.

### Keep settings or perform full cleanup

```sh
# Keep NBT-AAD configuration and installer backups.
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh uninstall --keep-config --restart-hmi

# Review the cleanup plan before removing anything.
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh uninstall --purge --dry-run
# Remove NBT-AAD, settings, backups, temporary uploads, and known RAM files.
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh uninstall --purge --restart-hmi
```

The desktop app's ordinary **Uninstall** keeps settings. Full cleanup deletes both backup folders after the file transaction, including when the program is already absent. Deleted backups cannot be restored. If deletion fails, review the output and retry `uninstall --purge` after addressing the error. Full cleanup logs to the terminal or PC.

Cleanup is limited to standard NBT-AAD paths, `/var/opt/nbt-aad-pc-<32 hex>` uploads, and known RAM logs and state. It preserves files on the PC and USB drive, shared HMI caches, system logs, CarPlay, SSH, coding, and Bluetooth pairings. Inspect any custom paths separately. The loaded plugin may recreate RAM state until the head unit restarts. If `pf_aa` rules were applied separately, restore them first. [Optional pf_aa utility](docs/PF-AA.en.md)

## Other commands

```sh
# Read component paths and running processes.
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh status

# Specify an exact USB path when several drives are connected.
/bin/sh /net/hu-omap/mnt/umass00100t12/nbt-aad.sh install --usb /mnt/umass00100t12

# Restart automatically after a successful install.
/bin/sh /net/hu-omap/mnt/umass*/nbt-aad.sh install --restart-hmi
```

`--restart-hmi` runs `OnOffDSICommander appreset` after the file operation, logging, lock release, and final `sync` when available. It skips restarts after errors and in dry runs. A restart may close temporary SSH. Installed Android Auto does not require ongoing SSH access.

The script checks root with `pidin` and headers with `dd`, `diff`, `wc`, and shell `echo`. It supports stock tool sets missing `id`, `printf`, `awk`, `cmp`, `od`, `rmdir`, or `sync`. It uses the source filesystem for header checks and `/var/opt` for locks and desktop uploads because QNX `/dev/shmem` does not support subdirectories. The script leaves read-write remount handling to the normal restart procedure. It leaves CarPlay activation, SSH, FSC, and coding unchanged.

## Backups, logs, and recovery

```text
/opt/nbt-aad-usb-tools-backups/<ID>/
├── action
├── operation.log
├── COMMITTED             # present after the file operation commits
├── aa/                   # previous /opt/aa, if present
├── plugin/               # previous plugin, if present
└── var-backup-path       # path to saved settings after archived removal

/var/opt/nbt-aad-usb-tools-backups/<ID>/aa/
```

The installer tries to restore old folders after an error or caught signal. The daemon remains stopped after rollback. If it reports `ROLLBACK INCOMPLETE`, check the log and backups before retrying. Power loss, a kernel hang, and SIGKILL can interrupt recovery. [Manual recovery](docs/RECOVERY.en.md)

A USB copy of the log is written as `nbt-aad-tools-<ID>.log` when the drive is available. Backups accumulate until removed explicitly. The lock at `/var/opt/nbt-aad-usb-tools.lock` prevents concurrent operations. If it remains after an interruption, check the log, PID in its `pid` file, and backup directories. Keep the lock in place while the installer is running.

Exit status `0` means success, `1` or `2` means failure or ambiguous input, and `3` means the file operation succeeded but the requested restart failed.

## Test the USB script

```sh
sh -n nbt-aad.sh
python3 -m unittest discover -s tests -v
```

The tests use isolated directories with real file operations and simulated QNX commands. They cover forwarding, argument handling, rollback, and restart order.

The procedure is based on the upstream [installation guide](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto/blob/main/install.md) and [update guide](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto/blob/main/updating.md). The installer is MIT-licensed. NBT-AAD is distributed separately by its authors under PolyForm Noncommercial 1.0.0.
