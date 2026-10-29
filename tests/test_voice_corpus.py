import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.voice_corpus import VoiceCorpus
from tools import iimeter
from tools.site_connections import SettingsConflict


class VoiceCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.corpus = self.base / 'corpus'; self.corpus.mkdir()
        p = patch.object(iimeter, 'CORPUS_DIR', self.corpus); p.start(); self.addCleanup(p.stop)
        self.service = VoiceCorpus(lambda: self.base / 'state')
        self.text = 'Я проверил датчик. Важно отметить: фикус пока жив. ' * 10

    def test_missing_empty_unsuitable_and_local_axes(self):
        self.assertEqual(self.service.measure(self.text, 'blog')['corpus_state']['status'], 'empty')
        (self.corpus / 'note.txt').write_text('не markdown', encoding='utf-8')
        value = self.service.measure(self.text, 'blog')
        self.assertEqual(value['corpus_state']['status'], 'unsuitable'); self.assertIsNone(value['score'])
        self.assertGreater(value['axes']['маркеры']['hits'], 0)
        self.assertIsInstance(value['axes']['структура']['score'], int)
        self.assertEqual(self.service.measure(self.text, 'talks')['corpus_state']['status'], 'not_configured')

    def test_live_add_edit_delete_and_profiles_do_not_reuse_old_score(self):
        (self.corpus / 'one.md').write_text(self.text, encoding='utf-8')
        a = self.service.measure(self.text, 'blog'); self.assertEqual(a['status'], 'ok')
        (self.corpus / 'two.md').write_text('Сегодня я принёс новую лейку. ' * 30, encoding='utf-8')
        b = self.service.measure(self.text, 'blog'); self.assertNotEqual(a['profile_fingerprint'], b['profile_fingerprint'])
        self.assertEqual(b['corpus_state']['files'], 2)
        self.assertIsNone(self.service.measure(self.text, 'talks')['score'])
        for p in self.corpus.glob('*.md'): p.unlink()
        self.assertIsNone(self.service.measure(self.text, 'blog')['score'])

    def test_error_has_reason_and_recovers(self):
        path = self.corpus / 'bad.md'; path.write_bytes(b'\xff\xfe')
        result = self.service.measure(self.text, 'blog')
        self.assertEqual(result['corpus_state']['status'], 'read_error'); self.assertIn('прочитать', result['corpus_state']['reason'])
        self.assertIsNone(result['score']); self.assertGreater(result['axes']['маркеры']['hits'], 0)
        path.write_text(self.text, encoding='utf-8')
        self.assertEqual(self.service.measure(self.text, 'blog')['status'], 'ok')

    def test_permission_error_is_not_silently_empty(self):
        with patch.object(Path, 'iterdir', side_effect=PermissionError('Нет доступа')):
            self.assertEqual(self.service.measure(self.text, 'blog')['corpus_state']['status'], 'read_error')

    def test_directory_stat_permission_error_is_not_missing_corpus(self):
        original = Path.stat
        def checked(path, *args, **kwargs):
            if path == self.corpus: raise PermissionError('Нет доступа к папке')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', checked):
            self.assertEqual(self.service.measure(self.text, 'blog')['corpus_state']['status'], 'read_error')

    def test_select_is_revision_guarded_and_corpus_is_unchanged(self):
        path = self.corpus / 'one.md'; path.write_text(self.text, encoding='utf-8'); before = path.read_bytes()
        revision = self.service.read()['revision']
        self.service.save({'directory': str(self.corpus), 'expected_revision': revision})
        with self.assertRaises(SettingsConflict): self.service.save({'directory': str(self.corpus), 'expected_revision': revision})
        self.service.measure(self.text, 'blog'); self.assertEqual(path.read_bytes(), before)
        for value in ['', 'relative', str(self.base / 'missing')]:
            with self.subTest(value=value), self.assertRaises(ValueError): self.service.save({'directory': value, 'expected_revision': self.service.read()['revision']})

    def test_empty_prose_and_short_sample(self):
        (self.corpus / 'one.md').write_text(self.text, encoding='utf-8')
        self.assertEqual(self.service.measure('```\ncode\n```', 'blog')['status'], 'insufficient_text')
        short = self.service.measure('Фикус жив.', 'blog')
        self.assertIsNotNone(short['sample_warning']); self.assertEqual(short['status'], 'ok')

    def test_corrupt_derived_profile_is_rebuilt_without_touching_corpus(self):
        source = self.corpus / 'one.md'; source.write_text(self.text, encoding='utf-8')
        self.service.measure(self.text, 'blog')
        cache = self.base / 'state/profiles/selected-blog.json'
        for raw in ['not json', '[]', '{"fingerprint":"bad"}']:
            cache.write_text(raw, encoding='utf-8')
            self.assertEqual(self.service.measure(self.text, 'blog')['status'], 'ok')
        self.assertEqual(source.read_text(encoding='utf-8'), self.text)
