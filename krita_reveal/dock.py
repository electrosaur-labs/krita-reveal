"""
dock.py — RevealDock: native PyQt5 docker for Reveal colour separation.

Replaces the browser-based HTML UI. Communicates with the engine directly
via a QThread worker (no HTTP server, no polling).

Controls are grouped into three sections: Basic (always visible),
Screen Printing (collapsible), and Advanced (collapsible).

Layout:
  Left panel (flex):
    [preview image — click to compare, hold to blink]
    [status bar]
    [palette surgeon — swatch grid with click/ctrl/alt interactions]

  Right panel (224px):
    [header: Basic | ? | Reset]
    [archetype selector]
    [knobs panel — 31 controls in collapsible groups]
    [Separate Colors / Build Layers buttons]
    [status line]
"""

from __future__ import annotations

import queue

from krita import DockWidget

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy, QGridLayout, QSlider, QComboBox, QCheckBox,
    QScrollArea, QLineEdit, QGroupBox, QApplication,
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal, QObject, QPoint, QRect
from PyQt5.QtGui import QImage, QPixmap, QColor, QPainter, QPen, QLinearGradient, QPalette

from .pipeline import (
    downsample_pixels_smooth, make_original_rgb, make_posterized_rgb,
    make_solo_rgb, read_document_raw, run_separation,
)
from .layer_builder import build_separation_layers

DOCKER_TITLE = 'Reveal Separation'

# ── Defaults (mirrors index.html DEFAULTS) ────────────────────────────────

DEFAULTS = {
    'colors': 6,
    'density': 0.5,
    'speckle': 0,
    'clamp': 0,
    'vibrancy_boost': 1.4,
    'vibrancy_mode': 'moderate',
    'l_weight': 1.2,
    'c_weight': 2.0,
    'black_bias': 3.0,
    'shadow_point': 15,
    'palette_reduction': 6.0,
    'enable_palette_reduction': True,
    'enable_hue_gap': True,
    'hue_lock_angle': 20,
    'preserve_white': True,
    'preserve_black': True,
    'preprocessing': 'off',
    'engine_type': 'reveal-mk1.5',
    'color_mode': 'color',
    'dither_type': 'none',
    'distance_metric': 'cie76',
    'centroid_strategy': 'ROBUST_SALIENCY',
    'split_mode': 'median',
    'quantizer': 'wu',
    'neutral_sovereignty': 0,
    'chroma_gate': 1.0,
    'highlight_threshold': 90,
    'highlight_boost': 1.5,
    'median_pass': False,
    'detail_rescue': 0,
    'proxy_resolution': 800,
    'mesh_size': 230,
    'trap_size': 0,
    'substrate_mode': 'none',
    'substrate_tolerance': 2.0,
    'ignore_transparent': True,
}


# ── Worker thread ──────────────────────────────────────────────────────────

from PyQt5.QtCore import QThread, pyqtSignal as Signal


class _Worker(QThread):
    done = Signal(dict)
    error = Signal(str)

    def __init__(self, pixels, width, height, target_colors, options):
        super().__init__()
        self._pixels = pixels
        self._width = width
        self._height = height
        self._target_colors = target_colors
        self._options = options

    def run(self):
        try:
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
            n = len(result['palette'])
            total = self._width * self._height
            counts = [0] * n
            for idx in result['assignments']:
                if idx < n:
                    counts[idx] += 1
            result['_coverage'] = [100.0 * c / total for c in counts]
            from .suggested_color_analyzer import SuggestedColorAnalyzer
            substrate_mode = self._options.get('substrate_mode', 'none')
            palette_lab = result.get('palette_lab', [])
            suggestions = SuggestedColorAnalyzer.analyze(
                self._pixels, self._width, self._height,
                palette_lab, substrate_mode=substrate_mode,
            )
            result['_suggestions'] = suggestions
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


# ── Preview label with blink comparator ────────────────────────────────────

class _LoupeOverlay(QWidget):
    """Circular magnifier overlay for the preview image."""

    RADIUS = 60       # overlay radius in pixels
    ZOOM = 3          # magnification factor

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.RADIUS * 2, self.RADIUS * 2)
        self.setVisible(False)
        self._loupe_px = None

    def update_loupe(self, cursor_pos, source_px, label_widget):
        if source_px is None:
            self.setVisible(False)
            return

        # Map cursor position to source pixmap coordinates.
        # The label scales the pixmap with KeepAspectRatio and centers it.
        img_w, img_h = source_px.width(), source_px.height()
        lbl_w, lbl_h = label_widget.width(), label_widget.height()

        # Compute the displayed size by replicating Qt's KeepAspectRatio scaling
        scale = min(lbl_w / img_w, lbl_h / img_h)
        disp_w = int(img_w * scale)
        disp_h = int(img_h * scale)

        # The label centers the pixmap
        off_x = (lbl_w - disp_w) / 2.0
        off_y = (lbl_h - disp_h) / 2.0

        # Cursor pos relative to the displayed pixmap
        rel_x = cursor_pos.x() - off_x
        rel_y = cursor_pos.y() - off_y

        if rel_x < 0 or rel_y < 0 or rel_x >= disp_w or rel_y >= disp_h:
            self.setVisible(False)
            return

        # Map to source coordinates
        src_x = rel_x / scale
        src_y = rel_y / scale

        # Extract region from source (sample_r in source-pixel units)
        sample_r = self.RADIUS / (self.ZOOM * scale)
        rx = max(0, int(src_x - sample_r))
        ry = max(0, int(src_y - sample_r))
        rw = min(int(sample_r * 2), img_w - rx)
        rh = min(int(sample_r * 2), img_h - ry)
        if rw <= 0 or rh <= 0:
            self.setVisible(False)
            return

        region = source_px.copy(rx, ry, rw, rh)
        self._loupe_px = region.scaled(
            self.RADIUS * 2, self.RADIUS * 2,
            Qt.IgnoreAspectRatio, Qt.SmoothTransformation,
        )

        # Position: offset above-right of cursor to avoid occlusion
        x = cursor_pos.x() + 20
        y = cursor_pos.y() - self.RADIUS * 2 - 10
        # Keep within parent bounds
        parent = self.parentWidget()
        if parent:
            if x + self.RADIUS * 2 > parent.width():
                x = cursor_pos.x() - self.RADIUS * 2 - 20
            if y < 0:
                y = cursor_pos.y() + 20
        self.move(x, y)
        self.setVisible(True)
        self.update()

    def paintEvent(self, e):
        if self._loupe_px is None:
            return
        from PyQt5.QtGui import QPainterPath
        d = self.RADIUS * 2
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))  # clear to transparent
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QPainter.Antialiasing)
        # Circular clip — only the circle is painted
        path = QPainterPath()
        path.addEllipse(0, 0, d, d)
        p.setClipPath(path)
        p.drawPixmap(0, 0, self._loupe_px)
        # Border
        p.setClipping(False)
        p.setPen(QPen(QColor('#888'), 2))
        p.drawEllipse(1, 1, d - 2, d - 2)
        # Crosshair
        cx, cy = self.RADIUS, self.RADIUS
        p.setPen(QPen(QColor(255, 255, 255, 100), 1))
        p.drawLine(cx - 5, cy, cx + 5, cy)
        p.drawLine(cx, cy - 5, cx, cy + 5)
        p.end()


class _PreviewLabel(QLabel):
    """Shows posterized preview; click to toggle original/solo, hold to blink."""

    clicked = Signal()  # emitted on quick click (not hold/blink)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(100)
        self.setStyleSheet('background: #1e1e1e; border: 1px solid #444; border-radius: 3px;')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._orig_px = None
        self._post_px = None
        self._showing_orig = False
        self._overlay_text = ''

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(300)
        self._hold_timer.timeout.connect(self._start_blink)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._blink_tick)

        self._loupe = _LoupeOverlay(self)

    def set_images(self, orig_px: QPixmap, post_px: QPixmap):
        self._orig_px = orig_px
        self._post_px = post_px
        self._showing_orig = False
        self._show_pixmap(post_px)
        self.setCursor(Qt.PointingHandCursor)

    def clear_images(self):
        self._blink_timer.stop()
        self._hold_timer.stop()
        self._orig_px = self._post_px = None
        self.clear()
        self.unsetCursor()
        self._loupe.setVisible(False)

    def update_post(self, post_px: QPixmap):
        self._post_px = post_px
        if not self._showing_orig:
            self._show_pixmap(post_px)

    def _show_pixmap(self, px: QPixmap):
        if px is None:
            return
        scaled = px.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)

    def _current_source_px(self):
        """Return the pixmap currently being displayed (for loupe)."""
        if self._showing_orig:
            return self._orig_px
        return self._post_px

    def set_overlay_text(self, text):
        self._overlay_text = text
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._post_px and not self._showing_orig:
            self._show_pixmap(self._post_px)
        elif self._orig_px and self._showing_orig:
            self._show_pixmap(self._orig_px)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._overlay_text:
            from PyQt5.QtGui import QFont
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            font = QFont('sans-serif', 13)
            font.setBold(True)
            p.setFont(font)
            # Semi-transparent background pill
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(self._overlay_text) + 24
            th = fm.height() + 12
            rx = (self.width() - tw) // 2
            ry = (self.height() - th) // 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 180))
            p.drawRoundedRect(rx, ry, tw, th, 6, 6)
            p.setPen(QColor('#e0e0e0'))
            p.drawText(QRect(rx, ry, tw, th), Qt.AlignCenter, self._overlay_text)
            p.end()

    def mousePressEvent(self, e):
        self._loupe.setVisible(False)
        if self._orig_px:
            self._hold_timer.start()

    def mouseReleaseEvent(self, e):
        if self._blink_timer.isActive():
            self._blink_timer.stop()
            self._showing_orig = False
            self._show_pixmap(self._post_px)
        elif self._hold_timer.isActive():
            self._hold_timer.stop()
            self.clicked.emit()

    def mouseMoveEvent(self, e):
        src = self._current_source_px()
        if src and not self._blink_timer.isActive() and not self._hold_timer.isActive():
            self._loupe.update_loupe(e.pos(), src, self)
        else:
            self._loupe.setVisible(False)

    def leaveEvent(self, e):
        self._loupe.setVisible(False)
        if self._blink_timer.isActive():
            self._blink_timer.stop()
        if self._hold_timer.isActive():
            self._hold_timer.stop()
        if self._showing_orig:
            self._showing_orig = False
            self._show_pixmap(self._post_px)

    def _start_blink(self):
        self._blink_timer.start()

    def _blink_tick(self):
        self._showing_orig = not self._showing_orig
        self._show_pixmap(self._orig_px if self._showing_orig else self._post_px)


