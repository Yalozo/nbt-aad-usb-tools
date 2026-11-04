import json
from pathlib import Path
import sys
import tempfile
import unittest

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
