from datetime import datetime
from collections import Counter
import json
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import webbrowser
from . import __version__
from .controller import Connection, Installer
from .paths import state_directory
from .releases import (list_releases, download_package, local_package,
                       list_local_packages, is_preview, PackageError)


UPSTREAM = 'https://github.com/Sq00ky/Supra-iDrive6-Android-Auto'
DRIVE2 = 'https://www.drive2.ru/l/747198416496362487/'


def installer_script():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'nbt-aad.sh'
    return Path(__file__).resolve().parents[2] / 'nbt-aad.sh'


class App:
    def __init__(self, root, state=None):
        self.root, self.state = root, Path(state) if state else state_directory()
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'logs').mkdir(exist_ok=True)
        self.logfile = self.state / 'logs' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
        self.events, self.busy = queue.Queue(), False
        self.releases, self.local_packages, self.widgets = [], [], []
        self.secret = ''
        self.log_handle = self.logfile.open('a', encoding='utf-8', buffering=1)
        config = {}
        try:
            config = json.loads((self.state / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        self.config = config if isinstance(config, dict) else {}
        config = self.config
        self.preferred_local = config.get('offline_package')
        root.title('NBT-AAD PC — BMW / MINI')
        height = min(820, root.winfo_screenheight() - 80)
        root.geometry('1000x' + str(height))
        root.minsize(840, min(710, height))
        root.configure(bg='#f3f6f7')
        font_family = 'Helvetica Neue' if sys.platform == 'darwin' else 'Segoe UI'
        mono_family = 'Menlo' if sys.platform == 'darwin' else 'Consolas'
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TFrame', background='#f3f6f7')
        style.configure('TLabel', background='#f3f6f7', font=(font_family, 10))
        style.configure('TButton', font=(font_family, 10), padding=(12, 10))
        style.configure('Title.TLabel', font=(font_family, 23, 'bold'), foreground='#142d34')
        style.configure('Accent.TButton', background='#008d91', foreground='white')
        frame = ttk.Frame(root, padding=26)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Беспроводной Android Auto', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='BMW и MINI с NBT Evo · бесплатный установщик ' + __version__).pack(anchor='w', pady=(4, 16))
        note = ('Подключите ENET. CarPlay должен быть активирован, SSH — открыт.\n'
                'SSH можно открыть бесплатным Bavarian SSH Client или другими решениями.')
        ttk.Label(frame, text=note, foreground='#526970').pack(anchor='w')
        links = ttk.Frame(frame)
        links.pack(fill='x', pady=(6, 14))
        for title, url in [('Открыть Bavarian SSH Client', 'https://ssh.bavarian-aa.uk/'),
                           ('Исходный проект NBT-AAD', UPSTREAM), ('Мой бортжурнал на Drive2', DRIVE2)]:
            button = ttk.Label(links, text=title, foreground='#008d91', cursor='hand2')
            button.pack(side='left', padx=(0, 25))
            button.bind('<Button-1>', lambda event, url=url: webbrowser.open(url))
        connection = ttk.Frame(frame)
        connection.pack(fill='x')
        self.host = tk.StringVar(value=config.get('host', '169.254.199.99'))
        self.port = tk.StringVar(value=str(config.get('port', 22)))
        self.password = tk.StringVar(value='ts&SK412')
        for column, (label, variable, width, secret) in enumerate([
                ('IP головного устройства', self.host, 24, False),
                ('Порт SSH', self.port, 8, False), ('Пароль root', self.password, 22, True)]):
            cell = ttk.Frame(connection)
            cell.grid(row=0, column=column, sticky='w', padx=(0, 18))
            ttk.Label(cell, text=label).pack(anchor='w', pady=(0, 5))
            entry = ttk.Entry(cell, textvariable=variable, width=width, show='•' if secret else '')
            entry.pack(anchor='w')
            self.widgets.append(entry)
        choices = ttk.Frame(frame)
        choices.pack(fill='x', pady=(18, 0))
        self.preview = tk.BooleanVar(value=False)
        self.offline = tk.BooleanVar(value=bool(config.get('offline', False)))
        self.reboot = tk.BooleanVar(value=True)
        for title, var in [('Показывать бета-релизы', self.preview),
                           ('Использовать скачанный комплект (без интернета)', self.offline),
                           ('Перезагрузить ГУ после успеха', self.reboot)]:
            check = ttk.Checkbutton(choices, text=title, variable=var,
                                    command=self.offline_changed if var is self.offline else None)
            check.pack(anchor='w', pady=3)
            self.widgets.append(check)
        release_row = ttk.Frame(frame)
        release_row.pack(fill='x', pady=(10, 0))
        ttk.Label(frame, text='Релиз из интернета').pack(before=release_row, anchor='w')
        self.release = ttk.Combobox(release_row, state='readonly', width=64,
                                   values=['Последний подходящий релиз автоматически'])
        self.release.current(0)
        self.release.pack(side='left', fill='x', expand=True)
        self.widgets.append(self.release)
        self.release_refresh = self.add_button(release_row, 'Проверить релизы', self.refresh)
        self.release_refresh.pack(side='left', padx=(10, 0))
        self.download_button = self.add_button(release_row, 'Скачать заранее', self.prepare)
        self.download_button.pack(side='left', padx=(10, 0))
        ttk.Label(frame, text='Сохранённые комплекты — выбор для установки без интернета').pack(anchor='w', pady=(10, 3))
        local_row = ttk.Frame(frame)
        local_row.pack(fill='x')
        self.local_release = ttk.Combobox(local_row, state='readonly', width=64)
        self.local_release.pack(side='left', fill='x', expand=True)
        self.local_release.bind('<<ComboboxSelected>>', lambda event: self.save_preferences())
        self.widgets.append(self.local_release)
        self.package_label = tk.StringVar(value='Скачанный комплект: нет')
        ttk.Label(frame, textvariable=self.package_label, foreground='#526970').pack(anchor='w', pady=(8, 13))
        actions = ttk.Frame(frame)
        actions.pack(fill='x')
        self.action_buttons = []
        for title, command, accent in [
                ('Состояние', lambda: self.operate('status'), False),
                ('Установить / обновить', lambda: self.operate('install'), True),
                ('Удалить', lambda: self.operate('uninstall'), False),
                ('Очистить ГУ', lambda: self.operate('purge'), False),
                ('Перезагрузить ГУ', lambda: self.operate('restart'), False)]:
            button = self.add_button(actions, title, command, accent)
            button.pack(side='left', padx=(0, 9))
            self.action_buttons.append(button)
            if title == 'Перезагрузить ГУ':
                self.restart_button = button
            elif title == 'Очистить ГУ':
                self.purge_button = button
        self.status = tk.StringVar(value='Готово к работе')
        ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=(16, 5))
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 10))
        self.output = ScrolledText(frame, height=12, font=(mono_family, 9),
                                  bg='#142d34', fg='#edf8f8', state='disabled', relief='flat')
        self.output.pack(fill='both', expand=True)
        ttk.Label(frame, text='Журнал: ' + str(self.logfile), foreground='#526970', wraplength=850).pack(anchor='w', pady=(8, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_after = root.after(70, self.poll)
        self.write('NBT-AAD PC ' + __version__ + '. Пароль не сохраняется в настройках или журнале.')
        self.update_cache_label(preferred=config.get('offline_package'))

    def add_button(self, parent, text, command, accent=False):
        button = ttk.Button(parent, text=text, command=command, style='Accent.TButton' if accent else 'TButton')
        self.widgets.append(button)
        return button

    def write(self, message):
        if self.secret:
            message = message.replace(self.secret, '[пароль скрыт]')
        line = datetime.now().strftime('%H:%M:%S') + '  ' + message + '\n'
        self.log_handle.write(line)
        self.output.configure(state='normal')
        self.output.insert('end', line)
        self.output.see('end')
        self.output.configure(state='disabled')

    def log(self, message):
        self.events.put(('log', str(message)))

    def confirm(self, host, fingerprint):
        result, done = [], threading.Event()
        self.events.put(('trust', (host, fingerprint, result, done)))
        if not done.wait(180):
            return False
        return bool(result and result[0])

    def start(self, title, function):
        if self.busy:
            return
        self.busy = True
        self.status.set(title)
        self.progress.start(15)
        self.update_controls()
        def worker():
            try:
                self.events.put(('done', function()))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'log':
                    self.write(data)
                elif kind == 'trust':
                    host, fingerprint, result, done = data
                    result.append(messagebox.askyesno('Первое подключение к ГУ',
                        'Адрес: ' + host + '\nКлюч SSH: ' + fingerprint +
                        '\n\nСохранить этот ключ для вашего ГУ? При последующих подключениях он будет проверяться.', parent=self.root))
                    done.set()
                elif kind == 'releases':
                    self.releases = data
                    self.release.configure(values=['Последний подходящий релиз автоматически'] +
                        [('БЕТА · ' if r.get('_preview') else '') + (r.get('name') or r['tag_name']) for r in data])
                    self.release.current(0)
                elif kind == 'package_downloaded':
                    self.update_cache_label(preferred=data)
                    self.save_preferences()
                elif kind in ('done', 'error'):
                    self.busy = False
                    self.progress.stop()
                    self.update_cache_label()
                    if kind == 'error':
                        self.status.set('Операция остановлена — смотрите журнал')
                        self.write('ОШИБКА: ' + data)
                        detail = data.replace(self.secret, '[пароль скрыт]') if self.secret else data
                        messagebox.showerror('Операция не завершена', detail +
                            '\n\nЕсли SSH закрыт, откройте его бесплатным Bavarian SSH Client или другим решением. Не повторяйте установку при незавершённом откате.', parent=self.root)
                    else:
                        self.status.set('Готово')
                        if data:
                            self.write(str(data))
        except queue.Empty:
            pass
        self.poll_after = self.root.after(70, self.poll)

    def snapshot(self):
        connection = Connection(self.host.get().strip(), int(self.port.get()), self.password.get())
        connection.validate()
        self.secret = connection.password
        # Password deliberately excluded.
        self.config.update(host=connection.host, port=connection.port)
        self.save_preferences()
        return connection

    def selected_release(self):
        index = self.release.current()
        return self.releases[index-1] if index > 0 else None

    def selected_local_identifier(self):
        index = self.local_release.current()
        if 0 <= index < len(self.local_packages):
            return self.local_packages[index].path.name
        return None

    def save_preferences(self):
        selected = self.selected_local_identifier()
        if selected:
            self.preferred_local = selected
        config = {'host': self.config.get('host', '169.254.199.99'),
                  'port': self.config.get('port', 22),
                  'offline': self.offline.get(),
                  'offline_package': self.preferred_local}
        (self.state / 'settings.json').write_text(json.dumps(config), encoding='utf-8')

    def offline_changed(self):
        self.update_controls()
        self.save_preferences()

    def update_controls(self):
        for widget in self.widgets:
            state = 'readonly' if widget in (self.release, self.local_release) else 'normal'
            widget.configure(state='disabled' if self.busy else state)
        if not self.busy:
            if self.offline.get():
                self.release.configure(state='disabled')
                self.release_refresh.configure(state='disabled')
                self.download_button.configure(state='disabled')
            else:
                self.local_release.configure(state='disabled')

    def get_package(self, offline, include_preview, selected, local_identifier=None):
        if offline:
            if local_identifier is None:
                raise PackageError('Выберите сохранённый комплект для установки без интернета.')
            package = local_package(self.state / 'packages', local_identifier)
            self.log('Сохранённый комплект: ' + package.label)
            return package
        if selected is not None and is_preview(selected) and not include_preview:
            raise ValueError('Для выбранной бета-версии включите «Показывать бета-релизы» или выберите автоматический релиз.')
        if selected is None:
            releases = list_releases(include_preview)
            if not releases:
                raise ValueError('Подходящих релизов не найдено. Проверьте настройки бета-релизов.')
            selected = releases[0]
        self.log('Выбран релиз: ' + (selected.get('name') or selected['tag_name']))
        package = download_package(selected, self.state / 'packages', self.log)
        self.events.put(('package_downloaded', package.path.name))
        return package

    def operate(self, action):
        removal = 'purge' if action == 'purge' else 'keep'
        if action == 'purge':
            if not messagebox.askyesno('Полное удаление NBT-AAD',
                'Будут безвозвратно удалены программа и плагин NBT-AAD, настройки /opt/aa и /var/opt/aa, '
                'все резервные копии нашего установщика, его временные загрузки и известные файлы NBT-AAD в RAM.\n\n'
                'Откат из этих резервных копий станет невозможен. Общие кэши HMI, системные журналы, '
                'активация SSH, CarPlay и сопряжения телефона сохраняются. Файлы на ПК и флешке сохраняются.\n\n'
                'Выполнить полное удаление?', parent=self.root):
                return
        try:
            connection = self.snapshot()
        except (ValueError, OSError) as exc:
            messagebox.showerror('Параметры подключения', str(exc), parent=self.root)
            return
        offline, preview, reboot = self.offline.get(), self.preview.get(), self.reboot.get()
        selected = self.selected_release()
        local_identifier = self.selected_local_identifier()
        def work():
            package = self.get_package(offline, preview, selected, local_identifier) if action == 'install' else None
            installer = Installer(installer_script(), self.state, self.log, self.confirm)
            return installer.execute('uninstall' if action == 'purge' else action,
                                     connection, package, reboot, removal=removal)
        self.start('Перезагрузка головного устройства…' if action == 'restart' else 'Работа с головным устройством…', work)

    def prepare(self):
        preview, selected = self.preview.get(), self.selected_release()
        self.start('Скачивание комплекта на ноутбук…',
                   lambda: 'Комплект скачан и проверен: ' + self.get_package(False, preview, selected).label)

    def refresh(self):
        include_preview = self.preview.get()
        def work():
            releases = list_releases(include_preview)
            for release in releases:
                release['_preview'] = is_preview(release)
            self.events.put(('releases', releases))
            return 'Найдено релизов: ' + str(len(releases))
        self.start('Проверка релизов GitHub…', work)

    def update_cache_label(self, preferred=None):
        preferred = preferred or self.selected_local_identifier() or self.preferred_local
        self.local_packages = list_local_packages(self.state / 'packages', self.log)
        labels = []
        for package in self.local_packages:
            release = package.metadata['release']
            date = (release.get('published_at') or '')[:10]
            labels.append(('БЕТА · ' if is_preview(release) else '') + package.label +
                          ' [' + release['tag_name'] + '] · ' + date)
        counts = Counter(labels)
        for index, package in enumerate(self.local_packages):
            if counts[labels[index]] > 1:
                labels[index] += ' · ' + package.path.name[:8]
        self.local_release.configure(values=labels)
        if self.local_packages:
            identifiers = [package.path.name for package in self.local_packages]
            if preferred and preferred not in identifiers:
                self.local_release.set('Выбранный комплект недоступен — выберите другой')
            else:
                try:
                    fallback = json.loads((self.state / 'packages/last.json').read_text())['package']
                except (OSError, ValueError, KeyError, TypeError):
                    fallback = None
                selected = preferred or fallback
                self.local_release.current(identifiers.index(selected) if selected in identifiers else 0)
                self.preferred_local = self.selected_local_identifier()
        else:
            self.local_release.set('Нет сохранённых комплектов — скачайте заранее')
        try:
            identifier = self.selected_local_identifier()
            package = next(p for p in self.local_packages if p.path.name == identifier)
            label = ('БЕТА · ' if is_preview(package.metadata['release']) else '') + package.label
        except Exception:
            label = 'комплект не выбран'
        self.package_label.set('Сохранено комплектов: ' + str(len(self.local_packages)) + '. Выбран: ' + label)
        self.update_controls()

    def close(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется', 'Дождитесь завершения операции перед закрытием программы.', parent=self.root)
            return
        self.save_preferences()
        self.root.after_cancel(self.poll_after)
        self.log_handle.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
