from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import shlex
import uuid
from .releases import PackageError
from .ssh import RemoteError, RemoteExitError, Session
from .installed import installed_versions


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
    output = session.run('uname -s; uname -n', stream=False).decode().splitlines()
    if len(output) != 2 or output[0] != 'QNX':
        raise RemoteError('Ожидалось подключение к QNX на NBT Evo.')
    node = output[1].split('.')[0]
    if node not in ('hu-omap', 'hu-jacinto'):
        raise RemoteError('Неизвестный узел QNX: ' + output[1])
    base = remote_path(node, '')
    session.run('test -d ' + shlex.quote(base + '/fs/sda0/opt/hmi/ID5'), stream=False)
    credentials = session.run(on_omap(node, 'echo NBT_AAD_SHELL_PID=$$; pidin -p $$ -F "%a %W"'),
                              stream=False).decode().splitlines()
    marker = re.fullmatch(r'NBT_AAD_SHELL_PID=([0-9]+)', credentials[0]) if credentials else None
    if not marker:
        raise RemoteError('Не удалось определить PID проверяемой оболочки ГУ.')
    rows = [line.split() for line in credentials[1:] if line.split() and line.split()[0] == marker[1]]
    if not rows or any(len(row) != 2 or row[1] != '0' for row in rows):
        raise RemoteError('Root-доступ на hu-omap не подтверждён: эффективный UID должен быть 0.')
    return node


def on_omap(node, command):
    if node == 'hu-jacinto':
        return 'on -f /net/hu-omap /bin/sh -c ' + shlex.quote(command)
    return command


def restart(session, node, files_processed=False):
    # When called after installation, the commit has already been verified.
    # so distinguish a requested reset from a confirmed boot of the head unit.
    command = on_omap(node, 'command -v OnOffDSICommander >/dev/null && '
                      'if command -v sync >/dev/null 2>&1; then sync || exit 1; fi; '
                      'echo NBT_AAD_RESTART_REQUESTED && OnOffDSICommander appreset')
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
        if not files_processed:
            raise RemoteError('Команда перезагрузки завершилась ошибкой: ' + str(exc)) from exc
        return 'Файлы обработаны успешно, но команда перезагрузки завершилась ошибкой: ' + str(exc)
    except Exception as exc:
        if 'NBT_AAD_RESTART_REQUESTED' in seen:
            return 'Команда перезагрузки отправлена, SSH закрылся. Дождитесь загрузки ГУ.'
        if not files_processed:
            raise RemoteError('Перезагрузку подтвердить не удалось: ' + str(exc)) from exc
        return 'Файлы обработаны успешно, но перезагрузку подтвердить не удалось: ' + str(exc)
    finally:
        session.log = original_log


class Installer:
    def __init__(self, script, state, log, confirm, session_factory=Session):
        self.script, self.state = Path(script), Path(state)
        self.log, self.confirm, self.session_factory = log, confirm, session_factory

    def execute(self, action, connection, package=None, reboot=True, removal='keep'):
        if action not in ('install', 'uninstall', 'status', 'restart'):
            raise ValueError('Неизвестная операция.')
        connection.validate()
        if removal not in ('keep', 'purge'):
            raise ValueError('Неизвестный режим удаления.')
        script, files = b'', {}
        if action != 'restart':
            script = self.script.read_bytes()
            if b'--source PATH' not in script:
                raise PackageError('Нужен установщик версии 0.3.0 или новее с поддержкой SSH.')
            files['nbt-aad.sh'] = script
        if action == 'install':
            if package is None:
                raise PackageError('Не выбран комплект NBT-AAD.')
            files.update({name: file.read_bytes() for name, file in package.files().items()})
        if sum(map(len, files.values())) > 16 * 1024 * 1024:
            raise PackageError('Комплект слишком велик для временной загрузки на ГУ.')
        self.log('Подключение по SSH к %s:%s…' % (connection.host, connection.port))
        session = self.session_factory(connection.host, connection.port, connection.password,
                                       self.state / 'known_hosts', self.confirm, self.log)
        stage, node = None, None
        completed = False
        try:
            node = detect(session)
            self.log('Проверка ГУ пройдена: ' + node)
            if action == 'restart':
                self.log('Отправка команды перезагрузки ГУ…')
                return restart(session, node)
            if action == 'status':
                # No staging or writes to the HU in status mode.
                command = on_omap(node, '/bin/sh -s -- status')
                status = session.run(command, data=script).decode('utf-8', errors='replace')
                versions = installed_versions(session, node, self.state / 'packages', self.log)
                return versions + '\n\n' + status
            stage = '/var/opt/nbt-aad-pc-' + uuid.uuid4().hex
            target = remote_path(node, stage)
            session.run('umask 077; mkdir ' + shlex.quote(target) +
                        ' && mkdir ' + shlex.quote(target + '/release'), stream=False)
            for index, (name, data) in enumerate(files.items(), 1):
                self.log('Передача и проверка %s (%s/%s)' % (name, index, len(files)))
                session.upload(target + '/' + name, data)
            command = '/bin/sh ' + shlex.quote(target + '/nbt-aad.sh') + ' ' + action + ' --source ' + shlex.quote(stage)
            if action == 'uninstall':
                command += ' --keep-config' if removal == 'keep' else ' --purge'
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
            if action == 'install' and 'pf_aa' in files:
                present += ' && test -s /opt/aa/pf_aa && test -x /opt/aa/pf_aa'
            absent = ('test ! -e /opt/aa/nbt-aad && test ! -e /opt/hmi/ID5/data/ro/Plugins/NbtAad'
                      if removal == 'keep' else
                      'test ! -e /opt/aa && test ! -e /opt/hmi/ID5/data/ro/Plugins/NbtAad && '
                      'test ! -e /var/opt/aa && test ! -e /opt/nbt-aad-usb-tools-backups && '
                      'test ! -e /var/opt/nbt-aad-usb-tools-backups')
            session.run(on_omap(node, present if action == 'install' else absent), stream=False)
            completed = True
            self.log('Результат на ГУ проверен. ' + ('Резервные копии удалены.'
                     if action == 'uninstall' and removal == 'purge' else 'Резервные копии остаются на ГУ.'))
            # Delete only this run's private upload, before a reset can terminate SSH.
            try:
                self.cleanup(session, node, stage)
            except Exception as exc:
                self.log('Файлы установлены/удалены, но временную загрузку очистить не удалось: ' + str(exc))
            stage = None
            message = ('Установка / обновление завершены.' if action == 'install' else
                       'NBT-AAD удалён. ' + ('Настройки сохранены на ГУ.' if removal == 'keep' else
                       'Настройки, резервные копии и временные файлы удалены.'))
            if reboot:
                message += '\n' + restart(session, node, files_processed=True)
            else:
                message += '\nПерезагрузка отключена в настройках.'
            return message
        finally:
            if stage and not completed:
                if action == 'uninstall' and removal == 'purge':
                    self.log('Полная очистка не подтверждена. Часть файлов и копий могла быть уже удалена. '
                             'Временный путь этой операции: ' + stage + '. Проверьте журнал перед повтором.')
                else:
                    self.log('При незавершённой операции временные файлы оставлены в ' + stage +
                             '. Они сохранены для диагностики; резервные копии не удаляются.')
            session.close()

    @staticmethod
    def cleanup(session, node, stage):
        if not re.fullmatch(r'/var/opt/nbt-aad-pc-[0-9a-f]{32}', stage):
            raise RemoteError('Недопустимый путь очистки.')
        session.run('rm -rf ' + shlex.quote(remote_path(node, stage)), stream=False)
