"""Per-user storage; neither platform needs administrator rights."""
import os
from pathlib import Path
import sys


def state_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'NBT-AAD-PC'
    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        return Path(local_app_data) / 'NBT-AAD-PC'
    return Path.home() / '.local' / 'share' / 'NBT-AAD-PC'
