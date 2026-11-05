"""Per-user storage; neither platform needs administrator rights."""
import os
from pathlib import Path
import sys


def state_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'NBT-AAD-PC'
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'share')) / 'NBT-AAD-PC'
