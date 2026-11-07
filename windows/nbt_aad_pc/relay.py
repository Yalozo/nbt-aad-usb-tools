"""Online SSH activation protocol and guarded diagnostic forwarding."""
from dataclasses import dataclass
import ipaddress
import queue
import socket
import ssl
import struct
import threading
import time

import certifi
import websocket

from .ssh import RemoteError, with_enet_hint


RELAY_URL = 'wss://pretzel.bavarian-aa.uk:8443'
MAX_RELAY_BODY = 128 * 1024
MAX_HSFZ_BODY = 65536
MAX_PENDING = 256 * 1024
BLOCKED_UDS = bytes.fromhex('31 01 FD EE 38')


class RelayError(RemoteError):
    pass


def ipv4(value, *, optional=False):
    if optional and not value:
        return ''
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError('Ожидался IPv4-адрес: ' + str(value)) from exc
    if address.is_unspecified or address.is_multicast or address.is_global:
        raise ValueError('Нужен локальный адрес ENET, а не интернет-адрес: ' + str(value))
    return str(address)


@dataclass(frozen=True)
class RelaySettings:
    gateway: str = ''
    source_ip: str = ''

    def validate(self):
        ipv4(self.gateway, optional=True)
        ipv4(self.source_ip, optional=True)


def field(opcode, payload):
    if len(payload) > MAX_RELAY_BODY:
        raise RelayError('Сообщение relay превышает допустимый размер.')
    return bytes([opcode]) + struct.pack('>I', len(payload)) + payload


def hello_frames():
    return (field(6, b'0.1.0'), b'\x01' + b'\0' * 12, field(2, b'ssh_enable'))


class ServerDecoder:
    """Decode arbitrarily fragmented/concatenated relay messages, not WebSocket frame boundaries."""
    def __init__(self):
        self.pending = bytearray()
        self.completed = False

    def accept(self, data):
        if self.completed and data:
            raise RelayError('Relay прислал данные после завершения операции.')
        if len(self.pending) + len(data) > MAX_PENDING:
            raise RelayError('Переполнение буфера relay.')
        self.pending.extend(data)
        events = []
        while self.pending:
            opcode = self.pending[0]
            if opcode == 0x83:
                if len(self.pending) < 2:
                    break
                status = self.pending[1]
                del self.pending[:2]
                if status not in (0, 1):
                    raise RelayError('Неизвестный код завершения relay: ' + str(status))
                if self.pending:
                    raise RelayError('Лишние данные после кода завершения relay.')
                self.completed = True
                events.append(('done', status == 1))
                break
            if opcode not in (0x81, 0x82):
                raise RelayError('Неизвестная команда relay: 0x%02X.' % opcode)
            # 0x82 has an extra severity byte preceding its u32 length.
            header = 6 if opcode == 0x82 else 5
            if len(self.pending) < header:
                break
            length = struct.unpack_from('>I', self.pending, header - 4)[0]
            if length > MAX_RELAY_BODY:
                raise RelayError('Недопустимая длина сообщения relay.')
            if len(self.pending) < header + length:
                break
            payload = bytes(self.pending[header:header + length])
            del self.pending[:header + length]
            events.append(('car' if opcode == 0x81 else 'log', payload))
        return events


class HsfzGuard:
    """Buffer complete HSFZ frames; mirror the APK's explicit blocked UDS sequence."""
    def __init__(self):
        self.pending = bytearray()
        self.tail = b''

    def accept(self, data):
        if len(self.pending) + len(data) > MAX_PENDING:
            raise RelayError('Переполнение диагностического буфера.')
        self.pending.extend(data)
        frames = []
        while len(self.pending) >= 6:
            length, control = struct.unpack_from('>IH', self.pending)
            if not 2 <= length <= MAX_HSFZ_BODY:
                raise RelayError('Недопустимая длина HSFZ: ' + str(length))
            if control not in (1, 2):
                raise RelayError('Недопустимый тип HSFZ: 0x%04X.' % control)
            if len(self.pending) < length + 6:
                break
            frame = bytes(self.pending[:length + 6])
            payload = frame[8:]
            combined = self.tail + payload
            if BLOCKED_UDS in combined:
                raise RelayError('Заблокирована UDS routine 31 01 FD EE 38. Открытие SSH остановлено.')
            self.tail = combined[-(len(BLOCKED_UDS) - 1):]
            del self.pending[:length + 6]
            frames.append(frame)
        return frames


def connect_car(host, port, source_ip='', timeout=6):
    ipv4(host)
    ipv4(source_ip, optional=True)
    return socket.create_connection((host, port), timeout=timeout,
                                    source_address=(source_ip, 0) if source_ip else None)


