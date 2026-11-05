"""Explicit local schedules over the reviewed Telegram outbox, never drafts.

No startup catch-up, no automatic retry after either rejection or uncertainty.
The existing delivery lock is the linearization point for send/cancel/reschedule.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .telegram_delivery import delivery_lock, PublicationBusy

GRACE_SECONDS = 60
SNAPSHOT_KEYS = ('id', 'root', 'rel', 'version', 'text', 'channel', 'sender_mode',
                 'config_fingerprint', 'photo_path', 'photo_sha256', 'media_paths',
                 'attachments', 'rich_message', 'action', 'target_id', 'base_source_id')


def snapshot_revision(record):
    value = {key: record.get(key) for key in SNAPSHOT_KEYS}
    if value.get('photo_path')and not value.get('photo_sha256'):
        value['photo_sha256']=hashlib.sha256(Path(value['photo_path']).read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def zone(name):
    if not isinstance(name, str) or len(name) > 80:
        raise ValueError('Выбери часовой пояс')
    if name == 'UTC':
        return timezone.utc
    match = re.fullmatch(r'UTC([+-])(\d{2}):(\d{2})', name)
    if match:
        hours, minutes = int(match[2]), int(match[3])
        if hours > 14 or minutes > 59 or hours == 14 and minutes:
            raise ValueError('Некорректное смещение UTC')
        return timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if match[1] == '+' else -1)))
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Часовой пояс недоступен. Выбери UTC или смещение UTC±ЧЧ:ММ.') from None


def scheduled_time(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?', value):
        raise ValueError('Укажи дату и время публикации')
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Укажи дату и время публикации') from None
    tz = zone(name)
    candidates = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=tz, fold=fold)
        if datetime.fromtimestamp(aware.timestamp(), tz).replace(tzinfo=None) == local:
            candidates.add(aware.timestamp())
    if len(candidates) != 1:
        raise ValueError('Время попало на перевод часов. Выбери другое время или фиксированное смещение UTC.')
    return candidates.pop()


def policy(value):
    if not isinstance(value, dict):
        raise ValueError('Проверь разрешённые дни и часы')
    days = value.get('days', list(range(7)))
    if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days):
        raise ValueError('Выбери дни публикации')
    start, end = value.get('start', '00:00'), value.get('end', '23:59')
    for val in (start, end):
        if not isinstance(val, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', val):
            raise ValueError('Укажи часы в формате ЧЧ:ММ')
    if start > end:
        raise ValueError('Начало окна должно быть раньше окончания')
    return {'days': sorted(set(days)), 'start': start, 'end': end}


def permitted(at, name, windows):
    local = datetime.fromtimestamp(at, zone(name))
    return local.weekday() in windows['days'] and windows['start'] <= local.strftime('%H:%M') <= windows['end']


class PublicationScheduler:
    def __init__(self, telegram, clock=time.time):
        self.telegram, self.clock = telegram, clock
        self.stopped = threading.Event()
        self.thread = None
        self.last_error = ''

    def schedule(self, identifier, values):
        if values.get('confirmed') is not True:
            raise ValueError('Подтверди автоматическую отправку выбранного снимка')
        try:
            operation = str(uuid.UUID(values.get('operation_id')))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Нужен идентификатор планирования') from None
        tz = values.get('timezone', 'UTC')
        due = scheduled_time(values.get('when'), tz)
        windows = policy(values.get('windows', {}))
        if not permitted(due, tz, windows):
            raise ValueError('Выбранное время вне разрешённых дней и часов')
        service = self.telegram
        with service.lock, delivery_lock(service.directory):
            record = service.read(identifier)
            previous = record.get('schedule', {})
            spec = {'due': due, 'timezone': tz, 'when': values['when'], 'windows': windows}
            if previous.get('id') == operation:
                if all(previous.get(k) == v for k, v in spec.items()):
                    return service.public(record)
                raise ValueError('Это планирование уже относится к другому времени')
            if previous.get('revision') != values.get('expected_schedule_revision'):
                raise ValueError('Очередь изменилась в другом окне. Обнови список.')
            if due <= self.clock() or due > self.clock() + 366 * 86400:
                raise ValueError('Выбери будущее время в пределах года')
            if record['status'] not in {'prepared', 'failed'}:
                raise ValueError('Этот снимок уже отправляется или не имеет подтверждённого результата')
            if values.get('review_revision') != snapshot_revision(record):
                raise ValueError('Снимок изменился. Проверь предпросмотр заново.')
            if record.get('photo_path')and not record.get('photo_sha256'):
                record['photo_sha256']=hashlib.sha256(Path(record['photo_path']).read_bytes()).hexdigest()
            service.require_channel_publication()
            if record['config_fingerprint'] != service.fingerprint():
                raise ValueError('Настройки бота изменились. Подготовь предпросмотр заново.')
            record['schedule'] = {**spec, 'id': operation, 'state': 'scheduled',
                                  'created': self.clock(), 'revision': uuid.uuid4().hex,
                                  'snapshot_revision': snapshot_revision(record)}
            service.persist(service.record_path(identifier), record)
            return service.public(record)

    def cancel(self, identifier, expected_revision):
        service = self.telegram
        with service.lock, delivery_lock(service.directory):
            record = service.read(identifier)
            schedule = record.get('schedule', {})
            if not expected_revision or expected_revision != schedule.get('revision'):
                raise ValueError('Очередь изменилась в другом окне. Обнови список.')
            if schedule.get('state') == 'cancelled':
                return service.public(record)
            if record['status'] not in {'prepared', 'failed'}:
                raise ValueError('Отправка уже началась. Проверь результат в канале.')
            schedule.update(state='cancelled', revision=uuid.uuid4().hex)
            service.persist(service.record_path(identifier), record)
            return service.public(record)

    def read(self):
        records = []
        for path in (self.telegram.directory / 'outbox').glob('*/publication.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('schedule'):
                records.append(self.telegram.public(record))
        return {'entries': sorted(records, key=lambda r: r['schedule']['due']),
                'running': bool(self.thread and self.thread.is_alive()),
                'last_error': self.last_error, 'grace_seconds': GRACE_SECONDS}

    def tick(self):
        service = self.telegram
        for path in (service.directory / 'outbox').glob('*/publication.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            schedule = record.get('schedule', {})
            if schedule.get('state') == 'scheduled' and schedule['due'] <= self.clock():
                try:
                    service.publish(record['id'], schedule_id=schedule['id'], now=self.clock())
                except PublicationBusy:
                    continue
                except (ValueError, OSError):
                    # A validation failure is before delivery. Keep its outcome durable;
                    # do not expose exception strings (they may contain personal paths).
                    self._pause(record['id'], schedule['id'])
            elif record['status'] == 'sending' and schedule:
                # Only an owner of the OS delivery lock may recover an abandoned send.
                try:
                    with service.lock, delivery_lock(service.directory):
                        current = service.read(record['id'])
                        if current['status'] == 'sending':
                            current.update(status='unknown', error='Отправка прервана. Проверь канал; автоматического повтора нет.')
                            current['schedule']['state'] = 'unknown'
                            service.persist(path, current)
                except PublicationBusy:
                    pass

    def _pause(self, identifier, schedule_id):
        service = self.telegram
        try:
            with service.lock, delivery_lock(service.directory):
                record = service.read(identifier)
                if record.get('schedule', {}).get('id') == schedule_id and record['schedule']['state'] == 'scheduled':
                    record['schedule'].update(state='error', error='Проверка перед отправкой не пройдена. Проверь настройки и снимок.', revision=uuid.uuid4().hex)
                    service.persist(service.record_path(identifier), record)
        except PublicationBusy:
            pass

    def start(self, activity=None):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        def run():
            while not self.stopped.is_set():
                entered = activity is None or activity.begin_request()
                if not entered:
                    return
                try:
                    self.tick()
                    self.last_error = ''
                except Exception:
                    self.last_error = 'Не удалось проверить локальную очередь. Проверь каталог данных.'
                finally:
                    if activity is not None:activity.end_request()
                self.stopped.wait(2)
        self.thread = threading.Thread(target=run, name='granka-publication-scheduler', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=2)
