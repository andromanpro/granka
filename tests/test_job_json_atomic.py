import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from tools import ai_provider
from tools.jobs import Jobs


class JobJsonAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.file = self.root / 'job.json'
        self.file.write_text('{"status":"running"}', encoding='utf-8')

    @staticmethod
    def denied():
        error = PermissionError('Windows sharing violation'); error.winerror = 32; return error

    def test_transient_reader_retries_only_rename(self):
        replace = ai_provider.os.replace; attempts = []
        def blocked(source, destination):
            attempts.append(1)
            if len(attempts) < 3: raise self.denied()
            replace(source, destination)
        with patch('tools.ai_provider._WINDOWS', True), patch('tools.ai_provider.os.replace', side_effect=blocked), patch('tools.ai_provider.time.sleep'):
            ai_provider.atomic_json(self.file, {'status': 'succeeded'})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(json.loads(self.file.read_text())['status'], 'succeeded')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_permanent_denial_keeps_old_file_and_cleans_temporary(self):
        with patch('tools.ai_provider._WINDOWS', True), patch('tools.ai_provider.os.replace', side_effect=self.denied()) as replace, patch('tools.ai_provider.time.sleep'):
            with self.assertRaises(PermissionError): ai_provider.atomic_json(self.file, {'status': 'succeeded'})
        self.assertEqual(replace.call_count, 8)
        self.assertEqual(json.loads(self.file.read_text())['status'], 'running')
        self.assertFalse(list(self.root.glob('*.tmp')))

    def test_other_os_does_not_retry_permission_failure(self):
        with patch('tools.ai_provider._WINDOWS', False), patch('tools.ai_provider.os.replace', side_effect=self.denied()) as replace:
            with self.assertRaises(PermissionError): ai_provider.atomic_json(self.file, {})
        self.assertEqual(replace.call_count, 1)

    def test_final_job_status_survives_reader_without_repeating_work(self):
        jobs = Jobs(self.root / 'jobs'); self.addCleanup(jobs.pool.shutdown, wait=True)
        replace = ai_provider.os.replace; denied = []; calls = []
        def blocked(source, destination):
            if Path(destination).name == 'job.json' and json.loads(Path(source).read_text())['status'] == 'succeeded' and not denied:
                denied.append(True); raise self.denied()
            replace(source, destination)
        with patch('tools.ai_provider._WINDOWS', True), patch('tools.ai_provider.os.replace', side_effect=blocked):
            job = jobs.submit('article-draft', 'new', 'fixture', 'once', {}, lambda directory: calls.append(1) or {'text': 'Готово'})
            for _ in range(100):
                current = jobs.get(job['id'])
                if current['status'] == 'succeeded': break
                time.sleep(.01)
        self.assertEqual(current['status'], 'succeeded')
        self.assertEqual(calls, [1]); self.assertEqual(denied, [True])

    def test_status_read_retries_transient_windows_replacement_denial(self):
        jobs = Jobs(self.root / 'jobs'); self.addCleanup(jobs.pool.shutdown, wait=True)
        ident = 'a' * 24; path = jobs.directory / ident / 'job.json'; path.parent.mkdir()
        path.write_text(json.dumps({'id': ident, 'kind': 'article-draft', 'status': 'failed', 'message': 'Отказ модели'}), encoding='utf-8')
        original = Path.read_text; calls = []
        def read(target, *args, **kwargs):
            if target == path:
                calls.append(1)
                if len(calls) < 3: raise self.denied()
            return original(target, *args, **kwargs)
        with patch('tools.jobs._WINDOWS', True), patch.object(Path, 'read_text', read), patch('tools.jobs.time.sleep'):
            result = jobs.get(ident)
        self.assertEqual(len(calls), 3); self.assertEqual(result['message'], 'Отказ модели')
        self.assertFalse(list(jobs.directory.glob('*/answer.json')))
