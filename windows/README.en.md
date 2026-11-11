# NBT-AAD PC for Windows

[Русский](README.md) · [macOS guide](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/macos/README.en.md)

NBT-AAD PC 0.1.15 bundles USB installer 0.4.2. It installs, updates, and removes [NBT-AAD](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto) on BMW and MINI vehicles with compatible NBT Evo ID5/ID6. It transfers files over SSH through a wired ENET connection.

## Getting started

1. Download the Windows x64 ZIP from [GitHub Releases](https://github.com/Yalozo/nbt-aad-usb-tools/releases/latest). Extract it and run `NBT-AAD-PC.exe` on Windows 10 or 11 x64. The installer runs without Python or administrator rights.
2. Select **English** or **Русский** at the top of the window. The app remembers your choice and updates the controls, help, dialogs, and new log messages immediately.
3. Plug the ENET cable into the vehicle and turn on the ignition. Keep the cable connected and the head unit powered during file operations. **A stable internet connection is required if SSH on the head unit is closed.** The app checks and opens SSH as needed for each head-unit action.
4. Use the default head unit address `169.254.199.99:22` and root password `ts&SK412` or enter your own settings. Direct SSH to `169.254.199.119:22` is also supported. Confirm your head unit's SSH key on the first connection. A changed saved key is rejected.
5. Select a release or keep the automatic choice, then click **Install / Update**. The app uses a verified local copy or downloads the package and verifies its SHA-256 hashes. It stages the files, runs a dry check, and installs them with backups. Existing settings are kept.
6. With **Restart head unit on success** selected, the app requests a restart after verifying the result. Wait for the standard menu, then open **Media/Radio → Android Auto**. If pairing fails on the first launch, remove the pairing on both the head unit and phone and pair them again.

Installation requires working Ethernet/OABR and root SSH access to a compatible head unit. NBT Evo ID4 flashed to ID5/ID6 needs separate testing. The app targets NBT Evo. It leaves CarPlay activation, FSC, coding, and firmware alone.

On compatible NBT-AAD builds without active CarPlay, enable **Use WiFi Direct Network** or **Android Auto over USB (beta)** in the head unit’s Android Auto settings and select **Restart** in its menu. Wi-Fi Direct needs working Wi-Fi and Miracast on the head unit. For USB, use a data cable.

## SSH and releases

Before requesting SSH activation, the app sends a read-only diagnostic request to head unit address `0x63` over ENET. The precheck reply stays on the PC. The log records the head unit VIN if present. If the gateway is reachable but the head unit does not reply, activation stops before contacting the online service. On retrofits, check the head unit’s Ethernet connection (OABR). A diagnostic reply alone does not confirm that the SSH port is reachable.

Opening closed SSH uses an external online service. Head unit diagnostic responses sent to that service may include the VIN and module information. The root password is used to log in directly to the head unit. The operation stops if activation is declined. If the initial network connection drops, it may retry once before sending the diagnostic request.

Normally you can leave **Diagnostic IP** and **ENET adapter IPv4** blank. If discovery uses the wrong adapter, run `ipconfig` and enter the IPv4 address of the Ethernet adapter connected to ENET. Internet traffic can use another connection, such as Wi-Fi.

- **Check Releases** fetches the online list. Green **Downloaded** entries are verified local copies in the same list. Use **Show online beta releases** to include online beta releases.
- **Download Ahead** caches the selected package. Choose that entry to install offline if SSH is already open. Checking the online list and downloading packages require internet access. Package identities include the files' SHA-256 hashes, so new files under an existing tag can appear as a separate build.

## Status, removal, and restart

- **Status** checks files, running processes, and embedded program and plugin build numbers, including for manual installations. It compares all six files, or seven with `pf_aa`, against cached packages. An exact SHA-256 match identifies the release. A running process alone does not confirm that the phone connected.
- **Uninstall** removes the program and menu plugin. It keeps configuration in `/opt/aa` and `/var/opt/aa`, including `nbt-aad.env`, and keeps installer backups. Reinstallation reuses the settings.
- **Full Cleanup** removes NBT-AAD, its settings, installer backups, temporary uploads, and known NBT-AAD logs and state files in RAM. It requires confirmation. It preserves shared HMI caches, system logs, SSH, CarPlay, Bluetooth pairings, and files on the PC or USB drive. Custom installation paths require separate inspection. The loaded plugin may recreate RAM state until the head unit restarts.
- **Restart Head Unit** sends `OnOffDSICommander appreset` after verifying QNX and root on the target node. It works independently of the post-operation checkbox and needs no package. A disconnect after the restart request is expected. Wait for the menu to return. If temporary SSH closes, the app will check access again before the next operation.

Uninstall and Full Cleanup need no release download. The restart checkbox applies to both. If cleanup stops partway through, review the log and retry after addressing the reported error. Removed backups cannot be recovered by the installer.

## Logs and troubleshooting

The log includes the vehicle VIN from gateway discovery when available. The VIN returned by the head unit during the precheck is logged separately. A retrofitted unit may have a different VIN. After SSH login, the app reads the NBT Evo firmware version from the head unit. Send Log attaches the log with these entries.

Check the ENET cable and ignition if the app cannot reach the vehicle. If automatic SSH activation fails, also check that the PC still has stable internet access.

Logs and cached packages are under `%LOCALAPPDATA%\NBT-AAD-PC\logs` and `%LOCALAPPDATA%\NBT-AAD-PC\packages`. The log path and **Send Log** are below the scrollable log. The path can be selected and copied. The button creates an email draft with the current log attached for review before sending. The app stores connection preferences and language in `settings.json`, and verified keys in `known_hosts`. It does not store the password.

The app stages files in a unique writable `/var/opt/nbt-aad-pc-<32 hex>` directory on `hu-omap`. A connection to `hu-jacinto` forwards file operations to `hu-omap` with `on -f`. The app reads uploads back for verification. The dry run writes only temporary header-check files. It removes the staging directory after success and preserves it for diagnosis if the result is uncertain. The path is in the log. QNX `/dev/shmem` cannot hold subdirectories.

Success requires the expected exit code, installer message, and checks of the installed paths. An SSH disconnect before those checks leaves the result unconfirmed. Restart is reported separately and requested only after a successful file operation. If `sync` is unavailable on the head unit, the log records that fact.

Backups are stored in `/opt/nbt-aad-usb-tools-backups` and `/var/opt/nbt-aad-usb-tools-backups`. Check the log before retrying after an incomplete rollback. [Manual recovery](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/docs/RECOVERY.en.md)

## Building and testing

Use Python 3.12 x64 with Tkinter on Windows. Run these commands from `windows`.

```powershell
python -m pip install -r requirements.txt
python main.py
powershell -ExecutionPolicy Bypass -File build.ps1
```

The build creates `windows/dist/NBT-AAD-PC.exe`. `--self-test` checks the bundled GUI, SSH and WebSocket libraries, TLS trust store, and shell installer. The tests cover package handling, UI, node forwarding, restarts, and local SSH/TLS test servers. Actual vehicle compatibility depends on the head unit and firmware.

## Optional pf_aa

If the chosen release includes `pf_aa`, the app verifies its size, SHA-256, and ARM ELF32 header, caches it, and installs it as executable at `/opt/aa/pf_aa`. It does not apply the rules or set `NBT_KEEP_PF` automatically. Re-download a package if its upstream release gains this file later.

Ordinary uninstall removes the executable and preserves `pf.bak` and settings. Restore separately applied rules before full cleanup. The installer protects a known backup if the relevant rules remain or the active configuration cannot be read. [Using and restoring pf_aa](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/docs/PF-AA.en.md)

The installer is MIT-licensed. NBT-AAD is distributed separately by its authors under PolyForm Noncommercial 1.0.0. The project is provided for research use under the terms of the [license](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/LICENSE).
