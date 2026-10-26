import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_markdown_browser as markdown_browser

ROOT = Path(__file__).resolve().parents[1]


class SpiceBrowserTests(unittest.TestCase):
    wait_ready = markdown_browser.MarkdownBrowserTests.wait_ready
    reload_ready = markdown_browser.MarkdownBrowserTests.reload_ready
    @classmethod
    def setUpClass(cls):
        markdown_browser.MarkdownBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        markdown_browser.MarkdownBrowserTests.tearDownClass.__func__(cls)

    setUp = markdown_browser.MarkdownBrowserTests.setUp
    tearDown = markdown_browser.MarkdownBrowserTests.tearDown

    def panel(self):
        self.page.click('[data-pane=spices]')
        self.page.wait_for_function('document.querySelector("#spice-recipe").options.length > 2')

    def test_right_panel_settings_recipes_and_paragraph_choice(self):
        self.panel()
        positions = self.page.evaluate('''() => ({panel:document.querySelector('#spice-panel').getBoundingClientRect().x,article:document.querySelector('#doc').getBoundingClientRect().right})''')
        self.assertGreaterEqual(positions['panel'], positions['article'])
        self.assertEqual(self.page.locator('.spice-vessel').count(), 4)
        self.assertEqual(len(set(self.page.locator('.spice-vessel').evaluate_all('(nodes)=>nodes.map(n=>getComputedStyle(n).height)'))), 4)
        self.page.click('#spice-settings'); self.page.select_option('#spice-view', 'text'); self.page.select_option('#spice-motion', 'none'); self.page.click('#settingssave')
        expect(self.page.locator('#spice-ingredients')).to_have_attribute('data-view', 'text')
        self.page.click('#spice-new'); self.page.fill('#spice-recipe-name', 'Мой кухонный рецепт'); self.page.fill('#spice-prompt', 'Объясни через бытовую аналогию'); self.page.click('#spice-save')
        expect(self.page.locator('#spice-recipe option:checked')).to_have_text('Мой кухонный рецепт')
        self.page.click('#spice-pick'); self.page.locator('#doc > p').first.click()
        expect(self.page.locator('#spice-target')).to_contain_text('Абзац: До **жирного**')
        self.assertIsNone(self.page.evaluate('granka.state.editor'))
        self.assertFalse(self.page.locator('#spice-run').is_disabled())
        self.page.reload(); self.panel()
        self.assertEqual(self.page.locator('#spice-ingredients').get_attribute('data-view'), 'text')
        self.assertEqual(self.page.locator('#spice-recipe option').filter(has_text='Мой кухонный рецепт').count(), 1)

    def test_drop_generates_preview_then_applies_only_the_chosen_paragraph(self):
        self.source = '# Заголовок 😀\n\nПервый абзац.\n\nВторой абзац.\n'
        self.data['versions'][0]['text'] = self.source; self.file.write_text(json.dumps(self.data), encoding='utf-8')
        md = self.file.with_name('fixture.md'); md.write_text(self.source, encoding='utf-8')
        self.page.reload(); self.page.wait_for_function('granka.state.document && !granka.state.busy'); self.panel()
        entered, release = threading.Event(), threading.Event()
        def respond(*args):
            entered.set(); release.wait(8)
            return {'summary': 'Щепотка иронии', 'changes': [{'quote': 'Второй абзац.', 'occurrence': 0, 'variants': ['Второй абзац тоже участвует.'], 'why': 'Ирония'}]}, {'provider': 'codex'}
        with patch.object(self.server.ai_provider, 'text_result', side_effect=respond):
            self.page.locator('.spice-vessel-button').first.drag_to(self.page.locator('#doc > p').nth(1))
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.page.locator('#doc .spice-working').count(), 1)
            self.assertIn('Второй', self.page.locator('#doc .spice-working').inner_text())
            self.assertEqual(len(json.loads(self.file.read_text(encoding="utf-8"))['versions']), 1)
            release.set(); self.page.locator('#spice-changes textarea').wait_for(timeout=10000)
        self.assertEqual(len(json.loads(self.file.read_text(encoding="utf-8"))['versions']), 1)
        self.page.screenshot(path=str(ROOT/'_audit/spices-2026-10-24/preview.png'))
        self.page.click('#spice-apply'); self.page.wait_for_function('granka.state.version === "v2" && !granka.state.busy')
        saved = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['versions'][0]['text'], self.source)
        self.assertEqual(saved['versions'][-1]['text'], self.source.replace('Второй абзац.', 'Второй абзац тоже участвует.'))
        self.assertEqual(md.read_text(encoding='utf-8'), saved['versions'][-1]['text'])

    def test_mobile_layout_and_actual_paragraph_controls(self):
        self.panel()
        for width in [1440, 800, 375]:
            self.page.set_viewport_size({'width': width, 'height': 1000})
            if width < 900:
                self.page.evaluate('document.querySelector("#inspector").classList.add("mobile-open")')
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            self.page.screenshot(path=str(ROOT/f'_audit/spices-2026-10-24/panel-{width}.png'))
        self.page.click('#spice-pick')
        self.assertNotIn('mobile-open', self.page.locator('#inspector').get_attribute('class'))
        self.page.locator('#doc > p').first.click()
        self.assertIn('mobile-open', self.page.locator('#inspector').get_attribute('class'))
        expect(self.page.locator('#spice-target')).to_contain_text('Абзац:')

    def test_selection_survives_panel_focus_and_external_markdown_is_not_overwritten(self):
        self.panel()
        quote = 'жирного'
        self.page.evaluate('''() => { const n=document.querySelector('#doc strong').firstChild; const r=document.createRange();r.selectNodeContents(n);const s=getSelection();s.removeAllRanges();s.addRange(r); }''')
        expect(self.page.locator('#spice-selection')).to_be_enabled()
        self.page.click('#spice-selection')
        expect(self.page.locator('#spice-target')).to_contain_text('Выделение: жирного')
        report = {'summary': 'Замена', 'changes': [{'quote': quote, 'occurrence': 0, 'variants': ['выделенного'], 'why': 'Пример'}]}
        md = self.file.with_name('fixture.md'); md.write_text(self.source, encoding='utf-8')
        with patch.object(self.server.ai_provider, 'text_result', return_value=(report, {'provider': 'codex'})):
            self.page.click('#spice-run'); self.page.locator('#spice-changes textarea').wait_for(timeout=10000)
        md.write_text('Внешняя правка исходника.', encoding='utf-8')
        self.page.click('#spice-apply'); self.page.wait_for_function('granka.state.version === "v2" && !granka.state.busy')
        self.assertEqual(md.read_text(encoding='utf-8'), 'Внешняя правка исходника.')
        saved = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['versions'][-1]['text'], self.source.replace('жирного', 'выделенного'))
        expect(self.page.locator('#toast')).to_contain_text('Markdown изменился')

    def test_apply_rejects_article_changed_after_preview(self):
        self.panel(); self.page.click('#spice-pick'); self.page.locator('#doc > p').first.click()
        report = {'summary': 'Замена', 'changes': [{'quote': 'До', 'occurrence': 0, 'variants': ['Перед'], 'why': 'Пример'}]}
        with patch.object(self.server.ai_provider, 'text_result', return_value=(report, {'provider': 'codex'})):
            self.page.click('#spice-run'); self.page.locator('#spice-changes textarea').wait_for(timeout=10000)
        changed = json.loads(self.file.read_text(encoding="utf-8")); changed['external'] = 'fresh'; self.file.write_text(json.dumps(changed), encoding='utf-8')
        self.page.click('#spice-apply')
        expect(self.page.locator('#toast')).to_contain_text('Статья изменилась')
        saved = json.loads(self.file.read_text(encoding="utf-8")); self.assertEqual(saved['external'], 'fresh'); self.assertEqual(len(saved['versions']), 1)


if __name__ == '__main__':
    unittest.main()
