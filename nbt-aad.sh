#!/bin/sh
# SPDX-License-Identifier: MIT
# Independent installer for NBT-AAD; no upstream binaries are distributed here.
# Run with QNX /bin/sh after obtaining root SSH access; hu-jacinto is forwarded.
set -eu
LC_ALL=C
export LC_ALL
umask 077

VERSION=0.4.0
REMOTE_NODE=/net/hu-omap
FS_ROOT=/fs/sda0
AA=/opt/aa
PLUGIN=/opt/hmi/ID5/data/ro/Plugins/NbtAad
SETTINGS=/var/opt/aa
BACKUPS=/opt/nbt-aad-usb-tools-backups
VAR_BACKUPS=/var/opt/nbt-aad-usb-tools-backups
MNT=/mnt
SOURCE_ROOT=/var/opt
LOCK=/var/opt/nbt-aad-usb-tools.lock
RAM=/dev/shmem
TMP=/tmp
FILES='libNbtAad.so NbtAad.manifest NbtAad.xml NbtAad_ALL_Texts.zip NbtAad_ALL_Images.zip'

ACTION=
USB=
SOURCE=
PAYLOAD=
DRY_RUN=0
RESTART=0
LOG=
BACKUP=
VAR_BACKUP=
LOCKED=0
ACTIVE=0
REMOVE_MODE=archive

say() {
    echo "$*"
    if [ -n "$LOG" ]; then echo "$*" >> "$LOG" 2>/dev/null || :; fi
}
die() { say "ERROR: $*" >&2; exit 1; }
exists() { [ -e "$1" ] || [ -L "$1" ]; }

usage() {
    cat <<'EOF'
NBT-AAD USB Tools 0.4.0
Usage: sh nbt-aad.sh install|uninstall|status [options]
  --usb /mnt/umass...   Select one USB drive explicitly
  --source PATH        Use a private /var/opt/nbt-aad-pc-<32 hex> SSH upload
  --dry-run            Validate and show the plan without changing anything
  --restart-hmi        Run OnOffDSICommander appreset after successful changes
  --keep-config        Uninstall, retaining /opt/aa configuration and /var/opt/aa
  --purge              Uninstall and erase NBT-AAD settings, backups and runtime files
  --help               Show this help

install also updates an existing installation, preserving its settings.
uninstall moves application files and persistent settings to backups.
Use --keep-config to leave settings in place, or --purge for irreversible cleanup.
Default: no automatic restart. Reboot the head unit after install/uninstall.
From hu-jacinto, execution is automatically forwarded to hu-omap via on -f.
SSH must already be open. This tool does not unlock SSH or activate CarPlay.
EOF
}

# Parse inside a function so the original argument vector survives for on -f.
parse_args() {
 while [ "$#" -gt 0 ]; do
    case "$1" in
        install|uninstall|status)
            [ -z "$ACTION" ] || die 'Specify exactly one action.'
            ACTION=$1 ;;
        --usb)
            [ "$#" -ge 2 ] || die '--usb needs a path.'
            USB=$2; shift ;;
        --source)
            [ "$#" -ge 2 ] || die '--source needs a path.'
            SOURCE=$2; shift ;;
        --dry-run) DRY_RUN=1 ;;
        --restart-hmi) RESTART=1 ;;
        --keep-config|--purge)
            [ "$REMOVE_MODE" = archive ] || die 'Choose only one removal mode.'
            case "$1" in --keep-config) REMOVE_MODE=keep ;; --purge) REMOVE_MODE=purge ;; esac ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
 done
}
# The older QNX shell treats an empty "$@" as unset under set -u.
[ "$#" -gt 0 ] || { usage; exit 1; }
parse_args "$@"
[ -n "$ACTION" ] || { usage; exit 1; }
[ "$REMOVE_MODE" = archive ] || [ "$ACTION" = uninstall ] || die 'Removal flags require uninstall.'

