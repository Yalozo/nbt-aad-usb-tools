# Changes

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
