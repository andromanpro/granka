"""Language policy is independent of UI locale and never translates source text."""
import re


def detect(text):
    prose = re.sub(r'```[\s\S]*?```|`[^`]*`|https?://\S+', '', text)
    ru = len(re.findall(r'[А-Яа-яЁё]', prose))
    en = len(re.findall(r'[A-Za-z]', prose))
    if not ru and not en:return 'unknown'
    if ru / (ru + en) >= .8:return 'ru'
    if en / (ru + en) >= .8:return 'en'
    return 'mixed'


def selected(settings, source=''):
    material = settings.get('material_language', 'auto')
    comments = settings.get('comments_language', 'ru')
    if not isinstance(material,str)or not isinstance(comments,str)or material not in {'auto', 'ru', 'en'} or comments not in {'ru', 'en'}:
        raise ValueError('Выбери язык материала и комментариев ИИ')
    detected = detect(source)
    return {'material_language': material,
            'resolved_material_language': (detected if detected in {'ru', 'en', 'mixed'} else 'ru') if material == 'auto' else material,
            'comments_language': comments}


def instruction(settings):
    resolved = settings.get('resolved_material_language', settings.get('material_language', 'auto'))
    material = {'ru': 'Russian', 'en': 'English', 'auto': 'the original language',
                'mixed': 'the original language of each passage'}[resolved]
    comments = 'English' if settings.get('comments_language') == 'en' else 'Russian'
    return ('LANGUAGE POLICY (applies to every output field): Write new article prose, replacements, '
            f'creative answers, and Telegram post variants in {material}. '
            f'Write editorial explanations, notes, diagnoses, labels and reasoning in {comments}. '
            'Preserve quotes verbatim, including spelling, punctuation and Unicode positions. '
            'Never translate the article or its quoted evidence unless translation is the explicit task. '
            'Keep JSON property names, enum values, identifiers and schema unchanged. '
            'This policy overrides any generic Russian-language role description below.\n')
