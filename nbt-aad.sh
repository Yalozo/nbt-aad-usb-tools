#!/bin/sh
# SPDX-License-Identifier: MIT
# Independent installer for NBT-AAD; no upstream binaries are distributed here.
# Run with QNX /bin/sh after obtaining root SSH access; hu-jacinto is forwarded.
set -eu
LC_ALL=C
export LC_ALL
umask 077

VERSION=0.2.0
REMOTE_NODE=/net/hu-omap
FS_ROOT=/fs/sda0
AA=/opt/aa
PLUGIN=/opt/hmi/ID5/data/ro/Plugins/NbtAad
SETTINGS=/var/opt/aa
BACKUPS=/opt/nbt-aad-usb-tools-backups
VAR_BACKUPS=/var/opt/nbt-aad-usb-tools-backups
MNT=/mnt
LOCK=/dev/shmem/nbt-aad-usb-tools.lock
FILES='libNbtAad.so NbtAad.manifest NbtAad.xml NbtAad_ALL_Texts.zip NbtAad_ALL_Images.zip'

ACTION=
USB=
PAYLOAD=
DRY_RUN=0
RESTART=0
LOG=
BACKUP=
VAR_BACKUP=
LOCKED=0
ACTIVE=0

say() {
    printf '%s\n' "$*"
    if [ -n "$LOG" ]; then printf '%s\n' "$*" >> "$LOG" 2>/dev/null || :; fi
}
die() { say "ERROR: $*" >&2; exit 1; }
exists() { [ -e "$1" ] || [ -L "$1" ]; }

usage() {
    cat <<'EOF'
NBT-AAD USB Tools 0.2.0
Usage: sh nbt-aad.sh install|uninstall|status [options]
  --usb /mnt/umass...   Select one USB drive explicitly
  --dry-run            Validate and show the plan without changing anything
  --restart-hmi        Run OnOffDSICommander appreset after successful changes
  --help               Show this help

install also updates an existing installation, preserving its settings.
uninstall moves application files and persistent settings to backups.
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
        --dry-run) DRY_RUN=1 ;;
        --restart-hmi) RESTART=1 ;;
        --help|-h) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
 done
}
parse_args "$@"
[ -n "$ACTION" ] || { usage; exit 1; }

# Only the documented head-unit node is permitted. In particular, this script
# must not modify a laptop or the other processor in the head unit.
[ "$(uname -s)" = QNX ] || die 'Run this script on the QNX head unit, not on the computer.'
[ "$(id -u)" = 0 ] || die 'Root SSH access is required.'
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
for tool in awk cat chmod cmp cp date grep mkdir mount mv od pidin printf rmdir rm sleep sync; do
    command -v "$tool" >/dev/null 2>&1 || die "Required command is missing: $tool"
done
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
    printf '%s\n' "$listing" | awk '
        $1 ~ /^[0-9]+$/ && ($2 == "nbt-aad" || $2 ~ /\/nbt-aad$/) {
            if (!seen[$1]++) print $1
        }'
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
    printf '%s\n' "$found"
}

if [ -n "$USB" ]; then
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

check_elf() {
    [ -f "$1" ] && [ -s "$1" ] && [ ! -L "$1" ] || die "Missing/empty/symlink binary: $1"
    bytes=$(od -A n -t u1 -N 52 "$1") || die "Cannot read binary: $1"
    # ELF32, little endian, current ELF version, ARM (e_machine 40).
    printf '%s\n' "$bytes" | awk '
        { for (i=1; i<=NF; i++) b[++n]=$i }
        END { exit !(n==52 && b[1]==127 && b[2]==69 && b[3]==76 && b[4]==70 &&
                     b[5]==1 && b[6]==1 && b[7]==1 && b[19]==40 && b[20]==0 &&
                     (b[17]==2 || b[17]==3) && b[18]==0 && b[41]==52 && b[42]==0) }' || die "Not an ARM-compatible ELF32 binary: $1"
}
check_zip() {
    bytes=$(od -A n -t u1 -N 4 "$1") || die "Cannot read ZIP: $1"
    set -- $bytes
    [ "$#" -eq 4 ] && [ "$1 $2 $3 $4" = '80 75 3 4' ] || die 'Invalid resource ZIP signature.'
}

if [ "$ACTION" = install ]; then
    check_elf "$USB/nbt-aad"
    check_elf "$PAYLOAD/libNbtAad.so"
    for item in $FILES; do
        [ ! -L "$PAYLOAD/$item" ] || die "Refusing a symbolic-link payload: $item"
    done
    check_zip "$PAYLOAD/NbtAad_ALL_Texts.zip"
    check_zip "$PAYLOAD/NbtAad_ALL_Images.zip"
fi

say "Action: $ACTION; USB: ${USB:-not needed}; plugin source: ${PAYLOAD:-not needed}"
say "Targets: $AA ; $PLUGIN ; $SETTINGS"
pids=$(daemon_pids) || exit 1
say "Running daemon PID(s): ${pids:-none} (will receive SIGTERM, no forced kill)."
if [ "$ACTION" = uninstall ] && ! exists "$AA" && ! exists "$PLUGIN" && ! exists "$SETTINGS"; then
    say 'Already uninstalled. No changes.'
    exit 0
