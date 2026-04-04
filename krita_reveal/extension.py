"""
extension.py — RevealExtension: registers Tools > Scripts > Reveal Separation…

Opens a local HTTP server and launches the browser-based UI.
"""

from __future__ import annotations

from krita import Extension


class RevealExtension(Extension):

    def __init__(self, parent):
        super().__init__(parent)
        self._server       = None
        self._processor    = None
        self._browser      = None   # Popen handle for the app-mode window
        self._numpy_warned = False   # show numpy warning once per session

    def setup(self):
        from krita import Krita
        Krita.instance().notifier().applicationClosing.connect(self._on_closing)

    def _on_closing(self):
        if self._browser is not None:
            try:
                self._browser.terminate()
            except Exception:
                pass
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
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

        self._check_numpy()

        from .browser import open_app_window
        url = f'http://127.0.0.1:{self._server.port}'
        self._browser = open_app_window(url)
        if self._browser is None:
            import webbrowser
            webbrowser.open(url)

    def _check_numpy(self):
        """Warn once per session if numpy is not available."""
        if self._numpy_warned:
            return
        self._numpy_warned = True
        try:
            import numpy  # noqa: F401
        except ImportError:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(
                None,
                'numpy not found',
                'Color separation will be very slow without numpy '
                '(~2 minutes for large images).\n\n'
                'See plugin documentation for installation instructions.',
            )
