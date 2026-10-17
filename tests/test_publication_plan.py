from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from tools.publication_plan import PublicationPlan, PlanConflict, distribute


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plan = PublicationPlan(lambda: self.root)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, title='Тема', **values):
        return self.plan.change({'action': 'save', 'expected_revision': self.plan.read()['revision'],
                                 'entry': {'title': title, 'status': 'draft', **values}})

    def options(self):
        return {'start': '2026-09-16', 'weekdays': [0, 2, 4], 'capacity': 1}

    def test_distribution_respects_published_fixed_days_capacity_and_channel(self):
        self.add('Вышло', date='2026-09-16', status='published')
        self.add('Закреплено', date='2026-09-18', pinned=True)
        self.add('Идея', status='idea')
        self.add('Текст')
        self.add('Telegram', channel='telegram', status='ready')
        state = self.plan.read(); before = deepcopy(state)
        proposed = distribute(state['entries'], self.options())
        self.assertEqual([(i['title'], i['date']) for i in proposed], [('Telegram', '2026-09-21'), ('Текст', '2026-09-23')])
        self.assertEqual(state, before)
        selected = distribute(state['entries'], {**self.options(), 'channel': 'article'})
        self.assertEqual([i['title'] for i in selected], ['Текст'])

    def test_preview_has_no_writes_and_stale_apply_cannot_overwrite_new_plan(self):
        state = self.add(); raw = self.plan.path.read_bytes()
        body = {'expected_revision': state['revision'], 'options': self.options()}
        result = self.plan.schedule(body)
        self.assertEqual(self.plan.path.read_bytes(), raw)
        self.assertEqual(result['assignments'][0]['date'], '2026-09-16')
        self.add('Ещё одна')
        with self.assertRaises(PlanConflict): self.plan.schedule(body, apply=True)
        self.assertTrue(all(not e['date'] for e in self.plan.read()['entries']))
        body['expected_revision'] = self.plan.read()['revision']
        after = self.plan.schedule(body, apply=True)
        self.assertEqual([e['date'] for e in after['entries']], ['2026-09-16', '2026-09-18'])

    def receipt(self, stage='prod', status='publish', created=1):
        directory = self.root / 'site-publications' / str(created); directory.mkdir(parents=True)
        data = {'id': str(created), 'created': created, 'root': 'articles', 'rel': 'text.granka.json', 'version': 'v1',
                'metadata': {'title': 'Текст', 'date': '2026-09-16'},
                'stages': {stage: {'status': 'succeeded', 'post_status': status, 'url': 'https://example.invalid/text'}}}
        (directory / 'plan.json').write_text(json.dumps(data), encoding='utf-8')

    def test_receipt_projection_deduplicates_linked_article_and_blocks_manual_changes(self):
        state = self.add('Черновик', root='articles', file='text.granka.json')
        identifier = state['entries'][0]['id']
        self.receipt(stage='nas')
        self.assertEqual(self.plan.read()['entries'][0]['status'], 'draft')
        self.receipt(created=2)
        state = self.plan.read()
        self.assertEqual(len(state['entries']), 1)
        self.assertEqual(state['entries'][0]['id'], identifier)
        self.assertEqual(state['entries'][0]['status'], 'published')
        self.assertEqual(distribute(state['entries'], self.options()), [])
        with self.assertRaises(ValueError):
            self.plan.change({'action': 'delete', 'id': identifier, 'expected_revision': state['revision']})
        self.receipt(created=3, status='draft')
        self.assertEqual(self.plan.read()['entries'][0]['status'], 'draft')

    def test_invalid_dates_links_and_empty_weekdays_are_rejected(self):
        for values in [{'date': '2026-02-30'}, {'url': 'javascript:alert(1)'}, {'status': 'published'}, {'pinned': True}]:
            with self.subTest(values=values), self.assertRaises(ValueError): self.add(**values)
        with self.assertRaises(ValueError): distribute([], {**self.options(), 'weekdays': []})

    def test_calendar_change_does_not_touch_document(self):
        article = self.root / 'article.md'; article.write_text('Original text', encoding='utf-8')
        state = self.add('План', root='articles', file='article.granka.json')
        self.plan.schedule({'expected_revision': state['revision'], 'options': self.options()}, apply=True)
        self.assertEqual(article.read_text(encoding='utf-8'), 'Original text')

    def test_publication_supersedes_saved_draft_and_keeps_private_notes(self):
        from tools.planning_library import scan_library, catalog_sources
        article = self.root / 'article.md'
        article.write_text('# Исходник\n\nТекст статьи.', encoding='utf-8')
        links = []
        self.plan.library = lambda: catalog_sources(scan_library({'work': self.root},
            {'work': ['article.md']}, include_published=True, publications=links), [])
        source = self.plan.read()['entries'][0]
        self.plan.change({'action': 'save', 'id': source['id'], 'expected_revision': self.plan.read()['revision'],
            'entry': {**source, 'status': 'ready', 'date': '2026-10-20', 'excluded': True,
                      'notes': 'Моя заметка', 'url': 'https://example.invalid/old'}})
        stored = self.plan.path.read_bytes()
        links.append({'root': 'work', 'file': 'article.granka.json',
                      'url': 'https://example.invalid/released', 'date': '2026-10-16'})
        item = self.plan.read()['entries'][0]
        self.assertEqual(item['id'], source['id'])
        self.assertEqual((item['status'], item['date'], item['url']),
                         ('published', '2026-10-16', 'https://example.invalid/released'))
        self.assertEqual(item['notes'], 'Моя заметка')
        self.assertFalse(item['excluded'])
        self.assertNotIn('source_error', item)
        self.assertEqual(item['readiness']['percent'], 100)
        self.assertEqual(distribute([item], self.options()), [])
        self.assertEqual(self.plan.path.read_bytes(), stored)
        self.assertEqual(article.read_text(encoding='utf-8'), '# Исходник\n\nТекст статьи.')
        self.assertFalse(article.with_suffix('.granka.json').exists())
