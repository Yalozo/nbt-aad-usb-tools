"""Prepare and verify SSH before a head-unit operation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import ipaddress
import socket
import time

from .controller import Connection, detect
from .discovery import find_gateways
from .relay import (RelayError, RelaySettings, UnknownRelayCommandError, activate,
                    connect_car, discover_gateway, ipv4)
from .ssh import Session, with_enet_hint
from .i18n import tr


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


def find_ssh(preferred, gateway='', source_ip='', port=22, *, probe=ssh_banner, network=None):
    """At most one gateway /24, never sweep the whole link-local /16 or the Internet."""
    common = list(dict.fromkeys(([preferred] if preferred else []) + ([gateway] if gateway else []) +
                               ['169.254.199.99', '169.254.199.119']))
    common = [host for host in common if host != source_ip]
    for host in common:
        banner = probe(host, port, source_ip)
        # The configured host is explicitly selected by the operator. Other hosts
        # must identify themselves as QNX before we offer an SSH login.
        if banner and (host == preferred or b'QNX' in banner.upper()):
            return host
    if not gateway:
        return None
    subnet = ipaddress.IPv4Network(gateway + '/24', strict=False)
    candidates = [str(ip) for ip in subnet.hosts() if str(ip) not in common and str(ip) != source_ip
                  and (network is None or (ip in network and ip not in (network.network_address,
                                                                       network.broadcast_address)))]
    with ThreadPoolExecutor(max_workers=24) as pool:
        banners = pool.map(lambda host: probe(host, port, source_ip), candidates)
        found = [host for host, banner in zip(candidates, banners) if b'QNX' in banner.upper()]
    if len(found) > 1:
        raise RelayError(tr('Найдено несколько QNX SSH: %s. Укажите IP нужного ГУ вручную.') % ', '.join(found))
    return found[0] if found else None


def verify(connection, state, confirm, log, session_factory=Session):
    kwargs = {'source_ip': connection.source_ip} if connection.source_ip else {}
    session = session_factory(connection.host, connection.port, connection.password,
                              state / 'known_hosts', confirm, log, **kwargs)
    try:
        node = detect(session)
        log(tr('SSH подтверждён: QNX, %s, root на hu-omap.') % node)
    finally:
        session.close()
    return connection


def find_local_connection(connection, log, *, discover=find_gateways, finder=find_ssh):
    try:
        gateways = discover(connection.source_ip, log)
    except (OSError, ValueError) as exc:
        log(str(exc))
        return None
    available = []
    targets = []
    for gateway, adapter in gateways:
        log(tr('Шлюз BMW %s найден через %s (%s).') % (gateway, adapter.name, adapter.ipv4))
        source_ip = str(adapter.ipv4.ip)
        target = (gateway, source_ip)
        if target in targets:
            continue
        targets.append(target)
        host = finder('', gateway, source_ip, connection.port, network=adapter.ipv4.network)
        if host:
            endpoint = (host, source_ip, gateway)
            if endpoint not in available:
                available.append(endpoint)
    if len(available) > 1:
        raise RelayError(tr('Найдено несколько QNX SSH: %s. Укажите IP нужного ГУ вручную.') %
                         ', '.join(f'{host} ({source_ip})' for host, source_ip, _ in available))
    if available:
        (host, source_ip, gateway), = available
        return replace(connection, host=host, source_ip=source_ip), gateway
    if len(targets) > 1:
        raise RelayError(tr('Найдено несколько шлюзов BMW. Укажите IP диагностики вручную.'))
    if targets:
        (gateway, source_ip), = targets
        return replace(connection, source_ip=source_ip), gateway
    return None


def open_ssh(connection, settings, state, confirm, log, *, finder=find_ssh,
             activator=activate, gateway_finder=discover_gateway, verifier=verify,
             sleeper=time.sleep, local_connection_finder=find_local_connection):
    connection.validate(allow_auto=True)
    ipv4(connection.host, optional=True)
    settings.validate()
    if connection.source_ip != settings.source_ip:
        raise ValueError(tr('Для диагностики и SSH должен использоваться один выбранный IPv4 Ethernet ПК.'))
    log(tr('Проверка доступа к головному устройству…'))
    if not connection.host and not settings.gateway:
        discovered = local_connection_finder(connection, log)
        if discovered:
            connection, gateway = discovered
            settings = replace(settings, gateway=gateway, source_ip=connection.source_ip)
            if connection.host:
                log(tr('SSH уже доступен: %s:%s. Проверка входа…') % (connection.host, connection.port))
                return verifier(connection, state, confirm, log)
    if not connection.host:
        connection = replace(connection, host=Connection().host)
    existing = finder(connection.host, '', settings.source_ip, connection.port)
    if existing:
        # Authentication, changed host-key and QNX verification errors propagate.
        # They must NEVER trigger a diagnostic unlock request.
        log(tr('SSH уже доступен: %s:%s. Проверка входа…') % (existing, connection.port))
        return verifier(replace(connection, host=existing), state, confirm, log)
    if connection.port != 22:
        raise RelayError(tr('Для автоматического открытия SSH укажите стандартный порт 22.'))
    try:
        gateway = settings.gateway or gateway_finder(settings.source_ip, log=log)
    except OSError as exc:
        raise RelayError(with_enet_hint(tr('Не удалось выполнить поиск автомобиля по ENET: ') + str(exc))) from exc
    existing = finder(connection.host, gateway, settings.source_ip, connection.port)
    if existing:
        return verifier(replace(connection, host=existing), state, confirm, log)
    log(tr('SSH не найден. Проверяем связь с ГУ перед открытием доступа.'))
    activation_error = None
    try:
        activator(replace(settings, gateway=gateway), log)
    except UnknownRelayCommandError as exc:
        activation_error = exc
        log(str(exc))
        log(tr('Проверяем доступность SSH после ошибки ответа сервиса.'))
    for attempt, delay in enumerate((2.5, 1.5, 1.5), 1):
        sleeper(delay)
        log(tr('Поиск SSH после активации: попытка %s из 3…') % attempt)
        host = finder(connection.host, gateway, settings.source_ip, connection.port)
        if host:
            return verifier(replace(connection, host=host), state, confirm, log)
    if activation_error is not None:
        raise activation_error
    raise RelayError(with_enet_hint(tr('Запрос выполнен, но SSH на ГУ не найден. Операция остановлена. '
                                      'Проверьте IP головного устройства и маршрут через ENET.'), retrofit=True))
