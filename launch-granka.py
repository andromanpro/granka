"""Start the local server once and open Granka, including from a desktop shortcut."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from tools import config as environment

for stream in (sys.stdout, sys.stderr):
    if stream is not None:
        stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
PORT=environment.load()['server']['port']
URL = f"http://127.0.0.1:{PORT}/"
LOG = environment.STATE / "server.log"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_running():
    try:
        with OPENER.open(URL, timeout=5) as response:
            html = response.read(512 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Порт {PORT} занят другим HTTP-сервисом.") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return False
        raise RuntimeError(f"Не удалось проверить локальный сервер на порту {PORT}.") from exc
    if "<title>Гранка</title>" not in html or 'id="docsel"' not in html:
        raise RuntimeError(f"Порт {PORT} занят другим приложением.")
    return True


def ensure_server():
    if is_running():
        return "already running"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    python = Path(sys.executable).with_name("python.exe") if os.name=='nt' else Path(sys.executable)
    # python.exe has stdout for the server's UTF-8 setup; CREATE_NO_WINDOW hides it.
    with LOG.open("ab") as log:
        proc = subprocess.Popen(
            [str(python), "-B", "-u", str(HERE / "granka-server.py"), "--no-browser"],
            cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
        )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if is_running():
            return "started"
        if proc.poll() is not None:
            raise RuntimeError(f"Сервер завершился с кодом {proc.returncode}.")
        time.sleep(0.2)
    raise RuntimeError("Сервер не ответил за 20 секунд.")


def main():
    if os.name!='nt':
        state=ensure_server()
        if '--no-browser' not in sys.argv:webbrowser.open(URL)
        print(f'Granka: {state}; {URL}')
        return
    # Serialize rapid double-clicks; Windows releases the mutex after a crash too.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel.CreateMutexW(None, False, f"Local\\Granka-Launcher-{PORT}")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(mutex, 30000) in (0, 0x80)
        if not acquired:
            raise RuntimeError("Предыдущий запуск ещё не завершился. Повторите через несколько секунд.")
        state = ensure_server()
    finally:
        if acquired:
            kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)
    if sys.stdout:
        print(f"Granka: {state}; {URL}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = f"Не удалось открыть Гранку.\n\n{exc}\n\nЖурнал сервера: {LOG}"
        if sys.stderr:
            sys.stderr.write(message + "\n")
        if os.name=='nt' and "--no-browser" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, message, "Гранка", 0x10)
        sys.exit(1)
