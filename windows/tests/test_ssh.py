"""Real loopback SSH: RSA/SHA1, binary stdin and host-key verification."""
from pathlib import Path
import hashlib
import shlex
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import paramiko
from nbt_aad_pc.ssh import Session, RemoteError


class CommandCompletionTest(unittest.TestCase):
    def session(self, stdout=b'', stderr=b'', code=0):
        channel = Mock()
        channel.closed = False
        channel.eof_received = False
        buffers = [bytearray(stdout), bytearray(stderr)]
        def read(index, limit):
            result = bytes(buffers[index][:limit])
            del buffers[index][:limit]
            return result
        channel.recv_ready.side_effect = lambda: bool(buffers[0])
        channel.recv_stderr_ready.side_effect = lambda: bool(buffers[1])
        channel.recv.side_effect = lambda size: read(0, size)
        channel.recv_stderr.side_effect = lambda size: read(1, size)
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = code
        session = Session.__new__(Session)
        session.client = Mock()
        session.client.get_transport.return_value.open_session.return_value = channel
        session.log = Mock()
        return session, channel, buffers

    def test_exit_status_before_late_binary_data_and_stderr_waits_for_eof(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                session, channel, buffers = self.session(b'first\n')
                tail = bytes(range(256)) * 1024 + b'\nlast\n'
                def deliver_tail(_):
                    buffers[0].extend(tail)
                    buffers[1].extend(b'late warning\n')
                    channel.eof_received = True
                with patch('nbt_aad_pc.ssh.time.sleep', side_effect=deliver_tail):
                    actual = session.run('cat /var/opt/test', stream=stream)
                self.assertEqual(actual, b'first\n' + tail)
                self.assertFalse(any(buffers))
                if stream:
                    self.assertIn('late warning', '\n'.join(c.args[0] for c in session.log.call_args_list))
                channel.close.assert_called_once()

    def test_exit_status_without_eof_still_has_a_deadline(self):
        session, channel, _ = self.session()
        with patch('nbt_aad_pc.ssh.time.monotonic', side_effect=[0, 0, 2]), \
                patch('nbt_aad_pc.ssh.time.sleep'):
            with self.assertRaisesRegex(RemoteError, 'отведённое время'):
                session.run('cat /var/opt/test', timeout=1)
        channel.close.assert_called_once()

    def test_closed_without_exit_status_is_an_error(self):
        session, channel, _ = self.session(code=-1)
        channel.closed = True
        with self.assertRaisesRegex(RemoteError, 'без кода завершения'):
            session.run('cat /var/opt/test')
        channel.close.assert_called_once()


class UploadVerificationTest(unittest.TestCase):
    def session(self, responses):
        session = Session.__new__(Session)
        session.run = Mock(side_effect=responses)
        session.log = Mock()
        return session

    def test_one_mismatch_retransmits_then_requires_a_matching_digest(self):
        data = bytes(range(256)) * 1024
        session = self.session([b'', data[:-1], b'', data])
        session.upload('/var/opt/stage/nbt-aad', data, node='hu-jacinto')
        self.assertEqual(session.run.call_count, 4)
        writes = session.run.call_args_list[::2]
        self.assertEqual(writes[0], writes[1])
        self.assertIn('on -f /net/hu-omap', writes[0].args[0])
        session.log.assert_called_once()
        self.assertIn('2/2', session.log.call_args.args[0])

    def test_repeated_mismatch_stops_with_path_sizes_and_both_hashes(self):
        data, corrupt = b'correct', b'incorrect'
        session = self.session([b'', corrupt, b'', corrupt])
        with self.assertRaises(RemoteError) as caught:
            session.upload('/var/opt/stage/nbt-aad', data)
        self.assertEqual(session.run.call_count, 4)
        message = str(caught.exception)
        for detail in ['/var/opt/stage/nbt-aad', str(len(data)), str(len(corrupt)),
                       hashlib.sha256(data).hexdigest(), hashlib.sha256(corrupt).hexdigest()]:
            self.assertIn(detail, message)

    def test_remote_failure_does_not_retry_a_command(self):
        for responses in ([RemoteError('write failed')], [b'', RemoteError('read failed')]):
            with self.subTest(responses=responses):
                session = self.session(responses)
                with self.assertRaises(RemoteError):
                    session.upload('/var/opt/stage/nbt-aad', b'data')
                self.assertEqual(session.run.call_count, len(responses))
                session.log.assert_not_called()


class Handler(paramiko.ServerInterface):
    def __init__(self):
        self.command = None
        self.ready = threading.Event()
    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ('root', 'test-password') else paramiko.AUTH_FAILED
    def get_allowed_auths(self, username):
        return 'password'
    def check_channel_request(self, kind, channelid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_exec_request(self, channel, command):
        self.command = command.decode()
        self.ready.set()
        return True


class LoopbackServer:
    def __init__(self):
        self.key = paramiko.RSAKey.generate(2048)
        self.files = {}
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(1)
        self.socket.settimeout(15)
        self.port = self.socket.getsockname()[1]
        self.transport = None
        self.error = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()
    def serve(self):
        try:
            sock, _ = self.socket.accept()
            transport = self.transport = paramiko.Transport(sock)
            options = transport.get_security_options()
            options.key_types = ('ssh-rsa',)
            options.digests = ('hmac-sha1',)
            transport.add_server_key(self.key)
            handler = Handler()
            transport.start_server(server=handler)
            while transport.is_active():
                channel = transport.accept(timeout=1)
                if channel is None:
                    continue
                if not handler.ready.wait(5):
                    raise RuntimeError('No exec request')
                command = handler.command
                handler.ready.clear()
                omap_prefix = 'on -f /net/hu-omap /bin/sh -c '
                if command.startswith(omap_prefix):
                    local, = shlex.split(command[len(omap_prefix):])
                    command = local.replace('/var/opt/nbt-aad-pc-test/', 'omap:')
                data = bytearray()
                while True:
                    chunk = channel.recv(65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                if command.startswith('umask 077; cat > '):
                    self.files[command.split(' > ', 1)[1]] = bytes(data)
                elif command.startswith('cat '):
                    channel.sendall(self.files[command[4:]])
                elif command == 'echo-test':
                    channel.sendall(b'output\n')
                    channel.send_stderr(b'warning\n')
                elif command == 'fail':
                    channel.send_stderr(b'injected failure')
                    channel.send_exit_status(7)
                    channel.close()
                    continue
                channel.send_exit_status(0)
                channel.close()
        except (EOFError, ConnectionResetError):
            pass
        except Exception as exc:
            self.error = exc
    def close(self):
        if self.transport:
            self.transport.close()
        self.socket.close()
        self.thread.join(timeout=5)


class SSHTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = LoopbackServer()
        self.addCleanup(self.server.close)
        self.known = Path(self.tmp.name) / 'known_hosts'
        self.logs = []
    def connect(self, confirm=lambda *args: True):
        session = Session('127.0.0.1', self.server.port, 'test-password', self.known, confirm, self.logs.append)
        self.addCleanup(session.close)
        return session
    def test_real_legacy_ssh_upload_readback_and_streamed_error(self):
        session = self.connect()
        data = bytes(range(256)) * 4000
        session.upload('/dev/shmem/test', data)
        self.assertEqual(self.server.files['/dev/shmem/test'], data)
        session.upload('/var/opt/nbt-aad-pc-test/binary', data, node='hu-jacinto')
        self.assertEqual(self.server.files['omap:binary'], data)
        self.assertEqual(session.run('echo-test'), b'output\n')
        self.assertIn('warning', self.logs)
        self.assertTrue(self.known.exists())
        self.assertEqual(session.client.get_transport().host_key_type, 'ssh-rsa')
        self.assertEqual(session.client.get_transport().remote_mac, 'hmac-sha1')
        self.assertIsNone(self.server.error)
    def test_remote_error_is_not_success(self):
        with self.assertRaisesRegex(RemoteError, '7'):
            self.connect().run('fail')
    def test_unknown_key_can_be_rejected(self):
        with self.assertRaises(RemoteError):
            self.connect(lambda *args: False)
        self.assertFalse(self.known.exists())
    def test_saved_different_key_is_rejected_without_trust_prompt(self):
        keys = paramiko.HostKeys()
        keys.add('[127.0.0.1]:%s' % self.server.port, 'ssh-rsa', paramiko.RSAKey.generate(2048))
        keys.save(str(self.known))
        asked = []
        with self.assertRaises(paramiko.BadHostKeyException):
            self.connect(lambda *args: asked.append(args) or True)
        self.assertEqual(asked, [])
