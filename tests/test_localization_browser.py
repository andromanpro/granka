"""Locale changes preserve real unsaved input; user prose is never translated."""
import json
import unittest
from playwright.sync_api import expect
import test_workspace_browser as workspace_tests


class LocalizationBrowserTests(unittest.TestCase):
    wait_ready=workspace_tests.WorkspaceBrowserTests.wait_ready
    reload_ready=workspace_tests.WorkspaceBrowserTests.reload_ready
    setUp=workspace_tests.WorkspaceBrowserTests.setUp
    tearDown=workspace_tests.WorkspaceBrowserTests.tearDown
    go=workspace_tests.WorkspaceBrowserTests.go

    @classmethod
    def setUpClass(cls):workspace_tests.WorkspaceBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):workspace_tests.WorkspaceBrowserTests.tearDownClass.__func__(cls)

    def test_switch_locale_preserves_unsaved_source_and_selection(self):
        before=self.file.read_bytes()
        self.page.click('[data-view=source]')
        draft='# Сохранить\n\nНастройки. Привет, world! 😀'
        self.page.fill('#texteditor',draft)
        self.page.locator('#texteditor').evaluate('(n)=>n.setSelectionRange(5,12)')
        self.go('settings');self.page.select_option('#ui-language','en')
        expect(self.page.locator('html')).to_have_attribute('lang','en')
        expect(self.page.locator('#settingssave')).to_have_text('Save settings')
        expect(self.page.locator('button[data-section=library]')).to_contain_text('Articles')
        expect(self.page.locator('#texteditor')).to_have_value(draft)
        self.assertEqual(self.page.locator('#texteditor').evaluate('(n)=>[n.selectionStart,n.selectionEnd]'),[5,12])
        self.page.click('#settingsclose')
        expect(self.page.locator('#texteditor')).to_have_value(draft)
        self.assertEqual(self.file.read_bytes(),before)
        self.go('settings');self.page.select_option('#ui-language','ru')
        expect(self.page.locator('#settingssave')).to_have_text('Сохранить настройки')
        self.assertEqual(self.file.read_bytes(),before)

    def test_english_workflows_layout_and_no_ai_or_publish_calls(self):
        calls=[]
        self.page.on('request',lambda request:calls.append(request.url)if request.method=='POST'and any(x in request.url for x in ('/jobs','/publish','/schedule','/article-draft'))else None)
        self.go('settings');self.page.select_option('#ui-language','en');self.page.click('#settingsclose')
        for width in (1440,900,390):
            self.page.set_viewport_size({'width':width,'height':1000})
            for section in ('library','settings','calendar','editor','telegram','history','spices','help'):
                self.go(section)
                self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'),(width,section))
        self.go('editor');self.page.click('#newarticle-main')
        expect(self.page.locator('#newarticle-next')).to_have_text('Next')
        self.page.click('#newarticle-close')
        self.assertEqual(calls,[])

    def test_language_selection_survives_reload_and_material_is_separate(self):
        self.go('settings');self.page.select_option('#ui-language','en')
        self.page.select_option('#material-language','en');self.page.select_option('#comments-language','ru')
        self.page.click('#settingssave');self.reload_ready();self.go('settings')
        expect(self.page.locator('#ui-language')).to_have_value('en')
        expect(self.page.locator('#material-language')).to_have_value('en')
        expect(self.page.locator('#comments-language')).to_have_value('ru')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'][0]['text'],self.source)

    def test_switch_preserves_inputs_appended_to_translatable_labels(self):
        self.go('settings');self.page.click('#site-add-destination')
        self.page.fill('[data-site-label]','My own title')
        self.page.fill('[data-site-host]','host.example.invalid')
        self.page.select_option('#ui-language','en')
        expect(self.page.locator('[data-site-label]')).to_have_value('My own title')
        expect(self.page.locator('[data-site-host]')).to_have_value('host.example.invalid')
        expect(self.page.locator('.connection-row').get_by_role('button',name='Delete',exact=True)).to_be_visible()
        self.page.select_option('#ui-language','ru')
        expect(self.page.locator('[data-site-host]')).to_have_value('host.example.invalid')
        expect(self.page.locator('.connection-row').get_by_role('button',name='Удалить',exact=True)).to_be_visible()

    def test_unavailable_language_resources_show_a_visible_error_without_writing(self):
        before=self.file.read_bytes()
        self.page.route('**/static/locales/en.json',lambda route:route.fulfill(status=404,body='not found'))
        self.page.reload()
        expect(self.page.locator('#language-load-error')).to_be_visible()
        self.assertEqual(self.file.read_bytes(),before)
        self.assertTrue(any('Cannot load UI language resources'in error for error in self.errors))
        self.errors.clear()


if __name__=='__main__':unittest.main()