# ── Lab Color Picker ──────────────────────────────────────────────────────

from pyreveal.color.encoding import lab_to_rgb, rgb_to_lab


def _lab_to_rgb_fast(L, a, b):
    """Fast Lab→RGB for canvas rendering (simple clamp, no iterative gamut mapping)."""
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200

    delta = 6 / 29
    xv = (fx ** 3 if fx > delta else 3 * delta * delta * (fx - 4 / 29)) * 0.95047
    yv = (fy ** 3 if fy > delta else 3 * delta * delta * (fy - 4 / 29))
    zv = (fz ** 3 if fz > delta else 3 * delta * delta * (fz - 4 / 29)) * 1.08883

    rl = xv *  3.2404542 + yv * -1.5371385 + zv * -0.4985314
    gl = xv * -0.9692660 + yv *  1.8760108 + zv *  0.0415560
    bl = xv *  0.0556434 + yv * -0.2040259 + zv *  1.0572252

    def gamma(v):
        return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055

    r = max(0, min(255, round(gamma(max(0.0, rl)) * 255)))
    g = max(0, min(255, round(gamma(max(0.0, gl)) * 255)))
    bv = max(0, min(255, round(gamma(max(0.0, bl)) * 255)))
    in_gamut = (0.0 <= rl <= 1.0 and 0.0 <= gl <= 1.0 and 0.0 <= bl <= 1.0)
    return r, g, bv, in_gamut


class _LabPicker(QWidget):
    """Compact Lab color picker: a/b chroma plane + L lightness strip."""

    colorPicked = Signal(int, int, int)  # r, g, b

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setFixedWidth(320)
        self.setStyleSheet(
            'background: #2a2a2a; border: 1px solid #555; border-radius: 5px;'
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._ab_widget = _ABCanvas()
        self._ab_widget.setFixedSize(300, 230)
        self._ab_widget.changed.connect(self._on_ab_changed)
        layout.addWidget(self._ab_widget)

        self._l_widget = _LStrip()
        self._l_widget.setFixedSize(300, 20)
        self._l_widget.changed.connect(self._on_l_changed)
        layout.addWidget(self._l_widget)

        row = QHBoxLayout()
        row.setSpacing(6)

        self._preview = QFrame()
        self._preview.setFixedSize(32, 32)
        self._preview.setStyleSheet('border: 1px solid #555; border-radius: 3px;')
        row.addWidget(self._preview)

        self._hex_input = QLineEdit()
        self._hex_input.setMaxLength(7)
        self._hex_input.setPlaceholderText('#RRGGBB')
        self._hex_input.setStyleSheet(
            'background: #1a1a1a; border: 1px solid #555; color: #fff; '
            'font-size: 12px; padding: 4px 6px; border-radius: 3px; font-family: monospace;'
        )
        self._hex_input.textEdited.connect(self._on_hex_edited)
        self._hex_input.returnPressed.connect(self._apply)
        row.addWidget(self._hex_input)

        cancel_btn = QPushButton('Cancel')
        cancel_btn.setFixedWidth(52)
        cancel_btn.setStyleSheet(
            'font-size: 10px; padding: 3px 8px; background: #2a2a2a; '
            'border: 1px solid #555; color: #aaa; border-radius: 3px;'
        )
        cancel_btn.clicked.connect(self.hide)
        row.addWidget(cancel_btn)

        ok_btn = QPushButton('OK')
        ok_btn.setFixedWidth(44)
        ok_btn.setStyleSheet(
            'font-size: 10px; padding: 3px 8px; background: #1a2a3a; '
            'border: 1px solid #3070a0; color: #60b0ff; border-radius: 3px;'
        )
        ok_btn.clicked.connect(self._apply)
        row.addWidget(ok_btn)

        layout.addLayout(row)

        self._L = 50.0
        self._a = 0.0
        self._b = 0.0
        self._target_idx = -1

    def open_for(self, idx, hex_color, anchor_widget):
        self._target_idx = idx
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        self._L, self._a, self._b = rgb_to_lab(r, g, b)
        self._update_all()

        pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 4))
        screen = QApplication.primaryScreen().availableGeometry()
        if pos.y() + self.sizeHint().height() > screen.bottom():
            pos = anchor_widget.mapToGlobal(QPoint(0, -self.sizeHint().height() - 4))
        self.move(pos)
        self.show()

    def _update_all(self, skip_hex=False):
        self._ab_widget.set_lab(self._L, self._a, self._b)
        self._l_widget.set_lab(self._L, self._a, self._b)
        r, g, b = lab_to_rgb(self._L, self._a, self._b)
        hex_str = f'#{r:02X}{g:02X}{b:02X}'
        self._preview.setStyleSheet(
            f'background: {hex_str}; border: 1px solid #555; border-radius: 3px;'
        )
        if not skip_hex:
            self._hex_input.setText(hex_str)

    def _on_ab_changed(self, a, b):
        self._a = a
        self._b = b
        self._update_all()

    def _on_l_changed(self, L):
        self._L = L
        self._update_all()

    def _on_hex_edited(self, text):
        v = text.strip()
        if not v.startswith('#'):
            v = '#' + v
        if len(v) == 7:
            try:
                r = int(v[1:3], 16)
                g = int(v[3:5], 16)
                b = int(v[5:7], 16)
                self._L, self._a, self._b = rgb_to_lab(r, g, b)
                self._update_all(skip_hex=True)
            except ValueError:
                pass

    def _apply(self):
        r, g, b = lab_to_rgb(self._L, self._a, self._b)
        idx = self._target_idx
        self.hide()
        if idx >= 0:
            self.colorPicked.emit(r, g, b)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


class _ABCanvas(QWidget):
    """a/b chroma plane at current L — the core of the Lab picker."""
    changed = Signal(float, float)  # a, b

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CrossCursor)
        self._L = 50.0
        self._a = 0.0
        self._b = 0.0
        self._dragging = False
        self._cache_img = None
        self._cache_L = None

    def set_lab(self, L, a, b):
        self._L = L
        self._a = a
        self._b = b
        if self._cache_L != round(L, 1):
            self._cache_img = None  # invalidate
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()

        # Render a/b plane image (cached per L value)
        L_rounded = round(self._L, 1)
        if self._cache_img is None or self._cache_L != L_rounded:
            self._cache_L = L_rounded
            buf = bytearray(w * h * 3)
            for py in range(h):
                bv = 127.0 - (py / (h - 1)) * 254.0  # top = +127, bottom = -127
                for px in range(w):
                    av = -128.0 + (px / (w - 1)) * 255.0  # left = -128, right = +127
                    r, g, b, in_gamut = _lab_to_rgb_fast(self._L, av, bv)
                    off = (py * w + px) * 3
                    if in_gamut:
                        buf[off] = r
                        buf[off + 1] = g
                        buf[off + 2] = b
                    else:
                        # Dim out-of-gamut: show color at 30% over dark gray
                        buf[off] = (r * 30 + 40 * 70) // 100
                        buf[off + 1] = (g * 30 + 40 * 70) // 100
                        buf[off + 2] = (b * 30 + 40 * 70) // 100
            self._cache_img = QImage(
                bytes(buf), w, h, w * 3, QImage.Format_RGB888
            ).copy()

        p.drawImage(0, 0, self._cache_img)

        # Cursor at current a, b
        cx = int(((self._a + 128) / 255) * (w - 1))
        cy = int(((127 - self._b) / 254) * (h - 1))
        pen_color = QColor(0, 0, 0) if self._L > 50 else QColor(255, 255, 255)
        p.setPen(QPen(pen_color, 1.5))
        p.drawEllipse(QPoint(cx, cy), 5, 5)

    def _pick(self, e):
        w, h = self.width(), self.height()
        self._a = -128.0 + (max(0, min(w - 1, e.x())) / (w - 1)) * 255.0
        self._b = 127.0 - (max(0, min(h - 1, e.y())) / (h - 1)) * 254.0
        self.update()
        self.changed.emit(self._a, self._b)

    def mousePressEvent(self, e):
        self._dragging = True
        self._pick(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._pick(e)

    def mouseReleaseEvent(self, e):
        self._dragging = False


class _LStrip(QWidget):
    """Horizontal L (lightness) strip."""
    changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setCursor(Qt.CrossCursor)
        self._L = 50.0
        self._a = 0.0
        self._b = 0.0
        self._dragging = False

    def set_lab(self, L, a, b):
        self._L = L
        self._a = a
        self._b = b
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        # Draw L gradient at current a, b
        for px in range(w):
            lv = (px / (w - 1)) * 100.0
            r, g, b, _ = _lab_to_rgb_fast(lv, self._a, self._b)
            p.setPen(QColor(r, g, b))
            p.drawLine(px, 0, px, h - 1)

        # Cursor
        cx = int((self._L / 100.0) * (w - 1))
        p.fillRect(cx - 2, 0, 4, h, QColor(255, 255, 255))
        p.setPen(QPen(QColor(0, 0, 0), 0.5))
        p.drawRect(cx - 2, 0, 4, h)

    def _pick(self, e):
        w = self.width()
        self._L = max(0.0, min(100.0, (e.x() / (w - 1)) * 100.0))
        self.update()
        self.changed.emit(self._L)

    def mousePressEvent(self, e):
        self._dragging = True
        self._pick(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._pick(e)

    def mouseReleaseEvent(self, e):
        self._dragging = False


# ── Slider with fill gradient + value display + revert ─────────────────────

class _RevealSlider(QWidget):
    """Slider with value display, revert button, and dirty tracking."""

    valueChanged = Signal()  # emitted on user interaction only (not programmatic)

    def __init__(self, key, label, min_val, max_val, default, step, fmt_fn,
                 help_text=None):
        super().__init__()
        self._key = key
        self._default = default
        self._archetype_default = default
        self._fmt = fmt_fn
        self._programmatic = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)

        # Top row: label + revert + value
        row = QHBoxLayout()
        row.setSpacing(4)

        self._label = QLabel(label)
        self._label.setStyleSheet('color: #e0e0e0; font-size: 11px;')
        row.addWidget(self._label, 1)

        self._revert_btn = QPushButton('↻')
        self._revert_btn.setFixedSize(18, 18)
        self._revert_btn.setStyleSheet(
            'QPushButton { background: none; border: none; color: #666; font-size: 13px; }'
            'QPushButton:hover { color: #aaa; }'
        )
        self._revert_btn.setVisible(False)
        self._revert_btn.clicked.connect(self._revert)
        row.addWidget(self._revert_btn)

        self._val_label = QLabel()
        self._val_label.setStyleSheet('color: #ccc; font-size: 11px;')
        self._val_label.setMinimumWidth(36)
        self._val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._val_label)

        layout.addLayout(row)

        # Slider — we use integer slider and scale
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setFocusPolicy(Qt.StrongFocus)
        self._slider.installEventFilter(self)
        self._slider.setMinimumHeight(18)
        self._step = step
        self._min = min_val
        self._max = max_val
        # Map float range to integer ticks
        self._ticks = round((max_val - min_val) / step)
        self._slider.setRange(0, self._ticks)
        self._slider.setValue(self._val_to_tick(default))
        self._slider.valueChanged.connect(self._on_slider_changed)

        layout.addWidget(self._slider)

        # Help text (hidden by default)
        if help_text:
            self._help = QLabel(help_text)
            self._help.setWordWrap(True)
            self._help.setStyleSheet('color: #777; font-size: 9px;')
            self._help.setVisible(False)
            layout.addWidget(self._help)
        else:
            self._help = None

        self._update_display()

    def _val_to_tick(self, val):
        return round((val - self._min) / self._step)

    def _tick_to_val(self):
        return self._min + self._slider.value() * self._step

    def value(self):
        return round(self._tick_to_val(), 6)

    def set_value(self, val, programmatic=True):
        self._programmatic = programmatic
        self._slider.setValue(self._val_to_tick(val))
        self._programmatic = False

    def set_archetype_default(self, val):
        self._archetype_default = val

    def _on_slider_changed(self):
        self._update_display()
        if not self._programmatic:
            self.valueChanged.emit()

    def _update_display(self):
        val = self.value()
        self._val_label.setText(self._fmt(val))
        is_dirty = abs(val - self._archetype_default) > self._step * 0.5
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet(
                'QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }'
                'QPushButton:hover { color: #80c0ff; }'
            )

    def _revert(self):
        self.set_value(self._archetype_default, programmatic=False)

    def eventFilter(self, obj, event):
        if obj is self._slider and event.type() == event.Wheel:
            if not self._slider.hasFocus():
                event.ignore()
                return True
        return super().eventFilter(obj, event)

    def set_help_visible(self, visible):
        if self._help:
            self._help.setVisible(visible)


