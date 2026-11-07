"""SSH transport without depending on an SFTP subsystem on the QNX device."""
import base64
import hashlib
from pathlib import Path
import shlex
import socket
import time
import paramiko


class RemoteError(Exception):
    pass


class RemoteExitError(RemoteError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__('Команда ГУ не завершилась успешно (код %s). %s' % (code, detail))


def with_enet_hint(message):
    return (message + '\n\nПроверьте подключение кабеля ENET к автомобилю '
            'и убедитесь, что зажигание включено.')


def on_omap(node, command):
    if node == 'hu-jacinto':
        return 'on -f /net/hu-omap /bin/sh -c ' + shlex.quote(command)
    return command


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
    def __init__(self, host, port, password, known_hosts, confirm, log=lambda _: None, source_ip=''):
        self.client = paramiko.SSHClient()
        self.log = log
        if Path(known_hosts).exists():
            self.client.load_host_keys(str(known_hosts))
        self.client.set_missing_host_key_policy(TrustPolicy(confirm, known_hosts))
        bound_socket = None
        try:
            if source_ip:
                bound_socket = socket.create_connection((host, port), timeout=12,
                                                        source_address=(source_ip, 0))
            kwargs = {'sock': bound_socket} if bound_socket is not None else {}
            self.client.connect(hostname=host, port=port, username='root', password=password,
                                look_for_keys=False, allow_agent=False, timeout=12,
                                banner_timeout=15, auth_timeout=15,
                                # Legacy NBT Evo uses ssh-rsa/HMAC-SHA1. Paramiko still
                                # negotiates these; no global TLS or SSH settings change.
                                compress=False, **kwargs)
            self.client.get_transport().set_keepalive(10)
        except Exception as exc:
            self.client.close()
            if bound_socket is not None:
                bound_socket.close()
            if isinstance(exc, (OSError, EOFError)):
                raise RemoteError(with_enet_hint('Не удалось подключиться к ГУ по SSH: ' + str(exc))) from exc
            raise

    def close(self):
        self.client.close()

    def run(self, command, data=None, timeout=240, stream=True, max_output=16 * 1024 * 1024):
        output, error, pending = bytearray(), bytearray(), bytearray()
        transport = self.client.get_transport()
        channel = None
        try:
            channel = transport.open_session(timeout=15)
            deadline = time.monotonic() + timeout
            channel.exec_command(command)
            if data is not None:
                channel.settimeout(30)
                channel.sendall(data)
            channel.shutdown_write()
            while True:
                if time.monotonic() > deadline:
                    raise RemoteError(with_enet_hint(
                        'ГУ не завершило команду за отведённое время. Состояние нужно проверить.'))
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
                        raise RemoteError(with_enet_hint(
                            'Соединение закрылось до подтверждения выполнения команды.'))
                    time.sleep(.02)
            if stream and pending:
                self.log(pending.decode('utf-8', errors='replace'))
            code = channel.recv_exit_status()
            if code != 0:
                detail = error.decode('utf-8', errors='replace').strip()[-1500:]
                if code < 0:
                    raise RemoteError(with_enet_hint('Соединение закрылось без кода завершения. ' + detail))
                raise RemoteExitError(code, detail)
            return bytes(output)
        except (OSError, EOFError) as exc:
            raise RemoteError(with_enet_hint('Связь с ГУ по SSH прервана: ' + str(exc))) from exc
        except paramiko.SSHException as exc:
            if not transport.is_active():
                raise RemoteError(with_enet_hint('Связь с ГУ по SSH прервана: ' + str(exc))) from exc
            raise
        finally:
            if channel is not None:
                channel.close()

    def upload(self, path, data, *, node='hu-omap'):
        quoted = shlex.quote(path)
        self.run(on_omap(node, 'umask 077; cat > ' + quoted), data=data, stream=False)
        actual = self.run(on_omap(node, 'cat ' + quoted), stream=False, max_output=len(data)+4096)
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise RemoteError('Не совпала контрольная сумма загруженного файла.')
