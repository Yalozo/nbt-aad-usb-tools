"""Prepare and verify SSH before a head-unit operation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import ipaddress
import socket
import time

from .controller import Connection, detect
from .relay import RelayError, RelaySettings, activate, connect_car, discover_gateway, ipv4
from .ssh import Session, with_enet_hint


def ssh_banner(host, port=22, source_ip='', timeout=.7, connector=connect_car):
    try:
        with connector(host, port, source_ip, timeout=timeout) as sock:
            sock.settimeout(timeout)
            data = bytearray()
            while len(data) < 1024:
                part = sock.recv(256)
                if not part:
                    break
                data.extend(part)
                for line in data.split(b'\n'):
                    if line.startswith(b'SSH-'):
                        return bytes(line).strip()
            return b''
    except (OSError, ValueError):
        return b''


def find_ssh(preferred, gateway='', source_ip='', port=22, *, probe=ssh_banner):
    """At most one gateway /24, never sweep the whole link-local /16 or the Internet."""
    common = list(dict.fromkeys([preferred] + ([gateway] if gateway else []) +
                               ['169.254.199.99', '169.254.199.119']))
    for host in common:
        banner = probe(host, port, source_ip)
        # The configured host is explicitly selected by the operator. Other hosts
        # must identify themselves as QNX before we offer an SSH login.
        if banner and (host == preferred or b'QNX' in banner.upper()):
            return host
    if not gateway:
        return None
    subnet = ipaddress.IPv4Network(gateway + '/24', strict=False)
    candidates = [str(ip) for ip in subnet.hosts() if str(ip) not in common and str(ip) != source_ip]
    with ThreadPoolExecutor(max_workers=24) as pool:
        banners = pool.map(lambda host: probe(host, port, source_ip), candidates)
        found = [host for host, banner in zip(candidates, banners) if b'QNX' in banner.upper()]
    if len(found) > 1:
        raise RelayError('Найдено несколько QNX SSH: %s. Укажите IP нужного ГУ вручную.' % ', '.join(found))
    return found[0] if found else None


def verify(connection, state, confirm, log, session_factory=Session):
    kwargs = {'source_ip': connection.source_ip} if connection.source_ip else {}
    session = session_factory(connection.host, connection.port, connection.password,
                              state / 'known_hosts', confirm, log, **kwargs)
    try:
        node = detect(session)
        log('SSH подтверждён: QNX, %s, root на hu-omap.' % node)
    finally:
        session.close()
    return connection


def open_ssh(connection, settings, state, confirm, log, *, finder=find_ssh,
             activator=activate, gateway_finder=discover_gateway, verifier=verify,
             sleeper=time.sleep):
    connection.validate()
    ipv4(connection.host)
    settings.validate()
    if connection.source_ip != settings.source_ip:
        raise ValueError('Для диагностики и SSH должен использоваться один выбранный IPv4 Ethernet ПК.')
    log('Проверка доступа к головному устройству…')
    existing = finder(connection.host, '', settings.source_ip, connection.port)
    if existing:
        # Authentication, changed host-key and QNX verification errors propagate.
        # They must NEVER trigger a diagnostic unlock request.
        log('SSH уже доступен: %s:%s. Проверка входа…' % (existing, connection.port))
        return verifier(replace(connection, host=existing), state, confirm, log)
    if connection.port != 22:
        raise RelayError('Для автоматического открытия SSH укажите стандартный порт 22.')
    try:
        gateway = settings.gateway or gateway_finder(settings.source_ip)
    except OSError as exc:
        raise RelayError(with_enet_hint('Не удалось выполнить поиск автомобиля по ENET: ' + str(exc))) from exc
    existing = finder(connection.host, gateway, settings.source_ip, connection.port)
    if existing:
        return verifier(replace(connection, host=existing), state, confirm, log)
    log('SSH закрыт. Открываем доступ автоматически. Сохраняйте питание ГУ, ENET и интернет.')
    activator(replace(settings, gateway=gateway), log)
    for attempt, delay in enumerate((2.5, 1.5, 1.5), 1):
        sleeper(delay)
        log('Поиск SSH после активации: попытка %s из 3…' % attempt)
        host = finder(connection.host, gateway, settings.source_ip, connection.port)
        if host:
            return verifier(replace(connection, host=host), state, confirm, log)
    raise RelayError(with_enet_hint('После открытия доступа SSH на ГУ не найден. Операция остановлена. '
                                   'Проверьте IP и журнал.'))
