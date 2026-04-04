"""
browser.py — Cross-platform Chromium browser discovery and app-mode launch.

The only module that knows about platform-specific browser locations.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess


def _macos_candidates() -> tuple:
    return (
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
    )


def _linux_candidates() -> tuple:
    return (
        'google-chrome',
        'google-chrome-stable',
        'chromium-browser',
        'chromium',
        'brave-browser',
    )


def _windows_candidates() -> tuple:
    """Chromium-based browser paths on Windows.

    Edge is guaranteed present on Windows 10/11 and listed first.
    """
    pf = os.environ.get('PROGRAMFILES', r'C:\Program Files')
    pf86 = os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')
    local = os.environ.get('LOCALAPPDATA', '')
    return (
        os.path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(pf86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(pf86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(pf, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
        os.path.join(local, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
    )


def find_browser() -> str | None:
    """Return the path to a Chromium-based browser, or None."""
    system = platform.system()
    if system == 'Darwin':
        candidates = _macos_candidates()
    elif system == 'Windows':
        candidates = _windows_candidates()
    else:
        candidates = _linux_candidates()

    for app in candidates:
        if shutil.which(app) or os.path.exists(app):
            return app
    return None


def open_app_window(url: str) -> subprocess.Popen | None:
    """Launch a Chromium browser in app mode. Returns the Popen handle or None."""
    app = find_browser()
    if app is None:
        return None
    return subprocess.Popen([app, f'--app={url}', '--window-size=780,680'])
