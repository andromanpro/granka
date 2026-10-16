"""Read article drafts for planning; never create or edit their source files."""
import hashlib
import json
from pathlib import Path
import re
import frontmatter
from .editorial_requests import editorial_requests

SKIP_DIRS = {'.git', 'node_modules', '_archive', '_audit', 'covers', 'voice-corpus', '__pycache__'}
SKIP_NAMES = {'readme.md', 'agents.md', 'claude.md', 'changelog.md', 'devlog.md', 'roadmap.md', 'public_release.md'}


def source_readiness(text, data=None):
    data = data or {}
    version = data.get('versions', [{}])[-1].get('id')
    requests = editorial_requests(text)
    prose = frontmatter.loads(text).content
    for request in reversed(requests):
        # Count only real prose for the first step, not editorial instructions.
        prose = prose.replace(request['quote'], '')
    prose = re.sub(r'^#+[^\n]*$', '', prose, flags=re.M)
    prose = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', prose)
    has_text = bool(re.search(r'\w{2,}', prose))
    current = any(a.get('version') == version and a.get('scope') != 'editorial' and
                  a.get('summary') != 'Подготовлены варианты по поручению автора.'
                  for a in data.get('analyses', [])) if version else False
    annotations = [a for a in data.get('annotations', []) if a.get('version') == version and a.get('type') == 'candidate']
    pending = sum(not a.get('decision') for a in annotations)
    unapplied = sum((a.get('decision') or {}).get('action') in {'accept', 'custom', 'allergen'} for a in annotations)
    checked = current and not pending and not unapplied
    checks = [{'label': 'Есть текст', 'done': has_text},
              {'label': 'Заполнены редакторские вставки', 'done': has_text and not requests},
              {'label': 'Текущая версия проверена, замечания разобраны', 'done': checked},
              {'label': 'Автор отметил готовность', 'done': False}]
    details = []
    if requests: details.append(f'Редакторских вставок: {len(requests)}')
    if not current: details.append('Текущая версия ещё не проверена')
    if pending: details.append(f'Нерешённых замечаний: {pending}')
    if unapplied: details.append(f'Решений к применению: {unapplied}')
    return {'percent': 25 * sum(c['done'] for c in checks), 'basis': 'steps', 'checks': checks,
            'details': details, 'pending': pending, 'requests': len(requests)}


def scan_library(roots, source_dirs=None, include_published=False):
    """source_dirs restricts each root to relative folders or explicitly selected files."""
    if source_dirs is not None and not isinstance(source_dirs, dict):
        raise ValueError('planning.source_dirs должен быть объектом')
    selectors = source_dirs or {key: ['.'] for key in roots}
    out = []; seen = set()
    for key, folders in selectors.items():
        if key not in roots or not isinstance(folders, list) or any(not isinstance(p, str) for p in folders):
            raise ValueError('Проверь корни и папки planning.source_dirs')
        root = Path(roots[key]).resolve()
        for folder in folders:
            directory = (root / folder).resolve()
            if not directory.is_relative_to(root):
                raise ValueError('Папка планирования находится вне рабочего корня')
            if directory.is_dir():
                paths = set(directory.rglob('*.md'))
                paths.update(directory.rglob('*.MD'))
                for document in directory.rglob('*.granka.json'):
                    markdown = document.with_name(document.name.removesuffix('.granka.json') + '.md')
                    uppercase = markdown.with_suffix('.MD')
                    paths.add(uppercase if not markdown.exists() and uppercase.is_file() else markdown)
            elif directory.suffix.lower() == '.md':
                paths = {directory}
            else:
                continue
            for markdown in sorted(paths):
                if not markdown.resolve().is_relative_to(root) or markdown.resolve() in seen: continue
                parts = markdown.relative_to(root).parts
                if markdown.name.lower() in SKIP_NAMES or any(p in SKIP_DIRS or p.endswith('.assets') for p in parts[:-1]): continue
                document = markdown.with_name(markdown.stem + '.granka.json')
                if not document.resolve().is_relative_to(root): continue
                path = document if document.is_file() else markdown
                try:
                    if path.stat().st_size > 8 * 1024 * 1024: continue
                    raw = path.read_bytes()
                    data = json.loads(raw) if document.is_file() else None
                    text = data['versions'][-1]['text'] if data else raw.decode('utf-8-sig')
                    if len(text) > 300_000: continue
                    post = frontmatter.loads(text)
                    published = post.metadata.get('status') in {'publish', 'published'}
                    if post.metadata.get('status') in {'private', 'archive', 'archived'} or (published and not include_published): continue
                    heading = re.search(r'^#\s+(.+)$', post.content, re.M)
                    title = str(post.metadata.get('title') or (heading[1] if heading else markdown.stem))
                    readiness = source_readiness(text, data)
                except (OSError, ValueError, KeyError, TypeError, IndexError):
                    continue
                seen.add(markdown.resolve())
                rel = document.relative_to(root).as_posix()
                channel = 'telegram' if post.metadata.get('channel') == 'telegram' or re.search(r'(?:^|[-_ ])(?:телеграм|telegram)(?:$|[-_ ])', markdown.stem, re.I) else 'article'
                identifier = 'article-' + hashlib.sha256(json.dumps([key, rel], ensure_ascii=False).encode()).hexdigest()[:24]
                out.append({'id': identifier, 'title': title[:300], 'root': key, 'file': rel,
                            'source_rel': markdown.relative_to(root).as_posix(), 'has_granka': document.is_file(),
                            'source_revision': hashlib.sha256(raw).hexdigest()[:24], 'origin': 'library',
                            'source_status': 'published' if published else 'draft', 'slug': str(post.metadata.get('slug') or ''),
                            'content_hash': hashlib.sha256(post.content.strip().encode('utf-8')).hexdigest(),
                            'mtime': path.stat().st_mtime, 'kb': round(len(raw) / 1024, 1),
                            'channel': channel, 'status': 'draft', 'date': None, 'pinned': False,
                            'excluded': False, 'progress': None, 'url': '', 'notes': '', 'readiness': readiness})
    return out


