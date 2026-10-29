import json
import threading
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_creation_meter_browser as base


class ArticleDraftBrowserTests(unittest.TestCase):
    wait_ready = base.CreationMeterBrowserTests.wait_ready
    reload_ready = base.CreationMeterBrowserTests.reload_ready
    wizard = base.CreationMeterBrowserTests.wizard
    next = base.CreationMeterBrowserTests.next
    finish = base.CreationMeterBrowserTests.finish
    tearDown = base.CreationMeterBrowserTests.tearDown
    @classmethod
    def setUpClass(cls): base.CreationMeterBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.CreationMeterBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        base.CreationMeterBrowserTests.setUp(self)
        self.provider = patch.object(self.server.ai_provider, 'text_result', return_value=(
            {'text': 'Я поставил датчик.\n\nТеперь фикус напоминает мне о воде.'}, {'provider': 'fake'}))
        self.generate = self.provider.start(); self.addCleanup(self.provider.stop)

    def ai(self):
        self.wizard('Учебная статья с ИИ'); self.next()
        self.page.select_option('#newarticle-mode', 'ai')
        self.page.fill('#newarticle-brief', 'Вымышленная история про датчик и фикус.')

    def run_ai(self):
        self.page.click('#newarticle-generate')
        try:
            expect(self.page.locator('#newarticle-ai-status')).to_contain_text('Заготовка готова', timeout=15000)
        except AssertionError as error:
            import sys, traceback
            self.fail(str(error) + '\n' + json.dumps({'calls': self.generate.call_count,
                'jobs': [json.loads(p.read_text(encoding='utf-8')) for p in self.server.JOBS.directory.glob('*/job.json')],
                'threads': [''.join(traceback.format_stack(frame)) for ident, frame in sys._current_frames().items()
                            if any(t.ident == ident and t.name.startswith('granka-job') for t in threading.enumerate())]}, ensure_ascii=False))
        expect(self.page.locator('#newarticle-next')).to_be_enabled()

    def recipe(self, value='dry'):
        self.page.check('#newarticle-season')
        self.page.wait_for_function('(value) => [...document.querySelector("#newarticle-recipe").options].some(n => n.value === value)', arg=value)
        self.page.select_option('#newarticle-recipe', value)

    def test_first_draft_recipe_edit_create_and_fresh_wizard(self):
        self.ai(); self.recipe()
        self.assertEqual(self.generate.call_count, 0)
        self.run_ai(); self.assertEqual(self.generate.call_count, 1)
        payload = json.loads(self.generate.call_args.args[0].split('\n\n', 1)[1])
        self.assertEqual(payload['request']['recipe']['doses'], [2, 0, 0, 0])
        self.assertIn('Вымышленная история', payload['request']['brief'])
        self.assertFalse(list(self.work.glob('Учебная статья с ИИ*')))
        self.page.fill('#newarticle-draft-text', 'Автор отредактировал заготовку.\n\nВторой абзац.')
        self.next(); self.finish()
        file = next(self.work.glob('Учебная статья с ИИ.granka.json'))
        doc = json.loads(file.read_text(encoding='utf-8'))
        self.assertIn('Автор отредактировал', doc['versions'][0]['text'])
        self.assertEqual(doc['intent']['generation']['recipe']['name'], 'Сухая ирония')
        self.wizard('Следующая статья'); self.next()
        expect(self.page.locator('#newarticle-mode')).to_have_value('blank')
        self.page.select_option('#newarticle-mode', 'ai')
        self.assertFalse(self.page.locator('#newarticle-season').is_checked())
        expect(self.page.locator('#newarticle-draft-text')).to_have_value('')
        self.assertFalse(self.server.SPICE_RECIPES.path.exists())

    def test_saved_recipe_and_changed_brief_require_new_generation(self):
        state = self.server.SPICE_RECIPES.read()
        saved = self.server.SPICE_RECIPES.save({'expected_revision': state['revision'], 'recipe': {
            'name': 'Учебная смесь', 'doses': [0, 0, 2, 1], 'mode': 'reduce', 'density': 'rare', 'prompt': 'Без пафоса'}})
        before = self.server.SPICE_RECIPES.path.read_bytes()
        self.ai(); self.recipe(saved['saved_id']); self.page.uncheck('#newarticle-style'); self.run_ai()
        payload = json.loads(self.generate.call_args.args[0].split('\n\n', 1)[1])
        self.assertEqual(payload['request']['recipe']['mode'], 'reduce')
        self.assertEqual(payload['style_examples'], [])
        old = self.page.locator('#newarticle-draft-text').input_value()
        self.page.fill('#newarticle-brief', 'Другая история.')
        expect(self.page.locator('#newarticle-next')).to_be_disabled()
        expect(self.page.locator('#newarticle-ai-stale')).to_be_visible()
        expect(self.page.locator('#newarticle-draft-text')).to_have_value(old)
        self.assertEqual(self.server.SPICE_RECIPES.path.read_bytes(), before)

    def test_lost_post_response_reload_and_same_operation_use_one_call(self):
        lost = []; ids = []
        def route(r):
            ids.append(r.request.post_data_json['operation_id'])
            if not lost:
                response = r.fetch(); self.assertEqual(response.status, 202); lost.append(True)
                r.fulfill(status=502, json={'error': 'Ответ потерян после запуска'})
            else: r.continue_()
        self.page.route('**/api/article-draft', route)
        self.ai(); self.page.click('#newarticle-generate')
        expect(self.page.locator('#newarticle-generate')).to_have_text('Проверить запуск')
        self.reload_ready(); self.page.click('#newarticle-main'); self.next()
        expect(self.page.locator('#newarticle-mode')).to_have_value('ai')
        self.page.click('#newarticle-generate')
        expect(self.page.locator('#newarticle-ai-status')).to_contain_text('Заготовка готова', timeout=15000)
        self.assertEqual(len(set(ids)), 1); self.generate.assert_called_once()

    def test_pending_close_reopen_and_double_click_do_not_repeat_or_create_files(self):
        release = threading.Event(); self.addCleanup(release.set)
        def held(*args):
            release.wait(10); return {'text': 'Готовый фикус.'}, {}
        self.generate.side_effect = held
        self.ai()
        self.page.evaluate('document.querySelector("#newarticle-generate").click(); document.querySelector("#newarticle-generate").click()')
        expect(self.page.locator('#newarticle-next')).to_be_disabled()
        expect(self.page.locator('#newarticle-ai')).to_have_attribute('aria-busy', 'true')
        self.page.click('#newarticle-close'); self.page.click('#newarticle-main')
        release.set()
        expect(self.page.locator('#newarticle-ai-status')).to_contain_text('Заготовка готова', timeout=15000)
        self.generate.assert_called_once()
        self.assertFalse(list(self.work.glob('Учебная статья с ИИ*')))

    def test_worker_failure_no_repeat_then_explicit_new_attempt(self):
        self.generate.side_effect = RuntimeError('Отказ модели')
        self.ai(); self.page.click('#newarticle-generate')
        expect(self.page.locator('#newarticle-ai-status')).to_contain_text('Отказ модели', timeout=15000)
        expect(self.page.locator('#newarticle-next')).to_be_disabled()
        self.assertEqual(self.generate.call_count, 1)
        self.generate.side_effect = None; self.run_ai()
        self.assertEqual(self.generate.call_count, 2)

    def test_validation_error_allows_fixing_parameters(self):
        self.ai(); self.page.check('#newarticle-season'); self.page.click('#newarticle-generate')
        expect(self.page.locator('#newarticle-ai-status')).to_contain_text('Выберите приправу', timeout=10000)
        expect(self.page.locator('#newarticle-season')).to_be_enabled()
        self.page.uncheck('#newarticle-season'); self.run_ai()
        self.generate.assert_called_once()

    def test_reload_keeps_edited_output_and_recipe_without_new_request(self):
        self.ai(); self.recipe(); self.run_ai()
        self.page.fill('#newarticle-draft-text', 'Мой исправленный текст.')
        self.reload_ready(); self.page.click('#newarticle-main'); self.next()
        expect(self.page.locator('#newarticle-recipe')).to_have_value('dry')
        expect(self.page.locator('#newarticle-draft-text')).to_have_value('Мой исправленный текст.')
        expect(self.page.locator('#newarticle-next')).to_be_enabled()
        self.generate.assert_called_once()

    def test_narrow_keyboard_and_no_automatic_generation(self):
        self.page.set_viewport_size({'width': 375, 'height': 900}); self.ai(); self.recipe()
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        expect(self.page.locator('#newarticle-next')).to_be_disabled()
        self.page.keyboard.press('Escape'); expect(self.page.locator('#newarticle-main')).to_be_focused()
        self.generate.assert_not_called()
