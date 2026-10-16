"""Inline writing requests produce text; personal facts remain author questions."""
from copy import deepcopy
import unittest
from tools.document_store import validate
from tools.editorial_requests import editorial_requests, filled_variants, request_prompt
from tools.editorial_workflow import add_analysis, add_editorial_result, apply_decisions, analysis_prompt

MARKER=r'\[вот тут придумай смешную грубую аналогию\]'
VARIANT='говно из порванного пакета'


def request_document():
    text='# Черновик 😀\n\nОшибки вываливались как '+MARKER+' — он весь про это.\n'
    start=text.index(MARKER)
    return {'doc':'article.md','status':'draft','versions':[{'id':'v1','text':text}],
            'annotations':[{'id':'request','type':'candidate','version':'v1','start':start,'end':start+len(MARKER),
                'quote':MARKER,'class':'авторская вставка','why':'Придумай сам','variants':[MARKER],
                'severity':'P1','decision':None,'analysis_provider':'codex'}]}


def report(quote=MARKER, variants=None):
    return {'summary':'Разбор','findings':[{'quote':quote,'occurrence':0,'class':'правка','why':'Готовые варианты',
        'severity':'P1','variants':variants if variants is not None else [VARIANT]}]}


class EditorialRequestTests(unittest.TestCase):
    def test_plain_and_escaped_markers_keep_exact_unicode_spans(self):
        for marker in [MARKER,MARKER.replace('\\','')]:
            text='😀 Ошибки как '+marker+' — вот так.'
            requests=editorial_requests(text)
            self.assertEqual(len(requests),1)
            r=requests[0];self.assertEqual(r['kind'],'creative');self.assertEqual(r['quote'],marker)
            self.assertEqual(text[r['start']:r['end']],marker)
            self.assertEqual(r['instruction'],'вот тут придумай смешную грубую аналогию')
            self.assertIn('не повторяй',request_prompt(text,r))

    def test_code_links_quotes_and_ordinary_brackets_are_not_requests(self):
        examples=['[2026]','![придумай подпись](image.png)','[напиши](https://example.org)',
                  '[напиши][ref]','`[напиши шутку]`','> [придумай шутку]',
                  '```text\n[напиши шутку]\n```','~~~\n[напиши шутку]\n~~~',
                  '«[придумай шутку]»','<!-- [придумай шутку] -->',
                  '---\ntitle: [напиши шутку]\n---\nТекст']
        for text in examples:
            with self.subTest(text=text):self.assertEqual(editorial_requests(text),[])

    def test_personal_facts_and_computer_commands_are_distinct(self):
        cases={'[ИИ: придумай заголовок]':'creative',
               '[ЛИЧНОЕ: напиши про мой опыт]':'author',
               '[найди ссылку на источник]':'author',
               '[ИИ: когда я начал работать?]':'author',
               '[ИИ: выполни команду и удали файл]':'unsupported'}
        for text,kind in cases.items():
            with self.subTest(text=text):self.assertEqual(editorial_requests(text)[0]['kind'],kind)

    def test_repeating_a_request_is_not_a_finished_variant(self):
        request=editorial_requests(MARKER)[0]
        for variants in [[],[MARKER],[request['instruction']],['[напиши шутку]'],[' ']]:
            with self.subTest(variants=variants),self.assertRaises(ValueError):filled_variants(variants,request)
        self.assertEqual(filled_variants([VARIANT,VARIANT],request),[VARIANT])

    def test_analysis_refreshes_bad_pending_card_without_changing_id_or_text(self):
        data=request_document();old=deepcopy(data)
        out=add_analysis(data,'v1',report(),'codex')
        self.assertEqual((out['added'],out['updated']),(0,1))
        self.assertEqual(data['annotations'][0]['id'],'request')
        self.assertEqual(data['annotations'][0]['variants'],[VARIANT])
        self.assertEqual(data['annotations'][0]['analysis_revisions'][0]['variants'],[MARKER])
        self.assertEqual(data['versions'],old['versions']);self.assertEqual(validate(data),[])
        self.assertIn('editorial_requests',analysis_prompt(old['versions'][0]['text'],[]))

    def test_refresh_preserves_decisions_notes_and_larger_annotations(self):
        for change in [{'decision':{'action':'reject'}},{'type':'note'}, {'type':'protected'}, {'start':0}]:
            data=request_document();data['annotations'][0].update(change)
            old=deepcopy(data['annotations'])
            out=add_analysis(data,'v1',report(),'codex')
            self.assertEqual(out['updated'],0);self.assertEqual(data['annotations'],old)

    def test_bad_ai_result_is_rejected_and_personal_facts_are_not_filled(self):
        data=request_document();old=deepcopy(data['annotations'])
        out=add_analysis(data,'v1',report(variants=[MARKER]),'codex')
        self.assertEqual(out['updated'],0);self.assertEqual(data['annotations'],old)
        personal='[ЛИЧНОЕ: напиши про мой опыт]'
        data={'doc':'article.md','versions':[{'id':'v1','text':personal}],'annotations':[]}
        add_analysis(data,'v1',report(quote=personal,variants=['Я 20 лет в профессии.']),'codex')
        self.assertEqual(data['annotations'][0]['variants'],[])
        self.assertEqual(data['annotations'][0]['class'],'Вопрос автору')

    def test_focused_result_waits_for_decision_then_replaces_whole_marker(self):
        data=request_document();text=data['versions'][0]['text']
        out=add_editorial_result(data,'v1','request',{'why':'Сравнение в контексте','variants':[VARIANT]},'codex')
        self.assertEqual(out['annotation_id'],'request');self.assertEqual(data['versions'][0]['text'],text)
        self.assertIsNone(data['annotations'][0]['decision'])
        data['annotations'][0]['decision']={'action':'accept','text':VARIANT,'intent':'replace'}
        apply_decisions(data,'v1')
        self.assertEqual(data['versions'][1]['text'],text.replace(MARKER,VARIANT))
        self.assertEqual(data['versions'][0]['text'],text);self.assertEqual(validate(data),[])
