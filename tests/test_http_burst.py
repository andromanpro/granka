"""Static modules must be reachable even when connections arrive in a burst."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
import importlib.util
from pathlib import Path
import socket
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HTTPBurstTests(unittest.TestCase):
    def test_parallel_connections_wait_for_accept_instead_of_being_refused(self):
        spec = importlib.util.spec_from_file_location('granka_burst_test', ROOT/'granka-server.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        gate = threading.Event()

        class HeldAccept(module.ThreadingHTTPServer):
            def get_request(self):
                gate.wait(5)
                return super().get_request()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'module ready')

        server = HeldAccept(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        clients = []
        errors = []
        def connect(_):
            try: return socket.create_connection(server.server_address, timeout=1)
            except OSError as error: return error
        try:
            # While accept is deliberately paused, browsers can queue their full
            # import graph. This fails with the old backlog of five.
            with ThreadPoolExecutor(max_workers=20) as pool:
                for item in pool.map(connect, range(20)):
                    if isinstance(item, OSError): errors.append(str(item))
                    else: clients.append(item)
            gate.set()
            self.assertEqual(errors, [], errors)
            self.assertEqual(len(clients), 20)
            for client in clients:
                client.settimeout(3)
                client.sendall(b'GET /module.js HTTP/1.0\r\nHost: localhost\r\n\r\n')
            for client in clients:
                response = b''
                while part := client.recv(4096): response += part
                self.assertIn(b'200 OK', response)
                self.assertTrue(response.endswith(b'module ready'))
        finally:
            gate.set()
            for client in clients: client.close()
            server.shutdown(); server.server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