# Only the documented head-unit node is permitted. In particular, this script
# must not modify a laptop or the other processor in the head unit.
[ "$(uname -s)" = QNX ] || die 'Run this script on the QNX head unit, not on the computer.'
case "$(uname -n)" in
    hu-omap|hu-omap.*) ;;
    hu-jacinto|hu-jacinto.*)
        [ "${NBT_AAD_NODE_HOP:-0}" = 0 ] || die 'on -f did not reach hu-omap; refusing a forwarding loop.'
        command -v on >/dev/null 2>&1 || die 'QNX on is unavailable; cannot enter hu-omap.'
        [ -d "$REMOTE_NODE/fs/sda0/opt/hmi/ID5" ] || die 'The hu-omap NBT Evo filesystem is not reachable.'
        [ -f "$0" ] && [ -r "$0" ] || die 'Run a saved script file so it can be forwarded to hu-omap.'
        say 'Entering hu-omap automatically (on -f); all file operations will run there.'
        NBT_AAD_NODE_HOP=1
        export NBT_AAD_NODE_HOP
        # Stream this exact script through inherited stdin. A local USB path or
        # relative path on hu-jacinto need not exist under hu-omap's network root.
        # Arguments stay separate; do not construct/eval a shell command string.
        exec on -f "$REMOTE_NODE" /bin/sh -s -- "$@" < "$0"
        ;;
    *) die 'Unexpected QNX node; expected hu-omap or hu-jacinto.' ;;
esac
[ -d "$FS_ROOT/opt/hmi/ID5" ] || die 'The expected NBT Evo ID5/ID6 layout was not found.'
for tool in cat pidin; do
    command -v "$tool" >/dev/null 2>&1 || die "Required command is missing: $tool"
done

# BMW/MINI production images may omit both id and printf. pidin is a native
# QNX utility: %a is PID and %W is effective UID (QNX 6.5 Utilities Reference).
# Verify this shell's credentials after the node hop; do not trust USER/LOGNAME.
credentials=$(pidin -p "$$" -F '%a %W') || die 'Cannot read shell credentials with pidin.'
root_found=0
root_invalid=0
while read -r credential_pid credential_uid credential_extra; do
    [ "$credential_pid" = "$$" ] || continue
    root_found=1
    if [ "$credential_uid" != 0 ] || [ -n "$credential_extra" ]; then root_invalid=1; fi
done <<EOF
$credentials
EOF
[ "$root_found" -eq 1 ] && [ "$root_invalid" -eq 0 ] || die 'Root SSH access could not be verified with pidin (effective UID must be 0).'
for target in "$AA" "$AA/nbt-aad" "$PLUGIN" "$SETTINGS" "$BACKUPS" "$VAR_BACKUPS"; do
    [ ! -L "$target" ] || die "Refusing a symbolic-link target: $target"
done
for target in "$AA" "$PLUGIN" "$SETTINGS" "$BACKUPS" "$VAR_BACKUPS"; do
    if exists "$target"; then [ -d "$target" ] || die "Expected a directory: $target"; fi
done

daemon_pids() {
    # %a = PID, %A = arguments (QNX 6.5). Match argv[0] exactly, never grep
    # the script name or trust a stale PID in nbt-aad-state.
    listing=$(pidin -F '%a %A') || die 'Cannot read the process list with pidin.'
    seen_pids=' '
    while read -r process_pid process_name process_arguments; do
        case "$process_pid" in ''|0|*[!0-9]*) continue ;; esac
        case "$process_name" in
            nbt-aad|*/nbt-aad)
                case "$seen_pids" in *" $process_pid "*) continue ;; esac
                echo "$process_pid"
                seen_pids="$seen_pids$process_pid " ;;
        esac
    done <<EOF
$listing
EOF
}

