"""
palette.py — Palette swatch widgets and Lab color picker.
"""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy, QLineEdit, QApplication,
)
from PyQt5.QtCore import Qt, pyqtSignal as Signal, QPoint, QRect
from PyQt5.QtGui import QImage, QPixmap, QColor, QPainter, QPen


class _SwatchWidget(QWidget):
    clicked = Signal(int, object)
    merged = Signal(int, int)

    def __init__(self, idx, r, g, b, pct, is_deleted=False, merge_count=0):
        super().__init__()
        self.idx = idx
        self._r = r
        self._g = g
        self._b = b
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
        h = f'#{self._r:02X}{self._g:02X}{self._b:02X}'
        if self._is_deleted:
            return f'{h}  —\nAlt+click: restore'
        return (
            f'{h}  {self._pct:.1f}%\n'
            'Click: solo  Alt+click: delete  Ctrl+click: edit\n'
            'Drag onto another swatch to merge'
        )

    def set_selected(self, sel):
        self._selected = sel
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._is_deleted:
            p.fillRect(self.rect(), QColor('#2a1e1e'))
            b = QColor('#663333')
        elif self._selected:
            p.fillRect(self.rect(), QColor('#2d3545'))
            b = QColor('#4da6ff')
        else:
            p.fillRect(self.rect(), QColor('#2a2a2a'))
            b = QColor(0, 0, 0, 0)
        
        p.setPen(QPen(b, 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)
        p.fillRect(4, 3, 19, 19, QColor(self._r, self._g, self._b))
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.drawRect(4, 3, 19, 19)
        
        if self._is_deleted:
            p.fillRect(4, 3, 19, 19, QColor(0, 0, 0, 128))
            p.setPen(QColor('#ff6666'))
            p.drawText(QRect(4, 3, 19, 19), Qt.AlignCenter, '✕')
            
        if self._merge_count > 0 and not self._is_deleted:
            bdg = f'+{self._merge_count}'
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#4da6ff'))
            bwm = max(14, p.fontMetrics().horizontalAdvance(bdg) + 6)
            p.drawRoundedRect(4 + 19 - bwm + 4, 3 - 4, bwm, 14, 7, 7)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRect(4 + 19 - bwm + 4, 3 - 4, bwm, 14), Qt.AlignCenter, bdg)
            
        p.setPen(QColor('#ccc'))
        pct_text = '—' if self._is_deleted else f'{self._pct:.0f}%'
        p.drawText(QRect(4 + 19 + 3, 0, 40, self.height()), Qt.AlignVCenter, pct_text)

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
        d = QDrag(self)
        m = QMimeData()
        m.setText(str(self.idx))
        d.setMimeData(m)
        d.exec_(Qt.MoveAction)
        self._drag_start = None

    def mouseReleaseEvent(self, e):
        self._drag_start = None

    def dragEnterEvent(self, e):
        if e.mimeData().hasText() and not self._is_deleted:
            try:
                sidx = int(e.mimeData().text())
                if sidx != self.idx:
                    e.acceptProposedAction()
            except:
                pass

    def dropEvent(self, e):
        try:
            sidx = int(e.mimeData().text())
            if sidx != self.idx:
                self.merged.emit(sidx, self.idx)
            e.acceptProposedAction()
        except:
            pass


# ── Lab Color Picker ──────────────────────────────────────────────────────────

def _lab_to_rgb_fast(L, a, b):
    fy = (L + 16) / 116
    fx = a / 500 + fy
    fz = fy - b / 200
    delta = 6 / 29
    
    if fx > delta:
        xv = fx ** 3
    else:
        xv = 3 * delta * delta * (fx - 4 / 29)
    xv = xv * 0.95047
    
    if fy > delta:
        yv = fy ** 3
    else:
        yv = 3 * delta * delta * (fy - 4 / 29)
        
    if fz > delta:
        zv = fz ** 3
    else:
        zv = 3 * delta * delta * (fz - 4 / 29)
    zv = zv * 1.08883
    
    rl = xv * 3.2404542 + yv * -1.5371385 + zv * -0.4985314
    gl = xv * -0.9692660 + yv * 1.8760108 + zv * 0.0415560
    bl = xv * 0.0556434 + yv * -0.2040259 + zv * 1.0572252
    
    def gamma(v):
        if v <= 0.0031308:
            return 12.92 * v
        else:
            return 1.055 * (v ** (1.0 / 2.4)) - 0.055
            
    r = max(0, min(255, round(gamma(max(0.0, rl)) * 255)))
    g = max(0, min(255, round(gamma(max(0.0, gl)) * 255)))
    bv = max(0, min(255, round(gamma(max(0.0, bl)) * 255)))
    in_gamut = (0.0 <= rl <= 1.0 and 0.0 <= gl <= 1.0 and 0.0 <= bl <= 1.0)
    return r, g, bv, in_gamut


