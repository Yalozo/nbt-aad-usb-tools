from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import shlex
import uuid

from paramiko import SSHException

from .releases import PackageError
from .ssh import RemoteError, RemoteExitError, Session, on_omap
from .installed import installed_versions
from .i18n import tr
from .diagnostics import restart as diagnostic_restart
from .relay import RelaySettings


class UnsupportedHeadUnitError(RemoteError):
    pass


@dataclass
class Connection:
    host: str = '169.254.199.99'
    port: int = 22
    password: str = ''
    source_ip: str = ''

    def validate(self, *, allow_auto=False, require_password=True):
        if self.host or not allow_auto:
            try:
                ipaddress.ip_address(self.host)
            except ValueError:
                raise ValueError(tr('Неверный IP головного устройства.')) from None
        if not 1 <= self.port <= 65535:
            raise ValueError(tr('Неверный порт SSH.'))
        if require_password and not self.password:
            raise ValueError(tr('Введите пароль root.'))
        if self.source_ip:
            ipaddress.IPv4Address(self.source_ip)


def detect(session):
    try:
        output = session.run('uname -s; uname -n', stream=False).decode().splitlines()
    except RemoteExitError as exc:
        raise RemoteError(tr('После входа по SSH не удалось определить систему и узел ГУ. ') + str(exc)) from exc
    if len(output) != 2 or output[0] != 'QNX':
        raise RemoteError(tr('Ожидалось подключение к QNX на NBT Evo.'))
    node = output[1].split('.')[0]
    if node not in ('hu-omap', 'hu-jacinto'):
        raise RemoteError(tr('Неизвестный узел QNX: ') + output[1])
    try:
        layout = session.run(on_omap(node, 'if test -d /fs/sda0/opt/hmi/ID5; then echo ID5; '
                                     'else echo ID5_MISSING; fi'), stream=False).strip()
    except RemoteExitError as exc:
        raise RemoteError(tr('Вход по SSH выполнен на %s. Проверка каталога /fs/sda0/opt/hmi/ID5 '
                             'на hu-omap не прошла. ') % node + str(exc)) from exc
    if layout == b'ID5_MISSING':
        raise UnsupportedHeadUnitError(tr('На ГУ отсутствует каталог интерфейса ID5 (/fs/sda0/opt/hmi/ID5). '
                                          'Для установки NBT-AAD требуется NBT Evo с прошивкой ID5 или ID6. '
                                          'Штатная прошивка ID4 не поддерживается.'))
    if layout != b'ID5':
        raise RemoteError(tr('ГУ вернуло неожиданный ответ при проверке интерфейса ID5/ID6.'))
    try:
        credentials = session.run(on_omap(node, 'echo NBT_AAD_SHELL_PID=$$; pidin -p $$ -F "%a %W"'),
                                  stream=False).decode().splitlines()
    except RemoteExitError as exc:
        raise RemoteError(tr('Вход по SSH выполнен на %s. Не удалось проверить права на hu-omap через pidin. ')
                          % node + str(exc)) from exc
    marker = re.fullmatch(r'NBT_AAD_SHELL_PID=([0-9]+)', credentials[0]) if credentials else None
    if not marker:
        raise RemoteError(tr('Не удалось определить PID проверяемой оболочки ГУ.'))
    rows = [line.split() for line in credentials[1:] if line.split() and line.split()[0] == marker[1]]
    if not rows or any(len(row) != 2 or row[1] != '0' for row in rows):
        raise RemoteError(tr('Root-доступ на hu-omap не подтверждён: эффективный UID должен быть 0.'))
    return node


