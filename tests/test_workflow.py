from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from tools.editorial_workflow import apply_decisions,add_analysis
from tools.document_store import validate
from tools.jobs import Jobs
ROOT=Path(__file__).resolve().parents[1]


def sample():
    text='# Тема\n\nПодводка: **ссылка**.\n\n## Что это\n\nСтарый оборот.\n'
    specs=[('a1','Подводка:','candidate','— (убрать подводку, оставить ссылку)'),
           ('n1','## Что это','note','Что это за херня? - вот так надо)'),
           ('a2','Старый оборот.','candidate','Своя фраза.')]
    return {'doc':'article.md','status':'ready','versions':[{'id':'v1','text':text}],
            'annotations':[{'id':aid,'version':'v1','type':kind,'start':text.index(quote),'end':text.index(quote)+len(quote),'quote':quote,
                            'decision':{'action':'custom','text':replacement}} for aid,quote,kind,replacement in specs]}


class WorkflowTests(unittest.TestCase):
    def test_apply_preserves_parent_and_interprets_legacy_instructions(self):
        data=sample();old=deepcopy(data);result=apply_decisions(data,'v1')
        self.assertEqual(data['versions'][0],old['versions'][0]);self.assertEqual(data['annotations'][:3],old['annotations'])
        text=data['versions'][-1]['text'];self.assertNotIn('убрать подводку',text);self.assertNotIn('вот так надо',text)
        self.assertIn('## Что это за херня?',text);self.assertIn('Своя фраза.',text);self.assertEqual(len(result['changes']),3)
        self.assertEqual(data['status'],'applied');self.assertEqual(validate(data),[])

    def test_comment_never_replaces_article(self):
        data=sample();data['annotations']=data['annotations'][1:2];data['annotations'][0]['decision'].update(intent='comment',text='Поясни этот раздел подробнее')
        text=data['versions'][0]['text'];result=apply_decisions(data,'v1');self.assertTrue(result['unchanged']);self.assertEqual(data['versions'][0]['text'],text)

    def test_analysis_quotes_are_validated_and_old_decisions_remain(self):
        data=sample();old=deepcopy(data['annotations']);result={'summary':'Проверено','findings':[
            {'quote':'несуществующая цитата','occurrence':0,'class':'тест','severity':'P2','why':'Причина','variants':['замена']},
            {'quote':'**ссылка**','occurrence':0,'class':'ясность','severity':'P2','why':'Уточнить подпись','variants':['**адрес проекта**']}]}
        out=add_analysis(data,'v1',result,'codex');self.assertEqual(out['added'],1);self.assertEqual(len(out['skipped']),1)
        self.assertEqual(data['annotations'][:3],old);self.assertEqual(validate(data),[])


class JobServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        spec=importlib.util.spec_from_file_location('workflow_server',ROOT/'granka-server.py');self.server=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.server)
        self.server.ROOTS={'articles':self.root};self.server.GIT_ROOTS={'articles':self.root}
        self.server.CORRECTIONS=self.root/'rules.md'
        from tools.author_preferences import AuthorPreferences
        self.server.PREFERENCES=AuthorPreferences(lambda:self.root/'preferences',lambda:self.server.ROOTS,lambda:self.server.CORRECTIONS)
        self.server.JOBS=Jobs(self.root/'jobs');self.file=self.root/'article.granka.json'
        self.file.write_text(json.dumps(sample(),ensure_ascii=False),encoding='utf-8');(self.root/'article.md').write_text(sample()['versions'][0]['text'],encoding='utf-8')
        self.config_patch=patch.object(self.server.ai_provider,'CONFIG',self.root/'config.json');self.config_patch.start()

    def tearDown(self):
        self.server.JOBS.pool.shutdown(wait=True);self.config_patch.stop();self.temp.cleanup()

    def start(self,kind,op='one'):
        code,response=self.server.enqueue_job('articles','article.granka.json',{'kind':kind,'version':'v1','expected_revision':self.server.revision_of(self.file),'operation_id':op})
        self.assertEqual(code,202);return response['job']['id']

    def wait(self,job_id):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            job=self.server.JOBS.get(job_id)
            if job['status'] not in {'queued','running'}:return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_apply_job_creates_version_and_materializes_markdown(self):
        job=self.wait(self.start('apply'));self.assertEqual(job['status'],'succeeded');self.assertEqual(job['result']['version'],'v2')
        data=json.loads(self.file.read_text(encoding='utf-8'));self.assertEqual((self.root/'article.md').read_text(encoding='utf-8'),data['versions'][-1]['text'])
        self.assertEqual(len(data['versions']),2)

    def test_analysis_conflict_never_overwrites_concurrent_edits(self):
        entered=threading.Event();release=threading.Event()
        def response(*args):entered.set();release.wait(3);return {'summary':'Разбор','findings':[]},{'provider':'codex'}
        with patch.object(self.server.ai_provider,'text_result',side_effect=response):
            job_id=self.start('analysis');self.assertTrue(entered.wait(2));data=json.loads(self.file.read_text(encoding='utf-8'));data['external']='new';self.file.write_text(json.dumps(data),encoding='utf-8');release.set();job=self.wait(job_id)
        self.assertEqual(job['status'],'conflict');self.assertEqual(json.loads(self.file.read_text(encoding='utf-8'))['external'],'new')

    def test_provider_failure_is_visible_and_not_retried(self):
        with patch.object(self.server.ai_provider,'text_result',side_effect=RuntimeError('Проверочная ошибка')) as provider:
            job=self.wait(self.start('analysis'));self.assertEqual(job['status'],'failed');self.assertIn('Проверочная ошибка',job['message']);self.assertEqual(provider.call_count,1)

    def test_deep_job_preserves_article_and_conflicting_result_stays_in_journal(self):
        from test_deep_analysis import document,responses
        for conflict in [False,True]:
            with self.subTest(conflict=conflict):
                data=document();data['feedback']=[{'id':'f1','text':'Проверь развитие мысли.','read_by_agent':False}]
                self.file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
                before_md=(self.root/'article.md').read_bytes()
                entered=threading.Event();release=threading.Event();replies=iter(responses())
                def respond(*args):
                    result=next(replies)
                    if args[2].name=='verify':entered.set();release.wait(3)
                    return result,{'provider':'codex'}
                with patch.object(self.server.ai_provider,'text_result',side_effect=respond) as provider:
                    job_id=self.start('deep-analysis',op='deep-'+str(conflict))
                    self.assertTrue(entered.wait(2))
                    if conflict:
                        changed=json.loads(self.file.read_text(encoding='utf-8'));changed['external']='fresh'
                        self.file.write_text(json.dumps(changed),encoding='utf-8')
                    release.set();job=self.wait(job_id)
                saved=json.loads(self.file.read_text(encoding='utf-8'))
                self.assertEqual(provider.call_count,3)
                self.assertEqual(job['status'],'conflict' if conflict else 'succeeded',job)
                self.assertEqual(saved['versions'],data['versions']);self.assertEqual(saved['annotations'],[])
                self.assertEqual((self.root/'article.md').read_bytes(),before_md)
                self.assertTrue((self.root/'jobs'/job_id/'deep-report.json').is_file())
                self.assertEqual(bool(saved.get('deep_analyses')),not conflict)
                self.assertEqual(saved['feedback'][0]['read_by_agent'],not conflict)

    def test_settings_keep_both_providers_and_default_codex(self):
        self.assertEqual(self.server.ai_provider.settings()['image_provider'],'codex')
        out=self.server.ai_provider.save_settings({'image_provider':'openrouter','openrouter_image_model':'test/model','analysis_provider':'codex'})
        self.assertEqual(out['image_provider'],'openrouter');self.assertEqual(self.server.ai_provider.settings()['analysis_provider'],'codex')
        self.server.ai_provider.save_settings({'image_provider':'codex'});self.assertEqual(self.server.ai_provider.settings()['openrouter_image_model'],'test/model')

    def test_focused_editorial_job_refreshes_card_without_rewriting_article(self):
        from test_editorial_requests import request_document,VARIANT
        from tools.editorial_requests import REQUEST_SCHEMA
        data=request_document();self.file.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        original_md=(self.root/'article.md').read_bytes()
        with patch.object(self.server.ai_provider,'text_result',return_value=({'why':'Готово','variants':[VARIANT]},{'provider':'codex'})) as provider:
            code,response=self.server.enqueue_job('articles','article.granka.json',{
                'kind':'editorial','version':'v1','expected_revision':self.server.revision_of(self.file),
                'operation_id':'editorial-test','annotation_id':'request'})
            self.assertEqual(code,202);job=self.wait(response['job']['id'])
            self.assertEqual(job['status'],'succeeded',job)
            self.assertEqual(provider.call_args.args[1],REQUEST_SCHEMA)
        saved=json.loads(self.file.read_text(encoding='utf-8'))
        self.assertEqual(saved['versions'],data['versions']);self.assertEqual(saved['annotations'][0]['variants'],[VARIANT])
        self.assertIsNone(saved['annotations'][0]['decision'])
        self.assertEqual((self.root/'article.md').read_bytes(),original_md)

    def test_announcement_job_keeps_article_unchanged(self):
        original=self.file.read_bytes()
        response={'variants':[{'label':'Прямо','text':'Написал про проверку результата.'}]}
        with patch.object(self.server.ai_provider,'text_result',return_value=(response,{'provider':'codex'})):
            job=self.wait(self.start('announcement'))
        self.assertEqual(job['status'],'succeeded',job);self.assertEqual(job['result']['variants'],response['variants'])
        self.assertEqual(self.file.read_bytes(),original)