class _ABCanvas(QWidget):
    changed = Signal(float, float)

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
            self._cache_img = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w = self.width()
        h = self.height()
        if self._cache_img is None or self._cache_L != round(self._L, 1):
            self._cache_L = round(self._L, 1)
            buf = bytearray(w * h * 3)
            for py in range(h):
                bv = 127.0 - (py / (h - 1)) * 254.0
                for px in range(w):
                    av = -128.0 + (px / (w - 1)) * 255.0
                    r, g, b, ok = _lab_to_rgb_fast(self._L, av, bv)
                    off = (py * w + px) * 3
                    if ok:
                        buf[off:off + 3] = [r, g, b]
                    else:
                        buf[off:off + 3] = [
                            (r * 30 + 40 * 70) // 100, 
                            (g * 30 + 40 * 70) // 100, 
                            (b * 30 + 40 * 70) // 100
                        ]
            self._cache_img = QImage(bytes(buf), w, h, w * 3, QImage.Format_RGB888).copy()
        
        p.drawImage(0, 0, self._cache_img)
        cx = int(((self._a + 128) / 255) * (w - 1))
        cy = int(((127 - self._b) / 254) * (h - 1))
        pen_color = Qt.black if self._L > 50 else Qt.white
        p.setPen(QPen(pen_color, 1.5))
        p.drawEllipse(QPoint(cx, cy), 5, 5)

    def _pick(self, e):
        w = self.width()
        h = self.height()
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
        w = self.width()
        h = self.height()
        for px in range(w):
            L_val = (px / (w - 1)) * 100.0
            r, g, b, _ = _lab_to_rgb_fast(L_val, self._a, self._b)
            p.setPen(QColor(r, g, b))
            p.drawLine(px, 0, px, h - 1)
        
        cx = int((self._L / 100.0) * (w - 1))
        p.fillRect(cx - 2, 0, 4, h, Qt.white)
        p.setPen(QPen(Qt.black, 0.5))
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


class _LabPicker(QWidget):
    colorPicked = Signal(int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setFixedWidth(320)
        self.setStyleSheet('background: #2a2a2a; border: 1px solid #555; border-radius: 5px;')
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
            'font-size: 12px; padding: 4px 6px; border-radius: 3px; '
            'font-family: monospace;'
        )
        self._hex_input.textEdited.connect(self._on_hex_edited)
        self._hex_input.returnPressed.connect(self._apply)
        row.addWidget(self._hex_input)
        
        cb = QPushButton('Cancel')
        cb.setFixedWidth(52)
        cb.setStyleSheet(
            'font-size: 10px; padding: 3px 8px; background: #2a2a2a; '
            'border: 1px solid #555; color: #aaa; border-radius: 3px;'
        )
        cb.clicked.connect(self.hide)
        row.addWidget(cb)
        
        ok = QPushButton('OK')
        ok.setFixedWidth(44)
        ok.setStyleSheet(
            'font-size: 10px; padding: 3px 8px; background: #1a2a3a; '
            'border: 1px solid #3070a0; color: #60b0ff; border-radius: 3px;'
        )
        ok.clicked.connect(self._apply)
        row.addWidget(ok)
        layout.addLayout(row)
        
        self._L = 50.0
        self._a = 0.0
        self._b = 0.0
        self._target_idx = -1

    def open_for(self, idx, hex_color, anchor_widget):
        from ..vendor.pyreveal.color.encoding import rgb_to_lab
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
        from ..vendor.pyreveal.color.encoding import lab_to_rgb
        self._ab_widget.set_lab(self._L, self._a, self._b)
        self._l_widget.set_lab(self._L, self._a, self._b)
        r, g, b = lab_to_rgb(self._L, self._a, self._b)
        hex_str = f'#{r:02X}{g:02X}{b:02X}'
        self._preview.setStyleSheet(f'background: {hex_str}; border: 1px solid #555; border-radius: 3px;')
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
        from ..vendor.pyreveal.color.encoding import rgb_to_lab
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
        from ..vendor.pyreveal.color.encoding import lab_to_rgb
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


class PaletteManager:
    def __init__(self, dock):
        self.dock = dock
        self.swatch_widgets = []
        self.palette_data = []

    def render_swatches(self, pdat):
        self.clear_swatches()
        self.palette_data = pdat
        self.swatch_widgets = []
        for i, c in enumerate(pdat):
            sw = _SwatchWidget(
                i, c['r'], c['g'], c['b'], c['pct'], 
                is_deleted=c.get('is_deleted', False), 
                merge_count=c.get('merge_count', 0)
            )
            sw.clicked.connect(self.dock._on_swatch_clicked)
            sw.merged.connect(self.dock._on_swatch_merged)
            if i == self.dock._selected_idx:
                sw.set_selected(True)
            self.dock._surgeon_layout.addWidget(sw)
            self.swatch_widgets.append(sw)
        self.dock._surgeon_layout.addWidget(self.dock._add_color_btn)
        self.dock._add_color_btn.setVisible(True)
        self.dock._surgeon_layout.addStretch()
        self.dock._surgeon_widget.setVisible(True)

    def clear_swatches(self):
        while self.dock._surgeon_layout.count():
            item = self.dock._surgeon_layout.takeAt(0)
            w = item.widget()
            if w and w is not self.dock._add_color_btn:
                w.deleteLater()
        self.dock._add_color_btn.setVisible(False)
        self.swatch_widgets = []
        self.dock._surgeon_widget.setVisible(False)

    def update_selection(self, selected_idx):
        for sw in self.swatch_widgets:
            sw.set_selected(sw.idx == selected_idx)

    def effective_assignments(self, result):
        pal = result['palette']
        plab = result.get('palette_lab', [])
        live = [i for i, c in enumerate(pal) if not c.get('is_deleted')]
        if not live or len(live) == len(pal):
            return result['assignments']
            
        def dist(i, j):
            a, b = plab[i], plab[j]
            return (a['L'] - b['L'])**2 + (a['a'] - b['a'])**2 + (a['b'] - b['b'])**2
            
        remap = {}
        for i, c in enumerate(pal):
            if c.get('is_deleted'):
                target = c.get('_merge_target')
                if target is None or target not in live:
                    target = min(live, key=lambda j: dist(i, j))
                    c['_merge_target'] = target
                remap[i] = target
        return [remap.get(a, a) for a in result['assignments']]
