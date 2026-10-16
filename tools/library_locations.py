"""User-selected local sources. Configuration and article contents stay untouched."""
import hashlib
import json
from pathlib import Path
import threading
from tools.ai_provider import atomic_json


class LocationsConflict(Exception):
    pass


class LibraryLocations:
    def __init__(self, state):
        self.state = state
        self.lock = threading.Lock()

    @property
    def file(self):
        return Path(self.state()) / 'library-locations.json'

    def read(self):
        raw = self.file.read_bytes() if self.file.exists() else b'{"locations":[]}'
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('locations'), list):
            raise ValueError('Не удалось прочитать выбранные папки библиотеки')
        return {**data, 'revision': hashlib.sha256(raw).hexdigest()[:24]}

    def roots(self):
        return {item['id']: Path(item['path']) for item in self.read()['locations']}

    def selectors(self):
        return {item['id']: item.get('files', ['.']) for item in self.read()['locations']}

    def add(self, value, kind, expected_revision):
        if kind not in {'file', 'folder'}:
            raise ValueError('Выбери файл или папку')
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError('Укажи полный путь к файлу или папке')
        chosen = Path(value.strip()).expanduser()
        if not chosen.is_absolute():
            raise ValueError('Нужен полный путь к файлу или папке')
        chosen = chosen.resolve()
        if kind == 'folder':
            if not chosen.is_dir():
                raise ValueError('Папка не найдена')
            parent = chosen
        else:
            if not chosen.is_file() or not (chosen.suffix.lower() == '.md' or chosen.name.endswith('.granka.json')):
                raise ValueError('Выбери существующий Markdown или файл .granka.json')
            parent = chosen.parent
        ident = 'local-' + hashlib.sha256(str(parent).encode('utf-8')).hexdigest()[:16]
        with self.lock:
            current = self.read()
            if expected_revision != current['revision']:
                raise LocationsConflict('Список папок изменился. Открой библиотеку ещё раз.')
            locations = current['locations']
            item = next((item for item in locations if item['id'] == ident), None)
            if item is None:
                item = {'id': ident, 'path': str(parent), 'files': []}
                locations.append(item)
            if kind == 'folder':
                item.pop('files', None)
            elif 'files' in item:
                markdown = chosen.name.removesuffix('.granka.json') + '.md' if chosen.name.endswith('.granka.json') else chosen.name
                item['files'] = sorted(set(item['files'] + [markdown]))
            atomic_json(self.file, {'locations': locations})
        selection = None
        if kind == 'file':
            markdown = chosen.with_name(chosen.name.removesuffix('.granka.json') + '.md') if chosen.name.endswith('.granka.json') else chosen
            document = markdown.with_name(markdown.stem + '.granka.json')
            selection = {'root': ident, 'rel': markdown.name,
                         'granka_rel': document.name, 'has_granka': document.is_file()}
        return {'ok': True, 'selection': selection, **self.read()}
