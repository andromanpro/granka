"""Published copies must not look like unpublished editorial drafts."""
import unittest
from playwright.sync_api import expect
import test_workspace_browser as workspace


class PublicationStatusBrowserTests(unittest.TestCase):
    setUpClass=classmethod(workspace.WorkspaceBrowserTests.setUpClass.__func__)
    tearDownClass=classmethod(workspace.WorkspaceBrowserTests.tearDownClass.__func__)
    setUp=workspace.WorkspaceBrowserTests.setUp
    tearDown=workspace.WorkspaceBrowserTests.tearDown
    wait_ready=workspace.WorkspaceBrowserTests.wait_ready
    reload_ready=workspace.WorkspaceBrowserTests.reload_ready
    go=workspace.WorkspaceBrowserTests.go
    def test_publication_view_evidence_and_partial_channels(self):
        result=self.page.evaluate('''async () => {
          const {publicationView:view}=await import('/static/ui/publication-state.js');
          const base={root:'articles',file:'fixture.granka.json',version:'v2'};
          const source={root:'articles',granka_rel:'fixture.granka.json',state:'published',channel:'article',url:'https://example.org/post'};
          const sent={status:'sent',version:'v2',url:'https://t.me/example/4'};
          return [view(base),view({...base,site:{publication:{post_status:'draft',url:'https://example.org/draft'}}}),
            view({...base,telegram:[{...sent,status:'unknown'}]}),view({...base,sources:[source]}),
            view({...base,sources:[source],telegram:[sent]}),view({...base,telegram:[sent]}),
            view({...base,site:{publication:{post_status:'publish',version:'v1',changed:true,url:'https://example.org/post'}}}),
            view({...base,sources:[{...source,root:'blog',granka_rel:'export.granka.json',copies:[{root:'articles',granka_rel:'fixture.granka.json'}]}]}),
            view({...base,telegram:[{...sent,url:'javascript:alert(1)'}]})];
        }''')
        self.assertEqual(result[:3],[None,None,None])
        self.assertEqual(result[3]['label'],'Опубликовано на сайте')
        self.assertEqual(result[4]['label'],'Опубликовано на сайте и в Telegram')
        self.assertEqual(result[5]['label'],'Опубликовано в Telegram')
        self.assertTrue(result[6]['changed'])
        self.assertEqual(result[7]['label'],'Опубликовано на сайте')
        self.assertIsNone(result[8])

    def test_header_uses_catalog_but_does_not_change_document(self):
        before=self.file.read_bytes()
        self.page.route('**/api/sources',lambda route:route.fulfill(json={'sources':[{
            'root':'articles','granka_rel':'fixture.granka.json','state':'published','channel':'article',
            'url':'https://example.org/already-published','copies':[]}]}))
        self.reload_ready()
        expect(self.page.locator('#stage-label')).to_have_text('Опубликовано на сайте')
        self.assertEqual(self.file.read_bytes(),before)

    def test_help_keeps_unfinished_source_and_returns_to_editor(self):
        self.page.click('[data-view=source]')
        draft='# Мой незавершённый текст\n\nЕго нельзя потерять.'
        self.page.fill('#texteditor',draft)
        self.go('help')
        expect(self.page.locator('#helpdialog')).to_be_visible()
        expect(self.page.locator('#helpdialog img')).to_have_count(8)
        self.page.locator('#help-close').click()
        expect(self.page.locator('body')).to_have_attribute('data-section','editor')
        expect(self.page.locator('#texteditor')).to_have_value(draft)

