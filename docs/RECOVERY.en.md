# Restore from a backup

[Русский](RECOVERY.md)

Install, update, and uninstall create backups of the previous program and plugin if present. Status and dry runs do not create backups. Backups accumulate without automatic rotation. They cover NBT-AAD files and settings. The installer does not back up firmware, coding, FSC, or the bootloader.

With `--keep-config`, settings stay in place. Archived removal without flags moves `/var/opt/aa` into a separate backup on the same partition. `--purge` deletes both backup folders after the removal transaction.

The installer tries to roll back normal file replacement failures. Use this procedure to restore after a completed operation or an interruption that the installer could not handle.

Open root SSH, switch to `hu-omap` with `on -f /net/hu-omap/ /bin/sh` if needed, use **Force Stop** in the Android Auto menu, and check with `pidin -F '%a %A'` that `nbt-aad` has stopped. Remount the partition for writing.

```sh
mount -o remount rw /fs/sda0
```

Identify the correct backup from the installer output and `operation.log`. Substitute the actual path below. The newest backup may contain only intermediate files from a failed attempt.

```sh
B=/opt/nbt-aad-usb-tools-backups/20261104-140000-12345-install
ls -l "$B"
```

Folders `aa` and `plugin` hold the previous contents. `staged-*` contains files that were not deployed, and `failed-*` contains files moved aside during rollback. A first installation or a completed rollback may leave no `aa` or `plugin` backup.

The following commands preserve current files before restoring the previous ones. Restore only components present in the chosen backup.

```sh
# Restore the previous /opt/aa
if [ -d "$B/aa" ]; then
    if [ -e /opt/aa ]; then
        [ ! -e "$B/before-manual-restore-aa" ] && mv /opt/aa "$B/before-manual-restore-aa"
    fi
    [ ! -e /opt/aa ] && mv "$B/aa" /opt/aa
fi

# Restore the previous plugin
if [ -d "$B/plugin" ]; then
    if [ -e /opt/hmi/ID5/data/ro/Plugins/NbtAad ]; then
        [ ! -e "$B/before-manual-restore-plugin" ] && mv /opt/hmi/ID5/data/ro/Plugins/NbtAad "$B/before-manual-restore-plugin"
    fi
    [ ! -e /opt/hmi/ID5/data/ro/Plugins/NbtAad ] && mv "$B/plugin" /opt/hmi/ID5/data/ro/Plugins/NbtAad
fi
```

After archived removal, persistent settings remain on the `/var` partition. Read the path from the backup.

```sh
cat "$B/var-backup-path"
```

Set `V` to that exact path, then restore the settings on the same partition if needed.

```sh
V=/var/opt/nbt-aad-usb-tools-backups/20261104-140000-12345-uninstall
if [ -d "$V/aa" ]; then
    if [ -e /var/opt/aa ]; then
        [ ! -e "$V/before-manual-restore-settings" ] && mv /var/opt/aa "$V/before-manual-restore-settings"
    fi
    [ ! -e /var/opt/aa ] && mv "$V/aa" /var/opt/aa
fi
```

Stop if any command reports an error. After successfully restoring the folders, run `sync` if available and restart the head unit. Restoration also restores the previous automatic-start flag. Keep copies until you have verified the result.

If `pf_aa` applied rules separately, use `/opt/aa/pf.bak` or its copy in the archived `aa` folder to restore the packet filter. The file operations above do not restore `/fs/sda0/etc/pf.conf`. Full cleanup protects those backups until the rules are restored. [Restore pf_aa rules](PF-AA.en.md)
