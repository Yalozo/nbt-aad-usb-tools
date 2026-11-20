"""Read installed files, never execute the daemon to ask for its version."""
import hashlib
import re
import shlex
from paramiko import SSHException
from .releases import FILES, MAX_ASSET, list_local_packages, validate_elf, PackageError
from .ssh import on_omap, RemoteError, RemoteExitError
from .i18n import tr


def embedded_version(data, plugin=False):
    # These are the literal build banners in upstream main.cpp/nbt_aad_app.cpp.
    # Reject ambiguous banners instead of selecting a random version string.
    pattern = (rb'(?<![A-Za-z0-9_-])(rhmiapp-[0-9]+[A-Za-z0-9_.-]*)(?=[ \x00(])' if plugin else
               rb'Build: (v[0-9]+(?:\.[0-9]+){1,3}[A-Za-z0-9_.-]*)(?=[ \x00(])')
    versions = sorted({x.decode('ascii') for x in re.findall(pattern, data) if len(x) <= 100})
    return versions[0] if len(versions) == 1 else None


def pf_status(session, node, daemon, helper):
    embedded = not helper
    data = daemon if embedded else helper
    pattern = (rb'pf_aa ([0-9]+(?:\.[0-9]+)+) \(built into nbt-aad\): Android Auto'
               if embedded else rb'pf_aa ([0-9]+(?:\.[0-9]+)+):')
    versions = {version.decode('ascii') for version in re.findall(pattern, data)}
    if not data or (embedded and len(versions) != 1):
        return [tr('PF: утилита настройки в установленном комплекте не обнаружена.')]
    try:
        validate_elf(data)
    except PackageError:
        return [tr('PF: файл утилиты не является корректным ARM ELF32.')]
    version = next(iter(versions)) if len(versions) == 1 else tr('версия не определена')
    lines = [tr('pf_aa встроен в nbt-aad: %s.') % version if embedded
             else tr('Утилита pf_aa: ') + version + '.']
    if len(versions) != 1:
        return lines
    command = '/opt/aa/nbt-aad --pf-check' if embedded else '/opt/aa/pf_aa --check'
    code = 0
    try:
        output = session.run(on_omap(node, command), stream=False, timeout=30, max_output=1024 * 1024)
        if output.strip():
            lines.append(output.decode('utf-8', errors='replace').strip())
    except RemoteExitError as exc:
        code = exc.code
        lines.append(str(exc))
    except (RemoteError, OSError, SSHException) as exc:
        lines.append(tr('Не удалось проверить PF. ') + str(exc))
        return lines
    if code == 0:
        lines.append(tr('Проверка PF пройдена.'))
    elif code == 9:
        lines.append(tr('Файлы PF проверены. Правила сейчас не активны; требуется перезагрузка ГУ.'))
    elif code == 7:
        lines.append(tr('Проверка PF выявила неполную настройку. Подробности выше.'))
    else:
        lines.append(tr('Проверка PF завершилась с кодом %s.') % code)
    return lines


def installed_versions(session, node, cache, log):
    paths = {'nbt-aad': '/opt/aa/nbt-aad', **{
        'release/' + name: '/opt/hmi/ID5/data/ro/Plugins/NbtAad/' + name for name in FILES}}
    paths['pf_aa'] = '/opt/aa/pf_aa'
    payloads = {}
    for name, path in paths.items():
        quoted = shlex.quote(path)
        # No symlink traversal and no creation of remote scratch files.
        command = 'if test -f ' + quoted + ' && test ! -L ' + quoted + '; then cat ' + quoted + '; fi'
        payloads[name] = session.run(on_omap(node, command), stream=False, max_output=MAX_ASSET, timeout=120)
    lines = []
    for name, title, plugin in [('nbt-aad', tr('Установленный NBT-AAD'), False),
                                 ('release/libNbtAad.so', tr('Установленный плагин'), True)]:
        data = payloads[name]
        if not data:
            lines.append(title + tr(': отсутствует или недоступен.'))
            continue
        try:
            validate_elf(data)
        except PackageError:
            lines.append(title + tr(': файл не является корректным ARM ELF32.'))
            continue
        version = embedded_version(data, plugin)
        lines.append(title + ': ' + (version or tr('номер сборки не найден в файле')) + '.')
    lines.extend(pf_status(session, node, payloads['nbt-aad'], payloads['pf_aa']))
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    matches = []
    matched_count = 0
    for package in list_local_packages(cache, log):
        expected = package.metadata['files']
        if {name: digests[name] for name in expected} == expected:
            if len(expected) > matched_count:
                matches = []
                matched_count = len(expected)
            if len(expected) < matched_count:
                continue
            release = package.metadata['release']
            matches.append(package.label + ' [' + release['tag_name'] + '] (' + str(len(expected)) + tr(' файлов)'))
    if matches:
        lines.append(tr('Комплект совпадает по SHA-256 всех ') + str(matched_count) + tr(' файлов: ') + '; '.join(dict.fromkeys(matches)))
    elif any(payloads.values()):
        lines.append(tr('Точного совпадения со скачанными комплектами нет. '
                     'Номера выше прочитаны из файлов; соответствие одному релизу не подтверждено.'))
    else:
        lines.append(tr('Нет данных для определения версии NBT-AAD.'))
    return '\n'.join(lines)
