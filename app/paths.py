"""Separate immutable application resources from private per-user job data."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def default_data():
    if not getattr(sys, 'frozen', False):
        return ROOT / 'data'
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    return base / 'format-hub' / 'data'
