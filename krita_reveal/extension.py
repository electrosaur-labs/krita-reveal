"""
extension.py — RevealExtension: registers Tools > Scripts > Reveal Separation…

Opens a local HTTP server and launches the browser-based UI.
"""

from __future__ import annotations

from krita import Extension


class RevealExtension(Extension):

    def __init__(self, parent):
        super().__init__(parent)
        self._server    = None
        self._processor = None

    def setup(self):
        pass

    def createActions(self, window):
        action = window.createAction(
            'reveal_separation',
            'Reveal Separation…',
            'tools/scripts',
        )
        action.triggered.connect(self._open)

    def _open(self):
        if self._server is None:
            from .server    import RevealServer
            from .processor import RevealCommandProcessor
            self._server    = RevealServer()
            self._processor = RevealCommandProcessor(self._server)
            self._server.start()

        url = f'http://127.0.0.1:{self._server.port}'
        if not self._open_app_window(url):
            import webbrowser
            webbrowser.open(url)

    def _open_app_window(self, url):
        """Try to open a dedicated app-mode window (no browser chrome). macOS only."""
        import subprocess, shutil
        for app in (
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
            '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
        ):
            if shutil.which(app) or __import__('os').path.exists(app):
                subprocess.Popen([app, f'--app={url}', '--window-size=780,680'])
                return True
        return False
