from datetime import datetime
from collections import Counter
from email.message import EmailMessage
from email.policy import SMTP
import json
import os
import subprocess
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
from .relay import RelaySettings
from .unlocker import open_ssh
from .paths import state_directory
from .tooltips import Tooltip
from .releases import (list_releases, download_package, local_package,
                       list_local_packages, is_preview, select_assets, package_identifier,
                       CACHE_ERRORS, PackageError)


UPSTREAM = 'https://github.com/Sq00ky/Supra-iDrive6-Android-Auto'
DRIVE2 = 'https://www.drive2.ru/l/747198416496362487/'
SUPPORT_EMAIL = 'avyalozo@gmail.com'
CAR_PREPARATION_NOTE = 'Подключите кабель ENET к автомобилю и включите зажигание.'
SSH_INTERNET_NOTE = 'Интернет нужен для открытия SSH на ГУ, если он ещё закрыт.'
DOWNLOADED_COLOR = '#14623b'
ETHERNET_HELP = ('Поле необязательное. Оставьте пустым, если ГУ находится автоматически. '
                 'Если на ПК несколько адаптеров и поиск идёт не через ENET, откройте ipconfig '
                 '(на Mac — настройки сети) и укажите IPv4-адрес адаптера, к которому подключён ENET. '
                 'Программа привяжет к этому адаптеру поиск ГУ и SSH. Интернет использует '
                 'обычный сетевой маршрут, например Wi-Fi.')
