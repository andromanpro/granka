import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_spices_browser as base


class SpiceSafetyBrowserTests(unittest.TestCase):
    wait_ready = base.SpiceBrowserTests.wait_ready
    reload_ready = base.SpiceBrowserTests.reload_ready
    panel = base.SpiceBrowserTests.panel
    @classmethod
    def setUpClass(cls): base.SpiceBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.SpiceBrowserTests.tearDownClass.__func__(cls)
    setUp = base.SpiceBrowserTests.setUp
    tearDown = base.SpiceBrowserTests.tearDown

    def target(self):
        self.panel(); self.page.click('#spice-pick'); self.page.locator('#doc > p').first.click()

    def report(self):
        return {'summary': 'Учебная замена', 'changes': [{'quote': 'До', 'occurrence': 0, 'variants': ['Перед'], 'why': 'Учебный пример'}]}

    def unchanged(self): self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'], self.data['versions'])

    def test_refusal_is_visible_and_never_retried(self):
        self.target()
        with patch.object(self.server.ai_provider, 'text_result', side_effect=RuntimeError('Учебный отказ модели')) as call:
            self.page.click('#spice-run'); expect(self.page.locator('#spice-status')).to_contain_text('отказ', timeout=15000)
            self.assertEqual(call.call_count, 1)
        self.unchanged(); expect(self.page.locator('#spice-apply')).to_be_disabled()

    def test_empty_proposals_are_not_a_successful_text_change(self):
        self.target()
        with patch.object(self.server.ai_provider, 'text_result', return_value=({'summary': 'Уместных изменений нет', 'changes': []}, {'provider': 'codex'})):
            self.page.click('#spice-run'); expect(self.page.locator('#spice-summary')).to_contain_text('изменений нет', timeout=15000)
        expect(self.page.locator('#spice-apply')).to_be_disabled(); self.unchanged()

    def test_invalid_model_quote_is_rejected_before_apply(self):
        self.target(); report = self.report(); report['changes'][0]['quote'] = 'Несуществующая цитата'
        with patch.object(self.server.ai_provider, 'text_result', return_value=(report, {'provider': 'codex'})) as call:
            self.page.click('#spice-run')
            self.page.wait_for_function('granka.state.jobs.some(j => j.kind === "spice" && j.status === "failed")')
            self.assertEqual(call.call_count, 1)
        self.unchanged(); expect(self.page.locator('#spice-apply')).to_be_disabled()

    def test_apply_failure_keeps_preview_and_old_version(self):
        self.target()
        with patch.object(self.server.ai_provider, 'text_result', return_value=(self.report(), {'provider': 'codex'})):
            self.page.click('#spice-run'); self.page.locator('#spice-changes textarea').wait_for(timeout=15000)
        with patch.object(self.server, 'mutate', side_effect=ValueError('Не удалось сохранить учебный пример')):
            self.page.click('#spice-apply'); expect(self.page.locator('#toast')).to_contain_text('Не удалось сохранить')
        expect(self.page.locator('#spice-result')).to_be_visible(); self.unchanged()

    def test_double_run_navigation_and_motion_preferences(self):
        self.data['versions'].insert(0, {'id': 'v0', 'text': '# Старый снимок\n\nПервоначальный абзац.'})
        self.file.write_text(json.dumps(self.data), encoding='utf-8'); self.reload_ready()
        self.target(); entered, release = threading.Event(), threading.Event()
        def respond(*args):
            entered.set(); release.wait(15); return self.report(), {'provider': 'codex'}
        with patch.object(self.server.ai_provider, 'text_result', side_effect=respond) as call:
            try:
                self.page.evaluate('document.querySelector("#spice-run").click(); document.querySelector("#spice-run").click()')
                self.assertTrue(entered.wait(3)); expect(self.page.locator('#doc .spice-working')).to_have_count(1)
                self.page.emulate_media(reduced_motion='no-preference')
                for mode in ['ripple', 'gleam', 'none']:
                    self.page.evaluate('async mode => {granka.state.settings.spice_motion=mode;(await import("/static/ui/core.js")).emit("settings")}', mode)
                    values = self.page.locator('#doc .spice-working').evaluate('(n)=>({self:getComputedStyle(n).animationName,after:getComputedStyle(n,"::after").animationName})')
                    if mode == 'ripple': self.assertNotEqual(values['self'], 'none')
                    elif mode == 'gleam': self.assertNotEqual(values['after'], 'none')
                    else: self.assertEqual(values, {'self': 'none', 'after': 'none'})
                self.page.evaluate('async () => {granka.state.settings.spice_motion="ripple";(await import("/static/ui/core.js")).emit("settings")}')
                self.page.emulate_media(reduced_motion='reduce')
                self.assertEqual(self.page.locator('#doc .spice-working').evaluate('(n)=>getComputedStyle(n).animationName'), 'none')
                self.assertEqual(self.page.locator('#doc .spice-working').evaluate('(n)=>getComputedStyle(n,"::after").animationName'), 'none')
                self.page.emulate_media(reduced_motion='no-preference'); self.page.evaluate('document.body.classList.add("no-motion")')
                self.assertEqual(self.page.locator('#doc .spice-working').evaluate('(n)=>getComputedStyle(n).animationName'), 'none')
                self.page.evaluate('async () => (await import("/static/ui/history.js")).selectVersion("v0")')
                expect(self.page.locator('#doc .spice-working')).to_have_count(0)
                expect(self.page.locator('#spice-apply')).to_be_disabled()
                self.page.evaluate('async () => (await import("/static/ui/history.js")).selectVersion("v1")')
                expect(self.page.locator('#doc .spice-working')).to_have_count(1)
                other = self.directory / 'other-safety.granka.json'; other.write_text(json.dumps({'doc': 'other', 'versions': [{'id': 'v1', 'text': '# Другой текст\n\nСовсем другой абзац.'}], 'annotations': []}), encoding='utf-8')
                self.page.evaluate('granka.loadDocument("articles", "other-safety.granka.json")')
                expect(self.page.locator('#doc .spice-working')).to_have_count(0)
                self.assertEqual(call.call_count, 1); self.unchanged()
            finally: release.set()
        self.assertEqual(self.page.evaluate('granka.state.file'), 'other-safety.granka.json')

    def test_edit_proposal_and_double_apply_preserve_neighbors(self):
        self.target()
        with patch.object(self.server.ai_provider, 'text_result', return_value=(self.report(), {'provider': 'codex'})):
            self.page.click('#spice-run'); self.page.locator('#spice-changes textarea').wait_for(timeout=15000)
        expect(self.page.locator('#spice-summary')).to_be_focused()
        self.assertTrue(self.page.locator('#spice-changes textarea').evaluate('(n)=>{const b=n.getBoundingClientRect();return b.top<innerHeight && b.bottom>0}'))
        self.page.fill('#spice-changes textarea', 'Сначала'); self.unchanged()
        self.page.evaluate('document.querySelector("#spice-apply").click(); document.querySelector("#spice-apply").click()')
        self.page.wait_for_function('granka.state.version === "v2" && !granka.state.busy')
        value = json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(len(value['versions']), 2)
        self.assertEqual(value['versions'][0]['text'], self.source)
        self.assertEqual(value['versions'][-1]['text'], self.source.replace('До', 'Сначала', 1))
