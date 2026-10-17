from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tools import deep_analysis as deep
from tools.document_store import validate
from tools.editorial_workflow import apply_decisions
from tools.text_edit import edit_text


TEXT = '# Редактор 😀\n\nСначала проверяю результат.\n\nПотом проверяю результат.\n\nСкобочки оставлю)))\n'


def document():
    return {'doc': 'review.md', 'versions': [{'id': 'v1', 'text': TEXT}], 'annotations': []}


def anchor(quote, role='revise', occurrence=0):
    return {'quote': quote, 'occurrence': occurrence, 'role': role}


def finding(**values):
    return {'title': 'Оговорка повторяется', 'category': 'repetition', 'severity': 'P2', 'confidence': 'medium',
            'anchors': [anchor('Сначала проверяю результат.'), anchor('Потом проверяю результат.')],
            'why': 'Оба места обещают одну проверку без нового действия.',
            'suggestion': 'Объединить оговорки или пояснить различие проверок.',
            'counterargument': 'Возможно, речь о разных этапах.', 'rule_id': '', **values}


def coverage():
    return {key: {'status': 'issue' if key == 'repetition' else 'clear', 'reason': 'Проверены связи и контекст.'} for key in deep.CATEGORIES}


def responses():
    return [
        {'thesis': 'Личная проверка редактора.', 'reader_task': 'Понять ход проверки.',
         'outline': [{'quote': 'Сначала проверяю результат.', 'occurrence': 0, 'role': 'Проверка', 'point': 'Первый этап.'}],
         'trajectory': 'Два этапа и авторское отступление.'},
        {'summary': 'Есть повтор.', 'coverage': coverage(), 'findings': [finding()]},
        {'summary': 'После спора осталось одно замечание.', 'coverage': coverage(),
         'verdicts': [{'candidate_id': 'd1', 'verdict': 'keep', 'reason': 'В тексте различие этапов не раскрыто.', 'finding': finding()}]},
    ]


def run_fixture(directory, data=None, replies=None, preferences=None):
    replies = responses() if replies is None else replies
    provider = Mock(side_effect=[(r, {'provider': 'codex', 'usage': {'tokens': 1}}) for r in replies])
    report = deep.run(data or document(), 'v1', preferences or {'rules': [], 'decisions': []}, [],
                      directory, {'analysis_provider': 'codex'}, provider)
    return report, provider


class DeepAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_three_passes_keep_text_and_store_exact_unicode_anchors(self):
        data = document(); original = deepcopy(data)
        report, provider = run_fixture(self.directory, data)
        self.assertEqual(provider.call_count, 3)
        self.assertEqual([c.args[2].name for c in provider.call_args_list], ['map', 'diagnose', 'verify'])
        self.assertIn('Скобочки оставлю)))', provider.call_args_list[2].args[0])
        self.assertIn('counterargument', provider.call_args_list[2].args[0])
        deep.add_report(data, 'v1', report)
        self.assertEqual(data['versions'], original['versions'])
        self.assertEqual(data['annotations'], [])
        self.assertEqual(validate(data), [])
        for a in report['findings'][0]['anchors']:
            self.assertEqual(TEXT[a['start']:a['end']], a['quote'])
        self.assertEqual(json.loads((self.directory/'deep-report.json').read_text(encoding='utf-8')), report)
        self.assertEqual(len(json.loads((self.directory/'stage-receipts.json').read_text(encoding='utf-8'))), 3)

    def test_verifier_can_disagree_and_drop_everything(self):
        replies = responses()
        replies[2]['verdicts'][0].update(verdict='drop', reason='Этапы различаются по назначению.', finding=None)
        report, _ = run_fixture(self.directory, replies=replies)
        self.assertEqual(report['findings'], [])
        self.assertEqual(report['challenges'][0]['verdict'], 'drop')
        self.assertEqual(report['coverage']['repetition']['status'], 'uncertain')

    def test_empty_diagnosis_does_not_spend_a_third_request(self):
        replies = responses()[:2]; replies[1]['findings'] = []
        report, provider = run_fixture(self.directory, replies=replies)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(report['findings'], [])

    def test_missing_or_duplicate_verdict_is_not_accepted(self):
        for verdicts in [[], [responses()[2]['verdicts'][0]]*2]:
            with self.subTest(verdicts=verdicts):
                replies = responses(); replies[2]['verdicts'] = verdicts
                with self.assertRaisesRegex(ValueError, 'пропустила или повторила'):
                    run_fixture(self.directory, replies=replies)

    def test_bad_quote_is_visible_and_never_becomes_an_anchor(self):
        replies = responses(); replies[1]['findings'][0]['anchors'][0]['quote'] = 'Вымышленная цитата'
        report, provider = run_fixture(self.directory, replies=replies)
        self.assertEqual(report['findings'], [])
        self.assertIn('не найдена', report['skipped'][0]['reason'])
        self.assertEqual(provider.call_count, 2)

    def test_protection_is_checked_both_before_and_after_verification(self):
        for stage in [1, 2]:
            with self.subTest(stage=stage):
                data = document(); quote = 'Скобочки оставлю)))'; start = TEXT.index(quote)
                data['annotations'] = [{'id': 'p', 'version': 'v1', 'type': 'protected', 'start': start,
                                        'end': start+len(quote), 'quote': quote}]
                replies = responses()
                f = replies[1]['findings'][0] if stage == 1 else replies[2]['verdicts'][0]['finding']
                f['anchors'][0] = anchor(quote)
                report, _ = run_fixture(self.directory, data, replies)
                self.assertEqual(report['findings'], [])
                self.assertIn('защищённого', report['skipped'][0]['reason'])

    def test_protected_text_is_allowed_as_context_only(self):
        quote = 'Сначала проверяю результат.'; start = TEXT.index(quote)
        data = document(); data['annotations'] = [{'id': 'p', 'version': 'v1', 'type': 'protected',
            'start': start, 'end': start+len(quote), 'quote': quote}]
        replies = responses()
        for f in [replies[1]['findings'][0], replies[2]['verdicts'][0]['finding']]: f['anchors'][0]['role'] = 'context'
        report, _ = run_fixture(self.directory, data, replies)
        self.assertEqual(len(report['findings']), 1)

    def test_repeated_quote_occurrence_and_single_place_repetition(self):
        located = deep.locate(TEXT, anchor('проверяю результат.', occurrence=1))
        self.assertEqual(located['start'], TEXT.rindex('проверяю результат.'))
        for value in [True, -1, '1', 99999]:
            with self.subTest(occurrence=value), self.assertRaises(ValueError): deep.locate(TEXT, anchor('проверяю', occurrence=value))
        with self.assertRaisesRegex(ValueError, 'двух разных мест'):
            deep.normalize_finding(TEXT, finding(anchors=[anchor('Сначала')]), [], set())

    def test_personal_rule_requires_a_real_rule_identity(self):
        with self.assertRaisesRegex(ValueError, 'личное правило'):
            deep.normalize_finding(TEXT, finding(category='personal_rules', rule_id='made-up'), [], {'real-rule'})
        good = deep.normalize_finding(TEXT, finding(category='personal_rules', rule_id='real-rule'), [], {'real-rule'})
        self.assertEqual(good['rule_id'], 'real-rule')

    def test_every_axis_must_be_checked_and_no_silent_length_truncation(self):
        replies = responses(); del replies[1]['coverage']['logic']
        with self.assertRaisesRegex(ValueError, 'не все направления'): run_fixture(self.directory, replies=replies)
        data = document(); data['versions'][0]['text'] = 'а'*120001
        provider = Mock()
        with self.assertRaisesRegex(ValueError, '120000'):
            deep.run(data, 'v1', {}, [], self.directory, {'analysis_provider': 'codex'}, provider)
        provider.assert_not_called()

    def test_map_filters_bad_quotes_and_repairs_order_with_visible_warning(self):
        replies=responses();first=replies[0]['outline'][0]
        replies[0]['outline']=[{**first,'quote':'Потом проверяю результат.'},{**first,'quote':'Вымышленный блок'},first]
        report,_=run_fixture(self.directory,replies=replies)
        self.assertEqual([b['quote'] for b in report['map']['outline']],['Сначала проверяю результат.','Потом проверяю результат.'])
        self.assertEqual([s['candidate_id'] for s in report['skipped']],['map2','map-order'])
        replies=responses();replies[0]['outline'][0]['quote']='Нет такого блока'
        with self.assertRaisesRegex(ValueError,'не осталось блоков'):run_fixture(self.directory/'invalid',replies=replies)

    def test_overlong_descriptions_are_bounded_visibly_but_ids_stay_strict(self):
        replies=responses();replies[0]['outline'][0]['point']='а'*1501
        replies[2]['summary']='б'*5001;replies[2]['verdicts'][0]['reason']='в'*3001
        report,_=run_fixture(self.directory,replies=replies)
        self.assertLessEqual(len(report['summary']),5000)
        self.assertEqual(len(report['skipped']),3)
        self.assertTrue(all('сокращено' in s['reason'] for s in report['skipped']))

    def test_signals_are_advisory_and_reach_both_reader_passes(self):
        data=document();data['versions'][0]['text']=TEXT+'\nБез настройки это работать не будет.\n\nЯ сам проверяю каждое решение в редакторе.\n\nБез просмотра истории не получается восстановить текст.\n'
        report,provider=run_fixture(self.directory,data)
        self.assertTrue(report['local_signals']['clues'])
        self.assertNotIn('local_signals',provider.call_args_list[0].args[0])
        self.assertIn('local_signals',provider.call_args_list[1].args[0]);self.assertIn('local_signals',provider.call_args_list[2].args[0])
        deep.add_report(data,'v1',report);self.assertEqual(validate(data),[])
        data['deep_analyses'][0]['local_signals']['clues'][0]['anchors'][0]['end']+=1
        self.assertTrue(validate(data))

    def test_failed_stage_does_not_retry_or_write_a_partial_report(self):
        provider = Mock(side_effect=[(responses()[0], {'provider': 'codex'}), RuntimeError('отказ')])
        data = document(); before = deepcopy(data)
        with self.assertRaisesRegex(RuntimeError, 'отказ'):
            deep.run(data, 'v1', {}, [], self.directory, {'analysis_provider': 'codex'}, provider)
        self.assertEqual(provider.call_count, 2); self.assertEqual(data, before)
        self.assertTrue((self.directory/'map-validated.json').is_file())
        self.assertFalse((self.directory/'deep-report.json').exists())

    def test_review_decisions_never_become_text_replacements_and_feed_next_run(self):
        data = document(); report, _ = run_fixture(self.directory)
        deep.add_report(data, 'v1', report); f = report['findings'][0]
        deep.decide(data, report['id'], f['id'], {'action': 'dismiss', 'note': 'Это намеренные этапы.'})
        result = apply_decisions(data, 'v1')
        self.assertTrue(result['unchanged']); self.assertEqual(data['versions'][0]['text'], TEXT)
        _, provider = run_fixture(self.directory/'next', data)
        self.assertIn('Это намеренные этапы.', provider.call_args_list[0].args[0])
        deep.decide(data, report['id'], f['id'], None)
        self.assertIsNone(f['decision'])

    def test_new_version_retains_old_report_without_reusing_old_positions(self):
        data = document(); report, _ = run_fixture(self.directory); deep.add_report(data, 'v1', report)
        edit_text(data, 'v1', TEXT+'\nНовый финал.\n')
        self.assertEqual(validate(data), [])
        self.assertEqual(data['deep_analyses'][0]['version'], 'v1')
        with self.assertRaisesRegex(ValueError, 'только для чтения'):
            deep.decide(data, report['id'], report['findings'][0]['id'], {'action': 'consider'})
        with self.assertRaises(ValueError): deep.add_report(data, 'v2', report)

    def test_persisted_anchors_hash_and_ids_are_validated(self):
        data = document(); report, _ = run_fixture(self.directory); deep.add_report(data, 'v1', report)
        for corrupt in ['offset', 'hash', 'duplicate']:
            broken = deepcopy(data)
            if corrupt == 'offset': broken['deep_analyses'][0]['findings'][0]['anchors'][0]['start'] += 1
            elif corrupt == 'hash': broken['deep_analyses'][0]['source_sha256'] = 'bad'
            else: broken['deep_analyses'].append(deepcopy(report))
            self.assertTrue(validate(broken), corrupt)


if __name__ == '__main__': unittest.main()