if [ "$ACTION" = status ]; then
    say "NBT-AAD USB Tools $VERSION"
    for target in "$AA/nbt-aad" "$PLUGIN" "$SETTINGS"; do
        if exists "$target"; then say "Present: $target"; else say "Absent:  $target"; fi
    done
    pids=$(daemon_pids) || exit 1
    say "Daemon PID(s): ${pids:-none}"
    say "Backups: $BACKUPS ; $VAR_BACKUPS"
    exit 0
fi

for tool in chmod cp date mkdir mount mv rm sleep; do
    command -v "$tool" >/dev/null 2>&1 || die "Required command is missing: $tool"
done

complete_payload() {
    for item in $FILES; do [ -f "$1/$item" ] && [ -s "$1/$item" ] || return 1; done
}

find_payload() {
    # Enumerate real directory entries instead of trying several case aliases:
    # FAT32/exFAT can resolve Release and release to the same directory.
    found=
    matches=0
    if complete_payload "$1"; then found=$1; matches=1; fi
    for sub in "$1"/*; do
        [ -d "$sub" ] && [ ! -L "$sub" ] || continue
        case "${sub##*/}" in
            [Rr][Ee][Ll][Ee][Aa][Ss][Ee]|[Rr][Ee][Ll][Ee][Aa][Ss][Ee][Ss])
                if complete_payload "$sub"; then found=$sub; matches=$((matches + 1)); fi ;;
        esac
    done
    if [ "$matches" -gt 1 ]; then
        say "ERROR: Several complete plugin folders on $1; leave only one." >&2
        exit 2
    fi
    [ "$matches" -eq 1 ] || return 1
    echo "$found"
}

if [ -n "$SOURCE" ]; then
    [ -z "$USB" ] || die '--source and --usb cannot be combined.'
    case "$SOURCE" in "$SOURCE_ROOT"/nbt-aad-pc-*) ;; *) die 'Invalid SSH upload path.' ;; esac
    token=${SOURCE#"$SOURCE_ROOT"/nbt-aad-pc-}
    [ "${#token}" -eq 32 ] || die 'Invalid SSH upload token length.'
    case "$token" in *[!a-f0-9]*) die 'Invalid SSH upload token.' ;; esac
    [ -d "$SOURCE" ] && [ ! -L "$SOURCE" ] || die 'SSH upload is not a real directory.'
    USB=$SOURCE
    if [ "$ACTION" = install ]; then
        PAYLOAD=$(find_payload "$USB") || die 'No complete plugin folder in the SSH upload.'
    fi
