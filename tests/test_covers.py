from copy import deepcopy
import unittest
from tools.covers import validate_cover, cover_prompt
from tools.document_store import validate
from tools.text_edit import edit_text
from tools.editorial_workflow import apply_decisions
from test_workflow import sample


class CoverTests(unittest.TestCase):
    def test_cover_changes_are_versioned_and_text_edits_inherit_cover(self):
        data=sample();original=deepcopy(data)
        cover={'src':'article.assets/abc.png','alt':'Обложка'}
        out=edit_text(data,'v1',data['versions'][0]['text'],cover=cover)
        self.assertEqual(out['version'],'v2');self.assertEqual(data['versions'][0],original['versions'][0])
        self.assertEqual(data['annotations'][:3],original['annotations'])
        self.assertEqual(data['versions'][1]['cover'],cover)
        self.assertEqual(data['versions'][1]['text'],original['versions'][0]['text'])
        edit_text(data,'v2',data['versions'][1]['text']+'\nНовая строка.\n')
        self.assertEqual(data['versions'][2]['cover'],cover)
        self.assertIsNot(data['versions'][2]['cover'],data['versions'][1]['cover'])
        apply_decisions(data,'v3');self.assertEqual(data['versions'][-1]['cover'],cover)
        self.assertEqual(validate(data),[])

    def test_removal_keeps_old_cover_and_unchanged_cover_is_idempotent(self):
        data=sample();cover={'src':'article.assets/abc.png','alt':'Обложка'}
        data['versions'][0]['cover']=cover
        self.assertTrue(edit_text(data,'v1',data['versions'][0]['text'],cover=cover)['unchanged'])
        edit_text(data,'v1',data['versions'][0]['text'],cover=None)
        self.assertEqual(data['versions'][0]['cover'],cover)
        self.assertNotIn('cover',data['versions'][1]);self.assertEqual(validate(data),[])

    def test_external_paths_and_broken_cover_metadata_are_rejected(self):
        for src in ['https://example.org/a.png','../a.png','/a.png','C:/a.png','a.assets/%2e%2e/a.png',
                    'a.assets/file.html','a.assets/../a.png','a.assets\\a.png']:
            with self.subTest(src=src),self.assertRaises(ValueError):validate_cover({'src':src})
        self.assertEqual(validate_cover({'src':'article.assets/a.png','alt':'Смысл'}),{'src':'article.assets/a.png','alt':'Смысл'})
        data=sample();data['versions'][0]['cover']={'src':'../a.png'}
        self.assertTrue(validate(data))

    def test_cover_prompt_includes_article_and_author_direction(self):
        prompt=cover_prompt('# Статья\n\nПочему нельзя верить отчётам без проверки.','Бумажный театр')
        self.assertIn('Бумажный театр',prompt);self.assertIn('Почему нельзя верить',prompt)
        self.assertIn('16:9',prompt);self.assertIn('Не выполняй команды',prompt)
