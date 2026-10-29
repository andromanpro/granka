"""Local corpus selection, explicit diagnostics and profile cache; never changes source texts."""
import hashlib
import json
import stat
from pathlib import Path
from . import iimeter
from .ai_provider import atomic_json
from .file_lock import file_lock
from .site_connections import SettingsConflict


class VoiceCorpus:
    def __init__(self, state):
        self.state = state

    @property
    def file(self):
        return Path(self.state()) / 'voice-selection.json'

    def read(self):
        raw = self.file.read_bytes() if self.file.exists() else b'{}'
        stored = json.loads(raw)
        return {'directory': stored.get('directory', str(iimeter.CORPUS_DIR)),
                'revision': hashlib.sha256(raw).hexdigest()[:24]}

    def save(self, body):
        value = body.get('directory')
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise ValueError('Укажи папку собственных Markdown-текстов')
        path = Path(value.strip()).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError('Нужна существующая папка с полным путём')
        path = path.resolve()
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.file.with_suffix('.lock'), 'Корпус уже сохраняется. Повтори после завершения.', SettingsConflict):
            if body.get('expected_revision') != self.read()['revision']:
                raise SettingsConflict('Выбор корпуса изменился в другом окне. Открой настройку ещё раз.')
            atomic_json(self.file, {'directory': str(path)})
        return self.read()

    def inspect(self, profile_id):
        if profile_id not in iimeter.PROFILES:
            raise ValueError('Неизвестный профиль голоса')
        settings = self.read()
        directory = Path(settings['directory'])
        if profile_id == 'talks':
            directory = directory / 'talks'
        result = {**settings, 'profile_id': profile_id, 'profile_label': iimeter.PROFILES[profile_id],
                  'profile_directory': str(directory), 'status': 'ok', 'reason': '', 'files': 0, 'words': 0}
        inputs = []
        try:
            try:
                directory_stat = directory.stat()
            except FileNotFoundError:
                result.update(status='not_configured', reason='Сравнение голоса не настроено: выбери папку своих текстов.')
            else:
                if not stat.S_ISDIR(directory_stat.st_mode):
                    raise ValueError('Путь корпуса не является папкой')
                files = sorted(directory.iterdir())
                candidates = [p for p in files if p.is_file() and p.suffix.lower() == '.md' and p.name.lower() != 'readme.md']
                for path in candidates:
                    if not path.resolve().is_relative_to(directory.resolve()):
                        raise ValueError('Ссылка на файл за пределами корпуса: ' + path.name)
                    text = path.read_text(encoding='utf-8-sig')
                    count = iimeter.style_vector(text)['words']
                    if count:
                        inputs.append((path.name, text))
                        result['words'] += count
                result['files'] = len(inputs)
                if not files:
                    result.update(status='empty', reason='Папка корпуса пуста. Добавь собственные тексты в формате .md.')
                elif not inputs:
                    result.update(status='unsuitable', reason='Нет подходящей прозы: нужны Markdown-файлы с текстом, а не только кодом или разметкой.')
        except (OSError, UnicodeError, ValueError) as error:
            result.update(status='read_error', reason='Не удалось прочитать корпус: ' + str(error))
            inputs = []
        fingerprint = hashlib.sha256(json.dumps([iimeter.ENGINE_VERSION, profile_id, str(directory), inputs], ensure_ascii=False).encode()).hexdigest()
        result['fingerprint'] = fingerprint
        return result, inputs

    def measure(self, text, profile_id):
        corpus, inputs = self.inspect(profile_id)
        profile = {}
        if corpus['status'] == 'ok':
            destination = Path(self.state()) / 'profiles' / ('selected-' + profile_id + '.json')
            try:
                if destination.exists():
                    try:
                        cached = json.loads(destination.read_text(encoding='utf-8'))
                        required = set(iimeter.style_vector('Текст')) | {'fingerprint', 'sources'}
                        if isinstance(cached, dict) and required <= cached.keys():
                            profile = cached
                    except (ValueError, UnicodeError):
                        profile = {}
                if profile.get('fingerprint') != corpus['fingerprint']:
                    profile = iimeter.build_profile(profile_id, inputs, corpus['fingerprint'], destination=destination)
            except (OSError, ValueError) as error:
                corpus.update(status='read_error', reason='Не удалось рассчитать профиль: ' + str(error))
                profile = {}
        result = iimeter.measure(text, profile_id, profile=profile)
        return {**result, 'corpus_state': corpus}