fi
if [ "$DRY_RUN" -eq 1 ]; then
    say 'DRY RUN: validation passed. No remount, signals, writes, or restart.'
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
        printf '%s\n' 'Previous files restored.' > "$BACKUP/ROLLED_BACK" || :
        say 'Previous files restored. The daemon remains stopped; reboot the head unit.'
    else
        printf '%s\n' 'Manual recovery required.' > "$BACKUP/ROLLBACK_FAILED" || :
        say "ROLLBACK INCOMPLETE. Keep the backups: $BACKUP ; $VAR_BACKUP"
    fi
    sync || :
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
        rm -f "$LOCK/pid" && rmdir "$LOCK" || return 1
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
printf '%s\n' "$$" > "$LOCK/pid"

# Match the official install guide, on the local hu-omap node. A full head-unit
# reboot afterwards restores the usual mount state; HMI appreset is not that reboot.
mount -o remount rw "$FS_ROOT" || die 'Cannot remount the application filesystem writable.'
mkdir -p "$BACKUPS"
stamp=$(date '+%Y%m%d-%H%M%S')
BACKUP=$BACKUPS/$stamp-$$-$ACTION
mkdir "$BACKUP" || die 'Cannot create a unique backup directory.'
LOG=$BACKUP/operation.log
say "NBT-AAD USB Tools $VERSION; $ACTION; backup: $BACKUP"
printf '%s\n' "$ACTION" > "$BACKUP/action"

if [ "$ACTION" = install ]; then
    mkdir "$BACKUP/staged-aa" "$BACKUP/staged-plugin"
    if [ -d "$AA" ]; then cp -Rp "$AA/." "$BACKUP/staged-aa/"; fi
    # Never follow a copied binary link into a different directory.
    [ ! -L "$BACKUP/staged-aa/nbt-aad" ] || die 'The existing daemon is a symbolic link.'
    cp "$USB/nbt-aad" "$BACKUP/staged-aa/nbt-aad"
    chmod 755 "$BACKUP/staged-aa" "$BACKUP/staged-aa/nbt-aad" "$BACKUP/staged-plugin"
    cmp -s "$USB/nbt-aad" "$BACKUP/staged-aa/nbt-aad" || die 'Daemon copy verification failed.'
    for item in $FILES; do
        cp "$PAYLOAD/$item" "$BACKUP/staged-plugin/$item"
        chmod 644 "$BACKUP/staged-plugin/$item"
        cmp -s "$PAYLOAD/$item" "$BACKUP/staged-plugin/$item" || die "Copy verification failed: $item"
    done
    say 'Six release files staged and compared with the USB originals.'
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

if [ "$ACTION" = uninstall ] && exists "$SETTINGS"; then
    # /var can be a different filesystem. Keep its backup on that same filesystem
    # so this is a rename, not a copy-and-delete across partitions.
    mkdir -p "$VAR_BACKUPS"
    VAR_BACKUP=$VAR_BACKUPS/${BACKUP##*/}
    mkdir "$VAR_BACKUP"
    printf '%s\n' "$VAR_BACKUP" > "$BACKUP/var-backup-path"
fi
mkdir -p "${PLUGIN%/*}"
ACTIVE=1
if exists "$AA"; then mv "$AA" "$BACKUP/aa"; fi
if exists "$PLUGIN"; then mv "$PLUGIN" "$BACKUP/plugin"; fi
if [ "$ACTION" = install ]; then
    mv "$BACKUP/staged-aa" "$AA"
    mv "$BACKUP/staged-plugin" "$PLUGIN"
else
    if exists "$SETTINGS"; then mv "$SETTINGS" "$VAR_BACKUP/aa"; fi
fi
sync
printf '%s\n' 'File transaction completed.' > "$BACKUP/COMMITTED"
ACTIVE=0
say "SUCCESS: $ACTION completed. Previous files: $BACKUP"
if [ -n "$VAR_BACKUP" ]; then say "Previous persistent settings: $VAR_BACKUP/aa"; fi

# The USB copy is convenient but optional: a read-only/unplugged stick must not
# invalidate a successful transaction. The main log stays with the backup.
if [ -n "$USB" ]; then
    cp "$LOG" "$USB/nbt-aad-tools-${BACKUP##*/}.log" 2>/dev/null || say 'USB log copy unavailable; use operation.log in the backup.'
fi
if [ "$RESTART" -eq 1 ]; then
    say 'Restarting the HMI with OnOffDSICommander appreset. The SSH connection may close.'
    # A reset may terminate this shell before its EXIT trap. File changes are
    # committed; persist logs and release the operation lock before requesting it.
    if ! release_lock; then
        say 'Files installed/removed, but cannot release the lock. HMI restart skipped.'
        exit 3
    fi
    sync || { say 'Final sync failed. HMI restart skipped; reboot manually after inspection.'; exit 3; }
    OnOffDSICommander appreset || { say 'Files installed/removed, but HMI restart failed. Reboot manually.'; exit 3; }
else
    say 'Reboot the head unit (hold its power button for about 30 seconds).'
fi
say 'After HMI-only appreset the filesystem can remain writable; a full reboot is recommended.'
