"""
dock.py — RevealDock: main plugin UI panel.

Layout:
  Colors: [6 ▲▼]
  [ Separate Colors ]
  ─────────────────
  ■ ■ ■ ■ ■   (swatches after run)
  [ Build Layers ]
  status line
"""

from __future__ import annotations

from krita import DockWidget
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSpinBox, QLabel, QFrame, QSizePolicy, QGridLayout,
)
from PyQt5.QtCore import QThread, pyqtSignal

from .pipeline import read_document_pixels, downsample_pixels, run_separation
from .layer_builder import build_separation_layers

DOCKER_TITLE = 'Reveal Separation'


class _Worker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, pixels, width, height, target_colors):
        super().__init__()
        self._pixels        = pixels
        self._width         = width
        self._height        = height
        self._target_colors = target_colors

    def run(self):
        try:
            result = run_separation(self._pixels, self._width, self._height, self._target_colors)
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class _Swatch(QFrame):
    def __init__(self, r, g, b, hex_name):
        super().__init__()
        self.setFixedSize(28, 28)
        self.setToolTip(hex_name)
        self.setStyleSheet(
            f'background-color: rgb({r},{g},{b});'
            f'border: 1px solid #555; border-radius: 3px;'
        )


class RevealDock(DockWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(DOCKER_TITLE)
        self._result = None
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        root = QWidget(self)
        self.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.addWidget(QLabel('Colors:'))
        self._spin = QSpinBox()
        self._spin.setRange(3, 9)
        self._spin.setValue(6)
        self._spin.setFixedWidth(52)
        row.addWidget(self._spin)
        row.addStretch()
        layout.addLayout(row)

        self._btn = QPushButton('Separate Colors')
        self._btn.clicked.connect(self._on_separate)
        layout.addWidget(self._btn)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        self._swatch_container = QWidget()
        self._swatch_grid = QGridLayout(self._swatch_container)
        self._swatch_grid.setContentsMargins(0, 0, 0, 0)
        self._swatch_grid.setSpacing(4)
        layout.addWidget(self._swatch_container)

        self._build_btn = QPushButton('Build Layers')
        self._build_btn.setVisible(False)
        self._build_btn.clicked.connect(self._on_build_layers)
        layout.addWidget(self._build_btn)

        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout.addWidget(self._status)

        layout.addStretch()

    def _on_separate(self):
        app = Krita.instance()
        doc = app.activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return

        if doc.colorModel() != 'LABA':
            self._set_status('Document must be in Lab colour mode.', error=True)
            return

        self._btn.setEnabled(False)
        self._build_btn.setVisible(False)
        self._clear_swatches()
        self._set_status('Reading pixels…')

        try:
            pixels, w, h = read_document_pixels(doc)
            pixels, dw, dh = downsample_pixels(pixels, w, h, max_dim=800)
        except Exception as e:
            self._btn.setEnabled(True)
            self._set_status(f'Read error: {e}', error=True)
            return

        self._proxy_w = dw
        self._proxy_h = dh
        self._doc_w   = w
        self._doc_h   = h
        self._set_status(f'Separating {dw}×{dh}…')
        self._worker = _Worker(pixels, dw, dh, self._spin.value())
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, result):
        result['_proxy_w'] = self._proxy_w
        result['_proxy_h'] = self._proxy_h
        result['_doc_w']   = self._doc_w
        result['_doc_h']   = self._doc_h
        self._result = result
        self._btn.setEnabled(True)
        meta = result['metadata']
        self._set_status(f'{meta["final_colors"]} colours  ({meta["duration"]}s)')
        self._show_swatches(result['palette'])
        self._build_btn.setVisible(True)

    def _on_error(self, msg):
        self._btn.setEnabled(True)
        self._set_status(f'Error: {msg}', error=True)

    def _on_build_layers(self):
        if not self._result:
            return
        doc = Krita.instance().activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return
        try:
            n = build_separation_layers(doc, self._result)
            self._set_status(f'Created {n} layers.')
        except Exception as e:
            self._set_status(f'Layer error: {e}', error=True)

    def _show_swatches(self, palette):
        self._clear_swatches()
        for idx, color in enumerate(palette):
            r, g, b = color['r'], color['g'], color['b']
            hex_name = f'#{r:02X}{g:02X}{b:02X}'
            row, col = divmod(idx, 5)
            self._swatch_grid.addWidget(_Swatch(r, g, b, hex_name), row, col)

    def _clear_swatches(self):
        while self._swatch_grid.count():
            item = self._swatch_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _set_status(self, msg, error=False):
        color = '#cc4444' if error else '#888'
        self._status.setStyleSheet(f'color: {color};')
        self._status.setText(msg)

    def canvasChanged(self, canvas):
        pass
