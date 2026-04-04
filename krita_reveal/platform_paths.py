"""
platform_paths.py — Cross-platform Krita user data directory resolution.
"""

from __future__ import annotations

import os
import platform


def krita_data_dir() -> str:
    """Return the Krita user data directory for the current platform."""
    system = platform.system()
    if system == 'Darwin':
        return os.path.join(os.path.expanduser('~'),
                            'Library', 'Application Support', 'krita')
    elif system == 'Windows':
        return os.path.join(os.environ.get('APPDATA', ''), 'krita')
    else:
        # Linux / FreeBSD — respect XDG_DATA_HOME (Flatpak sets this)
        xdg = os.environ.get('XDG_DATA_HOME',
                             os.path.join(os.path.expanduser('~'), '.local', 'share'))
        return os.path.join(xdg, 'krita')
