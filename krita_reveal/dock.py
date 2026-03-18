"""
dock.py — RevealDock: the main plugin UI panel.

Layout:
  ┌─────────────────────────┐
  │  Reveal Separation       │
  │  Colors: [3] ──── [9]   │
  │      target: 6  [▲][▼]  │
  │  [ Separate Colors ]     │
  │─────────────────────────│
  │  ■ #A34F2B  ■ #2B7FA3   │  ← swatches (appear after run)
  │  ■ #F0E8C2  ...         │
  │─────────────────────────│
  │  status / error line     │
  └─────────────────────────┘
"""

from __future__ import annotations

from krita import DockWidget, Krita
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSpinBox, QLabel, QFrame, QSizePolicy, QGridLayout,
)
from PyQt5.QtGui import QColor
from PyQt5.QtCore import Qt, QThread, pyqtSignal

from .pipeline import read_document_pixels, downsample_pixels, run_separation
from .layer_builder import build_separation_layers

DOCKER_TITLE = 'Reveal Separation'


# ── Background worker ─────────────────────────────────────────────────────────

class _Worker(QThread):
    done    = pyqtSignal(dict)
    error   = pyqtSignal(str)

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


# ── Swatch widget ─────────────────────────────────────────────────────────────

class _Swatch(QFrame):
    def __init__(self, r: int, g: int, b: int, hex_name: str):
        super().__init__()
        self.setFixedSize(28, 28)
        self.setToolTip(hex_name)
        self.setStyleSheet(
            f'background-color: rgb({r},{g},{b});'
            f'border: 1px solid #555; border-radius: 3px;'
        )


# ── Dock panel ────────────────────────────────────────────────────────────────

class RevealDock(DockWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(DOCKER_TITLE)
        self._result  = None
        self._worker  = None
        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget(self)
        self.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Target colour count
        row = QHBoxLayout()
        row.addWidget(QLabel('Colors:'))
        self._spin = QSpinBox()
        self._spin.setRange(3, 9)
        self._spin.setValue(6)
        self._spin.setFixedWidth(52)
        row.addWidget(self._spin)
        row.addStretch()
        layout.addLayout(row)

        # Separate button
        self._btn = QPushButton('Separate Colors')
        self._btn.clicked.connect(self._on_separate)
        layout.addWidget(self._btn)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        # Swatch grid (populated after run)
        self._swatch_container = QWidget()
        self._swatch_grid = QGridLayout(self._swatch_container)
        self._swatch_grid.setContentsMargins(0, 0, 0, 0)
        self._swatch_grid.setSpacing(4)
        layout.addWidget(self._swatch_container)

        # Build layers button (hidden until result available)
        self._build_btn = QPushButton('Build Layers')
        self._build_btn.setVisible(False)
        self._build_btn.clicked.connect(self._on_build_layers)
        layout.addWidget(self._build_btn)

        # Status label
        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout.addWidget(self._status)

        layout.addStretch()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_separate(self):
        app = Krita.instance()
        doc = app.activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return

        if doc.colorModel() != 'L':
            self._set_status('Document must be in Lab colour mode.', error=True)
            return

        self._btn.setEnabled(False)
        self._build_btn.setVisible(False)
        self._clear_swatches()
        self._set_status('Reading pixels…')

        try:
            pixels, w, h = read_document_pixels(doc)
            pixels, dw, dh, *_ = downsample_pixels(pixels, w, h, max_dim=800)
        except Exception as e:
            self._btn.setEnabled(True)
            self._set_status(f'Read error: {e}', error=True)
            return

        self._set_status(f'Separating {dw}×{dh}…')
        self._worker = _Worker(pixels, dw, dh, self._spin.value())
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, result: dict):
        self._result = result
        self._btn.setEnabled(True)
        palette = result['palette']
        meta    = result['metadata']
        self._set_status(
            f'{meta["final_colors"]} colours  '
            f'({meta["duration"]}s)'
        )
        self._show_swatches(palette)
        self._build_btn.setVisible(True)

    def _on_error(self, msg: str):
        self._btn.setEnabled(True)
        self._set_status(f'Error: {msg}', error=True)

    def _on_build_layers(self):
        if not self._result:
            return
        app = Krita.instance()
        doc = app.activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return

        try:
            n = build_separation_layers(doc, self._result)
            self._set_status(f'Created {n} layers.')
        except Exception as e:
            self._set_status(f'Layer error: {e}', error=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _show_swatches(self, palette: list):
        self._clear_swatches()
        cols = 5
        for idx, color in enumerate(palette):
            r, g, b   = color['r'], color['g'], color['b']
            hex_name  = f'#{r:02X}{g:02X}{b:02X}'
            swatch    = _Swatch(r, g, b, hex_name)
            row, col  = divmod(idx, cols)
            self._swatch_grid.addWidget(swatch, row, col)

    def _clear_swatches(self):
        while self._swatch_grid.count():
            item = self._swatch_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _set_status(self, msg: str, error: bool = False):
        color = '#cc4444' if error else '#888'
        self._status.setStyleSheet(f'color: {color};')
        self._status.setText(msg)

    # Required by DockWidget
    def canvasChanged(self, canvas):
        pass
