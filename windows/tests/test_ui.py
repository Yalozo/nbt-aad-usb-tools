import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

@unittest.skipUnless(sys.platform == 'win32', 'Native UI smoke test runs on Windows')
class WindowsUITest(unittest.TestCase):
    def test_window_constructs_and_password_is_not_saved(self):
        import tkinter as tk
        from nbt_aad_pc.ui import App
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = App(root, state=directory)
            app.password.set('unique-secret-password')
            app.snapshot()
            app.write('unique-secret-password test redaction')
            root.update()
            self.assertEqual(app.host.get(), '169.254.199.99')
            self.assertTrue(app.reboot.get())
            self.assertFalse(app.preview.get())
            config = (Path(directory) / 'settings.json').read_text(encoding='utf-8')
            self.assertNotIn('password', config)
            self.assertEqual(json.loads(config)['port'], 22)
            self.assertNotIn('unique-secret-password', app.logfile.read_text(encoding='utf-8'))
            app.close()

    def test_offline_library_selection_persists_and_restart_does_not_download(self):
        import hashlib
        import tkinter as tk
        from nbt_aad_pc.ui import App
        from nbt_aad_pc.releases import download_package, PackageError
        from test_releases import release
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'packages'
            older, payloads = release('older', '2026-11-01T10:00:00Z')
            first = download_package(older, cache, downloader=lambda url, limit: payloads[url])
            newer, data = release('newer', '2026-11-04T10:00:00Z')
            url = newer['assets'][0]['browser_download_url']
            data[url] += b'newer build'
            newer['assets'][0].update(size=len(data[url]),
                digest='sha256:' + hashlib.sha256(data[url]).hexdigest())
            download_package(newer, cache, downloader=lambda url, limit: data[url])
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                self.assertEqual(len(app.local_packages), 2)
                index = next(i for i, p in enumerate(app.local_packages) if p.path == first.path)
                app.local_release.current(index)
                app.offline.set(True)
                app.offline_changed()
                self.assertEqual(str(app.local_release.cget('state')), 'readonly')
                self.assertEqual(str(app.release.cget('state')), 'disabled')
                with patch('nbt_aad_pc.ui.list_releases', side_effect=AssertionError('network')), \
                     patch('nbt_aad_pc.ui.download_package', side_effect=AssertionError('download')):
                    self.assertEqual(app.get_package(True, False, None, first.path.name).path, first.path)
                    with self.assertRaises(PackageError):
                        app.get_package(True, False, None)
                with patch('nbt_aad_pc.ui.Installer') as installer, \
                     patch.object(app, 'get_package', side_effect=AssertionError('restart must not load package')), \
                     patch.object(app, 'start', side_effect=lambda title, work: work()):
                    app.restart_button.invoke()
                    args = installer.return_value.execute.call_args.args
                    self.assertEqual(args[0], 'restart')
                    self.assertIsNone(args[2])
            finally:
                app.close()
            root = tk.Tk(); root.withdraw()
            restored = App(root, state=directory)
            try:
                self.assertTrue(restored.offline.get())
                self.assertEqual(restored.selected_local_identifier(), first.path.name)
                # A corrupted chosen build must not fall back to the newer one.
                first.files()['nbt-aad'].write_bytes(b'corrupted')
                with self.assertRaises(PackageError):
                    restored.get_package(True, False, None, first.path.name)
                restored.update_cache_label()
                self.assertIsNone(restored.selected_local_identifier())
            finally:
                restored.close()
