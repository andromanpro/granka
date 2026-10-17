"""Read-only projection for the local Blatero board; never publishes or edits articles."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import config
from tools.planning_library import catalog_sources, scan_library
from tools.publication_plan import PublicationPlan
from tools.library_locations import LibraryLocations


def export_plan(roots=None, state=None, settings=None):
    state = config.STATE if state is None else Path(state)
    locations = LibraryLocations(lambda: state)
    roots = {**config.roots(), **locations.roots()} if roots is None else roots
    settings = config.load() if settings is None else settings
    repository = settings.get('site', {}).get('repository')
    repository = config.path(repository) if repository else None
    selectors = dict(settings.get('planning', {}).get('source_dirs') or {key: ['.'] for key in roots})
    selectors.update({key: value for key, value in locations.selectors().items() if key in roots})
    sources = catalog_sources(
        scan_library(roots, selectors, include_published=True,
                     publications=settings.get('planning',{}).get('publications',[]),
                     excluded_sources=settings.get('planning',{}).get('excluded_sources',{})),
        [], state / 'site-publications', repository, roots)
    plan = PublicationPlan(lambda: state, lambda: sources).read()
    allowed = ('id', 'title', 'root', 'file', 'source_rel', 'source_revision', 'channel',
               'date', 'status', 'url', 'excluded', 'notes', 'readiness', 'source_error', 'readonly', 'has_granka')
    entries = []
    for item in plan['entries']:
        value = {k: item[k] for k in allowed if k in item}
        root = roots.get(item.get('root'))
        relative = item.get('source_rel') or item.get('file')
        if root and relative:
            candidate = (Path(root) / relative).resolve()
            if candidate.is_relative_to(Path(root).resolve()):
                value['locator'] = str(candidate)
        entries.append(value)
    return {'schema_version': 1, 'provider': 'granka', 'revision': plan['revision'],
            'port': settings.get('server', {}).get('port', 8901),
            'observed_at': datetime.now(timezone.utc).isoformat(), 'entries': entries}


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(export_plan(), ensure_ascii=False))
