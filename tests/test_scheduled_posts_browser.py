from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_workspace_browser as workspace_tests
from test_publication_scheduler import fixture, TOKEN


class ScheduledPostsBrowserTests(unittest.TestCase):
    wait_ready=workspace_tests.WorkspaceBrowserTests.wait_ready
    reload_ready=workspace_tests.WorkspaceBrowserTests.reload_ready
    tearDown=workspace_tests.WorkspaceBrowserTests.tearDown
    go=workspace_tests.WorkspaceBrowserTests.go
    @classmethod
    def setUpClass(cls):workspace_tests.WorkspaceBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):workspace_tests.WorkspaceBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        workspace_tests.WorkspaceBrowserTests.setUp(self)
        self.server.TELEGRAM.save_settings({'chat_id':'@fixture_channel','token':TOKEN})
        mock=patch('tools.telegram.bot_call',side_effect=fixture)
        self.calls=mock.start();self.addCleanup(mock.stop)
        self.before=self.file.read_bytes()
        self.go('telegram');self.page.fill('#tg-text','Reviewed sample post — no user data.')
        self.page.click('#tg-prepare');expect(self.page.locator('#tg-schedule')).to_be_visible()
        self.assertFalse([c for c in self.calls.call_args_list if c.args[1].startswith('send')])

    def plan(self):
        self.page.click('#tg-schedule')
        self.when=(datetime.now(timezone.utc)+timedelta(hours=2)).replace(second=0,microsecond=0)
        self.page.fill('#schedule-when',self.when.strftime('%Y-%m-%dT%H:%M'))
        self.page.fill('#schedule-timezone','UTC')
        self.page.check('#schedule-confirm');self.page.click('#schedule-submit')
        expect(self.page.locator('#schedule-dialog')).not_to_be_visible()
        self.page.locator('#scheduled-details').evaluate('(n)=>n.open=true')
        expect(self.page.locator('#scheduled-posts')).to_contain_text('Reviewed sample post')

    def test_review_confirmation_queue_cancel_and_snapshot_preservation(self):
        self.page.click('#tg-schedule');self.page.click('#schedule-submit')
        expect(self.page.locator('#schedule-error')).not_to_be_empty()
        self.assertEqual(self.server.SCHEDULER.read()['entries'],[])
        self.page.click('#schedule-close');self.plan()
        row=self.page.locator('#scheduled-posts details');row.locator('summary').click()
        row.get_by_role('button',name='Отменить отправку').click()
        expect(self.page.locator('#scheduled-posts')).to_contain_text('Отменено')
        self.assertEqual(self.file.read_bytes(),self.before)
        self.assertFalse([c for c in self.calls.call_args_list if c.args[1].startswith('send')])

    def test_worker_publishes_one_fake_post_and_queue_shows_receipt(self):
        self.plan();self.server.SCHEDULER.clock=lambda:self.when.timestamp()+1
        self.server.SCHEDULER.tick();self.server.SCHEDULER.tick()
        self.page.click('#scheduled-refresh')
        expect(self.page.locator('#scheduled-posts')).to_contain_text('Опубликовано')
        self.assertEqual(len([c for c in self.calls.call_args_list if c.args[1].startswith('send')]),1)
        self.assertEqual(self.file.read_bytes(),self.before)

    def test_reschedule_and_english_confirmation_layout(self):
        self.plan();row=self.page.locator('#scheduled-posts details');row.locator('summary').click()
        row.get_by_role('button',name='Перенести',exact=True).click()
        self.page.fill('#schedule-when',(self.when+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'))
        self.page.check('#schedule-confirm');self.page.click('#schedule-submit')
        expect(self.page.locator('#schedule-dialog')).not_to_be_visible()
        self.assertEqual(self.server.SCHEDULER.read()['entries'][0]['schedule']['due'],(self.when+timedelta(days=1)).timestamp())
        self.assertFalse([c for c in self.calls.call_args_list if c.args[1].startswith('send')])

    def test_calendar_has_separate_buttons_for_delivery_queue_and_undated_materials(self):
        self.plan();self.go('calendar');self.page.click('#planner-scheduled-open')
        expect(self.page.locator('#scheduled-queue-dialog')).to_be_visible()
        expect(self.page.locator('#scheduled-posts details')).to_have_count(1)
        self.page.click('#scheduled-queue-close')
        expect(self.page.locator('#scheduled-queue-dialog')).not_to_be_visible()
        self.page.click('#planner-library')
        expect(self.page.locator('body')).to_have_attribute('data-section','library')


if __name__=='__main__':unittest.main()
