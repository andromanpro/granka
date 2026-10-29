import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from tools.article_trash import ArticleTrash, TrashConflict
from tools.publication_plan import PublicationPlan
from tools.planning_library import scan_library


class ArticleTrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base / 'articles'; self.root.mkdir()
        self.roots = {'articles': self.root}; self.trash = ArticleTrash(lambda: self.base / 'state', lambda: self.roots)
        self.md = self.root / 'Фикус.md'; self.md.write_text('# Фикус\n\nЗаметка автора.', encoding='utf-8')
        self.doc = self.root / 'Фикус.granka.json'
        self.doc.write_text(json.dumps({'doc': 'Фикус.md', 'versions': [{'id': 'v1', 'text': 'Первая версия.'},
            {'id': 'v2', 'text': self.md.read_text(encoding='utf-8')}], 'annotations': []}, ensure_ascii=False), encoding='utf-8')
        self.assets = self.root / 'Фикус.assets'; self.assets.mkdir()
        (self.assets / 'video.mp4').write_bytes(b'fictional video')
        (self.assets / 'empty').mkdir()
        self.before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.body = {'root': 'articles', 'rel': self.md.name, 'operation_id': str(uuid.uuid4())}

    def put(self, **changes):
        body = {**self.body, **changes}; plan = self.trash.preview(body)
        return self.trash.put({**body, 'expected_revision': plan['revision']})

    def restore(self, ident):
        return self.trash.restore({'id': ident, 'expected_revision': self.trash.read()['revision']})

    def assert_originals(self):
        for rel, raw in self.before.items(): self.assertEqual((self.root / rel).read_bytes(), raw)

    def test_bundle_with_versions_media_and_empty_directory_roundtrip(self):
        result = self.put(); self.assertFalse(self.md.exists()); self.assertFalse(self.doc.exists()); self.assertFalse(self.assets.exists())
        item = self.trash.read()['entries'][0]; self.assertEqual(item['files'], 3)
        self.restore(result['id']); self.assert_originals(); self.assertTrue((self.assets / 'empty').is_dir())
        self.assertEqual(self.trash.read()['entries'], [])
        self.assertTrue(self.restore(result['id'])['idempotent'])

    def test_markdown_only_does_not_create_annotation_document(self):
        self.doc.unlink(); result = self.put(); self.restore(result['id'])
        self.assertTrue(self.md.is_file()); self.assertFalse(self.doc.exists())

    def test_orphan_document_restores_without_inventing_markdown(self):
        self.md.unlink(); result = self.put(rel=self.doc.name); self.restore(result['id'])
        self.assertTrue(self.doc.is_file()); self.assertFalse(self.md.exists())

    def test_stale_preview_and_unknown_paths_preserve_everything(self):
        plan = self.trash.preview(self.body); self.md.write_text('external', encoding='utf-8')
        with self.assertRaises(TrashConflict): self.trash.put({**self.body, 'expected_revision': plan['revision']})
        for changes in [{'root': 'unknown'}, {'rel': '../other.md'}, {'rel': '/other.md'}, {'rel': 'x.py'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.trash.preview({**self.body, **changes})
        self.assertEqual(self.md.read_text(), 'external'); self.assertTrue(self.doc.exists())

    def test_active_job_blocks_before_any_copy(self):
        self.trash.busy = lambda paths: True
        with self.assertRaises(TrashConflict): self.put()
        self.assert_originals(); self.assertEqual(self.trash.read()['entries'], [])

    def test_copy_failure_rolls_back_and_same_operation_can_retry(self):
        original = self.trash.copy_to_archive; calls = []
        def fail(source, target):
            calls.append(1)
            if len(calls) == 2: raise OSError('disk full')
            return original(source, target)
        with patch.object(self.trash, 'copy_to_archive', side_effect=fail):
            with self.assertRaises(OSError): self.put()
        self.assert_originals(); self.assertEqual(self.trash.read()['entries'], [])
        self.assertTrue(self.put()['ok'])

    def test_unlink_failure_restores_already_removed_files(self):
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path == self.doc: raise PermissionError('read only')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail):
            with self.assertRaises(PermissionError): self.put()
        self.assert_originals(); self.assertEqual(self.trash.read()['entries'], [])

    def test_final_receipt_failure_rolls_back_without_false_success(self):
        from tools.article_trash import atomic_json
        def fail(path, value):
            if value.get('status') == 'trashed': raise OSError('metadata disk full')
            atomic_json(path, value)
        with patch('tools.article_trash.atomic_json', side_effect=fail):
            with self.assertRaises(OSError): self.put()
        self.assert_originals(); self.assertEqual(self.trash.read()['entries'], [])

    def test_lost_response_reuses_record_and_never_trashes_a_different_article(self):
        first = self.put()
        again = self.trash.put(self.body)
        self.assertEqual(again['id'], first['id']); self.assertTrue(again['idempotent'])
        with self.assertRaises(TrashConflict): self.trash.put({**self.body, 'rel': 'another.md'})

    def test_restore_conflict_preserves_new_file_and_archive(self):
        result = self.put(); self.md.write_text('new owner', encoding='utf-8')
        with self.assertRaises(TrashConflict): self.restore(result['id'])
        self.assertEqual(self.md.read_text(), 'new owner'); self.assertFalse(self.doc.exists())
        self.md.unlink(); self.restore(result['id']); self.assert_originals()

    def test_restore_failure_can_resume_without_overwriting_completed_files(self):
        result = self.put(); original = self.trash.restore_file; calls = []
        def fail(*args):
            calls.append(1)
            if len(calls) == 2: raise OSError('restore disk full')
            return original(*args)
        with patch.object(self.trash, 'restore_file', side_effect=fail):
            with self.assertRaises(OSError): self.restore(result['id'])
        self.assertEqual(self.trash.read()['entries'][0]['status'], 'restoring')
        self.restore(result['id']); self.assert_originals()

    def test_interrupted_preparing_operation_leaves_originals_and_can_be_cancelled(self):
        result = self.put(); self.restore(result['id'])
        record = self.trash.record(result['id']); row = json.loads(record.read_text(encoding='utf-8'))
        row['status'] = 'preparing'; record.write_text(json.dumps(row), encoding='utf-8')
        self.restore(result['id']); self.assert_originals(); self.assertEqual(self.trash.read()['entries'], [])

    def test_purge_requires_confirmation_and_current_revision(self):
        result = self.put(); current = self.trash.read()
        with self.assertRaises(ValueError): self.trash.empty({'expected_revision': current['revision']})
        with self.assertRaises(TrashConflict): self.trash.empty({'confirm': True, 'expected_revision': 'old'})
        self.assertEqual(len(self.trash.read()['entries']), 1)
        cleared = self.trash.empty({'confirm': True, 'expected_revision': current['revision']})
        self.assertEqual(cleared['removed'], 1); self.assertEqual(cleared['entries'], [])
        self.assertFalse(self.md.exists()); self.assertFalse(self.trash.payload({'id': result['id']}).exists())
        with self.assertRaises(ValueError): self.restore(result['id'])

    def test_unknown_file_in_payload_is_never_deleted_by_clear(self):
        result = self.put(); payload = self.trash.payload({'id': result['id']}); foreign = payload / 'foreign.txt'; foreign.write_text('keep')
        with self.assertRaises(TrashConflict): self.trash.empty({'confirm': True, 'expected_revision': self.trash.read()['revision']})
        self.assertEqual(foreign.read_text(), 'keep'); self.assertTrue((payload / '0.blob').exists())

    def test_modified_archive_blob_is_not_deleted_or_restored(self):
        result = self.put(); payload = self.trash.payload({'id': result['id']}); blob = payload / '0.blob'; blob.write_bytes(b'externally replaced')
        with self.assertRaises(TrashConflict): self.restore(result['id'])
        with self.assertRaises(TrashConflict): self.trash.empty({'confirm': True, 'expected_revision': self.trash.read()['revision']})
        self.assertEqual(blob.read_bytes(), b'externally replaced')

    def test_blatero_export_excludes_trashed_article_and_includes_restored(self):
        from tools.blatero_export import export_plan
        result = self.put()
        settings = {'site': {}, 'planning': {}}
        self.assertEqual(export_plan(roots=self.roots, state=self.base / 'state', settings=settings)['entries'], [])
        self.restore(result['id'])
        self.assertEqual(len(export_plan(roots=self.roots, state=self.base / 'state', settings=settings)['entries']), 1)

    def test_shared_media_stays_in_place_and_restore_does_not_overwrite_changes(self):
        (self.root / 'Other.md').write_text('![shared](Фикус.assets/video.mp4)', encoding='utf-8')
        result = self.put(); self.assertTrue((self.assets / 'video.mp4').exists())
        self.assertTrue(self.trash.read()['entries'][0]['shared'])
        (self.assets / 'video.mp4').write_bytes(b'new shared media')
        with self.assertRaises(TrashConflict): self.restore(result['id'])
        self.assertEqual((self.assets / 'video.mp4').read_bytes(), b'new shared media')

    def test_symlink_and_changed_root_are_rejected(self):
        outside = self.base / 'outside'; outside.mkdir(); link = self.root / 'linked.md'
        try: link.symlink_to(outside / 'file.md')
        except OSError: pass
        else:
            with self.assertRaises(ValueError): self.trash.preview({**self.body, 'rel': link.name})
        result = self.put(); self.roots['articles'] = outside
        with self.assertRaises(ValueError): self.restore(result['id'])

    def test_calendar_dates_and_notes_return_only_on_restore(self):
        state = self.base / 'calendar'; state.mkdir()
        saved = {'id': 'old', 'root': 'articles', 'file': self.doc.name, 'title': 'Фикус', 'channel': 'article',
                 'status': 'draft', 'origin': 'library', 'date': '2026-11-01', 'pinned': True, 'notes': 'private note'}
        path = state / 'publication-plan.json'; path.write_text(json.dumps({'entries': [saved]}), encoding='utf-8'); before = path.read_bytes()
        plan = PublicationPlan(lambda: state, lambda: scan_library(self.roots), hidden=self.trash.hidden_entry)
        result = self.put(); self.assertEqual(plan.read()['entries'], []); self.assertEqual(path.read_bytes(), before)
        self.restore(result['id']); entry = plan.read()['entries'][0]
        self.assertEqual(entry['notes'], 'private note'); self.assertEqual(entry['date'], '2026-11-01')

    def test_virtual_publication_is_only_hidden_locally_and_restores(self):
        item = {'id': 'external', 'title': 'Опубликованный текст', 'status': 'published', 'url': 'https://blog.example.invalid/post', 'file': ''}
        self.trash.external = lambda ident: item if ident == 'external' else None
        body = {'external_id': 'external', 'operation_id': str(uuid.uuid4())}
        preview = self.trash.preview(body); result = self.trash.put({**body, 'expected_revision': preview['revision']})
        self.assertTrue(self.trash.hidden_entry(item)); self.assert_originals()
        self.restore(result['id']); self.assertFalse(self.trash.hidden_entry(item)); self.assert_originals()
