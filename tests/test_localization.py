import json
from html.parser import HTMLParser
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from tools import languages, ai_provider, iimeter, article_draft

ROOT=Path(__file__).resolve().parents[1]


class LocalizationTests(unittest.TestCase):
    def test_html_ids_are_unique_so_new_controls_cannot_hijack_old_panels(self):
        identifiers=[]
        class Identifiers(HTMLParser):
            def handle_starttag(self,tag,attrs):
                identifiers.extend(value for name,value in attrs if name=='id')
        Identifiers().feed((ROOT/'static/index.html').read_text(encoding='utf-8'))
        self.assertEqual(len(identifiers),len(set(identifiers)),[v for v in set(identifiers)if identifiers.count(v)>1])

    def test_catalogs_complete_and_parameters_preserved(self):
        ru=json.loads((ROOT/'static/locales/ru.json').read_text(encoding='utf-8'))
        en=json.loads((ROOT/'static/locales/en.json').read_text(encoding='utf-8'))
        self.assertEqual(set(ru),set(en));self.assertGreater(len(ru),1000)
        for key in ru:
            with self.subTest(key=key):
                self.assertTrue(en[key].strip())
                self.assertEqual(sorted(re.findall(r'\{\d+\}',ru[key])),sorted(re.findall(r'\{\d+\}',en[key])))
                self.assertFalse(re.search('[А-Яа-яЁё]',en[key]))
        for source in (ROOT/'static/ui').glob('*.js'):
            for key in re.findall(r'\bt\("([^"]+)"',source.read_text(encoding='utf-8')):self.assertIn(key,ru,source)
        for key in re.findall(r'i18n:([\w.]+)|data-i18n-[\w-]+="([\w.]+)"',(ROOT/'static/index.html').read_text(encoding='utf-8')):
            self.assertIn(key[0]or key[1],ru)

    def test_detection_ignores_code_and_urls(self):
        self.assertEqual(languages.detect('This article describes a local editor.'),'en')
        self.assertEqual(languages.detect('Текст статьи о редакторе. `const veryLongVariableName = true`'),'ru')
        self.assertEqual(languages.detect('Текст текста и статьи. This is an English passage.'),'mixed')
        self.assertEqual(languages.detect('```js\nconsole.log("x");\n```'),'unknown')

    def test_language_choices_are_independent_and_validated(self):
        for material in ('auto','ru','en'):
            for comments in ('ru','en'):
                value=languages.selected({'material_language':material,'comments_language':comments},'An English article')
                self.assertEqual(value['comments_language'],comments)
                self.assertEqual(value['resolved_material_language'],'en'if material=='auto'else material)
        for values in ({'material_language':'de'},{'comments_language':'auto'},{'material_language':['en']}):
            with self.assertRaises((ValueError,TypeError)):languages.selected(values)

    def test_settings_save_rejects_invalid_languages_without_writing(self):
        with self.assertRaises(ValueError):ai_provider.validated_settings({'material_language':'de'}, {})
        self.assertEqual(ai_provider.validated_settings({'material_language':'en','comments_language':'ru'}, {})['material_language'],'en')

    def test_prompt_and_receipt_record_language_without_changing_quotes(self):
        quote='Точная цитата — ёж 😀'
        with tempfile.TemporaryDirectory()as td,patch.object(ai_provider,'run_codex',return_value=({'quote':quote},{'provider':'fake'}))as call:
            config={**ai_provider.DEFAULTS,**languages.selected({'material_language':'en','comments_language':'ru'})}
            value,receipt=ai_provider.text_result(quote,{},Path(td),config)
            self.assertEqual(value['quote'],quote)
            self.assertIn('in English',call.call_args.args[0]);self.assertIn('in Russian',call.call_args.args[0])
            self.assertEqual(receipt['languages']['material_language'],'en')
            self.assertEqual(json.loads((Path(td)/'receipt.json').read_text())['languages'],receipt['languages'])

    def test_english_and_mixed_meter_never_report_a_false_zero(self):
        for text in ('An English article about editing and publishing. '*30,
                     'Русская часть статьи. English passage about editing. '*30):
            result=iimeter.measure(text,profile={'sources':['ru.md'],'fingerprint':'ru'})
            self.assertEqual(result['status'],'unsupported_language');self.assertIsNone(result['score'])
            for axis in result['axes'].values():self.assertIsNone(axis['score'])
            self.assertEqual(result['highlights'],[])

    def test_draft_request_captures_explicit_language_choices(self):
        value=article_draft.request({'title':'Local editing','material_language':'en','comments_language':'ru'})
        self.assertEqual(value['resolved_material_language'],'en')
        self.assertEqual(value['comments_language'],'ru')
        self.assertIn('in English',article_draft.prompt(value,{},[]))


if __name__=='__main__':unittest.main()
