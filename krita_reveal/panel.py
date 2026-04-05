"""
panel.py — RevealPanel: self-contained separation UI widget.

Layout (two-column):
  Left  — preview image (expandable) + swatch grid + Build Layers
  Right — knobs form + Separate Colors + status
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSpinBox, QDoubleSpinBox, QLabel, QFrame,
    QSizePolicy, QGridLayout, QFormLayout,
)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt
from PyQt5.QtGui import QImage, QPixmap, QColor

from .pipeline import (
    read_document_pixels, downsample_pixels, run_separation,
    make_original_rgb, make_posterized_rgb, make_solo_rgb,
)
from .layer_builder import build_separation_layers, build_debug_comparison

PREVIEW_MAX = 512


# ── Worker thread ─────────────────────────────────────────────────────────────

class _Worker(QThread):
    done  = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, pixels, width, height, target_colors, options):
        super().__init__()
        self._pixels        = pixels
        self._width         = width
        self._height        = height
        self._target_colors = target_colors
        self._options       = options

    def run(self):
        try:
            # Capture original BEFORE run_separation — preprocessing modifies pixels in-place
            orig_rgb = make_original_rgb(self._pixels, self._width, self._height)

            result = run_separation(
                self._pixels, self._width, self._height,
                self._target_colors, self._options,
            )
            result['_orig_rgb'] = orig_rgb
            result['_post_rgb'] = make_posterized_rgb(
                result['assignments'], result['palette'],
                self._width, self._height,
            )
            n_colors = len(result['palette'])
            total    = self._width * self._height
            counts   = [0] * n_colors
            for idx in result['assignments']:
                if idx < n_colors:
                    counts[idx] += 1
            result['_coverage'] = [100.0 * c / total for c in counts]
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ── Preview label with blink comparator ──────────────────────────────────────

class _PreviewLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(200)
        self.setStyleSheet('background: #1e1e1e;')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._orig_px      = None
        self._post_px      = None
        self._showing_orig = False

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(300)
        self._hold_timer.timeout.connect(self._start_blink)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._blink_tick)

    def set_images(self, orig_px, post_px):
        self._orig_px      = orig_px
        self._post_px      = post_px
        self._showing_orig = False
        self._refresh()
        self.setCursor(Qt.PointingHandCursor)

    def clear_images(self):
        self._blink_timer.stop()
        self._hold_timer.stop()
        self._orig_px = self._post_px = None
        self.clear()
        self.unsetCursor()

    def resizeEvent(self, e):
        self._refresh()
        super().resizeEvent(e)

    def _refresh(self):
        px = self._orig_px if self._showing_orig else self._post_px
        if px:
            super().setPixmap(
                px.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def mousePressEvent(self, e):
        if self._orig_px:
            self._hold_timer.start()

    def mouseReleaseEvent(self, e):
        if self._blink_timer.isActive():
            self._blink_timer.stop()
            self._showing_orig = False
            self._refresh()
        elif self._hold_timer.isActive():
            self._hold_timer.stop()
            self._showing_orig = not self._showing_orig
            self._refresh()

    def _start_blink(self):
        self._blink_timer.start()

    def _blink_tick(self):
        self._showing_orig = not self._showing_orig
        self._refresh()


# ── Swatch card ───────────────────────────────────────────────────────────────

class _SwatchCard(QFrame):
    clicked = pyqtSignal(int)   # emits colour index

    def __init__(self, r, g, b, hex_name, pct, idx):
        super().__init__()
        self._idx = idx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(1)

        self._block = QFrame()
        self._block.setFixedSize(19, 19)
        self._block.setStyleSheet(
            f'background-color: rgb({r},{g},{b});'
            f'border: 1px solid #555; border-radius: 2px;'
        )
        layout.addWidget(self._block, alignment=Qt.AlignHCenter)

        lbl = QLabel(f'{pct:.0f}%')
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet('color: #888; font-size: 10px;')
        layout.addWidget(lbl)

        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f'{hex_name}  {pct:.1f}%')

    def set_selected(self, on: bool):
        border = '#4a9eff' if on else '#555'
        style = self._block.styleSheet()
        # Replace border colour
        self._block.setStyleSheet(
            ';'.join(
                p if 'border' not in p else f'border: 2px solid {border}; border-radius: 2px'
                for p in style.split(';')
            )
        )

    def mousePressEvent(self, e):
        self.clicked.emit(self._idx)


# ── Main panel ────────────────────────────────────────────────────────────────

class RevealPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result          = None
        self._worker          = None
        self._proxy_w         = self._proxy_h = 0
        self._doc_w           = self._doc_h   = 0
        self._orig_px         = None
        self._post_px         = None
        self._selected_swatch = -1
        self._swatch_cards    = []
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ── Left column: preview + swatches ──────────────────────────────────
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        self._preview = _PreviewLabel()
        lv.addWidget(self._preview, stretch=1)

        self._preview_info = QLabel('')
        self._preview_info.setStyleSheet('color: #555; font-size: 9px;')
        self._preview_info.setAlignment(Qt.AlignRight)
        lv.addWidget(self._preview_info)

        lv.addWidget(self._hline())

        self._swatch_container = QWidget()
        self._swatch_grid = QGridLayout(self._swatch_container)
        self._swatch_grid.setContentsMargins(0, 0, 0, 0)
        self._swatch_grid.setSpacing(3)
        lv.addWidget(self._swatch_container)

        self._build_btn = QPushButton('Build Layers')
        self._build_btn.setVisible(False)
        self._build_btn.clicked.connect(self._on_build_layers)
        lv.addWidget(self._build_btn)

        self._debug_btn = QPushButton('Debug: Compare Assignments')
        self._debug_btn.setVisible(False)
        self._debug_btn.clicked.connect(self._on_debug_compare)
        lv.addWidget(self._debug_btn)

        root.addWidget(left, stretch=3)

        # ── Right column: knobs + button + status ─────────────────────────────
        right = QWidget()
        right.setFixedWidth(180)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)
        form.setLabelAlignment(Qt.AlignRight)

        self._spin_colors = QSpinBox()
        self._spin_colors.setRange(2, 12)
        self._spin_colors.setValue(6)
        form.addRow('Colors:', self._spin_colors)

        self._spin_density = QDoubleSpinBox()
        self._spin_density.setRange(0.0, 5.0)
        self._spin_density.setSingleStep(0.5)
        self._spin_density.setValue(0.5)
        self._spin_density.setSuffix('%')
        form.addRow('Min volume:', self._spin_density)

        self._spin_speckle = QSpinBox()
        self._spin_speckle.setRange(0, 30)
        self._spin_speckle.setValue(0)
        self._spin_speckle.setSuffix(' px')
        form.addRow('Speckle:', self._spin_speckle)

        self._spin_clamp = QSpinBox()
        self._spin_clamp.setRange(0, 40)
        self._spin_clamp.setValue(0)
        self._spin_clamp.setSuffix('%')
        form.addRow('Shadow clamp:', self._spin_clamp)

        rv.addLayout(form)
        rv.addWidget(self._hline())

        self._btn = QPushButton('Separate Colors')
        self._btn.clicked.connect(self._on_separate)
        rv.addWidget(self._btn)

        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        rv.addWidget(self._status)

        rv.addStretch()
        root.addWidget(right, stretch=0)

    def _hline(self):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    # ── Handlers ──────────────────────────────────────────────────────────────

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
        self._debug_btn.setVisible(False)
        self._clear_swatches()
        self._preview.clear_images()
        self._preview_info.setText('')
        self._set_status('Reading pixels…')

        try:
            pixels, w, h = read_document_pixels(doc)
            pixels, dw, dh = downsample_pixels(pixels, w, h, max_dim=800)
        except Exception as e:
            self._btn.setEnabled(True)
            self._set_status(f'Read error: {e}', error=True)
            return

        self._proxy_w, self._proxy_h = dw, dh
        self._doc_w,   self._doc_h   = w,  h
        self._set_status(f'Separating {dw}×{dh}…')

        options = {
            'density_floor':  self._spin_density.value() / 100.0,
            'speckle_rescue': self._spin_speckle.value(),
            'shadow_clamp':   self._spin_clamp.value(),
        }
        self._worker = _Worker(pixels, dw, dh, self._spin_colors.value(), options)
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

        pw, ph  = self._proxy_w, self._proxy_h
        self._orig_px = self._to_pixmap(result['_orig_rgb'], pw, ph)
        self._post_px = self._to_pixmap(result['_post_rgb'], pw, ph)
        self._selected_swatch = -1
        self._preview.set_images(self._orig_px, self._post_px)
        self._preview_info.setText(
            f'{pw}×{ph} · {meta["duration"]}s · click to compare'
        )

        self._show_swatches(result['palette'], result.get('_coverage', []))
        self._build_btn.setVisible(True)
        self._debug_btn.setVisible(True)

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
        self._set_status('Building layers…')
        try:
            n = build_separation_layers(doc, self._result)
            self._set_status(f'Created {n} layers.')
            self.window().hide()
        except Exception as e:
            self._set_status(f'Layer error: {e}', error=True)

    def _on_debug_compare(self):
        if not self._result:
            return
        doc = Krita.instance().activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return
        self._set_status('Building debug comparison layers…')
        try:
            msg = build_debug_comparison(doc, self._result)
            self._set_status(msg)
        except Exception as e:
            self._set_status(f'Debug error: {e}', error=True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _to_pixmap(self, rgb_bytes: bytes, width: int, height: int) -> QPixmap:
        # .copy() forces QImage to own its data — avoids black image from GC'd buffer
        img = QImage(rgb_bytes, width, height, width * 3, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(img)

    def _show_swatches(self, palette, coverage):
        self._clear_swatches()
        for idx, color in enumerate(palette):
            r, g, b  = color['r'], color['g'], color['b']
            hex_name = f'#{r:02X}{g:02X}{b:02X}'
            pct      = coverage[idx] if idx < len(coverage) else 0.0
            row, col = divmod(idx, 6)
            card = _SwatchCard(r, g, b, hex_name, pct, idx)
            card.clicked.connect(self._on_swatch_click)
            self._swatch_cards.append(card)
            self._swatch_grid.addWidget(card, row, col)

    def _on_swatch_click(self, idx):
        if not self._result:
            return
        if self._selected_swatch == idx:
            self._selected_swatch = -1
            self._preview.set_images(self._orig_px, self._post_px)
            for card in self._swatch_cards:
                card.set_selected(False)
        else:
            self._selected_swatch = idx
            r = self._result
            solo_px = self._to_pixmap(
                make_solo_rgb(r['assignments'], r['palette'], idx,
                              r['_proxy_w'], r['_proxy_h']),
                r['_proxy_w'], r['_proxy_h'],
            )
            self._preview.set_images(self._orig_px, solo_px)
            for card in self._swatch_cards:
                card.set_selected(card._idx == idx)

    def _clear_swatches(self):
        self._swatch_cards.clear()
        while self._swatch_grid.count():
            item = self._swatch_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _set_status(self, msg, error=False):
        color = '#cc4444' if error else '#888'
        self._status.setStyleSheet(f'color: {color};')
        self._status.setText(msg)
