import queue
from pathlib import Path
import socket
import ssl
import struct
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, Mock, call, patch

import websocket
from nbt_aad_pc.controller import Connection
from nbt_aad_pc.relay import (BLOCKED_UDS, HsfzGuard, MAX_RELAY_BODY, RelayError,
                              RelaySettings, ServerDecoder, activate, discover_gateway,
                              field, hello_frames)
from nbt_aad_pc.unlocker import find_ssh, open_ssh, verify


def hsfz(uds=b'\x22\xf1\x90', source=0xf4, target=0x63, control=1):
    return struct.pack('>IHBB', len(uds) + 2, control, source, target) + uds


def probe_socket(*frames):
    data = bytearray(b''.join(frames))
    sock = MagicMock()
    sock.__enter__.return_value = sock

    def recv(limit):
        # Exercise TCP fragmentation even when several frames arrive together.
        count = min(limit, 3)
        chunk = bytes(data[:count])
        del data[:count]
        return chunk

    sock.recv.side_effect = recv
    return sock


class ProtocolTests(unittest.TestCase):
    def test_observed_three_separate_handshake_messages(self):
        self.assertEqual([x.hex() for x in hello_frames()], [
            '0600000005302e312e30', '01000000000000000000000000',
            '020000000a7373685f656e61626c65'])

    def test_bytewise_fragmentation_and_concatenated_server_events(self):
        frame = hsfz()
        log = b'check'
        stream = field(0x81, frame) + b'\x82\x01' + struct.pack('>I', len(log)) + log + b'\x83\x01'
        decoder = ServerDecoder()
        result = []
        for byte in stream:
            result.extend(decoder.accept(bytes([byte])))
        self.assertEqual(result, [('car', frame), ('log', log), ('done', True)])
        self.assertEqual(ServerDecoder().accept(stream), result)

    def test_explicit_failure_and_unknown_status_do_not_succeed(self):
        self.assertEqual(ServerDecoder().accept(b'\x83\0'), [('done', False)])
        for data in (b'\x83\x02', b'\x84', b'\x83\x01x', b'\x81' + struct.pack('>I', MAX_RELAY_BODY + 1)):
            with self.subTest(data=data), self.assertRaises(RelayError):
                ServerDecoder().accept(data)

    def test_guard_waits_for_whole_frames(self):
        guard = HsfzGuard()
        first, second = hsfz(), hsfz(b'\x3e\0')
        self.assertEqual(guard.accept(first[:7]), [])
        self.assertEqual(guard.accept(first[7:] + second), [first, second])

    def test_guard_rejects_blocked_routine_even_when_fragmented(self):
        frame = hsfz(BLOCKED_UDS)
        for cut in range(1, len(frame)):
            guard = HsfzGuard()
            self.assertEqual(guard.accept(frame[:cut]), [])
            with self.assertRaisesRegex(RelayError, '31 01 FD EE 38'):
                guard.accept(frame[cut:])
        # Also mirror the Android client's rolling check across HSFZ payloads.
        guard = HsfzGuard()
        guard.accept(hsfz(BLOCKED_UDS[:3]))
        with self.assertRaises(RelayError):
            guard.accept(hsfz(BLOCKED_UDS[3:]))

    def test_guard_rejects_invalid_control_and_length(self):
        for packet in (struct.pack('>IH', 1, 1), struct.pack('>IH', 65537, 1),
                       struct.pack('>IH', 2, 0xffff) + b'\xf4\x63'):
            with self.assertRaises(RelayError):
                HsfzGuard().accept(packet)


class FakeWebSocket:
    def __init__(self):
        self.received, self.sent = queue.Queue(), []
        self.closed = False
        self.on_send = lambda data: None

    def settimeout(self, timeout):
        pass

    def send_binary(self, data):
        self.sent.append(data)
        self.on_send(data)

    def recv_data(self, control_frame=False):
        try:
            item = self.received.get(timeout=.03)
        except queue.Empty:
            raise websocket.WebSocketTimeoutException()
        if isinstance(item, Exception):
            raise item
        return websocket.ABNF.OPCODE_BINARY, item

    def close(self, **kwargs):
        self.closed = True


