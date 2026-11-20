import ipaddress
from unittest.mock import Mock
import unittest

from nbt_aad_pc.diagnostics import restart
from nbt_aad_pc.discovery import LanAddress
from nbt_aad_pc.relay import RelayError, RelaySettings
from test_relay import hsfz, probe_socket


class DiagnosticRestartTests(unittest.TestCase):
    def run_reset(self, response, **kwargs):
        self.probe = probe_socket(hsfz(b'\x62\xf1\x90TEST', source=0x63, target=0xf4))
        self.reset = probe_socket(response)
        self.connector = Mock(side_effect=[self.probe, self.reset])
        return restart(RelaySettings('192.168.1.131', '192.168.1.200'), connector=self.connector, **kwargs)

    def test_reset_is_sent_once_and_requires_positive_head_unit_response(self):
        result = self.run_reset(hsfz(b'\x11\x01', control=2) +
                                hsfz(b'\x7f\x11\x78', source=0x63, target=0xf4) +
                                hsfz(b'\x51\x01', source=0x63, target=0xf4))
        self.assertIn('ECUReset ответом 51 01', result)
        self.reset.sendall.assert_called_once_with(bytes.fromhex('000000040001f4631101'))

    def test_acknowledgement_or_wrong_ecu_never_confirms_restart(self):
        for response in (hsfz(b'\x11\x01', control=2), hsfz(b'\x51\x01', source=0x10, target=0xf4)):
            with self.subTest(response=response), self.assertRaisesRegex(RelayError, 'ответ ГУ не получен'):
                self.run_reset(response)
            self.reset.sendall.assert_called_once()

    def test_negative_response_is_reported_without_retry(self):
        with self.assertRaisesRegex(RelayError, 'NRC 0x22'):
            self.run_reset(hsfz(b'\x7f\x11\x22', source=0x63, target=0xf4))
        self.reset.sendall.assert_called_once()

    def test_discovered_gateway_preserves_source_adapter(self):
        probe = probe_socket(hsfz(b'\x62\xf1\x90TEST', source=0x63, target=0xf4))
        reset = probe_socket(hsfz(b'\x51\x01', source=0x63, target=0xf4))
        connector = Mock(side_effect=[probe, reset])
        adapter = LanAddress('Wi-Fi', ipaddress.IPv4Interface('192.168.1.200/24'))
        restart(RelaySettings(), connector=connector,
                discover=Mock(return_value=[('192.168.1.131', adapter)]))
        self.assertTrue(all(call.args[:3] == ('192.168.1.131', 6801, '192.168.1.200')
                            for call in connector.call_args_list))

    def test_multiple_gateways_do_not_send_a_reset(self):
        connector = Mock()
        adapter = LanAddress('Wi-Fi', ipaddress.IPv4Interface('192.168.1.200/24'))
        with self.assertRaisesRegex(RelayError, 'несколько шлюзов'):
            restart(RelaySettings(), connector=connector,
                    discover=Mock(return_value=[('192.168.1.131', adapter), ('192.168.1.132', adapter)]))
        connector.assert_not_called()