def restart(session, node, files_processed=False):
    # When called after installation, the commit has already been verified.
    # so distinguish a requested reset from a confirmed boot of the head unit.
    command = on_omap(node, '''
reset_bin=
for candidate in /opt/core/bin/OnOffDSICommander /opt/car/bin/OnOffDSICommander \\
                 /fs/sda0/opt/core/bin/OnOffDSICommander /fs/sda0/opt/car/bin/OnOffDSICommander; do
    if [ -f "$candidate" ] && [ -x "$candidate" ]; then
        reset_bin=$candidate
        break
    fi
done
if [ -z "$reset_bin" ]; then
    reset_bin=$(command -v OnOffDSICommander) || reset_bin=
    case "$reset_bin" in /*) ;; *) reset_bin= ;; esac
fi
if [ ! -f "$reset_bin" ] || [ ! -x "$reset_bin" ]; then
    echo 'OnOffDSICommander: executable unavailable on hu-omap.' >&2
    exit 127
fi
if command -v sync >/dev/null 2>&1; then sync || exit 1; fi
echo "OnOffDSICommander: $reset_bin"
echo NBT_AAD_RESTART_REQUESTED
LD_LIBRARY_PATH="/opt/sys/lib:/opt/core/lib:/opt/car/lib:/opt/os/lib:/opt/share/lib:/boot/lib:/lib:/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$reset_bin" appreset
'''.strip())
    seen = []
    original_log = session.log
    def collect(line):
        seen.append(line)
        original_log(line)
    session.log = collect
    try:
        session.run(command, timeout=30)
        return tr('Перезагрузка ГУ запрошена. Дождитесь появления штатного меню.')
    except RemoteExitError as exc:
        if not files_processed:
            raise RemoteError(tr('Команда перезагрузки завершилась ошибкой: ') + str(exc)) from exc
        return tr('Файлы обработаны успешно, но команда перезагрузки завершилась ошибкой: ') + str(exc)
    except Exception as exc:
        if 'NBT_AAD_RESTART_REQUESTED' in seen:
            return tr('Команда перезагрузки отправлена, SSH закрылся. Дождитесь загрузки ГУ.')
        if not files_processed:
            raise RemoteError(tr('Перезагрузку подтвердить не удалось: ') + str(exc)) from exc
        return tr('Файлы обработаны успешно, но перезагрузку подтвердить не удалось: ') + str(exc)
    finally:
        session.log = original_log


