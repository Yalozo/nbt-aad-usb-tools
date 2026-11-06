"""Read installed files, never execute the daemon to ask for its version."""
import hashlib
import re
import shlex
from .releases import FILES, MAX_ASSET, list_local_packages, validate_elf, PackageError


def embedded_version(data, plugin=False):
    # These are the literal build banners in upstream main.cpp/nbt_aad_app.cpp.
    # Reject ambiguous banners instead of selecting a random version string.
    pattern = (rb'(?<![A-Za-z0-9_-])(rhmiapp-[0-9]+[A-Za-z0-9_.-]*)(?=[ \x00(])' if plugin else
               rb'Build: (v[0-9]+(?:\.[0-9]+){1,3}[A-Za-z0-9_.-]*)(?=[ \x00(])')
    versions = sorted({x.decode('ascii') for x in re.findall(pattern, data) if len(x) <= 100})
    return versions[0] if len(versions) == 1 else None


def installed_versions(session, node, cache, log):
    prefix = '/net/hu-omap' if node == 'hu-jacinto' else ''
    paths = {'nbt-aad': '/opt/aa/nbt-aad', **{
        'release/' + name: '/opt/hmi/ID5/data/ro/Plugins/NbtAad/' + name for name in FILES}}
    paths['pf_aa'] = '/opt/aa/pf_aa'
    payloads = {}
    for name, path in paths.items():
        quoted = shlex.quote(prefix + path)
        # No symlink traversal and no creation of remote scratch files.
        command = 'if test -f ' + quoted + ' && test ! -L ' + quoted + '; then cat ' + quoted + '; fi'
        payloads[name] = session.run(command, stream=False, max_output=MAX_ASSET, timeout=120)
    lines = []
    for name, title, plugin in [('nbt-aad', 'Установленный NBT-AAD', False),
                                 ('release/libNbtAad.so', 'Установленный плагин', True)]:
        data = payloads[name]
        if not data:
            lines.append(title + ': отсутствует или недоступен.')
            continue
        try:
            validate_elf(data)
        except PackageError:
            lines.append(title + ': файл не является корректным ARM ELF32.')
            continue
        version = embedded_version(data, plugin)
        lines.append(title + ': ' + (version or 'номер сборки не найден в файле') + '.')
    helper = payloads['pf_aa']
    if helper:
        try:
            validate_elf(helper)
            versions = {v.decode('ascii') for v in re.findall(rb'pf_aa ([0-9]+(?:\.[0-9]+)+):', helper)}
            version = next(iter(versions)) if len(versions) == 1 else 'версия не определена'
            lines.append('Утилита pf_aa: ' + version + ' (наличие файла не означает применение правил).')
        except PackageError:
            lines.append('Утилита pf_aa: файл не является корректным ARM ELF32.')
    else:
        lines.append('Утилита pf_aa: отсутствует (в старых релизах её может не быть).')
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
            matches.append(package.label + ' [' + release['tag_name'] + '] (' + str(len(expected)) + ' файлов)')
    if matches:
        lines.append('Комплект совпадает по SHA-256 всех ' + str(matched_count) + ' файлов: ' + '; '.join(dict.fromkeys(matches)))
    elif any(payloads.values()):
        lines.append('Точного совпадения со скачанными комплектами нет. '
                     'Номера выше прочитаны из файлов; соответствие одному релизу не подтверждено.')
    else:
        lines.append('NBT-AAD не установлен.')
    return '\n'.join(lines)
