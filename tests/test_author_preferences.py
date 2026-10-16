from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from tools.author_preferences import AuthorPreferences,decisions,read_rules
from tools.editorial_workflow import analysis_prompt,add_analysis


def fixture():
    text='До. Старое слово. После.';quote='Старое слово.';start=text.index(quote)
    ann={'id':'a1','version':'v1','type':'candidate','start':start,'end':start+len(quote),'quote':quote,
         'variants':['Новое слово.'],'decision':{'action':'accept','text':'Новое слово.','ts':'2026-01-01'}}
    clone={**deepcopy(ann),'id':'a2','source_annotation':'a1','version':'v2'}
    return {'doc':'fixture.md','versions':[{'id':'v1','text':text},{'id':'v2','text':text}],'annotations':[ann,clone]}


class AuthorPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.rules=self.root/'rules.md'
        self.rules.write_text('# Header must not be copied\n\n## 2026-01-01 — Короткие подводки\nУбирай пустые подводки.\n',encoding='utf-8')
        self.data=fixture();self.file=self.root/'sample.granka.json';self.save()
        self.preferences=AuthorPreferences(lambda:self.root/'state',lambda:{'articles':self.root},lambda:self.rules)
    def save(self):self.file.write_text(json.dumps(self.data,ensure_ascii=False),encoding='utf-8')
    def tearDown(self):self.temp.cleanup()

    def test_inherited_decisions_count_once_and_undo_removes_example(self):
        self.assertEqual(len(decisions(self.data,'source')),1)
        self.data['annotations'][-1]['decision']=None
        self.assertEqual(decisions(self.data,'source'),[])

    def test_bad_variants_rejection_is_not_a_keep_rule(self):
        self.data['annotations'][-1]['decision']={'action':'reject','reason':'worse'}
        item=decisions(self.data,'source')[0]
        self.assertEqual(item['expected'],'avoid_variants');self.assertEqual(item['rejected_variants'],['Новое слово.'])
        self.data['annotations'][-1]['decision']['reason']='voice'
        self.assertEqual(decisions(self.data,'source')[0]['expected'],'keep')

    def test_rules_and_examples_enter_prompt_without_paths_or_factual_authority(self):
        context=self.preferences.context('Новое слово.')
        self.assertEqual(context['total_decisions'],1);self.assertEqual(len(context['rules']),1)
        self.assertNotIn('Header must',json.dumps(context))
        self.assertNotIn(str(self.root),json.dumps(context))
        prompt=analysis_prompt('Статья',[],author_preferences=context)
        self.assertIn('Короткие подводки',prompt);self.assertIn('Новое слово.',prompt)
        self.assertIn('не переноси факты',prompt)

    def test_benchmark_withholds_answers_and_is_frozen(self):
        self.preferences.build_suite();suite,cases,prompt=self.preferences.evaluation(8)
        self.assertNotIn('Новое слово.',prompt);self.assertNotIn('expected',prompt)
        self.data['annotations'][-1]['decision']['text']='Другое решение';self.save()
        self.assertEqual(self.preferences.suite()['fingerprint'],suite['fingerprint'])
        result={'results':[{'id':cases[0]['id'],'action':'keep','text':'','reason':'Оставить'}]}
        report=self.preferences.score(suite,cases,result,{'provider':'fixture'},'one')
        self.assertEqual(report['passed'],0)
        result['results'][0].update(action='edit',text='Новое слово.')
        report=self.preferences.score(suite,cases,result,{},'two')
        self.assertEqual(report['passed'],1);self.assertTrue(report['results'][0]['exact_match'])

    def test_noop_and_personal_placeholder_are_not_regression_targets(self):
        self.data['annotations'][-1]['decision']['text']=self.data['annotations'][-1]['quote']
        self.assertEqual(decisions(self.data,'source'),[])
        self.data['annotations'][-1]['quote']='[ЛИЧНОЕ: напиши случай]'
        self.assertEqual(decisions(self.data,'source'),[])

    def test_analysis_keeps_explicit_rule_identity(self):
        data={'doc':'f.md','versions':[{'id':'v1','text':'Слово слово.'}],'annotations':[]}
        report={'summary':'Проверено','findings':[{'quote':'Слово слово.','occurrence':0,'class':'повтор','severity':'P2','why':'Повтор',
            'variants':['Слово.'],'rule_id':'duplicate-word','rule_title':'Повтор слова подряд'}]}
        add_analysis(data,'v1',report,'fixture')
        self.assertEqual(data['annotations'][0]['rule_id'],'duplicate-word')
