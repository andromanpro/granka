"""Recoverable article bundles. External publications and shared originals are untouched."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
from urllib.parse import unquote
from .ai_provider import atomic_json
from .file_lock import file_lock


class TrashConflict(ValueError):
    pass


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


class ArticleTrash:
    def __init__(self, state, roots, busy=None, external=None):
        self.state, self.roots = state, roots
        self.busy = busy or (lambda paths: False)
        self.external = external or (lambda ident: None)

    @property
    def directory(self):
        return Path(self.state()) / 'article-trash'

    @contextmanager
    def locked(self):
        with file_lock(self.directory / 'trash.lock', 'Корзина занята. Обнови её и повтори.', TrashConflict):
            yield

    def record(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident):
            raise ValueError('Неизвестная запись корзины')
        return self.directory / 'records' / (ident + '.json')

    def records(self):
        return [json.loads(p.read_text(encoding='utf-8')) for p in sorted((self.directory / 'records').glob('*.json'))]

    def read(self):
        records = [r for r in self.records() if r['status'] not in {'cancelled', 'restored', 'purged'}]
        return {'revision': fingerprint(records), 'entries': [
            {k: r[k] for k in ['id', 'title', 'deleted_at', 'root', 'rel', 'status', 'external_id']} |
            {'files': len(r['files']), 'bytes': sum(f['size'] for f in r['files']), 'shared': r['shared']}
            for r in sorted(records, key=lambda r: r['deleted_at'], reverse=True)]}

    def hidden_entry(self, entry):
        for row in self.records():
            if row['status'] in {'cancelled', 'restored'}:
                continue
            if row.get('external_id'):
                if entry.get('id') == row['external_id']:
                    return True
                continue
            root = self.roots().get(entry.get('root'))
            if root is None or str(Path(root).resolve()) != row['root_path']:
                continue
            if entry.get('file') != row['document_rel']:
                continue
            # A newly created source at a reused path is projected afresh, without
            # the deleted article's saved calendar date/notes or publication receipt.
            if entry.get('source_revision') and row['status'] in {'trashed', 'purged'}:
                return False
            return True
        return False

    @staticmethod
    def checked(root, relative):
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '\\' in relative or '..' in Path(relative).parts:
            raise ValueError('Недопустимый путь статьи')
        root = Path(root).resolve(); path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError('Путь выходит за папку статей')
        current = root
        for part in Path(relative).parts:
            current /= part
            if current.is_symlink() or (current.exists() and getattr(current.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
                raise ValueError('Ссылки и точки подключения нельзя помещать в корзину')
        return path

    @staticmethod
    def stamp(path):
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('В статье найден необычный файл')
        return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'inode': info.st_ino}

    def identity(self, body):
        return {k: body.get(k, '') for k in ['root', 'rel', 'external_id']}

    def shared_assets(self, assets, article_files):
        for base in {Path(p).resolve() for p in self.roots().values()}:
            for pattern in ['*.md', '*.MD', '*.granka.json']:
                for path in base.rglob(pattern):
                    if path.resolve() in article_files or path.is_relative_to(assets) or '.git' in path.parts:
                        continue
                    if not path.resolve().is_relative_to(base):
                        continue
                    try:
                        if path.stat().st_size > 16 * 1024 * 1024:
                            return True  # Unable to inspect safely: retain shared originals.
                        if assets.name in unquote(path.read_text(encoding='utf-8-sig')):
                            return True
                    except (OSError, UnicodeError):
                        return True
        return False

    def preview(self, body):
        identity = self.identity(body)
        if identity['external_id']:
            item = self.external(identity['external_id'])
            if not item:
                raise LookupError('Ссылка на публикацию не найдена')
            value = {**identity, 'title': item['title'], 'root_path': '', 'document_rel': '',
                     'files': [], 'dirs': [], 'shared': [], 'external': item}
        else:
            root = self.roots().get(identity['root'])
            if root is None:
                raise ValueError('Папка статей не подключена')
            root = Path(root).resolve(); chosen = self.checked(root, identity['rel'])
            if chosen.name.endswith('.granka.json'):
                document = chosen; markdown = chosen.with_name(chosen.name.removesuffix('.granka.json') + '.md')
                if not markdown.exists() and markdown.with_suffix('.MD').exists(): markdown = markdown.with_suffix('.MD')
            elif chosen.suffix.lower() == '.md':
                markdown = chosen; document = chosen.with_name(chosen.stem + '.granka.json')
            else:
                raise ValueError('В корзину можно перенести Markdown и разметку статьи')
            files = []
            for path in [markdown, document]:
                self.checked(root, path.relative_to(root).as_posix())
                if path.is_file(): files.append(path)
            if not files:
                raise LookupError('Статья уже отсутствует в этой папке')
            if self.busy(set(files)):
                raise TrashConflict('Для статьи выполняется задание. Дождись его завершения.')
            title = markdown.stem
            try:
                text = json.loads(document.read_text(encoding='utf-8'))['versions'][-1]['text'] if document.is_file() else markdown.read_text(encoding='utf-8-sig')
                heading = re.search(r'^#\s+(.+)$', text, re.M)
                if heading: title = heading[1][:300]
            except (ValueError, KeyError, IndexError, UnicodeError):
                pass
            values = [{'rel': p.relative_to(root).as_posix(), 'keep': False, **self.stamp(p)} for p in files]
            assets = markdown.with_name(markdown.stem + '.assets'); dirs = []; shared = []
            self.checked(root, assets.relative_to(root).as_posix())
            if assets.exists():
                if not assets.is_dir() or self.directory.resolve().is_relative_to(assets.resolve()):
                    raise ValueError('Не удалось безопасно определить материалы статьи')
                keep = self.shared_assets(assets, {p.resolve() for p in files})
                if keep: shared.append(assets.relative_to(root).as_posix())
                for path in [assets, *sorted(assets.rglob('*'))]:
                    rel = path.relative_to(root).as_posix(); self.checked(root, rel)
                    if path.is_dir(): dirs.append({'rel': rel, 'keep': keep})
                    else: values.append({'rel': rel, 'keep': keep, **self.stamp(path)})
            value = {**identity, 'title': title, 'root_path': str(root), 'document_rel': document.relative_to(root).as_posix(),
                     'files': values, 'dirs': dirs, 'shared': shared}
        return {**value, 'revision': fingerprint(value)}

    def payload(self, row):
        path = self.directory / 'payload' / row['id']
        if path.resolve().parent != (self.directory / 'payload').resolve() or path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
            raise ValueError('Повреждён путь корзины')
        return path

    def clear_payload(self, row):
        folder = self.payload(row)
        if not folder.exists(): return
        allowed = {str(i) + '.blob' for i in range(len(row['files']))}
        for path in folder.iterdir():
            if path.name not in allowed or path.is_symlink() or not path.is_file():
                raise TrashConflict('Содержимое корзины изменено извне. Автоматическая очистка остановлена.')
            item = row['files'][int(path.stem)]
            if item.get('sha256') and digest(path) != item['sha256']:
                raise TrashConflict('Копия в корзине изменена извне. Автоматическая очистка остановлена.')
        for path in folder.iterdir(): path.unlink()
        folder.rmdir()

    def copy_to_archive(self, source, target):
        hasher = hashlib.sha256()
        with source.open('rb') as src, target.open('xb') as dest:
            while chunk := src.read(1024 * 1024):
                hasher.update(chunk); dest.write(chunk)
            dest.flush(); os.fsync(dest.fileno())
        if digest(target) != hasher.hexdigest(): raise ValueError('Не удалось проверить копию в корзине')
        return hasher.hexdigest()

    def put(self, body):
        try: ident = uuid.UUID(body.get('operation_id', '')).hex
        except (TypeError, ValueError, AttributeError): raise ValueError('Нужен идентификатор переноса') from None
        with self.locked():
            record = self.record(ident)
            if record.exists():
                previous = json.loads(record.read_text(encoding='utf-8'))
                if self.identity(body) != {k: previous[k] for k in ['root', 'rel', 'external_id']}:
                    raise TrashConflict('Этот запрос относится к другой статье')
                if previous['status'] == 'trashed': return {'ok': True, 'id': ident, 'idempotent': True}
                if previous['status'] != 'cancelled':
                    raise TrashConflict('Перенос был прерван. Открой корзину и восстанови статью перед новым переносом.')
            plan = self.preview(body)
            if plan['revision'] != body.get('expected_revision'):
                raise TrashConflict('Статья или материалы изменились. Проверь список файлов ещё раз.')
            row = {**plan, 'id': ident, 'deleted_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'status': 'preparing'}
            folder = self.payload(row)
            removed = []; complete = False
            try:
                atomic_json(record, row); folder.mkdir(parents=True, exist_ok=False)
                for i, item in enumerate(row['files']):
                    source = self.checked(row['root_path'], item['rel'])
                    if self.stamp(source) != {k: item[k] for k in ['size', 'mtime_ns', 'inode']}:
                        raise TrashConflict('Файл изменился во время переноса')
                    item['sha256'] = self.copy_to_archive(source, folder / (str(i) + '.blob'))
                    if self.stamp(source) != {k: item[k] for k in ['size', 'mtime_ns', 'inode']}:
                        raise TrashConflict('Файл изменился во время копирования')
                row['status'] = 'deleting'; atomic_json(record, row); complete = True
                for item in row['files']:
                    if item['keep']: continue
                    source = self.checked(row['root_path'], item['rel'])
                    if self.stamp(source) != {k: item[k] for k in ['size', 'mtime_ns', 'inode']}:
                        raise TrashConflict('Файл изменился перед переносом в корзину')
                    source.unlink(); removed.append(item)
                for item in reversed(row['dirs']):
                    if not item['keep']:
                        directory = self.checked(row['root_path'], item['rel'])
                        try: directory.rmdir()
                        except OSError: pass  # Newly added or shared files stay where they are.
                row['status'] = 'trashed'; atomic_json(record, row)
                return {'ok': True, 'id': ident}
            except Exception:
                # No original is removed until every archive copy is complete.
                rollback = True
                for item in removed:
                    try:
                        self.restore_file(row, item, row['files'].index(item))
                    except (OSError, ValueError): rollback = False
                row['status'] = 'cancelled' if rollback else 'recovery'
                if not complete: row['status'] = 'cancelled'
                atomic_json(record, row)
                if row['status'] == 'cancelled': self.clear_payload(row)
                raise

    def restore_file(self, row, item, index):
        target = self.checked(row['root_path'], item['rel']); blob = self.payload(row) / (str(index) + '.blob')
        if blob.is_symlink() or digest(blob) != item['sha256']:
            raise TrashConflict('Копия в корзине повреждена; исходники не перезаписаны')
        if target.exists():
            if not target.is_file() or digest(target) != item['sha256']:
                raise TrashConflict('Имя занято другим файлом: ' + item['rel'])
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = target.with_name('.granka-restore-' + uuid.uuid4().hex + '.tmp')
        try:
            with stage.open('xb') as dest, blob.open('rb') as src:
                while chunk := src.read(1024 * 1024): dest.write(chunk)
                dest.flush(); os.fsync(dest.fileno())
            if digest(stage) != item['sha256']: raise TrashConflict('Не удалось проверить восстановленный файл')
            if os.name == 'nt': stage.rename(target)  # Windows rename refuses an existing destination.
            else: os.link(stage, target)
        except FileExistsError:
            raise TrashConflict('Имя занято: ' + item['rel']) from None
        finally:
            stage.unlink(missing_ok=True)
        return True

    def restore(self, body):
        with self.locked():
            record = self.record(body.get('id')); row = json.loads(record.read_text(encoding='utf-8'))
            if row['status'] == 'restored': return {'ok': True, 'idempotent': True}
            if row['status'] == 'preparing':
                if any(not self.checked(row['root_path'], item['rel']).is_file() for item in row['files']):
                    raise TrashConflict('Копирование прервано, один из исходников отсутствует. Копии сохранены для проверки.')
                row['status'] = 'cancelled'; atomic_json(record, row); self.clear_payload(row)
                return {'ok': True}
            if row['status'] not in {'trashed', 'recovery', 'deleting', 'restoring'}:
                raise ValueError('Эту запись сейчас нельзя восстановить')
            if body.get('expected_revision') != self.read()['revision']:
                raise TrashConflict('Корзина изменилась. Обнови список.')
            root = self.roots().get(row['root'])
            if not row['external_id'] and (root is None or str(Path(root).resolve()) != row['root_path'] or not Path(root).is_dir()):
                raise ValueError('Исходная папка не подключена или недоступна')
            for i, item in enumerate(row['files']):
                target = self.checked(row['root_path'], item['rel'])
                if target.exists() and (not target.is_file() or digest(target) != item['sha256']):
                    raise TrashConflict('Имя занято другим файлом: ' + item['rel'])
                blob = self.payload(row) / (str(i) + '.blob')
                if blob.is_symlink() or digest(blob) != item['sha256']: raise TrashConflict('Копия в корзине повреждена')
            row['status'] = 'restoring'; atomic_json(record, row)
            for item in row['dirs']: self.checked(row['root_path'], item['rel']).mkdir(parents=True, exist_ok=True)
            for i, item in enumerate(row['files']): self.restore_file(row, item, i)
            row['status'] = 'restored'; atomic_json(record, row)
            self.clear_payload(row)
            return {'ok': True, 'root': row['root'], 'rel': row['document_rel']}

    def empty(self, body):
        if body.get('confirm') is not True: raise ValueError('Подтверди окончательную очистку корзины')
        with self.locked():
            current = self.read()
            if body.get('expected_revision') != current['revision']:
                if not current['entries']: return {'ok': True, 'removed': 0, **current}
                raise TrashConflict('Корзина изменилась. Проверь список перед очисткой.')
            rows = [r for r in self.records() if r['status'] not in {'cancelled', 'restored', 'purged'}]
            if any(r['status'] not in {'trashed', 'purging'} for r in rows):
                raise TrashConflict('Сначала восстанови незавершённые переносы из корзины')
            for row in rows:
                row['status'] = 'purging'; atomic_json(self.record(row['id']), row)
                self.clear_payload(row)
                # Keep only a tombstone for stale calendar entries, not article contents.
                row = {k: row[k] for k in ['id', 'root', 'root_path', 'rel', 'document_rel', 'external_id', 'deleted_at']}
                row['status'] = 'purged'; atomic_json(self.record(row['id']), row)
            return {'ok': True, 'removed': len(rows), **self.read()}