BUTTON_HELP = {
    'Состояние': 'Проверяет установленные версии программы и плагина, файлы и запущенный процесс. '
        'При закрытом SSH автоматически открывает доступ.',
    'Установить / обновить': 'Устанавливает выбранный релиз или обновляет имеющийся Android Auto. '
        'Настройки сохраняются, прежние файлы помещаются в резервную копию. '
        'Можно выбрать сохранённый комплект.',
    'Удалить': 'Обычное удаление Android Auto и пункта меню. Настройки подключения, nbt-aad.env '
        'и резервные копии остаются на ГУ. При повторной установке настройки используются снова. '
        'Скачанный комплект не нужен.',
    'Очистить ГУ': 'Полностью удаляет NBT-AAD: программу, плагин, настройки, резервные копии '
        'нашего установщика, его временные загрузки и известные журналы/файлы состояния. '
        'Восстановление из этих копий станет невозможно. Требуется подтверждение. '
        'Если pf_aa применялся отдельно, сначала нужно восстановить его правила. '
        'Это не заводской сброс: SSH, CarPlay и сопряжения Bluetooth сохраняются.',
    'Перезагрузить ГУ': 'Отправляет отдельную команду перезагрузки ГУ через SSH. '
        'Не устанавливает и не удаляет Android Auto. Не зависит от флажка перезагрузки после установки. '
        'Дождитесь загрузки штатного меню.',
    'Скачать заранее': 'Скачивает выбранный интернет-релиз на компьютер и проверяет его файлы. '
        'Готовый комплект получает отметку «Скачан» в списке релизов.',
    'Отправить лог': 'Подготавливает письмо разработчику с текущим журналом во вложении. '
        'Проверьте журнал и отправьте письмо из своего почтового приложения.',
    'Проверить релизы': 'Получает список доступных релизов NBT-AAD из GitHub. '
        'Нужен интернет. Головное устройство не изменяется.',
}


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
        self.releases, self.release_choices, self.widgets = [], [], []
        self.tooltips = []
        self.secret = ''
        self.log_handle = self.logfile.open('a', encoding='utf-8', buffering=1)
        config = {}
        try:
            config = json.loads((self.state / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        self.config = config if isinstance(config, dict) else {}
        config = self.config
        self.preferred_release = config.get('release_package',
            config.get('offline_package') if config.get('offline') else None)
        root.title('NBT-AAD PC')
        height = min(840, root.winfo_screenheight() - 80)
        root.geometry('1000x' + str(height))
        root.minsize(840, min(690, height))
        root.configure(bg='#f3f6f7')
        font_family = 'Helvetica Neue' if sys.platform == 'darwin' else 'Segoe UI'
        mono_family = 'Menlo' if sys.platform == 'darwin' else 'Consolas'
        style = ttk.Style(root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('TFrame', background='#f3f6f7')
        style.configure('TLabel', background='#f3f6f7', font=(font_family, 10))
        style.configure('TButton', font=(font_family, 10), padding=(12, 10))
        style.configure('Title.TLabel', font=(font_family, 16, 'bold'), foreground='#142d34')
        style.configure('Accent.TButton', background='#008d91', foreground='white')
        frame = ttk.Frame(root, padding=(26, 16, 26, 18))
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Беспроводной Android Auto', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='BMW и MINI с NBT Evo · бесплатный установщик ' + __version__).pack(anchor='w', pady=(2, 8))
        note = CAR_PREPARATION_NOTE + '\n' + SSH_INTERNET_NOTE + '\nCarPlay должен быть активирован.'
        ttk.Label(frame, text=note, foreground='#526970').pack(anchor='w')
        links = ttk.Frame(frame)
        links.pack(fill='x', pady=(6, 10))
        for title, url in [('Исходный проект NBT-AAD', UPSTREAM), ('Мой бортжурнал на Drive2', DRIVE2)]:
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
        relay_row = ttk.Frame(frame)
        relay_row.pack(fill='x', pady=(8, 0))
        self.gateway = tk.StringVar(value=config.get('gateway', ''))
        self.source_ip = tk.StringVar(value=config.get('source_ip', ''))
        for label, variable in [('IP диагностики (пусто = поиск)', self.gateway),
                                ('IPv4 Ethernet ПК (необязательно)', self.source_ip)]:
            ttk.Label(relay_row, text=label).pack(side='left', padx=(0, 6))
            entry = ttk.Entry(relay_row, textvariable=variable, width=18)
            entry.pack(side='left', padx=(0, 16))
            self.widgets.append(entry)
            if variable is self.source_ip:
                self.tooltips.append(Tooltip(entry, ETHERNET_HELP))
        self.preview = tk.BooleanVar(value=False)
        self.reboot = tk.BooleanVar(value=True)
        release_row = ttk.Frame(frame)
        release_row.pack(fill='x', pady=(10, 13))
        release_row.columnconfigure(0, weight=1)
        ttk.Label(frame, text='Релиз NBT-AAD').pack(before=release_row, anchor='w', pady=(12, 0))
        self.release = tk.Listbox(release_row, height=4, width=38, selectmode='browse',
                                  exportselection=False, font=(font_family, 10),
                                  background='white', foreground='#142d34',
                                  selectbackground='#dce9eb', selectforeground='#142d34')
        self.release.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(release_row, orient='vertical', command=self.release.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.release.configure(yscrollcommand=scrollbar.set)
        self.release.bind('<<ListboxSelect>>', self.release_selected)
        self.widgets.append(self.release)
        self.release_refresh = self.add_button(release_row, 'Проверить релизы', self.refresh)
        self.release_refresh.grid(row=0, column=2, sticky='n', padx=(10, 0))
        self.download_button = self.add_button(release_row, 'Скачать заранее', self.prepare)
        self.download_button.grid(row=0, column=3, sticky='n', padx=(10, 0))
        preview_check = ttk.Checkbutton(release_row, text='Показывать бета-релизы из интернета',
                                        variable=self.preview, command=self.update_release_list)
        preview_check.grid(row=1, column=0, sticky='w', pady=(3, 0))
        self.widgets.append(preview_check)
        self.release_hint = ttk.Label(release_row, foreground='#526970',
            text='Выбранный релиз недоступен — обновите список или выберите другой')
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
        reboot_check = ttk.Checkbutton(frame, text='Перезагрузить ГУ после успеха', variable=self.reboot)
        reboot_check.pack(anchor='w', pady=(6, 0))
        self.widgets.append(reboot_check)
        self.status = tk.StringVar(value='Готово к работе')
        ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=(16, 5))
        self.progress = ttk.Progressbar(frame, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 10))
        self.output = ScrolledText(frame, height=9, font=(mono_family, 9),
                                  bg='#142d34', fg='#edf8f8', state='disabled', relief='flat')
        self.output.pack(fill='both', expand=True)
        log_row = ttk.Frame(frame)
        log_row.pack(fill='x', pady=(8, 0))
        self.send_log_button = self.add_button(log_row, 'Отправить лог', self.send_log)
        self.send_log_button.pack(side='right')
        ttk.Label(log_row, text='Журнал: ' + str(self.logfile), foreground='#526970',
                  wraplength=650).pack(side='left', fill='x', expand=True)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.poll_after = root.after(70, self.poll)
        self.write('NBT-AAD PC ' + __version__ + '. Пароль не сохраняется в настройках или журнале.')
        self.write(CAR_PREPARATION_NOTE)
        self.write(SSH_INTERNET_NOTE)
        self.update_release_list()

    def add_button(self, parent, text, command, accent=False):
        button = ttk.Button(parent, text=text, command=command, style='Accent.TButton' if accent else 'TButton')
        self.widgets.append(button)
        if text in BUTTON_HELP:
            self.tooltips.append(Tooltip(button, BUTTON_HELP[text]))
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
                    self.update_release_list()
                elif kind == 'package_downloaded':
                    self.update_release_list()
                elif kind == 'ssh_verified':
                    self.host.set(data.host)
                    self.config.update(host=data.host, port=data.port)
                    self.save_preferences()
                elif kind in ('done', 'error'):
                    self.busy = False
                    self.progress.stop()
                    if kind == 'error':
                        self.update_release_list()
                        self.status.set('Операция остановлена — смотрите журнал')
                        self.write('ОШИБКА: ' + data)
                        detail = data.replace(self.secret, '[пароль скрыт]') if self.secret else data
                        messagebox.showerror('Операция не завершена', detail +
                            '\n\nПри ошибке открытия SSH автоматического повтора нет. Не повторяйте установку при незавершённом откате.', parent=self.root)
                    else:
                        self.update_controls()
                        self.status.set('Готово')
                        if data:
                            self.write(str(data))
        except queue.Empty:
            pass
        self.poll_after = self.root.after(70, self.poll)

    def snapshot(self):
        connection = Connection(self.host.get().strip(), int(self.port.get()), self.password.get(),
                                self.source_ip.get().strip())
        connection.validate()
        self.secret = connection.password
        # Password deliberately excluded.
        self.config.update(host=connection.host, port=connection.port)
        self.save_preferences()
        return connection

    def selected_release(self):
        selection = self.release.curselection()
        if not selection:
            raise PackageError('Выберите релиз из списка.')
        return self.release_choices[selection[0]]

    def release_selected(self, event=None):
        if not self.release.curselection():
            return
        self.preferred_release, _ = self.selected_release()
        self.release_hint.grid_remove()
        self.save_preferences()

    def save_preferences(self):
        config = {'host': self.config.get('host', '169.254.199.99'),
                  'port': self.config.get('port', 22),
                  'release_package': self.preferred_release,
                  'gateway': self.gateway.get().strip(),
                  'source_ip': self.source_ip.get().strip()}
        (self.state / 'settings.json').write_text(json.dumps(config), encoding='utf-8')

    def update_controls(self):
        for tooltip in self.tooltips:
            tooltip.hide()
        for widget in self.widgets:
            widget.configure(state='disabled' if self.busy else 'normal')

    def get_package(self, include_preview, choice):
        identifier, selected = choice
        cache = self.state / 'packages'
        if identifier is None:
            releases = list_releases(include_preview)
            if not releases:
                raise ValueError('Подходящих релизов не найдено. Проверьте настройки бета-релизов.')
            selected = releases[0]
            identifier = package_identifier(select_assets(selected))
        try:
            package = local_package(cache, identifier)
        except CACHE_ERRORS:
            if selected is None:
                raise PackageError('Скачанный комплект недоступен. Обновите список релизов.') from None
        else:
            self.log('Сохранённый комплект: ' + package.label)
            return package
        self.log('Выбран релиз: ' + (selected.get('name') or selected['tag_name']))
        package = download_package(selected, cache, self.log)
        self.events.put(('package_downloaded', package.path.name))
        return package

    def operate(self, action):
        if self.busy:
            return
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
            settings = RelaySettings(self.gateway.get().strip(), connection.source_ip)
            settings.validate()
            selected = self.selected_release() if action == 'install' else None
        except (ValueError, OSError, PackageError) as exc:
            messagebox.showerror('Параметры подключения', str(exc), parent=self.root)
            return
        preview, reboot = self.preview.get(), self.reboot.get()
        def work():
            package = self.get_package(preview, selected) if action == 'install' else None
            verified = open_ssh(connection, settings, self.state, self.confirm, self.log)
            self.events.put(('ssh_verified', verified))
            installer = Installer(installer_script(), self.state, self.log, self.confirm)
            return installer.execute('uninstall' if action == 'purge' else action,
                                     verified, package, reboot, removal=removal)
        self.start('Перезагрузка головного устройства…' if action == 'restart' else 'Работа с головным устройством…', work)

    def prepare(self):
        try:
            selected = self.selected_release()
        except PackageError as exc:
            messagebox.showerror('Выбор релиза', str(exc), parent=self.root)
            return
        preview = self.preview.get()
        self.start('Скачивание комплекта на ноутбук…',
                   lambda: 'Комплект готов: ' + self.get_package(preview, selected).label)

    def refresh(self):
        include_preview = self.preview.get()
        def work():
            releases = list_releases(include_preview=True)
            self.events.put(('releases', releases))
            return 'Найдено релизов: ' + str(sum(include_preview or not is_preview(r) for r in releases))
        self.start('Проверка релизов GitHub…', work)

    def update_release_list(self):
        cached = {p.path.name: p.metadata['release']
                  for p in list_local_packages(self.state / 'packages', self.log)}
        online = {}
        for release in self.releases:
            if self.preview.get() or not is_preview(release):
                identifier = package_identifier(select_assets(release))
                online.setdefault(identifier, release)
        releases = {**cached, **online}
        identifiers = sorted(releases, key=lambda key: releases[key].get('published_at') or '', reverse=True)
        labels = []
        for key in identifiers:
            release = releases[key]
            label = ('Скачан · ' if key in cached else '') + ('БЕТА · ' if is_preview(release) else '')
            labels.append(label + (release.get('name') or release['tag_name']) +
                          ' [' + release['tag_name'] + '] · ' + (release.get('published_at') or '')[:10])
        counts = Counter(labels)
        labels = [label + ' · ' + key[:8] if counts[label] > 1 else label
                  for key, label in zip(identifiers, labels)]
        self.release_choices = [(None, None)] + [(key, online.get(key)) for key in identifiers]
        self.release.configure(state='normal')
        self.release.delete(0, 'end')
        self.release.insert('end', 'Последний подходящий релиз автоматически', *labels)
        for index, key in enumerate(identifiers, 1):
            if key in cached:
                self.release.itemconfigure(index, foreground=DOWNLOADED_COLOR,
                                           selectforeground=DOWNLOADED_COLOR,
                                           selectbackground='#d7efe1')
        self.release_hint.grid_remove()
        if self.preferred_release is None:
            index = 0
        elif self.preferred_release in identifiers:
            index = identifiers.index(self.preferred_release) + 1
        else:
            index = None
            self.release_hint.grid(row=2, column=0, columnspan=4, sticky='w', pady=(3, 0))
        if index is not None:
            self.release.selection_set(index)
            self.release.activate(index)
            self.release.see(index)
        self.update_controls()

    def send_log(self):
        self.log_handle.flush()
        draft = self.logfile.with_suffix('.eml')
        message = EmailMessage(policy=SMTP)
        message['To'] = SUPPORT_EMAIL
        message['Subject'] = 'Журнал NBT-AAD PC ' + __version__
        message['X-Unsent'] = '1'
        message.set_content('Здравствуйте! Прилагаю журнал установщика.\n\nОписание проблемы:\n')
        try:
            message.add_attachment(self.logfile.read_bytes(), maintype='application',
                                   subtype='octet-stream', filename=self.logfile.name)
            draft.write_bytes(message.as_bytes())
        except OSError as exc:
            messagebox.showerror('Подготовка письма', str(exc), parent=self.root)
            return
        try:
            if sys.platform == 'win32':
                os.startfile(str(draft.resolve()))
            elif sys.platform == 'darwin':
                subprocess.run(['/usr/bin/open', str(draft.resolve())], check=True)
            else:
                webbrowser.open(draft.resolve().as_uri())
        except (OSError, subprocess.CalledProcessError):
            messagebox.showinfo('Письмо с журналом сохранено',
                                'Откройте файл письма в почтовой программе:\n' + str(draft),
                                parent=self.root)

    def close(self):
        if self.busy:
            messagebox.showinfo('Операция выполняется', 'Дождитесь завершения операции перед закрытием программы.', parent=self.root)
            return
        self.save_preferences()
        for tooltip in self.tooltips:
            tooltip.hide()
        self.root.after_cancel(self.poll_after)
        self.log_handle.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
