import json
from pathlib import Path
import tempfile
import unittest
from tools.author_preferences import AuthorPreferences
from test_author_preferences import fixture


class RuleLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.file=self.root/'article.granka.json'
        self.data=fixture();self.file.write_text(json.dumps(self.data),encoding='utf-8')
        self.rules=self.root/'rules.md';self.rules.write_text('## Простые слова\nПредпочитай простые слова, кроме терминов.\n',encoding='utf-8')
        self.preferences=AuthorPreferences(lambda:self.root/'state',lambda:{'articles':self.root},lambda:self.rules)
        self.learning=self.preferences.learning
    def tearDown(self):self.temp.cleanup()
    def propose(self,replaces=''):
        source,prompt=self.learning.proposal_input();evidence=source['decisions'][0]['id']
        result={'proposals':[{'title':'Конкретные слова','instruction':'Выбирай конкретную формулировку, сохраняя точные термины.',
                             'rationale':'Основано на авторской замене.','replaces':replaces,'evidence_ids':[evidence]}]}
        self.learning.save_proposals(source,result,{},'test')
        return self.learning.proposals()[0]

    def test_proposal_is_inactive_until_author_accepts_edited_text(self):
        old=self.rules.read_bytes();proposal=self.propose()
        self.assertEqual(len(self.preferences.rules()),1)
        revision=self.learning.catalog()['revision']
        self.learning.decide(proposal['id'],'accepted',revision,title='Мой заголовок',instruction='Моя формулировка правила.')
        active=self.preferences.context('Текст')['rules']
        self.assertEqual(active[-1]['instruction'],'Моя формулировка правила.')
        self.assertEqual(active[-1]['source'],'author-approved');self.assertEqual(self.rules.read_bytes(),old)
        self.assertTrue(self.learning.decide(proposal['id'],'accepted',revision)['idempotent'])

    def test_replacement_and_disable_restore_original_rule(self):
        original=self.preferences.rules()[0];proposal=self.propose(original['id'])
        self.learning.decide(proposal['id'],'accepted',self.learning.catalog()['revision'])
        active=self.preferences.rules();self.assertEqual(len(active),1);self.assertNotEqual(active[0]['id'],original['id'])
        self.learning.toggle(active[0]['id'],False,self.learning.catalog()['revision'])
        self.assertEqual([r['id'] for r in self.preferences.rules()],[original['id']])

    def test_rejected_proposal_does_not_become_rule_and_is_seen_by_next_generator(self):
        proposal=self.propose();self.learning.decide(proposal['id'],'rejected',self.learning.catalog()['revision'])
        self.assertEqual(len(self.preferences.rules()),1)
        source,prompt=self.learning.proposal_input();self.assertEqual(source['dismissed'][0]['title'],proposal['title'])
        self.assertNotIn(str(self.root),prompt)

    def test_stale_revision_and_changed_evidence_prevent_acceptance(self):
        proposal=self.propose()
        with self.assertRaises(ValueError):self.learning.decide(proposal['id'],'accepted','old')
        self.data['annotations'][-1]['decision']=None;self.file.write_text(json.dumps(self.data),encoding='utf-8')
        with self.assertRaises(ValueError):self.learning.decide(proposal['id'],'accepted',self.learning.catalog()['revision'])
        self.assertEqual(self.learning.proposals()[0]['status'],'pending')

    def test_fabricated_evidence_is_rejected_before_saving(self):
        source,_=self.learning.proposal_input()
        with self.assertRaises(ValueError):self.learning.save_proposals(source,{'proposals':[{'title':'Wrong','instruction':'Rule','rationale':'Why','replaces':'','evidence_ids':['invented']}]},{},'bad')
        self.assertEqual(self.learning.proposals(),[])

    def test_editing_base_file_invalidates_its_learned_replacement(self):
        proposal=self.propose(self.preferences.rules()[0]['id'])
        self.learning.decide(proposal['id'],'accepted',self.learning.catalog()['revision'])
        self.rules.write_text('## Другой совет\nСохраняй терминологию.\n',encoding='utf-8')
        catalog=self.learning.catalog()
        self.assertEqual(catalog['active'][0]['title'],'Другой совет')
        self.assertEqual(catalog['inactive'][0]['state'],'stale')
