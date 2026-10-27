"""HTTP framing, socket reuse and shutdown without interrupting active work."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class HTTPPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('granka_persistence_test', ROOT / 'granka-server.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'sample.mp4'
        self.media.write_bytes(b'0123456789')
        self.started = threading.Event()
        self.release = threading.Event()
        owner = self

        class Handler(self.module.H):
            def log_message(self, *args): pass
            def _do_GET(self):
                if self.path == '/sample':
                    self._media_file(owner.media)
                else:
                    super()._do_GET()
            def _do_POST(self):
                if self.path == '/held-write':
                    owner.started.set()
                    owner.release.wait(10)
                    owner.media.write_bytes(b'completed write')
                    self._send(200, {'ok': True})
                else:
                    super()._do_POST()

        self.http = self.module.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.module.ALLOWED_HOSTS = {f'127.0.0.1:{self.http.server_port}'}
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.release.set)
        self.env = patch.dict(os.environ, {'GRANKA_PACKAGED': '1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.state = patch.object(self.module.ai_provider, 'STATE', self.root)
        self.state.start(); self.addCleanup(self.state.stop)

    def connection(self):
        conn = http.client.HTTPConnection(*self.http.server_address, timeout=5)
        self.addCleanup(conn.close)
        return conn

    def quit(self):
        conn = self.connection()
        conn.request('POST', '/api/quit', json.dumps({'installation': str(ROOT)}),
                     {'Content-Type': 'application/json', 'X-Granka-Token': self.module.TOKEN})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def test_many_static_and_api_requests_reuse_one_socket(self):
        conn = self.connection()
        sock = None
        for index in range(40):
            conn.request('GET', '/api/runtime' if index % 2 else '/static/ui/voice.js')
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            body = response.read()
            self.assertEqual(len(body), int(response.getheader('Content-Length')))
            self.assertEqual(response.version, 11)
            self.assertFalse(response.will_close)
            if sock is None:
                sock = conn.sock
            self.assertIs(conn.sock, sock)

    def test_bad_post_closes_connection_with_unread_body(self):
        conn = self.connection()
        conn.request('POST', '/api/quit', b'{"unread":"body"}', {'Content-Type': 'application/json'})
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        self.assertEqual(response.getheader('Connection'), 'close')
        response.read()
        self.assertIsNone(conn.sock)
        self.assertFalse(self.http.stopping)

    def test_invalid_media_range_is_framed_for_next_request(self):
        conn = self.connection()
        for value in ['bytes=100-', 'invalid']:
            conn.request('GET', '/sample', headers={'Range': value})
            response = conn.getresponse()
            self.assertEqual(response.status, 416)
            self.assertEqual(response.getheader('Content-Length'), '0')
            self.assertEqual(response.read(), b'')
            sock = conn.sock
            conn.request('GET', '/sample', headers={'Range': 'bytes=2-5'})
            response = conn.getresponse()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b'2345')
            self.assertIs(conn.sock, sock)

    def test_idle_keepalive_does_not_hold_server_close(self):
        conn = self.connection()
        conn.request('GET', '/api/runtime')
        response = conn.getresponse(); response.read()
        self.assertIsNotNone(conn.sock)
        self.assertEqual(self.quit(), (200, {'ok': True}))
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        start = time.monotonic()
        self.http.server_close()
        self.assertLess(time.monotonic() - start, 1)

    def test_shutdown_refuses_active_write_then_finishes_safely(self):
        conn = self.connection()
        conn.request('POST', '/held-write', b'')
        self.assertTrue(self.started.wait(3))
        code, value = self.quit()
        self.assertEqual(code, 400)
        self.assertIn('завершения запросов', value['error'])
        self.assertFalse(self.http.stopping)
        self.release.set()
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        self.assertEqual(self.media.read_bytes(), b'completed write')
        self.assertEqual(self.quit(), (200, {'ok': True}))

    def test_shutdown_gate_rejects_new_requests(self):
        with patch.object(self.http, 'shutdown') as shutdown:
            self.assertEqual(self.quit(), (200, {'ok': True}))
            conn = self.connection()
            conn.request('GET', '/api/runtime')
            response = conn.getresponse()
            self.assertEqual(response.status, 503)
            self.assertEqual(response.getheader('Connection'), 'close')
            response.read()
            shutdown.assert_called_once()