# ── Select (combo) with revert ─────────────────────────────────────────────

class _RevealCombo(QWidget):
    """QComboBox with label and revert button."""

    valueChanged = Signal()

    def __init__(self, key, label, options, default):
        super().__init__()
        self._key = key
        self._default = default
        self._archetype_default = default
        self._programmatic = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)

        self._label = QLabel(label)
        self._label.setStyleSheet('color: #e0e0e0; font-size: 11px;')
        layout.addWidget(self._label)

        self._revert_btn = QPushButton('↻')
        self._revert_btn.setFixedSize(18, 18)
        self._revert_btn.setStyleSheet(
            'QPushButton { background: none; border: none; color: #666; font-size: 13px; }'
        )
        self._revert_btn.setVisible(False)
        self._revert_btn.clicked.connect(self._revert)
        layout.addWidget(self._revert_btn)

        layout.addStretch()

        self._combo = QComboBox()
        self._combo.setFocusPolicy(Qt.StrongFocus)
        self._combo.installEventFilter(self)
        self._combo.setStyleSheet(
            'QComboBox { background: #2a2a2a; border: 1px solid #555; color: #ccc; '
            'font-size: 11px; padding: 3px 6px; border-radius: 3px; }'
        )
        for value, text in options:
            self._combo.addItem(text, value)
        idx = self._combo.findData(default)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._combo.currentIndexChanged.connect(self._on_changed)

        layout.addWidget(self._combo)

    def value(self):
        return self._combo.currentData()

    def set_value(self, val, programmatic=True):
        self._programmatic = programmatic
        idx = self._combo.findData(val)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._programmatic = False

    def set_archetype_default(self, val):
        self._archetype_default = val

    def _on_changed(self):
        is_dirty = str(self.value()) != str(self._archetype_default)
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet(
                'QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }'
            )
        if not self._programmatic:
            self.valueChanged.emit()

    def eventFilter(self, obj, event):
        if obj is self._combo and event.type() == event.Wheel:
            if not self._combo.hasFocus():
                event.ignore()
                return True
        return super().eventFilter(obj, event)

    def _revert(self):
        self.set_value(self._archetype_default, programmatic=False)


# ── Checkbox with revert ───────────────────────────────────────────────────

class _RevealCheck(QWidget):
    """Checkbox with label and revert button."""

    valueChanged = Signal()

    def __init__(self, key, label, default):
        super().__init__()
        self._key = key
        self._default = default
        self._archetype_default = default
        self._programmatic = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        self._label = QLabel(label)
        self._label.setStyleSheet('color: #e0e0e0; font-size: 11px;')
        layout.addWidget(self._label, 1)

        self._revert_btn = QPushButton('↻')
        self._revert_btn.setFixedSize(18, 18)
        self._revert_btn.setStyleSheet(
            'QPushButton { background: none; border: none; color: #666; font-size: 13px; }'
        )
        self._revert_btn.setVisible(False)
        self._revert_btn.clicked.connect(self._revert)
        layout.addWidget(self._revert_btn)

        self._check = QCheckBox()
        self._check.setStyleSheet(
            'QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #666; '
            'border-radius: 2px; background: #2a2a2a; }'
            'QCheckBox::indicator:checked { background: #4090c0; border-color: #60b0e0; }'
        )
        self._check.setChecked(default)
        self._check.stateChanged.connect(self._on_changed)
        layout.addWidget(self._check)

    def value(self):
        return self._check.isChecked()

    def set_value(self, val, programmatic=True):
        self._programmatic = programmatic
        self._check.setChecked(bool(val))
        self._programmatic = False

    def set_archetype_default(self, val):
        self._archetype_default = val

    def _on_changed(self):
        is_dirty = self.value() != self._archetype_default
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet(
                'QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }'
            )
        if not self._programmatic:
            self.valueChanged.emit()

    def _revert(self):
        self.set_value(self._archetype_default, programmatic=False)


# ── Palette Surgeon swatch ─────────────────────────────────────────────────

class _SwatchWidget(QWidget):
    """Single palette swatch: color block + coverage %."""

    clicked = Signal(int, object)   # idx, QMouseEvent
    merged = Signal(int, int)       # source_idx, target_idx (drag A onto B)

    def __init__(self, idx, r, g, b, pct, is_deleted=False, merge_count=0):
        super().__init__()
        self.idx = idx
        self._r, self._g, self._b = r, g, b
        self._pct = pct
        self._is_deleted = is_deleted
        self._merge_count = merge_count
        self._selected = False
        self._drag_start = None

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(24)
        self.setMinimumWidth(44)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setAcceptDrops(True)
        self.setToolTip(self._make_tooltip())

    def _make_tooltip(self):
        hex_name = f'#{self._r:02X}{self._g:02X}{self._b:02X}'
        if self._is_deleted:
            return f'{hex_name}  —\nAlt+click: restore'
        return (f'{hex_name}  {self._pct:.1f}%\n'
                'Click: solo  Alt+click: delete  Ctrl+click: edit\n'
                'Drag onto another swatch to merge')

    def set_selected(self, sel):
        self._selected = sel
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Background
        if self._is_deleted:
            p.fillRect(self.rect(), QColor('#2a1e1e'))
            border = QColor('#663333')
        elif self._selected:
            p.fillRect(self.rect(), QColor('#2d3545'))
            border = QColor('#4da6ff')
        else:
            p.fillRect(self.rect(), QColor('#2a2a2a'))
            border = QColor(0, 0, 0, 0)

        p.setPen(QPen(border, 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)

        # Color block
        block_x, block_y = 4, 3
        block_w, block_h = 19, 19
        p.fillRect(block_x, block_y, block_w, block_h, QColor(self._r, self._g, self._b))
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.drawRect(block_x, block_y, block_w, block_h)

        # Deleted overlay
        if self._is_deleted:
            p.fillRect(block_x, block_y, block_w, block_h, QColor(0, 0, 0, 128))
            p.setPen(QColor('#ff6666'))
            p.drawText(QRect(block_x, block_y, block_w, block_h), Qt.AlignCenter, '✕')

        # Merge badge
        if self._merge_count > 0 and not self._is_deleted:
            badge_text = f'+{self._merge_count}'
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#4da6ff'))
            bw = max(14, p.fontMetrics().horizontalAdvance(badge_text) + 6)
            p.drawRoundedRect(block_x + block_w - bw + 4, block_y - 4, bw, 14, 7, 7)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRect(block_x + block_w - bw + 4, block_y - 4, bw, 14),
                       Qt.AlignCenter, badge_text)

        # Coverage text
        pct_text = '—' if self._is_deleted else f'{self._pct:.0f}%'
        p.setPen(QColor('#ccc'))
        p.drawText(QRect(block_x + block_w + 3, 0, 40, self.height()),
                   Qt.AlignVCenter, pct_text)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not e.modifiers():
            self._drag_start = e.pos()
        self.clicked.emit(self.idx, e)

    def mouseMoveEvent(self, e):
        if self._drag_start is None or self._is_deleted:
            return
        if (e.pos() - self._drag_start).manhattanLength() < 8:
            return
        from PyQt5.QtCore import QMimeData
        from PyQt5.QtGui import QDrag
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self.idx))
        drag.setMimeData(mime)
        drag.exec_(Qt.MoveAction)
        self._drag_start = None

    def mouseReleaseEvent(self, e):
        self._drag_start = None

    def dragEnterEvent(self, e):
        if e.mimeData().hasText() and not self._is_deleted:
            src = int(e.mimeData().text())
            if src != self.idx:
                e.acceptProposedAction()

    def dropEvent(self, e):
        src = int(e.mimeData().text())
        if src != self.idx:
            self.merged.emit(src, self.idx)
            e.acceptProposedAction()


