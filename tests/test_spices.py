from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from tools import spices
from tools.document_store import validate
import test_workflow as workflow


def document():
    return {'doc': 'Тема', 'versions': [{'id': 'v1', 'text': '# Тема 😀\n\nПервый абзац.\n\nВторой абзац.\n\n`код()` и [ссылка](https://example.invalid/).\n'}], 'annotations': []}


def request(data, quote='Второй абзац.'):
    source = data['versions'][0]['text']
    start = source.index(quote)
    return spices.prepare(data, data['versions'][0], {'recipe': spices.BUILTINS[0],
        'target': {'start': start, 'end': start + len(quote), 'quote': quote}})


def response(quote='Второй абзац.'):
    return {'summary': 'Сухая ирония', 'changes': [{'quote': quote, 'occurrence': 0,
            'variants': ['Второй абзац тоже участвует.'], 'why': 'Ирония'}]}


class SpiceTests(unittest.TestCase):
    def test_proposals_keep_source_and_scope_unicode(self):
        data = document(); before = deepcopy(data); req = request(data)
        result = spices.proposals(data['versions'][0]['text'], req, response())
        self.assertEqual(data, before)
        out = spices.apply(data, result, [{'index': 0, 'text': result['changes'][0]['variants'][0]}])
        self.assertEqual(out['version'], 'v2'); self.assertEqual(data['versions'][0], before['versions'][0])
        self.assertEqual(data['versions'][-1]['text'], before['versions'][0]['text'].replace('Второй абзац.', 'Второй абзац тоже участвует.'))
        self.assertEqual(validate(data), [])

    def test_rejects_model_escape_overlap_and_protected_content(self):
        data = document(); source = data['versions'][0]['text']; req = request(data)
        with self.assertRaisesRegex(ValueError, 'пределами'):
            spices.proposals(source, req, response('Первый абзац.'))
        bad = response(); bad['changes'] *= 2
        with self.assertRaisesRegex(ValueError, 'пересекаются'):
            spices.proposals(source, req, bad)
        start = source.index('Второй'); data['annotations'] = [{'id': 'p', 'version': 'v1', 'type': 'protected', 'start': start, 'end': start + 6, 'quote': 'Второй'}]
        with self.assertRaisesRegex(ValueError, 'защищённый'):
            spices.proposals(source, request(data), response())
        for quote in ['`код()`', 'https://example.invalid/']:
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, 'защищённый'):
                spices.proposals(source, request(data, quote), response(quote))

    def test_duplicate_quote_uses_global_occurrence_and_cannot_escape_target(self):
        data = document(); data['versions'][0]['text'] += '\nВторой абзац.'
        result = response(); result['changes'][0]['occurrence'] = 1
        with self.assertRaisesRegex(ValueError, 'пределами'):
            spices.proposals(data['versions'][0]['text'], request(data), result)

    def test_bad_target_or_recipe_rejected_before_provider(self):
        data = document(); source = data['versions'][0]['text']
        for target in [None, {'start': True, 'end': 2, 'quote': source[:2]}, {'start': 0, 'end': 3, 'quote': 'нет'}]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                spices.prepare(data, data['versions'][0], {'recipe': spices.BUILTINS[0], 'target': target})
        own = {**spices.BUILTINS[0], 'doses': [0, 0, 0, 0], 'prompt': 'Сократи пафос'}
        self.assertEqual(spices.recipe(own)['prompt'], 'Сократи пафос')
        for doses in [[True, 0, 0, 0], [4, 0, 0, 0], [0, 0]]:
            with self.subTest(doses=doses), self.assertRaises(ValueError):
                spices.recipe({**own, 'doses': doses})

    def test_apply_subset_and_stale_version_do_not_touch_other_text(self):
        data = document(); source = data['versions'][0]['text']
        req = spices.prepare(data, data['versions'][0], {'recipe': spices.BUILTINS[0], 'target': {'start': 0, 'end': len(source), 'quote': source}})
        report = response(); report['changes'].append({'quote': 'Первый абзац.', 'occurrence': 0, 'variants': ['Первый тоже.'], 'why': ''})
        result = spices.proposals(source, req, report)
        spices.apply(data, result, [{'index': 1, 'text': 'Авторская замена.'}])
        self.assertIn('Первый абзац.', data['versions'][-1]['text']); self.assertIn('Авторская замена.', data['versions'][-1]['text'])
        with self.assertRaisesRegex(ValueError, 'Версия'):
            spices.apply(data, result, [{'index': 0, 'text': 'Ещё.'}])

    def test_recipe_updates_are_revision_checked_and_builtins_stay_immutable(self):
        with tempfile.TemporaryDirectory() as folder:
            store = spices.Recipes(lambda: Path(folder)); old = store.read()
            own = {**spices.BUILTINS[0], 'name': 'Моя смесь'}
            new = store.save({'expected_revision': old['revision'], 'recipe': own})
            self.assertEqual(len(new['recipes']), 1); self.assertEqual(new['builtins'], old['builtins'])
            with self.assertRaises(spices.RecipeConflict):
                store.save({'expected_revision': old['revision'], 'recipe': own})
            new = store.save({'expected_revision': new['revision'], 'id': new['saved_id'], 'recipe': {**own, 'prompt': 'Моё пожелание'}})
            self.assertEqual(new['recipes'][0]['prompt'], 'Моё пожелание')
            new = store.save({'expected_revision': new['revision'], 'id': new['saved_id'], 'delete': True})
            self.assertEqual(new['recipes'], [])


class SpiceJobTests(unittest.TestCase):
    setUp = workflow.JobServiceTests.setUp
    tearDown = workflow.JobServiceTests.tearDown
    wait = workflow.JobServiceTests.wait

    def start_spice(self):
        data = document(); self.file.write_text(json.dumps(data), encoding='utf-8')
        self.source = data['versions'][0]['text']; (self.root/'article.md').write_text(self.source, encoding='utf-8')
        req = request(data)
        code, out = self.server.enqueue_job('articles', 'article.granka.json', {'kind': 'spice', 'version': 'v1',
            'expected_revision': self.server.revision_of(self.file), 'operation_id': 'spice',
            'recipe': req['recipe'], 'target': req['target']})
        self.assertEqual(code, 202); return out['job']['id']

    def test_job_is_preview_only_and_snapshots_request(self):
        with patch.object(self.server.ai_provider, 'text_result', return_value=(response(), {'provider': 'codex'})) as provider:
            job = self.wait(self.start_spice())
        self.assertEqual(job['status'], 'succeeded', job)
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))['versions'], document()['versions'])
        self.assertEqual((self.root/'article.md').read_text(encoding='utf-8'), self.source)
        self.assertTrue((self.root/'jobs'/job['id']/'spice-result.json').is_file())
        self.assertEqual(provider.call_count, 1)

    def test_concurrent_article_change_keeps_result_but_conflicts(self):
        entered, release = threading.Event(), threading.Event()
        def respond(*args):
            entered.set(); release.wait(3); return response(), {'provider': 'codex'}
        with patch.object(self.server.ai_provider, 'text_result', side_effect=respond):
            ident = self.start_spice(); self.assertTrue(entered.wait(2))
            data = json.loads(self.file.read_text(encoding="utf-8")); data['external'] = 'fresh'; self.file.write_text(json.dumps(data), encoding='utf-8')
            release.set(); job = self.wait(ident)
        self.assertEqual(job['status'], 'conflict'); self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))['external'], 'fresh')
        self.assertTrue((self.root/'jobs'/ident/'spice-result.json').is_file())


if __name__ == '__main__':
    unittest.main()
