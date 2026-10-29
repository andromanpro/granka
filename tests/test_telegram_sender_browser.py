"""Real settings, retained draft, preview identity and server-side manual guard."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
import test_markdown_browser as browser_tests
from test_telegram_sender import TOKEN,reply

ROOT=Path(__file__).resolve().parents[1]
SHOTS=ROOT/'_audit/telegram-sender-2026-10-29'


class SenderBrowserTests(unittest.TestCase):
    wait_ready=browser_tests.MarkdownBrowserTests.wait_ready
    reload_ready=browser_tests.MarkdownBrowserTests.reload_ready

    @classmethod
    def setUpClass(cls):browser_tests.MarkdownBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):browser_tests.MarkdownBrowserTests.tearDownClass.__func__(cls)

    def setUp(self):
        browser_tests.MarkdownBrowserTests.setUp(self)
        self.server.TELEGRAM.save_settings({'chat_id':'@sender_fixture','token':TOKEN})
        self.page.click('#announcement-btn')
        expect(self.page.locator('#tg-prepare')).to_be_enabled()
        self.page.locator('#tg-cover').uncheck()
        self.page.locator('#tg-text').fill('Учебный пост, не отправлять в настоящий канал.')

    def tearDown(self):
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'],self.data['versions'])
        browser_tests.MarkdownBrowserTests.tearDown(self)

    def mode(self,value):
        self.page.click('[data-tg-tab=bot]')
        self.page.locator('#tg-publication-mode').select_option(value)
        self.page.click('#tg-settings-save')
        expect(self.page.locator('#tg-bot-status')).to_have_text('Настройки сохранены.')

    def test_channel_setting_persists_and_preview_names_the_sender(self):
        self.page.click('[data-tg-tab=bot]')
        expect(self.page.locator('#tg-publication-mode')).to_have_value('channel')
        expect(self.page.locator('#tg-publication-mode-help')).to_contain_text('личный профиль не используется')
        self.mode('channel')
        SHOTS.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(SHOTS/'channel-settings.png'),full_page=True)
        self.page.click('[data-tg-tab=compose]')
        with patch('tools.telegram.bot_call',side_effect=reply) as network:
            self.page.click('#tg-prepare')
            expect(self.page.locator('#tg-publish')).to_be_visible()
        self.assertEqual([c.args[1] for c in network.call_args_list],['getMe','getChat','getChatMember'])
        expect(self.page.locator('#tg-sender')).to_contain_text('От имени канала «Учебный канал»')
        expect(self.page.locator('#tg-sender')).to_contain_text('через бота')
        expect(self.page.locator('#tg-publish-step')).to_contain_text('Отправитель — канал «Учебный канал»')
        SHOTS.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(SHOTS/'channel-preview.png'),full_page=True)
        expect(self.page.locator('#tg-publish')).to_be_enabled()

    def test_manual_mode_keeps_draft_and_copy_but_cannot_send_even_via_api(self):
        with patch('tools.telegram.bot_call') as network:
            self.mode('manual')
            self.page.click('[data-tg-tab=compose]')
            expect(self.page.locator('#tg-prepare')).to_be_disabled()
            expect(self.page.locator('#tg-sender')).to_contain_text('Гранка ничего не отправляет')
            expect(self.page.locator('#tg-copy')).to_be_enabled()
            self.page.evaluate('navigator.clipboard.writeText=async text=>{window.copiedText=text}')
            self.page.click('#tg-copy')
            self.assertIn('Учебный пост',self.page.evaluate('window.copiedText'))
            self.page.click('#tg-back-article');self.page.click('#announcement-btn')
            expect(self.page.locator('#tg-prepare')).to_be_disabled()
            expect(self.page.locator('#tg-text')).to_have_value('Учебный пост, не отправлять в настоящий канал.')
            error=self.page.evaluate('''async () => {
                const {api,S}=await import('/static/ui/core.js');
                try {await api('/api/telegram/prepare?root=articles&f=fixture.granka.json',{
                    method:'POST',body:{version:S.version,expected_revision:S.revision,text:'Попытка обойти кнопку',include_cover:false,mode:'announcement',attachments:[]}});return ''}
                catch(error){return error.message}
            }''')
            self.assertIn('ручная публикация',error)
            self.page.click('[data-tg-tab=bot]')
            expect(self.page.locator('#tg-publication-mode')).to_have_value('manual')
            expect(self.page.locator('#tg-bot-credentials')).not_to_be_visible()
            expect(self.page.locator('#tg-check')).not_to_be_visible()
            self.page.set_viewport_size({'width':390,'height':900})
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth>innerWidth'))
            SHOTS.mkdir(parents=True,exist_ok=True)
            self.page.screenshot(path=str(SHOTS/'manual-settings-390.png'),full_page=True)
        network.assert_not_called()

    def test_stale_window_cannot_publish_after_another_window_switches_to_manual(self):
        with patch('tools.telegram.bot_call',side_effect=reply):
            self.page.click('#tg-prepare');expect(self.page.locator('#tg-publish')).to_be_visible()
        record=self.server.TELEGRAM.records('articles','fixture.granka.json')[0]
        before=self.server.TELEGRAM.record_path(record['id']).read_bytes()
        self.server.TELEGRAM.save_settings({'publication_mode':'manual'})
        with patch('tools.telegram.bot_call') as network:
            self.page.click('#tg-publish')
            expect(self.page.locator('#tg-status')).to_contain_text('ручная публикация')
        network.assert_not_called()
        self.assertEqual(self.server.TELEGRAM.record_path(record['id']).read_bytes(),before)
        self.assertEqual(self.server.TELEGRAM.read(record['id'])['status'],'prepared')