elif [ -n "$USB" ]; then
    USB=${USB%/}
    case "$USB" in "$MNT"/umass*) ;; *) die '--usb must be an immediate /mnt/umass... directory.' ;; esac
    rest=${USB#"$MNT"/}
    case "$rest" in */*|*..*) die 'Invalid USB mount path.' ;; esac
    [ -d "$USB" ] && [ ! -L "$USB" ] || die "USB mount is not a real directory: $USB"
    if [ "$ACTION" = install ]; then
        PAYLOAD=$(find_payload "$USB") || die 'No complete plugin folder on the selected USB.'
    fi
else
    count=0
    for drive in "$MNT"/umass*; do
        [ -d "$drive" ] && [ ! -L "$drive" ] || continue
        if [ "$ACTION" = install ]; then
            [ -s "$drive/nbt-aad" ] || continue
            # Keep ambiguous-package errors visible across command substitution.
            candidate=$(find_payload "$drive") || {
                result=$?
                [ "$result" -eq 1 ] || exit "$result"
                continue
            }
        else
            # Uninstall needs no downloaded binaries, only this script as marker.
            [ -f "$drive/nbt-aad.sh" ] || continue
            candidate=
        fi
        count=$((count + 1)); USB=$drive; PAYLOAD=$candidate
    done
    [ "$count" -le 1 ] || die 'Several USB drives match. Select one with --usb /mnt/umass...'
    if [ "$ACTION" = install ]; then
        [ "$count" -eq 1 ] || die 'No complete release found on /mnt/umass*. See README.'
    fi
fi

# QNX production images have dd/diff/wc but may omit od/cmp/awk. Tiny
# validation files are created on the selected source filesystem and removed
# on every exit. QNX /dev/shmem does not support directories.
# No binary data passes through shell variables: NUL bytes must be preserved.
emit_bytes() {
    if [ "$BYTE_ECHO" = plain ]; then echo "$1\c"; else echo -e "$1\c"; fi
}
compare_chunk() {
    dd if="$1" of="$VALIDATION_DIR/actual" bs=1 skip="$2" count="$3" 2>/dev/null || return 1
    emit_bytes "$4" > "$VALIDATION_DIR/expected" || return 1
    diff "$VALIDATION_DIR/expected" "$VALIDATION_DIR/actual" >/dev/null 2>&1
}
check_elf() {
    [ -f "$1" ] && [ -s "$1" ] && [ ! -L "$1" ] || die "Missing/empty/symlink binary: $1"
    elf_file=$1
    elf_size=$(wc -c < "$elf_file") || die "Cannot read binary size: $elf_file"
    set -- $elf_size
    [ "$#" -eq 1 ] || die "Cannot parse binary size: $elf_file"
    case "$1" in ''|*[!0-9]*) die "Invalid binary size: $elf_file" ;; esac
    [ "$1" -ge 52 ] || die "Truncated ELF header: $elf_file"
    compare_chunk "$elf_file" 0 7 '\0177ELF\0001\0001\0001' || die "Not a little-endian ELF32 binary: $elf_file"
    if ! compare_chunk "$elf_file" 16 4 '\0002\0000\0050\0000'; then
        compare_chunk "$elf_file" 16 4 '\0003\0000\0050\0000' || die "Not an ARM executable/shared library: $elf_file"
    fi
    compare_chunk "$elf_file" 40 2 '\0064\0000' || die "Invalid ELF header size: $elf_file"
}
check_zip() {
    compare_chunk "$1" 0 4 '\0120\0113\0003\0004' || die "Invalid resource ZIP signature: $1"
}
validate_payload() (
    if [ "$(echo '\0101\c')" = A ]; then
        BYTE_ECHO=plain
    elif [ "$(echo -e '\0101\c')" = A ]; then
        BYTE_ECHO=escapes
    else
        die 'This shell cannot emit the binary header reference bytes.'
    fi
    VALIDATION_DIR=$USB/nbt-aad-header-check-$$
    mkdir "$VALIDATION_DIR" || die 'Cannot create temporary header checks on the selected source. It must be writable.'
    trap 'rm -rf "$VALIDATION_DIR"' 0
    trap 'exit 129' 1
    trap 'exit 130' 2
    trap 'exit 143' 15
    check_elf "$USB/nbt-aad"
    check_elf "$PAYLOAD/libNbtAad.so"
    for item in $FILES; do
        [ ! -L "$PAYLOAD/$item" ] || die "Refusing a symbolic-link payload: $item"
    done
    check_zip "$PAYLOAD/NbtAad_ALL_Texts.zip"
    check_zip "$PAYLOAD/NbtAad_ALL_Images.zip"
)

if [ "$ACTION" = install ]; then
    for tool in dd diff wc; do
        command -v "$tool" >/dev/null 2>&1 || die "Required command is missing: $tool"
    done
    validate_payload
fi

# sync is absent on confirmed stock NBT Evo images. When provided, failures
# still abort the transaction. Without it, follow the upstream copy-and-reset
# procedure; do not claim a sleep or readback is an equivalent disk flush.
flush_if_available() {
    if command -v sync >/dev/null 2>&1; then sync; fi
}

if ! command -v sync >/dev/null 2>&1; then
    say 'Note: sync is not installed; no explicit filesystem flush will be requested.'
fi
say "Action: $ACTION; USB: ${USB:-not needed}; plugin source: ${PAYLOAD:-not needed}"
say "Targets: $AA ; $PLUGIN ; $SETTINGS"
if [ "$ACTION" = uninstall ]; then say "Removal mode: $REMOVE_MODE"; fi
# Enumerate only paths belonging to this project. Never traverse general logs,
# Bluetooth databases or firmware settings. Native HMI caches are shared.
purge_paths() {
    for target in "$AA" "$PLUGIN" "$SETTINGS" "$BACKUPS" "$VAR_BACKUPS"; do
        exists "$target" && echo "$target"
    done
    for target in "$RAM"/nbt-aad.log "$RAM"/nbt-aad-app.log \
        "$RAM"/nbt-aad-state "$RAM"/nbt-aad-pdc "$RAM"/nbt-aad-gear \
        "$RAM"/nbt-aad-daynight "$RAM"/nbt-aad-lm1280 "$RAM"/nbt-aad-kombi-nav.shown \
        "$TMP"/nbt-aad.log "$TMP"/nbt-aad-app.log; do
        exists "$target" && echo "$target"
    done
    for target in "$SOURCE_ROOT"/nbt-aad-pc-*; do
        token=${target#"$SOURCE_ROOT"/nbt-aad-pc-}
        [ "${#token}" -eq 32 ] || continue
        case "$token" in *[!a-f0-9]*) continue ;; esac
        exists "$target" && echo "$target"
    done
    return 0
}
if [ "$REMOVE_MODE" = purge ]; then
    say 'Irreversible cleanup plan (including installer backups and uploads):'
    purge_paths
    say 'Shared HMI caches/system logs, SSH activation, CarPlay and phone pairing are not changed.'
fi
pids=$(daemon_pids) || exit 1
say "Running daemon PID(s): ${pids:-none} (will receive SIGTERM, no forced kill)."
if [ "$ACTION" = uninstall ] && [ "$REMOVE_MODE" != purge ] && ! exists "$AA" && ! exists "$PLUGIN" && ! exists "$SETTINGS"; then
    say 'Already uninstalled. No changes.'
    exit 0
fi
if [ "$DRY_RUN" -eq 1 ]; then
    say 'DRY RUN: validation passed. No remount, signals, application/configuration writes, or restart.'
    exit 0
fi
if [ "$RESTART" -eq 1 ]; then
    command -v OnOffDSICommander >/dev/null 2>&1 || die 'OnOffDSICommander is unavailable.'
fi

rollback() {
    say 'Rolling back file changes. Do not reboot until this finishes.'
    rollback_failed=0
    if [ -f "$BACKUP/COMMITTED" ]; then
        mv "$BACKUP/COMMITTED" "$BACKUP/COMMIT_INTERRUPTED" || rollback_failed=1
    fi
    # Inspect completed renames instead of flags set after mv: a signal can
    # arrive after a successful rename but before the shell's next assignment.
    recover_directory "$PLUGIN" "$BACKUP/plugin" "$BACKUP/staged-plugin" "$BACKUP/failed-plugin"
    recover_directory "$AA" "$BACKUP/aa" "$BACKUP/staged-aa" "$BACKUP/failed-aa"
    if [ -n "$VAR_BACKUP" ]; then
        recover_directory "$SETTINGS" "$VAR_BACKUP/aa" '' "$VAR_BACKUP/failed-settings"
    fi
    if [ "$rollback_failed" -eq 0 ]; then
        echo 'Previous files restored.' > "$BACKUP/ROLLED_BACK" || :
        say 'Previous files restored. The daemon remains stopped; reboot the head unit.'
    else
        echo 'Manual recovery required.' > "$BACKUP/ROLLBACK_FAILED" || :
        say "ROLLBACK INCOMPLETE. Keep the backups: $BACKUP ; $VAR_BACKUP"
    fi
    flush_if_available || :
}
recover_directory() {
    if exists "$2"; then
        if exists "$1"; then mv "$1" "$4" || rollback_failed=1; fi
        restore_directory "$2" "$1"
    elif [ "$ACTION" = install ] && [ -n "$3" ] && ! exists "$3" && exists "$1"; then
        # Fresh installation: the stage was moved into a previously absent target.
        mv "$1" "$4" || rollback_failed=1
    fi
}
restore_directory() {
    # mv of a directory onto an existing directory would silently nest it.
    if exists "$2"; then
        say "Cannot restore $1: destination $2 is occupied."
        rollback_failed=1
    else
        mv "$1" "$2" || rollback_failed=1
    fi
}
release_lock() {
    if [ "$LOCKED" -eq 1 ]; then
        # This directory was acquired atomically under /var/opt. Never create
        # lock directories under QNX /dev/shmem (no subdirectory support).
        [ -d "$LOCK" ] && [ ! -L "$LOCK" ] || return 1
        lock_owner=
        read -r lock_owner < "$LOCK/pid" || return 1
        [ "$lock_owner" = "$$" ] || return 1
        rm -rf "$LOCK" || return 1
        LOCKED=0
    fi
}
finish() {
    result=$1
    trap - 0 1 2 15
    set +e
    if [ "$ACTIVE" -eq 1 ]; then rollback; fi
    release_lock
    exit "$result"
}
trap 'finish $?' 0
trap 'exit 129' 1
trap 'exit 130' 2
trap 'exit 143' 15
mkdir "$LOCK" 2>/dev/null || die "Another run or stale lock exists: $LOCK (see README)."
LOCKED=1
echo "$$" > "$LOCK/pid"

# Match the official install guide on the local hu-omap node.
mount -o remount rw "$FS_ROOT" || die 'Cannot remount the application filesystem writable.'
mkdir -p "$BACKUPS"
stamp=$(date '+%Y%m%d-%H%M%S')
BACKUP=$BACKUPS/$stamp-$$-$ACTION
mkdir "$BACKUP" || die 'Cannot create a unique backup directory.'
LOG=$BACKUP/operation.log
say "NBT-AAD USB Tools $VERSION; $ACTION; backup: $BACKUP"
echo "$ACTION" > "$BACKUP/action"

if [ "$ACTION" = uninstall ] && [ "$REMOVE_MODE" = keep ] && [ -d "$AA" ]; then
    mkdir "$BACKUP/staged-aa"
    cp -Rp "$AA/." "$BACKUP/staged-aa/"
    rm -f "$BACKUP/staged-aa/nbt-aad"
    say 'Configuration files in /opt/aa and /var/opt/aa will remain in place.'
fi
if [ "$ACTION" = install ]; then
    mkdir "$BACKUP/staged-aa" "$BACKUP/staged-plugin"
    if [ -d "$AA" ]; then cp -Rp "$AA/." "$BACKUP/staged-aa/"; fi
    # Never follow a copied binary link into a different directory.
    [ ! -L "$BACKUP/staged-aa/nbt-aad" ] || die 'The existing daemon is a symbolic link.'
    cp "$USB/nbt-aad" "$BACKUP/staged-aa/nbt-aad"
    chmod 755 "$BACKUP/staged-aa" "$BACKUP/staged-aa/nbt-aad" "$BACKUP/staged-plugin"
    diff "$USB/nbt-aad" "$BACKUP/staged-aa/nbt-aad" >/dev/null 2>&1 || die 'Daemon copy verification failed.'
    for item in $FILES; do
        cp "$PAYLOAD/$item" "$BACKUP/staged-plugin/$item"
        chmod 644 "$BACKUP/staged-plugin/$item"
        diff "$PAYLOAD/$item" "$BACKUP/staged-plugin/$item" >/dev/null 2>&1 || die "Copy verification failed: $item"
    done
    say 'Six release files staged and compared with the source originals.'
fi

# File staging is finished before stopping the running daemon. Read the list
# again rather than sending a signal to PIDs captured during preflight.
pids=$(daemon_pids) || exit 1
for pid in $pids; do
    say "Sending SIGTERM to nbt-aad PID $pid."
    kill -TERM "$pid" || die 'Cannot stop the daemon; use Force Stop in iDrive, then retry.'
done
remaining=20
while :; do
    pids=$(daemon_pids) || exit 1
    [ -n "$pids" ] || break
    [ "$remaining" -gt 0 ] || die 'Daemon is still running. No application files were moved; use Force Stop and retry.'
    sleep 1
    remaining=$((remaining - 1))
done

if [ "$ACTION" = uninstall ] && [ "$REMOVE_MODE" != keep ] && exists "$SETTINGS"; then
    # /var can be a different filesystem. Keep its backup on that same filesystem
    # so this is a rename, not a copy-and-delete across partitions.
    mkdir -p "$VAR_BACKUPS"
    VAR_BACKUP=$VAR_BACKUPS/${BACKUP##*/}
    mkdir "$VAR_BACKUP"
    echo "$VAR_BACKUP" > "$BACKUP/var-backup-path"
