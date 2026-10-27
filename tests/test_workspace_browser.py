"""The atelier must preserve real drafts and actions across its workspace pages."""
import copy
import json
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_markdown_browser as browser_tests


class WorkspaceBrowserTests(unittest.TestCase):
    wait_ready = browser_tests.MarkdownBrowserTests.wait_ready
    reload_ready = browser_tests.MarkdownBrowserTests.reload_ready

    @classmethod
    def setUpClass(cls):
        browser_tests.MarkdownBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        browser_tests.MarkdownBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        browser_tests.MarkdownBrowserTests.setUp(self)
        config = copy.deepcopy(self.server.environment.DEFAULTS)
        config['roots'] = {'articles': str(self.directory)}
        config['planning'] = {'source_dirs': {'articles': ['.']}}
        settings = patch.object(self.server.environment, 'load', return_value=config)
        settings.start(); self.addCleanup(settings.stop)
        (self.directory/'fixture.md').write_text(self.source, encoding='utf-8')

    tearDown = browser_tests.MarkdownBrowserTests.tearDown

    def go(self, section):
        self.page.locator('button[data-section="'+section+'"]').click()
        expect(self.page.locator('body')).to_have_attribute('data-section', section)
        expect(self.page.locator('button[data-section="'+section+'"]')).to_have_attribute('aria-current', 'page')

    def test_pages_preserve_source_draft_and_view_change_uses_unsaved_guard(self):
        self.page.click('[data-view=source]')
        draft = '# Незавершённая правка\n\nЕё нельзя потерять при навигации.'
        self.page.fill('#texteditor', draft)
        self.go('library')
        expect(self.page.locator('#source-list .source-item')).to_have_count(1)
        self.assertFalse(self.page.locator('#source-dialog').evaluate('(n) => n.matches(":modal")'))
        self.go('editor')
        expect(self.page.locator('#texteditor')).to_have_value(draft)
        self.assertIsNotNone(self.page.evaluate('granka.state.editor'))
        self.go('calendar'); self.go('editor')
        expect(self.page.locator('#texteditor')).to_have_value(draft)
        self.page.locator('button[data-section=diff]').click()
        expect(self.page.locator('#unsaved-dialog')).to_be_visible()
        self.page.click('#unsaved-cancel')
        expect(self.page.locator('body')).to_have_attribute('data-section', 'editor')
        expect(self.page.locator('#texteditor')).to_have_value(draft)
        self.page.locator('button[data-section=diff]').click()
        self.page.click('#unsaved-discard')
        expect(self.page.locator('body')).to_have_attribute('data-section', 'diff')
        self.assertIsNone(self.page.evaluate('granka.state.editor'))
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'][0]['text'], self.source)

    def test_settings_return_to_spices_and_reading_preferences_survive_reload(self):
        self.go('spices'); self.page.click('#spice-settings')
        expect(self.page.locator('body')).to_have_attribute('data-section', 'settings')
        self.page.select_option('#reading-font', 'sans')
        self.page.select_option('#reading-size', '20')
        self.page.uncheck('#reading-motion')
        self.page.select_option('#spice-view', 'text')
        self.page.click('#settingssave')
        expect(self.page.locator('body')).to_have_attribute('data-section', 'spices')
        expect(self.page.locator('#spice-ingredients')).to_have_attribute('data-view', 'text')
        self.assertEqual(self.page.locator('#doc').evaluate('(n) => getComputedStyle(n).fontSize'), '20px')
        self.reload_ready()
        expect(self.page.locator('body')).to_have_attribute('data-section', 'spices')
        self.assertTrue(self.page.locator('body').evaluate('(n) => n.classList.contains("no-motion")'))
        self.assertEqual(self.page.locator('#doc').evaluate('(n) => getComputedStyle(n).fontSize'), '20px')
        self.go('settings')
        expect(self.page.locator('#reading-font')).to_have_value('sans')
        expect(self.page.locator('#reading-size')).to_have_value('20')

    def test_telegram_draft_remains_local_and_same_page_does_not_reset_it(self):
        sent = []
        self.page.on('request', lambda request: sent.append(request.url) if '/publish' in request.url else None)
        self.go('telegram')
        text = 'Новый текст анонса. Скобочки на месте)))'
        self.page.fill('#tg-text', text)
        self.page.locator('button[data-section=telegram]').click()
        expect(self.page.locator('#tg-text')).to_have_value(text)
        self.go('settings'); self.page.click('#settingsclose')
        self.go('telegram')
        expect(self.page.locator('#tg-text')).to_have_value(text)
        expect(self.page.locator('#tg-preview')).to_contain_text(text)
        self.assertEqual(sent, [])

    def test_all_sections_usable_at_three_widths_and_nested_dialog_stays_modal(self):
        for width in [1600, 900, 390]:
            self.page.set_viewport_size({'width': width, 'height': 1000})
            for section in ['library', 'editor', 'spices', 'history', 'diff', 'calendar', 'settings', 'editor', 'telegram']:
                self.go(section)
                self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'), (width, section))
                expect(self.page.locator('.app-sidebar')).to_be_visible()
                if section in ['library', 'calendar', 'settings', 'telegram']:
                    page = self.page.locator('.workspace-page[open]')
                    self.assertFalse(page.evaluate('(n) => n.matches(":modal")'))
                    self.assertLessEqual(page.evaluate('(n) => n.scrollWidth - n.clientWidth'), 1, (width, section))
            self.go('calendar')
            self.page.click('#planner-add')
            self.assertTrue(self.page.locator('#planner-entry-dialog').evaluate('(n) => n.matches(":modal")'))
            self.page.click('#planner-entry-close')

    def test_deep_links_and_browser_back_restore_correct_section(self):
        self.page.goto(self.base+'/workbench#calendar'); self.wait_ready()
        expect(self.page.locator('body')).to_have_attribute('data-section', 'calendar')
        self.go('settings'); self.go('editor')
        self.page.go_back()
        expect(self.page.locator('body')).to_have_attribute('data-section', 'settings')
        self.page.go_back()
        expect(self.page.locator('body')).to_have_attribute('data-section', 'calendar')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('body')).to_have_attribute('data-section', 'editor')

    def test_image_executor_settings_are_accessible_over_modal_and_keep_prompt(self):
        self.page.click('#image-btn')
        self.page.click('[data-image-tab=generate]')
        prompt = 'Нарисовать редакторскую мастерскую со скобочками'
        self.page.fill('#generation-prompt', prompt)
        self.page.click('#generation-settings')
        expect(self.page.locator('#settingssave')).to_be_visible()
        self.assertTrue(self.page.locator('#settingsdialog').evaluate('(n) => n.matches(":modal")'))
        self.page.click('#settingsclose')
        expect(self.page.locator('#imagegallery')).to_be_visible()
        expect(self.page.locator('#generation-prompt')).to_have_value(prompt)
        self.page.click('#imageclose')

    def test_back_to_library_after_telegram_route(self):
        self.go('library'); self.go('telegram')
        self.page.go_back()
        expect(self.page.locator('body')).to_have_attribute('data-section', 'library')

    def test_back_while_telegram_settings_pending_preserves_history(self):
        held=[]
        self.page.route('**/api/telegram/settings',lambda route:held.append(route))
        try:
            self.go('library')
            with self.page.expect_request('**/api/telegram/settings'):self.go('telegram')
            self.assertEqual(self.page.evaluate('location.hash'),'#telegram');self.assertTrue(held)
            self.page.go_back();self.assertEqual(self.page.evaluate('location.hash'),'#library')
            held.pop(0).continue_()
            expect(self.page.locator('body')).to_have_attribute('data-section','library')
            with self.page.expect_request('**/api/telegram/settings'):self.page.go_forward()
            expect(self.page.locator('body')).to_have_attribute('data-section','telegram')
            while held:held.pop(0).continue_()
            expect(self.page.locator('button[data-section=library]')).to_be_enabled()
            self.page.go_back();expect(self.page.locator('body')).to_have_attribute('data-section','library')
        finally:
            while held:held.pop(0).continue_()

    def test_missing_exact_document_stays_visible_without_automatic_library(self):
        self.page.goto(self.base+'/?root=articles&source_file=missing.md')
        expect(self.page.locator('#doc')).to_contain_text('по этой ссылке не найдена')
        self.assertIsNone(self.page.evaluate('granka.state.document'))
        expect(self.page.locator('#source-dialog')).not_to_be_visible()
        expect(self.page.locator('body')).to_have_attribute('data-section', 'editor')


if __name__ == '__main__':
    unittest.main()
