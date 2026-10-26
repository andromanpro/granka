"""Local article assets and immutable Telegram media inputs; no network access."""
import hashlib
import io
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from PIL import Image

PHOTO_EXT = {'.png', '.jpg', '.jpeg', '.webp'}
MEDIA_EXT = PHOTO_EXT | {'.mp4'}
MAX_TOTAL = 50 * 1024 * 1024
MIME = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.webp': 'image/webp', '.mp4': 'video/mp4'}
IMAGE_BLOCK = re.compile(r'(?m)^([ \t]*)!\[([^\]\n]*)\]\((<?[^\s)>]+>?)(?:[ \t]+"([^"\n]*)")?\)[ \t]*$')


def assets_directory(document, root):
    directory = (document.parent / (document.name.removesuffix('.granka.json') + '.assets')).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise PermissionError('Материалы вне рабочей папки')
    return directory


def validate_bytes(raw, suffix):
    suffix = suffix.lower()
    if suffix not in MEDIA_EXT or not raw:
        raise ValueError('Выбери PNG, JPEG, WebP или MP4')
    if len(raw) > (MAX_TOTAL if suffix == '.mp4' else 10 * 1024 * 1024):
        raise ValueError('Видео — до 50 МБ, картинка — до 10 МБ')
    if suffix == '.mp4':
        if len(raw) < 16 or raw[4:8] != b'ftyp':
            raise ValueError('Файл не похож на MP4')
    else:
        try:
            with Image.open(io.BytesIO(raw)) as image:
                w, h = image.size
                if image.format not in {'PNG', 'JPEG', 'WEBP'} or w + h > 10000 or max(w, h) / min(w, h) > 20:
                    raise ValueError('Для Telegram нужна картинка с суммой сторон до 10000px и соотношением до 20:1')
                image.verify()
        except (OSError, Image.DecompressionBombError) as error:
            raise ValueError('Не удалось прочитать картинку') from error
    return 'video' if suffix == '.mp4' else 'photo'


def import_asset(document, root, filename, raw):
    suffix = Path(filename).suffix.lower()
    kind = validate_bytes(raw, suffix)
    digest = hashlib.sha256(raw).hexdigest()
    directory = assets_directory(document, root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ('tg-' + digest[:24] + suffix)
    if not path.exists():
        with path.open('xb') as stream:
            stream.write(raw)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Конфликт сохранённого материала')
    return {'src': path.relative_to(document.parent).as_posix(), 'type': kind,
            'name': Path(filename.replace('\\', '/')).name[:150], 'bytes': len(raw), 'sha256': digest}


def resolve_asset(document, root, src):
    parsed = urlsplit(src)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Нужен локальный материал статьи')
    path = (document.parent / unquote(src)).resolve()
    if path.suffix.lower() not in MEDIA_EXT or not path.is_relative_to(assets_directory(document, root)):
        raise PermissionError('Вложение вне каталога материалов статьи')
    if not path.is_file():
        raise LookupError('Вложение не найдено')
    return path


def attachment_specs(value):
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError('К анонсу можно добавить до 10 фото и видео')
    clean = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get('src'), str):
            raise ValueError('Некорректное вложение')
        src = item['src']
        parsed = urlsplit(src)
        if len(src) > 1000 or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or '\x00' in src:
            raise ValueError('Некорректный путь вложения')
        if Path(unquote(src)).suffix.lower() not in MEDIA_EXT:
            raise ValueError('Выбери картинку или MP4')
        clean.append({'src': src, 'name': str(item.get('name') or Path(src).name)[:150],
                      'type': 'video' if Path(unquote(src)).suffix.lower() == '.mp4' else 'photo'})
    if len({item['src'] for item in clean}) != len(clean):
        raise ValueError('Вложение добавлено дважды')
    return clean


def rich_input(text, resolver):
    """Replace standalone local Markdown media with Bot API upload references."""
    media, files = [], {}
    def replace(match):
        raw = match[3].strip('<>')
        parsed = urlsplit(raw)
        if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
            return match[0]
        if parsed.scheme or parsed.netloc:
            raise ValueError('В статье Telegram есть неподдерживаемый адрес медиа')
        path = resolver(raw)
        if len(media) >= 30:
            raise ValueError('В одной статье поддерживается до 30 локальных медиа')
        kind = 'video' if path.suffix.lower() == '.mp4' else 'photo'
        identifier = 'm' + str(len(media))
        descriptor = {'type': kind, 'media': 'attach://' + identifier}
        if kind == 'video':
            descriptor['supports_streaming'] = True
        media.append({'id': identifier, 'media': descriptor})
        files[identifier] = path
        caption = (match[4] or match[2]).replace('"', '\\"')
        return f'![{match[2]}](tg://{kind}?id={identifier} "{caption}")'
    markdown = IMAGE_BLOCK.sub(replace, text)
    if re.search(r'!\[[^\]\n]*\]\[',markdown):
        raise ValueError('Для статьи Telegram используй вставку медиа вида ![подпись](путь), отдельным абзацем')
    for match in re.finditer(r'<(?:img|video|audio|source)\b[^>]*\bsrc=["\']([^"\']+)',markdown,re.I):
        if not match[1].startswith(('http://','https://','tg://')):
            raise ValueError('Локальные медиа вставляй кнопкой добавления, отдельным абзацем Markdown')
    # Fail visibly rather than publishing a local path left inside inline media.
    for match in re.finditer(r'!\[[^\]\n]*\]\(([^\s)]+)', markdown):
        if not match[1].startswith(('http://', 'https://', 'tg://')):
            raise ValueError('Картинки и видео в статье Telegram размещай отдельным абзацем')
    result = {'markdown': markdown}
    if media:
        result['media'] = media
    return result, files
