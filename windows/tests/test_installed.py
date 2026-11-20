from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nbt_aad_pc.installed import embedded_version, installed_versions
from nbt_aad_pc.releases import download_package
from nbt_aad_pc.ssh import RemoteExitError
from test_releases import release, elf


class InstalledVersionTest(unittest.TestCase):
    def test_build_banners_not_random_version_or_ambiguous_strings(self):
        data = elf() + b'\0Build: v1.8.66-kombi-bt (%s %s)\n\0unrelated v9.9.9'
        self.assertEqual(embedded_version(data), 'v1.8.66-kombi-bt')
        self.assertIsNone(embedded_version(data + b'\0Build: v1.9.1 (%s)'))
        self.assertIsNone(embedded_version(b'v1.8.66'))
        self.assertEqual(embedded_version(elf() + b'\0rhmiapp-24b-credits (Nov 4)\0', True),
                         'rhmiapp-24b-credits')

    def test_offline_match_reads_all_six_files_on_omap_without_running_them(self):
        with tempfile.TemporaryDirectory() as temp:
            metadata, payloads = release('manual-build', '2026-11-04T10:00:00Z')
            package = download_package(metadata, Path(temp), downloader=lambda url, limit: payloads[url])
            files = {name: path.read_bytes() for name, path in package.files().items()}
            calls = []
            class Reader:
                def run(self, command, **kwargs):
                    calls.append(command)
                    if '/pf_aa;' in command:
                        return files.get('pf_aa', b'')
                    for name, data in files.items():
                        if '/' + name.split('/')[-1] + ';' in command:
                            return data
                    raise AssertionError(command)
            with patch('nbt_aad_pc.releases.fetch', side_effect=AssertionError('network')):
                result = installed_versions(Reader(), 'hu-jacinto', Path(temp), lambda _: None)
                self.assertIn('SHA-256 всех 6 файлов', result)
                self.assertIn('manual-build', result)
                files['release/NbtAad.xml'] += b'changed'
                changed = installed_versions(Reader(), 'hu-jacinto', Path(temp), lambda _: None)
                self.assertNotIn('Комплект совпадает', changed)
            self.assertTrue(all(c.startswith('on -f /net/hu-omap /bin/sh -c ') for c in calls))
            self.assertTrue(all('/net/hu-omap/' not in c for c in calls))
            self.assertTrue(all('cat ' in c and '--version' not in c and 'mkdir' not in c for c in calls))

    def test_status_matches_helper_hash_reports_version_and_checks_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            rel, payloads = release(helper=True)
            package = download_package(rel, Path(temp), downloader=lambda url, limit: payloads[url])
            data = {name.split('/')[-1]: path.read_bytes() for name, path in package.files().items()}
            class Reader:
                def run(self, command, **kwargs):
                    if command == '/opt/aa/pf_aa --check': return b'PF configuration verified'
                    for name, content in data.items():
                        if '/' + name + ';' in command: return content
                    raise AssertionError(command)
            result = installed_versions(Reader(), 'hu-omap', Path(temp), lambda _: None)
            self.assertIn('pf_aa: 1.1', result)
            self.assertIn('SHA-256 всех 7 файлов', result)
            data['pf_aa'] += b'changed'
            changed = installed_versions(Reader(), 'hu-omap', Path(temp), lambda _: None)
            self.assertNotIn('Комплект совпадает', changed)

    def test_status_recognizes_embedded_pf_and_checks_it_on_omap(self):
        for code in (0, 9, 7):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                commands = []
                class Reader:
                    def run(self, command, **kwargs):
                        commands.append(command)
                        if '--pf-check' in command:
                            if code:
                                raise RemoteExitError(code, 'PF check details')
                            return b'rules look good and are active'
                        if '/nbt-aad;' in command:
                            return elf() + b'pf_aa 1.3 (built into nbt-aad): Android Auto'
                        return b''
                result = installed_versions(Reader(), 'hu-jacinto', Path(temp), lambda _: None)
                self.assertIn('pf_aa встроен в nbt-aad: 1.3', result)
                expected = {0: 'Проверка PF пройдена', 9: 'Правила сейчас не активны', 7: 'неполную настройку'}
                self.assertIn(expected[code], result)
                self.assertEqual(sum('--pf-check' in command for command in commands), 1)
                self.assertTrue(all(command.startswith('on -f /net/hu-omap /bin/sh -c ') for command in commands))
                self.assertFalse(any('--pf-install' in command for command in commands))

    def test_embedded_banner_in_an_invalid_executable_is_not_executed(self):
        commands = []
        class Reader:
            def run(self, command, **kwargs):
                commands.append(command)
                if '/nbt-aad;' in command:
                    return b'pf_aa 1.3 (built into nbt-aad): Android Auto'
                return b''
        with tempfile.TemporaryDirectory() as temp:
            result = installed_versions(Reader(), 'hu-omap', Path(temp), lambda _: None)
        self.assertIn('PF: файл утилиты не является корректным ARM ELF32', result)
        self.assertFalse(any('--pf-check' in command for command in commands))

    def test_absent_install_does_not_guess_latest_or_stale_logs(self):
        class Empty:
            def run(self, *args, **kwargs):
                return b''
        with tempfile.TemporaryDirectory() as temp:
            result = installed_versions(Empty(), 'hu-omap', Path(temp), lambda _: None)
        self.assertIn('Нет данных для определения версии NBT-AAD', result)
        self.assertNotIn('v1.', result)
