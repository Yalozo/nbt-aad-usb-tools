from datetime import datetime
import json
import os
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
from .releases import list_releases, download_package, cached_package, is_preview


UPSTREAM = 'https://github.com/Sq00ky/Supra-iDrive6-Android-Auto'


def state_directory():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'NBT-AAD-PC'


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
        self.releases, self.widgets = [], []
        self.secret = ''
        self.log_handle = self.logfile.open('a', encoding='utf-8', buffering=1)
        config = {}
        try:
            config = json.loads((self.state / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        root.title('NBT-AAD PC — BMW / MINI')
        root.geometry('940x780')
        root.minsize(840, 710)
        root.configure(bg='#f3f6f7')
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TFrame', background='#f3f6f7')
        style.configure('TLabel', background='#f3f6f7', font=('Segoe UI', 10))
        style.configure('TButton', font=('Segoe UI', 10), padding=(12, 10))
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'), foreground='#142d34')
        style.configure('Accent.TButton', background='#008d91', foreground='white')
        frame = ttk.Frame(root, padding=26)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Беспроводной Android Auto', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='BMW и MINI с NBT Evo · бесплатный установщик ' + __version__).pack(anchor='w', pady=(4, 16))
        note = ('Подключите ENET и заранее откройте SSH через Bavarian SSH Client.\n'
                'CarPlay должен быть активирован. Флешка и PuTTY не нужны.')
        ttk.Label(frame, text=note, foreground='#526970').pack(anchor='w')
        links = ttk.Frame(frame)
        links.pack(fill='x', pady=(6, 14))
        for title, url in [('Открыть Bavarian SSH Client', 'https://ssh.bavarian-aa.uk/'),
                           ('Исходный проект NBT-AAD', UPSTREAM)]:
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
        self.offline = tk.BooleanVar(value=False)
        self.reboot = tk.BooleanVar(value=True)
        for title, var in [('Показывать бета-релизы', self.preview),
                           ('Использовать скачанный комплект (без интернета)', self.offline),
                           ('Перезагрузить ГУ после успеха', self.reboot)]:
            check = ttk.Checkbutton(choices, text=title, variable=var)
            check.pack(anchor='w', pady=3)
            self.widgets.append(check)
        release_row = ttk.Frame(frame)
        release_row.pack(fill='x', pady=(10, 0))
        self.release = ttk.Combobox(release_row, state='readonly', width=64,
                                   values=['Последний подходящий релиз автоматически'])
        self.release.current(0)
        self.release.pack(side='left', fill='x', expand=True)
        self.widgets.append(self.release)
        self.add_button(release_row, 'Проверить релизы', self.refresh).pack(side='left', padx=(10, 0))
        self.package_label = tk.StringVar(value='Скачанный комплект: нет')
        self.update_cache_label()
        ttk.Label(frame, textvariable=self.package_label, foreground='#526970').pack(anchor='w', pady=(8, 13))
        actions = ttk.Frame(frame)
        actions.pack(fill='x')
        for title, command, accent in [
                ('Установить / обновить', lambda: self.operate('install'), True),
                ('Удалить', lambda: self.operate('uninstall'), False),
                ('Состояние', lambda: self.operate('status'), False),
                ('Скачать заранее', self.prepare, False)]:
            self.add_button(actions, title, command, accent).pack(side='left', padx=(0, 9))
        self.status = tk.StringVar(value='Готово к работе')
        ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=(16, 5))
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 10))
        self.output = ScrolledText(frame, height=12, font=('Consolas', 9),
                                  bg='#142d34', fg='#edf8f8', state='disabled', relief='flat')
        self.output.pack(fill='both', expand=True)
        ttk.Label(frame, text='Журнал: ' + str(self.logfile), foreground='#526970', wraplength=850).pack(anchor='w', pady=(8, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(70, self.poll)
        self.write('NBT-AAD PC ' + __version__ + '. Пароль не сохраняется в настройках или журнале.')

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
        for widget in self.widgets:
            widget.configure(state='disabled')
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
                elif kind in ('done', 'error'):
                    self.busy = False
                    self.progress.stop()
                    for widget in self.widgets:
                        widget.configure(state='readonly' if widget is self.release else 'normal')
                    self.update_cache_label()
                    if kind == 'error':
                        self.status.set('Операция остановлена — смотрите журнал')
                        self.write('ОШИБКА: ' + data)
                        detail = data.replace(self.secret, '[пароль скрыт]') if self.secret else data
                        messagebox.showerror('Операция не завершена', detail +
                            '\n\nЕсли SSH закрыт, сначала откройте его через Bavarian SSH Client. Не повторяйте установку при незавершённом откате.', parent=self.root)
                    else:
                        self.status.set('Готово')
                        if data:
                            self.write(str(data))
        except queue.Empty:
            pass
        self.root.after(70, self.poll)

    def snapshot(self):
        connection = Connection(self.host.get().strip(), int(self.port.get()), self.password.get())
        connection.validate()
        self.secret = connection.password
        # Password deliberately excluded.
        (self.state / 'settings.json').write_text(json.dumps({'host': connection.host, 'port': connection.port}), encoding='utf-8')
        return connection

    def selected_release(self):
        index = self.release.current()
        return self.releases[index-1] if index > 0 else None

    def get_package(self, offline, include_preview, selected):
        if offline:
            package = cached_package(self.state / 'packages')
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
        return download_package(selected, self.state / 'packages', self.log)

    def operate(self, action):
        try:
            connection = self.snapshot()
        except (ValueError, OSError) as exc:
            messagebox.showerror('Параметры подключения', str(exc), parent=self.root)
            return
        offline, preview, reboot = self.offline.get(), self.preview.get(), self.reboot.get()
        selected = self.selected_release()
        def work():
            package = self.get_package(offline, preview, selected) if action == 'install' else None
            installer = Installer(installer_script(), self.state, self.log, self.confirm)
            return installer.execute(action, connection, package, reboot)
        self.start('Работа с головным устройством…', work)

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

    def update_cache_label(self):
        try:
            package = cached_package(self.state / 'packages')
            label = ('БЕТА · ' if is_preview(package.metadata['release']) else '') + package.label
        except Exception:
            label = 'нет доступного проверенного комплекта'
        self.package_label.set('Скачанный комплект: ' + label)

    def close(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется', 'Дождитесь завершения операции перед закрытием программы.', parent=self.root)
            return
        self.log_handle.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
