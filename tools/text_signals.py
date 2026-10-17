"""Cheap, source-addressed clues for a reader, never automatic style verdicts."""
from collections import defaultdict
import re


STOP = set('это этой этого этот такие такой также чтобы когда который которая которые можно нужно надо будет быть есть текст текста статье статьи'.split())
WORDS = re.compile(r'[А-Яа-яЁёA-Za-z]+')


def signals(text):
    from .iimeter import strip_markup, FUNCTION_WORDS
    stop = STOP | set(FUNCTION_WORDS)
    clean = strip_markup(text)
    blocks = []; sentences = []; clues = []; quote_budget = 12000; budget_limited = False

    def anchor(start, end):
        quote = text[start:end]; occurrence = 0; previous = -1
        while True:
            previous = text.find(quote, previous+1)
            if previous < 0 or previous >= start: break
            occurrence += 1
        return {'quote': quote, 'occurrence': occurrence, 'start': start, 'end': end}

    def add(kind, why, items):
        nonlocal quote_budget, budget_limited
        if len(clues) >= 14 or quote_budget < 200:
            budget_limited = True; return
        items = items[:6]
        per_quote = min(1200, quote_budget//max(1, len(items)))
        anchors = [anchor(i['start'], min(i['end'], i['start']+per_quote)) for i in items]
        if len(anchors) >= 2:
            clues.append({'id': 's'+str(len(clues)+1), 'kind': kind, 'why_check': why, 'anchors': anchors})
            quote_budget -= sum(len(a['quote']) for a in anchors)

    for match in re.finditer(r'\S[\s\S]*?(?=\r?\n[ \t]*\r?\n|\Z)', clean):
        value = match.group()
        # Quoted examples and Markdown headings are not the author's prose here.
        if re.match(r'^(?:#{1,6}\s|>|[-*+]\s|\d+[.)]\s)', value):
            blocks.append(None); continue
        words = [w.casefold().replace('ё', 'е') for w in WORDS.findall(value)]
        if len(words) < 4: blocks.append(None); continue
        end = match.start()+len(value.rstrip())
        block = {'start': match.start(), 'end': end, 'words': words, 'sentences': []}
        blocks.append(block)
        for sentence in re.finditer(r'[^.!?…]+(?:[.!?…]+|$)', value):
            raw = sentence.group(); start = match.start()+sentence.start()+len(raw)-len(raw.lstrip())
            finish = match.start()+sentence.end()-len(raw)+len(raw.rstrip())
            tokens = [w.casefold().replace('ё', 'е') for w in WORDS.findall(raw)]
            if len(tokens) < 5 or finish-start > 1000: continue
            item = {'start': start, 'end': finish, 'block': len(blocks)-1,
                    'tokens': {w for w in tokens if len(w) >= 4 and w not in stop}}
            sentences.append(item); block['sentences'].append(item)

    # Bounded lexical comparison; semantic paraphrases may share few words.
    pairs = []
    for i, left in enumerate(sentences[:400]):
        for right in sentences[i+1:400]:
            if left['block'] == right['block']: continue
            shared = left['tokens'] & right['tokens']; union = left['tokens'] | right['tokens']
            similarity = len(shared)/len(union) if union else 0
            if len(shared) >= 4 and similarity >= .5: pairs.append((similarity, left, right))
    used = set()
    for _, left, right in sorted(pairs, key=lambda p: (-p[0], p[1]['start'], p[2]['start'])):
        pair = (left['block'], right['block'])
        if pair in used: continue
        used.add(pair)
        add('lexical_overlap', 'Похожие содержательные слова. Проверь: повторена ли мысль или добавлена новая деталь?', [left, right])
        if len(used) >= 6: break

    openings = defaultdict(list)
    for block in blocks:
        if block: openings[tuple(block['words'][:2])].append(block)
    for opening, repeated in sorted(openings.items()):
        if len(repeated) >= 3:
            add('paragraph_opening', 'Несколько абзацев начинаются одинаково. Это может быть намеренным перечислением; оцени по жанру.', repeated)
            if sum(c['kind'] == 'paragraph_opening' for c in clues) >= 3: break

    caveats = []
    for block in blocks:
        if not block: continue
        item = next((s for s in block['sentences'] if re.search(
            r'\b(?:не|без|сам|сама|самому|самостоятельно|по-прежнему|все равно)\b',
            clean[s['start']:s['end']].casefold().replace('ё', 'е'))), None)
        if item: caveats.append(item)
    if len(caveats) >= 3:
        chosen = [caveats[round(i*(len(caveats)-1)/min(5, len(caveats)-1))] for i in range(min(6, len(caveats)))]
        add('caveat_cluster', 'Оговорки в разных частях. Сопоставь их смысл: нужные границы разных функций могут быть уместны; повтор заверений — нет.', chosen)

    run = []
    for block in [*blocks, None]:
        if block and 6 <= len(block['words']) <= 45 and 1 <= len(block['sentences']) <= 2:
            run.append(block)
        else:
            if len(run) >= 4:
                add('short_paragraph_run', 'Серия коротких законченных блоков. Проверь развитие мысли и переходы; длина сама по себе не ошибка.', run)
            run = []
    return {'notice': 'Локальные лексические и структурные подсказки, а не диагноз и не признак авторства. '
                      'Не превращай их в замечания без смыслового основания. Отсутствие подсказок ничего не доказывает.',
            'sentences_checked_for_overlap': min(400, len(sentences)), 'overlap_limited': len(sentences) > 400,
            'clue_budget_limited': budget_limited,
            'clues': clues[:14]}
