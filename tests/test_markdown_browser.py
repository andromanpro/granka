"""Browser regressions: formatted text must still address the original Markdown."""
import base64
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from tools.jobs import Jobs
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


class MarkdownBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('granka_markdown_test',ROOT/'granka-server.py')
        cls.server=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.server)
        cls.temp=tempfile.TemporaryDirectory(prefix='granka-markdown-test-')
        cls.directory=Path(cls.temp.name).resolve()
        cls.server.ROOTS={'articles':cls.directory}
        cls.server.GIT_ROOTS={'articles':cls.directory}
        cls.server.IMAGE_DIRS=[cls.directory]
        cls.server.H.log_message=lambda *args:None
        cls.http=cls.server.ThreadingHTTPServer(('127.0.0.1',0),cls.server.H)
        cls.base=f'http://127.0.0.1:{cls.http.server_port}'
        cls.server.ALLOWED_HOSTS={f'127.0.0.1:{cls.http.server_port}'}
        threading.Thread(target=cls.http.serve_forever,daemon=True).start()
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(channel=os.environ.get('GRANKA_BROWSER_CHANNEL') or ('msedge' if os.name=='nt' else None),headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.http.shutdown();cls.http.server_close();cls.temp.cleanup()

    def setUp(self):
        self.server.JOBS=Jobs(self.directory/('jobs-'+self._testMethodName))
        from tools.author_preferences import AuthorPreferences
        self.server.CORRECTIONS=self.directory/(self._testMethodName+'-rules.md')
        self.server.PREFERENCES=AuthorPreferences(lambda:self.directory/('prefs-'+self._testMethodName),lambda:self.server.ROOTS,lambda:self.server.CORRECTIONS)
        from tools.telegram import Telegram
        self.server.TELEGRAM=Telegram(lambda:self.directory/('tg-'+self._testMethodName))
        from tools.publication_plan import PublicationPlan
        self.server.CALENDAR=PublicationPlan(lambda:self.directory/('calendar-'+self._testMethodName))
        self.state_patch=patch.object(self.server.ai_provider,'STATE',self.directory/'ai-state');self.state_patch.start()
        self.config_patch=patch.object(self.server.ai_provider,'CONFIG',self.directory/(self._testMethodName+'-config.json'));self.config_patch.start()
        self.file=self.directory/'fixture.granka.json'
        self.source='# Заголовок 😀\n\nДо **жирного** есть [ссылка](https://example.org/).\n\n## Раздел\n\n- пункт один\n- пункт два\n\n> Цитата автора.\n'
        self.data={'doc':'fixture.md','versions':[{'id':'v1','text':self.source}],'annotations':[]}
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.context=self.browser.new_context(viewport={'width':1440,'height':1000})
        self.page=self.context.new_page()
        self.errors=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.goto(self.base+'/workbench');self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')

    def tearDown(self):
        self.context.close()
        self.server.JOBS.pool.shutdown(wait=True);self.state_patch.stop();self.config_patch.stop()
        self.assertEqual(self.errors,[])

    def test_source_mapping_and_safe_rendering(self):
        results=self.page.evaluate((ROOT/'tests/markdown-browser-cases.js').read_text(encoding='utf-8'))
        self.assertEqual(len(results),21)

    def test_unsaved_navigation_cancel_save_discard_and_failed_save(self):
        self.data['versions'].append({'id':'v2','text':self.source+'\nВторая версия.\n'})
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        other=self.directory/'other.granka.json'
        other.write_text(json.dumps({'doc':'other.md','versions':[{'id':'v1','text':'# Другая статья\n\nЕё текст.'}],'annotations':[]}),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('granka.state.version === "v2" && !granka.state.busy')
        self.page.click('[data-view=source]')
        edited='# Сохранить перед переходом\n\nМой незавершённый текст.'
        self.page.fill('#texteditor',edited)
        self.page.select_option('#docsel','articles|other.granka.json')
        self.page.locator('#unsaved-dialog[open]').wait_for()
        self.page.click('#unsaved-cancel')
        self.assertEqual(self.page.evaluate('granka.state.file'),'fixture.granka.json')
        self.assertEqual(self.page.locator('#texteditor').input_value(),edited)
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']),2)
        self.page.select_option('#docsel','articles|other.granka.json')
        self.page.click('#unsaved-save')
        self.page.wait_for_function('granka.state.file === "other.granka.json" && !granka.state.busy')
        saved=json.loads(self.file.read_text(encoding='utf-8'))['versions']
        self.assertEqual(len(saved),3);self.assertEqual(saved[-1]['text'],edited)
        self.page.select_option('#docsel','articles|fixture.granka.json')
        self.page.wait_for_function('granka.state.file === "fixture.granka.json" && !granka.state.busy')
        self.page.fill('#texteditor','# Не сохранять\n\nВременная правка.')
        self.page.select_option('#versel','v1')
        self.page.click('#unsaved-discard')
        self.page.wait_for_function('granka.state.version === "v1" && !granka.state.editor')
        self.assertEqual(self.page.locator('#texteditor').input_value(),self.source)
        self.assertIsNone(self.page.evaluate('localStorage.getItem("granka.text-draft:articles|fixture.granka.json")'))
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']),3)
        self.page.select_option('#versel','v3')
        self.page.wait_for_function('granka.state.version === "v3"')
        failed='# Сохранение с конфликтом\n\nЭтот текст должен остаться в редакторе.'
        self.page.fill('#texteditor',failed)
        self.page.route('**/api/text?*',lambda route:route.fulfill(status=409,json={'error':'conflict'}))
        self.page.select_option('#docsel','articles|other.granka.json')
        self.page.click('#unsaved-save')
        self.page.wait_for_function('!granka.state.busy && document.querySelector("#editor-status").textContent.includes("снаружи")')
        self.assertEqual(self.page.evaluate('granka.state.file'),'fixture.granka.json')
        self.assertEqual(self.page.locator('#texteditor').input_value(),failed)
        self.assertIsNotNone(self.page.evaluate('granka.state.editor'))

    def test_library_pickers_manual_folder_cancel_and_file_scope(self):
        from tools.library_locations import LibraryLocations
        locations=LibraryLocations(lambda:self.directory/(self._testMethodName+'-locations'))
        settings=self.server.environment.load()
        settings['planning']={'source_dirs':{'articles':['.']}}
        roots=dict(self.server.ROOTS);git_roots=dict(self.server.GIT_ROOTS);images=list(self.server.IMAGE_DIRS)
        try:
            with tempfile.TemporaryDirectory(prefix='granka-source-choice-') as folder, patch.object(self.server,'LOCATIONS',locations), patch.object(self.server.environment,'load',return_value=settings):
                base=Path(folder);chosen=base/'выбранная.MD';sibling=base/'соседняя.md'
                chosen.write_text('# Выбранная статья\n\nТекст выбранной статьи.',encoding='utf-8')
                sibling.write_text('# Соседняя статья\n\nДругой текст рядом.',encoding='utf-8')
                original=chosen.read_bytes()
                original_fixture=self.file.read_bytes()
                self.page.locator('#doc p').last.click()
                self.page.keyboard.press('End');self.page.keyboard.insert_text(' Несохранённая приписка.')
                self.page.click('#opensrc')
                self.page.locator('#source-file').wait_for()
                with patch.object(self.server,'choose_local_path',return_value=None) as picker:
                    self.page.click('#source-file')
                    self.page.wait_for_function('document.querySelector("#source-location-status").textContent === "Выбор отменён."')
                    self.assertFalse(locations.file.exists());picker.assert_called_once_with('file')
                with patch.object(self.server,'choose_local_path',side_effect=ValueError('Системное окно недоступно. Укажи путь вручную.')):
                    self.page.click('#source-folder')
                    self.page.wait_for_function('document.querySelector("#source-location-status").textContent.includes("вручную")')
                with patch.object(self.server,'choose_local_path',return_value=str(chosen)) as picker:
                    self.page.click('#source-file')
                    self.page.locator('#unsaved-dialog[open]').wait_for()
                    self.page.click('#unsaved-cancel')
                    self.assertFalse(chosen.with_suffix('.granka.json').exists())
                    self.assertEqual(self.file.read_bytes(),original_fixture)
                    self.assertIn('Несохранённая приписка.',self.page.locator('#doc').inner_text())
                    self.page.click('#source-file')
                    self.page.locator('#unsaved-dialog[open]').wait_for()
                    self.page.click('#unsaved-discard')
                    self.page.wait_for_function('granka.state.document?.doc === "выбранная.MD" && !granka.state.busy')
                    self.assertEqual(picker.call_count,2)
                self.assertEqual(chosen.read_bytes(),original)
                self.assertTrue(chosen.with_suffix('.granka.json').is_file())
                self.assertFalse(sibling.with_suffix('.granka.json').exists())
                self.page.click('#opensrc')
                self.assertEqual(self.page.locator('#source-list .source-item').filter(has_text='Соседняя статья').count(),0)
                self.page.locator('.source-locations summary').click()
                self.page.select_option('#source-path-kind','folder');self.page.fill('#source-path',str(base))
                self.page.click('#source-path-add')
                self.page.locator('#source-list .source-item').filter(has_text='Соседняя статья').wait_for()
                self.assertEqual(chosen.read_bytes(),original)
                self.assertFalse(sibling.with_suffix('.granka.json').exists())
                self.page.set_viewport_size({'width':500,'height':900})
                self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
                self.page.screenshot(path=str(ROOT/'_audit/library-source-picker.png'))
        finally:
            self.server.ROOTS.clear();self.server.ROOTS.update(roots)
            self.server.GIT_ROOTS.clear();self.server.GIT_ROOTS.update(git_roots)
            self.server.IMAGE_DIRS[:]=images

    def test_native_select_options_are_readable_and_keep_keyboard_navigation(self):
        self.data['versions'].append({'id':'v2','text':self.source+'\nВторая версия.\n'})
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.version === "v2" && !granka.state.busy')
        before=self.file.read_bytes()
        # Native popups may use a light OS theme even when the page is dark.
        # Check actual options, including grouped options, in both OS themes.
        self.page.evaluate('''() => {
            const group=document.createElement('optgroup');group.label='Тестовая группа';
            group.append(new Option('Тестовый пункт','test-only'));
            document.querySelector('#docsel').append(group);
        }''')
        for scheme,forced in [('light','none'),('dark','none'),('light','active')]:
            self.page.emulate_media(color_scheme=scheme,forced_colors=forced)
            styles=self.page.locator('select option, select optgroup').evaluate_all('''nodes => nodes.map(node => {
                const style=getComputedStyle(node);
                return {color:style.color,background:style.backgroundColor};
            })''')
            self.assertTrue(styles)
            for style in styles:
                colors=[]
                for key in ['color','background']:
                    rgb=[float(v) for v in re.findall(r'[\d.]+',style[key])]
                    self.assertEqual(len(rgb),3,(scheme,forced,style))
                    linear=[v/255/12.92 if v/255<=0.04045 else ((v/255+0.055)/1.055)**2.4 for v in rgb]
                    colors.append(sum(a*b for a,b in zip(linear,[0.2126,0.7152,0.0722])))
                contrast=(max(colors)+0.05)/(min(colors)+0.05)
                self.assertGreaterEqual(contrast,4.5,(scheme,forced,style,contrast))
        self.page.emulate_media(color_scheme='dark',forced_colors='none')
        self.page.locator('#versel').focus()
        self.page.keyboard.press('Home');self.page.keyboard.press('Enter')
        self.page.wait_for_function('granka.state.version === "v1" && !granka.state.busy')
        self.assertEqual(self.file.read_bytes(),before)

    def test_publication_calendar_preview_apply_and_edit(self):
        self.data['versions'].append({'id':'v2','text':self.source+'\nНовая строка.\n'})
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.version === "v2" && !granka.state.busy')
        for view in ['article','source','diff']:
            self.page.click('[data-view='+view+']')
            self.assertTrue(self.page.locator('#planner-btn').is_visible(),view)
        self.page.click('#planner-btn')
        self.page.locator('#plannerdialog[open]').wait_for()
        expect(self.page.locator('#planner-btn')).to_have_attribute('aria-expanded','true')
        self.page.click('#planner-add')
        self.page.fill('#planner-entry-title','Следующая статья')
        self.page.select_option('#planner-entry-status','ready')
        self.page.click('#planner-entry-link')
        self.page.click('#planner-entry-save')
        self.page.locator('#planner-queue .planner-card').wait_for()
        self.assertEqual(self.server.CALENDAR.read()['entries'][0]['file'],'fixture.granka.json')
        self.page.click('#planner-distribute')
        self.page.fill('#planner-start','2026-09-16')
        self.page.click('#planner-preview')
        self.page.locator('.planner-proposed-row').wait_for()
        self.assertIsNone(self.server.CALENDAR.read()['entries'][0]['date'])
        self.page.click('#planner-apply')
        card=self.page.locator('.planner-day[data-date="2026-09-16"] .planner-card')
        card.wait_for();card.click()
        self.page.fill('#planner-entry-title','Обновлённый план')
        self.page.check('#planner-entry-pinned')
        self.page.click('#planner-entry-save')
        self.page.wait_for_function('!document.querySelector("#planner-entry-dialog").open')
        saved=self.server.CALENDAR.read()['entries'][0]
        self.assertEqual(saved['title'],'Обновлённый план');self.assertTrue(saved['pinned'])
        self.assertEqual(card.get_attribute('draggable'),'false')
        self.page.screenshot(path=str(ROOT/'_audit/calendar-desktop.png'))
        self.page.set_viewport_size({'width':500,'height':900})
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.page.screenshot(path=str(ROOT/'_audit/calendar-mobile.png'))
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'],self.data['versions'])
        self.page.click('#planner-close')
        self.page.wait_for_function('location.hash !== "#calendar"')
        self.assertFalse(self.page.url.endswith('#calendar'))
        self.page.goto(self.base+'/#calendar')
        self.page.locator('#plannerdialog[open]').wait_for()
        expect(self.page.locator('#planner-btn')).to_have_attribute('aria-expanded','true')
        self.page.keyboard.press('Escape')
        self.page.wait_for_function('!document.querySelector("#plannerdialog").open')
        expect(self.page.locator('#planner-btn')).to_have_attribute('aria-expanded','false')

    def test_published_article_banner_opens_update_with_original_address(self):
        self.page.route('**/api/site?*',lambda route:route.fulfill(json={
            'configured':True,'environments':{'prod':{'url':'https://example.invalid'}},'plans':[],'source_hash':'new',
            'publication':{'url':'https://example.invalid/article','post_id':42,'post_status':'publish','version':'v1','date':'2026-09-01','changed':True},
            'metadata':{'title':'Заголовок','slug':'original-address','date':'2026-09-01','status':'publish','categories':['Тест'],'tags':[],'excerpt':''}}))
        self.page.reload();self.page.locator('#publication-banner').wait_for()
        self.assertIn('Есть неопубликованные изменения',self.page.locator('#publication-description').inner_text())
        self.page.click('#publication-edit')
        self.page.wait_for_function('document.querySelector("#site-slug").value === "original-address"')
        self.assertEqual(self.page.locator('#site-status').input_value(),'publish')
        self.assertTrue(self.page.locator('#site-slug').evaluate('e=>e.readOnly'))
        self.assertTrue(self.page.locator('#site-date').evaluate('e=>e.readOnly'))

    def test_calendar_imports_drafts_excludes_restores_and_drags_between_days(self):
        from tools.publication_plan import PublicationPlan
        from tools.planning_library import scan_library
        folder=self.directory/'calendar-import-fixture';folder.mkdir(exist_ok=True)
        raw=folder/'raw.md';content='# Черновик для плана\n\nТекст статьи.\n\n[ЛИЧНОЕ: добавь случай]\n'
        raw.write_text(content,encoding='utf-8')
        (folder/'other.md').write_text('# Второй текст\n\nТекст без вставок.',encoding='utf-8')
        self.server.CALENDAR=PublicationPlan(lambda:self.directory/'calendar-library-state',
            lambda:scan_library(self.server.ROOTS,{'articles':['calendar-import-fixture']}))
        self.page.click('#planner-btn')
        queue=self.page.locator('#planner-queue .planner-card').filter(has_text='Черновик для плана')
        queue.wait_for();self.assertIn('25%',queue.inner_text())
        queue.get_by_role('button',name='Исключить',exact=True).click()
        self.page.locator('#planner-excluded-section summary').click()
        excluded=self.page.locator('#planner-excluded .planner-card').filter(has_text='Черновик для плана')
        excluded.get_by_role('button',name='Вернуть в план',exact=True).click()
        queue.locator('.planner-card-main').click()
        self.assertIn('Редакторских вставок: 1',self.page.locator('#planner-entry-readiness').inner_text())
        self.page.fill('#planner-entry-progress','65');self.page.click('#planner-entry-save')
        self.page.wait_for_function('!document.querySelector("#planner-entry-dialog").open')
        self.assertIn('65%',queue.inner_text())
        self.page.click('#planner-distribute');self.page.fill('#planner-start','2026-09-21')
        self.page.click('#planner-preview');self.page.locator('.planner-proposed-row').first.wait_for()
        self.assertEqual(self.page.locator('.planner-proposed-row').count(),2)
        self.assertIn('Черновик для плана',self.page.locator('.planner-proposed-row').first.inner_text())
        self.assertTrue(all(e['date'] is None for e in self.server.CALENDAR.read()['entries']))
        self.page.click('#planner-apply')
        day=self.page.locator('.planner-day[data-date="2026-09-21"] .planner-card')
        day.wait_for()
        day.drag_to(self.page.locator('.planner-day[data-date="2026-09-25"]'))
        moved=self.page.locator('.planner-day[data-date="2026-09-25"] .planner-card')
        moved.wait_for()
        self.assertTrue(any(e['date']=='2026-09-25' and e['title']=='Черновик для плана' for e in self.server.CALENDAR.read()['entries']))
        moved.get_by_role('button',name='Без даты',exact=True).click();queue.wait_for()
        queue.locator('.planner-card-main').click();self.page.click('#planner-entry-open')
        self.page.wait_for_function('granka.state.file === "calendar-import-fixture/raw.granka.json" && !granka.state.busy')
        self.assertEqual(raw.read_text(encoding='utf-8'),content)
        self.assertEqual(json.loads(raw.with_suffix('.granka.json').read_text(encoding='utf-8'))['versions'][0]['text'],content)

    def test_library_shares_drafts_with_calendar_and_filters_support_files(self):
        from tools.publication_plan import PublicationPlan
        folder=self.directory/'library-fixture'/'drafts';folder.mkdir(parents=True,exist_ok=True)
        text='# Понятное название статьи\n\nЧерновик.'
        for name,content in [('raw.md',text),('same.md',text),('README.md','# Инструкции'),
                             ('published.md','---\nstatus: publish\ntitle: Выпущенная статья\n---\nТекст.')]:
            (folder/name).write_text(content,encoding='utf-8')
        assets=folder/'raw.assets';assets.mkdir();(assets/'prompt.md').write_text('# Промпт',encoding='utf-8')
        settings=self.server.environment.load();settings['planning']={'source_dirs':{'articles':['library-fixture/drafts']}}
        self.server.CALENDAR=PublicationPlan(lambda:self.directory/'library-catalog-state',lambda:[s for s in self.server.library_sources() if s['state']=='draft'])
        with patch.object(self.server.environment,'load',return_value=settings):
            self.page.click('#opensrc');self.page.locator('#source-list .source-item').first.wait_for()
            self.assertEqual(self.page.locator('#source-list .source-item').count(),1)
            self.assertIn('Понятное название статьи',self.page.locator('#source-list').inner_text())
            self.assertNotIn('Промпт',self.page.locator('#source-list').inner_text())
            self.assertEqual(self.page.locator('#source-list details').count(),1)
            self.page.get_by_role('button',name='В календарь',exact=True).click()
            self.page.locator('#planner-queue .planner-card').wait_for()
            self.assertEqual(self.page.locator('#planner-queue .planner-card').count(),1)
            self.page.locator('#planner-queue').get_by_role('button',name='Исключить',exact=True).click()
            self.page.click('#planner-library');self.page.locator('#source-list .source-item').first.wait_for()
            self.assertIn('Исключено из плана',self.page.locator('#source-list').inner_text())
            self.page.get_by_role('button',name='Вернуть в план',exact=True).click()
            self.page.locator('#planner-queue .planner-card').wait_for()
            self.page.click('#planner-library');self.page.click('[data-source-filter=published]')
            self.assertEqual(self.page.locator('#source-list .source-item').count(),1)
            self.assertIn('Выпущенная статья',self.page.locator('#source-list').inner_text())
            self.page.click('[data-source-filter=all]')
            self.assertEqual(self.page.locator('#source-list .source-item').count(),2)
            self.page.screenshot(path=str(ROOT/'_audit/library-clean-fixture.png'))
        self.assertEqual((folder/'raw.md').read_text(encoding='utf-8'),text)

    def test_published_library_links_dates_and_external_card_do_not_create_documents(self):
        from tools.publication_plan import PublicationPlan
        folder=self.directory/'published-links-fixture';folder.mkdir()
        source=folder/'released.md';source.write_text('# Вышедшая статья\n\nАвторский текст.',encoding='utf-8')
        original=source.read_bytes()
        settings=self.server.environment.load()
        settings['planning']={'source_dirs':{'articles':['published-links-fixture']},'publications':[
            {'root':'articles','file':'published-links-fixture/released.granka.json',
             'url':'https://example.invalid/released/','date':'2026-10-16'}]}
        self.server.CALENDAR=PublicationPlan(lambda:self.directory/'published-links-calendar',lambda:self.server.library_sources())
        with patch.object(self.server.environment,'load',return_value=settings):
            self.server.CALENDAR.change({'action':'save','expected_revision':self.server.CALENDAR.read()['revision'],
                'entry':{'title':'Старая публикация','status':'published','date':'2019-07-28',
                         'url':'https://example.invalid/historical/'}})
            requests=[];self.page.on('request',lambda req:requests.append((req.method,req.url)))
            self.context.route('https://example.invalid/**',lambda route:route.fulfill(body='Готовая публикация'))
            self.page.click('#opensrc');self.page.click('[data-source-filter=published]')
            local=self.page.locator('.source-item').filter(has_text='Вышедшая статья')
            expect(local).to_be_visible()
            self.assertIn('Опубликовано · 2026-10-16',local.inner_text())
            expect(local.locator('.source-publication')).to_have_attribute('href','https://example.invalid/released/')
            self.assertEqual(local.locator('button.source-open').count(),1)
            external=self.page.locator('.source-item').filter(has_text='Старая публикация')
            expect(external.locator('a.source-open')).to_have_attribute('href','https://example.invalid/historical/')
            self.assertIn('Опубликовано · 2019-07-28',external.inner_text())
            self.assertEqual(external.locator('button').count(),0)
            with self.page.expect_popup() as opened:
                external.locator('a.source-open').click()
            popup=opened.value;popup.wait_for_load_state()
            self.assertEqual(popup.url,'https://example.invalid/historical/');popup.close()
            self.assertEqual(self.page.evaluate('granka.state.file'),'fixture.granka.json')
            self.page.set_viewport_size({'width':500,'height':900})
            self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            self.assertFalse(any(method=='POST' for method,url in requests),requests)
        self.assertEqual(source.read_bytes(),original)
        self.assertFalse(source.with_suffix('.granka.json').exists())
        self.assertEqual(self.errors,[])

    def test_exact_source_link_opens_selected_markdown_and_never_falls_back_to_namesake(self):
        from tools.publication_plan import PublicationPlan
        from urllib.parse import urlencode
        folder=self.directory/'deep-link-fixture';folder.mkdir()
        first=folder/'one.md';first.write_text('# Одинаковый заголовок\n\nПервый текст.',encoding='utf-8')
        second=folder/'два & три.md';second.write_text('# Одинаковый заголовок\n\nВыбранный текст.',encoding='utf-8')
        original=second.read_bytes()
        settings=self.server.environment.load();settings['planning']={'source_dirs':{'articles':['deep-link-fixture']}}
        self.server.CALENDAR=PublicationPlan(lambda:self.directory/'deep-link-calendar',lambda:self.server.library_sources())
        with patch.object(self.server.environment,'load',return_value=settings):
            self.page.goto(self.base+'/?'+urlencode({'root':'articles','source_file':'deep-link-fixture/два & три.md'}))
            self.page.wait_for_function('granka.state.file === "deep-link-fixture/два & три.granka.json" && !granka.state.busy')
            self.assertIn('Выбранный текст.',self.page.locator('#doc').inner_text())
            self.assertFalse(first.with_suffix('.granka.json').exists())
            self.assertEqual(second.read_bytes(),original)
            posts=[];self.page.on('request',lambda req:posts.append(req.url) if req.method=='POST' else None)
            for root,relative in [('articles','deep-link-fixture/missing.md'),('wrong','deep-link-fixture/one.md'),('articles','../one.md')]:
                self.page.goto(self.base+'/?'+urlencode({'root':root,'source_file':relative}))
                self.page.wait_for_function('document.querySelector("#doc").textContent.includes("по этой ссылке не найдена")')
                self.assertIsNone(self.page.evaluate('granka.state.document'))
            self.assertEqual(posts,[])
        self.assertEqual(self.errors,[])

    def test_note_in_formatted_text_round_trips_to_source(self):
        self.assertEqual(self.page.locator('#doc h1').inner_text(),'Заголовок 😀')
        self.assertEqual(self.page.locator('#doc ul li').count(),2)
        self.page.evaluate("""() => {
          const range=document.createRange();range.selectNodeContents(document.querySelector('#doc strong'));
          const sel=getSelection();sel.removeAllRanges();sel.addRange(range);
          document.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
        }""")
        self.page.click('#addann')
        self.page.wait_for_function('granka.state.document.annotations.length===1 && !granka.state.busy')
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        ann=saved['annotations'][0]
        self.assertEqual(ann['quote'],'жирного')
        self.assertEqual(self.source[ann['start']:ann['end']],ann['quote'])
        self.assertEqual(saved['versions'][0]['text'],self.source)
        self.assertEqual(self.server.contract_errors(saved),[])
        self.assertEqual(self.page.locator('#doc strong mark').inner_text(),'жирного')
        self.page.click('[data-view=source]')
        self.assertEqual(self.page.locator('#texteditor').input_value(),self.source)
        self.page.click('[data-view=article]')
        self.assertEqual(self.page.locator('#doc h1').count(),1)
        self.page.set_viewport_size({'width':500,'height':900})
        self.assertFalse(self.page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        self.assertTrue(self.page.evaluate("[...document.querySelectorAll('header button')].every(b=>b.scrollWidth<=b.clientWidth+1)"))
        self.page.screenshot(path=str(ROOT/'_audit/markdown-mobile.png'),full_page=True)

    def test_marker_only_annotation_opens_source(self):
        self.data['annotations']=[{'id':'syntax','type':'candidate','version':'v1','start':0,'end':2,
          'quote':'# ','class':'heading','severity':'P2','variants':[],'decision':None}]
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.locator('#card-syntax .review-quote').click()
        self.assertEqual(self.page.evaluate('granka.state.view'),'source')
        self.assertEqual(self.page.evaluate('document.querySelector("#texteditor").value.slice(document.querySelector("#texteditor").selectionStart,document.querySelector("#texteditor").selectionEnd)'), '# ')

    def test_manual_edit_keeps_markdown_and_renders_new_version(self):
        edited='## Готово\n\n**😀 Ручная правка.**\n'
        self.page.click('[data-view=source]')
        self.page.locator('#texteditor').fill(edited)
        self.page.locator('#texteditor').press('Control+Enter')
        self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['versions'][0]['text'],self.source)
        self.assertEqual(data['versions'][1]['text'],edited)
        self.assertEqual(self.page.locator('#doc h2').inner_text(),'Готово')
        self.assertEqual(self.page.locator('#doc strong').inner_text(),'😀 Ручная правка.')
        self.page.click('[data-view=diff]')
        self.assertTrue(self.page.locator('#diffR').is_visible())
        self.assertIn('**',self.page.locator('#diffR').text_content())

    def select_visible(self,selector,collapse=None):
        self.page.evaluate("""({selector,collapse})=>{
          const range=document.createRange();range.selectNodeContents(document.querySelector(selector));
          if(collapse!==null)range.collapse(collapse);
          document.querySelector('#doc').focus();const sel=getSelection();sel.removeAllRanges();sel.addRange(range);
        }""",{'selector':selector,'collapse':collapse})

    def test_type_directly_in_article_and_save(self):
        self.assertEqual(self.page.locator('#doc').get_attribute('contenteditable'),'true')
        self.select_visible('#doc strong')
        self.page.keyboard.type('своего')
        self.assertTrue(self.page.evaluate('granka.state.editor?.mode==="rich"'))
        self.assertFalse(self.page.locator('#texteditor').is_visible())
        self.assertEqual(self.page.locator('#doc strong').inner_text(),'своего')
        self.page.keyboard.press('Control+s')
        self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['versions'][0]['text'],self.source)
        self.assertEqual(data['versions'][1]['text'],self.source.replace('жирного','своего'))
        self.assertEqual(self.page.locator('#doc strong').inner_text(),'своего')

    def test_inline_typing_does_not_trigger_judge_shortcuts_and_undo(self):
        self.select_visible('#doc p',False)
        self.page.keyboard.type(' jokcau123')
        self.assertIn('jokcau123',self.page.locator('#doc p').first.inner_text())
        self.page.keyboard.press('Control+z')
        self.assertNotIn('jokcau123',self.page.locator('#doc p').first.inner_text())
        # Entering the editor may give the initial space its own native undo step.
        for _ in range(2):
            if self.page.evaluate('granka.editor.text()')==self.source:break
            self.page.keyboard.press('Control+z')
        self.assertEqual(self.page.evaluate('granka.editor.text()'),self.source)

    def test_enter_creates_paragraph_without_changing_other_blocks(self):
        self.select_visible('#doc p',False)
        self.page.keyboard.press('Enter');self.page.keyboard.type('Новый абзац.')
        self.page.keyboard.press('Control+s')
        self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        new=data['versions'][-1]['text']
        self.assertIn('Новый абзац.',new)
        self.assertIn('# Заголовок 😀',new)
        self.assertIn('## Раздел\n\n- пункт один\n- пункт два\n\n> Цитата автора.',new)
        self.assertGreaterEqual(self.page.locator('#doc > p').count(),2)

    def test_context_menu_image_preview_and_actual_insertion(self):
        image=self.directory/'bright.png'
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII=')
        image.write_bytes(png)
        self.select_visible('#doc strong')
        self.page.locator('#doc strong').click(button='right')
        self.assertTrue(self.page.locator('#contextimage').is_visible())
        self.page.click('#contextimage')
        self.page.locator('.image-tile').filter(has_text='bright.png').click()
        self.page.wait_for_function('document.querySelector("#imagepreview").naturalWidth===1')
        self.page.locator('#imagealt').fill('Иллюстрация рядом с текстом')
        self.page.screenshot(path=str(ROOT/'_audit/image-gallery.png'),full_page=True)
        self.page.click('#imageinsert')
        self.page.wait_for_function('granka.state.editor?.mode==="rich" && document.querySelector("#doc img")?.naturalWidth===1 && !granka.state.busy')
        self.assertFalse(self.page.locator('#imagegallery').is_visible())
        self.assertIn('жирного',self.page.locator('#doc').inner_text())
        self.page.screenshot(path=str(ROOT/'_audit/rich-with-image.png'),full_page=True)
        self.page.click('#savebtn')
        self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertIn('![Иллюстрация рядом с текстом](fixture.assets/',data['versions'][-1]['text'])
        self.assertNotIn('/api/article-image',data['versions'][-1]['text'])
        self.assertEqual(data['versions'][0]['text'],self.source)
        self.assertEqual(next((self.directory/'fixture.assets').glob('*.png')).read_bytes(),png)
        self.page.wait_for_function('document.querySelector("#doc img")?.naturalWidth===1')

    def test_image_note_is_fulfilled_in_new_version(self):
        (self.directory/'note-image.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII='))
        start=self.source.index('До ')
        self.data['annotations']=[{'id':'u-image','type':'note','kind':'image','version':'v1','start':start,'end':start+2,'quote':'До','variants':[],'decision':None}]
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.locator('#card-u-image').get_by_text('Выбрать или сгенерировать картинку',exact=True).click()
        self.page.locator('.image-tile').filter(has_text='note-image.png').click()
        self.page.wait_for_function('document.querySelector("#imagepreview").naturalWidth===1')
        self.page.click('#imageinsert')
        self.page.wait_for_function('granka.state.editor?.mode==="rich" && !granka.state.busy && document.querySelector("#doc img")')
        self.page.click('#savebtn');self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['annotations'][0],self.data['annotations'][0])
        self.assertEqual([a for a in data['annotations'] if a['version']=='v2'],[])
        self.assertEqual(data['versions'][-1]['fulfilled_image_notes'][0]['id'],'u-image')

    def test_rich_conflict_preserves_unsaved_text(self):
        self.select_visible('#doc strong');self.page.keyboard.type('мой черновик')
        data=json.loads(self.file.read_text(encoding='utf-8'));data['status']='draft'
        self.file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.page.keyboard.press('Control+s')
        self.page.wait_for_function('!granka.state.busy && document.querySelector("#editor-status").textContent.includes("изменил")')
        self.assertIn('мой черновик',self.page.locator('#doc').inner_text())
        self.assertTrue(self.page.evaluate('!!granka.state.editor'))
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']),1)

    def test_rich_draft_can_be_restored_after_reload(self):
        self.select_visible('#doc strong');self.page.keyboard.type('Мой незаконченный текст')
        self.page.on('dialog',lambda dialog:dialog.accept())
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.click('#restore-draft')
        self.assertIn('Мой незаконченный текст',self.page.locator('#doc').inner_text())
        self.page.click('#savebtn');self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        self.assertIn('Мой незаконченный текст',json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text'])

    def test_rich_edit_preserves_metadata_code_and_definitions(self):
        source='---\ntitle: hidden\n---\n\n# Заголовок\n\nОбычная фраза с [ссылкой][ref].\n\n~~~bsl\nСообщить(1);\n~~~\n\n[ref]: https://example.org/ "Адрес"\n'
        self.data['versions'][0]['text']=source
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.select_visible('#doc p',True);self.page.keyboard.type('Вставка. ')
        self.page.keyboard.press('Control+s');self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        text=json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text']
        self.assertTrue(text.startswith('---\ntitle: hidden\n---\n\n# Заголовок\n\n'))
        self.assertIn('~~~bsl\nСообщить(1);\n~~~',text)
        self.assertTrue(text.endswith('[ref]: https://example.org/ "Адрес"\n'))

    def test_image_prepare_conflict_and_boundary_checks(self):
        image=self.directory/'prepare.png'
        image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII='))
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path,revision,expected in [(str(image),'stale',409),(str(ROOT/'README.md'),self.server.revision_of(self.file),403)]:
            req=urllib.request.Request(self.base+'/api/prepare-image?root=articles&f=fixture.granka.json',
              data=json.dumps({'path':path,'expected_revision':revision}).encode(),
              headers={'X-Granka-Token':self.server.TOKEN,'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(req)
            self.assertEqual(caught.exception.code,expected);caught.exception.close()

    def test_apply_button_completes_workflow_and_shows_diff(self):
        start=self.source.index('жирного')
        self.data['annotations']=[{'id':'approved','type':'candidate','version':'v1','start':start,'end':start+7,'quote':'жирного','decision':{'action':'accept','text':'своего'},'class':'Стиль','severity':'P2','variants':['своего']}]
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('granka?.state.document && !granka.state.busy')
        self.page.click('#readybtn');self.page.wait_for_function('granka.state.document.versions.length===2',timeout=10000)
        self.assertIn('своего',self.page.locator('#doc').inner_text());self.assertIn('Новая версия готова',self.page.locator('#stage-label').inner_text())
        self.page.click('[data-view=diff]');self.assertIn('своего',self.page.locator('#diffR').inner_text())

    def test_analysis_button_uses_selected_provider_and_adds_findings(self):
        response={'summary':'Найдено одно замечание.','findings':[{'quote':'Цитата автора.','occurrence':0,'class':'Стиль','severity':'P2','why':'Проверка','variants':['Авторская цитата.']}]}
        with patch.object(self.server.ai_provider,'text_result',return_value=(response,{'provider':'codex'})) as provider:
            self.page.click('#analysisbtn');self.page.wait_for_function('granka.state.document.analyses?.length===1',timeout=10000)
            self.assertEqual(provider.call_count,1)
        self.assertIn('Найдено одно замечание',self.page.locator('#review-result').inner_text())
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['annotations']),1)

    def test_deep_review_links_multiple_passages_saves_decision_and_preserves_markdown(self):
        from test_deep_analysis import responses,anchor
        replies=responses()
        replies[0]['outline'][0].update(quote='## Раздел',role='Список возможностей',point='Два пункта без развития.')
        for finding in [replies[1]['findings'][0],replies[2]['verdicts'][0]['finding']]:
            finding['anchors']=[anchor('пункт один'),anchor('пункт два')]
        start=self.source.index('пункт один')
        self.data['annotations']=[{'id':'existing','version':'v1','type':'candidate','start':start,'end':start+len('пункт один'),
                                  'quote':'пункт один','why':'Обычная карточка','variants':['первый пункт'],'severity':'P2'}]
        self.data['analyses']=[{'version':'v1','summary':'Предыдущая обычная проверка','added':['existing']}]
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('!granka.state.busy && granka.state.document.annotations.length===1')
        with patch.object(self.server.ai_provider,'text_result',side_effect=[(r,{'provider':'codex'}) for r in replies]) as provider:
            self.page.click('#deep-analysis-btn')
            self.page.wait_for_function('granka.state.document.deep_analyses?.length===1',timeout=15000)
            self.assertEqual(provider.call_count,3)
        self.assertEqual(self.page.locator('#deep-review .deep-card').count(),1)
        self.assertIsNone(self.page.evaluate('granka.state.active'))
        self.assertEqual(self.page.evaluate('granka.state.deepFocus.findingId'),self.page.evaluate('granka.state.document.deep_analyses[0].findings[0].id'))
        quotes=self.page.locator('.deep-card .deep-quote')
        quotes.nth(0).click()
        expect(self.page.locator('#doc mark[data-id=existing].deep-mark')).to_have_count(1)
        quotes.nth(1).click()
        self.assertIn('пункт два',self.page.locator('#doc mark[data-deep-indexes="1"]').inner_text())
        self.assertEqual(self.page.evaluate('granka.editor.text()'),self.source)
        self.page.locator('.deep-card textarea').fill('Здесь мне нужны оба этапа.')
        self.page.locator('.deep-card').get_by_role('button',name='Не согласен',exact=True).click()
        self.page.wait_for_function('granka.state.document.deep_analyses[0].findings[0].decision?.action === "dismiss" && !granka.state.busy')
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['versions'],self.data['versions']);self.assertEqual(saved['annotations'],self.data['annotations'])
        self.assertEqual(saved['deep_analyses'][0]['findings'][0]['decision']['note'],'Здесь мне нужны оба этапа.')
        self.page.reload();self.page.wait_for_function('!granka.state.busy && granka.state.document.deep_analyses?.length===1')
        self.assertIn('Здесь мне нужны оба этапа.',self.page.locator('#deep-review .decision-text').inner_text())
        self.page.locator('#deep-review .deep-map summary').click()
        self.page.locator('.deep-map-block button').click()
        expect(self.page.locator('#doc h2 mark.deep-mark')).to_have_count(1)
        if os.environ.get('GRANKA_DEEP_SCREENSHOT'): self.page.screenshot(path=os.environ['GRANKA_DEEP_SCREENSHOT'])
        self.page.locator('#doc blockquote p').click()
        self.page.keyboard.press('End');self.page.keyboard.insert_text(' Дополнение.')
        self.page.click('#savebtn');self.page.wait_for_function('granka.state.version === "v2" && !granka.state.busy')
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text'],self.source.replace('Цитата автора.','Цитата автора. Дополнение.'))
        self.assertEqual(self.page.locator('#doc mark.deep-mark').count(),0)
        self.assertIn('Для v2 запусти новый',self.page.locator('#deep-review').inner_text())
        self.page.select_option('#versel','v1')
        self.page.wait_for_function('granka.state.version === "v1"')
        self.assertTrue(self.page.locator('#deep-review .deep-actions button').is_disabled())

    def test_deep_decision_http_conflict_never_overwrites_other_changes(self):
        from test_deep_analysis import document,run_fixture
        data=document();report,_=run_fixture(self.directory/'deep-api-fixture')
        self.server.deep_analysis.add_report(data,'v1',report)
        self.file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('!granka.state.busy && granka.state.document.deep_analyses?.length===1')
        external=json.loads(self.file.read_text(encoding='utf-8'));external['external']='new'
        self.file.write_text(json.dumps(external),encoding='utf-8')
        self.page.locator('.deep-card').get_by_role('button',name='В работу',exact=True).click()
        self.page.wait_for_function('!granka.state.busy && document.querySelector("#toast").textContent.includes("другой вкладке")')
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertIsNone(saved['deep_analyses'][0]['findings'][0]['decision']);self.assertEqual(saved['external'],'new')

    def test_invalid_deep_report_is_reported_without_breaking_editor(self):
        from test_deep_analysis import document,run_fixture
        data=document();report,_=run_fixture(self.directory/'deep-invalid-fixture')
        self.server.deep_analysis.add_report(data,'v1',report)
        report['findings'][0]['anchors'][0]['start']+=1
        self.file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('!granka.state.busy && granka.state.contractErrors?.length')
        self.assertIn('ошибки разметки',self.page.locator('#deep-review').inner_text())
        self.assertIn('Скобочки оставлю)))',self.page.locator('#doc').inner_text())

    def test_local_clues_can_be_read_and_navigated_after_empty_diagnosis(self):
        from test_deep_analysis import responses,run_fixture
        source='Можно выбрать нужный вариант в карточке, без новой настройки.\n\nМожно выбрать другой файл в библиотеке, но не отправлять его.\n\nМожно выбрать удобную дату, публикация сама не начнётся.\n'
        data={'doc':'fixture.md','versions':[{'id':'v1','text':source}],'annotations':[]}
        replies=responses()[:2];replies[0]['outline'][0]['quote']='Можно выбрать';replies[1]['findings']=[]
        report,_=run_fixture(self.directory/'deep-clues-fixture',data,replies)
        self.server.deep_analysis.add_report(data,'v1',report)
        self.file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('!granka.state.busy && granka.state.document.deep_analyses?.length===1')
        self.page.locator('.deep-signals summary').click()
        self.page.locator('.deep-signals button').nth(1).click()
        self.assertIsNotNone(self.page.evaluate('granka.state.deepFocus.signalIndex'))
        self.assertGreater(self.page.locator('#doc mark.deep-mark').count(),1)
        self.assertEqual(self.page.evaluate('granka.editor.text()'),source)
        self.assertIn('После перепроверки замечаний нет',self.page.locator('#deep-review').inner_text())

    def test_generated_image_is_previewed_and_insertable(self):
        generated=self.directory/'provider-result.png';generated.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII='))
        self.select_visible('#doc strong');self.page.locator('#doc strong').click(button='right');self.page.click('#contextimage')
        self.page.click('[data-image-tab=generate]');self.page.locator('#generation-prompt').fill('Редакторское перо и бумага')
        with patch.object(self.server.ai_provider,'generate_image',return_value=(generated,{'provider':'codex'})) as provider:
            self.page.click('#generate-image');self.page.wait_for_function('document.querySelector("#generation-status").textContent.includes("Готово")',timeout=10000)
            self.page.wait_for_function('document.querySelector("#imagepreview").naturalWidth===1');self.assertEqual(provider.call_count,1)
        self.assertFalse(self.page.locator('#imageinsert').is_disabled());self.page.click('#imageinsert')
        self.page.wait_for_function('granka.state.editor && !granka.state.busy');self.page.click('#savebtn');self.page.wait_for_function('granka.state.document.versions.length===2')
        self.assertIn('fixture.assets/',json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text'])

    def test_meter_highlights_coexist_with_review_and_protected_spans(self):
        source = '# Пример 😀\n\nВажно отметить результат.\n\nВ современном мире нужен редактор.\n'
        self.data['versions'][0]['text'] = source
        self.data['annotations'] = [
            {'id': 'protected', 'type': 'protected', 'version': 'v1', 'start': source.index('Важно'),
             'end': source.index('результат.') + len('результат.'), 'quote': 'Важно отметить результат.'},
            {'id': 'candidate', 'type': 'candidate', 'version': 'v1', 'start': source.index('В современном'),
             'end': source.index('редактор.') + len('редактор.'), 'quote': 'В современном мире нужен редактор.',
             'severity': 'P1', 'class': 'Повтор', 'why': 'Проверить вводную', 'variants': ['Нужен редактор.'],
             'decision': {'action': 'reject', 'reason': 'worse'}},
        ]
        self.file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        self.page.reload(); self.page.wait_for_function('granka.state.meter?.axes["маркеры"].hits === 2')
        self.page.click('[data-pane=meter]'); self.page.click('#meterhl')
        expect(self.page.locator('#doc mark.meter')).to_have_count(2)
        expect(self.page.locator('#doc mark.protected.meter')).to_have_count(1)
        expect(self.page.locator('#doc mark.P1.decided.meter')).to_have_count(1)
        self.assertIn('филлер значимости', self.page.locator('#doc mark.protected.meter').get_attribute('title'))
        self.page.locator('#doc mark.P1.meter').click()
        self.assertEqual(self.page.evaluate('granka.state.active'), 'candidate')
        self.page.click('#meterhl'); expect(self.page.locator('#doc mark.meter')).to_have_count(0)
        self.assertEqual(self.page.evaluate('GrankaMarkdown.render(document.createElement("div"), granka.state.document.versions[0].text, [], {mark: () => document.createElement("mark")}).serialize()'), source)

    def test_meter_button_resets_and_empty_hits_are_explained(self):
        self.data['versions'][0]['text'] = '# Пример\n\nВажно отметить результат.'
        self.data['versions'].append({'id': 'v2', 'text': '# Другая версия\n\nЯ открыл файл.'})
        self.file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        self.page.reload(); self.page.wait_for_function('granka.state.version === "v2" && granka.state.meter')
        self.page.select_option('#versel', 'v1')
        self.page.wait_for_function('granka.state.version === "v1" && granka.state.meter?.axes["маркеры"].hits === 1')
        self.page.click('[data-pane=meter]'); self.page.click('#meterhl')
        self.page.select_option('#versel', 'v2')
        self.page.wait_for_function('granka.state.version === "v2" && granka.state.meter?.axes["маркеры"].hits === 0')
        self.assertIn('Подсветить', self.page.locator('#meterhl').inner_text())
        self.assertEqual(self.page.locator('#meterhl').get_attribute('aria-pressed'), 'false')
        self.page.click('#meterhl')
        self.assertIn('Словарных оборотов не найдено', self.page.locator('#meterflags').inner_text())
        self.assertEqual(self.page.locator('#doc mark.meter').count(), 0)

    def test_uncalibrated_meter_keeps_local_findings_visible(self):
        self.data['versions'][0]['text'] = '# Пример\n\nВажно отметить результат.'
        self.file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        with patch.object(self.server.iimeter, 'CORPUS_DIR', self.directory/'missing-corpus'):
            self.page.reload(); self.page.wait_for_function('granka.state.meter?.status === "uncalibrated"')
            self.page.click('[data-pane=meter]')
            self.assertEqual(self.page.locator('#axis-markers-number').inner_text(), '100')
            self.assertEqual(self.page.locator('#axis-voice-number').inner_text(), '—')
            self.page.click('#meterhl')
            expect(self.page.locator('#doc mark.meter')).to_have_count(1)
            self.assertIn('1', self.page.locator('#meterhl').inner_text())

    def test_meter_highlights_follow_markdown_and_preserve_source_on_edit(self):
        source = '# Пример 😀\n\n[Важно **отметить**](https://example.invalid/) результат.\n\n`В современном мире` — пример в коде.\n'
        self.data['versions'][0]['text'] = source
        self.file.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')
        self.page.reload(); self.page.wait_for_function('granka.state.meter?.axes["маркеры"].hits === 1')
        self.page.click('[data-pane=meter]'); self.page.click('#meterhl')
        self.assertEqual(''.join(self.page.locator('#doc mark.meter').all_text_contents()), 'Важно отметить')
        self.assertEqual(self.page.locator('#doc code mark').count(), 0)
        self.page.locator('#doc mark.meter').first.evaluate('(mark) => { mark.firstChild.data = "Стоит "; mark.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText"})); }')
        self.page.wait_for_function('granka.state.editor')
        self.assertEqual(self.page.locator('#meterhl').get_attribute('aria-pressed'), 'false')
        self.page.click('#savebtn'); self.page.wait_for_function('granka.state.version === "v2" && !granka.state.busy')
        saved = json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text']
        self.assertEqual(saved, source.replace('Важно ', 'Стоит ', 1))

    def test_settings_and_meter_are_visible(self):
        self.page.wait_for_function('granka.state.meter');self.assertTrue(self.page.locator('#meterwidget').is_visible())
        self.page.click('#settingsbtn');self.assertEqual(self.page.locator('#image-provider').input_value(),'codex')
        self.page.select_option('#image-provider','openrouter');self.page.click('#settingssave')
        self.page.wait_for_function('!document.querySelector("#settingsdialog").open && granka.state.settings.image_provider === "openrouter"')
        self.assertEqual(self.server.ai_provider.settings()['image_provider'],'openrouter')

    def test_cover_generation_is_separate_and_survives_editing(self):
        generated=self.directory/'cover-result.png'
        generated.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII='))
        self.page.click('#cover-btn')
        self.assertEqual(self.page.locator('#image-title').inner_text(),'Обложка статьи')
        self.assertTrue(self.page.locator('#image-generation').is_visible())
        self.page.locator('#generation-prompt').fill('Бумажный театр для обложки')
        with patch.object(self.server.ai_provider,'generate_image',return_value=(generated,{'provider':'codex'})) as provider:
            self.page.click('#generate-image')
            self.page.wait_for_function('document.querySelector("#generation-status").textContent.includes("Обложка готова")',timeout=10000)
            self.assertIn('Бумажный театр',provider.call_args.args[0])
            self.assertIn('Заголовок 😀',provider.call_args.args[0])
            self.assertEqual(provider.call_args.args[2]['image_aspect'],'16:9')
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']),1)
        self.assertEqual(self.page.locator('#imageinsert').inner_text(),'Сделать обложкой')
        self.page.locator('#imagealt').fill('Обложка про текст')
        self.page.click('#imageinsert')
        self.page.wait_for_function('granka.state.version==="v2" && !granka.state.busy')
        self.page.wait_for_function('document.querySelector("#cover-preview").naturalWidth===1')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['versions'][1]['text'],self.source)
        self.assertEqual(data['versions'][1]['cover']['alt'],'Обложка про текст')
        self.assertTrue(self.page.locator('#article-cover').is_visible())
        self.assertEqual(self.page.locator('#doc img').count(),0)
        self.assertEqual(self.page.locator('#cover-download').get_attribute('download').split('.')[-1],'png')
        self.page.click('[data-view=diff]')
        self.assertEqual(self.page.locator('#diffL .diff-cover-label').inner_text(),'Без обложки')
        self.assertEqual(self.page.locator('#diffR .diff-cover').count(),1)
        self.page.click('[data-view=article]')
        self.select_visible('#doc strong');self.page.keyboard.type('правки')
        self.page.keyboard.press('Control+s');self.page.wait_for_function('granka.state.version==="v3" && !granka.state.busy')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(data['versions'][2]['cover'],data['versions'][1]['cover'])
        self.page.click('#cover-remove');self.page.wait_for_function('granka.state.version==="v4" && !granka.state.busy')
        self.assertFalse(self.page.locator('#article-cover').is_visible())
        data=json.loads(self.file.read_text(encoding='utf-8'));self.assertNotIn('cover',data['versions'][-1])
        self.assertIn('cover',data['versions'][-2])

    def test_brand_assets_and_release_version_are_served(self):
        self.page.wait_for_function('document.querySelector(".brand-word").naturalWidth>0')
        self.assertEqual(self.page.locator('.app-version').inner_text(),(ROOT/'VERSION').read_text().strip())
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path,mime in [('/favicon.ico','image/x-icon'),('/static/brand/mark.svg','image/svg+xml'),('/static/brand/favicon-32.png','image/png')]:
            with opener.open(self.base+path) as response:
                self.assertEqual(response.status,200);self.assertEqual(response.headers['Content-Type'],mime)

    def test_announcement_generation_draft_and_explicit_publication(self):
        from test_telegram import TOKEN,response
        self.page.click('#announcement-btn')
        self.assertTrue(self.page.locator('#telegramdialog').is_visible())
        with patch.object(self.server.ai_provider,'text_result',return_value=({'variants':[{'label':'Коротко','text':'Написал о том, как проверять результат.'}]},{'provider':'codex'})):
            self.page.click('#tg-generate');self.page.locator('#tg-variants').get_by_role('button',name='Коротко').click(timeout=10000)
        self.page.locator('#tg-url').fill('https://example.org/article')
        self.page.click('#tg-save')
        self.page.wait_for_function('granka.state.document.announcement && !granka.state.busy')
        data=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(len(data['versions']),1);self.assertEqual(data['versions'][0]['text'],self.source)
        self.page.click('[data-tg-tab=bot]');self.page.locator('#tg-chat-id').fill('@granka_test');self.page.locator('#tg-token').fill(TOKEN)
        self.page.click('#tg-settings-save');self.page.wait_for_function('document.querySelector("#tg-bot-status").textContent.includes("сохранены")')
        self.assertEqual(self.page.locator('#tg-token').input_value(),'')
        self.page.click('[data-tg-tab=compose]')
        with patch('tools.telegram.bot_call',side_effect=response) as call:
            self.page.click('#tg-prepare');self.page.wait_for_function('!document.querySelector("#tg-publish").hidden')
            self.assertEqual(call.call_count,3)
            self.assertEqual(self.page.locator('#tg-preview').inner_text(),'Написал о том, как проверять результат.\n\nhttps://example.org/article')
            self.assertIn('Тестовый канал',self.page.locator('#tg-channel').inner_text())
            self.page.screenshot(path=str(ROOT/'_audit/telegram-preview.png'),full_page=True)
            self.page.click('#tg-publish');self.page.locator('#tg-status a').wait_for()
            self.assertEqual(sum(c.args[1]=='sendMessage' for c in call.call_args_list),1)
        self.assertTrue(self.page.locator('#tg-publish').is_disabled())
        self.assertEqual(self.page.locator('#tg-status a').get_attribute('href'),'https://t.me/granka_test/42')

    def test_unsaved_announcement_survives_reopening(self):
        self.page.click('#announcement-btn');self.page.locator('#tg-text').fill('Мой незавершённый анонс')
        self.page.click('#telegram-close');self.page.click('#announcement-btn')
        self.assertEqual(self.page.locator('#tg-text').input_value(),'Мой незавершённый анонс')

    def test_batch_preview_save_and_undo(self):
        from test_batch_decisions import fixture
        self.data=fixture();self.data['doc']='fixture.md'
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.locator('#batch-groups').get_by_role('button',name='Принять варианты').click()
        self.assertEqual(self.page.locator('#batch-items .batch-after').all_text_contents(),['Первый.','Второй.'])
        self.page.screenshot(path=str(ROOT/'_audit/batch-preview.png'),full_page=True)
        self.page.click('#batch-save');self.page.wait_for_function('granka.state.document.annotations.every(a=>a.decision) && !granka.state.busy')
        self.assertEqual(len(json.loads(self.file.read_text(encoding='utf-8'))['versions']),1)
        self.page.locator('#batch-groups').get_by_role('button',name='Отменить последний пакет · 2').click()
        self.page.wait_for_function('granka.state.document.annotations.every(a=>!a.decision) && !granka.state.busy')

    def test_personal_rules_and_benchmark_results(self):
        from test_author_preferences import fixture
        self.data=fixture();self.data['doc']='fixture.md'
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.server.CORRECTIONS.write_text('## 2026-01-01 — Правило автора\nСохраняй конкретику.\n',encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.click('#preferences-btn');self.page.wait_for_function('document.querySelector("#preferences-summary").textContent.includes("Уникальных решений: 1")')
        self.assertIn('Правило автора',self.page.locator('#preferences-rules').inner_text())
        self.page.click('#benchmark-build');self.page.wait_for_function('document.querySelector("#benchmark-summary").textContent.includes("В наборе 1")')
        def answer(prompt,*args):
            self.assertNotIn('Новое слово.',prompt)
            identifier=self.server.PREFERENCES.suite()['cases'][0]['id']
            return {'results':[{'id':identifier,'action':'edit','text':'Новое слово.','reason':'Проверено'}]},{'provider':'fixture'}
        with patch.object(self.server.ai_provider,'text_result',side_effect=answer):
            self.page.click('#benchmark-run');self.page.wait_for_function('document.querySelector("#benchmark-results").textContent.includes("Совпало решений: 1 из 1")',timeout=10000)
        self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['versions'],self.data['versions'])

    def test_rule_proposal_requires_author_acceptance_and_can_be_disabled(self):
        from test_author_preferences import fixture
        self.data=fixture();self.data['doc']='fixture.md';self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.server.CORRECTIONS.write_text('## Правило автора\nСохраняй терминологию.\n',encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.click('#preferences-btn');self.page.wait_for_function('document.querySelector("#preferences-summary").textContent.includes("Уникальных решений: 1")')
        def answer(*args):
            case=self.server.PREFERENCES.collect()[0][0]
            return {'proposals':[{'title':'Уточнение слов','instruction':'Называй предмет конкретно.','rationale':'Автор уточнил фразу.',
                   'replaces':'','evidence_ids':[case['id']]}]},{'provider':'fixture'}
        with patch.object(self.server.ai_provider,'text_result',side_effect=answer):
            self.page.click('#rules-propose');self.page.locator('.rule-proposal').wait_for(timeout=10000)
        self.assertEqual(len(self.server.PREFERENCES.rules()),1)
        self.page.locator('.rule-proposal textarea').fill('Конкретные слова; точные термины сохранять.')
        self.page.locator('.rule-proposal').get_by_role('button',name='Принять правило').click()
        self.page.wait_for_function('document.querySelector("#preferences-summary").textContent.includes("Личных правил: 2")')
        self.assertEqual(self.server.PREFERENCES.rules()[-1]['instruction'],'Конкретные слова; точные термины сохранять.')
        rule=self.page.locator('#preferences-rules details').filter(has_text='Уточнение слов')
        rule.locator('summary').click();rule.get_by_role('button',name='Отключить правило').click()
        self.page.wait_for_function('document.querySelector("#preferences-summary").textContent.includes("Личных правил: 1")')
        self.page.get_by_text('Отключённые и прежние правила',exact=True).click()
        self.assertIn('Уточнение слов',self.page.locator('#inactive-rules').inner_text())

    def test_telegram_send_requires_post_confirmation_and_current_source(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        query='?root=articles&f=fixture.granka.json'
        cases=[('/api/telegram/publish','GET',None,404),
               ('/api/telegram/publish','POST',{'confirmed':False},400),
               ('/api/telegram/prepare','POST',{'version':'v1','text':'Анонс','expected_revision':'stale'},409)]
        with patch('tools.telegram.bot_call') as call:
            for path,method,body,status in cases:
                req=urllib.request.Request(self.base+path+query,method=method,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers={'X-Granka-Token':self.server.TOKEN,'Content-Type':'application/json'})
                with self.subTest(path=path,method=method),self.assertRaises(urllib.error.HTTPError) as caught:opener.open(req)
                self.assertEqual(caught.exception.code,status);caught.exception.close()
        call.assert_not_called()

    def test_site_publication_promotes_same_snapshot_in_order(self):
        from tools.site_publish import SitePublisher
        from test_site_publish import ENGINE,FakeWP
        repo=self.directory/'site-blog';(repo/'tools').mkdir(parents=True,exist_ok=True);(repo/'posts').mkdir(exist_ok=True)
        (repo/'tools/publish-post.py').write_text(ENGINE,encoding='utf-8')
        FakeWP.states={};FakeWP.calls=[]
        self.server.SITE=SitePublisher(lambda:self.directory/'site-state',repo,'mock-origin',FakeWP)
        with patch.object(self.server.SITE,'git',return_value='mock-origin'),patch.object(self.server.SITE,'push_bundle',return_value={'commit':'abc','url':'https://example.org/commit/abc'}):
            self.page.click('#site-btn');self.page.wait_for_function('document.querySelector("#site-post-title").value.length>0')
            self.page.click('#site-prepare');self.page.wait_for_function('document.querySelector("#site-operation").textContent.includes("Снимок готов")')
            rows=self.page.locator('.site-stage')
            self.assertTrue(rows.nth(1).get_by_role('button').is_disabled());self.assertTrue(rows.nth(2).get_by_role('button').is_disabled())
            for index,stage in enumerate(['nas','staging','prod']):
                rows.nth(index).get_by_role('button').click()
                self.page.locator('#site-review').wait_for(state='visible')
                if stage!='nas':
                    self.assertTrue(self.page.locator('#site-confirm').is_disabled());self.page.check('#site-approval')
                if stage=='prod':self.assertEqual(self.page.locator('#site-confirm').inner_text(),'Сохранить черновик на проде')
                self.page.click('#site-confirm');rows.nth(index).get_by_role('link').first.wait_for(timeout=10000)
            self.page.screenshot(path=str(ROOT/'_audit/site-workflow.png'),full_page=True)
        self.assertEqual(FakeWP.calls,['nas','staging','prod'])
        saved=json.loads(self.file.read_text(encoding='utf-8'));self.assertEqual(saved['versions'][0]['text'],self.source)

    def test_site_cannot_publish_without_confirmation_or_with_stale_source(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));query='?root=articles&f=fixture.granka.json'
        for route,method,body,status in [('/api/site/deploy','GET',None,404),('/api/site/prepare','POST',{'expected_revision':'old'},409)]:
            req=urllib.request.Request(self.base+route+query,method=method,
                data=json.dumps(body).encode() if body else None,headers={'X-Granka-Token':self.server.TOKEN,'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as caught:opener.open(req)
            self.assertEqual(caught.exception.code,status);caught.exception.close()

    def test_editorial_request_generates_options_and_applies_only_after_accept(self):
        from test_editorial_requests import request_document,VARIANT,MARKER
        self.data=request_document();self.data['doc']='fixture.md'
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        card=self.page.locator('#card-request')
        self.assertIn('Поручение ИИ',card.inner_text())
        self.assertEqual(card.get_by_role('button',name='Принять',exact=True).count(),0)
        with patch.object(self.server.ai_provider,'text_result',return_value=({'why':'Готовые сравнения','variants':[VARIANT]},{'provider':'codex'})) as provider:
            card.get_by_role('button',name='Выполнить поручение',exact=True).click()
            self.page.wait_for_function('granka.state.document.annotations[0].editorial_request && !granka.state.busy',timeout=10000)
            self.assertEqual(provider.call_count,1)
        self.assertEqual(self.page.evaluate('granka.state.view'),'article')
        self.assertEqual(self.page.evaluate('granka.state.active'),'request')
        self.assertEqual(card.locator('.variant').inner_text(),VARIANT)
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertIsNone(saved['annotations'][0]['decision']);self.assertEqual(len(saved['versions']),1)
        self.page.screenshot(path=str(ROOT/'_audit/editorial-request.png'),full_page=True)
        card.get_by_role('button',name='Принять',exact=True).click()
        self.page.wait_for_function('granka.state.document.annotations[0].decision && !granka.state.busy')
        self.page.click('#readybtn');self.page.wait_for_function('granka.state.document.versions.length===2',timeout=10000)
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['versions'][1]['text'],self.data['versions'][0]['text'].replace(MARKER,VARIANT))

    def test_author_question_has_no_fake_variant_or_generation(self):
        quote='[ЛИЧНОЕ: напиши про мой опыт]'
        self.data['versions'][0]['text']=quote
        self.data['annotations']=[{'id':'personal','type':'candidate','version':'v1','start':0,'end':len(quote),
            'quote':quote,'variants':[quote],'decision':None,'why':'Какой случай ты помнишь?'}]
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        card=self.page.locator('#card-personal')
        self.assertIn('Вопрос автору',card.inner_text());self.assertEqual(card.locator('.variant').count(),0)
        self.assertEqual(card.locator('.editorial-generate').count(),0)
        self.assertEqual(card.get_by_role('button',name='Принять',exact=True).count(),0)
        self.assertTrue(card.get_by_placeholder('Твой ответ для текста статьи').is_visible())
        card.get_by_role('button',name='Убрать вставку',exact=True).click()
        self.page.wait_for_function('granka.state.document.annotations[0].decision && !granka.state.busy')
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['annotations'][0]['decision']['intent'],'delete')
        self.assertIn('Вставка будет удалена',card.inner_text())

    def test_image_is_selected_by_click_and_removed_like_text(self):
        (self.directory/'shot.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII='))
        self.data['versions'][0]['text']='# Заголовок\n\n![Схема](shot.png)\n\n*Подпись.*\n\nТекст с ![вставкой](shot.png) внутри.\n'
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.locator('#doc .md-image').first.click()
        self.assertEqual(self.page.locator('#doc .md-image').first.get_attribute('data-picked'),'1')
        self.assertTrue(self.page.locator('#delimg').is_visible())
        self.page.keyboard.press('Delete')
        self.page.wait_for_function('document.querySelectorAll("#doc .md-image").length===1')
        self.page.locator('#doc .md-image').first.click(button='right')
        self.assertTrue(self.page.locator('#contextdelimage').is_visible())
        self.page.click('#contextdelimage')
        self.page.wait_for_function('document.querySelectorAll("#doc .md-image").length===0')
        self.page.keyboard.press('Control+s')
        self.page.wait_for_function('granka.state.document.versions.length===2 && !granka.state.busy && !granka.state.editor')
        new=json.loads(self.file.read_text(encoding='utf-8'))['versions'][-1]['text']
        self.assertNotIn('shot.png',new)
        self.assertIn('# Заголовок\n\n*Подпись.*',new)
        self.assertIn('внутри.',new)

    def test_local_image_and_path_guard(self):
        (self.directory/'sample.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jLXYAAAAASUVORK5CYII='))
        self.data['versions'][0]['text']='![Пример](sample.png)'
        self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
        self.page.reload();self.page.wait_for_function('window.granka?.state.document && !granka.state.busy')
        self.page.wait_for_function("document.querySelector('#doc img')?.naturalWidth===1")
        query='?root=articles&f=fixture.granka.json&src='
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for route in ['/api/article-image'+query+'../outside.png', '/api/article-image'+query+'fixture.granka.json','/static/../granka-server.py']:
            with self.subTest(route=route),self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(self.base+route)
            self.assertIn(caught.exception.code,(403,404))
            caught.exception.close()


if __name__=='__main__':unittest.main()
