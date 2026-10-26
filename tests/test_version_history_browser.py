from copy import deepcopy
import json
from pathlib import Path
import unittest
from playwright.sync_api import expect
import test_markdown_browser as markdown_browser

ROOT = Path(__file__).resolve().parents[1]


class VersionHistoryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        markdown_browser.MarkdownBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        markdown_browser.MarkdownBrowserTests.tearDownClass.__func__(cls)

    wait_ready = markdown_browser.MarkdownBrowserTests.wait_ready
    reload_ready = markdown_browser.MarkdownBrowserTests.reload_ready
    tearDown = markdown_browser.MarkdownBrowserTests.tearDown

    def setUp(self):
        markdown_browser.MarkdownBrowserTests.setUp(self)
        self.data['versions'].extend([
            {'id': 'v2', 'text': self.source.replace('жирного', 'крупного'), 'origin': 'spices', 'label': 'приправы'},
            {'id': 'v3', 'text': self.source.replace('жирного', 'специального'), 'label': 'поздняя правка'}])
        self.file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        self.md = self.file.with_name('fixture.md'); self.md.write_text(self.data['versions'][-1]['text'], encoding='utf-8')
        self.reload_ready(); self.page.wait_for_function('granka.state.version === "v3" && !granka.state.busy')
        self.page.click('[data-pane=history]')

    def row(self, version):
        return self.page.locator('#version-history [data-version="'+version+'"]')

    def test_open_compare_any_pair_and_restore_without_losing_newer_versions(self):
        before = deepcopy(self.data['versions'])
        self.row('v1').get_by_role('button', name='Открыть', exact=True).click()
        self.page.wait_for_function('granka.state.version === "v1"')
        self.assertEqual(self.page.locator('#versel').input_value(), 'v1')
        self.assertEqual(self.page.locator('#doc').get_attribute('contenteditable'), 'false')
        self.row('v3').get_by_role('button', name='Сравнить с открытой', exact=True).click()
        self.assertEqual(self.page.locator('#diff-from').input_value(), 'v3')
        self.assertEqual(self.page.locator('#diff-to').input_value(), 'v1')
        self.page.select_option('#diff-from', 'v1'); self.page.select_option('#diff-to', 'v3')
        self.assertIn('специального', ''.join(self.page.locator('#diffR ins').all_inner_texts()))
        self.page.click('#diff-swap')
        self.assertEqual(self.page.locator('#diff-from').input_value(), 'v3')
        self.page.select_option('#diff-from', 'v2'); self.page.select_option('#diff-to', 'v2')
        expect(self.page.locator('#diff-status')).to_have_text('Выбрана одна и та же версия')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'], before)
        self.row('v1').get_by_role('button', name='Вернуть в работу', exact=True).click()
        self.page.wait_for_function('granka.state.version === "v4" && !granka.state.busy')
        self.assertEqual(self.page.locator('#versel').input_value(), 'v4')
        saved = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['versions'][:3], before); self.assertEqual(saved['versions'][-1]['text'], self.source)
        self.assertEqual(saved['versions'][-1]['restored_from'], 'v1')
        self.assertEqual(self.md.read_text(encoding='utf-8'), self.source)
        self.page.screenshot(path=str(ROOT/'_audit/history-2026-10-24/restored.png'))
        self.row('v2').get_by_role('button', name='Сравнить с открытой', exact=True).click()
        self.page.screenshot(path=str(ROOT/'_audit/history-2026-10-24/comparison.png'))

    def test_unsaved_open_cancel_and_restore_discard_preserve_navigation_guard(self):
        self.page.click('[data-view=source]'); self.page.fill('#texteditor', '# Несохранённая правка\n\nТекст.')
        self.row('v1').get_by_role('button', name='Открыть', exact=True).click()
        self.page.locator('#unsaved-dialog[open]').wait_for(); self.page.click('#unsaved-cancel')
        self.assertEqual(self.page.evaluate('granka.state.version'), 'v3')
        self.assertIn('Несохранённая', self.page.locator('#texteditor').input_value())
        self.row('v1').get_by_role('button', name='Вернуть в работу', exact=True).click()
        self.page.locator('#unsaved-dialog[open]').wait_for(); self.page.click('#unsaved-discard')
        self.page.wait_for_function('granka.state.version === "v4" && !granka.state.busy')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text'], self.source)

    def test_external_markdown_is_preserved_and_stale_revision_is_rejected(self):
        body = self.page.evaluate('''() => ({source_version:'v1',base_version:'v3',expected_revision:granka.state.revision,operation_id:'stale-restore'})''')
        changed = json.loads(self.file.read_text(encoding='utf-8')); changed['external'] = 'fresh'
        self.file.write_text(json.dumps(changed), encoding='utf-8')
        code = self.page.evaluate('''async body => {try {await granka.api('/api/restore-version?root=articles&f=fixture.granka.json',{method:'POST',body});return 200;}catch(e){return e.status;}}''', body)
        self.assertEqual(code, 409); self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']), 3)
        self.reload_ready(); self.page.wait_for_function('granka.state.version === "v3" && !granka.state.busy'); self.page.click('[data-pane=history]')
        self.md.write_text('Внешняя новая рукопись.', encoding='utf-8')
        self.row('v1').get_by_role('button', name='Вернуть в работу', exact=True).click()
        self.page.wait_for_function('granka.state.version === "v4" && !granka.state.busy')
        self.assertEqual(self.md.read_text(encoding='utf-8'), 'Внешняя новая рукопись.')
        expect(self.page.locator('#toast')).to_contain_text('Markdown изменился')

    def test_restore_retry_is_idempotent_and_mobile_controls_fit(self):
        body = self.page.evaluate('''() => ({source_version:'v1',base_version:'v3',expected_revision:granka.state.revision,operation_id:'same-restore'})''')
        result = self.page.evaluate('''async body => {const path='/api/restore-version?root=articles&f=fixture.granka.json';const a=await granka.api(path,{method:'POST',body});const b=await granka.api(path,{method:'POST',body});return {a,b};}''', body)
        self.assertEqual(result['a']['version'], 'v4'); self.assertTrue(result['b']['idempotent'])
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']), 4)
        self.reload_ready(); self.page.wait_for_function('granka.state.version === "v4" && !granka.state.busy')
        self.page.set_viewport_size({'width': 375, 'height': 950})
        self.page.evaluate('granka.pane("history");document.querySelector("#inspector").classList.add("mobile-open")')
        self.row('v1').get_by_role('button', name='Сравнить с открытой', exact=True).click()
        self.assertNotIn('mobile-open', self.page.locator('#inspector').get_attribute('class'))
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.page.screenshot(path=str(ROOT/'_audit/history-2026-10-24/mobile.png'))


if __name__ == '__main__':
    unittest.main()