class Installer:
    def __init__(self, script, state, log, confirm, session_factory=Session, resetter=None):
        self.script, self.state = Path(script), Path(state)
        self.log, self.confirm, self.session_factory = log, confirm, session_factory
        self.resetter = resetter or (lambda connection: diagnostic_restart(
            RelaySettings(source_ip=connection.source_ip), self.log))

    def execute(self, action, connection, package=None, reboot=True, removal='keep'):
        if action not in ('install', 'uninstall', 'status', 'restart'):
            raise ValueError(tr('Неизвестная операция.'))
        connection.validate(allow_auto=action == 'restart', require_password=action != 'restart')
        if removal not in ('keep', 'purge'):
            raise ValueError(tr('Неизвестный режим удаления.'))
        if action == 'restart':
            return self.resetter(connection)
        script, files = b'', {}
        if action != 'restart':
            script = self.script.read_bytes()
            if b'--source PATH' not in script:
                raise PackageError(tr('Нужен установщик версии 0.3.0 или новее с поддержкой SSH.'))
            files['nbt-aad.sh'] = script
        if action == 'install':
            if package is None:
                raise PackageError(tr('Не выбран комплект NBT-AAD.'))
            files.update({name: file.read_bytes() for name, file in package.files().items()})
        if sum(map(len, files.values())) > 16 * 1024 * 1024:
            raise PackageError(tr('Комплект слишком велик для временной загрузки на ГУ.'))
        self.log(tr('Подключение по SSH к %s:%s…') % (connection.host, connection.port))
        kwargs = {'source_ip': connection.source_ip} if connection.source_ip else {}
        session = self.session_factory(connection.host, connection.port, connection.password,
                                       self.state / 'known_hosts', self.confirm, self.log, **kwargs)
        stage, node = None, None
        completed = False
        operation_started = False
        try:
            node = detect(session)
            self.log(tr('Проверка ГУ пройдена: ') + node)
            try:
                version = session.run(on_omap(node, 'cat /opt/sys/etc/nbt_version.txt'),
                                      timeout=5, stream=False, max_output=256).strip()
            except (RemoteError, SSHException):
                version = b''
            if re.fullmatch(rb'NBTevo_[a-z0-9][a-z0-9_]{0,63}', version, re.IGNORECASE):
                self.log(tr('Версия прошивки NBT Evo: ') + version.decode('ascii'))
            else:
                self.log(tr('Версию прошивки NBT Evo прочитать не удалось.'))
            if action == 'status':
                # No staging or writes to the HU in status mode.
                command = on_omap(node, '/bin/sh -s -- status')
                status = session.run(command, data=script).decode('utf-8', errors='replace')
                versions = installed_versions(session, node, self.state / 'packages', self.log)
                return versions + '\n\n' + status
            candidate = '/var/opt/nbt-aad-pc-' + uuid.uuid4().hex
            try:
                session.run(on_omap(node, 'umask 077; mkdir ' + shlex.quote(candidate)), stream=False)
            except RemoteExitError as exc:
                raise RemoteError(tr('Не удалось создать временный каталог в /var/opt на hu-omap. '
                                  'Установка и удаление ещё не запускались. ') + str(exc)) from exc
            stage = candidate
            if action == 'install':
                session.run(on_omap(node, 'umask 077; mkdir ' + shlex.quote(stage + '/release')),
                            stream=False)
            for index, (name, data) in enumerate(files.items(), 1):
                self.log(tr('Передача и проверка %s (%s/%s)') % (name, index, len(files)))
                session.upload(stage + '/' + name, data, node=node)
            command = '/bin/sh ' + shlex.quote(stage + '/nbt-aad.sh') + ' ' + action + ' --source ' + shlex.quote(stage)
            if (action == 'install' and 'pf_aa' not in files and b'--pf-embedded' in script
                    and b'(built into nbt-aad): Android Auto' in files['nbt-aad']):
                command += ' --pf-embedded'
            if action == 'uninstall':
                command += ' --keep-config' if removal == 'keep' else ' --purge'
            self.log(tr('Проверка установки без изменений…'))
            validation = session.run(on_omap(node, command + ' --dry-run'))
            if b'DRY RUN: validation passed' not in validation and b'Already uninstalled.' not in validation:
                raise RemoteError(tr('Установщик не подтвердил готовность к операции.'))
            self.log(tr('Выполнение операции… Не отключайте питание и ENET.'))
            operation_started = True
            result = session.run(on_omap(node, command))
            marker = ('SUCCESS: ' + action + ' completed.').encode()
            if marker not in result and not (action == 'uninstall' and b'Already uninstalled.' in result):
                raise RemoteError(tr('Нет подтверждения успешной операции. Проверьте журнал.'))
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
            self.log(tr('Результат на ГУ проверен. ') + (tr('Резервные копии удалены.')
                     if action == 'uninstall' and removal == 'purge' else tr('Резервные копии остаются на ГУ.')))
            # Delete only this run's private upload, before a reset can terminate SSH.
            try:
                self.cleanup(session, node, stage)
            except Exception as exc:
                self.log(tr('Файлы установлены/удалены, но временную загрузку очистить не удалось: ') + str(exc))
            stage = None
            message = (tr('Установка / обновление завершены.') if action == 'install' else
                       tr('NBT-AAD удалён. ') + (tr('Настройки сохранены на ГУ.') if removal == 'keep' else
                       tr('Настройки, резервные копии и временные файлы удалены.')))
            if reboot:
                try:
                    message += '\n' + self.resetter(connection)
                except Exception as exc:
                    message += '\n' + tr('Файлы обработаны успешно, но перезагрузку подтвердить не удалось: ') + str(exc)
            else:
                message += tr('\nПерезагрузка отключена в настройках.')
            return message
        finally:
            if not completed and action in ('install', 'uninstall'):
                if not operation_started:
                    self.log(tr('Сбой до запуска установки или удаления.'))
                elif action == 'uninstall' and removal == 'purge':
                    self.log(tr('Полная очистка не подтверждена. Часть файлов и копий могла быть уже удалена.'))
                if stage:
                    self.log(tr('Временный каталог этой операции на hu-omap: ') + stage)
            session.close()

    @staticmethod
    def cleanup(session, node, stage):
        if not re.fullmatch(r'/var/opt/nbt-aad-pc-[0-9a-f]{32}', stage):
            raise RemoteError(tr('Недопустимый путь очистки.'))
        session.run(on_omap(node, 'rm -rf ' + shlex.quote(stage)), stream=False)
