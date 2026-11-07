# Changes

## Windows/macOS app 0.1.9

- Run staging, file transfer, readback, installation, removal and status on hu-omap when SSH lands on hu-jacinto. This fixes the mkdir failure caused by resolving /net/hu-omap/var/opt from Jacinto.
- Report preparation failures at the correct stage and log a staging directory only after its creation is acknowledged.

## USB 0.4.1 / Windows/macOS app 0.1.8

- Download optional pf_aa from the same upstream release and validate its digest, size and ARM ELF32 header. Preserve six-file cache compatibility and include the helper digest in new content-addressed package identities.
- Install pf_aa as an executable in /opt/aa without applying system firewall changes automatically; display its embedded version and verify six- or seven-file installed packages.
- Remove the helper during ordinary uninstall; protect pf.bak, including archived copies, from full purge while AA firewall rules remain or pf.conf cannot be inspected.
- Exclude TESTING/UNSTABLE/test-build names from automatic non-preview selection.
- Extend download/cache, offline status, SSH transfer, QNX staging/rollback and purge-preflight tests.

## Windows/macOS app 0.1.7

- Explain each action in hover/keyboard-focus tooltips, including which settings and backups survive uninstall and are erased by full NBT-AAD cleanup.
- Hide/cancel tooltips when operations start, buttons are pressed, focus moves or the window closes.
- Capture real Windows application windows with verified release packages for the graphical installation guide.

## Windows/macOS app 0.1.6

- Order actions as status, install/update, uninstall preserving settings, full NBT-AAD cleanup and restart. Move pre-download beside online releases; refresh saved packages automatically.
- Link the author’s Drive2 installation report and clarify SSH can be opened with free Bavarian or other solutions.

- Build native macOS .app bundles for Apple Silicon and Intel with the same installer/features as Windows.
- Store Mac settings and offline packages in ~/Library/Application Support/NBT-AAD-PC; retain Windows storage compatibility.
- Use Mac fonts and run GUI tests on both desktop platforms.
- Bundle the public HTTPS CA store so downloading releases does not depend on an external Python installation; verify certificate checking remains enabled.
- Verify frozen app startup, code signatures, native architecture and minimum macOS deployment targets; preserve bundle symlinks in ZIP downloads.
- Add macOS build instructions and separate GitHub Actions artifacts, including local-network permission text and first-launch instructions for ad-hoc signed builds.

## 0.4.0 / Windows app 0.1.5

- Read installed daemon/plugin build banners without executing either binary; match all six installed file hashes against the offline package library. Manual installs are supported; unknown or mixed builds are not guessed.
- Add uninstall --keep-config, retaining /opt/aa configuration (including nbt-aad.env) and /var/opt/aa for reinstall. Windows uses this mode by default.
- Add explicit irreversible uninstall --purge, removing known project runtime files, settings, installer backups and strictly named SSH uploads, including remnants after prior removal. Shared system/HMI caches, SSH and CarPlay remain untouched.
- Show removal mode in the Windows UI with a separate full-cleanup confirmation. Keep the original archival CLI uninstall for compatibility.
- Preserve transaction rollback before erasure; report erasure failures without a success claim or restart, permitting cleanup retries.
- Extend filesystem, offline version-identification, controller and native Windows GUI tests.

## Windows app 0.1.4

- Add a standalone head-unit restart button that checks SSH/QNX/root and does not load an installer or package.
- Show all verified saved packages in an offline selector, including caches created by earlier app versions.
- Keep all downloaded builds; distinguish reused names/tags and avoid duplicating identical payloads.
- Install exactly the selected local copy without network access or fallback to another version.
- Persist offline mode and package selection; disable online controls while offline.
- Validate chosen files again before installation and skip damaged/incomplete library entries with a log message.
- Add tests for historical-cache migration, multi-version selection, restart-only execution and native Windows UI behavior.

## 0.3.3 / Windows app 0.1.3

- Fix QNX ENOSYS from mkdir under /dev/shmem, which has no subdirectory support.
- Store temporary binary-header checks on the selected writable USB/source filesystem; preserve cleanup on success/error.
- Move the atomic directory lock to /var/opt and verify its PID owner before cleanup.
- Move Windows SSH staging to /var/opt; retain strict path validation and readback checks. Failed-run staging may persist and is reported accurately.
- Add filesystem-semantic tests that reject all mkdir calls below simulated QNX /dev/shmem, for both USB and SSH paths.
- Map production SOURCE_ROOT/LOCK locations faithfully in tests instead of assuming directory-capable host RAM semantics.

## 0.3.2 / Windows app 0.1.2

- Adapt to the confirmed production hu-omap tool inventory; remove awk, cmp, od and rmdir dependencies.
- Parse credentials and daemon PIDs with shell read/case, retaining strict UID checks and PID deduplication.
- Validate binary ELF/ZIP headers using bounded dd reads and diff, preserving NUL byte positions. Private RAM reference files are always cleaned up.
- Compare all staged files with binary-aware diff before stopping the daemon or changing live directories.
- Treat sync as optional, with an explicit log note when unavailable; do not equate a delay/readback with a forced disk flush.
- Add a restricted-PATH fixture based on the owner's actual /bin inventory, covering status/dry-run/install/update/uninstall and rollback.
- Keep the Windows restart request functional when sync is absent.

## 0.3.1 / Windows app 0.1.1

- Fix startup on actual BMW/MINI QNX images lacking `id` and `printf`.
- Verify the effective UID via native QNX `pidin` (%a/%W), after forwarding to hu-omap. USER/LOGNAME are not accepted as proof of root.
- Use echo for messages and markers, including the Windows reset request.
- Guard empty argument lists before expanding "$@" under nounset on the older QNX shell.
- Read-only status checks require only their own utilities; install dependencies are checked before changing files.
- Add regressions for missing utilities, zero arguments, UID output parsing and mismatched/non-root process credentials.

## 0.3.0 / Windows app 0.1.0

- Added a Windows GUI for SSH install/update, uninstall and read-only status.
- Download matched official assets with SHA-256 verification and an offline cache.
- Added constrained `--source` staging in a private `/dev/shmem/nbt-aad-pc-<32 hex>` directory; USB operation remains supported.
- Preserve backups/rollback, independently verify deployed paths and request reset only after acknowledged success.
- Include Windows EXE builds, frozen smoke tests, GUI tests and real loopback legacy-SSH tests.
- Use the documented `OnOffDSICommander appreset` as the automatic head-unit restart, without an additional power-button step.

## 0.2.0

- Automatic execution forwarding from `hu-jacinto` to `hu-omap` using QNX `on -f`.
- The exact script is streamed to the remote shell; command arguments are preserved without `eval`.
- A single-hop guard rejects a failed redirect instead of entering a loop.
- README entry commands locate the script through `/net/hu-omap/mnt/umass*` so no manual node switch is needed.
- Before `--restart-hmi`, release the installation lock and sync completed file operations. An unsuccessful transaction or dry run never requests a restart.
- `--restart-hmi` continues to mean the documented `OnOffDSICommander appreset`; a whole-unit/power-cycle equivalent is not implemented or claimed.
- Added integration tests for forwarding and restart ordering.

## 0.1.0

- Initial installer, updater, uninstaller and read-only status command.
- Automatic release discovery, staged copying, preserved settings and backup directories.
- Error/signal rollback and isolated filesystem tests.