class RelayBridgeTests(unittest.TestCase):
    def setUp(self):
        self.ws = FakeWebSocket()
        self.client, self.vehicle = socket.socketpair()
        self.vehicle.settimeout(2)
        self.addCleanup(self.client.close)
        self.addCleanup(self.vehicle.close)
        self.logs = []
        self.factory = Mock(return_value=self.ws)
        self.settings = RelaySettings('127.0.0.1')
        self.probe = probe_socket(hsfz(b'\x62\xf1\x90TEST', source=0x63, target=0xf4))
        self.car_factory = Mock(side_effect=[self.probe, self.client])

    def run_relay(self, **kwargs):
        return activate(self.settings, self.logs.append, ws_factory=self.factory,
                        car_factory=self.car_factory, timeout=kwargs.get('timeout', .4))

    def test_full_bidirectional_bridge_and_explicit_success(self):
        command = hsfz()
        response = hsfz(b'\x62\xf1\x90TEST', source=0x63, target=0xf4)
        remote_text = b'provider name and diagnostic data'
        self.ws.received.put(b'\x82\x01' + struct.pack('>I', len(remote_text)) + remote_text)
        self.ws.received.put(field(0x81, command)[:4])
        self.ws.received.put(field(0x81, command)[4:])
        problems = []
        def vehicle():
            try:
                data = b''
                while len(data) < len(command):
                    data += self.vehicle.recv(1024)
                self.assertEqual(data, command)
                self.vehicle.sendall(response)
            except Exception as exc:
                problems.append(exc)
        thread = threading.Thread(target=vehicle)
        thread.start()
        def reply(data):
            if data == field(3, response):
                self.ws.received.put(b'\x83\x01')
        self.ws.on_send = reply
        self.assertEqual(self.run_relay(timeout=2), '127.0.0.1')
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(problems)
        self.assertNotIn(remote_text.decode(), '\n'.join(self.logs))
        self.assertEqual(self.ws.sent[:3], list(hello_frames()))
        self.assertIn(field(3, response), self.ws.sent)
        self.assertTrue(self.ws.closed)
        self.assertEqual(self.client.fileno(), -1)
        options = self.factory.call_args.kwargs
        self.assertEqual(options['sslopt']['cert_reqs'], ssl.CERT_REQUIRED)
        self.assertTrue(options['sslopt']['check_hostname'])
        self.assertTrue(Path(options['sslopt']['ca_certs']).is_file())
        self.assertEqual(options['redirect_limit'], 0)

    def test_preflight_ignores_ack_echo_and_other_ecus_and_waits_for_head_unit(self):
        probe = probe_socket(hsfz(control=2), hsfz(),
                             hsfz(b'\x62\xf1\x90OTHER', source=0x10, target=0xf4),
                             hsfz(b'\x7f\x22\x78', source=0x63, target=0xf4),
                             hsfz(b'\x7f\x22\x31', source=0x63, target=0xf4))
        self.settings = RelaySettings('169.254.199.1', '169.254.199.50')
        self.car_factory.side_effect = [probe, self.client]
        self.ws.received.put(b'\x83\x01')
        self.factory.side_effect = lambda *args, **kwargs: self._after_probe(probe)
        self.assertEqual(self.run_relay(), '169.254.199.1')
        self.assertEqual(self.car_factory.call_args_list,
                         [call('169.254.199.1', 6801, '169.254.199.50', timeout=6)] * 2)
        self.assertEqual(self.ws.sent, list(hello_frames()))

    def _after_probe(self, probe):
        probe.sendall.assert_called_once_with(hsfz())
        probe.__exit__.assert_called_once()
        return self.ws

    def test_gateway_ack_without_head_unit_reply_stops_before_online_request(self):
        for frames in (
                (hsfz(control=2),),
                (hsfz(b'\x62\xf1\x90OTHER', source=0x10, target=0xf4),),
                (hsfz(b'\x7f\x22\x78', source=0x63, target=0xf4),),
                (hsfz(b'\x7f\x22\x25', source=0x63, target=0xf4),),
                (struct.pack('>IH', 0, 0x43),)):
            with self.subTest(frames=frames):
                probe = probe_socket(*frames)
                self.car_factory.reset_mock(side_effect=True)
                self.car_factory.side_effect = [probe]
                with self.assertRaisesRegex(RelayError, 'шлюз доступен, но ГУ.*не ответило') as error:
                    self.run_relay()
                self.assertIn('OABR', str(error.exception))
                self.assertIn('Запрос на открытие SSH не отправлен', str(error.exception))
                probe.sendall.assert_called_once_with(hsfz())
                probe.__exit__.assert_called_once()
                self.car_factory.assert_called_once()
                self.factory.assert_not_called()
                self.assertFalse(self.ws.sent)

    def test_remote_failure_is_not_success_and_attempt_is_not_repeated(self):
        self.ws.received.put(b'\x83\0')
        with self.assertRaisesRegex(RelayError, 'отказ'):
            self.run_relay()
        self.assertEqual(self.ws.sent, list(hello_frames()))
        self.factory.assert_called_once()
        self.assertTrue(self.ws.closed)

    def test_retries_a_connection_reset_before_sending_the_single_activation(self):
        self.factory.side_effect = [ConnectionResetError(), self.ws]
        self.ws.received.put(b'\x83\1')
        with patch('nbt_aad_pc.relay.time.sleep') as sleep:
            self.assertEqual(self.run_relay(), '127.0.0.1')
        sleep.assert_called_once()
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.car_factory.call_count, 2)
        self.assertEqual(self.ws.sent, list(hello_frames()))

    def test_timeout_without_completion_is_not_success(self):
        with self.assertRaisesRegex(RelayError, 'Истекло время'):
            self.run_relay(timeout=.06)
        self.assertEqual(self.ws.sent, list(hello_frames()))

    def test_server_close_does_not_count_as_completion(self):
        self.ws.received.put(websocket.WebSocketConnectionClosedException('closed'))
        with self.assertRaisesRegex(RelayError, 'с сервисом открытия SSH закрылось.*не подтверждён'):
            self.run_relay()
        self.assertEqual(self.ws.sent, list(hello_frames()))
        self.factory.assert_called_once()

    def test_blocked_data_is_never_written_to_vehicle(self):
        self.ws.received.put(field(0x81, hsfz(BLOCKED_UDS)))
        with self.assertRaisesRegex(RelayError, 'Заблокирована'):
            self.run_relay()
        self.assertEqual(self.vehicle.recv(1024), b'')

    def test_partial_hsfz_at_completion_does_not_succeed(self):
        self.ws.received.put(field(0x81, hsfz()[:5]) + b'\x83\1')
        with self.assertRaisesRegex(RelayError, 'неполным'):
            self.run_relay()

    def test_tls_failure_never_opens_relay_vehicle_socket_or_sends_hello(self):
        self.factory.side_effect = ssl.SSLCertVerificationError('test CA mismatch')
        with self.assertRaises(RelayError) as error:
            self.run_relay()
        self.assertNotIn('зажигание', str(error.exception))
        self.car_factory.assert_called_once()
        self.probe.sendall.assert_called_once_with(hsfz())
        self.probe.__exit__.assert_called_once()
        self.assertFalse(self.ws.sent)
        self.client.close()

    def test_car_connection_failure_suggests_enet_and_ignition(self):
        self.car_factory.side_effect = ConnectionRefusedError()
        self.addCleanup(self.client.close)
        with self.assertRaisesRegex(RelayError, 'кабеля ENET к автомобилю.*зажигание включено'):
            self.run_relay()
        self.factory.assert_not_called()
        self.assertFalse(self.ws.sent)


class ActivationFlowTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection(password='secret')
        self.settings = RelaySettings(gateway='169.254.199.1')
        self.activator = Mock()
        self.verifier = Mock(side_effect=lambda connection, *args: connection)
        self.sleep = Mock()

    def call(self, finder, **kwargs):
        return open_ssh(self.connection, self.settings, Path('/tmp/test'), lambda *args: True,
                        lambda message: None, finder=finder, activator=self.activator,
                        verifier=self.verifier, sleeper=self.sleep, **kwargs)

    def test_existing_ssh_authentication_failure_never_activates(self):
        for error in (RuntimeError('bad password'), RuntimeError('host key changed'),
                      RuntimeError('not QNX')):
            self.verifier.side_effect = error
            with self.assertRaises(RuntimeError):
                self.call(Mock(return_value=self.connection.host))
            self.activator.assert_not_called()

    def test_activation_once_then_verified_discovered_host(self):
        result = self.call(Mock(side_effect=[None, None, None, '169.254.199.119']))
        self.assertEqual(result.host, '169.254.199.119')
        self.activator.assert_called_once()
        self.verifier.assert_called_once()
        self.assertEqual(self.sleep.call_count, 2)

    def test_relay_success_without_ssh_does_not_verify_or_repeat(self):
        with self.assertRaisesRegex(RelayError, 'SSH на ГУ не найден') as error:
            self.call(Mock(return_value=None))
        self.assertIn('зажигание включено', str(error.exception))
        self.assertIn('OABR', str(error.exception))
        self.activator.assert_called_once()
        self.verifier.assert_not_called()
        self.assertEqual(self.sleep.call_count, 3)

    def test_relay_failure_does_not_scan_again_or_verify(self):
        self.activator.side_effect = RelayError('failed')
        finder = Mock(return_value=None)
        with self.assertRaises(RelayError):
            self.call(finder)
        self.assertEqual(finder.call_count, 2)
        self.verifier.assert_not_called()

    def test_manual_source_ip_is_preserved_for_verification(self):
        self.connection.source_ip = '169.254.199.50'
        self.settings = RelaySettings(self.settings.gateway, self.connection.source_ip)
        result = self.call(Mock(return_value=self.connection.host))
        self.assertEqual(result.source_ip, self.connection.source_ip)

    def test_multiple_discovered_qnx_hosts_are_not_guessed(self):
        def probe(host, *args):
            return b'SSH-2.0-QNX_Secure_Shell' if host.endswith(('.11', '.12')) else b''
        with self.assertRaisesRegex(RelayError, 'несколько QNX'):
            find_ssh('192.168.1.99', '192.168.1.1', probe=probe)

    def test_unrelated_ssh_server_is_not_used_by_subnet_discovery(self):
        probed = []
        def probe(host, *args):
            probed.append(host)
            return b'SSH-2.0-OpenSSH' if host.endswith('.10') else b''
        self.assertIsNone(find_ssh('192.168.1.99', '192.168.1.1', probe=probe))
        self.assertLessEqual(len(probed), 260)
        self.assertTrue(all(host.startswith(('192.168.1.', '169.254.199.')) for host in probed))

    def test_verify_closes_session_even_if_qnx_check_fails(self):
        session = Mock()
        with patch('nbt_aad_pc.unlocker.detect', side_effect=RuntimeError('wrong target')):
            with self.assertRaisesRegex(RuntimeError, 'wrong target'):
                verify(self.connection, Path('/tmp/test'), Mock(), Mock(),
                       session_factory=Mock(return_value=session))
        session.close.assert_called_once()


class DiscoveryTests(unittest.TestCase):
    def test_discovery_without_reply_suggests_enet_and_ignition(self):
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.recvfrom.side_effect = socket.timeout()
        with self.assertRaisesRegex(RelayError, 'кабеля ENET к автомобилю.*зажигание включено'):
            discover_gateway(socket_factory=Mock(return_value=fake))

    def test_ident_reply_returns_only_one_bmw_gateway(self):
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.recvfrom.side_effect = [
            (struct.pack('>IH', 0, 0x11) + b'DIAGADR63BMWVINTEST0000000000000', ('169.254.199.119', 6811)),
            (struct.pack('>IH', 0, 0x11) + b'DIAGADR10BMWVINTEST0000000000000', ('169.254.199.1', 6811)),
            socket.timeout()]
        self.assertEqual(discover_gateway('169.254.199.50', socket_factory=Mock(return_value=fake)),
                         '169.254.199.1')
        fake.bind.assert_called_once_with(('169.254.199.50', 0))

    def test_public_gateway_is_rejected_before_any_connection(self):
        with self.assertRaises(ValueError):
            activate(RelaySettings('8.8.8.8'), ws_factory=Mock())


if __name__ == '__main__':
    unittest.main()
