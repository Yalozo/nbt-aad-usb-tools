# Changes

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
