"""Exercise a self-contained payload with isolated state and no system Python in PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def check(payload, output, state=None):
    payload, output = Path(payload).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state = Path(state).resolve() if state else output / ('state-' + uuid.uuid4().hex[:12])
    state.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    settings = state / 'granka-config.json'
    previous = json.loads(settings.read_text(encoding='utf-8')) if settings.exists() else {}
    settings.write_text(json.dumps({**previous, 'server': {'port': port}}, ensure_ascii=False), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'OPENROUTER_API_KEY'}}
    env.update(GRANKA_STATE_DIR=str(state), GRANKA_CONFIG=str(settings),
               CODEX_HOME=str(state / 'test-codex-home'), PYTHONUTF8='1',
               PATH=str(Path(os.environ['WINDIR']) / 'System32') + ';' + os.environ['WINDIR'])
    (state / 'test-codex-home').mkdir(exist_ok=True)
    base = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token = None
    journal = []

    def api(path, body=None):
        headers = {}
        if body is not None:
            headers = {'Content-Type': 'application/json', 'X-Granka-Token': token}
        request = urllib.request.Request(base + path,
            data=json.dumps(body).encode('utf-8') if body is not None else None, headers=headers)
        with opener.open(request, timeout=20) as response:
            return json.load(response)

    def launch(*args):
        result = subprocess.run([str(payload / 'Granka.exe'), *args], env=env, cwd=str(payload),
            capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('Standalone launcher failed; inspect logs in ' + str(state))

    def stop():
        launch('--stop', '--quiet')
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                api('/api/version')
            except (OSError, urllib.error.URLError):
                return
            time.sleep(0.1)
        raise RuntimeError('Standalone server did not stop')

    pending = state / 'jobs/smoke-test/job.json'
    try:
        launch('--no-browser', '--quiet')
        runtime = api('/api/runtime')
        assert runtime['packaged'] and runtime['installation'] == str(payload / 'app'), runtime
        with opener.open(base + '/', timeout=10) as response:
            html = response.read().decode('utf-8')
        token = re.search(r'<meta name="granka-token" content="([a-f0-9]+)"', html)[1]
        documents = api('/api/docs')['docs']
        assert any('review.granka.json' in str(x) for x in documents), 'Demo was not seeded'
        assert (state / 'articles/Начало работы.md').is_file(), 'Welcome text was not seeded'
        info = api('/api/settings')
        assert info['codex_available'], 'Bundled Codex was not discovered'
        journal.append('first launch and bundled Codex discovery with system tools removed from PATH')
        version = subprocess.run([str(payload / 'codex/codex.exe'), '--version'], env=env,
            capture_output=True, encoding='utf-8', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert version.returncode == 0, version.stderr
        journal.append(version.stdout.strip())
        document_path = state / 'articles/review.granka.json'
        before = json.loads(document_path.read_text(encoding='utf-8'))
        original = before['versions'][-1]['text']
        expected = original + '\n\nПроверка автономной поставки: сохранённый текст.\n'
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel=os.environ.get('GRANKA_BROWSER_CHANNEL') or 'msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base + '/?' + urllib.parse.urlencode({'root': 'articles', 'f': 'review.granka.json'}))
                page.wait_for_function('window.granka?.state.document && !granka.state.busy')
                page.click('[data-view=source]')
                page.fill('#texteditor', expected)
                page.click('#savebtn')
                page.wait_for_function('granka.state.document.versions.length === 2 && !granka.state.busy')
                page.click('[data-view=article]')
                page.screenshot(path=str(output / 'standalone-first-run.png'), full_page=True)
                assert not errors, errors
            finally:
                browser.close()
        after = json.loads(document_path.read_text(encoding='utf-8'))
        assert after['versions'][-1]['text'] == expected and after['versions'][0] == before['versions'][0]
        api('/api/settings', {'settings': {'image_aspect': '1:1'}})
        journal.append('real browser editing, immutable previous version and saving settings')
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps({'status': 'running'}), encoding='utf-8')
        try:
            api('/api/quit', {'installation': runtime['installation']})
            raise AssertionError('Server accepted shutdown with a running job')
        except urllib.error.HTTPError as error:
            with error:
                assert error.code == 400
        pending.write_text(json.dumps({'status': 'succeeded'}), encoding='utf-8')
        stop()
        journal.append('shutdown refuses an active job, then exits gracefully')
        launch('--no-browser', '--quiet')
        assert json.loads(document_path.read_text(encoding='utf-8')) == after
        assert api('/api/settings')['settings']['image_aspect'] == '1:1'
        assert len(api('/api/docs')['docs']) == len(documents)
        stop()
        journal.append('second launch preserves edited demo, history and settings without duplicate seeding')
        result = {'status': 'passed', 'payload': str(payload), 'state': str(state), 'port': port,
                  'checks': journal, 'actual_account_login_tested': False,
                  'clean_windows_vm_tested': False, 'system_tools_removed_from_path': True}
        (output / 'smoke-receipt.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result
    finally:
        if pending.exists():
            pending.write_text(json.dumps({'status': 'succeeded'}), encoding='utf-8')
        try:
            launch('--stop', '--quiet')
        except (OSError, subprocess.SubprocessError, RuntimeError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--payload', type=Path)
    source.add_argument('--receipt', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    payload = args.payload or Path(json.loads(args.receipt.read_text(encoding='utf-8'))['payload'])
    print(json.dumps(check(payload, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8')
    main()
