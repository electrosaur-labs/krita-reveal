"""
preview.py — Preview label with blink comparator and loupe support.
"""

from __future__ import annotations
from PyQt5.QtWidgets import QLabel, QSizePolicy
from PyQt5.QtCore import QTimer, Qt, pyqtSignal as Signal, QRect
from PyQt5.QtGui import QPixmap, QColor, QPainter

from .widgets import _LoupeOverlay


class _PreviewLabel(QLabel):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(100)
        self.setStyleSheet('background: #111; border: 1px solid #444; border-radius: 3px;')
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
        self.update()
        self.setCursor(Qt.PointingHandCursor)

    def clear_images(self):
        self._blink_timer.stop()
        self._hold_timer.stop()
        self._orig_px = None
        self._post_px = None
        self.update()
        self.unsetCursor()
        self._loupe.setVisible(False)

    def update_post(self, post_px: QPixmap):
        self._post_px = post_px
        self.update()

    def _current_source_px(self):
        if self._showing_orig:
            return self._orig_px
        return self._post_px

    def set_overlay_text(self, text):
        self._overlay_text = text
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor('#111'))
        
        px = self._current_source_px()
        if px:
            cw = self.width()
            ch = self.height()
            pw = px.width()
            ph = px.height()
            if cw > 0 and ch > 0 and pw > 0 and ph > 0:
                s = min(cw / pw, ch / ph)
                dw = int(pw * s)
                dh = int(ph * s)
                dx = (cw - dw) // 2
                dy = (ch - dh) // 2
                p.drawPixmap(dx, dy, dw, dh, px)
                
        if self._overlay_text:
            from PyQt5.QtGui import QFont
            font = QFont('sans-serif', 13)
            font.setBold(True)
            p.setFont(font)
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

    def mousePressEvent(self, e):
        self._loupe.setVisible(False)
        if self._orig_px:
            self._hold_timer.start()

    def mouseReleaseEvent(self, e):
        if self._blink_timer.isActive():
            self._blink_timer.stop()
            self._showing_orig = False
            self.update()
        elif self._hold_timer.isActive():
            self._hold_timer.stop()
            self.clicked.emit()

    def mouseMoveEvent(self, e):
        src = self._current_source_px()
        if src and not self._blink_timer.isActive() and not self._hold_timer.isActive() and self._loupe.ZOOM > 0:
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
            self.update()

    def _start_blink(self):
        self._blink_timer.start()

    def _blink_tick(self):
        self._showing_orig = not self._showing_orig
        self.update()
