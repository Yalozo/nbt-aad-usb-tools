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
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    matches = []
    for package in list_local_packages(cache, log):
        if digests == package.metadata['files']:
            release = package.metadata['release']
            matches.append(package.label + ' [' + release['tag_name'] + ']')
    if matches:
        lines.append('Комплект совпадает по SHA-256 всех 6 файлов: ' + '; '.join(dict.fromkeys(matches)))
    elif any(payloads.values()):
        lines.append('Точного совпадения со скачанными комплектами нет. '
                     'Номера выше прочитаны из файлов; соответствие одному релизу не подтверждено.')
    else:
        lines.append('NBT-AAD не установлен.')
    return '\n'.join(lines)
