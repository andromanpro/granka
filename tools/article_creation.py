"""Create a Markdown/document pair with exclusive writes and durable operation receipts."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import uuid
from .ai_provider import atomic_json
from .document_store import validate
from .file_lock import file_lock


class CreationConflict(ValueError):
    pass


def line(value, name, limit):
    if not isinstance(value, str) or len(value) > limit or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError(name + ': недопустимые символы или слишком длинное значение')
    return unicodedata.normalize('NFC', value).strip()


class ArticleCreator:
    def __init__(self, state, roots):
        self.state, self.roots = state, roots

    @property
    def directory(self):
        return Path(self.state()) / 'article-creations'

    def targets(self):
        return [{'id': key, 'path': str(path)} for key, path in self.roots().items()]

    def values(self, body):
        title = line(body.get('title'), 'Название', 180)
        if not title:
            raise ValueError('Укажи название статьи')
        audience = line(body.get('audience', ''), 'Для кого', 500)
        purpose = line(body.get('purpose', ''), 'Зачем', 1000)
        mode = body.get('mode', 'blank')
        if mode not in {'blank', 'outline', 'notes', 'ai'}:
            raise ValueError('Выбери способ начала текста')
        notes = body.get('notes', '')
        if not isinstance(notes, str) or len(notes) > 100000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', notes):
            raise ValueError('Проверь заметки: до 100000 символов')
        notes = notes.replace('\r\n', '\n').replace('\r', '\n')
        root_id = body.get('root')
        root = self.roots().get(root_id)
        if root is None:
            raise ValueError('Неизвестная папка. Выбери подключённый источник.')
        root = Path(root).resolve()
        folder = body.get('folder', '')
        if not isinstance(folder, str) or len(folder) > 1000 or '\\' in folder or Path(folder).is_absolute() or '..' in Path(folder).parts:
            raise ValueError('Выбери папку внутри разрешённого источника')
        target = (root / folder).resolve()
        if not target.is_relative_to(root):
            raise ValueError('Папка выходит за разрешённый источник')
        if target != root and not target.is_dir():
            raise ValueError('Папка не найдена')
        # A symlink or junction inside a source must never escape that source.
        if target != root and any(p.is_symlink() for p in [root / part for part in Path(folder).parts]):
            raise ValueError('Выбери обычную папку источника')
        stem = re.sub(r'[<>:"/\\|?*]', '-', title).rstrip(' .')
        if not stem or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', stem):
            raise ValueError('Название нельзя использовать как имя файла')
        text = '# ' + title + '\n\n'
        if mode == 'outline':
            text += '## С чего всё началось\n\n\n## Что получилось\n\n\n## Что дальше\n\n'
        elif mode == 'notes':
            text += notes.strip() + '\n'
        elif mode == 'ai':
            draft = body.get('draft_text')
            if not isinstance(draft, str) or not draft.strip() or len(draft) > 100000 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', draft):
                raise ValueError('Сначала получи или заполни заготовку: до 100000 символов')
            text += draft.strip().replace('\r\n', '\n').replace('\r', '\n') + '\n'
            line(body.get('draft_job'), 'Задание заготовки', 24)
        return {'title': title, 'audience': audience, 'purpose': purpose, 'mode': mode,
                'notes': notes, 'root': root_id, 'root_path': str(root), 'folder': folder,
                'stem': stem, 'text': text, 'draft_job': body.get('draft_job') if mode == 'ai' else None}, target

    def preview(self, body):
        value, target = self.values(body)
        stem, n = value['stem'], 1
        while (target / (stem + '.md')).exists() or (target / (stem + '.granka.json')).exists():
            n += 1
            stem = value['stem'] + '-' + str(n)
        return {'filename': stem, 'files': [str(target / (stem + ext)) for ext in ['.md', '.granka.json']]}

    @staticmethod
    def exclusive_write(path, raw):
        stream = path.open('xb')
        identity = os.fstat(stream.fileno())
        try:
            with stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            if path.exists():
                current = path.stat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    path.unlink()
            raise

    def create(self, body, *, generation=None):
        value, target = self.values(body)
        try:
            operation = str(uuid.UUID(body.get('operation_id', '')))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Нужен идентификатор создания статьи') from None
        filename = line(body.get('filename', ''), 'Имя файла', 200)
        if filename and (filename != Path(filename).name or re.search(r'[<>:"/\\|?*]', filename) or filename.endswith((' ', '.')) or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', filename)):
            raise ValueError('Недопустимое имя файла')
        fingerprint = hashlib.sha256(json.dumps([value, filename], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        receipt_file = self.directory / (operation + '.json')
        with file_lock(self.directory / 'create.lock', 'Создание уже выполняется. Повтори тот же запрос.', CreationConflict):
            previous = json.loads(receipt_file.read_text(encoding='utf-8')) if receipt_file.exists() else None
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise CreationConflict('Этот запрос уже использован с другими данными')
                if previous['status'] == 'created':
                    return {**previous['result'], 'idempotent': True}
                filename = previous['filename']
                document = previous['document']
            else:
                filename = filename or self.preview(body)['filename']
                document = {'doc': filename + '.md', 'status': 'draft',
                            'source': (target / (filename + '.md')).relative_to(Path(value['root_path'])).as_posix(),
                            'intent': {'audience': value['audience'], 'purpose': value['purpose']},
                            'versions': [{'id': 'v1', 'label': 'новая статья', 'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'text': value['text']}],
                            'annotations': [], 'ops': [], 'history': []}
                if value['mode'] == 'ai':
                    document['intent']['generation'] = generation or {'job': value['draft_job']}
                    document['versions'][0].update(origin='ai-draft', label='ИИ-заготовка')
                assert not validate(document)
                if any((target / (filename + ext)).exists() for ext in ['.md', '.granka.json']):
                    raise CreationConflict('Имя уже занято. Проверь список создаваемых файлов ещё раз.')
                previous = {'status': 'pending', 'fingerprint': fingerprint, 'filename': filename, 'document': document}
                atomic_json(receipt_file, previous)
            files = [(target / (filename + '.md'), value['text'].encode('utf-8')),
                     (target / (filename + '.granka.json'), json.dumps(document, ensure_ascii=False, indent=1).encode('utf-8'))]
            created = []
            try:
                target.mkdir(parents=True, exist_ok=True)
                # Pending receipts recover a lost process between the two exclusive writes.
                for path, raw in files:
                    if path.exists():
                        if path.read_bytes() != raw:
                            raise CreationConflict('Файл изменён извне. Существующие данные сохранены.')
                    else:
                        created.append((path, raw))
                        self.exclusive_write(path, raw)
                result = {'ok': True, 'root': value['root'], 'rel': files[1][0].relative_to(Path(value['root_path'])).as_posix(),
                          'source_rel': files[0][0].relative_to(Path(value['root_path'])).as_posix(), 'files': [str(p) for p, _ in files]}
                atomic_json(receipt_file, {**previous, 'status': 'created', 'root_path': value['root_path'], 'result': result})
                return result
            except Exception:
                for path, raw in reversed(created):
                    if path.exists() and path.read_bytes() == raw:
                        path.unlink()
                raise

    def selectors(self):
        result = {}
        for file in self.directory.glob('*.json'):
            entry = json.loads(file.read_text(encoding='utf-8'))
            if entry.get('status') != 'created':
                continue
            value = entry['result']
            root = self.roots().get(value['root'])
            if root is not None and str(Path(root).resolve()) == entry.get('root_path'):
                result.setdefault(value['root'], []).append(value['source_rel'])
        return result