def catalog_sources(sources, calendar, publication_directory=None, repository=None, roots=None):
    """One source per article; export copies and exact duplicates remain expandable."""
    planned = {(e.get('root'), e.get('file'), e['channel']): e for e in calendar if e.get('file')}
    rows = []
    for source in sources:
        item = planned.get((source['root'], source['file'], source['channel']), {})
        state = 'published' if source['source_status'] == 'published' or item.get('status') == 'published' else 'draft'
        rows.append({**source, 'name': source['title'], 'rel': source['source_rel'], 'granka_rel': source['file'],
                     'dir': Path(source['source_rel']).parent.as_posix(), 'state': state,
                     'readiness': item.get('readiness', source['readiness']), 'planning_id': item.get('id'),
                     'planning_state': 'published' if state == 'published' else 'excluded' if item.get('excluded') else 'scheduled' if item.get('date') else 'queue',
                     'planned_date': item.get('date'), 'copies': []})
    by_key = {(r['root'], r['granka_rel']): r for r in rows}
    aliases = {}
    if publication_directory and repository:
        for path in Path(publication_directory).glob('*/plan.json'):
            try:
                plan = json.loads(path.read_text(encoding='utf-8'))
                if plan.get('stages', {}).get('nas', {}).get('status') != 'succeeded': continue
                source_key = (plan['root'], plan['rel'])
                exported = (Path(repository) / plan['file']).resolve()
                if not exported.is_relative_to(Path(repository).resolve()): continue
                for root_id, root in (roots or {}).items():
                    root = Path(root).resolve()
                    if exported.is_relative_to(root):
                        rel = exported.relative_to(root).with_suffix('.granka.json').as_posix()
                        export_key = (root_id, rel)
                        if source_key != export_key and source_key in by_key and export_key in by_key:
                            aliases[export_key] = source_key
            except (OSError, ValueError, KeyError, TypeError):
                continue
    def group_key(row):
        return (row['root'], row['state'], 'slug' if row['slug'] else 'content', row['slug'] or row['content_hash'])
    for exported_key, original_key in list(aliases.items()):
        same = group_key(by_key[exported_key])
        for key, row in by_key.items():
            if key != original_key and group_key(row) == same:
                aliases[key] = original_key
    result = []; groups = {}
    for row in sorted(rows, key=lambda r: (r['root'], r['granka_rel'])):
        key = (row['root'], row['granka_rel'])
        if key in aliases:
            by_key[aliases[key]]['copies'].append({**row, 'copy_label': 'Копия для сайта'})
            continue
        duplicate_key = group_key(row)
        if duplicate_key in groups:
            groups[duplicate_key]['copies'].append({**row, 'copy_label': 'Другой исходник'})
        else:
            groups[duplicate_key] = row; result.append(row)
    return sorted(result, key=lambda r: -r['mtime'])
