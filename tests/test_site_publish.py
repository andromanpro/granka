from copy import deepcopy
import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools.site_publish import SitePublisher,metadata
from tools.site_wordpress import WordPress,fingerprint

ENGINE='''import markdown
def render_markdown(body):return markdown.markdown(body,extensions=['extra'])
def ensure_term(taxonomy,name,site_dir):return 1
def find_attachment_by_filename(filename,site_dir):return None
'''
ENVIRONMENTS={stage:{'url':'https://'+stage+'.example.invalid','ssh':['publisher@host.example.invalid'],
                     'wp':['~/bin/wp','--path=~/sites/'+stage]} for stage in ['nas','staging','prod']}

def fields():
    return {'title':'Статья','slug':'article','date':str(datetime.date.today()),'status':'draft',
            'categories':['дев-лог'],'tags':[],'excerpt':'Описание'}

class FakeWP:
    states={};calls=[]
    def __init__(self,stage,directory):self.stage=stage
    def inspect(self,slug):return deepcopy(self.states.get(self.stage))
    def deploy(self,plan,baseline):
        if fingerprint(self.states.get(self.stage))!=baseline:raise ValueError('changed')
        self.calls.append(self.stage)
        self.states[self.stage]={'id':42,'title':plan['metadata']['title'],'status':'publish','url':'https://example.org/article','modified':'now'}
        return {'post_id':42,'url':'https://example.org/article','post_status':'draft' if self.stage=='prod' else 'publish','fingerprint':fingerprint(self.states[self.stage])}

