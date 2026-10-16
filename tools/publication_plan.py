"""Private editorial calendar. Assigning dates never starts a publication."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
import uuid

from .ai_provider import atomic_json


class PlanConflict(ValueError):
    pass


def day(value, optional=False):
    if optional and value in (None, ''):
        return None
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('Выбери дату')
    return date.fromisoformat(value).isoformat()


def entry(values, identifier):
    if not isinstance(values, dict):
        raise ValueError('Некорректная запись плана')
    result = {'id': identifier}
    for key, limit in [('title', 300), ('notes', 4000), ('root', 80), ('file', 1000), ('url', 2000)]:
        text = values.get(key, '')
        if not isinstance(text, str) or len(text) > limit or '\0' in text:
            raise ValueError('Некорректное поле ' + key)
        result[key] = text.strip()
    if not result['title']:
        raise ValueError('Напиши название материала')
    if result['url'] and (urlsplit(result['url']).scheme not in {'http', 'https'} or not urlsplit(result['url']).netloc):
        raise ValueError('Ссылка должна начинаться с https:// или http://')
    result['channel'] = values.get('channel', 'article')
    result['status'] = values.get('status', 'idea')
    if result['channel'] not in {'article', 'telegram'} or result['status'] not in {'idea', 'draft', 'ready', 'published'}:
        raise ValueError('Неизвестный канал или состояние')
    result['date'] = day(values.get('date'), optional=True)
    result['pinned'] = values.get('pinned') is True
    result['excluded'] = values.get('excluded') is True
    progress = values.get('progress')
    if progress is not None and (type(progress) is not int or not 0 <= progress <= 100):
        raise ValueError('Готовность: число от 0 до 100 или автоматическая оценка')
    result['progress'] = progress
    if result['excluded']:
        result['date'] = None
        result['pinned'] = False
    if result['pinned'] and not result['date']:
        raise ValueError('Для закрепления выбери дату')
    if result['status'] == 'published' and not result['date']:
        raise ValueError('Для опубликованного материала нужна дата')
    return result


def distribute(entries, options):
    if not isinstance(options, dict):
        raise ValueError('Некорректные настройки распределения')
    start = date.fromisoformat(day(options.get('start')))
    weekdays = options.get('weekdays', [0, 2, 4])
    capacity = options.get('capacity', 1)
    channel = options.get('channel', 'all')
    if not isinstance(weekdays, list) or not weekdays or any(type(d) is not int or d not in range(7) for d in weekdays):
        raise ValueError('Выбери хотя бы один день недели')
    if type(capacity) is not int or not 1 <= capacity <= 10 or channel not in {'all', 'article', 'telegram'}:
        raise ValueError('Проверь число публикаций и канал')
    eligible = [e for e in entries if not e.get('readonly') and not e.get('excluded') and not e.get('source_error') and not e.get('date') and not e.get('pinned') and
                e['status'] in ({'draft', 'ready', 'idea'} if options.get('include_ideas') is True else {'draft', 'ready'}) and
                (channel == 'all' or e['channel'] == channel)]
    # Ready materials go first; creation order is retained within each group.
    eligible.sort(key=lambda e: ({'ready': 0, 'draft': 1, 'idea': 2}[e['status']], -e.get('readiness', {}).get('percent', 0)))
    occupied = {}
    for e in entries:
        if e.get('date') and not e.get('excluded'):
            occupied[e['date']] = occupied.get(e['date'], 0) + 1
    result = []
    cursor = start
    for item in eligible:
        while cursor.weekday() not in weekdays or occupied.get(cursor.isoformat(), 0) >= capacity:
            cursor += timedelta(days=1)
            if (cursor - start).days > 730:
                raise ValueError('План не помещается в два года. Увеличь частоту публикаций.')
        target = cursor.isoformat()
        result.append({'id': item['id'], 'title': item['title'], 'channel': item['channel'], 'date': target})
        occupied[target] = occupied.get(target, 0) + 1
    return result


class PublicationPlan:
    def __init__(self, state, library=None):
        self.state = state
        self.library = library or (lambda: [])
        self.lock = threading.RLock()

    @property
    def path(self):
        return Path(self.state()) / 'publication-plan.json'

    def receipts(self):
        """Use only completed production receipts, never previews or attempted sends."""
        latest = {}
        for path in (Path(self.state()) / 'site-publications').glob('*/plan.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            prod = record.get('stages', {}).get('prod', {})
            if prod.get('status') != 'succeeded':
                continue
            key = (record['root'], record['rel'])
            if record['created'] > latest.get(key, {}).get('created', -1):
                latest[key] = record
        results = []
        for record in latest.values():
            prod = record['stages']['prod']
            if prod.get('post_status') != 'publish':
                continue
            meta = record['metadata']
            results.append({'id': 'site-' + record['id'], 'title': meta['title'], 'date': meta['date'],
                            'channel': 'article', 'status': 'published', 'pinned': True, 'readonly': True,
                            'root': record['root'], 'file': record['rel'], 'url': prod['url'],
                            'notes': 'Опубликовано из Гранки · ' + record['version']})
        return results

    def read(self):
        with self.lock:
            saved = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'entries': []}
            entries = deepcopy(saved['entries'])
            library = self.library()
            available = {(s['root'], s['file'], s['channel']) for s in library}
            for value in entries:
                if value.get('origin') == 'library' and (value['root'], value['file'], value['channel']) not in available:
                    value['source_error'] = 'Источник больше не доступен среди черновиков: проверь файл и его статус'
            for source in library:
                linked = next((i for i in entries if i.get('root') == source['root'] and i.get('file') == source['file'] and i['channel'] == source['channel']), None)
                if linked:
                    merged = {**source, **linked}
                    for key in ['origin', 'source_rel', 'has_granka', 'source_revision', 'readiness']:
                        merged[key] = source[key]
                    entries[entries.index(linked)] = merged
                else:
                    entries.append(source)
            for receipt in self.receipts():
                linked = next((i for i in entries if i['channel'] == 'article' and i.get('root') == receipt['root'] and i.get('file') == receipt['file']), None)
                if linked:
                    # Keep its stable calendar ID and private notes.
                    receipt.update(id=linked['id'], notes=linked.get('notes') or receipt['notes'])
                    entries[entries.index(linked)] = receipt
                else:
                    entries.append(receipt)
            for value in entries:
                value.setdefault('excluded', False)
                value.setdefault('progress', None)
                readiness = deepcopy(value.get('readiness') or {'percent': 0 if value['status'] == 'idea' else 25,
                    'basis': 'status', 'checks': [], 'details': ['Готовность можно уточнить в карточке']})
                if value['status'] == 'published':
                    readiness.update(percent=100, basis='published', details=[], checks=[])
                elif value['status'] == 'ready':
                    readiness.update(percent=100, basis='author')
                    if readiness['checks']: readiness['checks'][-1]['done'] = True
                elif value.get('progress') is not None:
                    readiness.update(percent=value['progress'], basis='author')
                value['readiness'] = readiness
            revision = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:24]
            return {'entries': entries, 'revision': revision}

    def checked(self, revision):
        state = self.read()
        if revision != state['revision']:
            raise PlanConflict('План изменился. Обнови окно и повтори действие.')
        return state

    def change(self, body):
        with self.lock:
            state = self.checked(body.get('expected_revision'))
            identifier = body.get('id')
            existing = next((e for e in state['entries'] if e['id'] == identifier), None)
            if identifier and not existing:
                raise LookupError('Запись не найдена')
            if existing and existing.get('readonly'):
                raise ValueError('Это квитанция публикации. Статью можно изменить через «Обновить на сайте».')
            # Persist local items only; receipt projections are rebuilt on read.
            saved = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'entries': []}
            entries = saved['entries']
            action = body.get('action')
            if action == 'delete' and existing and existing.get('origin') == 'library':
                action = 'exclude'
            if action in {'exclude', 'include', 'unschedule'}:
                if not existing: raise LookupError('Запись не найдена')
                value = entry({**existing, 'date': None, 'pinned': False,
                               'excluded': action == 'exclude' if action != 'unschedule' else existing.get('excluded', False)}, identifier)
                if existing.get('origin') == 'library': value['origin'] = 'library'
                entries = [e for e in entries if e['id'] != identifier] + [value]
            elif action == 'delete':
                if not existing:
                    raise LookupError('Запись не найдена')
                entries = [e for e in entries if e['id'] != identifier]
            elif action == 'save':
                value = entry(body.get('entry'), identifier or uuid.uuid4().hex)
                if existing and existing.get('origin') == 'library':
                    if any(value[k] != existing[k] for k in ['root', 'file', 'channel']):
                        raise ValueError('Связь с найденным черновиком сохраняется автоматически')
                    value['origin'] = 'library'
                if not identifier and len(entries) >= 1000:
                    raise ValueError('В плане уже 1000 материалов')
                if existing:
                    position = next((i for i, e in enumerate(entries) if e['id'] == identifier), None)
                    if position is None: entries.append(value)
                    else: entries[position] = value
                else:
                    entries.append(value)
            else:
                raise ValueError('Неизвестная операция')
            atomic_json(self.path, {'entries': entries})
            return self.read()

    def schedule(self, body, apply=False):
        with self.lock:
            state = self.checked(body.get('expected_revision'))
            proposed = distribute(state['entries'], body.get('options'))
            if not apply:
                return {'revision': state['revision'], 'assignments': proposed,
                        'excluded_count': sum(bool(e.get('excluded')) for e in state['entries'])}
            saved = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'entries': []}
            dates = {e['id']: e['date'] for e in proposed}
            for current in state['entries']:
                if current['id'] not in dates: continue
                value = entry({**current, 'date': dates[current['id']]}, current['id'])
                if current.get('origin') == 'library': value['origin'] = 'library'
                position = next((i for i, e in enumerate(saved['entries']) if e['id'] == current['id']), None)
                if position is None: saved['entries'].append(value)
                else: saved['entries'][position] = value
            atomic_json(self.path, saved)
            return self.read()
