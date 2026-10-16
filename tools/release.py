"""Check release documentation and create an immutable annotated version tag."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True, encoding='utf-8').strip()


def check():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', version):
        raise ValueError('VERSION: expected MAJOR.MINOR.PATCH')
    for filename in ['CHANGELOG.md', 'DEVLOG.md']:
        content = (ROOT / filename).read_text(encoding='utf-8')
        if not re.search(r'^## \['+re.escape(version)+r'\] - \d{4}-\d{2}-\d{2}$', content, re.M):
            raise ValueError(filename + ': missing dated entry for '+version)
    html = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    if '__GRANKA_VERSION__' not in html:
        raise ValueError('Version must be displayed from VERSION')
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check VERSION, changelog and devlog (default)')
    parser.add_argument('--tag', action='store_true', help='Create annotated tag after all changes are committed; never pushes')
    args = parser.parse_args()
    version = check()
    if args.tag:
        if git('status', '--porcelain'):
            raise ValueError('Commit changes before tagging a release')
        tag = 'v'+version
        if tag in git('tag', '--list', tag).splitlines():
            if git('rev-parse', tag+'^{commit}') != git('rev-parse', 'HEAD'):
                raise ValueError('Existing tag points to another commit; increment VERSION')
        else:
            git('tag', '-a', tag, '-m', 'Granka '+version)
        print(tag + ' -> '+git('rev-parse', '--short', 'HEAD'))
    print('Release '+version+': documentation consistent')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
