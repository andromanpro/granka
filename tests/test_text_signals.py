import unittest
from tools.text_signals import signals


class TextSignalTests(unittest.TestCase):
    def check_anchors(self, text, result):
        for clue in result['clues']:
            for anchor in clue['anchors']:
                self.assertEqual(text[anchor['start']:anchor['end']], anchor['quote'])

    def test_code_quotes_headings_and_urls_do_not_create_repeat_clues(self):
        text='# Заголовок\n\n```\nНе забывайте проверять каждое новое решение редактора.\nНе забывайте проверять каждое новое решение редактора.\n```\n\n> Не забывайте проверять каждое новое решение редактора.\n\nОбычный абзац про запись изменений в журнал.\n'
        result=signals(text);self.assertEqual(result['clues'],[]);self.check_anchors(text,result)

    def test_lexical_pairs_keep_exact_markdown_and_unicode_offsets(self):
        text='# Пример 😀\n\nРедактор сохраняет **каждое** решение автора в отдельном журнале.\n\nРедактор сохраняет каждое решение автора в отдельном журнале проекта.\n'
        result=signals(text);pairs=[c for c in result['clues'] if c['kind']=='lexical_overlap']
        self.assertEqual(len(pairs),1);self.assertIn('**каждое**',pairs[0]['anchors'][0]['quote'])
        self.check_anchors(text,result)

    def test_caveats_and_repeated_openings_remain_clues_without_severity(self):
        text='Можно выбрать нужный вариант в карточке, без новой настройки.\n\nМожно выбрать другой файл в библиотеке, но не отправлять его.\n\nМожно выбрать удобную дату, публикация сама не начнётся.\n'
        result=signals(text);kinds={c['kind'] for c in result['clues']}
        self.assertTrue({'caveat_cluster','paragraph_opening'}<=kinds)
        self.assertTrue(all('severity' not in c for c in result['clues']))
        self.check_anchors(text,result)

    def test_empty_and_short_text_have_no_artificial_clues(self):
        for text in ['', '# Заголовок', 'Да.\n\nНет.']:
            self.assertEqual(signals(text)['clues'],[])

    def test_long_text_has_deterministic_bounded_prompt_clues(self):
        text='\n\n'.join(f'Можно выбрать нужный вариант из списка и сохранить решение в журнале без повторной настройки проекта номер {i}.' for i in range(800))
        first=signals(text);second=signals(text)
        self.assertEqual(first,second)
        self.assertTrue(first['overlap_limited']);self.assertLessEqual(len(first['clues']),14)
        self.assertLessEqual(sum(len(a['quote']) for c in first['clues'] for a in c['anchors']),12000)
        self.check_anchors(text,first)


if __name__=='__main__': unittest.main()
