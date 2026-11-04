from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import shlex
import uuid
from .releases import PackageError
from .ssh import RemoteError, RemoteExitError, Session


@dataclass
class Connection:
    host: str = '169.254.199.99'
    port: int = 22
    password: str = ''

    def validate(self):
        ipaddress.ip_address(self.host)
        if not 1 <= self.port <= 65535:
            raise ValueError('Неверный порт SSH.')
        if not self.password:
            raise ValueError('Введите пароль root.')


def remote_path(node, local):
    if node == 'hu-jacinto':
        return '/net/hu-omap' + local
    return local


def detect(session):
    output = session.run('uname -s; uname -n; id -u', stream=False).decode().splitlines()
    if len(output) != 3 or output[0] != 'QNX' or output[2] != '0':
        raise RemoteError('Ожидалось root-подключение к QNX на NBT Evo.')
    node = output[1].split('.')[0]
    if node not in ('hu-omap', 'hu-jacinto'):
        raise RemoteError('Неизвестный узел QNX: ' + output[1])
    base = remote_path(node, '')
    session.run('test -d ' + shlex.quote(base + '/fs/sda0/opt/hmi/ID5'), stream=False)
    return node


def on_omap(node, command):
    if node == 'hu-jacinto':
        return 'on -f /net/hu-omap /bin/sh -c ' + shlex.quote(command)
    return command


def restart(session, node):
    # Commit is already acknowledged and verified. The reset can close SSH,
    # so distinguish a requested reset from a confirmed boot of the head unit.
    command = on_omap(node, 'command -v OnOffDSICommander >/dev/null && '
                      'sync && printf "NBT_AAD_RESTART_REQUESTED\\n" && OnOffDSICommander appreset')
    seen = []
    original_log = session.log
    def collect(line):
        seen.append(line)
        original_log(line)
    session.log = collect
    try:
        session.run(command, timeout=30)
        return 'Перезагрузка ГУ запрошена. Дождитесь появления штатного меню.'
    except RemoteExitError as exc:
        return 'Файлы обработаны успешно, но команда перезагрузки завершилась ошибкой: ' + str(exc)
    except Exception as exc:
        if 'NBT_AAD_RESTART_REQUESTED' in seen:
            return 'Команда перезагрузки отправлена, SSH закрылся. Дождитесь загрузки ГУ.'
        return 'Файлы обработаны успешно, но перезагрузку подтвердить не удалось: ' + str(exc)
    finally:
        session.log = original_log


class Installer:
    def __init__(self, script, state, log, confirm, session_factory=Session):
        self.script, self.state = Path(script), Path(state)
        self.log, self.confirm, self.session_factory = log, confirm, session_factory

    def execute(self, action, connection, package=None, reboot=True):
        if action not in ('install', 'uninstall', 'status'):
            raise ValueError('Неизвестная операция.')
        connection.validate()
        script = self.script.read_bytes()
        if b'--source PATH' not in script:
            raise PackageError('Нужен установщик версии 0.3.0 или новее с поддержкой SSH.')
        files = {'nbt-aad.sh': script}
        if action == 'install':
            if package is None:
                raise PackageError('Не выбран комплект NBT-AAD.')
            files.update({name: file.read_bytes() for name, file in package.files().items()})
        if sum(map(len, files.values())) > 16 * 1024 * 1024:
            raise PackageError('Комплект слишком велик для временной загрузки в память ГУ.')
        self.log('Подключение по SSH к %s:%s…' % (connection.host, connection.port))
        session = self.session_factory(connection.host, connection.port, connection.password,
                                       self.state / 'known_hosts', self.confirm, self.log)
        stage, node = None, None
        completed = False
        try:
            node = detect(session)
            self.log('Проверка ГУ пройдена: ' + node)
            if action == 'status':
                # No staging or writes to the HU in status mode.
                command = on_omap(node, '/bin/sh -s -- status')
                return session.run(command, data=script).decode('utf-8', errors='replace')
            stage = '/dev/shmem/nbt-aad-pc-' + uuid.uuid4().hex
            target = remote_path(node, stage)
            session.run('umask 077; mkdir ' + shlex.quote(target) +
                        ' && mkdir ' + shlex.quote(target + '/release'), stream=False)
            for index, (name, data) in enumerate(files.items(), 1):
                self.log('Передача и проверка %s (%s/%s)' % (name, index, len(files)))
                session.upload(target + '/' + name, data)
            command = '/bin/sh ' + shlex.quote(target + '/nbt-aad.sh') + ' ' + action + ' --source ' + shlex.quote(stage)
            self.log('Проверка установки без изменений…')
            validation = session.run(command + ' --dry-run')
            if b'DRY RUN: validation passed' not in validation and b'Already uninstalled.' not in validation:
                raise RemoteError('Установщик не подтвердил готовность к операции.')
            self.log('Выполнение операции… Не отключайте питание и ENET.')
            result = session.run(command)
            marker = ('SUCCESS: ' + action + ' completed.').encode()
            if marker not in result and not (action == 'uninstall' and b'Already uninstalled.' in result):
                raise RemoteError('Нет подтверждения успешной операции. Проверьте журнал.')
            # Independently verify the resulting paths on the actual OMAP node.
            present = 'test -s /opt/aa/nbt-aad && test -s /opt/hmi/ID5/data/ro/Plugins/NbtAad/libNbtAad.so'
            absent = 'test ! -e /opt/aa && test ! -e /opt/hmi/ID5/data/ro/Plugins/NbtAad && test ! -e /var/opt/aa'
            session.run(on_omap(node, present if action == 'install' else absent), stream=False)
            completed = True
            self.log('Результат на ГУ проверен. Резервные копии остаются на ГУ.')
            # Delete only this run's private upload, before a reset can terminate SSH.
            try:
                self.cleanup(session, node, stage)
            except Exception as exc:
                self.log('Файлы установлены/удалены, но временную загрузку очистить не удалось: ' + str(exc))
            stage = None
            message = 'Установка / обновление завершены.' if action == 'install' else 'NBT-AAD удалён.'
            if reboot:
                message += '\n' + restart(session, node)
            else:
                message += '\nПерезагрузка отключена в настройках.'
            return message
        finally:
            if stage and not completed:
                self.log('При незавершённой операции временные файлы оставлены в ' + stage +
                         '. Они исчезнут после перезагрузки; резервные копии не удаляются.')
            session.close()

    @staticmethod
    def cleanup(session, node, stage):
        if not re.fullmatch(r'/dev/shmem/nbt-aad-pc-[0-9a-f]{32}', stage):
            raise RemoteError('Недопустимый путь очистки.')
        session.run('rm -rf ' + shlex.quote(remote_path(node, stage)), stream=False)
