"""SSH transport without depending on an SFTP subsystem on the QNX device."""
import base64
import hashlib
from pathlib import Path
import shlex
import time
import paramiko


class RemoteError(Exception):
    pass


class RemoteExitError(RemoteError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__('Команда ГУ не завершилась успешно (код %s). %s' % (code, detail))


class TrustPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, confirm, known_hosts):
        self.confirm, self.known_hosts = confirm, Path(known_hosts)

    def missing_host_key(self, client, hostname, key):
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        if not self.confirm(hostname, fingerprint):
            raise RemoteError('Подключение к неизвестному ГУ отменено.')
        client.get_host_keys().add(hostname, key.get_name(), key)
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        client.save_host_keys(str(self.known_hosts))


class Session:
    def __init__(self, host, port, password, known_hosts, confirm, log=lambda _: None):
        self.client = paramiko.SSHClient()
        self.log = log
        if Path(known_hosts).exists():
            self.client.load_host_keys(str(known_hosts))
        self.client.set_missing_host_key_policy(TrustPolicy(confirm, known_hosts))
        try:
            self.client.connect(hostname=host, port=port, username='root', password=password,
                                look_for_keys=False, allow_agent=False, timeout=12,
                                banner_timeout=15, auth_timeout=15,
                                # Legacy NBT Evo uses ssh-rsa/HMAC-SHA1. Paramiko still
                                # negotiates these; no global TLS or SSH settings change.
                                compress=False)
            self.client.get_transport().set_keepalive(10)
        except Exception:
            self.client.close()
            raise

    def close(self):
        self.client.close()

    def run(self, command, data=None, timeout=240, stream=True, max_output=16 * 1024 * 1024):
        channel = self.client.get_transport().open_session(timeout=15)
        output, error, pending = bytearray(), bytearray(), bytearray()
        deadline = time.monotonic() + timeout
        try:
            channel.exec_command(command)
            if data is not None:
                channel.settimeout(30)
                channel.sendall(data)
            channel.shutdown_write()
            while True:
                if time.monotonic() > deadline:
                    raise RemoteError('ГУ не завершило команду за отведённое время. Состояние нужно проверить.')
                received = False
                for ready, read, target in ((channel.recv_ready, channel.recv, output),
                                             (channel.recv_stderr_ready, channel.recv_stderr, error)):
                    if ready():
                        part = read(65536)
                        target.extend(part)
                        received = True
                        if len(output) + len(error) > max_output:
                            raise RemoteError('Слишком большой ответ ГУ.')
                        if stream:
                            pending.extend(part)
                            while b'\n' in pending:
                                line, _, rest = pending.partition(b'\n')
                                pending = bytearray(rest)
                                self.log(line.decode('utf-8', errors='replace').rstrip('\r'))
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if not received:
                    if channel.closed:
                        raise RemoteError('Соединение закрылось до подтверждения выполнения команды.')
                    time.sleep(.02)
            if stream and pending:
                self.log(pending.decode('utf-8', errors='replace'))
            code = channel.recv_exit_status()
            if code != 0:
                detail = error.decode('utf-8', errors='replace').strip()[-1500:]
                if code < 0:
                    raise RemoteError('Соединение закрылось без кода завершения. ' + detail)
                raise RemoteExitError(code, detail)
            return bytes(output)
        finally:
            channel.close()

    def upload(self, path, data):
        self.run('umask 077; cat > ' + shlex.quote(path), data=data, stream=False)
        actual = self.run('cat ' + shlex.quote(path), stream=False, max_output=len(data)+4096)
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise RemoteError('Не совпала контрольная сумма загруженного файла.')
