"""Entry point for the self-contained Windows distribution."""
from pathlib import Path
import sys

if __name__ == '__main__':
    from tools.desktop_runtime import main
    raise SystemExit(main(Path(__file__).resolve().parent))
