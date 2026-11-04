"""Real loopback SSH: RSA/SHA1, binary stdin and host-key verification."""
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import paramiko
from nbt_aad_pc.ssh import Session, RemoteError


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
