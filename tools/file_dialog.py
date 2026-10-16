"""Native dialog in a separate main-thread process, opened only by a user click."""
import json
from pathlib import Path
import subprocess
import sys
import threading

_dialog_lock = threading.Lock()


def choose_local_path(kind):
    if kind not in {'file', 'folder'}:
        raise ValueError('Выбери файл или папку')
    if not _dialog_lock.acquire(blocking=False):
        raise ValueError('Окно выбора уже открыто')
    try:
        try:
            result = subprocess.run(
                [sys.executable, '-B', str(Path(__file__).resolve()), kind],
                capture_output=True, timeout=300,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise ValueError('Время выбора истекло. Выбери файл ещё раз.') from None
        try:
            data = json.loads(result.stdout.decode('utf-8'))
            if result.returncode or data.get('error'):
                raise ValueError()
            return data['path']
        except (ValueError, KeyError):
            raise ValueError('Системное окно недоступно. В библиотеке можно указать путь вручную.') from None
    finally:
        _dialog_lock.release()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        try:
            window.attributes('-topmost', True)
            if sys.argv[1] == 'folder':
                path = filedialog.askdirectory(parent=window, title='Гранка — добавить папку', mustexist=True)
            else:
                path = filedialog.askopenfilename(parent=window, title='Гранка — открыть статью',
                    filetypes=[('Статьи Markdown и Гранки', '*.md *.MD *.granka.json')])
            print(json.dumps({'path': path or None}, ensure_ascii=False))
        finally:
            window.destroy()
    except Exception:
        print(json.dumps({'error': 'native dialog unavailable'}))
        sys.exit(1)


if __name__ == '__main__':
    main()
