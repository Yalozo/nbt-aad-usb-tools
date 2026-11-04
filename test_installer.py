"""Filesystem-level integration tests; no access to a real head unit.

Only absolute path constants in a temporary copy of the production script are
redirected. QNX-only utilities are simulated; cp/mv/od/cmp/sh are real host tools.
"""
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "nbt-aad.sh"
FILES = ("libNbtAad.so", "NbtAad.manifest", "NbtAad.xml",
         "NbtAad_ALL_Texts.zip", "NbtAad_ALL_Images.zip")


def elf(tag=b"new", machine=40):
    data = bytearray(64)
    data[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HHI", data, 16, 3, machine, 1)
    struct.pack_into("<H", data, 40, 52)
    return bytes(data) + tag


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nbtaad-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = {
            "REMOTE_NODE": self.root / "net/hu-omap",
            "FS_ROOT": self.root / "fs",
            "AA": self.root / "opt/aa",
            "PLUGIN": self.root / "opt/hmi/ID5/data/ro/Plugins/NbtAad",
            "SETTINGS": self.root / "var/opt/aa",
            "BACKUPS": self.root / "opt/backups",
            "VAR_BACKUPS": self.root / "var/opt/backups",
            "MNT": self.root / "mnt",
            "LOCK": self.root / "shmem/installer.lock",
        }
        for path in self.paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        (self.paths["FS_ROOT"] / "opt/hmi/ID5").mkdir(parents=True)
        (self.paths["REMOTE_NODE"] / "fs/sda0/opt/hmi/ID5").mkdir(parents=True)
        self.paths["MNT"].mkdir(exist_ok=True)
        content = SOURCE.read_text()
        for key, value in self.paths.items():
            lines = content.splitlines()
            matches = [n for n, line in enumerate(lines) if line.startswith(key + "=")]
            self.assertEqual(len(matches), 1)
            lines[matches[0]] = key + "=" + shlex.quote(str(value))
            content = "\n".join(lines) + "\n"
        self.script = self.root / "nbt-aad.sh"
        self.script.write_text(content)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "calls"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        TEST_CALLS=str(self.calls), TEST_ROOT=str(self.root))
        self.stub("uname", 'if [ "$1" = -s ]; then echo QNX; else echo "${TEST_NODE:-hu-omap}"; fi')
        self.stub("id", "echo 0")
        self.stub("mount", 'echo mount >> "$TEST_CALLS"; exit "${TEST_MOUNT_RC:-0}"')
        self.stub("sync", 'echo sync >> "$TEST_CALLS"; exit "${TEST_SYNC_RC:-0}"')
        self.stub("OnOffDSICommander", 'echo hmi >> "$TEST_CALLS"; exit "${TEST_HMI_RC:-0}"')
        self.stub("on", 'echo on >> "$TEST_CALLS"\n'
                  '[ "$1" = -f ] && [ "$2" = "$TEST_ROOT/net/hu-omap" ] || exit 90\n'
                  'shift 2\n'
                  'TEST_NODE=${TEST_FORWARD_NODE:-hu-omap}; export TEST_NODE\n'
                  'exec "$@"')
        self.stub("pidin", 'if [ "${TEST_PIDIN_RC:-0}" != 0 ]; then exit 1; fi\n'
                  'echo "pid Arguments"\n'
                  'if [ -f "$TEST_ROOT/processes" ]; then cat "$TEST_ROOT/processes"; fi')
        self.usb = self.payload()

    def stub(self, name, body):
        p = self.bin / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o755)

    def payload(self, name="umass00100t12", folder="release"):
        usb = self.paths["MNT"] / name
        usb.mkdir()
        (usb / "nbt-aad.sh").write_text("marker")
        (usb / "nbt-aad").write_bytes(elf())
        dest = usb / folder if folder else usb
        dest.mkdir(exist_ok=True)
        (dest / "libNbtAad.so").write_bytes(elf(b"plugin"))
        (dest / "NbtAad.manifest").write_text("<plugin>example</plugin>")
        (dest / "NbtAad.xml").write_text("<pluginApps/>")
        for name in FILES[-2:]:
            with zipfile.ZipFile(dest / name, "w") as z:
                z.writestr("db", b"test")
        return usb

    def old_install(self):
        self.paths["AA"].mkdir()
        (self.paths["AA"] / "nbt-aad").write_bytes(elf(b"old"))
        (self.paths["AA"] / "nbt-aad.env").write_text("NBT_VID_DPI=152\n")
        self.paths["PLUGIN"].mkdir()
        (self.paths["PLUGIN"] / "libNbtAad.so").write_bytes(elf(b"old-plugin"))
        (self.paths["PLUGIN"] / "custom-file").write_text("preserve in backup")
        self.paths["SETTINGS"].mkdir()
        (self.paths["SETTINGS"] / "autostart").touch()
        (self.paths["SETTINGS"] / "spp-model").write_text("phone-open")

    def run_script(self, *args, ok=True, **env):
        proc = subprocess.run(["/bin/sh", str(self.script), *args], text=True,
                              capture_output=True, env=dict(self.env, **env), timeout=15)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc

    def snapshot(self):
        return {str(p.relative_to(self.root)): ("link", os.readlink(p)) if p.is_symlink()
                else p.read_bytes() for p in self.root.rglob("*") if p.is_file() or p.is_symlink()}

    def backup(self):
        backups = list(self.paths["BACKUPS"].iterdir())
        self.assertEqual(len(backups), 1)
        return backups[0]

    def assert_old_intact(self):
        self.assertEqual((self.paths["AA"] / "nbt-aad").read_bytes(), elf(b"old"))
        self.assertEqual((self.paths["PLUGIN"] / "libNbtAad.so").read_bytes(), elf(b"old-plugin"))
        self.assertTrue((self.paths["SETTINGS"] / "autostart").exists())

    def test_install_detects_usb_and_copies_exact_six_files(self):
        self.run_script("install")
        self.assertEqual((self.paths["AA"] / "nbt-aad").read_bytes(), elf())
        self.assertEqual(set(p.name for p in self.paths["PLUGIN"].iterdir()), set(FILES))
        self.assertEqual((self.paths["AA"] / "nbt-aad").stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.backup() / "COMMITTED").exists())
        self.assertFalse(self.paths["LOCK"].exists())
        self.assertNotIn("hmi", self.calls.read_text())

    def test_dry_run_has_no_filesystem_changes(self):
        self.old_install()
        before = self.snapshot()
        self.run_script("install", "--dry-run")
        self.assertEqual(self.snapshot(), before)

    def test_update_preserves_settings_and_old_loaded_inode(self):
        self.old_install()
        old = self.paths["AA"] / "nbt-aad"
        with old.open("rb") as loaded:
            old_inode = os.fstat(loaded.fileno()).st_ino
            self.run_script("install")
            self.assertEqual(loaded.read(), elf(b"old"))
        backup = self.backup()
        self.assertEqual((backup / "aa/nbt-aad").stat().st_ino, old_inode)
        self.assertEqual((self.paths["AA"] / "nbt-aad.env").read_text(), "NBT_VID_DPI=152\n")
        self.assertEqual((self.paths["SETTINGS"] / "spp-model").read_text(), "phone-open")
        self.assertTrue((backup / "plugin/custom-file").exists())
        self.assertFalse((self.paths["PLUGIN"] / "custom-file").exists())

    def test_uninstall_archives_application_and_persistent_settings(self):
        self.old_install()
        settings_inode = self.paths["SETTINGS"].stat().st_ino
        self.run_script("uninstall")
        for key in ("AA", "PLUGIN", "SETTINGS"):
            self.assertFalse(self.paths[key].exists())
        backup = self.backup()
        self.assertTrue((backup / "aa/nbt-aad.env").exists())
        var = Path((backup / "var-backup-path").read_text().strip()) / "aa"
        self.assertEqual(var.stat().st_ino, settings_inode)
        self.assertTrue((var / "autostart").exists())
        self.assertTrue((self.usb / "nbt-aad").exists())
        self.run_script("uninstall")
        self.assertEqual(len(list(self.paths["BACKUPS"].iterdir())), 1)

    def test_uninstall_needs_no_release_or_usb(self):
        self.old_install()
        shutil.rmtree(self.usb)
        self.run_script("uninstall")
        self.assertFalse(self.paths["AA"].exists())

    def test_status_is_read_only(self):
        self.old_install()
        before = self.snapshot()
        self.run_script("status")
        self.assertEqual(self.snapshot(), before)

    def test_case_variants_and_flat_layout(self):
        for folder in ("Releases", "Release", "rElEaSeS", ""):
            with self.subTest(folder=folder):
                shutil.rmtree(self.usb)
                self.usb = self.payload(folder=folder)
                self.run_script("install", "--dry-run")

    def test_missing_payload_refuses_before_remount(self):
        (self.usb / "release/NbtAad.xml").unlink()
        self.run_script("install", ok=False)
        self.assertFalse(self.calls.exists())

    def test_wrong_architecture_and_truncated_binary(self):
        for content in (elf(machine=62), b"\x7fELF" + b"\0" * 16):
            with self.subTest(content=content[:4]):
                (self.usb / "nbt-aad").write_bytes(content)
                self.run_script("install", ok=False)
                self.assertFalse(self.calls.exists())

    def test_invalid_zip_header(self):
        (self.usb / "release/NbtAad_ALL_Texts.zip").write_bytes(b"broken zip")
        self.run_script("install", ok=False)
        self.assertFalse(self.calls.exists())

    def test_ambiguous_drives_and_explicit_selection(self):
        self.payload("umass2", "Releases")
        self.run_script("install", ok=False)
        self.assertFalse(self.calls.exists())
        self.run_script("install", "--usb", str(self.usb))

    def test_ambiguous_folders_cannot_be_hidden_by_second_good_drive(self):
        shutil.copytree(self.usb / "release", self.usb / "releases")
        self.payload("umass2", "Releases")
        self.run_script("install", ok=False)
        self.assertFalse(self.calls.exists())

    def test_symlink_source_or_target_is_rejected(self):
        source = self.usb / "nbt-aad"
        other = self.root / "external"
        other.write_bytes(elf())
        source.unlink()
        source.symlink_to(other)
        self.run_script("install", ok=False)
        self.assertFalse(self.calls.exists())
        source.unlink()
        source.write_bytes(elf())
        self.paths["AA"].symlink_to(self.root / "external-dir")
        self.run_script("install", ok=False)
        self.assertEqual(other.read_bytes(), elf())
        self.assertFalse(self.calls.exists())

    def test_other_os_node_nonroot_and_failed_process_listing(self):
        original_uname = (self.bin / "uname").read_text()
        self.stub("uname", "echo Darwin")
        self.run_script("install", ok=False)
        self.stub("uname", 'if [ "$1" = -s ]; then echo QNX; else echo unknown-node; fi')
        self.run_script("install", ok=False)
        (self.bin / "uname").write_text(original_uname)
        self.stub("id", "echo 1000")
        self.run_script("install", ok=False)
        self.stub("id", "echo 0")
        self.run_script("install", TEST_PIDIN_RC="1", ok=False)
        self.assertFalse(self.calls.exists())

    def test_mount_failure_leaves_installation_intact(self):
        self.old_install()
        self.run_script("install", TEST_MOUNT_RC="1", ok=False)
        self.assert_old_intact()
        self.assertFalse(self.paths["LOCK"].exists())

    def fail_rename(self, source_suffix, dest_suffix):
        real_mv = shutil.which("mv")
        self.stub("mv", 'case "$1|$2" in\n' +
                  f'  *{shlex.quote(source_suffix)}\\|*{shlex.quote(dest_suffix)}) '
                  'echo "injected move failure" >&2; exit 1 ;;\nesac\n' +
                  'exec ' + shlex.quote(real_mv) + ' "$@"')

    def test_update_rolls_back_when_second_deployment_fails(self):
        self.old_install()
        self.fail_rename("/staged-plugin", "/NbtAad")
        proc = self.run_script("install", ok=False)
        self.assertIn("Previous files restored", proc.stdout)
        self.assert_old_intact()
        self.assertFalse((self.backup() / "COMMITTED").exists())
        self.assertFalse(self.paths["LOCK"].exists())

    def test_new_install_rolls_back_without_leaving_half_installation(self):
        self.fail_rename("/staged-plugin", "/NbtAad")
        self.run_script("install", ok=False)
        self.assertFalse(self.paths["AA"].exists())
        self.assertFalse(self.paths["PLUGIN"].exists())

    def test_uninstall_rolls_back_if_settings_move_fails(self):
        self.old_install()
        self.fail_rename("/var/opt/aa", "/aa")
        self.run_script("uninstall", ok=False)
        self.assert_old_intact()

    def test_sync_failure_rolls_back(self):
        self.old_install()
        self.run_script("install", TEST_SYNC_RC="1", ok=False)
        self.assert_old_intact()

    def test_restart_is_explicit_and_restart_failure_does_not_undo_commit(self):
        proc = self.run_script("install", "--restart-hmi", TEST_HMI_RC="1", ok=False)
        self.assertEqual(proc.returncode, 3)
        self.assertTrue((self.backup() / "COMMITTED").exists())
        self.assertTrue((self.paths["AA"] / "nbt-aad").exists())
        self.assertIn("hmi", self.calls.read_text())

    def test_stale_lock_refuses(self):
        self.paths["LOCK"].mkdir()
        self.run_script("install", ok=False)
        self.assertFalse(self.calls.exists())

    def test_script_process_is_not_mistaken_for_daemon(self):
        (self.root / "processes").write_text("99999999 /bin/sh /mnt/umass1/nbt-aad.sh install\n")
        self.run_script("install")

    def test_graceful_termination_of_only_the_matching_daemon(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        def cleanup_child():
            if child.poll() is None:
                child.kill()
            child.wait()
        self.addCleanup(cleanup_child)
        # Poll + wait in a background helper reaps the child so kill(0) won't
        # keep reporting a zombie. pidin observes the real test-owned process.
        import threading
        waiter = threading.Thread(target=child.wait, daemon=True)
        waiter.start()
        pidin = self.bin / "pidin"
        pidin.write_text(f"#!{sys.executable}\nimport os\nprint('pid Arguments')\n"
                         f"try: os.kill({child.pid}, 0)\nexcept ProcessLookupError: pass\n"
                         f"else: print('{child.pid} /opt/aa/nbt-aad')\n")
        self.run_script("install")
        waiter.join(timeout=3)
        self.assertIsNotNone(child.poll())

    def test_signal_immediately_after_renaming_old_directory_restores_it(self):
        self.old_install()
        real_mv = shutil.which("mv")
        self.stub("mv", shlex.quote(real_mv) + ' "$@" || exit $?\n'
                  'case "$2" in\n'
                  '  */plugin) if [ ! -f "$TEST_ROOT/signalled" ]; then\n'
                  '    touch "$TEST_ROOT/signalled"; kill -TERM "$PPID"; fi ;;\nesac')
        proc = self.run_script("install", ok=False)
        self.assertEqual(proc.returncode, 143)
        self.assert_old_intact()

    def test_signal_after_first_new_directory_removes_partial_install(self):
        real_mv = shutil.which("mv")
        self.stub("mv", shlex.quote(real_mv) + ' "$@" || exit $?\n'
                  'case "$1" in\n'
                  '  */staged-aa) kill -TERM "$PPID" ;;\nesac')
        self.run_script("install", ok=False)
        self.assertFalse(self.paths["AA"].exists())
        self.assertFalse(self.paths["PLUGIN"].exists())

    def test_staging_copy_failure_does_not_touch_previous_installation(self):
        self.old_install()
        real_cp = shutil.which("cp")
        self.stub("cp", 'case "$2" in */staged-plugin/*) exit 1 ;; esac\n'
                  'exec ' + shlex.quote(real_cp) + ' "$@"')
        self.run_script("install", ok=False)
        self.assert_old_intact()

    def test_uninstall_dry_run_preserves_everything(self):
        self.old_install()
        before = self.snapshot()
        self.run_script("uninstall", "--dry-run")
        self.assertEqual(before, self.snapshot())

    def test_jacinto_install_forwards_script_stdin_and_original_arguments(self):
        self.payload('umass-second', 'Releases')
        proc = self.run_script('install', '--usb', str(self.usb), '--restart-hmi', TEST_NODE='hu-jacinto')
        self.assertIn('Entering hu-omap automatically', proc.stdout)
        calls = self.calls.read_text().splitlines()
        self.assertEqual(calls.count('on'), 1)
        self.assertIn('hmi', calls)
        self.assertTrue((self.backup() / 'COMMITTED').exists())

    def test_jacinto_dry_run_forwards_without_mount_signals_or_writes(self):
        self.old_install()
        before = self.snapshot()
        self.run_script('install', '--dry-run', TEST_NODE='hu-jacinto')
        after = self.snapshot()
        after.pop('calls')  # Written by the mock on command, not by the installer.
        self.assertEqual(before, after)
        self.assertEqual(self.calls.read_text(), 'on\n')

    def test_jacinto_uninstall_forwards_and_archives_settings(self):
        self.old_install()
        self.run_script('uninstall', TEST_NODE='hu-jacinto')
        self.assertFalse(self.paths['AA'].exists())
        self.assertFalse(self.paths['SETTINGS'].exists())
        self.assertTrue((self.backup() / 'COMMITTED').exists())

    def test_jacinto_status_forwards_read_only(self):
        self.run_script('status', TEST_NODE='hu-jacinto')
        self.assertEqual(self.calls.read_text(), 'on\n')

    def test_forwarding_loop_is_rejected_before_remount(self):
        proc = self.run_script('install', TEST_NODE='hu-jacinto', TEST_FORWARD_NODE='hu-jacinto', ok=False)
        self.assertIn('forwarding loop', proc.stderr)
        self.assertEqual(self.calls.read_text(), 'on\n')

    def test_unreachable_remote_node_is_rejected(self):
        shutil.rmtree(self.paths['REMOTE_NODE'])
        self.run_script('install', TEST_NODE='hu-jacinto', ok=False)
        self.assertFalse(self.calls.exists())

    def test_restart_occurs_only_after_commit_and_lock_release(self):
        self.stub('OnOffDSICommander',
                  '[ ! -d "$TEST_ROOT/shmem/installer.lock" ] || exit 91\n'
                  'set -- "$TEST_ROOT"/opt/backups/*/COMMITTED\n'
                  '[ "$#" -eq 1 ] && [ -f "$1" ] || exit 92\n'
                  'echo hmi >> "$TEST_CALLS"')
        self.run_script('install', '--restart-hmi')
        self.assertIn('hmi', self.calls.read_text())

    def test_failed_transaction_never_restarts_hmi(self):
        self.old_install()
        self.fail_rename('/staged-plugin', '/NbtAad')
        self.run_script('install', '--restart-hmi', ok=False)
        self.assertNotIn('hmi', self.calls.read_text())

    def test_dry_run_with_restart_flag_does_not_restart(self):
        self.run_script('install', '--dry-run', '--restart-hmi')
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
