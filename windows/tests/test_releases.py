import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
from nbt_aad_pc import releases as r


def elf():
    data = bytearray(64)
    data[:7] = b'\x7fELF\x01\x01\x01'
    struct.pack_into('<HHI', data, 16, 3, 40, 1)
    struct.pack_into('<H', data, 40, 52)
    return bytes(data)


def zip_bytes(items):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, data in items.items():
            z.writestr(name, data)
    return out.getvalue()


def resources():
    return zip_bytes({'resource': b'hello'})


def plugin():
    return {'libNbtAad.so': elf(), 'NbtAad.manifest': b'<manifest/>',
            'NbtAad.xml': b'<app/>', **{name: resources() for name in r.FILES[-2:]}}


def release(tag='Installer', date='2026-11-03T10:00:00Z', preview=False):
    payloads = [elf(), zip_bytes({'Release/' + name: data for name, data in plugin().items()})]
    assets = [{'name': name, 'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
               'browser_download_url': f'https://github.com/{r.REPO}/releases/download/{tag}/{name}'}
              for name, data in zip(('nbt-aad', 'Releases.zip'), payloads)]
    return {'id': 1, 'tag_name': tag, 'name': tag + ' 1.8.72', 'published_at': date,
            'prerelease': preview, 'draft': False, 'html_url': f'https://github.com/{r.REPO}/releases/tag/{tag}',
            'assets': assets}, dict(zip((a['browser_download_url'] for a in assets), payloads))


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.release, self.payloads = release()

    def download(self, url, limit):
        return self.payloads[url]

    def test_download_verify_cache_and_preserve_inner_zips(self):
        package = r.download_package(self.release, self.cache, downloader=self.download)
        self.assertEqual(package.label, 'Installer 1.8.72')
        self.assertEqual(package.files()['release/NbtAad_ALL_Texts.zip'].read_bytes(), resources())
        self.assertEqual(r.cached_package(self.cache).path, package.path)
        self.assertEqual(len(list(self.cache.glob('download-*'))), 0)

    def test_cached_file_corruption_is_detected(self):
        package = r.download_package(self.release, self.cache, downloader=self.download)
        package.files()['nbt-aad'].write_bytes(b'bad')
        with self.assertRaises(r.PackageError):
            r.cached_package(self.cache)

    def test_bad_digest_stops_before_publishing_package(self):
        self.release['assets'][0]['digest'] = 'sha256:' + 'a' * 64
        with self.assertRaises(r.PackageError):
            r.download_package(self.release, self.cache, downloader=self.download)
        self.assertFalse((self.cache / 'last.json').exists())
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_changed_tag_creates_different_content_addressed_cache(self):
        first = r.download_package(self.release, self.cache, downloader=self.download)
        url = self.release['assets'][0]['browser_download_url']
        self.payloads[url] += b'new version same tag'
        self.release['assets'][0].update(size=len(self.payloads[url]),
            digest='sha256:' + hashlib.sha256(self.payloads[url]).hexdigest())
        second = r.download_package(self.release, self.cache, downloader=self.download)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.files()['nbt-aad'].read_bytes(), elf())

    def test_selection_ignores_beta_tag_even_if_not_prerelease(self):
        beta, _ = release('beta3', '2026-11-04T10:00:00Z')
        old, _ = release('older', '2026-11-01T10:00:00Z')
        downloader = lambda *args: json.dumps([old, beta, self.release]).encode()
        self.assertEqual(r.list_releases(downloader=downloader)[0]['tag_name'], 'Installer')
        self.assertEqual(r.list_releases(True, downloader=downloader)[0]['tag_name'], 'beta3')

    def test_draft_prerelease_and_incomplete_are_skipped(self):
        draft, _ = release('draft'); draft['draft'] = True
        preview, _ = release('experiment', preview=True)
        broken, _ = release('broken'); broken['assets'].pop()
        downloader = lambda *args: json.dumps([draft, preview, broken, self.release]).encode()
        self.assertEqual(len(r.list_releases(downloader=downloader)), 1)

    def test_unsafe_zip_paths_are_rejected(self):
        for name in ('../escape', '/absolute', 'C:/file', 'Release\\evil'):
            with self.subTest(name=name), self.assertRaises(r.PackageError):
                r.unpack_plugin(zip_bytes({name: b'bad', **plugin()}))

    def test_mixed_plugin_folders_and_missing_file_are_rejected(self):
        members = plugin()
        members['Other/libNbtAad.so'] = members.pop('libNbtAad.so')
        with self.assertRaises(r.PackageError):
            r.unpack_plugin(zip_bytes(members))
        members.pop('NbtAad.xml')
        with self.assertRaises(r.PackageError):
            r.unpack_plugin(zip_bytes(members))

    def test_bad_elf_and_inner_zip_are_rejected(self):
        for key, value in [('libNbtAad.so', b'not ARM'), ('NbtAad_ALL_Texts.zip', b'PK\x03\x04bad')]:
            members = plugin(); members[key] = value
            with self.subTest(key=key), self.assertRaises((r.PackageError, zipfile.BadZipFile)):
                r.unpack_plugin(zip_bytes(members))

    def test_missing_digest_foreign_host_or_huge_asset_are_rejected(self):
        for key, value in [('digest', None), ('size', r.MAX_ASSET+1),
                           ('browser_download_url', 'http://github.com/unsafe'),
                           ('browser_download_url', 'https://example.com/payload')]:
            rel, _ = release(); rel['assets'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(r.PackageError):
                r.select_assets(rel)

    def test_pointer_path_traversal_is_rejected(self):
        (self.cache / 'last.json').write_text(json.dumps({'package': '../something'}))
        with self.assertRaises(r.PackageError):
            r.cached_package(self.cache)

    def test_https_download_requirement(self):
        for url in ('http://example.com', 'file:///etc/passwd', 'https://user@example.com/a'):
            with self.subTest(url=url), self.assertRaises(r.PackageError):
                r.fetch(url)
