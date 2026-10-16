from copy import deepcopy
import unittest
from tools.batch_decisions import decide_batch,undo_batch
from tools.editorial_workflow import apply_decisions


def fixture():
    text='Первый первый. Второй второй.';annotations=[]
    for i,quote in enumerate(['Первый первый.','Второй второй.']):
        start=text.index(quote);annotations.append({'id':str(i),'type':'candidate','version':'v1','start':start,'end':start+len(quote),
          'quote':quote,'rule_id':'duplicate-word','rule_title':'Повтор слова подряд','variants':[quote.split()[0]+'.'],'decision':None})
    return {'doc':'f.md','versions':[{'id':'v1','text':text}],'annotations':annotations}


class BatchDecisionTests(unittest.TestCase):
    def test_batch_saves_decisions_then_apply_changes_text(self):
        data=fixture();old=deepcopy(data['versions'])
        out=decide_batch(data,'v1','duplicate-word',[{'id':str(i),'action':'accept','variant':0} for i in range(2)],'batch')
        self.assertEqual(out['count'],2);self.assertEqual(data['versions'],old)
        apply_decisions(data,'v1');self.assertEqual(data['versions'][-1]['text'],'Первый. Второй.')

    def test_invalid_or_already_decided_member_prevents_entire_batch(self):
        for change in [{'rule_id':'different'},{'type':'protected'},{'decision':{'action':'reject'}},{'editorial_request':{'kind':'author'}}]:
            data=fixture();data['annotations'][1].update(change);old=deepcopy(data)
            with self.subTest(change=change),self.assertRaises(ValueError):
                decide_batch(data,'v1','duplicate-word',[{'id':'0','action':'accept','variant':0},{'id':'1','action':'accept','variant':0}],'batch')
            self.assertEqual(data,old)

    def test_batch_undo_preserves_later_individual_changes(self):
        data=fixture();items=[{'id':str(i),'action':'reject','reason':'voice'} for i in range(2)]
        decide_batch(data,'v1','duplicate-word',items,'batch');undo_batch(data,'v1','batch')
        self.assertTrue(all(a['decision'] is None for a in data['annotations']))
        decide_batch(data,'v1','duplicate-word',items,'batch2');data['annotations'][1]['decision']={'action':'reject','reason':'worse'}
        old=deepcopy(data)
        with self.assertRaises(ValueError):undo_batch(data,'v1','batch2')
        self.assertEqual(data,old)
