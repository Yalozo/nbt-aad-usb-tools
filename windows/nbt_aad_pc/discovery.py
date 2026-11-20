"""Read-only discovery of BMW diagnostic gateways on directly attached LANs."""
from dataclasses import dataclass
import ipaddress
import json
import re
import socket
import subprocess
import sys
import time

from .i18n import tr


@dataclass(frozen=True)
class LanAddress:
    name: str
    ipv4: ipaddress.IPv4Interface


def _run(command, **options):
    try:
        return subprocess.check_output(command, encoding='utf-8', errors='replace',
                                       stderr=subprocess.DEVNULL, timeout=12, **options)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(tr('Не удалось получить список сетевых интерфейсов.')) from exc


def _mac_addresses(output):
    for match in re.finditer(r'^(en\d+): flags=[^\n]*<([^>]+)>[^\n]*\n((?:[ \t].*\n?)*)',
                             output, re.MULTILINE):
        name, flags, details = match.groups()
        if not {'UP', 'BROADCAST'}.issubset(flags.split(',')) or not re.search(
                r'^\s+status: active$', details, re.MULTILINE):
            continue
        for address, mask in re.findall(r'^\s+inet (\S+) netmask (0x[0-9a-f]+)\s+broadcast ',
                                        details, re.MULTILINE | re.IGNORECASE):
            netmask = ipaddress.IPv4Address(int(mask, 16))
            yield LanAddress(name, ipaddress.IPv4Interface(f'{address}/{netmask}'))


def _windows_addresses(output):
    rows = json.loads(output or '[]')
    for row in rows:
        yield LanAddress(row['Name'], ipaddress.IPv4Interface(f"{row['Address']}/{row['Prefix']}"))


def lan_addresses():
    if sys.platform == 'darwin':
        return list(_mac_addresses(_run(['/sbin/ifconfig', '-a'])))
    if sys.platform == 'win32':
        # IANA interface types 6 and 71 are Ethernet and IEEE 802.11.
        command = """
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $ErrorActionPreference = 'Stop'
        $adapters = Get-NetAdapter -Physical | Where-Object {
            $_.Status -eq 'Up' -and $_.InterfaceType -in 6, 71
        } | Sort-Object InterfaceIndex
        $addresses = @(foreach ($adapter in $adapters) {
            Get-NetIPAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.AddressState -in 'Preferred', 'Deprecated' } |
                ForEach-Object {
                    [pscustomobject]@{Name=$adapter.Name; Address=$_.IPAddress; Prefix=$_.PrefixLength}
                }
        })
        ConvertTo-Json -InputObject $addresses -Compress
        """
        return list(_windows_addresses(_run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            creationflags=subprocess.CREATE_NO_WINDOW)))
    return []


def _probe(adapter, timeout):
    source = adapter.ipv4.ip
    network = adapter.ipv4.network
    found = set()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if sys.platform == 'darwin':
            # Darwin's IP_BOUND_IF prevents routing via a different adapter on the same subnet.
            sock.setsockopt(socket.IPPROTO_IP, getattr(socket, 'IP_BOUND_IF', 25),
                            socket.if_nametoindex(adapter.name))
        sock.bind((str(source), 0))
        sock.sendto(b'\x00\x00\x00\x00\x00\x11', (str(network.broadcast_address), 6811))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                data, peer = sock.recvfrom(2048)
            except TimeoutError:
                break
            address = ipaddress.IPv4Address(peer[0])
            if (address in network and address not in (source, network.network_address, network.broadcast_address)
                    and data[4:6] == b'\x00\x11' and b'DIAGADR10' in data[6:] and b'BMWVIN' in data[6:]):
                found.add(address)
    return sorted(found)


def find_gateways(source_ip='', log=lambda _: None, *, timeout=2.0):
    routes = [adapter for adapter in dict.fromkeys(lan_addresses())
              if 0 < adapter.ipv4.network.prefixlen < 31
              and not (adapter.ipv4.ip.is_unspecified or adapter.ipv4.ip.is_loopback
                       or adapter.ipv4.ip.is_multicast)
              and (not source_ip or str(adapter.ipv4.ip) == source_ip)]
    if not routes:
        raise ValueError(tr('Нет подходящего активного интерфейса Ethernet или Wi-Fi с IPv4.'))
    matches = []
    successful = False
    for adapter in routes:
        log(tr('Поиск шлюза BMW через %s (%s)…') % (adapter.name, adapter.ipv4))
        try:
            addresses = _probe(adapter, timeout)
        except OSError as exc:
            log(tr('Ошибка поиска через %s: %s') % (adapter.name, exc))
            continue
        successful = True
        for address in addresses:
            matches.append((str(address), adapter))
    if not successful:
        raise OSError(tr('Не удалось отправить запрос поиска. Подробности в журнале.'))
    return matches
