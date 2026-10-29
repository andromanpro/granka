import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import uuid
from tools import article_draft as draft
from tools.article_creation import ArticleCreator, CreationConflict
from tools.jobs import Jobs


class ArticleDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.jobs = Jobs(self.root / 'jobs')
        self.addCleanup(self.jobs.pool.shutdown, wait=True)
        self.prefs = Mock(); self.prefs.context.return_value = {'rules': ['Без канцелярита'], 'decisions': []}
        self.voice = Mock(); self.voice.inspect.return_value = (
            {'status': 'ok', 'fingerprint': 'corpus-v1'}, [('a.md', '---\nsecret: ignored\n---\nМой голос. ' * 1000)])
        self.body = {'title': 'Фикус', 'brief': 'Вымышленный пример с датчиком.', 'wizard_id': str(uuid.uuid4()),
                     'operation_id': str(uuid.uuid4())}
        self.provider = patch.object(draft.ai_provider, 'text_result', return_value=({'text': 'Я поставил датчик.\n\nОн просит воды.'}, {'provider': 'fake'}))
        self.generate = self.provider.start(); self.addCleanup(self.provider.stop)

    def enqueue(self, **changes):
        return draft.enqueue({**self.body, **changes}, self.jobs, self.prefs, self.voice)

    def finish(self, job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.jobs.get(job['id'])
            if job['status'] not in {'running', 'queued'}: return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_explicit_generation_uses_voice_rules_and_saves_receipt_without_article(self):
        job = self.finish(self.enqueue())
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['result']['style']['examples'], 1)
        prompt = self.generate.call_args.args[0]
        data = json.loads(prompt.split('\n\n', 1)[1])
        self.assertIn('Без канцелярита', data['author_preferences']['rules'])
        self.assertLessEqual(len(data['style_examples'][0]), 2000)
        self.assertFalse(data['style_examples'][0].startswith('---'))
        self.assertTrue((self.jobs.directory / job['id'] / 'draft-result.json').is_file())
        self.assertFalse(list(self.root.glob('*.md')))

    def test_recipe_is_part_of_first_prompt_and_never_saves_global_preferences(self):
        recipe = {'name': 'Кухня', 'doses': [1, 1, 2, 0], 'mode': 'add', 'density': 'some', 'prompt': 'Один образ кухни'}
        job = self.finish(self.enqueue(recipe=recipe))
        self.assertEqual(job['result']['request']['recipe'], recipe)
        data = json.loads(self.generate.call_args.args[0].split('\n\n', 1)[1])
        self.assertEqual(data['request']['recipe'], recipe)
        self.assertIn('композицию', self.generate.call_args.args[0])
        self.assertFalse(list(self.root.rglob('spice-recipes.json')))

    def test_style_off_does_not_read_or_send_preferences_and_corpus(self):
        job = self.finish(self.enqueue(use_style=False))
        self.assertEqual(job['result']['style']['examples'], 0)
        self.prefs.context.assert_not_called(); self.voice.inspect.assert_not_called()
        self.assertNotIn('Без канцелярита', self.generate.call_args.args[0])

    def test_missing_corpus_keeps_rules_without_inventing_samples(self):
        self.voice.inspect.return_value = ({'status': 'empty', 'fingerprint': 'empty'}, [])
        job = self.finish(self.enqueue())
        self.assertEqual(job['result']['style']['corpus_status'], 'empty')
        self.assertEqual(job['result']['style']['rules'], 1)
        self.assertEqual(job['result']['style']['examples'], 0)

    def test_retry_after_lost_response_reuses_one_provider_call(self):
        first = self.finish(self.enqueue())
        again = self.enqueue()
        self.assertEqual(first['id'], again['id']); self.generate.assert_called_once()
        with self.assertRaises(CreationConflict): self.enqueue(title='Другая тема')
        self.generate.assert_called_once()

    def test_provider_failure_is_terminal_without_automatic_retry(self):
        self.generate.side_effect = RuntimeError('Отказ модели')
        job = self.finish(self.enqueue())
        self.assertEqual(job['status'], 'failed'); self.assertIn('Отказ модели', job['message'])
        self.assertEqual(self.enqueue()['status'], 'failed'); self.generate.assert_called_once()

    def test_invalid_and_empty_model_answers_fail(self):
        for answer in [{}, {'text': ''}, {'text': '# Только заголовок\n\n'}, {'text': 'bad\x00text'}]:
            with self.subTest(answer=answer):
                self.generate.return_value = answer, {}
                job = self.finish(self.enqueue(operation_id=str(uuid.uuid4())))
                self.assertEqual(job['status'], 'failed')

    def test_bad_parameters_do_not_start_model(self):
        for change in [{'title': ''}, {'title': 'x' * 181}, {'brief': 'x' * 16001}, {'brief': 'bad\x00'},
                       {'use_style': 'yes'}, {'length': 'huge'}, {'profile': 'other'}, {'wizard_id': '../x'},
                       {'operation_id': ''}, {'recipe': {'doses': [4, 0, 0, 0]}}]:
            with self.subTest(change=change), self.assertRaises(ValueError): self.enqueue(**change)
        self.generate.assert_not_called()

    def test_create_edited_draft_and_provenance_but_reject_changed_brief(self):
        self.generate.return_value = {'text': '# Лишний заголовок\n\nГотовая статья.'}, {}
        job = self.finish(self.enqueue())
        self.assertEqual(job['result']['text'], 'Готовая статья.')
        body = {**self.body, 'mode': 'ai', 'draft_job': job['id'], 'draft_text': 'Правка автора.', 'root': 'articles'}
        metadata = draft.creation_metadata(body, self.jobs)
        creator = ArticleCreator(lambda: self.root / 'state', lambda: {'articles': self.root / 'articles'})
        created = creator.create(body, generation=metadata)
        data = json.loads(Path(created['files'][1]).read_text(encoding='utf-8'))
        self.assertEqual(data['versions'][0]['text'], '# Фикус\n\nПравка автора.\n')
        self.assertEqual(data['intent']['generation']['job'], job['id'])
        self.assertEqual(data['versions'][0]['origin'], 'ai-draft')
        with self.assertRaises(ValueError): draft.creation_metadata({**body, 'brief': 'Другое'}, self.jobs)

    def test_create_rejects_unfinished_or_foreign_job_and_empty_text(self):
        with self.assertRaises(LookupError): draft.creation_metadata({'draft_job': 'a' * 24}, self.jobs)
        self.generate.side_effect = RuntimeError('Нет ответа')
        job = self.finish(self.enqueue())
        with self.assertRaises(ValueError): draft.creation_metadata({**self.body, 'draft_job': job['id']}, self.jobs)
        creator = ArticleCreator(lambda: self.root, lambda: {'articles': self.root / 'articles'})
        with self.assertRaises(ValueError): creator.preview({**self.body, 'root': 'articles', 'mode': 'ai', 'draft_job': job['id'], 'draft_text': ''})
