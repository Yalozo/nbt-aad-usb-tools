"""Actual TLS WebSocket + TCP vehicle simulators; no external server or vehicle."""
import base64
import hashlib
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import unittest

import websocket
from nbt_aad_pc.relay import RelaySettings, activate, field, hello_frames


def read_exact(sock, length):
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise EOFError('simulator EOF')
        data += chunk
    return data


def read_ws(sock):
    header = read_exact(sock, 2)
    length = header[1] & 127
    if length == 126:
        length = struct.unpack('>H', read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack('>Q', read_exact(sock, 8))[0]
    if not header[1] & 128:
        raise AssertionError('client WebSocket frames must be masked')
    mask = read_exact(sock, 4)
    payload = read_exact(sock, length)
    return header[0] & 15, bytes(value ^ mask[i % 4] for i, value in enumerate(payload))


def send_ws(sock, payload):
    header = bytes([0x82, len(payload)]) if len(payload) < 126 else b'\x82\x7e' + struct.pack('>H', len(payload))
    sock.sendall(header + payload)


class ActualNetworkBridgeTest(unittest.TestCase):
    def test_real_tls_upgrade_masked_hello_and_bidirectional_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix='nbt-relay-tls-test-') as folder:
            folder = Path(folder)
            certificate, private = folder / 'cert.pem', folder / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', str(private), '-out', str(certificate), '-days', '1',
                            '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(certificate, private)
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(5)
            self.addCleanup(listener.close)
            endpoint = listener.getsockname()
            command = struct.pack('>IHBB', 5, 1, 0xf4, 0x63) + b'\x22\xf1\x90'
            reply = struct.pack('>IHBB', 6, 1, 0x63, 0xf4) + b'\x62\xf1\x90X'
            car_client, car_server = socket.socketpair()
            car_server.settimeout(5)
            self.addCleanup(car_server.close)
            failures, seen = [], []

            def server():
                try:
                    peer, _ = listener.accept()
                    with server_context.wrap_socket(peer, server_side=True) as tls:
                        tls.settimeout(5)
                        request = b''
                        while not request.endswith(b'\r\n\r\n'):
                            request += read_exact(tls, 1)
                        headers = dict(line.split(': ', 1) for line in request.decode().split('\r\n')[1:] if ': ' in line)
                        digest = base64.b64encode(hashlib.sha1((headers['Sec-WebSocket-Key'] +
                            '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
                        tls.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                                    b'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + digest + b'\r\n\r\n')
                        for expected in hello_frames():
                            opcode, payload = read_ws(tls)
                            self.assertEqual(opcode, 2)
                            self.assertEqual(payload, expected)
                            seen.append(payload)
                        # Fragment the application protocol across distinct WebSocket messages.
                        message = field(0x81, command)
                        send_ws(tls, message[:3])
                        send_ws(tls, message[3:])
                        opcode, payload = read_ws(tls)
                        self.assertEqual((opcode, payload), (2, field(3, reply)))
                        send_ws(tls, b'\x83\x01')
                        read_ws(tls)  # receive the client's close handshake
                except Exception as exc:
                    failures.append(exc)

            def vehicle():
                try:
                    self.assertEqual(read_exact(car_server, len(command)), command)
                    car_server.sendall(reply)
                except Exception as exc:
                    failures.append(exc)

            server_thread = threading.Thread(target=server, daemon=True)
            car_thread = threading.Thread(target=vehicle, daemon=True)
            server_thread.start()
            car_thread.start()

            def local_ws(url, **options):
                self.assertEqual(url, 'wss://pretzel.bavarian-aa.uk:8443')
                self.assertEqual(options['sslopt']['cert_reqs'], ssl.CERT_REQUIRED)
                self.assertTrue(options['sslopt']['check_hostname'])
                options['sslopt'] = {**options['sslopt'], 'ca_certs': str(certificate)}
                options['http_no_proxy'] = ['127.0.0.1']
                return websocket.create_connection('wss://127.0.0.1:%s/' % endpoint[1], **options)

            result = activate(RelaySettings('127.0.0.1'), ws_factory=local_ws,
                              car_factory=lambda *args, **kwargs: car_client, timeout=5)
            self.assertEqual(result, '127.0.0.1')
            for thread in (server_thread, car_thread):
                thread.join(5)
                self.assertFalse(thread.is_alive())
            self.assertEqual(seen, list(hello_frames()))
            if failures:
                raise failures[0]


if __name__ == '__main__':
    unittest.main()
