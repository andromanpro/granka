from copy import deepcopy
import unittest
from tools.text_edit import restore_version
from tools.document_store import validate


def document():
    return {'doc': 'Статья', 'status': 'applied', 'versions': [
        {'id': 'v1', 'text': '# Тема 😀\n\nПервая мысль.', 'cover': {'src': 'article.assets/old.png', 'alt': 'Старая обложка'}},
        {'id': 'v2', 'text': '# Тема 😀\n\nМысль после приправ.', 'origin': 'spices'},
        {'id': 'v3', 'text': '# Тема 😀\n\nЕщё одна правка.'}], 'annotations': [
            {'id': 'note', 'version': 'v1', 'start': 10, 'end': 16, 'quote': 'Первая', 'type': 'note', 'note': 'Моя пометка'},
            {'id': 'keep', 'version': 'v3', 'start': 10, 'end': 13, 'quote': 'Ещё', 'type': 'protected'}],
            'history': []}


class VersionHistoryTests(unittest.TestCase):
    def test_restore_keeps_old_versions_and_copies_snapshot_annotations_and_cover(self):
        data = document(); before = deepcopy(data); self.assertEqual(validate(data), [])
        out = restore_version(data, 'v1', 'v3')
        self.assertEqual(out['version'], 'v4'); self.assertEqual(data['versions'][:3], before['versions'])
        self.assertEqual(data['versions'][-1]['text'], before['versions'][0]['text'])
        self.assertEqual(data['versions'][-1]['cover'], before['versions'][0]['cover'])
        self.assertEqual(data['versions'][-1]['restored_from'], 'v1')
        self.assertEqual(data['versions'][-1]['parent_version'], 'v3')
        self.assertEqual(data['annotations'][:2], before['annotations'])
        clone = data['annotations'][-1]; self.assertEqual(clone['version'], 'v4'); self.assertEqual(clone['source_annotation'], 'note')
        self.assertEqual(clone['note'], 'Моя пометка'); self.assertNotEqual(clone['id'], 'note')
        self.assertEqual(data['status'], 'draft'); self.assertEqual(validate(data), [])

    def test_restore_removes_cover_when_historical_snapshot_has_none(self):
        data = document(); data['versions'][-1]['cover'] = data['versions'][0]['cover']
        restore_version(data, 'v2', 'v3')
        self.assertNotIn('cover', data['versions'][-1]); self.assertEqual(validate(data), [])

    def test_identical_text_still_restores_historical_notes_as_a_new_version(self):
        data = document(); data['versions'][-1]['text'] = data['versions'][0]['text']; data['annotations'] = data['annotations'][:1]
        data['versions'][-1]['cover'] = deepcopy(data['versions'][0]['cover'])
        out = restore_version(data, 'v1', 'v3')
        self.assertEqual(out['version'], 'v4'); self.assertEqual(data['annotations'][-1]['version'], 'v4')
        self.assertEqual(validate(data), [])

    def test_invalid_or_stale_base_does_not_mutate(self):
        for source, base in [('missing', 'v3'), ('v1', 'v2')]:
            data = document(); before = deepcopy(data)
            with self.subTest(source=source, base=base), self.assertRaises(ValueError):
                restore_version(data, source, base)
            self.assertEqual(data, before)

    def test_current_head_is_unchanged(self):
        data = document(); before = deepcopy(data)
        self.assertTrue(restore_version(data, 'v3', 'v3')['unchanged']); self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
