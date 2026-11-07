from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest
from nbt_aad_pc.controller import Connection, Installer, restart
from nbt_aad_pc.ssh import RemoteError, RemoteExitError, Session


class FakeSession:
    def __init__(self, *args):
        self.log = args[-1]
        self.calls, self.uploads, self.closed = [], {}, False
        self.node = 'hu-omap'
        self.credential_output = b'NBT_AAD_SHELL_PID=42\npid euid\n42 0\n'
        self.fail_dry = False
        self.fail_mkdir = False
        self.fail_commit = False
        self.no_success = False
        self.fail_verify = False
        self.fail_reset = False
        self.flat_shmem = False
        self.fail_reset_without_ack = False
        self.reset_exit_code = None

    def close(self):
        self.closed = True

    upload = Session.upload

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == 'uname -s; uname -n':
            return ('QNX\n' + self.node + '\n').encode()
        if self.node == 'hu-jacinto':
            prefix = 'on -f /net/hu-omap /bin/sh -c '
            if not command.startswith(prefix):
                raise RemoteExitError(1, 'Path unavailable from Jacinto: ' + command)
            command, = shlex.split(command[len(prefix):])
            if '/net/hu-omap/' in command:
                raise RemoteExitError(1, 'Expected local OMAP filesystem paths')
        if 'mkdir ' in command and self.fail_mkdir:
            raise RemoteExitError(1, 'mkdir: No such file or directory')
        if self.flat_shmem and 'mkdir' in command and '/dev/shmem/' in command:
            raise RemoteError('Function not implemented')
        if 'NBT_AAD_SHELL_PID=' in command:
            return self.credential_output
        if command.startswith('umask 077; cat > '):
            path, = shlex.split(command.partition(' > ')[2])
            self.uploads[path] = kwargs['data']
            return b''
        if command.startswith('cat '):
            path, = shlex.split(command[4:])
            return self.uploads[path]
        if '--dry-run' in command:
            if self.fail_dry:
                raise RemoteError('dry-run failed')
            return b'DRY RUN: validation passed.\n'
        if '--source' in command:
            if self.fail_commit:
                raise RemoteError('connection closed')
            if self.no_success:
                return b'no acknowledgment'
            action = 'uninstall' if ' uninstall ' in command else 'install'
            return ('SUCCESS: ' + action + ' completed.\n').encode()
        if 'test -s /opt/aa' in command and self.fail_verify:
            raise RemoteError('verification failed')
        if 'echo NBT_AAD_RESTART_REQUESTED' in command:
            if self.fail_reset_without_ack:
                raise RemoteError('SSH closed before reset acknowledgment')
            self.log('NBT_AAD_RESTART_REQUESTED')
            if self.reset_exit_code is not None:
                raise RemoteExitError(self.reset_exit_code, 'reset rejected')
            if self.fail_reset:
                raise RemoteError('SSH closed during reboot')
        return b'Present: /opt/aa/nbt-aad\n'


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / 'nbt-aad.sh'
        self.script.write_bytes(b'# --source PATH\n')
        self.logs = []
        self.session = FakeSession('', '', '', '', '', self.logs.append)
        self.installer = Installer(self.script, self.root, self.logs.append, lambda *a: True,
                                   session_factory=lambda *a: self.session)
        file = self.root / 'nbt-aad'; file.write_bytes(b'binary')
        class Package:
            def files(self):
                return {'nbt-aad': file}
        self.package = Package()
        self.connection = Connection(password='secret')

    def execute(self, action='install', **kwargs):
        return self.installer.execute(action, self.connection, self.package, **kwargs)

    def commands(self):
        return '\n'.join(x[0] for x in self.session.calls)

    def test_install_upload_validate_verify_cleanup_then_reset(self):
        self.assertIn('завершены', self.execute())
        commands = self.commands()
        self.assertLess(commands.index('--dry-run'), commands.index('test -s /opt/aa'))
        self.assertLess(commands.index('rm -rf'), commands.index('echo NBT_AAD_RESTART_REQUESTED'))
        self.assertEqual(len(self.session.uploads), 2)
        self.assertTrue(self.session.closed)

    def test_optional_pf_aa_upload_is_verified_without_applying_rules(self):
        helper = self.root / 'pf_aa'
        helper.write_bytes(b'validated package helper')
        original = self.package.files
        self.package.files = lambda: {**original(), 'pf_aa': helper}
        self.execute()
        self.assertEqual(len(self.session.uploads), 3)
        self.assertIn('test -x /opt/aa/pf_aa', self.commands())
        self.assertNotIn('pf_aa --install', self.commands())

    def test_jacinto_stages_transfers_and_runs_on_omap_for_install_and_uninstall(self):
        self.session.node = 'hu-jacinto'
        for action, removal in (('install', 'keep'), ('uninstall', 'keep'), ('uninstall', 'purge')):
            with self.subTest(action=action, removal=removal):
                self.session.uploads.clear()
                self.session.calls.clear()
                self.execute(action, removal=removal)
                self.assertEqual(len(self.session.uploads), 2 if action == 'install' else 1)
                self.assertTrue(all(p.startswith('/var/opt/nbt-aad-pc-') for p in self.session.uploads))
                commands = self.commands()
                self.assertIn('cat /var/opt/nbt-aad-pc-', commands)
                self.assertIn(action + ' --source /var/opt/nbt-aad-pc-', commands)
                self.assertIn('rm -rf /var/opt/nbt-aad-pc-', commands)
                self.assertIn('echo NBT_AAD_RESTART_REQUESTED', commands)

    def test_purge_preparation_failure_never_reports_partial_deletion(self):
        self.session.node = 'hu-jacinto'
        self.session.fail_mkdir = True
        with self.assertRaisesRegex(RemoteError, 'временный каталог.*hu-omap.*mkdir'):
            self.execute('uninstall', removal='purge')
        self.assertFalse(self.session.uploads)
        self.assertNotIn('--purge', self.commands())
        self.assertNotIn('appreset', self.commands())
        self.assertIn('Сбой до запуска установки или удаления.', self.logs)
        self.assertFalse(any('могла быть уже удалена' in line for line in self.logs))

    def test_status_has_no_staging_upload_or_reset(self):
        self.execute('status')
        self.assertFalse(self.session.uploads)
        self.assertNotIn('mkdir', self.commands())
        self.assertNotIn('appreset', self.commands())

    def test_no_reset_or_cleanup_after_uncertain_commit(self):
        for option in ('fail_dry', 'fail_commit', 'no_success', 'fail_verify'):
            with self.subTest(option=option):
                self.session.calls.clear()
                setattr(self.session, option, True)
                with self.assertRaises(RemoteError):
                    self.execute()
                self.assertNotIn('appreset', self.commands())
                self.assertNotIn('rm -rf', self.commands())
                setattr(self.session, option, False)

    def test_reset_disconnect_is_distinguished_from_install_failure(self):
        self.session.fail_reset = True
        self.assertIn('Команда перезагрузки отправлена', self.execute())

    def test_uninstall_does_not_require_downloaded_package(self):
        self.installer.execute('uninstall', self.connection)
        self.assertEqual(len(self.session.uploads), 1)
        self.assertIn('test ! -e /opt/aa', self.commands())
        self.assertIn('--keep-config', self.commands())
        self.assertNotIn('test ! -e /var/opt/aa', self.commands())

    def test_full_removal_is_explicit_and_verifies_backup_absence(self):
        self.installer.execute('uninstall', self.connection, removal='purge')
        self.assertEqual(len(self.session.uploads), 1)
        self.assertIn('--purge --dry-run', self.commands())
        self.assertIn('test ! -e /opt/nbt-aad-usb-tools-backups', self.commands())
        self.assertIn('test ! -e /var/opt/nbt-aad-usb-tools-backups', self.commands())
        self.assertNotIn('--keep-config', self.commands())
        self.assertTrue(any('Резервные копии удалены' in line for line in self.logs))

    def test_invalid_removal_mode_is_rejected_before_connecting(self):
        with self.assertRaises(ValueError):
            self.installer.execute('uninstall', self.connection, removal='other')
        self.assertFalse(self.session.calls)

    def test_restart_can_be_disabled(self):
        self.execute(reboot=False)
        self.assertNotIn('appreset', self.commands())

    def test_standalone_restart_needs_no_script_or_package_and_makes_no_install_changes(self):
        self.script.unlink()
        self.session.node = 'hu-jacinto'
        message = self.installer.execute('restart', self.connection, reboot=False)
        self.assertIn('Перезагрузка ГУ запрошена', message)
        self.assertFalse(self.session.uploads)
        self.assertNotIn('mkdir', self.commands())
        self.assertNotIn('--source', self.commands())
        self.assertIn('on -f /net/hu-omap', self.commands())
        self.assertTrue(self.session.closed)

    def test_standalone_restart_failure_is_an_error_without_file_success_claim(self):
        for flag in ('fail_reset_without_ack', 'reset_exit_code'):
            with self.subTest(flag=flag):
                setattr(self.session, flag, True if flag.startswith('fail') else 1)
                with self.assertRaises(RemoteError) as error:
                    self.execute('restart')
                self.assertNotIn('Файлы обработаны успешно', str(error.exception))
                setattr(self.session, flag, False if flag.startswith('fail') else None)
        self.assertFalse(self.session.uploads)

    def test_standalone_restart_can_disconnect_after_acknowledgment(self):
        self.session.fail_reset = True
        self.assertIn('Команда перезагрузки отправлена', self.execute('restart'))

    def test_reset_error_after_install_keeps_confirmed_file_result(self):
        self.session.reset_exit_code = 1
        self.assertIn('Файлы обработаны успешно', self.execute())

    def test_qnx_flat_shmem_upload_uses_real_var_filesystem(self):
        self.session.node = 'hu-jacinto'
        self.session.flat_shmem = True
        self.execute()
        self.assertTrue(self.session.uploads)
        self.assertTrue(all(path.startswith('/var/opt/nbt-aad-pc-')
                            for path in self.session.uploads))
        self.assertNotIn('/dev/shmem/', self.commands())
        self.assertTrue(self.session.closed)

    def test_unknown_node_and_non_ip_are_rejected(self):
        self.session.node = 'unknown'
        with self.assertRaises(RemoteError):
            self.execute()
        self.assertFalse(self.session.uploads)
        for host in ('169.254.199.99; shutdown', 'example.com'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                Connection(host=host, password='pw').validate()

    def test_cleanup_rejects_arbitrary_paths(self):
        for path in ('/opt/aa', '/var/opt/nbt-aad-pc-../aa', '/var/opt/nbt-aad-pc-short'):
            with self.subTest(path=path), self.assertRaises(RemoteError):
                Installer.cleanup(self.session, 'hu-omap', path)

    def test_root_check_uses_pidin_on_omap_without_id_or_printf(self):
        self.session.node = 'hu-jacinto'
        self.execute()
        commands = self.commands()
        self.assertIn('on -f /net/hu-omap /bin/sh -c', commands)
        self.assertIn('pidin -p $$ -F', commands)
        self.assertNotIn('id -u', commands)
        self.assertNotIn('printf', commands)

    def test_nonroot_empty_or_mismatched_credentials_stop_before_upload(self):
        for output in (b'NBT_AAD_SHELL_PID=42\n42 1000\n', b'',
                       b'NBT_AAD_SHELL_PID=42\n43 0\n',
                       b'NBT_AAD_SHELL_PID=42\n42 0\n42 1000\n'):
            with self.subTest(output=output), self.assertRaises(RemoteError):
                self.session.credential_output = output
                self.execute()
            self.assertFalse(self.session.uploads)
            self.assertNotIn('appreset', self.commands())

    @unittest.skipIf(os.name == 'nt', 'Runs the remote POSIX command locally')
    def test_reset_prefers_firmware_path_and_passes_library_environment_without_sync(self):
        binary = self.root / 'core-bin/OnOffDSICommander'
        binary.parent.mkdir()
        marker = self.root / 'reset-requested'
        binary.write_text('#!/bin/sh\n'
                          'case "$LD_LIBRARY_PATH" in /opt/sys/lib:*:/inherited/lib) ;; *) exit 126 ;; esac\n'
                          'echo "$1" > "' + str(marker) + '"\n')
        binary.chmod(0o755)
        wrong = self.root / 'OnOffDSICommander'
        wrong.write_text('#!/bin/sh\nexit 126\n')
        wrong.chmod(0o755)
        class LocalShellSession:
            def __init__(session):
                session.log = self.logs.append
            def run(session, command, **kwargs):
                command = command.replace('/opt/core/bin/OnOffDSICommander', str(binary), 1)
                result = subprocess.run(['/bin/sh', '-c', command],
                    env={'PATH': str(self.root), 'LD_LIBRARY_PATH': '/inherited/lib'},
                    capture_output=True, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    session.log(line)
                if result.returncode:
                    raise RemoteExitError(result.returncode, result.stderr)
                return result.stdout.encode()
        message = restart(LocalShellSession(), 'hu-omap')
        self.assertIn('Перезагрузка ГУ запрошена', message)
        self.assertEqual(marker.read_text(), 'appreset\n')
        self.assertIn('OnOffDSICommander: ' + str(binary), self.logs)