def discover_gateway(source_ip='', timeout=3, socket_factory=socket.socket):
    ipv4(source_ip, optional=True)
    # The default is the conventional ENET link-local broadcast. A specified source
    # also gets limited broadcast on its chosen interface, useful with DHCP adapters.
    destinations = ['169.254.255.255']
    if source_ip:
        destinations.append('255.255.255.255')
    found = {}
    with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((source_ip or '0.0.0.0', 0))
        for destination in destinations:
            sock.sendto(struct.pack('>IH', 0, 0x11), (destination, 6811))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(.05, deadline - time.monotonic()))
            try:
                response, peer = sock.recvfrom(2048)
            except socket.timeout:
                break
            if len(response) < 6 or response[4:6] != b'\x00\x11':
                continue
            identification = response[6:].decode('latin1', errors='replace')
            if 'DIAGADR10' not in identification or 'BMWVIN' not in identification:
                continue
            host = ipv4(peer[0])
            found[host] = identification
    if not found:
        raise RelayError(with_enet_hint('Автомобиль не ответил на поиск шлюза BMW.'))
    if len(found) != 1:
        raise RelayError('Шлюз BMW не найден однозначно. Укажите IP диагностики вручную; '
                         'при необходимости также IPv4 Ethernet-адаптера ПК.')
    return next(iter(found))


def activate(settings, log=lambda message: None, *, ws_factory=websocket.create_connection,
             car_factory=connect_car, gateway_finder=discover_gateway, timeout=75):
    """Attempt SSH activation once. The caller verifies SSH separately."""
    settings.validate()
    gateway = settings.gateway or gateway_finder(settings.source_ip)
    log('Диагностический шлюз BMW: %s:6801.' % gateway)
    decoder, guard = ServerDecoder(), HsfzGuard()
    stop, errors = threading.Event(), queue.Queue()
    ws, car, reader = None, None, None
    outcome = None
    try:
        # Establish verified WSS before opening the car session or sending any diagnostics.
        for attempt in range(2):
            try:
                ws = ws_factory(RELAY_URL, timeout=10, enable_multithread=True,
                                redirect_limit=0, suppress_origin=True,
                                header={'User-Agent': 'NBT-AAD-PC/0.1'},
                                sslopt={'ca_certs': certifi.where(), 'cert_reqs': ssl.CERT_REQUIRED,
                                        'check_hostname': True})
            except (ConnectionResetError, TimeoutError, socket.timeout, websocket.WebSocketTimeoutException):
                if attempt:
                    raise
                log('Сетевое подключение прервалось. Повторяем подключение перед открытием SSH.')
                time.sleep(1)
            else:
                break
        ws.settimeout(1)
        try:
            car = car_factory(gateway, 6801, settings.source_ip, timeout=6)
        except OSError as exc:
            raise RelayError(with_enet_hint('Не удалось подключиться к диагностике автомобиля.')) from exc
        car.settimeout(.5)
        log('Соединение для открытия SSH установлено.')
        for message in hello_frames():
            ws.send_binary(message)

        def responses():
            try:
                while not stop.is_set():
                    try:
                        data = car.recv(32768)
                    except socket.timeout:
                        continue
                    if not data:
                        # A remote ECU reset can close HSFZ. Await the relay's explicit status;
                        # an EOF alone is neither proof of failure nor success.
                        log('Диагностическое соединение закрылось. Ожидаем результат открытия SSH.')
                        return
                    ws.send_binary(field(3, data))
            except Exception as exc:
                if not stop.is_set():
                    errors.put(exc)

        reader = threading.Thread(target=responses, name='ssh-relay-car-reader', daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        while outcome is None and time.monotonic() < deadline:
            if not errors.empty():
                raise RelayError(with_enet_hint(
                    'Ошибка передачи ответа автомобиля (' + type(errors.get()).__name__ + ').'))
            try:
                opcode, data = ws.recv_data(control_frame=True)
            except websocket.WebSocketTimeoutException:
                continue
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                raise RelayError('Соединение закрылось. Результат открытия SSH не подтверждён.')
            if opcode in (websocket.ABNF.OPCODE_PING, websocket.ABNF.OPCODE_PONG):
                continue
            if opcode != websocket.ABNF.OPCODE_BINARY:
                raise RelayError('Ожидалось бинарное сообщение relay.')
            for kind, payload in decoder.accept(data):
                if kind == 'car':
                    for frame in guard.accept(payload):
                        try:
                            car.sendall(frame)
                        except OSError as exc:
                            raise RelayError(with_enet_hint('Диагностическая связь с автомобилем прервана.')) from exc
                # Keep server-provided text and diagnostic payloads out of the user log.
                elif kind == 'done':
                    if guard.pending:
                        raise RelayError('Relay завершился с неполным диагностическим кадром.')
                    outcome = payload
        if outcome is not True:
            if outcome is False:
                raise RelayError('Получен отказ в открытии SSH. Запрос автоматически не повторяется.')
            raise RelayError(with_enet_hint('Истекло время ожидания открытия SSH. '
                                           'Запрос автоматически не повторяется.'))
        log('Запрос открытия SSH выполнен. Проверяем вход.')
        return gateway
    except (OSError, websocket.WebSocketException) as exc:
        raise RelayError('Результат открытия SSH не подтверждён (' + type(exc).__name__ +
                         '). Проверьте интернет и ENET.') from exc
    finally:
        stop.set()
        if car is not None:
            try:
                car.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            car.close()
        if ws is not None:
            try:
                ws.close(timeout=1)
            except Exception:
                pass
        if reader is not None:
            reader.join(timeout=2)
