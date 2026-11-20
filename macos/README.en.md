# NBT-AAD PC for macOS

[Русский](README.md) · [Shared app guide](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/windows/README.en.md)

NBT-AAD PC 0.1.19 with USB installer 0.4.5 installs wireless Android Auto on compatible BMW and MINI NBT Evo ID5/ID6 head units. It shares its interface and features with the Windows app.


Version 0.1.17 keeps the discovered LAN gateway and source adapter when SSH is closed. It checks the standard link-local SSH addresses through that adapter and searches the local subnet for QNX when needed. Connect the computer and head unit to the same LAN to use Wi-Fi.
## First launch

1. Open [GitHub Releases](https://github.com/Yalozo/nbt-aad-usb-tools/releases/latest). Choose **macOS-arm64** for Apple Silicon or **macOS-x86_64** for Intel. Check Apple menu → About This Mac if unsure.
2. Extract the download and its nested `NBT-AAD-PC-macOS-….zip`. Move **NBT-AAD-PC.app** to Applications and open it. Python and administrator privileges are unnecessary.
3. The bundle uses ad-hoc signing. If macOS blocks the first launch and you trust the download, use **System Settings → Privacy & Security → Open Anyway**. [Apple instructions](https://support.apple.com/102445)
4. Allow local network access when macOS asks so the app can connect over ENET.
5. Choose **English** or **Русский** in the top-right corner. The change takes effect immediately and is saved.

The deployment target is macOS 13 Ventura or later. CI builds and tests the Intel and Apple Silicon variants separately on macOS 15 and checks the minimum OS in each bundled Mach-O file.

## Working with the head unit

Before requesting SSH activation, the app checks for a reply from the head unit to a read-only diagnostic request. If only the gateway responds, no activation request is sent. On retrofits, check the head unit’s Ethernet connection (OABR).

**Head unit coding before installation.** Enable `A4A = aktiv` and `REMOTE_HMI = aktiv` under `3000 HMI`. Check the NBT-AAD coding requirements for the other settings applicable to your firmware. Without the required coding, HMI may skip the plugin, so the Android Auto menu entry can be missing even after the files were installed successfully. Apply the coding separately with E-Sys, BimmerCode, or another compatible tool. [NBT-AAD coding requirements](https://github.com/Sq00ky/Supra-iDrive6-Android-Auto/blob/main/coding-requirements.md)

1. Connect the Mac to the vehicle via wired ENET and turn on the ignition. Use a USB Ethernet adapter if needed. Installation requires Ethernet/OABR and root SSH access to the head unit. Keep power and ENET connected throughout the file operation.
2. The app checks SSH before each head-unit action. **If SSH is closed, the Mac needs a stable internet connection so the app can open it automatically.**
3. Leave the IP fields blank for automatic discovery or enter your head unit's IP. The standard SSH port is `22` and the username is `root`. Enter your own password if you changed it. Confirm the SSH key of your head unit when prompted.
4. Select a release or keep the automatic choice. Click **Install / Update**. The app uses a verified downloaded package or downloads it and verifies its SHA-256 hashes. Existing settings are kept.
5. The green **Downloaded** marker identifies cached releases in the common list. Use **Download Ahead** to cache a package for offline installation with SSH already open.
6. Wait for the result and for the standard menu after the requested restart. Open **Media/Radio → Android Auto**. If necessary, remove the phone pairing on both devices and pair them again.

When the head unit IP and diagnostic IP are blank, the app checks active Ethernet and Wi-Fi interfaces with IPv4 one at a time as part of head unit actions. The log lists discovered gateways and interfaces. If the app cannot reach the vehicle, check the ENET cable and ignition. See the [shared app guide](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/windows/README.en.md#ssh-and-releases) for connection details.

**Status** reads installed versions and compares the files to the local library. **Restart Head Unit** works independently of installation. **Uninstall** keeps settings and backups. **Full Cleanup** removes NBT-AAD, its settings, and installer backups after confirmation. [Manual recovery](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/docs/RECOVERY.en.md)

On compatible NBT-AAD builds without active CarPlay, enable **Use WiFi Direct Network** or **Android Auto over USB (beta)** in the head unit’s Android Auto settings and select **Restart** in its menu. Wi-Fi Direct needs working Wi-Fi and Miracast on the head unit. For USB, use a data cable.

## Local data and building

The log includes the vehicle VIN from gateway discovery when available. The VIN returned by the head unit during the precheck is logged separately. A retrofitted unit may have a different VIN. After SSH login, the app reads the NBT Evo firmware version from the head unit. Send Log attaches the log with these entries.

The app stores `packages`, `logs`, `settings.json`, and `known_hosts` in `~/Library/Application Support/NBT-AAD-PC/`. It remembers the language and connection settings. The root password is not saved. The log path and **Send Log** remain below the scrollable log. The button prepares a mail draft with the current log attached. Removing the app bundle or cleaning up NBT-AAD on the head unit preserves the Mac's library and logs.

From the repository root on a Mac with Python 3.12 and working Tkinter:

```sh
PYTHON_BIN=python3.12 bash macos/build.sh
```

The script creates `macos/.venv`, installs dependencies, runs unit and UI tests, builds the native app, checks the architecture, minimum OS and signature, and runs an offline self-test. Packages are written to `macos/package`. Build each architecture on a matching Mac. The app bundles Python, Tk, and SSH dependencies. NBT-AAD releases are downloaded during use.

Intel builds also require Xcode Command Line Tools, Perl, and Rust/Cargo. The build verifies the OpenSSL 3.5.9 source checksum and builds it privately for macOS 13, then builds cryptography 50.0.2 against it. Apple Silicon uses a cryptography wheel.

## Optional pf_aa

The app verifies and installs `pf_aa` with the selected release when available. Builds with shared script 0.4.3 run `--install` and `--check` over SSH before restarting, using the embedded PF commands if the daemon provides them and the standalone utility is absent. Full cleanup with script 0.4.4 removes the rules generated by `pf_aa` and validates the remaining configuration. [Using and restoring pf_aa](https://github.com/Yalozo/nbt-aad-usb-tools/blob/main/docs/PF-AA.en.md)

The installer is MIT-licensed and provided for research use. Its dependencies retain their own licenses.
