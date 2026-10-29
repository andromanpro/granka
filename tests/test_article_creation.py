import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from tools.article_creation import ArticleCreator, CreationConflict


class ArticleCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve(); self.root = self.base / 'articles'; self.root.mkdir()
        self.creator = ArticleCreator(lambda: self.base / 'state', lambda: {'articles': self.root})
        self.body = {'title': 'Фикус: проверка', 'root': 'articles', 'operation_id': str(uuid.uuid4())}

    def test_modes_and_private_metadata(self):
        for mode in ['blank', 'outline', 'notes']:
            with self.subTest(mode=mode):
                b = {**self.body, 'operation_id': str(uuid.uuid4()), 'mode': mode, 'notes': 'Первая заметка.\nВторая строка. 😀', 'audience': 'Секретная аудитория', 'purpose': 'Секретная цель'}
                result = self.creator.create(b)
                md, doc = map(Path, result['files']); data = json.loads(doc.read_text(encoding='utf-8'))
                self.assertNotIn('Секретная', md.read_text(encoding='utf-8'))
                self.assertEqual(data['intent']['purpose'], b['purpose'])
                self.assertEqual(data['versions'][0]['text'], md.read_text(encoding='utf-8'))
                self.assertEqual(data['status'], 'draft')
                if mode == 'outline': self.assertIn('## Что получилось', data['versions'][0]['text'])
                if mode == 'notes': self.assertIn(b['notes'], data['versions'][0]['text'])
        self.assertEqual(len(list(self.root.glob('*.md'))), 3)

    def test_retry_after_lost_reply_is_exactly_one_document(self):
        first = self.creator.create(self.body)
        self.assertTrue(self.creator.create(self.body)['idempotent'])
        self.assertEqual(len(list(self.root.glob('*.md'))), 1)
        with self.assertRaises(CreationConflict): self.creator.create({**self.body, 'title': 'Другой текст'})
        self.assertEqual(self.creator.selectors(), {'articles': [first['source_rel']]})

    def test_existing_files_and_preview_race_never_overwrite(self):
        (self.root / 'Фикус- проверка.md').write_text('old', encoding='utf-8')
        plan = self.creator.preview(self.body)
        self.assertEqual(plan['filename'], 'Фикус- проверка-2')
        (self.root / (plan['filename'] + '.granka.json')).write_text('foreign', encoding='utf-8')
        with self.assertRaises(CreationConflict): self.creator.create({**self.body, 'filename': plan['filename']})
        self.assertEqual((self.root / 'Фикус- проверка.md').read_text(), 'old')

    def test_second_file_failure_rolls_back_and_same_request_recovers(self):
        original = self.creator.exclusive_write
        def fail(path, raw):
            if path.name.endswith('.granka.json'): raise OSError('disk full')
            return original(path, raw)
        with patch.object(self.creator, 'exclusive_write', side_effect=fail):
            with self.assertRaises(OSError): self.creator.create(self.body)
        self.assertEqual(list(self.root.iterdir()), [])
        result = self.creator.create(self.body)
        self.assertEqual(len(result['files']), 2)
        self.assertEqual(len(list(self.root.glob('*.md'))), 1)

    def test_interrupted_pending_pair_recovers_without_new_name(self):
        result = self.creator.create(self.body)
        receipt = self.creator.directory / (self.body['operation_id'] + '.json')
        data = json.loads(receipt.read_text()); data['status'] = 'pending'; receipt.write_text(json.dumps(data), encoding='utf-8')
        Path(result['files'][1]).unlink()
        self.assertEqual(self.creator.create(self.body)['files'], result['files'])
        self.assertEqual(len(list(self.root.glob('*.md'))), 1)

    def test_pending_operation_preserves_external_edit(self):
        result = self.creator.create(self.body)
        receipt = self.creator.directory / (self.body['operation_id'] + '.json')
        data = json.loads(receipt.read_text()); data['status'] = 'pending'; receipt.write_text(json.dumps(data), encoding='utf-8')
        Path(result['files'][0]).write_text('external', encoding='utf-8')
        with self.assertRaises(CreationConflict): self.creator.create(self.body)
        self.assertEqual(Path(result['files'][0]).read_text(), 'external')

    def test_bad_inputs_and_unknown_or_escaping_folder(self):
        for change in [{'title': ''}, {'title': 'x' * 181}, {'title': 'NUL'}, {'title': 'bad\x00title'},
                       {'root': 'unknown'}, {'folder': '../outside'}, {'folder': '/outside'},
                       {'folder': 'missing'}, {'filename': '../other'}, {'mode': 'unknown'}, {'notes': 'x\x00'}]:
            with self.subTest(change=change), self.assertRaises(ValueError): self.creator.create({**self.body, **change})
        self.assertFalse(list(self.root.glob('*.md')))

    def test_root_without_directory_is_supported(self):
        self.root.rmdir()
        self.assertEqual(len(self.creator.create(self.body)['files']), 2)

    def test_first_file_permission_and_flush_failure_leave_no_partial_article(self):
        with patch.object(self.creator, 'exclusive_write', side_effect=PermissionError('read only')):
            with self.assertRaises(PermissionError): self.creator.create(self.body)
        self.assertEqual(list(self.root.iterdir()), [])
        with patch('tools.article_creation.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.creator.create(self.body)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue(self.creator.create(self.body)['ok'])

    def test_failed_commit_receipt_rolls_back_pair(self):
        from tools.article_creation import atomic_json
        def fail(path, data):
            if data.get('status') == 'created': raise OSError('receipt disk full')
            atomic_json(path, data)
        with patch('tools.article_creation.atomic_json', side_effect=fail):
            with self.assertRaises(OSError): self.creator.create(self.body)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertTrue(self.creator.create(self.body)['ok'])

    def test_link_outside_root_is_rejected(self):
        outside = self.base / 'outside'; outside.mkdir(); link = self.root / 'linked'
        try: link.symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest('Symlink creation not available for this account')
        with self.assertRaises(ValueError): self.creator.create({**self.body, 'folder': 'linked'})
        self.assertEqual(list(outside.iterdir()), [])
