"""Packaged Windows entry points; user state stays outside installed program files."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

SERVER_MUTEX = 'Local\\GrankaInstalledServer'


def prepare(app):
    app = Path(app).resolve()
    os.environ['GRANKA_PACKAGED'] = '1'
    from . import config
    os.environ.setdefault('GRANKA_CONFIG', str(config.state_directory() / 'granka-config.json'))
    config.CONFIG_FILE = Path(os.environ['GRANKA_CONFIG']).resolve()
    state = config.state_directory()
    state.mkdir(parents=True, exist_ok=True)
    marker = state / 'welcome-seeded.json'
    articles = state / 'articles'
    if not marker.exists():
        articles.mkdir(parents=True, exist_ok=True)
        stored = json.loads(config.CONFIG_FILE.read_text(encoding='utf-8')) if config.CONFIG_FILE.exists() else {}
        if not any(articles.iterdir()) and not stored.get('roots'):
            for name in ['review.md', 'review.granka.json', 'Начало работы.md']:
                source = app / 'examples' / name
                if source.is_file():
                    shutil.copyfile(source, articles / name)
        # Exclusive creation also makes repeated launches leave user edits alone.
        try:
            with marker.open('x', encoding='utf-8') as out:
                json.dump({'seeded': True}, out)
        except FileExistsError:
            pass
    return state


def active_jobs(directory):
    for path in Path(directory).glob('*/job.json'):
        try:
            if json.loads(path.read_text(encoding='utf-8')).get('status') in {'queued', 'running'}:
                return True
        except (OSError, ValueError):
            continue
    return False


@contextmanager
def server_mutex():
    handle = None
    if os.name == 'nt' and os.environ.get('GRANKA_PACKAGED') == '1':
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        api.CreateMutexW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.CreateMutexW(None, False, SERVER_MUTEX)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if handle:
            api.CloseHandle(handle)


def stop_server(app):
    from . import config
    base = 'http://127.0.0.1:' + str(config.load()['server']['port'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + '/api/runtime', timeout=5) as response:
            runtime = json.load(response)
    except urllib.error.URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return {'ok': True, 'already_stopped': True}
        raise
    installation = str(Path(app).resolve())
    if runtime.get('installation') != installation or not runtime.get('packaged'):
        raise RuntimeError('По этому адресу работает другая копия Гранки. Она не остановлена.')
    with opener.open(base + '/', timeout=5) as response:
        html = response.read().decode('utf-8')
    match = re.search(r'<meta name="granka-token" content="([a-f0-9]+)"', html)
    if not match:
        raise RuntimeError('Сервер не подтвердил запрос остановки.')
    request = urllib.request.Request(base + '/api/quit',
        data=json.dumps({'installation': installation}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'X-Granka-Token': match[1]})
    try:
        with opener.open(request, timeout=10) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            with error:
                detail = json.load(error).get('error')
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(detail or 'Не удалось остановить Гранку.') from None
    if not result.get('ok'):
        raise RuntimeError('Гранка не подтвердила остановку.')
    return result


def login(app, state):
    from .ai_provider import codex_binary
    log = Path(state) / 'codex-login.log'
    with log.open('wb') as out:
        result = subprocess.run([codex_binary(), 'login'], stdout=out, stderr=out,
            timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Вход не завершён. Повтори его позже. Подробности: ' + str(log))


def message(text, error=False, confirm=False):
    if os.name == 'nt':
        flags = (0x24 if confirm else 0x10 if error else 0x40)
        return ctypes.windll.user32.MessageBoxW(None, text, 'Гранка', flags)
    print(text)
    return 6


def main(app, argv=None):
    app = Path(app).resolve()
    args = list(sys.argv[1:] if argv is None else argv)
    state = prepare(app)
    quiet = '--quiet' in args
    try:
        if '--stop' in args:
            if not quiet and message('Сохрани текст во всех вкладках Гранки. Остановить программу?', confirm=True) != 6:
                return 0
            stop_server(app)
            return 0
        if '--login' in args:
            login(app, state)
            if not quiet:
                message('Вход выполнен. Можно вернуться в Гранку и проверить текст.')
            return 0
        import importlib.util
        spec = importlib.util.spec_from_file_location('granka_desktop_launcher', app / 'launch-granka.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        launcher.main()
        return 0
    except Exception as error:
        if not quiet:
            message(str(error), error=True)
        if sys.stderr:
            print(str(error), file=sys.stderr)
        return 1