# ── Main dock ──────────────────────────────────────────────────────────────

class RevealDock(DockWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(DOCKER_TITLE)
        self._result = None
        self._worker = None
        self._proxy_pixels = None
        self._archetype_scores = []
        self._archetype_list = []
        self._others_expanded = False
        self._last_archetype_id = ''
        self._proxy_w = self._proxy_h = 0
        self._doc_w = self._doc_h = 0
        self._palette_data = []
        self._selected_idx = -1
        self._is_running = False
        self._has_result = False
        self._rerun_timer = QTimer(self)
        self._rerun_timer.setSingleShot(True)
        self._rerun_timer.setInterval(700)
        self._rerun_timer.timeout.connect(self._do_rerun)
        self._help_visible = False
        self._controls = {}  # key → widget
        self._archetype_defaults = dict(DEFAULTS)
        self._numpy_warned = False
        self._startup = True  # gates auto-separate in showEvent; cleared after 5s
        QTimer.singleShot(5000, self._end_startup)
        self._build_ui()

    def _end_startup(self):
        self._startup = False

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget(self)

        # Base colors via QPalette — no stylesheet cascading issues
        pal = root.palette()
        pal.setColor(QPalette.Window, QColor('#323232'))
        pal.setColor(QPalette.WindowText, QColor('#e0e0e0'))
        pal.setColor(QPalette.Base, QColor('#2a2a2a'))
        pal.setColor(QPalette.Text, QColor('#e0e0e0'))
        pal.setColor(QPalette.Button, QColor('#3a3a3a'))
        pal.setColor(QPalette.ButtonText, QColor('#e0e0e0'))
        root.setPalette(pal)
        root.setAutoFillBackground(True)

        self.setWidget(root)

        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Left panel: preview + status + surgeon ──
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        # View controls row: Resolution + Loupe magnification (above preview)
        preview_ctrl_row = QHBoxLayout()
        preview_ctrl_row.setSpacing(8)

        combo_style = (
            'QComboBox { background: #2a2a2a; border: 1px solid #444; color: #ccc; '
            'font-size: 9px; padding: 1px 4px; border-radius: 2px; }'
        )
        label_style = 'color: #888; font-size: 9px;'

        preview_ctrl_row.addStretch()

        res_label = QLabel('Resolution')
        res_label.setStyleSheet(label_style)
        preview_ctrl_row.addWidget(res_label)
        self._proxy_combo = QComboBox()
        self._proxy_combo.setStyleSheet(combo_style)
        self._proxy_combo.blockSignals(True)
        for val, text in [('1000', '1000'), ('1500', '1500'), ('2000', '2000')]:
            self._proxy_combo.addItem(text, val)
        self._proxy_combo.setCurrentIndex(0)  # 1000 default
        self._proxy_combo.blockSignals(False)
        self._proxy_combo.currentIndexChanged.connect(self._on_separate)
        preview_ctrl_row.addWidget(self._proxy_combo)

        loupe_label = QLabel('Loupe')
        loupe_label.setStyleSheet(label_style)
        preview_ctrl_row.addWidget(loupe_label)
        self._loupe_mag_combo = QComboBox()
        self._loupe_mag_combo.setStyleSheet(combo_style)
        for val, text in [(1, '1:1'), (2, '1:2'), (4, '1:4'), (8, '1:8')]:
            self._loupe_mag_combo.addItem(text, val)
        self._loupe_mag_combo.setCurrentIndex(1)  # 1:2 default
        self._loupe_mag_combo.currentIndexChanged.connect(self._on_loupe_mag_changed)
        preview_ctrl_row.addWidget(self._loupe_mag_combo)
        left_layout.addLayout(preview_ctrl_row)

        self._preview = _PreviewLabel()
        self._preview.clicked.connect(self._on_preview_clicked)
        left_layout.addWidget(self._preview, 1)

        self._status_bar = QLabel('Ready')
        self._status_bar.setStyleSheet('color: #bbb; font-size: 11px;')
        left_layout.addWidget(self._status_bar)

        # Palette surgeon
        self._surgeon_widget = QWidget()
        self._surgeon_widget.setStyleSheet(
            'background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 3px;'
        )
        self._surgeon_layout = QHBoxLayout(self._surgeon_widget)
        self._surgeon_layout.setContentsMargins(4, 4, 4, 4)
        self._surgeon_layout.setSpacing(2)
        self._surgeon_widget.setVisible(False)
        left_layout.addWidget(self._surgeon_widget)

        # "+" button to add new colors to palette
        self._add_color_btn = QPushButton('+')
        self._add_color_btn.setFixedSize(24, 24)
        self._add_color_btn.setStyleSheet(
            'QPushButton { background: #2a2a2a; border: 1px solid #555; color: #aaa; '
            'font-size: 14px; font-weight: bold; border-radius: 3px; }'
            'QPushButton:hover { color: #fff; border-color: #888; background: #3a3a3a; }'
        )
        self._add_color_btn.setToolTip('Add a colour to the palette')
        self._add_color_btn.setVisible(False)
        self._add_color_btn.clicked.connect(self._on_add_color)
        # Will be placed at end of surgeon row in _render_swatches

        # Suggested tray
        self._suggested_widget = QWidget()
        self._suggested_widget.setVisible(False)
        sug_layout = QVBoxLayout(self._suggested_widget)
        sug_layout.setContentsMargins(0, 4, 0, 0)
        sug_label = QLabel('SUGGESTED')
        sug_label.setStyleSheet('color: #aaa; font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px;')
        sug_layout.addWidget(sug_label)
        self._suggested_grid = QHBoxLayout()
        self._suggested_grid.setSpacing(4)
        sug_layout.addLayout(self._suggested_grid)
        left_layout.addWidget(self._suggested_widget)

        main_layout.addWidget(left, 1)

        # ── Right panel: controls (224px, matches HTML UI) ──
        right = QWidget()
        right.setObjectName('rightPanel')
        right.setFixedWidth(224)
        right.setStyleSheet('#rightPanel { border-left: 1px solid #3a3a3a; }')
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Reread Document + Reset to Defaults
        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(7, 5, 7, 4)
        btn_col.setSpacing(4)

        reread_btn = QPushButton('Reread Document')
        reread_btn.setToolTip('Re-read pixels from the active document and re-separate')
        reread_btn.setStyleSheet(
            'QPushButton { background: #1a2a3a; border: 1px solid #3070a0; '
            'color: #60b0ff; border-radius: 3px; font-size: 11px; font-weight: 600; '
            'padding: 6px 8px; }'
            'QPushButton:hover { background: #203848; border-color: #4090c0; }'
        )
        reread_btn.clicked.connect(self._on_reread)
        btn_col.addWidget(reread_btn)

        reset_btn = QPushButton('Reset to Defaults')
        reset_btn.setToolTip('Reset all knobs and palette edits to archetype defaults')
        reset_btn.setStyleSheet(
            'QPushButton { background: #3a3020; border: 1px solid #7a6530; '
            'color: #e0a030; border-radius: 3px; font-size: 11px; font-weight: 600; '
            'padding: 6px 8px; }'
            'QPushButton:hover { background: #4a3a28; border-color: #a08040; }'
        )
        reset_btn.clicked.connect(self._reset_all)
        btn_col.addWidget(reset_btn)

        right_layout.addLayout(btn_col)

        # Header: Controls | ?
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(7, 4, 7, 4)

        header_label = QLabel('Basic')
        header_label.setStyleSheet('color: #e0e0e0; font-size: 14px; font-weight: 600; letter-spacing: 0.5px;')
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        help_btn = QPushButton('?')
        help_btn.setFixedSize(20, 18)
        help_btn.setStyleSheet(
            'QPushButton { background: none; border: 1px solid #444; color: #777; '
            'font-size: 9px; border-radius: 3px; }'
            'QPushButton:hover { color: #bbb; border-color: #666; }'
        )
        help_btn.clicked.connect(self._toggle_help)
        header_layout.addWidget(help_btn)

        right_layout.addLayout(header_layout)

        # Separator
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet('color: #333;')
        sep1.setFixedHeight(1)
        right_layout.addWidget(sep1)

        # Archetype selector
        arch_layout = QVBoxLayout()
        arch_layout.setContentsMargins(7, 4, 7, 4)
        arch_layout.setSpacing(2)

        arch_label = QLabel('Archetype')
        arch_label.setStyleSheet(
            'color: #e0e0e0; font-size: 11px; letter-spacing: 0.6px;'
        )
        arch_layout.addWidget(arch_label)

        self._archetype_combo = QComboBox()
        self._archetype_combo.setStyleSheet(
            'QComboBox { background: #252525; border: 1px solid #3a6a3a; color: #80c080; '
            'font-size: 11px; padding: 2px 5px; border-radius: 3px; }'
        )
        self._archetype_combo.currentIndexChanged.connect(self._on_archetype_changed)
        arch_layout.addWidget(self._archetype_combo)

        right_layout.addLayout(arch_layout)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet('color: #333;')
        sep2.setFixedHeight(1)
        right_layout.addWidget(sep2)

        # Knobs panel (scrollable)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        knobs_widget = QWidget()
        self._knobs_layout = QVBoxLayout(knobs_widget)
        self._knobs_layout.setContentsMargins(8, 8, 8, 8)
        self._knobs_layout.setSpacing(4)

        self._build_controls()

        self._knobs_layout.addStretch()
        scroll.setWidget(knobs_widget)
        right_layout.addWidget(scroll, 1)

        # Footer: buttons + status
        footer_layout = QVBoxLayout()
        footer_layout.setContentsMargins(7, 6, 7, 6)
        footer_layout.setSpacing(5)

        self._btn_build = QPushButton('Build Layers')
        self._btn_build.setStyleSheet(
            'QPushButton { background: #1a3a2a; border: 1px solid #307050; '
            'color: #60c090; border-radius: 3px; font-size: 11px; font-weight: 600; '
            'padding: 6px 8px; }'
            'QPushButton:hover { background: #204838; border-color: #409060; }'
        )
        self._btn_build.setVisible(False)
        self._btn_build.clicked.connect(self._on_build_layers)
        footer_layout.addWidget(self._btn_build)

        self._side_status = QLabel('')
        self._side_status.setWordWrap(True)
        self._side_status.setStyleSheet('color: #bbb; font-size: 11px;')
        footer_layout.addWidget(self._side_status)

        right_layout.addLayout(footer_layout)

        main_layout.addWidget(right)

        # Color picker (shared, created once)
        self._color_picker = _LabPicker()
        self._color_picker.colorPicked.connect(self._on_color_picked)

    # ── Build controls (mirrors index.html exactly) ─────────────────────────

    def _build_controls(self):
        # We use self._target as the layout target so that redirecting it
        # to the Advanced container works correctly from the closures.
        self._target = self._knobs_layout

        def slider(key, label, mn, mx, default, step, fmt, help_text=None, rerun=True):
            w = _RevealSlider(key, label, mn, mx, default, step, fmt, help_text)
            self._controls[key] = w
            if rerun:
                w.valueChanged.connect(self._schedule_rerun)
            self._target.addWidget(w)
            return w

        def combo(key, label, options, default, rerun=True):
            w = _RevealCombo(key, label, options, default)
            self._controls[key] = w
            if rerun:
                w.valueChanged.connect(self._schedule_rerun)
            self._target.addWidget(w)
            return w

        def check(key, label, default, rerun=True):
            w = _RevealCheck(key, label, default)
            self._controls[key] = w
            if rerun:
                w.valueChanged.connect(self._schedule_rerun)
            self._target.addWidget(w)
            return w

        def group_label(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                'color: #999; font-size: 10px; text-transform: uppercase; '
                'letter-spacing: 0.6px; margin-top: 10px; padding-top: 6px; '
                'border-top: 1px solid #333;'
            )
            self._target.addWidget(lbl)

        # ── Primary (always visible) ──
        slider('colors', 'Target Colors', 3, 12, 6, 1,
               lambda v: str(int(v)),
               'Target number of output colors — the engine may produce a few more or fewer.')
        check('preserve_white', 'Preserve White', True)
        check('preserve_black', 'Preserve Black', True)
        slider('density', 'Minimum Coverage', 0, 5, 0.5, 0.1,
               lambda v: f'{v:.1f}%',
               'Colors covering less than this percentage of the image get merged into their nearest neighbor.')
        slider('speckle', 'Despeckle', 0, 30, 0, 1,
               lambda v: f'{int(v)} px',
               'Remove isolated pixel clusters smaller than this radius. Higher values = cleaner output but may lose fine detail.')
        slider('clamp', 'Minimum Opacity', 0, 40, 0, 0.5,
               lambda v: f'{int(v)}%',
               'Minimum mask density for each color. Faint areas below this threshold get boosted so they remain visible.')

        # Dither does NOT auto-rerun (post-processing only, matches HTML UI)
        combo('dither_type', 'Dither',
              [('none', 'None'), ('floyd-steinberg', 'Floyd-Steinberg'),
               ('bayer', 'Bayer'), ('atkinson', 'Atkinson'), ('stucki', 'Stucki')],
              'none', rerun=False)

        # ── Screen Printing (collapsible) ──
        sp_header = QPushButton('▶ Screen Printing')
        sp_header.setStyleSheet(
            'QPushButton { background: none; border: none; color: #e0e0e0; '
            'font-size: 14px; font-weight: 600; text-align: left; padding: 6px 0; margin-top: 4px; '
            'letter-spacing: 0.5px; }'
            'QPushButton:hover { color: #fff; }'
        )
        sp_header.setCursor(Qt.PointingHandCursor)
        self._target.addWidget(sp_header)

        sp_container = QWidget()
        sp_container.setVisible(False)
        sp_layout = QVBoxLayout(sp_container)
        sp_layout.setContentsMargins(0, 6, 0, 0)
        sp_layout.setSpacing(4)
        self._target.addWidget(sp_container)

        def _toggle_screen_printing():
            vis = not sp_container.isVisible()
            sp_container.setVisible(vis)
            sp_header.setText(('▼ ' if vis else '▶ ') + 'Screen Printing')
        sp_header.clicked.connect(_toggle_screen_printing)

        self._target = sp_layout

        slider('mesh_size', 'Mesh', 0, 400, 230, 10,
               lambda v: f'{int(v)} TPI' if v > 0 else 'Off',
               'Mesh density in threads per inch. Determines minimum reproducible detail size. 0 = disabled.')
        slider('trap_size', 'Trap Width', 0, 10, 0, 1,
               lambda v: f'{int(v)} pt',
               'Expand lighter colors under darker neighbors to prevent gaps from misregistration. 0 = no trapping.')

        # Restore target to main knobs layout
        self._target = self._knobs_layout

        # ── Advanced (collapsible) ──
        adv_header = QPushButton('▶ Advanced')
        adv_header.setStyleSheet(
            'QPushButton { background: none; border: none; color: #e0e0e0; '
            'font-size: 14px; font-weight: 600; text-align: left; padding: 6px 0; margin-top: 4px; '
            'letter-spacing: 0.5px; }'
            'QPushButton:hover { color: #fff; }'
        )
        adv_header.setCursor(Qt.PointingHandCursor)
        self._target.addWidget(adv_header)

        adv_container = QWidget()
        adv_container.setVisible(False)
        adv_layout = QVBoxLayout(adv_container)
        adv_layout.setContentsMargins(0, 6, 0, 0)
        adv_layout.setSpacing(4)
        self._target.addWidget(adv_container)

        def _toggle_advanced():
            vis = not adv_container.isVisible()
            adv_container.setVisible(vis)
            adv_header.setText(('▼ ' if vis else '▶ ') + 'Advanced')
        adv_header.clicked.connect(_toggle_advanced)

        # Redirect control creation into the Advanced container
        self._target = adv_layout

        # Method
        group_label('Method')
        combo('engine_type', 'Separation Method',
              [('reveal-mk1.5', 'Standard'), ('distilled', 'Adaptive'),
               ('reveal', 'Hue-Aware'), ('balanced', 'Fast'), ('stencil', 'Stencil')],
              'reveal-mk1.5')
        combo('color_mode', 'Color Mode',
              [('color', 'Color'), ('bw', 'B/W'), ('grayscale', 'Grayscale')], 'color')

        # Algorithm
        group_label('Algorithm')
        combo('split_mode', 'Starting Palette',
              [('variance', 'Detail Priority'), ('median', 'Color Priority')], 'median')
        combo('quantizer', 'Quantizer',
              [('wu', 'Wu (Vibrance)'), ('median_cut', 'Median Cut')], 'wu')
        combo('distance_metric', 'Color Matching',
              [('cie76', 'Standard (CIE76)'), ('cie94', 'Perceptual (CIE94)'),
               ('cie2000', 'Museum Grade (CIE2000)')], 'cie76')
        combo('centroid_strategy', 'Color Selection',
              [('SALIENCY', 'Eye-Catching (Saliency)'),
               ('ROBUST_SALIENCY', 'Balanced (Robust Saliency)'),
               ('VOLUMETRIC', 'Even Fill (Volumetric)'),
               ('AVERAGE', 'Simple Average (Average)')], 'ROBUST_SALIENCY')
        slider('neutral_sovereignty', 'Gray Protection', 0, 100, 0, 1,
               lambda v: str(int(v)))

        # Saturation
        group_label('Saturation')
        slider('vibrancy_boost', 'Boost', 0.5, 3.0, 1.4, 0.05,
               lambda v: f'{v:.1f}')
        combo('vibrancy_mode', 'Curve',
              [('linear', 'Linear'), ('subtle', 'Subtle'), ('moderate', 'Moderate'),
               ('aggressive', 'Aggressive'), ('exponential', 'Exponential')], 'moderate')
        slider('chroma_gate', 'Gate', 1.0, 3.0, 1.0, 0.1,
               lambda v: f'{v:.1f}')

        # Color Merging
        group_label('Color Merging')
        slider('palette_reduction', 'Merge Distance', 2, 14, 6.0, 0.5,
               lambda v: f'{v:.1f}')
        check('enable_palette_reduction', 'Auto Merge', True)
        check('enable_hue_gap', 'Hue Recovery', True)
        slider('hue_lock_angle', 'Hue Lock', 10, 60, 20, 5,
               lambda v: f'{int(v)}°')

        # Color Priority
        group_label('Color Priority')
        slider('l_weight', 'Lightness', 0.5, 3.0, 1.2, 0.1,
               lambda v: f'{v:.1f}')
        slider('c_weight', 'Color Intensity', 0.5, 5.0, 2.0, 0.1,
               lambda v: f'{v:.1f}')
        slider('black_bias', 'Black Pull', 0, 10, 3.0, 0.5,
               lambda v: f'{v:.1f}')

        # Tone
        group_label('Tone')
        slider('highlight_threshold', 'Highlight', 80, 100, 90, 1,
               lambda v: str(int(v)))
        slider('highlight_boost', 'Highlight Boost', 0, 3.0, 1.5, 0.1,
               lambda v: f'{v:.1f}')
        slider('shadow_point', 'Shadow Point', 0, 30, 15, 1,
               lambda v: str(int(v)))

        # Surface
        group_label('Surface')
        combo('substrate_mode', 'Substrate',
              [('none', 'None'), ('auto', 'Auto'), ('force', 'White'),
               ('dark', 'Dark'), ('black', 'Black'), ('transparent', 'Transparent')],
              'none')
        slider('substrate_tolerance', 'Substrate Tolerance', 0, 5, 2.0, 0.5,
               lambda v: f'{v:.1f}',
               'How close a pixel must be to the substrate color before it is treated as background.')
        check('ignore_transparent', 'Skip Transparent', True)

        # Detail
        group_label('Detail')
        combo('preprocessing', 'Pre-Smoothing',
              [('off', 'Off'), ('auto', 'Auto'), ('light', 'Light'),
               ('medium', 'Medium'), ('heavy', 'Heavy')], 'off')
        check('median_pass', 'Smoothing', False)
        slider('detail_rescue', 'Fine Detail', 0, 20, 0, 1,
               lambda v: str(int(v)))

        # Restore target to main knobs layout
        self._target = self._knobs_layout

    # ── Archetype handling ─────────────────────────────────────────────────

    def _render_archetypes(self, archetypes, matched_id):
        """Render archetype dropdown: top 6 + expandable Others toggle.

        Mirrors the HTML UI: initially folded, clicking the sentinel
        '▾ Others (N)…' expands, '▴ Collapse others' folds.
        """
        self._archetype_combo.blockSignals(True)
        self._archetype_combo.clear()
        sorted_arch = sorted(archetypes, key=lambda a: a['score'], reverse=True)
        self._archetype_list = sorted_arch  # stash for re-render
        self._others_expanded = getattr(self, '_others_expanded', False)

        top = sorted_arch[:6]
        rest = sorted_arch[6:]

        for a in top:
            self._archetype_combo.addItem(
                f"{a['name']}  {int(a['score'])}%", a['id'])

        if rest:
            if self._others_expanded:
                self._archetype_combo.insertSeparator(self._archetype_combo.count())
                for a in rest:
                    self._archetype_combo.addItem(
                        f"{a['name']}  {int(a['score'])}%", a['id'])
                self._archetype_combo.addItem(
                    '\u25b4 Collapse others', '__others_toggle__')
            else:
                self._archetype_combo.addItem(
                    f'\u25be Others ({len(rest)})\u2026', '__others_toggle__')

        idx = self._archetype_combo.findData(matched_id)
        if idx >= 0:
            self._archetype_combo.setCurrentIndex(idx)
        self._archetype_combo.blockSignals(False)

    def _on_archetype_changed(self):
        if not self._has_result or self._is_running:
            return
        arch_id = self._archetype_combo.currentData()
        if not arch_id:
            return

        # Others toggle — expand/collapse and re-render in place
        if arch_id == '__others_toggle__':
            self._others_expanded = not self._others_expanded
            # Find the currently selected real archetype before re-render
            prev_id = getattr(self, '_last_archetype_id', '') or '__auto__'
            self._render_archetypes(self._archetype_list, prev_id)
            return

        self._last_archetype_id = arch_id
        self._others_expanded = False  # collapse after picking from Others
        self._is_running = True
        self._has_result = False
        self._preview.clear_images()
        self._clear_swatches()
        self._set_status('Applying archetype…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()  # ensure cursor updates before worker starts

        pixels = list(self._proxy_pixels)
        dw, dh = self._proxy_w, self._proxy_h
        options = {'_archetype_id': arch_id}
        self._worker = _Worker(pixels, dw, dh, 0, options)
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _apply_matched_archetype(self, ma):
        """Update all controls from matched archetype values."""
        if not ma:
            return

        MA_TO_KEY = {
            'colors': 'colors', 'density': 'density', 'speckle': 'speckle', 'clamp': 'clamp',
            'vibrancy_boost': 'vibrancy_boost', 'vibrancy_mode': 'vibrancy_mode',
            'l_weight': 'l_weight', 'c_weight': 'c_weight', 'black_bias': 'black_bias',
            'shadow_point': 'shadow_point', 'palette_reduction': 'palette_reduction',
            'enable_palette_reduction': 'enable_palette_reduction',
            'enable_hue_gap_analysis': 'enable_hue_gap',
            'hue_lock_angle': 'hue_lock_angle',
            'preserve_white': 'preserve_white',
            'preserve_black': 'preserve_black', 'preprocessing': 'preprocessing',
            'engine_type': 'engine_type', 'color_mode': 'color_mode',
            'dither_type': 'dither_type', 'distance_metric': 'distance_metric',
            'centroid_strategy': 'centroid_strategy', 'split_mode': 'split_mode',
            'quantizer': 'quantizer',
            'neutral_sovereignty_threshold': 'neutral_sovereignty',
            'chroma_gate': 'chroma_gate', 'highlight_threshold': 'highlight_threshold',
            'highlight_boost': 'highlight_boost', 'median_pass': 'median_pass',
            'detail_rescue': 'detail_rescue',
            'substrate_mode': 'substrate_mode',
            'substrate_tolerance': 'substrate_tolerance',
            'ignore_transparent': 'ignore_transparent',
            'mesh_size': 'mesh_size',
            'trap_size': 'trap_size',
        }

        for ma_key, ctrl_key in MA_TO_KEY.items():
            val = ma.get(ma_key)
            if val is not None and ctrl_key in self._controls:
                ctrl = self._controls[ctrl_key]
                ctrl.set_archetype_default(val)
                ctrl.set_value(val, programmatic=True)
                self._archetype_defaults[ctrl_key] = val
        # DEBUG: verify dither control state after apply
        dc = self._controls.get('dither_type')
        if dc:
            print(f"[Reveal] dither_type control value after apply: {dc.value()}")

    # ── Collect params ─────────────────────────────────────────────────────

    def _collect_params(self):
        p = {}
        for key, ctrl in self._controls.items():
            p[key] = ctrl.value()
        # Remap keys for processor compatibility; inject hidden defaults
        return {
            'colors': int(p['colors']),
            'density': p['density'],
            'speckle': int(p['speckle']),
            'clamp': p['clamp'],
            'vibrancy_boost': p['vibrancy_boost'],
            'vibrancy_mode': p['vibrancy_mode'],
            'l_weight': p['l_weight'],
            'c_weight': p['c_weight'],
            'black_bias': p['black_bias'],
            'shadow_point': p['shadow_point'],
            'palette_reduction': p['palette_reduction'],
            'enable_palette_reduction': p['enable_palette_reduction'],
            'enable_hue_gap_analysis': p['enable_hue_gap'],
            'hue_lock_angle': p['hue_lock_angle'],
            'preserve_white': p['preserve_white'],
            'preserve_black': p['preserve_black'],
            'preprocessing': p['preprocessing'],
            'engine_type': p['engine_type'],
            'color_mode': p['color_mode'],
            'dither_type': p['dither_type'],
            'distance_metric': p['distance_metric'],
            'centroid_strategy': p['centroid_strategy'],
            'split_mode': p['split_mode'],
            'quantizer': p['quantizer'],
            'neutral_sovereignty_threshold': p['neutral_sovereignty'],
            'chroma_gate': p['chroma_gate'],
            'highlight_threshold': p['highlight_threshold'],
            'highlight_boost': p['highlight_boost'],
            'median_pass': p['median_pass'],
            'detail_rescue': p['detail_rescue'],
            'proxy_resolution': int(self._proxy_combo.currentData() or 1000),
            'substrate_mode': p['substrate_mode'],
            'substrate_tolerance': p['substrate_tolerance'],
            'ignore_transparent': p['ignore_transparent'],
            'mesh_size': int(p['mesh_size']),
            'trap_size': int(p['trap_size']),
        }

    # ── Separate ───────────────────────────────────────────────────────────

    def _on_separate(self):
        if self._is_running:
            return
        if self._rerun_timer.isActive():
            self._rerun_timer.stop()

        self._check_numpy()

        app = Krita.instance()
        doc = app.activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return
        if doc.colorModel() != 'LABA':
            self._set_status('Document must be in Lab colour mode.', error=True)
            return

        self._is_running = True
        self._has_result = False
        self._selected_idx = -1
        self._preview.clear_images()
        self._clear_swatches()
        self._btn_build.setVisible(False)
        self._set_status('Reading pixels…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()  # ensure cursor updates before blocking read

        params = self._collect_params()
        try:
            raw, w, h = read_document_raw(doc)
            proxy_res = int(params.get('proxy_resolution', 800))
            pixels, dw, dh = downsample_pixels_smooth(raw, w, h, max_dim=proxy_res)
        except Exception as e:
            self._is_running = False
            QApplication.restoreOverrideCursor()
            self._set_status(f'Read error: {e}', error=True)
            return

        self._proxy_w, self._proxy_h = dw, dh
        self._doc_w, self._doc_h = w, h
        self._proxy_pixels = list(pixels)
        self._set_status(f'Separating {dw}×{dh}…')

        # Let the archetype drive algorithm params (engine_type, dither_type,
        # centroid_strategy, etc.). Only pass mechanical knobs + preprocessing
        # so UI defaults don't override archetype values.
        # Full UI param passthrough happens in _do_rerun for user tweaks.
        options = {
            '_archetype_id': self._archetype_combo.currentData() or '__auto__',
            '_preprocessing_intensity': params['preprocessing'],
            'density_floor': params['density'] / 100.0,
            'speckle_rescue': int(params['speckle']),
            'shadow_clamp': int(params['clamp']),
        }

        self._worker = _Worker(pixels, dw, dh, 0, options)
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    def _on_worker_done(self, result):
        try:
            self._handle_worker_result(result)
        except Exception as e:
            self._is_running = False
            self._has_result = bool(self._proxy_pixels)
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self._set_status(f'Error: {e}', error=True)
            import traceback
            traceback.print_exc()

    def _handle_worker_result(self, result):
        QApplication.restoreOverrideCursor()
        self._preview.set_overlay_text('')
        result['_proxy_w'] = self._proxy_w
        result['_proxy_h'] = self._proxy_h
        result['_doc_w'] = self._doc_w
        result['_doc_h'] = self._doc_h
        self._result = result
        self._is_running = False
        self._has_result = True

        pw, ph = self._proxy_w, self._proxy_h
        orig_px = self._to_pixmap(result['_orig_rgb'], pw, ph)
        post_px = self._to_pixmap(result['_post_rgb'], pw, ph)
        self._preview.set_images(orig_px, post_px)

        meta = result['metadata']
        matched = result.get('_matched_archetype', {})
        arch_name = matched.get('name', '')
        if arch_name:
            msg = f"{meta['final_colors']} colours · {arch_name} ({meta['duration']}s)"
        else:
            msg = f"{meta['final_colors']} colours ({meta['duration']}s)"
        self._set_status(msg)

        # Palette
        coverage = result.get('_coverage', [])
        palette_data = []
        for i, c in enumerate(result['palette']):
            palette_data.append({
                'r': c['r'], 'g': c['g'], 'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': round(coverage[i] if i < len(coverage) else 0.0, 1),
                'is_deleted': False,
                'merge_count': 0,
            })
        self._render_swatches(palette_data)

        # Suggestions
        from pyreveal.color.encoding import lab_to_rgb
        suggestions = []
        for s in result.get('_suggestions', []):
            r, g, b = lab_to_rgb(s['L'], s['a'], s['b'])
            suggestions.append({
                'r': r, 'g': g, 'b': b,
                'hex': f'#{r:02X}{g:02X}{b:02X}',
                'reason': s.get('reason', ''),
                'score': round(s.get('score', 0), 1),
            })
        self._render_suggestions(suggestions)

        # Archetypes
        fresh = result.get('_archetype_scores', [])
        if len(fresh) > 1:
            self._archetype_scores = fresh
        if self._archetype_scores:
            self._last_archetype_id = matched.get('id', '')
            self._render_archetypes(self._archetype_scores, self._last_archetype_id)
        self._apply_matched_archetype(matched)
        # DEBUG: verify dither_type propagation
        print(f"[Reveal] archetype={matched.get('name','?')} dither_type={matched.get('dither_type','MISSING')}")

        self._btn_build.setVisible(True)

    def _on_worker_error(self, msg):
        QApplication.restoreOverrideCursor()
        self._is_running = False
        self._set_status(f'Error: {msg}', error=True)

    # ── Debounced rerun ────────────────────────────────────────────────────

    def _schedule_rerun(self):
        if not self._has_result:
            return
        self._rerun_timer.start()

    def _do_rerun(self):
        if not self._has_result or self._proxy_pixels is None:
            return
        self._is_running = True
        self._has_result = False
        self._preview.clear_images()
        self._clear_swatches()
        self._set_status('Applying changes…')
        QApplication.setOverrideCursor(Qt.WaitCursor)

        params = self._collect_params()
        arch_id = self._archetype_combo.currentData() or '__auto__'

        options = {'_archetype_id': arch_id}
        # Pass all params so the engine uses the user's current values
        for key in ('vibrancy_boost', 'l_weight', 'c_weight', 'black_bias',
                    'shadow_point', 'palette_reduction', 'hue_lock_angle',
                    'neutral_sovereignty_threshold', 'chroma_gate',
                    'highlight_threshold', 'highlight_boost',
                    'detail_rescue', 'substrate_tolerance'):
            options[key] = float(params[key])
        for key in ('vibrancy_mode', 'substrate_mode', 'engine_type', 'color_mode',
                    'dither_type', 'distance_metric', 'centroid_strategy',
                    'split_mode', 'quantizer'):
            options[key] = str(params[key])
        for key in ('enable_palette_reduction', 'enable_hue_gap_analysis',
                    'preserve_white', 'preserve_black', 'median_pass',
                    'ignore_transparent'):
            options[key] = bool(params[key])
        for key in ('mesh_size', 'trap_size'):
            options[key] = int(params[key])
        options['_preprocessing_intensity'] = str(params['preprocessing'])
        options['density'] = params['density']
        options['density_floor'] = params['density'] / 100.0
        options['speckle_rescue'] = int(params['speckle'])
        options['shadow_clamp'] = int(params['clamp'])

        colors = int(params['colors'])
        dw, dh = self._proxy_w, self._proxy_h
        self._worker = _Worker(list(self._proxy_pixels), dw, dh, colors, options)
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    # ── Solo / Override / Delete ────────────────────────────────────────────

    def _on_loupe_mag_changed(self):
        mag = self._loupe_mag_combo.currentData() or 2
        self._preview._loupe.ZOOM = mag

    def _on_preview_clicked(self):
        """Quick click on preview: if swatch selected → deselect (show full),
        else → toggle original/posterized."""
        if self._selected_idx >= 0:
            # Deselect swatch, show full posterized view
            self._selected_idx = -1
            for sw in self._swatch_widgets:
                sw.set_selected(False)
            if self._result:
                r = self._result
                pw, ph = r['_proxy_w'], r['_proxy_h']
                post_px = self._to_pixmap(r['_post_rgb'], pw, ph)
                self._preview.update_post(post_px)
        else:
            # Toggle original/posterized (blink comparator single-click)
            self._preview._showing_orig = not self._preview._showing_orig
            if self._preview._showing_orig:
                self._preview._show_pixmap(self._preview._orig_px)
            else:
                self._preview._show_pixmap(self._preview._post_px)

    def _on_swatch_clicked(self, idx, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            # Ctrl+click (or Cmd+click on macOS): open color picker
            c = self._palette_data[idx]
            if c.get('is_deleted'):
                return
            swatch = self._swatch_widgets[idx]
            self._color_picker._target_idx = idx
            self._color_picker.open_for(idx, c['hex'], swatch)
            return

        if event.modifiers() & Qt.AltModifier:
            # Alt+click: delete/undelete
            self._toggle_delete(idx)
            return

        # Click: solo
        c = self._palette_data[idx]
        if c.get('is_deleted'):
            return
        deselect = self._selected_idx == idx
        self._selected_idx = -1 if deselect else idx

        # Update selection visuals
        for sw in self._swatch_widgets:
            sw.set_selected(sw.idx == self._selected_idx)

        # Generate solo/full preview
        if not self._result:
            return
        r = self._result
        pw, ph = r['_proxy_w'], r['_proxy_h']
        if self._selected_idx < 0:
            rgb = r['_post_rgb']
        else:
            assignments = self._effective_assignments()
            rgb = make_solo_rgb(assignments, r['palette'], self._selected_idx, pw, ph)
        post_px = self._to_pixmap(rgb, pw, ph)
        self._preview.update_post(post_px)

    def _on_color_picked(self, r, g, b):
        idx = self._color_picker._target_idx
        if idx < 0 or not self._result:
            return
        palette = self._result['palette']
        if idx >= len(palette):
            # Add-new mode
            self._on_new_color_added(r, g, b)
            return
        palette[idx] = {'r': r, 'g': g, 'b': b}
        # Also update palette_lab so Build Layers uses the edited color
        palette_lab = self._result.get('palette_lab', [])
        if idx < len(palette_lab):
            L, a_val, b_val = rgb_to_lab(r, g, b)
            palette_lab[idx] = {'L': L, 'a': a_val, 'b': b_val}
            print(f"[Reveal] Color edit [{idx}]: rgb=({r},{g},{b}) → Lab=({L:.1f},{a_val:.1f},{b_val:.1f})")
        else:
            print(f"[Reveal] WARNING: palette_lab has {len(palette_lab)} entries, idx={idx} — Lab NOT updated")
        self._rebuild_preview()

    def _on_add_color(self):
        """Open color picker to add a new color to the palette."""
        if not self._result:
            return
        palette = self._result['palette']
        live_count = sum(1 for c in palette if not c.get('is_deleted'))
        if live_count >= 20:
            self._set_status('Maximum 20 colours.', error=True)
            return
        # Use a special index to signal "add new" mode
        self._adding_color = True
        self._color_picker._target_idx = len(palette)
        # Start with neutral gray
        self._color_picker.open_for(len(palette), '#808080', self._add_color_btn)

    def _on_new_color_added(self, r, g, b):
        """Handle color picked in add-new mode."""
        if not self._result:
            return
        L, a_val, b_val = rgb_to_lab(r, g, b)
        self._result['palette'].append({'r': r, 'g': g, 'b': b})
        palette_lab = self._result.get('palette_lab', [])
        palette_lab.append({'L': L, 'a': a_val, 'b': b_val})
        # New color has 0% coverage; assignments unchanged (pixels stay with original colors)
        coverage = self._result.get('_coverage', [])
        coverage.append(0.0)
        self._rebuild_preview()

    def _toggle_delete(self, idx):
        if not self._result:
            return
        palette = self._result['palette']
        if idx < 0 or idx >= len(palette):
            return
        if palette[idx].get('is_deleted'):
            palette[idx].pop('is_deleted', None)
        else:
            live_count = sum(1 for c in palette if not c.get('is_deleted'))
            if live_count <= 2:
                return
            palette[idx]['is_deleted'] = True
        self._rebuild_preview()

    def _on_swatch_merged(self, src_idx, tgt_idx):
        """Drag swatch A onto B → A is deleted, its pixels merge into B."""
        if not self._result:
            return
        palette = self._result['palette']
        if src_idx < 0 or src_idx >= len(palette):
            return
        if tgt_idx < 0 or tgt_idx >= len(palette):
            return
        if palette[src_idx].get('is_deleted'):
            return
        live_count = sum(1 for c in palette if not c.get('is_deleted'))
        if live_count <= 2:
            return
        palette[src_idx]['is_deleted'] = True
        palette[src_idx]['_merge_target'] = tgt_idx
        self._rebuild_preview()

    def _effective_assignments(self):
        palette = self._result['palette']
        palette_lab = self._result.get('palette_lab', [])
        live = [i for i, c in enumerate(palette) if not c.get('is_deleted')]
        if not live or len(live) == len(palette):
            return self._result['assignments']

        def lab_dist(i, j):
            a, b = palette_lab[i], palette_lab[j]
            return (a['L'] - b['L']) ** 2 + (a['a'] - b['a']) ** 2 + (a['b'] - b['b']) ** 2

        remap = {}
        for i, c in enumerate(palette):
            if c.get('is_deleted'):
                tgt = c.get('_merge_target')
                if tgt is not None and tgt in live:
                    remap[i] = tgt
                else:
                    remap[i] = min(live, key=lambda j: lab_dist(i, j))

        return [remap.get(a, a) for a in self._result['assignments']]

    def _rebuild_preview(self):
        r = self._result
        pw, ph = r['_proxy_w'], r['_proxy_h']

        effective = self._effective_assignments()
        r['_post_rgb'] = make_posterized_rgb(effective, r['palette'], pw, ph)
        post_px = self._to_pixmap(r['_post_rgb'], pw, ph)
        self._preview.update_post(post_px)

        # Update coverage
        total = pw * ph
        counts = [0] * len(r['palette'])
        for a in effective:
            if a < len(counts):
                counts[a] += 1
        r['_coverage'] = [100.0 * c / total for c in counts]

        # Merge counts
        palette_lab = r.get('palette_lab', [])
        live = [i for i, c in enumerate(r['palette']) if not c.get('is_deleted')]
        merge_counts = [0] * len(r['palette'])
        for i, c in enumerate(r['palette']):
            if c.get('is_deleted') and live and palette_lab:
                def lab_dist(a, b):
                    return (palette_lab[a]['L'] - palette_lab[b]['L']) ** 2 + \
                           (palette_lab[a]['a'] - palette_lab[b]['a']) ** 2 + \
                           (palette_lab[a]['b'] - palette_lab[b]['b']) ** 2
                target = min(live, key=lambda j: lab_dist(i, j))
                merge_counts[target] += 1

        coverage = r['_coverage']
        palette_data = []
        for i, c in enumerate(r['palette']):
            palette_data.append({
                'r': c['r'], 'g': c['g'], 'b': c['b'],
                'hex': f"#{c['r']:02X}{c['g']:02X}{c['b']:02X}",
                'pct': 0.0 if c.get('is_deleted') else round(coverage[i] if i < len(coverage) else 0.0, 1),
                'is_deleted': bool(c.get('is_deleted', False)),
                'merge_count': merge_counts[i],
            })
        self._render_swatches(palette_data)

    # ── Build Layers ───────────────────────────────────────────────────────

    def _on_build_layers(self):
        if not self._result or self._is_running:
            return
        doc = Krita.instance().activeDocument()
        if not doc:
            self._set_status('No active document.', error=True)
            return

        self._is_running = True
        self._set_status('Building layers…')
        QApplication.setOverrideCursor(Qt.WaitCursor)

        # Build a result with palette edits and deleted colors applied.
        # Shallow-copy the result dict; deep-copy only the palettes (the large
        # byte arrays like _orig_rgb, lab_pixels etc. are shared, not mutated).
        build_result = dict(self._result)
        palette = [dict(c) for c in build_result['palette']]
        palette_lab = [dict(c) for c in build_result.get('palette_lab', [])]
        build_result['palette'] = palette
        build_result['palette_lab'] = palette_lab

        # DEBUG: log palette state at build time
        print(f"[Reveal] Build: {len(palette)} colors, {sum(1 for c in palette if c.get('is_deleted'))} deleted")
        for i, (c, lab) in enumerate(zip(palette, palette_lab)):
            d = ' DELETED' if c.get('is_deleted') else ''
            print(f"  [{i}] rgb=({c['r']},{c['g']},{c['b']}) lab=({lab['L']:.1f},{lab['a']:.1f},{lab['b']:.1f}){d}")

        live = [i for i, c in enumerate(palette) if not c.get('is_deleted')]
        if len(live) < len(palette) and live:
            # Remap assignments so deleted colors merge into nearest live color
            effective = self._effective_assignments()
            # Build index mapping: old live index → new contiguous index
            remap = {old: new for new, old in enumerate(live)}
            build_result['assignments'] = [remap.get(a, 0) for a in effective]
            build_result['palette'] = [palette[i] for i in live]
            build_result['palette_lab'] = [palette_lab[i] for i in live]

        try:
            def _progress(msg):
                self._set_status(msg)
                QApplication.processEvents()

            n = build_separation_layers(doc, build_result, on_progress=_progress)
            self._set_status(f'Created {n} layers.')
            # Hide the dock after successful build (user doesn't need it anymore)
            self.setVisible(False)
        except Exception as e:
            self._set_status(f'Layer error: {e}', error=True)
        finally:
            self._is_running = False
            QApplication.restoreOverrideCursor()

    # ── Swatch rendering ──────────────────────────────────────────────────

    def _render_swatches(self, palette_data):
        self._clear_swatches()
        self._palette_data = palette_data
        self._swatch_widgets = []

        for i, c in enumerate(palette_data):
            sw = _SwatchWidget(
                i, c['r'], c['g'], c['b'], c['pct'],
                is_deleted=c.get('is_deleted', False),
                merge_count=c.get('merge_count', 0),
            )
            sw.clicked.connect(self._on_swatch_clicked)
            sw.merged.connect(self._on_swatch_merged)
            if i == self._selected_idx:
                sw.set_selected(True)
            self._surgeon_layout.addWidget(sw)
            self._swatch_widgets.append(sw)
        self._surgeon_layout.addWidget(self._add_color_btn)
        self._add_color_btn.setVisible(True)
        self._surgeon_layout.addStretch()

        self._surgeon_widget.setVisible(True)

    def _clear_swatches(self):
        while self._surgeon_layout.count():
            item = self._surgeon_layout.takeAt(0)
            w = item.widget()
            if w and w is not self._add_color_btn:
                w.deleteLater()
        self._add_color_btn.setVisible(False)
        self._swatch_widgets = []
        self._surgeon_widget.setVisible(False)

    def _render_suggestions(self, suggestions):
        # Clear
        while self._suggested_grid.count():
            item = self._suggested_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not suggestions:
            self._suggested_widget.setVisible(False)
            return

        for s in suggestions:
            # Match palette swatch layout: 19×19 color block + score label
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 4, 0)
            row_layout.setSpacing(3)

            block = QFrame()
            block.setFixedSize(19, 19)
            block.setStyleSheet(
                f"background: {s['hex']}; border: 1px solid rgba(255,255,255,0.15); "
                f"border-radius: 2px;"
            )

            score_lbl = QLabel(str(int(s['score'])))
            score_lbl.setStyleSheet('color: #999; font-size: 10px;')

            row_layout.addWidget(block)
            row_layout.addWidget(score_lbl)
            row.setToolTip(f"{s['hex']}\n{s['reason']}")
            self._suggested_grid.addWidget(row)

        self._suggested_grid.addStretch()
        self._suggested_widget.setVisible(True)

    # ── Help toggle ────────────────────────────────────────────────────────

    def _toggle_help(self):
        self._help_visible = not self._help_visible
        for ctrl in self._controls.values():
            if hasattr(ctrl, 'set_help_visible'):
                ctrl.set_help_visible(self._help_visible)

    # ── Reset all ──────────────────────────────────────────────────────────

    def _on_reread(self):
        """Re-read pixels from the active document and run a fresh separation."""
        if self._is_running:
            return
        # Invalidate cached pixels so _on_separate reads fresh from the document
        self._proxy_pixels = None
        self._result = None
        self._has_result = False
        try:
            self._on_separate()
        except Exception as e:
            self._is_running = False
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self._set_status(f'Reread failed: {e}', error=True)
            import traceback
            traceback.print_exc()

    def _reset_all(self):
        for key, ctrl in self._controls.items():
            default = self._archetype_defaults.get(key, DEFAULTS.get(key))
            if default is not None:
                ctrl.set_value(default, programmatic=False)
        if self._has_result:
            self._schedule_rerun()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _to_pixmap(self, rgb_bytes: bytes, width: int, height: int) -> QPixmap:
        img = QImage(rgb_bytes, width, height, width * 3, QImage.Format_RGB888)
        return QPixmap.fromImage(img.copy())

    def _set_status(self, msg, error=False):
        color = '#cc4444' if error else '#bbb'
        self._status_bar.setStyleSheet(f'color: {color}; font-size: 11px;')
        self._status_bar.setText(msg)
        self._side_status.setStyleSheet(f'color: {color}; font-size: 11px;')
        self._side_status.setText(msg)
        # Show progress on the preview image area when running
        if self._is_running:
            self._preview.set_overlay_text(msg)
        else:
            self._preview.set_overlay_text('')

    def _check_numpy(self):
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

    def showEvent(self, e):
        super().showEvent(e)
        # Reset running state in case dock was closed mid-operation
        if self._is_running:
            self._is_running = False
            self._rerun_timer.stop()
            QApplication.restoreOverrideCursor()
            self._set_status('Ready')

        # Auto-separate when user opens dock after startup.
        # _startup is True for the first 5s after __init__ to skip
        # Krita's initial showEvent during app launch.
        if not self._startup and not self._has_result and not self._is_running:
            app = Krita.instance()
            doc = app.activeDocument() if app else None
            if doc and doc.colorModel() == 'LABA':
                QTimer.singleShot(200, self._on_separate)

    def canvasChanged(self, canvas):
        """Called by Krita when the active canvas changes (document open/switch).

        Invalidate cached pixels/result so the next separation reads the new document.
        """
        # Reset all cached state from the previous document
        self._proxy_pixels = None
        self._result = None
        self._has_result = False
        self._archetype_scores = []
        self._archetype_list = []
        self._others_expanded = False
        self._last_archetype_id = ''
        self._preview.clear_images()
        self._clear_swatches()
        self._archetype_combo.blockSignals(True)
        self._archetype_combo.clear()
        self._archetype_combo.blockSignals(False)
        self._btn_build.setVisible(False)
        self._set_status('Ready')

        # Do NOT auto-separate here — Krita fires canvasChanged on document
        # open, and the dock may report isVisible() from saved state in kritarc,
        # causing the dock to pop up. User must click Separate Colors or
        # re-open the dock via Settings > Dockers.