fi
mkdir -p "${PLUGIN%/*}"
ACTIVE=1
if exists "$AA"; then mv "$AA" "$BACKUP/aa"; fi
if exists "$PLUGIN"; then mv "$PLUGIN" "$BACKUP/plugin"; fi
if [ "$ACTION" = install ]; then
    mv "$BACKUP/staged-aa" "$AA"
    mv "$BACKUP/staged-plugin" "$PLUGIN"
else
    if [ "$REMOVE_MODE" = keep ]; then
        if exists "$BACKUP/staged-aa"; then mv "$BACKUP/staged-aa" "$AA"; fi
    else
        if exists "$SETTINGS"; then mv "$SETTINGS" "$VAR_BACKUP/aa"; fi
    fi
fi
flush_if_available
echo 'File transaction completed.' > "$BACKUP/COMMITTED"
ACTIVE=0
if [ "$REMOVE_MODE" = purge ]; then
    # Commit the removal first. Once erasure begins, rollback is no longer
    # possible. Keep diagnostics on stdout/the PC, not in a deleted backup.
    LOG=
    say 'Application removal committed. Erasing the listed project files; rollback is no longer available.'
    purge_list=$(purge_paths) || die 'Cannot enumerate cleanup paths.'
    while IFS= read -r target; do
        [ -n "$target" ] || continue
        say "Erasing: $target"
        rm -rf "$target" || die "Cleanup incomplete: $target. Retry uninstall --purge."
        exists "$target" && die "Cleanup incomplete: $target"
    done <<EOF
