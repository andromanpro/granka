import json
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_creation_meter_browser as base
from tools.publication_plan import PublicationPlan


class ArticleTrashBrowserTests(unittest.TestCase):
    wait_ready = base.CreationMeterBrowserTests.wait_ready
    reload_ready = base.CreationMeterBrowserTests.reload_ready
    tearDown = base.CreationMeterBrowserTests.tearDown
    @classmethod
    def setUpClass(cls): base.CreationMeterBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): base.CreationMeterBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        # The previous case may deliberately remove its entire selected source.
        # Bootstrap against the base fixture before choosing this case's root.
        self.server.ROOTS = {'articles': self.directory}
        self.server.GIT_ROOTS = dict(self.server.ROOTS)
        base.CreationMeterBrowserTests.setUp(self)
        state = patch.object(self.server.ai_provider, 'STATE', self.work / 'state')
        state.start(); self.addCleanup(state.stop)
        self.md = self.work / 'fixture.md'; self.md.write_text(self.source, encoding='utf-8')
        self.assets = self.work / 'fixture.assets'; self.assets.mkdir(); (self.assets / 'clip.mp4').write_bytes(b'fictional clip')
        self.server.CALENDAR = PublicationPlan(lambda: self.server.ai_provider.STATE,
            lambda: self.server.library_sources(), hidden=self.server.TRASH.hidden_entry)

    def library(self):
        self.page.click('button[data-section=library]')
        expect(self.page.locator('#source-list .source-item')).to_have_count(1)

    def ask(self):
        self.library(); self.page.locator('#source-list > .source-item > .source-trash').click()
        expect(self.page.locator('#trash-confirm')).to_be_visible()

    def put(self):
        self.ask(); self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#source-trash')).to_have_text('Корзина · 1', timeout=15000)
        self.assertFalse(self.md.exists()); self.assertFalse(self.file.exists())

    def test_cancel_move_restore_preserves_article_versions_and_media(self):
        before = self.file.read_bytes(); self.ask(); self.page.click('#trash-confirm-cancel')
        self.assertEqual(self.file.read_bytes(), before)
        self.page.locator('#source-list > .source-item > .source-trash').click(); self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#source-trash')).to_have_text('Корзина · 1', timeout=15000)
        expect(self.page.locator('#source-list .source-item')).to_have_count(0)
        self.page.click('#source-trash'); expect(self.page.locator('.trash-item')).to_have_count(1)
        self.page.locator('.trash-item button').click()
        expect(self.page.locator('#trash-list')).to_contain_text('Корзина пуста')
        self.assertEqual(self.file.read_bytes(), before); self.assertEqual((self.assets / 'clip.mp4').read_bytes(), b'fictional clip')
        self.page.click('#trash-close'); expect(self.page.locator('#source-list .source-item')).to_have_count(1)

    def test_clear_needs_second_confirmation_and_cancel_keeps_archive(self):
        self.put(); self.page.click('#source-trash'); self.page.click('#trash-empty')
        expect(self.page.locator('#trash-confirm-detail')).to_contain_text('навсегда')
        self.page.click('#trash-confirm-cancel'); self.assertEqual(len(self.server.TRASH.read()['entries']), 1)
        self.page.click('#trash-empty'); self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#trash-list')).to_contain_text('Корзина пуста')
        expect(self.page.locator('#trash-empty')).to_be_disabled()
        self.assertFalse(self.md.exists()); self.assertFalse(self.file.exists())
        self.assertFalse(list((self.server.TRASH.directory / 'payload').glob('*')))

    def test_bare_library_url_refreshes_after_move_and_drops_stale_document(self):
        self.page.goto(self.base + '/#library')
        self.put()
        expect(self.page.locator('#source-list .source-item')).to_have_count(0)
        self.assertIsNone(self.page.evaluate('granka.state.document'))

    def test_lost_move_response_retries_same_operation_once(self):
        lost = []; operations = []
        def route(r):
            if r.request.method != 'POST': r.continue_(); return
            operations.append(r.request.post_data_json['operation_id'])
            if not lost:
                response = r.fetch(); self.assertEqual(response.status, 200); lost.append(True)
                r.fulfill(status=502, json={'error': 'Ответ потерян после переноса'})
            else: r.continue_()
        self.page.route('**/api/article-trash', route)
        self.ask(); self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#trash-confirm-status')).to_contain_text('Ответ потерян')
        self.page.click('#trash-confirm-apply'); expect(self.page.locator('#source-trash')).to_have_text('Корзина · 1')
        self.assertEqual(len(set(operations)), 1); self.assertEqual(len(self.server.TRASH.read()['entries']), 1)

    def test_unsaved_text_guard_can_cancel_move(self):
        self.page.click('[data-view=source]'); self.page.fill('#texteditor', self.source + '\nНесохранённое.')
        self.library(); self.page.locator('#source-list > .source-item > .source-trash').click()
        expect(self.page.locator('#unsaved-dialog')).to_be_visible()
        self.page.click('#unsaved-cancel'); self.assertTrue(self.file.exists())
        expect(self.page.locator('#trash-confirm')).not_to_be_visible()

    def test_edit_during_slow_preview_is_guarded_again_before_move(self):
        held = []
        self.page.route('**/api/article-trash/preview', lambda route: held.append(route))
        self.library(); self.page.locator('#source-list > .source-item > .source-trash').click()
        self.page.wait_for_timeout(100); self.assertEqual(len(held), 1)
        self.page.click('button[data-section=editor]'); self.page.click('[data-view=source]')
        draft = self.source + '\nПравка во время проверки состава.'
        self.page.fill('#texteditor', draft)
        held[0].continue_(); expect(self.page.locator('#trash-confirm')).to_be_visible()
        self.page.click('#trash-confirm-apply'); expect(self.page.locator('#unsaved-dialog')).to_be_visible()
        self.page.click('#unsaved-cancel')
        expect(self.page.locator('#trash-confirm-status')).to_contain_text('Перенос отменён')
        expect(self.page.locator('#texteditor')).to_have_value(draft)
        self.assertTrue(self.file.exists()); self.assertEqual(self.server.TRASH.read()['entries'], [])

    def test_active_job_blocks_and_changed_preview_does_not_delete(self):
        ident = 'a' * 24; folder = self.server.JOBS.directory / ident; folder.mkdir()
        (folder / 'job.json').write_text(json.dumps({'id': ident, 'root': 'articles', 'rel': self.file.name,
            'kind': 'analysis', 'status': 'running', 'created': 0}), encoding='utf-8')
        self.library(); self.page.locator('#source-list > .source-item > .source-trash').click()
        expect(self.page.locator('#toast')).to_contain_text('задание')
        (folder / 'job.json').unlink(); self.page.locator('#source-list > .source-item > .source-trash').click()
        expect(self.page.locator('#trash-confirm')).to_be_visible()
        self.md.write_text('Внешняя правка.', encoding='utf-8'); self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#trash-confirm-status')).to_contain_text('изменились')
        self.assertEqual(self.md.read_text(encoding='utf-8'), 'Внешняя правка.'); self.assertTrue(self.file.exists())

    def test_restore_conflict_shows_reason_and_preserves_both_copies(self):
        self.put(); self.md.write_text('Новая статья по тому же пути.', encoding='utf-8')
        self.page.click('#source-trash'); self.page.locator('.trash-item button').click()
        expect(self.page.locator('#trash-status')).to_contain_text('Имя занято')
        self.assertIn('Новая статья', self.md.read_text(encoding='utf-8'))
        self.assertEqual(len(self.server.TRASH.read()['entries']), 1)

    def test_copy_has_its_own_trash_button_without_deleting_primary_source(self):
        other = self.work / 'other.md'; other.write_text(self.source, encoding='utf-8')
        second = self.work / 'other.granka.json'; second.write_bytes(self.file.read_bytes())
        self.library(); self.page.locator('.source-copies summary').click()
        self.page.locator('.source-copies .source-trash').click(); self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#source-trash')).to_have_text('Корзина · 1')
        self.assertTrue(self.file.exists()); self.assertTrue(self.md.exists()); self.assertFalse(second.exists()); self.assertFalse(other.exists())

    def test_narrow_keyboard_confirmation_focus(self):
        self.page.set_viewport_size({'width': 375, 'height': 900}); self.ask()
        expect(self.page.locator('#trash-confirm-cancel')).to_be_focused()
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.keyboard.press('Escape'); expect(self.page.locator('#trash-confirm')).not_to_be_visible()
        expect(self.page.locator('#source-list > .source-item > .source-trash')).to_be_focused()
        self.assertTrue(self.file.exists())

    def test_new_trash_entry_after_clear_preview_cannot_be_purged_unseen(self):
        self.put(); self.page.click('#source-trash'); self.page.click('#trash-empty')
        other = self.work / 'other.md'; other.write_text('# Другая статья', encoding='utf-8')
        body = {'root': 'articles', 'rel': other.name, 'operation_id': 'b' * 32}
        self.server.TRASH.put({**body, 'expected_revision': self.server.TRASH.preview(body)['revision']})
        self.page.click('#trash-confirm-apply')
        expect(self.page.locator('#trash-confirm-status')).to_contain_text('Корзина изменилась')
        self.assertEqual(len(self.server.TRASH.read()['entries']), 2)
