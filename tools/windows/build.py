"""Build an audited Windows x64 payload, portable ZIP and per-user installer."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.public_audit import scan

ROOT_FILES = ['VERSION', 'LICENSE', 'README.md', 'docs/WINDOWS.md', 'docs/USER-GUIDE.md',
             'docs/CONFIGURATION.md', 'docs/DEEP-REVIEW.md',
             'start-granka.py', 'launch-granka.py', 'granka-server.py']
RUNTIME_EXCLUDE = {'public_audit.py', 'public_export.py', 'release.py', 'build-brand.py', 'import-voice-archive.py'}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def package_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def validate_wheel_lock(lock, requirements):
    expected = {}
    for line in Path(requirements).read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s]+)', line)
        if not match:
            raise ValueError('Runtime dependency must have an exact version: ' + line)
        expected[package_name(match[1])] = match[2]
    actual = {package_name(wheel['name']): wheel['version'] for wheel in lock['wheels']}
    if len(actual) != len(lock['wheels']) or actual != expected:
        raise ValueError('Windows wheel lock does not match requirements.txt')


def validate_wheel_metadata(path, item):
    with zipfile.ZipFile(path) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError('Wheel must contain one package metadata file')
        package = BytesParser().parsebytes(wheel.read(metadata[0]))
    if package_name(package.get('Name', '')) != package_name(item['name']) or package.get('Version') != item['version']:
        raise ValueError('Wheel metadata does not match lock: ' + item['filename'])


def fetch(item, cache):
    filename = item.get('filename') or item.get('name') or item['url'].rsplit('/', 1)[-1]
    destination = Path(cache) / filename
    if destination.exists():
        if sha256(destination) != item['sha256']:
            raise ValueError('Cached download failed SHA256: ' + filename)
        return destination
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.partial')
    request = urllib.request.Request(item['url'], headers={'User-Agent': 'Granka-Windows-Build'})
    with urllib.request.urlopen(request, timeout=120) as response, temporary.open('xb') as out:
        shutil.copyfileobj(response, out)
    if sha256(temporary) != item['sha256']:
        raise ValueError('Downloaded file failed SHA256: ' + filename)
    temporary.replace(destination)
    return destination


def extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe archive member')
        source.extractall(destination)


def app_files(root):
    root = Path(root)
    files = [root / name for name in ROOT_FILES]
    files += [p for p in (root / 'tools').glob('*.py') if p.name not in RUNTIME_EXCLUDE]
    files += [p for p in (root / 'static').rglob('*') if p.is_file()]
    files += [p for p in (root / 'docs/images').glob('*.png') if p.is_file()]
    files += [root / 'examples' / name for name in ['review.md', 'review.granka.json', 'Начало работы.md']]
    return sorted(files)


def copy_app(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    for path in app_files(root):
        rel = path.relative_to(root)
        if path.is_symlink() or scan(rel.as_posix(), path.read_bytes()):
            raise ValueError('Public audit rejected application file: ' + rel.as_posix())
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)


def run(command, **kwargs):
    result = subprocess.run([str(x) for x in command], capture_output=True,
        encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)
    if result.returncode:
        raise RuntimeError(Path(command[0]).name + ' failed:\n' + result.stdout + result.stderr)
    return result.stdout.strip()


def compile_launcher(payload, work, version):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():
        raise ValueError('Windows .NET Framework C# compiler was not found')
    source = work / 'Launcher.cs'
    source.write_text((ROOT / 'tools/windows/Launcher.cs').read_text(encoding='utf-8').replace('__VERSION__', version), encoding='utf-8-sig')
    run([compiler, '/nologo', '/target:winexe', '/platform:anycpu', '/optimize+', '/debug-',
         '/reference:System.Windows.Forms.dll', '/win32icon:' + str(payload / 'app/static/brand/granka.ico'),
         '/out:' + str(payload / 'Granka.exe'), source])


def inno_compiler(lock, downloads, cache, supplied=None):
    if supplied:
        compiler = Path(supplied).resolve()
        if not compiler.is_file():
            raise ValueError('ISCC executable was not found')
        return compiler
    directory = cache / ('inno-' + lock['inno']['version'])
    compiler = directory / 'tools/ISCC.exe'
    if not compiler.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        extract(downloads['inno'], directory)
    if not compiler.is_file():
        raise ValueError('Inno Setup compiler installation failed')
    return compiler


def build(output, cache, allow_dirty=False, iscc=None, portable_only=False):
    if os.name != 'nt' or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise ValueError('Build this payload on Windows x64')
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    dirty = bool(run(['git', '-C', ROOT, 'status', '--porcelain']))
    if dirty and not allow_dirty:
        raise ValueError('Commit reviewed changes, or use --allow-dirty for a local candidate only')
    output, cache = Path(output).resolve(), Path(cache).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    work = output / ('build-' + uuid.uuid4().hex[:12])
    payload = work / 'Granka'
    payload.mkdir(parents=True)
    lock = json.loads((ROOT / 'tools/windows/dependencies.lock.json').read_text(encoding='utf-8'))
    validate_wheel_lock(lock, ROOT / 'requirements.txt')
    items = [(key, lock[key]) for key in ['python', 'codex', 'inno']]
    items += [(item['filename'], item) for item in lock['wheels']]
    items += [(item['name'], item) for item in lock['licenses']]
    items += [(item['name'], item) for item in lock.get('codex_helpers', [])]
    with ThreadPoolExecutor(max_workers=5) as pool:
        paths = list(pool.map(lambda pair: fetch(pair[1], cache), items))
    downloads = dict(zip((pair[0] for pair in items), paths))
    print('Downloads verified by SHA256', flush=True)
    runtime = payload / 'runtime'
    extract(downloads['python'], runtime)
    packages = runtime / 'Lib/site-packages'
    packages.mkdir(parents=True, exist_ok=True)
    for wheel in lock['wheels']:
        validate_wheel_metadata(downloads[wheel['filename']], wheel)
        extract(downloads[wheel['filename']], packages)
    # Isolate the runtime from registry paths, PYTHONPATH and global packages.
    (runtime / 'python314._pth').write_text('.\nDLLs\nLib\nLib/site-packages\n../app\nimport site\n', encoding='utf-8')
    copy_app(ROOT, payload / 'app')
    codex = payload / 'codex'
    codex.mkdir()
    from compression import zstd
    with downloads['codex'].open('rb') as source, (codex / 'codex.exe').open('wb') as out:
        with zstd.open(source, 'rb') as decoded:
            shutil.copyfileobj(decoded, out)
    for item in lock.get('codex_helpers', []):
        with downloads[item['name']].open('rb') as source, (codex / item['name']).open('wb') as out:
            with zstd.open(source, 'rb') as decoded:
                shutil.copyfileobj(decoded, out)
    for item in lock['licenses']:
        shutil.copyfile(downloads[item['name']], codex / item['name'])
    compile_launcher(payload, work, version)
    for filename, argument in [('Войти в ChatGPT.cmd', '--login'), ('Остановить Гранку.cmd', '--stop')]:
        (payload / filename).write_text('@echo off\r\nstart "" "%~dp0Granka.exe" ' + argument + '\r\n', encoding='ascii')
    readme = ('Гранка ' + version + '\n\nЗапуск: Granka.exe. Python и Node.js устанавливать не нужно.\n'
              'Открой учебную статью и «Начало работы» в библиотеке.\n'
              'Для ИИ: «Войти в ChatGPT.cmd». Установка не включает подписку на ИИ.\n'
              'Перед обновлением: сохрани текст и запусти «Остановить Гранку.cmd».\n'
              'Настройки и учебные статьи находятся в LocalAppData/Granka и сохраняются после удаления.\n'
              'Сборка не подписана цифровым сертификатом. Windows может показать неизвестного издателя.\n'
              'Исходники и выпуски: https://github.com/andromanpro/granka\n')
    (payload / 'Начать здесь.txt').write_text(readme, encoding='utf-8-sig')
    manifest = {'version': version, 'target': 'windows-x64', 'source_dirty': dirty,
                'source_commit': run(['git', '-C', ROOT, 'rev-parse', 'HEAD']),
                'python_version': lock['python']['version'], 'codex_version': lock['codex']['version'],
                'signed': False, 'dependencies_lock_sha256': sha256(ROOT / 'tools/windows/dependencies.lock.json')}
    (payload / 'granka-package.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print('Payload ready: ' + str(payload), flush=True)
    # The bundled interpreter, imports and native dialogs must work without system Python.
    run([runtime / 'python.exe', '-I', '-B', '-c',
         'import sys,frontmatter,markdown,yaml,tkinter;from PIL import Image;from tools import iimeter,telegram_media;print(sys.version);print(tkinter.Tcl().eval("info patchlevel"))'], cwd=payload / 'app')
    codex_version = run([codex / 'codex.exe', '--version'])
    if lock['codex']['version'] not in codex_version:
        raise ValueError('Bundled Codex version did not match the lock')
    suffix = '-candidate' if dirty else ''
    name = 'Granka-' + version + suffix
    portable = output / (name + '-Portable-x64.zip')
    if portable.exists():
        raise ValueError('Artifact exists; choose a fresh output directory')
    with zipfile.ZipFile(portable, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(payload.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(payload.parent))
    artifacts = [portable]
    if not portable_only:
        compiler = inno_compiler(lock, downloads, cache, iscc)
        run([compiler, '/Qp', '/DPayloadDir=' + str(payload), '/DAppVersion=' + version,
             '/DArtifactDir=' + str(output), '/DOutputName=' + name + '-Setup-x64', ROOT / 'tools/windows/granka.iss'])
        artifacts.append(output / (name + '-Setup-x64.exe'))
    checksums = ''.join(sha256(path) + '  ' + path.name + '\n' for path in artifacts)
    (output / 'SHA256SUMS.txt').write_text(checksums, encoding='ascii')
    receipt = {**manifest, 'payload': str(payload),
        'artifacts': [{'name': path.name, 'bytes': path.stat().st_size, 'sha256': sha256(path)} for path in artifacts]}
    (output / 'build-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '_dist/windows')
    parser.add_argument('--cache', type=Path, default=ROOT / '_dist/windows-cache')
    parser.add_argument('--iscc', type=Path)
    parser.add_argument('--allow-dirty', action='store_true')
    parser.add_argument('--portable-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.cache, args.allow_dirty, args.iscc, args.portable_only),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8')
    main()
