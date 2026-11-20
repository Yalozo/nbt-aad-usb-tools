import ipaddress
import unittest
from unittest.mock import patch

from nbt_aad_pc import discovery


class DiscoveryTests(unittest.TestCase):
    def test_mac_uses_active_lan_addresses_and_netmasks(self):
        addresses = discovery._mac_addresses("""
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.11.2 netmask 0xffff0000 broadcast 169.254.255.255
\tstatus: active
en1: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.12.32 netmask 0xffffff80 broadcast 192.168.12.127
\tstatus: active
en2: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.3.5 netmask 0xffffff00 broadcast 192.168.3.255
\tstatus: inactive
utun0: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1500
\tinet 10.0.0.4 netmask 0xffffff00 broadcast 10.0.0.255
\tstatus: active
""")
        self.assertEqual([(row.name, str(row.ipv4.network.broadcast_address)) for row in addresses],
                         [('en0', '169.254.255.255'), ('en1', '192.168.12.127')])

    def test_windows_preserves_adapter_names_and_ipv4_prefixes(self):
        result = list(discovery._windows_addresses(
            '[{"Name":"Сеть","Address":"192.168.1.200","Prefix":24}]'))
        self.assertEqual(result[0], discovery.LanAddress('Сеть', ipaddress.IPv4Interface('192.168.1.200/24')))

    def test_discovery_checks_all_routes_in_order_even_after_failure_or_match(self):
        adapters = [
            discovery.LanAddress('A', ipaddress.IPv4Interface('192.168.1.5/24')),
            discovery.LanAddress('B', ipaddress.IPv4Interface('192.168.2.5/24')),
            discovery.LanAddress('C', ipaddress.IPv4Interface('192.168.3.5/24')),
        ]
        first = ipaddress.IPv4Address('192.168.1.107')
        last = ipaddress.IPv4Address('192.168.3.107')
        with (patch.object(discovery, 'lan_addresses', return_value=adapters),
              patch.object(discovery, '_probe', side_effect=[[first], OSError(), [last]]) as probe):
            self.assertEqual(discovery.find_gateways(), [(str(first), adapters[0]), (str(last), adapters[2])])
            self.assertEqual([call.args[0] for call in probe.call_args_list], adapters)

    def test_explicit_source_limits_discovery_to_that_address(self):
        other = discovery.LanAddress('A', ipaddress.IPv4Interface('192.168.1.5/24'))
        selected = discovery.LanAddress('B', ipaddress.IPv4Interface('192.168.2.5/24'))
        with (patch.object(discovery, 'lan_addresses', return_value=[other, selected]),
              patch.object(discovery, '_probe', return_value=[]) as probe):
            self.assertEqual(discovery.find_gateways('192.168.2.5', timeout=0.5), [])
            probe.assert_called_once_with(selected, 0.5)
