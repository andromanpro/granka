import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_markdown_browser as base


class CreationMeterBrowserTests(unittest.TestCase):
    wait_ready = base.MarkdownBrowserTests.wait_ready
    reload_ready = base.MarkdownBrowserTests.reload_ready
    @classmethod
    def setUpClass(cls): base.MarkdownBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.MarkdownBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        base.MarkdownBrowserTests.setUp(self)
        self.work = self.directory / self._testMethodName; self.work.mkdir(exist_ok=True)
        self.server.ROOTS = {'articles': self.work}; self.server.GIT_ROOTS = {'articles': self.work}
        self.server.IMAGE_DIRS = [self.work]
        from tools.publication_plan import PublicationPlan
        self.server.CALENDAR = PublicationPlan(lambda: self.work / 'calendar-state', lambda: self.server.library_sources())
        self.file = self.work / 'fixture.granka.json'
        self.file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        p = patch.object(self.server.iimeter, 'CORPUS_DIR', self.work / 'unset-corpus'); p.start(); self.addCleanup(p.stop)
        self.page.goto(self.base + '/?root=articles&f=fixture.granka.json'); self.wait_ready()

    tearDown = base.MarkdownBrowserTests.tearDown

    def wizard(self, title='Учебный фикус'):
        self.page.click('#newarticle-main'); expect(self.page.locator('#newarticle-dialog')).to_be_visible()
        self.page.fill('#newarticle-title', title)

    def next(self): self.page.click('#newarticle-next')

    def finish(self):
        expect(self.page.locator('#newarticle-next')).to_be_enabled()
        self.next(); expect(self.page.locator('#newarticle-dialog')).not_to_be_visible(timeout=15000)
        self.page.wait_for_function('granka.state.file !== "fixture.granka.json" && !granka.state.busy')

    def test_empty_library_blank_creation_and_double_press(self):
        self.file.unlink(); self.page.goto(self.base + '/#library')
        expect(self.page.locator('#newarticle-library')).to_be_visible()
        self.page.click('#newarticle-library'); self.next()
        expect(self.page.locator('#newarticle-status')).to_contain_text('название')
        self.page.fill('#newarticle-title', 'Новая статья: фикус')
        self.page.fill('#newarticle-audience', 'Учебная аудитория'); self.page.fill('#newarticle-purpose', 'Цель вне текста')
        self.next(); self.next(); expect(self.page.locator('#newarticle-files')).to_contain_text('.granka.json')
        expect(self.page.locator('#newarticle-next')).to_be_enabled()
        self.page.evaluate('document.querySelector("#newarticle-next").click(); document.querySelector("#newarticle-next").click()')
        expect(self.page.locator('#newarticle-dialog')).not_to_be_visible(timeout=15000)
        documents = list(self.work.glob('*.granka.json')); self.assertEqual(len(documents), 1)
        data = json.loads(documents[0].read_text(encoding='utf-8'))
        self.assertNotIn('Цель вне текста', data['versions'][0]['text'])
        self.assertEqual(data['intent']['audience'], 'Учебная аудитория')
        self.assertTrue(self.page.locator('#texteditor').is_editable())
        sources = self.server.library_sources()
        self.assertTrue(any(s['file'] == documents[0].name for s in sources))
        self.assertTrue(any(e.get('file') == documents[0].name for e in self.server.CALENDAR.read()['entries']))

    def test_notes_back_cancel_and_reopen_keep_input(self):
        self.wizard(); self.next(); self.page.select_option('#newarticle-mode', 'notes')
        self.page.fill('#newarticle-notes', 'Первая строка.\n\nВторая заметка с кириллицей 😀.')
        self.page.click('#newarticle-back'); expect(self.page.locator('#newarticle-title')).to_have_value('Учебный фикус')
        self.page.click('#newarticle-close'); self.page.click('#newarticle-main'); self.next()
        expect(self.page.locator('#newarticle-notes')).to_have_value('Первая строка.\n\nВторая заметка с кириллицей 😀.')
        self.next(); self.finish()
        self.assertIn('Вторая заметка', self.page.locator('#texteditor').input_value())

    def test_outline_and_same_title_use_other_filename(self):
        for index in range(2):
            self.wizard('Одинаковое название'); self.next(); self.page.select_option('#newarticle-mode', 'outline'); self.next(); self.finish()
        self.assertEqual(len(list(self.work.glob('Одинаковое название*.md'))), 2)
        self.assertIn('## Что получилось', self.page.locator('#texteditor').input_value())

    def test_lost_response_retry_does_not_duplicate(self):
        lost = []
        def route(request):
            if request.request.method == 'POST' and not lost:
                response = request.fetch(); self.assertEqual(response.status, 200); lost.append(True)
                request.fulfill(status=502, json={'error': 'Ответ потерян после создания'})
            else: request.continue_()
        self.page.route('**/api/article-creation', route)
        self.wizard('Повтор запроса'); self.next(); self.next(); expect(self.page.locator('#newarticle-next')).to_be_enabled(); self.next()
        expect(self.page.locator('#newarticle-status')).to_contain_text('Ответ потерян')
        self.finish(); self.assertEqual(len(list(self.work.glob('Повтор запроса*.md'))), 1)

    def test_unsaved_current_article_guards_wizard(self):
        self.page.click('[data-view=source]'); self.page.fill('#texteditor', self.source + '\nНесохранённая строка.\n')
        self.page.click('#newarticle-main'); expect(self.page.locator('#unsaved-dialog')).to_be_visible()
        self.page.click('#unsaved-cancel'); expect(self.page.locator('#newarticle-dialog')).not_to_be_visible()
        self.assertIn('Несохранённая', self.page.locator('#texteditor').input_value())

    def test_corpus_setup_and_profile_switch_keep_local_findings(self):
        text = '# Учебный фикус\n\nВажно отметить: я проверил датчик. ' * 12
        self.data['versions'][0]['text'] = text; self.file.write_text(json.dumps(self.data), encoding='utf-8'); self.reload_ready()
        self.page.click('[data-pane=meter]'); self.page.wait_for_function('granka.state.meter?.status === "uncalibrated"')
        expect(self.page.locator('#meter-number')).to_have_text('—'); self.assertGreater(int(self.page.locator('#axis-markers-number').inner_text()), 0)
        self.page.click('#meter-configure')
        corpus = self.work / 'учебный-корпус'; corpus.mkdir()
        source = corpus / 'one.md'; source.write_text('Сегодня я проверял датчик. Днём принёс лейку, вечером пошёл домой. ' * 25, encoding='utf-8')
        before = source.read_bytes()
        expect(self.page.locator('#meter-corpus-path')).not_to_have_value('')
        self.page.fill('#meter-corpus-path', str(corpus)); self.page.click('#meter-corpus-save')
        self.page.wait_for_function('granka.state.meter?.status === "ok"')
        expect(self.page.locator('#meter-number')).not_to_have_text('—')
        self.assertEqual(source.read_bytes(), before)
        self.page.select_option('#meterprofile', 'talks'); self.page.wait_for_function('granka.state.meter?.profile_id === "talks"')
        expect(self.page.locator('#meter-number')).to_have_text('—')
        self.assertEqual(self.page.locator('#meterwidget').get_attribute('aria-busy'), 'false')

    def test_old_measurement_error_cannot_replace_new_document(self):
        held = []
        def hold(route):
            if 'fixture.granka.json' in route.request.url: held.append(route)
            else: route.continue_()
        self.page.route('**/api/iimeter?*', hold)
        self.page.click('#meterrefresh'); self.page.wait_for_timeout(150)
        self.assertTrue(held)
        second = self.work / 'other.granka.json'; data = json.loads(self.file.read_text()); data['versions'][0]['text'] = 'Новый фикус. ' * 20; second.write_text(json.dumps(data), encoding='utf-8')
        self.page.evaluate('granka.loadDocument("articles", "other.granka.json")')
        self.page.wait_for_function('granka.state.file === "other.granka.json" && !!granka.state.meter')
        status = self.page.locator('#meter-status').inner_text()
        for route in held: route.fulfill(status=500, json={'error': 'Ошибка старого запроса'})
        self.page.wait_for_timeout(150)
        expect(self.page.locator('#meter-status')).to_have_text(status)
        expect(self.page.locator('#meter-error')).to_have_text('')
        self.assertNotIn('measuring', self.page.locator('#meterwidget').get_attribute('class'))

    def test_current_error_retry_and_clear_measurement_spinner(self):
        self.page.wait_for_function('!!granka.state.meter')
        self.page.route('**/api/iimeter?*', lambda route: route.fulfill(status=503, json={'error': 'Расчёт временно недоступен'}))
        self.page.click('#meterrefresh'); self.page.click('[data-pane=meter]')
        expect(self.page.locator('#meter-error')).to_contain_text('временно недоступен')
        expect(self.page.locator('#meter-retry')).to_be_visible()
        self.page.unroute('**/api/iimeter?*'); self.page.click('#meter-retry')
        self.page.wait_for_function('!!granka.state.meter'); expect(self.page.locator('#meter-error')).to_have_text('')
        self.page.evaluate('document.querySelector("#meterwidget").classList.add("measuring")')
        self.page.evaluate('async () => (await import("/static/ui/meter.js")).clearMeter()')
        self.assertNotIn('measuring', self.page.locator('#meterwidget').get_attribute('class'))

    def test_keyboard_mobile_and_zoom(self):
        self.page.set_viewport_size({'width': 375, 'height': 900}); self.wizard('Клавиатурный черновик')
        self.page.locator('#newarticle-title').press('Enter')
        expect(self.page.locator('#newarticle-progress')).to_contain_text('Шаг 2')
        self.page.locator('#newarticle-next').focus(); self.page.keyboard.press('Enter')
        expect(self.page.locator('#newarticle-files')).to_contain_text('.md')
        self.page.add_style_tag(content='html {font-size:200%}')
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertTrue(self.page.locator('#newarticle-next').is_visible())
        self.page.keyboard.press('Escape'); expect(self.page.locator('#newarticle-dialog')).not_to_be_visible()
        expect(self.page.locator('#newarticle-main')).to_be_focused()
