from pathlib import Path
import tempfile
import unittest
from nbt_aad_pc.controller import Connection, Installer, remote_path
from nbt_aad_pc.ssh import RemoteError


class FakeSession:
    def __init__(self, *args):
        self.log = args[-1]
        self.calls, self.uploads, self.closed = [], {}, False
        self.node = 'hu-omap'
        self.credential_output = b'NBT_AAD_SHELL_PID=42\npid euid\n42 0\n'
        self.fail_dry = False
        self.fail_commit = False
        self.no_success = False
        self.fail_verify = False
        self.fail_reset = False

    def close(self):
        self.closed = True

    def upload(self, path, data):
        self.uploads[path] = data

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command == 'uname -s; uname -n':
            return ('QNX\n' + self.node + '\n').encode()
        if 'NBT_AAD_SHELL_PID=' in command:
            return self.credential_output
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
        if 'OnOffDSICommander appreset' in command:
            self.log('NBT_AAD_RESTART_REQUESTED')
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
        self.assertLess(commands.index('rm -rf'), commands.index('OnOffDSICommander appreset'))
        self.assertEqual(len(self.session.uploads), 2)
        self.assertTrue(self.session.closed)

    def test_jacinto_uses_qnet_upload_and_omap_reset(self):
        self.session.node = 'hu-jacinto'
        self.execute()
        self.assertTrue(all(p.startswith('/net/hu-omap/dev/shmem/') for p in self.session.uploads))
        self.assertIn('on -f /net/hu-omap /bin/sh -c', self.commands())
        self.assertEqual(remote_path('hu-omap', '/dev/shmem/a'), '/dev/shmem/a')

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

    def test_restart_can_be_disabled(self):
        self.execute(reboot=False)
        self.assertNotIn('appreset', self.commands())

    def test_unknown_node_and_non_ip_are_rejected(self):
        self.session.node = 'unknown'
        with self.assertRaises(RemoteError):
            self.execute()
        self.assertFalse(self.session.uploads)
        for host in ('169.254.199.99; shutdown', 'example.com'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                Connection(host=host, password='pw').validate()

    def test_cleanup_rejects_arbitrary_paths(self):
        for path in ('/opt/aa', '/dev/shmem/nbt-aad-pc-../aa', '/dev/shmem/nbt-aad-pc-short'):
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
