"""Download matched release assets and verify them before touching the head unit."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import ssl
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile
import certifi

REPO = 'Sq00ky/Supra-iDrive6-Android-Auto'
API = f'https://api.github.com/repos/{REPO}/releases?per_page=100'
FILES = ('libNbtAad.so', 'NbtAad.manifest', 'NbtAad.xml',
         'NbtAad_ALL_Texts.zip', 'NbtAad_ALL_Images.zip')
MAX_ASSET = 32 * 1024 * 1024


class PackageError(Exception):
    pass


def _https(url):
    parts = urlparse(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username:
        raise PackageError('Недопустимый адрес скачивания.')
    return url


def tls_context():
    context = ssl.create_default_context()
    # A packaged Mac app must not depend on the build machine's Python CA path.
    # Keep system roots when available and add the bundled public CA store.
    context.load_verify_locations(cafile=certifi.where())
    return context


def fetch(url, limit=MAX_ASSET):
    request = Request(_https(url), headers={'User-Agent': 'NBT-AAD-PC/0.1',
                                           'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=40, context=tls_context()) as response:
        _https(response.url)
        data = response.read(limit + 1)
    if len(data) > limit:
        raise PackageError('Размер загружаемого файла превышает ограничение.')
    return data


def is_preview(release):
    return bool(release.get('prerelease') or
                re.search(r'(?i)(alpha|beta|preview|unstable|(?:^|[\W_])(?:rc\d*|test(?:ing)?)(?:$|[\W_]))',
                          (release.get('tag_name') or '') + ' ' + (release.get('name') or '')))


def select_assets(release):
    assets = release.get('assets', [])
    daemon = [x for x in assets if x.get('name') == 'nbt-aad']
    plugin = [x for x in assets if re.fullmatch(r'releases?\.zip', x.get('name', ''), re.I)]
    helper = [x for x in assets if x.get('name') == 'pf_aa']
    if len(daemon) != 1 or len(plugin) != 1:
        raise PackageError('Релиз должен содержать один nbt-aad и один release(s).zip.')
    if len(helper) > 1:
        raise PackageError('В релизе несколько файлов pf_aa.')
    for asset in daemon + plugin + helper:
        if not 0 < asset.get('size', 0) <= MAX_ASSET:
            raise PackageError('Недопустимый размер файла релиза.')
        url = _https(asset.get('browser_download_url', ''))
        if not url.startswith(f'https://github.com/{REPO}/releases/download/'):
            raise PackageError('Файл находится за пределами официального проекта.')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or ''):
            raise PackageError('GitHub не предоставил SHA-256 файла. Автоустановка остановлена.')
    return (daemon[0], plugin[0], *helper)


def list_releases(include_preview=False, downloader=fetch):
    releases = json.loads(downloader(API, 4 * 1024 * 1024))
    if not isinstance(releases, list):
        raise PackageError('Не удалось получить список релизов GitHub.')
    valid = []
    for release in releases:
        if release.get('draft') or (is_preview(release) and not include_preview):
            continue
        try:
            select_assets(release)
            datetime.fromisoformat(release['published_at'].replace('Z', '+00:00'))
        except (PackageError, KeyError, ValueError):
            continue
        valid.append(release)
    return sorted(valid, key=lambda r: r['published_at'], reverse=True)


def validate_elf(data):
    if (len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01' or
            data[16:18] not in (b'\x02\0', b'\x03\0') or data[18:20] != b'\x28\0' or
            data[40:42] != b'\x34\0'):
        raise PackageError('Файл не является ARM ELF32 для NBT Evo.')


def unpack_plugin(data):
    """Allowlist members; never extract untrusted paths or unpack inner ZIPs."""
    found = {}
    parents = set()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > 100:
            raise PackageError('Слишком много файлов в архиве плагина.')
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in member.filename or
                    ':' in member.filename or stat.S_ISLNK(member.external_attr >> 16)):
                raise PackageError('Небезопасный путь в архиве плагина.')
            if member.is_dir():
                continue
            if path.name not in FILES:
                # Ignore release notes, but reject additional binaries/config layouts.
                if path.suffix.lower() not in ('.txt', '.md'):
                    raise PackageError('Неизвестный состав плагина: ' + member.filename)
                continue
            if path.name in found or not 0 < member.file_size <= MAX_ASSET:
                raise PackageError('Повторяющийся или слишком большой файл плагина.')
            found[path.name] = archive.read(member)
            parents.add(path.parent)
    if set(found) != set(FILES) or len(parents) != 1:
        raise PackageError('В архиве нет единого полного комплекта из пяти файлов плагина.')
    validate_elf(found['libNbtAad.so'])
    for name in FILES[-2:]:
        with zipfile.ZipFile(io.BytesIO(found[name])) as archive:
            if sum(x.file_size for x in archive.infolist()) > MAX_ASSET:
                raise PackageError('Архив ресурсов слишком большой.')
            if archive.testzip() is not None:
                raise PackageError('Повреждён архив ресурсов: ' + name)
    for name in ('NbtAad.manifest', 'NbtAad.xml'):
        if not found[name].strip():
            raise PackageError('Пустой файл ' + name)
    return found


@dataclass
class Package:
    path: Path
    metadata: dict

    @property
    def label(self):
        r = self.metadata['release']
        return r.get('name') or r['tag_name']

    def files(self):
        files = {'nbt-aad': self.path / 'nbt-aad',
                 **{'release/' + name: self.path / 'release' / name for name in FILES}}
        if isinstance(self.metadata.get('files'), dict) and 'pf_aa' in self.metadata['files']:
            files['pf_aa'] = self.path / 'pf_aa'
        return files


def load_package(path):
    path = Path(path)
    metadata = json.loads((path / 'package.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict):
        raise PackageError('Некорректное описание сохранённого комплекта.')
    release = metadata.get('release')
    if (not isinstance(release, dict) or not isinstance(release.get('tag_name'), str)
            or not release['tag_name'] or
            any(release.get(key) is not None and not isinstance(release[key], str)
                for key in ('name', 'published_at')) or
            (metadata.get('downloaded_at') is not None and not isinstance(metadata['downloaded_at'], str))):
        raise PackageError('В комплекте нет сведений о релизе.')
    package = Package(path, metadata)
    files = package.files()
    if not isinstance(metadata.get('files'), dict) or set(metadata['files']) != set(files):
        raise PackageError('Сохранённый комплект неполон.')
    for name, file in files.items():
        if file.is_symlink() or file.stat().st_size > MAX_ASSET:
            raise PackageError('Недопустимый файл в кэше: ' + name)
        if hashlib.sha256(file.read_bytes()).hexdigest() != metadata['files'][name]:
            raise PackageError('Сохранённый файл изменился: ' + name)
    validate_elf(files['nbt-aad'].read_bytes())
    validate_elf(files['release/libNbtAad.so'].read_bytes())
    if 'pf_aa' in files:
        validate_elf(files['pf_aa'].read_bytes())
    return package


def download_package(release, cache, log=lambda _: None, downloader=fetch):
    assets = select_assets(release)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='download-', dir=cache))
    try:
        payloads = []
        for asset in assets:
            log('Скачивание ' + asset['name'])
            data = downloader(asset['browser_download_url'], MAX_ASSET)
            if len(data) != asset['size'] or 'sha256:' + hashlib.sha256(data).hexdigest() != asset['digest']:
                raise PackageError('Не совпали размер или SHA-256: ' + asset['name'])
            payloads.append(data)
        validate_elf(payloads[0])
        plugin = unpack_plugin(payloads[1])
        (work / 'release').mkdir()
        (work / 'nbt-aad').write_bytes(payloads[0])
        helper_files = []
        if len(payloads) == 3:
            validate_elf(payloads[2])
            (work / 'pf_aa').write_bytes(payloads[2])
            helper_files.append(work / 'pf_aa')
        for name, data in plugin.items():
            (work / 'release' / name).write_bytes(data)
        metadata = {'schema': 2 if helper_files else 1, 'downloaded_at': datetime.now(timezone.utc).isoformat(),
                    'release': {k: release.get(k) for k in
                    ('id', 'name', 'tag_name', 'published_at', 'html_url', 'prerelease')},
                    'assets': [{k: a[k] for k in ('name', 'digest', 'size')} for a in assets],
                    'files': {file.relative_to(work).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
                              for file in [work / 'nbt-aad', *helper_files, *sorted((work / 'release').iterdir())]}}
        (work / 'package.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        # Content-addressed packages survive moving upstream tags and interrupted downloads.
        identifier = hashlib.sha256(''.join(a['digest'] for a in assets).encode()).hexdigest()[:24]
        destination = cache / identifier
        if destination.exists():
            load_package(destination)
        else:
            work.rename(destination)
        pointer = cache / 'last.json.tmp'
        pointer.write_text(json.dumps({'package': identifier}), encoding='utf-8')
        pointer.replace(cache / 'last.json')
        return load_package(destination)
    finally:
        if work.exists():
            shutil.rmtree(work)


def cached_package(cache):
    cache = Path(cache)
    identifier = json.loads((cache / 'last.json').read_text(encoding='utf-8'))['package']
    if not re.fullmatch(r'[0-9a-f]{24}', identifier):
        raise PackageError('Некорректный указатель на сохранённый комплект.')
    return load_package(cache / identifier)


def local_package(cache, identifier):
    """Load exactly the selected local build, never consult last.json or the network."""
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{24}', identifier):
        raise PackageError('Выберите сохранённый комплект из списка.')
    path = Path(cache) / identifier
    if path.is_symlink():
        raise PackageError('Ссылка вместо сохранённого комплекта недопустима.')
    return load_package(path)


def list_local_packages(cache, on_error=lambda _: None):
    """Keep all content-addressed builds, including caches from older app versions."""
    cache = Path(cache)
    if not cache.exists():
        return []
    packages = []
    try:
        candidates = list(cache.iterdir())
    except OSError as exc:
        on_error('Не удалось прочитать список сохранённых комплектов: ' + str(exc))
        return []
    for path in candidates:
        if not re.fullmatch(r'[0-9a-f]{24}', path.name):
            continue
        try:
            packages.append(local_package(cache, path.name))
        except (OSError, ValueError, KeyError, TypeError, PackageError) as exc:
            on_error('Пропущен повреждённый комплект ' + path.name + ': ' + str(exc))
    return sorted(packages, key=lambda p: (p.metadata.get('downloaded_at') or
                  p.metadata['release'].get('published_at') or '', p.path.name), reverse=True)
