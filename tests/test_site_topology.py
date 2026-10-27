from copy import deepcopy
import datetime,json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import ai_provider
from tools.site_publish import SitePublisher
from tools.site_wordpress import WordPress,fingerprint
from tools.site_topology import validate,order,publication_result
from tools.publication_plan import PublicationPlan
from test_site_publish import FakeWP,fields


def site(sequence=('live',)):
    return {'enabled':True,'git_enabled':False,'pipeline':list(sequence),
            'environments':{k:{'label':k.title(),'url':'https://'+k+'.example.invalid','ssh':['publisher@host.example.invalid'],'wp':['wp','--path=/var/www/site']}for k in sequence}}


class TopologyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.document=self.root/'article.granka.json';self.data={'doc':'article.md','versions':[{'id':'v1','text':'# Статья\n\nТекст.'}],'annotations':[]};self.document.write_text(json.dumps(self.data),encoding='utf-8')
        self.config=site();FakeWP.states={};FakeWP.calls=[]

    def service(self):
        with patch('tools.config.load',return_value={'site':self.config}):return SitePublisher(lambda:self.root/'state',wordpress=FakeWP,environments=self.config['environments'])
    def prepare(self,service):return service.prepare('articles','article.granka.json',self.data,self.document,self.root,fields())
    def deploy(self,service,plan,stage,approval=False):
        review=service.review(plan['id'],stage);return service.deploy(plan['id'],review['id'],approval)

    def test_single_wordpress_no_git_repository_or_preview(self):
        s=self.service()
        with patch.object(s,'git',side_effect=AssertionError('Git must not run')):
            p=self.prepare(s);self.assertEqual(p['pipeline'],['live']);r=self.deploy(s,p,'live')
        self.assertEqual(r['status'],'succeeded');self.assertEqual(FakeWP.calls,['live']);self.assertIsNone(s.repo)
        info=s.info(self.data,'articles','article.granka.json');self.assertTrue(info['configured']);self.assertEqual(info['publication']['post_id'],42)

    def test_disabled_git_does_not_resolve_a_stored_unavailable_repository(self):
        self.config.update(repository='unavailable-old-repository',remote='https://git.example.invalid/old.git')
        with patch('tools.config.load',return_value={'site':self.config}),patch('tools.config.path',side_effect=AssertionError('Disabled Git path must not be resolved')):
            s=SitePublisher(lambda:self.root/'state',wordpress=FakeWP,environments=self.config['environments'])
            self.prepare(s)
        self.assertIsNone(s.repo)

    def test_two_arbitrary_stages_order_and_approval_and_previous_edit(self):
        self.config=site(('review','website'));s=self.service();p=self.prepare(s)
        with self.assertRaises(ValueError):s.review(p['id'],'website')
        self.deploy(s,p,'review');review=s.review(p['id'],'website');self.assertEqual(review['previous'],'review');self.assertTrue(review['final'])
        with self.assertRaises(ValueError):s.deploy(p['id'],review['id'],False)
        FakeWP.states['review']['title']='External edit'
        with self.assertRaisesRegex(ValueError,'изменилась'):s.deploy(p['id'],review['id'],True)
        self.assertEqual(FakeWP.calls,['review'])

    def test_repoint_or_reorder_requires_new_snapshot_proxy_and_label_can_change(self):
        self.config=site(('review','website'));s=self.service();p=self.prepare(s)
        s.environments['review']['ssh_proxy']='system';s.environments['review']['label']='New label';s.review(p['id'],'review')
        s.environments['website']['url']='https://changed.example.invalid'
        with self.assertRaisesRegex(ValueError,'Подключение'):s.review(p['id'],'review')
        s.environments['website']['url']='https://website.example.invalid';s.config['pipeline']=['website','review']
        with self.assertRaises(ValueError):s.review(p['id'],'review')
        self.assertEqual(FakeWP.calls,[])

    def test_stale_remote_and_modified_bundle_still_block_single_site(self):
        s=self.service();p=self.prepare(s);review=s.review(p['id'],'live')
        FakeWP.states['live']={'id':1,'title':'Other','status':'publish','url':'https://live.example.invalid/other','modified':'now'}
        with self.assertRaises(ValueError):s.deploy(p['id'],review['id'],False)
        FakeWP.states.clear();(s.path(p['id'])/'bundle'/p['file']).write_text('tampered',encoding='utf-8')
        with self.assertRaises(ValueError):self.deploy(s,p,'live')
        self.assertEqual(FakeWP.calls,[])

    def test_final_arbitrary_key_controls_wordpress_draft(self):
        s=self.service();p=self.prepare(s);wp=WordPress('live',s.path(p['id']),self.config['environments'])
        after={'id':42,'title':'Статья','status':'draft','excerpt':'Описание','cover':0,'categories':['дев-лог'],'tags':[],'content':'<p>Текст.</p>','url':'https://live.example.invalid/article/'}
        def run(args,content=None):return str(datetime.date.today())if args[0]=='eval'else'42'
        with patch.object(wp,'inspect',side_effect=[None,after]),patch.object(wp,'wp',side_effect=run)as call:
            result=wp.deploy(p,fingerprint(None))
        self.assertEqual(result['post_status'],'draft');self.assertTrue(any('--post_status=draft'in c.args[0]for c in call.call_args_list))

    def test_general_receipt_marks_library_published_only_when_final_is_publish(self):
        s=self.service();p=self.prepare(s);p['stages']={'live':{'status':'succeeded','post_status':'publish','url':'https://live.example.invalid/article/'}}
        from tools.ai_provider import atomic_json
        atomic_json(s.path(p['id'])/'plan.json',p)
        receipts=PublicationPlan(lambda:self.root/'state').receipts();self.assertEqual(len(receipts),1)
        p['stages']['live']['post_status']='draft';atomic_json(s.path(p['id'])/'plan.json',p);self.assertEqual(PublicationPlan(lambda:self.root/'state').receipts(),[])

    def test_legacy_receipts_and_configuration_keep_old_order(self):
        self.config=site(('nas','staging','prod'));self.config.pop('pipeline');self.assertEqual(order(self.config),['nas','staging','prod'])
        s=self.service();p=self.prepare(s);p.pop('pipeline');p.pop('destination_signature')
        from tools.ai_provider import atomic_json
        atomic_json(s.path(p['id'])/'plan.json',p)
        for stage in ['nas','staging','prod']:self.deploy(s,p,stage,True)
        self.assertEqual(FakeWP.calls,['nas','staging','prod']);self.assertEqual(publication_result(s.read(p['id']))['post_id'],42)

    def test_settings_atomic_full_setup_and_validation_preserves_previous_machine_fields(self):
        path=self.root/'settings.json';path.write_text(json.dumps({'private':'keep'}),encoding='utf-8')
        from tools.site_connections import snapshot,SettingsConflict
        revision=snapshot(path)['revision']
        with patch.object(ai_provider,'CONFIG',path):ai_provider.save_settings({'codex_model':'model'},site=self.config,expected_revision=revision)
        saved=json.loads(path.read_text(encoding='utf-8'));self.assertEqual(saved['private'],'keep');self.assertEqual(saved['site']['pipeline'],['live'])
        before=path.read_bytes()
        with patch.object(ai_provider,'CONFIG',path),self.assertRaises(SettingsConflict):ai_provider.save_settings({},site=site(('other',)),expected_revision=revision)
        self.assertEqual(path.read_bytes(),before)
        for bad in [{**self.config,'pipeline':['missing']},{**self.config,'pipeline':['live','live']},{**self.config,'git_enabled':True},{**self.config,'environments':{}}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate(bad)

    def test_empty_fresh_settings_have_no_nas_or_addresses(self):
        from tools.site_connections import snapshot
        result=snapshot(self.root/'missing.json');self.assertEqual(result['environments'],[]);self.assertFalse(result['site']['enabled']);self.assertFalse(result['site']['git_enabled'])
