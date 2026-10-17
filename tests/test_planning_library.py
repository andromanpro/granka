import json
from pathlib import Path
import tempfile
import unittest
from tools.planning_library import scan_library, source_readiness, catalog_sources
from tools.publication_plan import PublicationPlan, PlanConflict


class LibraryCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.articles = self.root / 'articles'; self.articles.mkdir()
        self.roots = {'articles': self.articles}
        self.library = lambda: scan_library(self.roots)
        self.plan = PublicationPlan(lambda: self.root / 'state', self.library)

    def tearDown(self): self.temp.cleanup()

    def draft(self, name='draft.md', text='# Тема\n\nТекст статьи.'):
        path = self.articles / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8'); return path

    def schedule(self, apply=False):
        return self.plan.schedule({'expected_revision': self.plan.read()['revision'],
            'options': {'start': '2026-09-21', 'weekdays': [0, 1, 2, 3, 4], 'capacity': 1}}, apply=apply)

    def action(self, identifier, action, **args):
        return self.plan.change({'id': identifier, 'action': action, 'expected_revision': self.plan.read()['revision'], **args})

    def test_raw_markdown_enters_distribution_without_opening_editor(self):
        path = self.draft(text='---\nstatus: draft\ndate: 2020-01-01\n---\n# Тема\n\nТекст.'); raw = path.read_bytes()
        self.assertEqual(len(self.plan.read()['entries']), 1)
        preview = self.schedule()
        self.assertEqual(len(preview['assignments']), 1)
        self.assertFalse(self.plan.path.exists())
        result = self.schedule(apply=True)
        self.assertEqual(result['entries'][0]['date'], '2026-09-21')
        self.assertEqual(PublicationPlan(lambda: self.root / 'state', self.library).read(), result)
        self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(path.with_suffix('.granka.json').exists())

    def test_exclude_restore_move_and_unschedule_do_not_modify_article(self):
        path = self.draft(); raw = path.read_bytes()
        item = self.schedule(apply=True)['entries'][0]
        result = self.action(item['id'], 'exclude'); excluded = result['entries'][0]
        self.assertTrue(excluded['excluded']); self.assertIsNone(excluded['date'])
        self.assertEqual(self.schedule()['assignments'], [])
        item = self.action(item['id'], 'include')['entries'][0]
        self.assertFalse(item['excluded'])
        self.action(item['id'], 'save', entry={**item, 'date': '2026-09-25'})
        self.assertEqual(self.plan.read()['entries'][0]['date'], '2026-09-25')
        self.action(item['id'], 'unschedule')
        self.assertIsNone(self.plan.read()['entries'][0]['date'])
        self.assertEqual(len(self.schedule()['assignments']), 1)
        self.assertEqual(path.read_bytes(), raw)

    def test_opening_draft_in_editor_keeps_id_and_exclusion(self):
        path = self.draft(); item = self.plan.read()['entries'][0]
        self.action(item['id'], 'exclude')
        path.with_suffix('.granka.json').write_text(json.dumps({'versions': [{'id':'v1','text':path.read_text(encoding='utf-8')}], 'annotations': []}), encoding='utf-8')
        result = self.plan.read()['entries']
        self.assertEqual(len(result), 1); self.assertEqual(result[0]['id'], item['id'])
        self.assertTrue(result[0]['has_granka']); self.assertTrue(result[0]['excluded'])

    def test_published_and_support_files_are_not_drafts_and_scope_is_configured(self):
        self.draft('README.md'); self.draft('draft.assets/prompt.md')
        self.draft('published.md', '---\nstatus: publish\n---\n# Выпущено\n\nТекст.')
        self.draft('drafts/article.md'); self.draft('notes/internal.md')
        rows = scan_library(self.roots, {'articles':['drafts']})
        self.assertEqual([r['source_rel'] for r in rows], ['drafts/article.md'])
        self.assertEqual(len(self.library()), 2)
        with self.assertRaises(ValueError): scan_library(self.roots, {'articles':['../']})

    def test_source_changes_invalidate_distribution_and_missing_source_is_not_scheduled(self):
        path = self.draft(); state = self.plan.read()
        self.schedule()
        path.write_text('# Изменено\n\nДругая статья.', encoding='utf-8')
        with self.assertRaises(PlanConflict):
            self.plan.schedule({'expected_revision': state['revision'], 'options': {'start':'2026-09-21'}}, apply=True)
        item = self.plan.read()['entries'][0]
        self.action(item['id'], 'save', entry={**item, 'notes':'В плане'})
        path.unlink()
        self.assertIn('source_error', self.plan.read()['entries'][0])
        self.assertEqual(self.schedule()['assignments'], [])

    def test_readiness_explains_steps_and_can_be_overridden_by_author(self):
        text = '# Тема\n\nТекст.\n\n[ЛИЧНОЕ: добавь пример]'
        path = self.draft(text=text)
        self.assertEqual(self.plan.read()['entries'][0]['readiness']['percent'], 25)
        clean = '# Тема\n\nТекст.'
        checked = {'versions':[{'id':'v1','text':clean}], 'annotations':[], 'analyses':[{'version':'v1'}]}
        self.assertEqual(source_readiness(clean, checked)['percent'], 75)
        checked['analyses'][0]['scope']='editorial'
        self.assertEqual(source_readiness(clean, checked)['percent'], 50)
        checked['analyses'][0].pop('scope')
        checked['versions'].append({'id':'v2','text':clean+' Правка.'})
        self.assertEqual(source_readiness(checked['versions'][-1]['text'], checked)['percent'], 50)
        item = self.plan.read()['entries'][0]
        updated = self.action(item['id'], 'save', entry={**item, 'progress':80})['entries'][0]
        self.assertEqual(updated['readiness']['percent'], 80)
        self.assertEqual(updated['readiness']['basis'], 'author')
        self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_material_with_higher_preparation_is_scheduled_first(self):
        self.draft('a.md', '# Начато\n\nТекст.\n\n[ЛИЧНОЕ: добавь случай]')
        self.draft('b.md', '# Дописано\n\nТекст готов.')
        self.assertEqual([a['title'] for a in self.schedule()['assignments']], ['Дописано', 'Начато'])

    def test_catalog_groups_export_copy_and_keeps_original_files_accessible(self):
        self.draft('original.md', '# Статья автора\n\nТекст статьи.')
        repo=self.root/'blog';posts=repo/'posts';posts.mkdir(parents=True)
        (posts/'export.md').write_text('---\ntitle: Статья автора\nslug: article\nstatus: publish\n---\nТекст статьи.',encoding='utf-8')
        (posts/'duplicate.md').write_text('---\ntitle: Статья автора\nslug: article\nstatus: publish\n---\nТекст статьи.',encoding='utf-8')
        roots={**self.roots,'blog':posts}
        receipt=self.root/'receipts'/'one';receipt.mkdir(parents=True)
        (receipt/'plan.json').write_text(json.dumps({'root':'articles','rel':'original.granka.json','file':'posts/export.md','stages':{'nas':{'status':'succeeded'}}}),encoding='utf-8')
        rows=catalog_sources(scan_library(roots,include_published=True),[],receipt.parent,repo,roots)
        original=next(r for r in rows if r['root']=='articles')
        self.assertEqual(len(rows),1)
        self.assertEqual({c['rel'] for c in original['copies']},{'export.md','duplicate.md'})
        self.assertTrue(all((roots[r['root']]/r['rel']).is_file() for r in [*rows,*original['copies']]))
        # Identical plain drafts collapse in both consumers using the same catalog.
        self.draft('same.md', '# Статья автора\n\nТекст статьи.')
        rows=catalog_sources(scan_library(self.roots),[])
        self.assertEqual(len(rows),1);self.assertEqual(len(rows[0]['copies']),1)

    def test_published_metadata_reaches_calendar_library_and_is_not_scheduled(self):
        self.draft('published.md','---\nstatus: publish\ndate: 2026-09-20\nurl: https://example.invalid/post\n---\n# Выпущено\n\nТекст.')
        sources=lambda:scan_library(self.roots,include_published=True)
        plan=PublicationPlan(lambda:self.root/'state',sources)
        current=plan.read();item=current['entries'][0]
        self.assertEqual((item['status'],item['date'],item['url']),('published','2026-09-20','https://example.invalid/post'))
        self.assertEqual(plan.schedule({'expected_revision':current['revision'],'options':{'start':'2026-09-21'}})['assignments'],[])
        row=catalog_sources(sources(),current['entries'])[0]
        self.assertEqual((row['state'],row['published_date'],row['url'],row['readiness']['percent']),('published','2026-09-20','https://example.invalid/post',100))
        self.assertFalse(plan.path.exists())

    def test_confirmed_calendar_link_groups_cross_root_publication_copies(self):
        self.draft('article.md','# Статья\n\nАвторская редакция.')
        other=self.root/'other';other.mkdir();(other/'export.md').write_text('# Статья\n\nПубликационная редакция.',encoding='utf-8')
        roots={**self.roots,'other':other}
        sources=scan_library(roots)
        links=[{**s,'status':'published','date':'2026-09-20','url':'https://example.invalid/post'} for s in sources]
        rows=catalog_sources(sources,links)
        self.assertEqual(len(rows),1);self.assertEqual(len(rows[0]['copies']),1)
        self.assertEqual(rows[0]['state'],'published')
        self.assertEqual(rows[0]['url'],'https://example.invalid/post')

    def test_link_only_publications_are_visible_once_and_bad_urls_are_not_links(self):
        self.draft()
        published={'id':'external','title':'Внешний выпуск','status':'published','date':'2026-09-20',
                   'url':'https://example.invalid/released','file':'','root':'','channel':'article'}
        bad={**published,'id':'bad','url':'javascript:alert(1)'}
        rows=catalog_sources(self.library(),[published,{**published,'id':'copy'},bad])
        external=[r for r in rows if r.get('external')]
        self.assertEqual(len(external),1);self.assertEqual(external[0]['url'],published['url'])
        self.draft('unsafe.md','---\nstatus: publish\nurl: "javascript:alert(1)"\n---\n# Текст\n\nСтатья.')
        self.assertEqual(next(s for s in scan_library(self.roots,include_published=True) if s['source_rel']=='unsafe.md')['url'],'')

    def test_different_publication_addresses_do_not_merge_on_decoded_reserved_characters(self):
        self.draft('one.md','# Первый материал\n\nПервый текст.')
        self.draft('two.md','# Второй материал\n\nВторой текст.')
        links=[{'root':'articles','file':name+'.granka.json','url':url,'date':'2026-10-16'}
               for name,url in [('one','https://example.invalid/a%2Fb'),('two','https://example.invalid/a/b')]]
        rows=catalog_sources(scan_library(self.roots,include_published=True,publications=links),[])
        self.assertEqual(len(rows),2)
        self.assertEqual({r['url'] for r in rows},{e['url'] for e in links})

    def test_explicit_release_link_marks_manuscript_without_rewriting_it(self):
        path=self.draft();before=path.read_bytes()
        link={'root':'articles','file':'draft.granka.json','url':'https://example.invalid/released','date':'2026-09-20'}
        self.assertEqual(scan_library(self.roots,publications=[link]),[])
        source=scan_library(self.roots,include_published=True,publications=[link])[0]
        self.assertEqual((source['status'],source['date'],source['url']),('published','2026-09-20',link['url']))
        self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(ValueError):scan_library(self.roots,publications=[link,link])
        with self.assertRaises(ValueError):scan_library(self.roots,publications=[{**link,'url':'javascript:alert(1)'}])

    def test_exclusions_keep_new_articles_discoverable_without_deleting_old_sources(self):
        old=self.draft('old.md','# Прежняя версия\n\nСтарый текст.')
        self.draft('old.granka.json',json.dumps({'versions':[{'id':'v1','text':old.read_text(encoding='utf-8')}]}))
        self.draft('support/notes.md','# Журнал\n\nРабочие заметки.')
        options={'excluded_sources':{'articles':['old.md','support']}}
        before=old.read_bytes()
        self.assertEqual(scan_library(self.roots,**options),[])
        self.draft('new.md','# Новый материал\n\nНовый текст.')
        self.assertEqual([s['source_rel'] for s in scan_library(self.roots,**options)],['new.md'])
        self.assertEqual(old.read_bytes(),before)
        with self.assertRaises(ValueError):scan_library(self.roots,excluded_sources={'articles':['../outside']})
