"""Distribution boundaries and first launch without touching personal files."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import zipfile

from tools import config, desktop_runtime, ai_provider

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('granka_windows_build', ROOT / 'tools/windows/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class WindowsDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def fixture_app(self):
        app = self.root / 'app'
        for name in builder.ROOT_FILES + ['tools/config.py', 'static/index.html',
                'examples/review.md', 'examples/review.granka.json', 'examples/Начало работы.md']:
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Учебный пример', encoding='utf-8')
        return app

    def test_explicit_payload_excludes_local_config_corpus_and_build_outputs(self):
        app = self.fixture_app()
        for name in ['granka-config.json', 'private/token.txt', 'voice-corpus/personal.md',
                     '_audit/log.txt', '_dist/old.exe', 'tools/windows/dependencies.lock.json']:
            path = app / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private fixture', encoding='utf-8')
        destination = self.root / 'payload'
        builder.copy_app(app, destination)
        self.assertTrue((destination / 'static/index.html').is_file())
        self.assertTrue((destination / 'examples/review.md').is_file())
        self.assertEqual(set(p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()),
                         set(p.relative_to(app).as_posix() for p in builder.app_files(app)))
        self.assertFalse((destination / 'granka-config.json').exists())
        self.assertFalse((destination / 'private').exists())

    def test_payload_audit_rejects_private_literal(self):
        app = self.fixture_app()
        (app / 'tools/config.py').write_text('address=' + '192.' + '168.77.22', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'audit rejected'):
            builder.copy_app(app, self.root / 'payload')

    def test_download_rejects_wrong_hash_and_never_promotes_partial_file(self):
        item = {'url': 'https://download.example.invalid/python.zip', 'sha256': '0' * 64}
        with patch.object(builder.urllib.request, 'urlopen', return_value=io.BytesIO(b'corrupt download')):
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                builder.fetch(item, self.root)
        self.assertFalse((self.root / 'python.zip').exists())

    def test_extraction_rejects_path_escape(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            out.writestr('../escaped.txt', 'wrong')
        with self.assertRaisesRegex(ValueError, 'Unsafe archive'):
            builder.extract(archive, self.root / 'target')
        self.assertFalse((self.root / 'escaped.txt').exists())

    def test_first_run_seeds_once_and_never_replaces_user_text(self):
        app = self.fixture_app()
        state = self.root / 'state'
        with patch.dict(os.environ, {'GRANKA_STATE_DIR': str(state), 'GRANKA_CONFIG': str(state / 'settings.json')}), \
                patch.object(config, 'CONFIG_FILE', state / 'settings.json'):
            self.assertEqual(desktop_runtime.prepare(app), state)
            source = state / 'articles/review.md'
            self.assertTrue(source.exists())
            source.write_text('Мой изменённый текст', encoding='utf-8')
            desktop_runtime.prepare(app)
            self.assertEqual(source.read_text(encoding='utf-8'), 'Мой изменённый текст')

    def test_existing_configuration_suppresses_automatic_demo(self):
        app = self.fixture_app()
        state = self.root / 'existing'
        state.mkdir()
        settings = state / 'settings.json'
        settings.write_text(json.dumps({'roots': {'mine': str(self.root / 'my-articles')}}), encoding='utf-8')
        with patch.dict(os.environ, {'GRANKA_STATE_DIR': str(state), 'GRANKA_CONFIG': str(settings)}), \
                patch.object(config, 'CONFIG_FILE', settings):
            desktop_runtime.prepare(app)
        self.assertFalse((state / 'articles/review.md').exists())

    def test_packaged_codex_does_not_depend_on_path(self):
        app = self.root / 'bundle/app'
        app.mkdir(parents=True)
        binary = app.parent / 'codex/codex.exe'
        binary.parent.mkdir()
        binary.write_bytes(b'fixture')
        with patch.object(ai_provider, 'HERE', app), patch.object(config, 'load', return_value={'codex_binary': ''}), \
                patch.object(ai_provider.shutil, 'which', return_value=None):
            self.assertEqual(ai_provider.codex_binary(), str(binary))

    def test_shutdown_detects_jobs_across_all_articles(self):
        job = self.root / 'job-one/job.json'
        job.parent.mkdir()
        for status, expected in [('queued', True), ('running', True), ('succeeded', False), ('failed', False)]:
            job.write_text(json.dumps({'status': status}), encoding='utf-8')
            self.assertEqual(desktop_runtime.active_jobs(self.root), expected)

    def test_stop_never_sends_shutdown_to_another_installation(self):
        response = io.BytesIO(json.dumps({'packaged': True, 'installation': 'another-copy'}).encode())
        with patch.object(desktop_runtime.urllib.request, 'build_opener') as build_opener, \
                patch.object(config, 'load', return_value={'server': {'port': 8901}}):
            build_opener.return_value.open.return_value = response
            with self.assertRaisesRegex(RuntimeError, 'другая копия'):
                desktop_runtime.stop_server(self.root / 'this-copy')
            self.assertEqual(build_opener.return_value.open.call_count, 1)


class PackagedShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('granka_distribution_server_test', ROOT / 'granka-server.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.module.H.log_message = lambda *args: None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'GRANKA_PACKAGED': '1'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.state = patch.object(self.module.ai_provider, 'STATE', self.root)
        self.state.start(); self.addCleanup(self.state.stop)
        self.http = self.module.ThreadingHTTPServer(('127.0.0.1', 0), self.module.H)
        self.module.ALLOWED_HOSTS = {f'127.0.0.1:{self.http.server_port}'}
        self.base = f'http://127.0.0.1:{self.http.server_port}'
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, body, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Granka-Token'] = self.module.TOKEN
        request = urllib.request.Request(self.base + '/api/quit', data=json.dumps(body).encode(), headers=headers)
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_shutdown_requires_token_and_own_installation(self):
        with patch.object(self.http, 'shutdown') as shutdown:
            self.assertEqual(self.request({'installation': str(ROOT)}, token=False)[0], 403)
            self.assertEqual(self.request({'installation': str(self.root / 'foreign')})[0], 400)
            shutdown.assert_not_called()

    def test_shutdown_is_blocked_by_active_job(self):
        job = self.root / 'jobs/job-one/job.json'
        job.parent.mkdir(parents=True)
        job.write_text(json.dumps({'status': 'running'}), encoding='utf-8')
        with patch.object(self.http, 'shutdown') as shutdown:
            status, value = self.request({'installation': str(ROOT)})
            self.assertEqual(status, 400)
            self.assertIn('завершения заданий', value['error'])
            shutdown.assert_not_called()

    def test_shutdown_acknowledges_then_stops_server(self):
        called = threading.Event()
        with patch.object(self.http, 'shutdown', side_effect=called.set):
            self.assertEqual(self.request({'installation': str(ROOT)}), (200, {'ok': True}))
            self.assertTrue(called.wait(2))


if __name__ == '__main__':
    unittest.main()
