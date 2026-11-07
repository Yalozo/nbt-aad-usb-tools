import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

@unittest.skipUnless(sys.platform in ('win32', 'darwin') and importlib.util.find_spec('_tkinter'),
                     'Native UI tests require a Tk-enabled Python on Windows/macOS')
class NativeUITest(unittest.TestCase):
    def test_language_switch_persists_and_footer_fits_at_minimum_height(self):
        import tkinter as tk
        from nbt_aad_pc.ui import App
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = App(root, state=directory)
            # Test native Tk allocation without the CI window manager.
            app.layout.pack_forget()

            def resize(width, height):
                app.layout.place(x=0, y=0, width=width, height=height)
                root.update_idletasks()

            try:
                for choice, button, ready in (
                        ('Русский', 'Отправить лог', 'Готово к работе'),
                        ('English', 'Send Log', 'Ready to work')):
                    with self.subTest(language=choice):
                        app.language_choice.set(choice)
                        app.change_language()
                        width, height = root.minsize()
                        resize(width, height + 90)
                        larger_log = app.output.winfo_height()
                        resize(width, height)
                        self.assertLess(app.output.winfo_height(), larger_log)
                        for widget in (app.log_path_entry, app.send_log_button):
                            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                                 app.layout.winfo_rooty() + height)
                        self.assertEqual(app.send_log_button.winfo_height(),
                                         app.send_log_button.winfo_reqheight())
                        self.assertEqual(app.send_log_button.cget('text'), button)
                        self.assertEqual(app.status.get(), ready)
                        self.assertEqual(app.log_path_entry.get(), str(app.logfile))
                self.assertEqual(app.action_buttons[1].cget('text'), 'Install / Update')
                self.assertEqual(app.release.get(0), 'Latest matching release (automatic)')
                self.assertTrue(any(t.text.startswith('Prepares an email draft') for t in app.tooltips))
                self.assertEqual(json.loads((Path(directory) / 'settings.json').read_text())['language'], 'en')
            finally:
                app.close()
            root = tk.Tk()
            root.withdraw()
            app = App(root, state=directory)
            try:
                self.assertEqual(app.language_choice.get(), 'English')
                self.assertEqual(app.send_log_button.cget('text'), 'Send Log')
            finally:
                app.close()

    def test_english_error_uses_selected_language_in_worker(self):
        import tkinter as tk
        from nbt_aad_pc.controller import Connection
        from nbt_aad_pc.ui import App
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'settings.json').write_text('{"language": "en"}')
            root = tk.Tk()
            root.withdraw()
            app = App(root, state=directory)
            try:
                with patch('nbt_aad_pc.ui.messagebox.showerror') as showerror:
                    app.start('Работа с головным устройством…', Connection().validate)
                    self.assertEqual(str(app.language_choice.cget('state')), 'disabled')
                    error = app.events.get(timeout=5)
                    app.events.put(error)
                    root.after_cancel(app.poll_after)
                    app.poll()
                self.assertEqual(str(app.language_choice.cget('state')), 'readonly')
                self.assertEqual(app.status.get(), 'Operation stopped. See the log.')
                self.assertEqual(showerror.call_args.args[0], 'Operation incomplete')
                self.assertIn('Enter the root password.', showerror.call_args.args[1])
            finally:
                app.busy = False
                app.close()

    def test_all_head_unit_actions_automatically_verify_ssh_and_use_found_host(self):
        import tkinter as tk
        from nbt_aad_pc.ui import App
        from nbt_aad_pc.controller import Connection
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                app.gateway.set('169.254.199.1')
                app.source_ip.set('169.254.199.50')
                app.password.set('private-test-password')
                result = Connection('169.254.199.119', 22, 'private-test-password', '169.254.199.50')
                with patch('nbt_aad_pc.ui.open_ssh', return_value=result) as opener, \
                     patch('nbt_aad_pc.ui.Installer') as installer, \
                     patch.object(app, 'get_package') as get_package, \
                     patch('nbt_aad_pc.ui.messagebox.askyesno', return_value=True), \
                     patch.object(app, 'start', side_effect=lambda title, work: work()):
                    execute = installer.return_value.execute
                    execute.side_effect = lambda *args, **kwargs: opener.assert_called_once()
                    for action in ('status', 'install', 'uninstall', 'purge', 'restart'):
                        with self.subTest(action=action):
                            opener.reset_mock()
                            execute.reset_mock()
                            app.operate(action)
                            execute.assert_called_once()
                            args = execute.call_args.args
                            self.assertEqual(args[0], 'uninstall' if action == 'purge' else action)
                            self.assertIs(args[1], result)
                            self.assertIs(args[2], get_package.return_value if action == 'install' else None)
                    get_package.assert_called_once()
                    opener.side_effect = RuntimeError('SSH недоступен')
                    installer.reset_mock()
                    with self.assertRaisesRegex(RuntimeError, 'SSH недоступен'):
                        app.operate('status')
                    installer.assert_not_called()
                root.after_cancel(app.poll_after)
                app.poll()
                self.assertEqual(app.host.get(), '169.254.199.119')
                saved = (Path(directory) / 'settings.json').read_text()
                self.assertNotIn('private-test-password', saved)
                self.assertEqual(json.loads(saved)['gateway'], '169.254.199.1')
                self.assertEqual(json.loads(saved)['source_ip'], '169.254.199.50')
            finally:
                app.close()

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
            self.assertEqual(app.host.get(), '169.254.199.99')
            self.assertTrue(app.reboot.get())
            self.assertFalse(app.preview.get())
            self.assertEqual([button.cget('text') for button in app.action_buttons], [
                'Состояние', 'Установить / обновить', 'Удалить', 'Очистить ГУ', 'Перезагрузить ГУ'])
            self.assertFalse(hasattr(app, 'ssh_open_button'))
            self.assertEqual(app.download_button.master, app.release.master)
            self.assertFalse(hasattr(app, 'offline'))
            self.assertTrue(any('ipconfig' in tooltip.text and 'Поле необязательное' in tooltip.text
                                for tooltip in app.tooltips))
            config = (Path(directory) / 'settings.json').read_text(encoding='utf-8')
            self.assertNotIn('password', config)
            self.assertEqual(json.loads(config)['port'], 22)
            self.assertNotIn('unique-secret-password', app.logfile.read_text(encoding='utf-8'))
            app.close()

    def test_removal_mode_and_full_cleanup_confirmation(self):
        import tkinter as tk
        from nbt_aad_pc.ui import App
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                with patch('nbt_aad_pc.ui.Installer') as installer, \
                     patch('nbt_aad_pc.ui.open_ssh', side_effect=lambda connection, *args: connection) as opener, \
                     patch.object(app, 'get_package', side_effect=AssertionError('uninstall must not download')), \
                     patch.object(app, 'start', side_effect=lambda title, work: work()), \
                     patch('nbt_aad_pc.ui.messagebox.askyesno', return_value=False) as question:
                    app.operate('uninstall')
                    self.assertEqual(installer.return_value.execute.call_args.kwargs['removal'], 'keep')
                    question.assert_not_called()
                    installer.reset_mock()
                    opener.reset_mock()
                    app.purge_button.invoke()
                    installer.assert_not_called()
                    opener.assert_not_called()
                    question.assert_called_once()
                    question.return_value = True
                    app.purge_button.invoke()
                    self.assertEqual(installer.return_value.execute.call_args.kwargs['removal'], 'purge')
                    self.assertEqual(installer.return_value.execute.call_args.args[0], 'uninstall')
                app.busy = True
                app.update_controls()
                self.assertEqual(str(app.purge_button.cget('state')), 'disabled')
                app.busy = False
            finally:
                app.busy = False
                app.close()

    def test_unified_list_merges_exact_builds_and_restores_old_offline_selection(self):
        import hashlib
        import tkinter as tk
        from nbt_aad_pc.ui import App
        from nbt_aad_pc.releases import download_package, PackageError
        from test_releases import release
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'packages'
            older, payloads = release('older', '2026-11-01T10:00:00Z')
            first = download_package(older, cache, downloader=lambda url, limit: payloads[url])
            newer, data = release('older', '2026-11-04T10:00:00Z')
            url = newer['assets'][0]['browser_download_url']
            data[url] += b'newer build'
            newer['assets'][0].update(size=len(data[url]),
                digest='sha256:' + hashlib.sha256(data[url]).hexdigest())
            (Path(directory) / 'settings.json').write_text(json.dumps(
                {'offline': True, 'offline_package': first.path.name}), encoding='utf-8')
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                self.assertEqual(app.selected_release(), (first.path.name, None))
                app.releases = [newer, older]
                app.update_release_list()
                self.assertEqual(len(app.release_choices), 3)  # auto + two distinct builds
                self.assertEqual(app.selected_release(), (first.path.name, older))
                cached_index, = app.release.curselection()
                self.assertTrue(app.release.get(cached_index).startswith('Скачан · '))
                self.assertTrue(app.release.itemcget(cached_index, 'foreground'))
                self.assertEqual(app.release.itemcget(cached_index, 'foreground'),
                                 app.release.itemcget(cached_index, 'selectforeground'))
                online_index = next(i for i, choice in enumerate(app.release_choices) if choice[1] is newer)
                self.assertFalse(app.release.itemcget(online_index, 'foreground'))
                with patch('nbt_aad_pc.ui.list_releases', side_effect=AssertionError('network')), \
                     patch('nbt_aad_pc.ui.download_package', side_effect=AssertionError('download')):
                    self.assertEqual(app.get_package(False, app.selected_release()).path, first.path)
                online_choice = next(choice for choice in app.release_choices if choice[1] is newer)
                with patch('nbt_aad_pc.ui.download_package',
                           side_effect=lambda release, cache, log: download_package(
                               release, cache, log, downloader=lambda url, limit: data[url])) as download:
                    second = app.get_package(False, online_choice)
                    download.assert_called_once()
                    self.assertNotEqual(first.path, second.path)
                    app.get_package(False, online_choice)
                    download.assert_called_once()
                with patch('nbt_aad_pc.ui.Installer') as installer, \
                     patch('nbt_aad_pc.ui.open_ssh', side_effect=lambda connection, *args: connection), \
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
                self.assertEqual(restored.selected_release(), (first.path.name, None))
                # A corrupted chosen build must not fall back to the newer one.
                first.files()['nbt-aad'].write_bytes(b'corrupted')
                with self.assertRaises(PackageError):
                    restored.get_package(False, restored.selected_release())
                restored.update_release_list()
                with self.assertRaises(PackageError):
                    restored.selected_release()
            finally:
                restored.close()
            settings = json.loads((Path(directory) / 'settings.json').read_text())
            self.assertEqual(settings['release_package'], first.path.name)
            self.assertNotIn('offline', settings)

    def test_new_download_appears_in_local_library_automatically(self):
        import tkinter as tk
        from nbt_aad_pc.ui import App
        from nbt_aad_pc.releases import download_package
        from test_releases import release
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                self.assertEqual(app.release_choices, [(None, None)])
                metadata, payloads = release('new-download', '2026-11-05T10:00:00Z')
                package = download_package(metadata, Path(directory) / 'packages',
                                           downloader=lambda url, limit: payloads[url])
                app.events.put(('package_downloaded', package.path.name))
                root.after_cancel(app.poll_after)
                app.poll()
                self.assertIn((package.path.name, None), app.release_choices)
                self.assertTrue(app.release.get(1).startswith('Скачан · '))
            finally:
                app.close()

    def test_send_log_opens_a_draft_with_the_current_log_attached(self):
        from email import policy
        from email.parser import BytesParser
        import tkinter as tk
        from nbt_aad_pc.ui import App
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                app.write('Тестовая запись журнала')
                with patch('nbt_aad_pc.ui.sys.platform', 'win32'), \
                     patch('nbt_aad_pc.ui.os.startfile', create=True) as open_draft:
                    app.send_log_button.invoke()
                draft = app.logfile.with_suffix('.eml')
                open_draft.assert_called_once_with(str(draft.resolve()))
                message = BytesParser(policy=policy.default).parsebytes(draft.read_bytes())
                self.assertEqual(message['To'], 'avyalozo@gmail.com')
                self.assertEqual(message['X-Unsent'], '1')
                attachment, = message.iter_attachments()
                self.assertEqual(attachment.get_filename(), app.logfile.name)
                self.assertEqual(attachment.get_payload(decode=True), app.logfile.read_bytes())
            finally:
                app.close()

    def test_tooltip_is_bound_to_focus_and_closes_when_operation_starts(self):
        import tkinter as tk
        from nbt_aad_pc.ui import App
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk(); root.withdraw()
            app = App(root, state=directory)
            try:
                tooltip = next(t for t in app.tooltips if t.widget is app.purge_button)
                self.assertTrue(app.purge_button.bind('<FocusIn>'))
                tooltip.schedule()
                self.assertIsNotNone(tooltip.pending)
                tooltip.widget.after_cancel(tooltip.pending)
                # A native popup on a withdrawn root can block a headless macOS runner.
                with patch('nbt_aad_pc.tooltips.tk.Toplevel') as toplevel, \
                     patch('nbt_aad_pc.tooltips.tk.Label'):
                    popup = toplevel.return_value
                    popup.winfo_reqwidth.return_value = 100
                    popup.winfo_reqheight.return_value = 40
                    tooltip.show()
                    popup.deiconify.assert_called_once_with()
                app.busy = True
                app.update_controls()
                popup.destroy.assert_called_once_with()
                self.assertIsNone(tooltip.window)
                tooltip.schedule()
                self.assertIsNone(tooltip.pending)
            finally:
                app.busy = False
                app.close()