$purge_list
EOF
    say 'SUCCESS: uninstall completed. NBT-AAD project files and installer backups/uploads erased.'
    say 'The loaded HMI plugin is released on reboot; it may recreate RAM state until then.'
else
say "SUCCESS: $ACTION completed. Previous files: $BACKUP"
if [ -n "$VAR_BACKUP" ]; then say "Previous persistent settings: $VAR_BACKUP/aa"; fi

# The USB copy is convenient but optional: a read-only/unplugged stick must not
# invalidate a successful transaction. The main log stays with the backup.
if [ -n "$USB" ]; then
    cp "$LOG" "$USB/nbt-aad-tools-${BACKUP##*/}.log" 2>/dev/null || say 'USB log copy unavailable; use operation.log in the backup.'
fi
fi
if [ "$RESTART" -eq 1 ]; then
    say 'Restarting the head unit with OnOffDSICommander appreset. The SSH connection may close.'
    # A reset may terminate this shell before its EXIT trap. File changes are
    # committed; persist logs and release the operation lock before requesting it.
    if ! release_lock; then
        say 'Files installed/removed, but cannot release the lock. HMI restart skipped.'
        exit 3
    fi
    flush_if_available || { say 'Final sync failed. HMI restart skipped; reboot manually after inspection.'; exit 3; }
    OnOffDSICommander appreset || { say 'Files installed/removed, but HMI restart failed. Reboot manually.'; exit 3; }
else
    say 'Reboot the head unit after successful changes.'
fi