class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.repo=self.root/'blog';self.repo.mkdir();(self.repo/'posts').mkdir();(self.repo/'tools').mkdir()
        (self.repo/'tools/publish-post.py').write_text(ENGINE,encoding='utf-8')
        self.remote=self.root/'remote.git'
        subprocess.run(['git','init','--bare',str(self.remote)],capture_output=True,check=True)
        self.git('init','-b','main');self.git('config','user.name','Test');self.git('config','user.email','test@example.org')
        self.git('add','tools/publish-post.py');self.git('commit','-m','initial');self.git('remote','add','origin',str(self.remote));self.git('push','-u','origin','main')
        self.source=self.root/'articles';self.source.mkdir();self.document=self.source/'article.granka.json'
        self.data={'doc':'article.md','versions':[{'id':'v1','text':'# Статья\n\nТекст статьи.\n'}],'annotations':[]}
        self.document.write_text(json.dumps(self.data),encoding='utf-8')
        FakeWP.states={};FakeWP.calls=[]
        self.service=SitePublisher(lambda:self.root/'state',self.repo,str(self.remote),FakeWP)

    def tearDown(self):self.temp.cleanup()
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.DEVNULL).decode('utf-8').strip()
    def prepare(self):return self.service.prepare('articles','article.granka.json',self.data,self.document,self.source,fields())
    def deploy(self,plan,stage,approval=True):
        review=self.service.review(plan['id'],stage)
        return self.service.deploy(plan['id'],review['id'],approval)

    def test_bundle_preserves_markdown_and_copies_cover_and_reference_images(self):
        assets=self.source/'article.assets';assets.mkdir();(assets/'тест.png').write_bytes(b'png')
        self.data['versions'][0]['text']='# Статья\n\n**Текст**.\n\n![Картинка][ref]\n\n[ref]: article.assets/%D1%82%D0%B5%D1%81%D1%82.png\n'
        self.data['versions'][0]['cover']={'src':'article.assets/%D1%82%D0%B5%D1%81%D1%82.png','alt':'Обложка'}
        old=deepcopy(self.data);plan=self.prepare();content=(self.service.path(plan['id'])/'bundle'/plan['file']).read_text(encoding='utf-8')
        self.assertEqual(self.data,old);self.assertEqual(len(plan['assets']),1)
        self.assertIn('**Текст**',content);self.assertIn('[ref]: ../covers/',content);self.assertIn('featured_image:',content)
        self.assertNotIn('# Статья',content);self.assertFalse((self.repo/plan['file']).exists())

    def test_three_stages_require_order_and_approval(self):
        plan=self.prepare()
        with self.assertRaises(ValueError):self.service.review(plan['id'],'prod')
        self.assertEqual(self.deploy(plan,'nas')['status'],'succeeded')
        review=self.service.review(plan['id'],'staging')
        with self.assertRaises(ValueError):self.service.deploy(plan['id'],review['id'],False)
        self.assertEqual(self.service.deploy(plan['id'],review['id'],True)['status'],'succeeded')
        self.assertEqual(self.deploy(plan,'prod')['post_status'],'draft')
        self.assertEqual(FakeWP.calls,['nas','staging','prod'])

    def test_published_article_keeps_address_date_file_and_shows_unpublished_edits(self):
        plan=self.prepare()
        for stage in ['nas','staging','prod']:self.deploy(plan,stage)
        old=self.service.info(self.data,'articles','article.granka.json')
        self.assertFalse(old['publication']['changed'])
        self.data['versions'].append({'id':'v2','text':'# Новый заголовок\n\nИсправленный текст.'})
        info=self.service.info(self.data,'articles','article.granka.json')
        self.assertTrue(info['publication']['changed']);self.assertEqual(info['metadata']['title'],'Новый заголовок')
        self.assertEqual(info['metadata']['slug'],'article');self.assertEqual(info['metadata']['date'],plan['metadata']['date'])
        updated=self.service.prepare('articles','article.granka.json',self.data,self.document,self.source,info['metadata'])
        self.assertEqual(updated['file'],plan['file'])
        with self.assertRaises(ValueError):
            self.service.prepare('articles','article.granka.json',self.data,self.document,self.source,{**info['metadata'],'slug':'new-address'})
        with self.assertRaises(ValueError):
            self.service.prepare('articles','article.granka.json',self.data,self.document,self.source,{**info['metadata'],'date':'2020-01-01'})

    def test_wordpress_update_uses_existing_post_id(self):
        plan=self.prepare();directory=self.service.path(plan['id']);wp=WordPress('prod',directory,ENVIRONMENTS)
        before={'id':42,'title':'Old','content':'Old content','status':'publish','cover':0,'date':str(datetime.date.today())+' 00:00:00'}
        after={**before,'title':'Статья','status':'draft','excerpt':'Описание','cover':0,'categories':['дев-лог'],
               'tags':[],'content':'<p>Текст статьи.</p>','url':'https://prod.example.invalid/article/'}
        calls=[]
        def run(args,content=None):
            calls.append(args);return str(datetime.date.today()) if args[0]=='eval' else '42'
        with patch.object(wp,'inspect',side_effect=[before,after]),patch.object(wp,'wp',side_effect=run):
            result=wp.deploy(plan,fingerprint(before))
        self.assertEqual(result['post_id'],42)
        self.assertTrue(any(args[:4]==['post','update','42','-'] for args in calls))
        self.assertFalse(any(args[:2]==['post','create'] for args in calls))

    def test_git_push_preserves_unrelated_staged_and_untracked_files(self):
        (self.repo/'unrelated.txt').write_text('staged');self.git('add','unrelated.txt')
        (self.repo/'posts/another.md').write_text('---\ntitle: Other\nslug: other\n---\nUnrelated')
        plan=self.prepare();self.deploy(plan,'nas')
        self.assertEqual(self.git('diff','--cached','--name-only'),'unrelated.txt')
        committed=self.git('show','--pretty=','--name-only','HEAD')
        self.assertNotIn('unrelated',committed);self.assertNotIn('another.md',committed)
        self.assertEqual(self.git('rev-parse','HEAD'),self.git('ls-remote','origin','refs/heads/main').split()[0])

    def test_failed_gitea_push_blocks_nas_and_can_be_explicitly_retried(self):
        plan=self.prepare();real=self.service.git
        def git(*args,**kw):
            if args[0]=='push':raise RuntimeError('Gitea down')
            return real(*args,**kw)
        with patch.object(self.service,'git',side_effect=git),self.assertRaises(RuntimeError):self.deploy(plan,'nas')
        self.assertEqual(FakeWP.calls,[])
        with self.assertRaises(ValueError):self.service.review(plan['id'],'staging')
        self.assertEqual(self.deploy(plan,'nas')['status'],'succeeded')

    def test_changed_remote_post_and_bundle_stop_before_writes(self):
        plan=self.prepare();review=self.service.review(plan['id'],'nas')
        FakeWP.states['nas']={'id':42,'content':'edited in admin','title':'Изменено','status':'publish','url':'https://example.org/post','modified':'now'}
        with self.assertRaises(ValueError):self.service.deploy(plan['id'],review['id'],False)
        self.assertFalse((self.repo/plan['file']).exists())
        (self.service.path(plan['id'])/'bundle'/plan['file']).write_text('tampered')
        with self.assertRaises(ValueError):self.deploy(plan,'nas')
        self.assertEqual(FakeWP.calls,[])

    def test_collision_and_unfilled_requests_cannot_be_exported(self):
        (self.repo/'posts/existing.md').write_text('---\ntitle: Existing\nslug: article\n---\nOther')
        with self.assertRaises(ValueError):self.prepare()
        self.data['versions'][0]['text']='[ЛИЧНОЕ: расскажи случай]'
        with self.assertRaises(ValueError):self.prepare()
        for slug in ['../bad','a;rm','UPPER','a$(cmd)']:
            with self.subTest(slug=slug),self.assertRaises(ValueError):metadata({**fields(),'slug':slug})

    def test_unrelated_local_commits_are_not_pushed(self):
        plan=self.prepare();(self.repo/'other.txt').write_text('another task');self.git('add','other.txt');self.git('commit','-m','other task')
        with self.assertRaises(ValueError):self.deploy(plan,'nas')
        self.assertEqual(FakeWP.calls,[])

    def test_ssh_uses_stdin_for_content_and_quotes_arguments(self):
        wp=WordPress('staging',self.root,ENVIRONMENTS)
        with patch.object(wp,'ssh',return_value='42') as ssh:
            wp.wp(['post','create','-','--post_title=Title $(command)'],content='текст'*10000)
        command,content=ssh.call_args.args
        self.assertIn("'--post_title=Title $(command)'",command)
        self.assertIn('--path="$HOME"/sites/staging',command)
        self.assertNotIn('текст',command);self.assertEqual(content,('текст'*10000).encode('utf-8'))

    def test_previous_site_edit_blocks_promotion(self):
        plan=self.prepare();self.deploy(plan,'nas')
        FakeWP.states['nas']['title']='Исправлено в админке'
        with self.assertRaises(ValueError):self.service.review(plan['id'],'staging')
        self.assertEqual(FakeWP.calls,['nas'])

    def test_wordpress_writer_sends_content_on_stdin_and_verifies_result(self):
        plan=self.prepare();directory=self.service.path(plan['id']);wp=WordPress('prod',directory,ENVIRONMENTS)
        after={'id':42,'title':'Статья','status':'draft','excerpt':'Описание','cover':0,'categories':['дев-лог'],
               'tags':[],'content':'<p>Текст статьи.</p>','url':'https://prod.example.invalid/article/'}
        calls=[]
        def run(args,content=None):
            calls.append((args,content))
            return str(datetime.date.today()) if args[0]=='eval' else '42'
        with patch.object(wp,'inspect',side_effect=[None,after]),patch.object(wp,'wp',side_effect=run):
            result=wp.deploy(plan,fingerprint(None))
        self.assertEqual(result['post_status'],'draft')
        args,html=next(c for c in calls if c[0][:2]==['post','create'])
        self.assertEqual(args[2],'-');self.assertIn('--post_status=draft',args)
        self.assertEqual(html,'<p>Текст статьи.</p>');self.assertFalse(any(a.startswith('--post_content=') for a in args))
        with patch.object(wp,'inspect',side_effect=[None,{**after,'content':'Wrong result'}]),patch.object(wp,'wp',side_effect=run),self.assertRaises(RuntimeError):
            wp.deploy(plan,fingerprint(None))

    def test_preconnection_timeout_can_retry_but_broken_connection_cannot(self):
        wp=WordPress('staging',self.root,ENVIRONMENTS)
        timeout=subprocess.CompletedProcess([],255,b'',b'ssh: connect to host host.example.invalid port 22: Connection timed out\n')
        success=subprocess.CompletedProcess([],0,b'42',b'')
        with patch('tools.site_wordpress.subprocess.run',side_effect=[timeout,success]) as call:
            self.assertEqual(wp.ssh('trusted-command'),'42');self.assertEqual(call.call_count,2)
        broken=subprocess.CompletedProcess([],255,b'',b'Connection to host.example.invalid closed: Broken pipe\n')
        with patch('tools.site_wordpress.subprocess.run',return_value=broken) as call,self.assertRaises(RuntimeError):wp.ssh('trusted-command')
        self.assertEqual(call.call_count,1)

    def test_upload_preserves_hash_filename(self):
        from unittest.mock import Mock
        path=self.root/('a'*64+'.png');path.write_bytes(b'png')
        wp=WordPress('staging',self.root,ENVIRONMENTS);engine=Mock();engine.find_attachment_by_filename.return_value=None
        imported=[]
        def call(args,content=None):
            if args[:2]==['media','import']:imported.append(args[2]);return '7'
            if 'wp_get_attachment_url' in args[-1]:return 'https://staging.example.invalid/uploads/'+path.name
            return ''
        with patch.object(wp,'wp',side_effect=call),patch.object(wp,'remote_shell'):
            wp.media(path,engine)
        self.assertEqual(Path(imported[0]).name,path.name)

    def test_existing_legacy_cover_is_reused_after_partial_publication(self):
        from unittest.mock import Mock
        path=self.root/('b'*64+'.png');path.write_bytes(b'png')
        wp=WordPress('staging',self.root,ENVIRONMENTS);engine=Mock();engine.find_attachment_by_filename.return_value=None
        def call(args,content=None):
            if 'wp_get_attachment_url' in args[-1]:return 'https://staging.example.invalid/uploads/legacy.png'
            return '277'
        with patch.object(wp,'wp',side_effect=call),patch.object(wp,'remote_shell') as upload:
            result=wp.media(path,engine)
        self.assertEqual(result[0],277);upload.assert_not_called()
