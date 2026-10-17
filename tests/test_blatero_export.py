import json
from pathlib import Path
import tempfile
import unittest

from tools.blatero_export import export_plan
from tools.library_locations import LibraryLocations
from unittest.mock import patch


class BlateroExportTests(unittest.TestCase):
    def test_published_source_includes_verified_link_and_date(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);article=base/'published.md'
            article.write_text('---\nstatus: publish\ndate: 2026-09-20\nurl: https://example.invalid/post\n---\n# Статья\n\nТекст.',encoding='utf-8')
            result=export_plan({'articles':base},base/'state',{'server':{},'site':{},'planning':{}})
            item=result['entries'][0]
            self.assertEqual((item['status'],item['date'],item['url']),('published','2026-09-20','https://example.invalid/post'))
            self.assertFalse((base/'state/publication-plan.json').exists())

    def test_user_selected_file_is_shared_with_board_without_scanning_neighbors(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);articles=base/'external';articles.mkdir()
            first=articles/'one.md';first.write_text('# Один\n\nПервый текст.',encoding='utf-8')
            (articles/'two.md').write_text('# Два\n\nВторой текст.',encoding='utf-8')
            locations=LibraryLocations(lambda:base/'state')
            locations.add(str(first),'file',locations.read()['revision'])
            before=locations.file.read_bytes()
            with patch('tools.blatero_export.config.roots',return_value={'articles':base/'empty'}):
                result=export_plan(state=base/'state',settings={'server':{},'site':{},'planning':{}})
            self.assertEqual([e['title'] for e in result['entries']],['Один'])
            self.assertEqual(locations.file.read_bytes(),before)

    def test_export_uses_canonical_plan_preserves_files_and_omits_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            articles = base / 'articles'
            articles.mkdir()
            markdown = articles / 'заметка.md'
            markdown.write_text('# Проверенный заголовок\n\nНастоящий текст статьи.', encoding='utf-8')
            settings = {'server':{'port':8901}, 'site':{}, 'planning':{}, 'token':'secret-value'}
            first = export_plan({'articles':articles}, base, settings)
            entry = first['entries'][0]
            (base / 'publication-plan.json').write_text(json.dumps({'entries':[{'id':entry['id'], 'title':entry['title'], 'date':'2026-10-12', 'channel':'article', 'status':'ready', 'root':'articles', 'file':entry['file'], 'origin':'library'}]}), encoding='utf-8')
            before = {p:p.read_bytes() for p in base.rglob('*') if p.is_file()}
            exported = export_plan({'articles':articles}, base, settings)
            self.assertEqual(exported['entries'][0]['date'], '2026-10-12')
            self.assertEqual(exported['entries'][0]['status'], 'ready')
            self.assertEqual(exported['entries'][0]['id'], entry['id'])
            self.assertNotIn('secret-value', json.dumps(exported))
            self.assertEqual(before, {p:p.read_bytes() for p in before})
            self.assertEqual(set(before), {p for p in base.rglob('*') if p.is_file()})
