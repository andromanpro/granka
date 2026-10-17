"""Silent install/repair/uninstall in a verified temporary target, preserving test state."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import winreg

UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\{247B6618-4778-4CBE-B529-8EDC5ED0BA10}_is1'


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def existing_installation():
    for access in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ | access):
                return True
        except FileNotFoundError:
            continue
    return False


def verify(installer, output):
    installer, output = Path(installer).resolve(), Path(output).resolve()
    if existing_installation():
        raise RuntimeError('An existing Granka installation is registered; leave it untouched and use another account/VM.')
    output.mkdir(parents=True, exist_ok=True)
    target = (output / ('install-' + uuid.uuid4().hex[:12]) / 'Программа Гранка').resolve()
    if not target.is_relative_to(output) or target.exists():
        raise ValueError('Installer test target must be a new directory inside the output workspace')
    state = output / ('user-state-' + uuid.uuid4().hex[:12])
    install_log = output / 'install.log'
    bootstrap_temp = output / 'bootstrap-temp'
    bootstrap_temp.mkdir(exist_ok=True)
    env = {**os.environ, 'TEMP': str(bootstrap_temp), 'TMP': str(bootstrap_temp)}

    def install(log):
        result = subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            '/SP-', '/NOICONS', '/TASKS=', '/DIR=' + str(target), '/LOG=' + str(log)],
            capture_output=True, timeout=240, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('Silent installation failed with code ' + str(result.returncode) + '; inspect ' + str(log))
        if not (target / 'Granka.exe').is_file():
            raise RuntimeError('Installer did not create the launcher')

    install(install_log)
    module_file = Path(__file__).with_name('smoke.py')
    spec = importlib.util.spec_from_file_location('granka_installed_smoke', module_file)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    result = smoke.check(target, output / 'installed-smoke', state=state)
    preserved = {str(path.relative_to(state)): file_hash(path) for path in state.rglob('*') if path.is_file()}
    install(output / 'repair.log')
    assert all((state / name).is_file() and file_hash(state / name) == digest for name, digest in preserved.items())
    uninstaller = target / 'unins000.exe'
    if not uninstaller.is_file():
        raise RuntimeError('Uninstaller was not installed')
    uninstall = subprocess.run([str(uninstaller), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
        '/LOG=' + str(output / 'uninstall.log')], capture_output=True, timeout=120,
        env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if uninstall.returncode:
        raise RuntimeError('Uninstall failed with code ' + str(uninstall.returncode))
    assert not (target / 'Granka.exe').exists()
    assert not existing_installation()
    assert all((state / name).is_file() and file_hash(state / name) == digest for name, digest in preserved.items())
    receipt = {'status': 'passed', 'installer': str(installer), 'installation_target': str(target),
        'state': str(state), 'installed_smoke': result['status'], 'repair_preserved_files': len(preserved),
        'uninstall_preserved_files': len(preserved), 'launcher_removed': True, 'uninstall_registration_removed': True,
        'real_account_login_tested': False, 'interactive_wizard_tested': False}
    (output / 'installer-verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--installer', type=Path)
    source.add_argument('--receipt', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    installer = args.installer
    if args.receipt:
        receipt = json.loads(args.receipt.read_text(encoding='utf-8'))
        installer = args.receipt.parent / next(a['name'] for a in receipt['artifacts'] if a['name'].endswith('-Setup-x64.exe'))
    print(json.dumps(verify(installer, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8')
    main()
