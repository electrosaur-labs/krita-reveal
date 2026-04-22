"""
preview.py — Preview label with blink comparator and loupe support.
"""

from __future__ import annotations
import os
from PyQt5.QtWidgets import QLabel, QSizePolicy, QWidget
from PyQt5.QtCore import QTimer, Qt, pyqtSignal as Signal, QRect, QRectF
from PyQt5.QtGui import (
    QPixmap, QColor, QPainter, QPen, QFont, QFontDatabase, 
    QRadialGradient, QLinearGradient, QBrush
)

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
        
        # --- Splash Mode Assets ---
        self._assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ui', 'assets')
        self._logo = QPixmap(os.path.join(self._assets_dir, 'electrosaur.png'))
        font_id = QFontDatabase.addApplicationFont(os.path.join(self._assets_dir, 'Anton-Regular.ttf'))
        font_families = QFontDatabase.applicationFontFamilies(font_id)
        self._anton_family = font_families[0] if font_families else 'sans-serif'
        
        self._steps = [] 
        self._progress_pct = 0
        
        # Styles
        self._brand_font = QFont(self._anton_family, 28)
        self._tagline_font = QFont('sans-serif', 9)
        self._tagline_font.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        self._log_header_font = QFont('monospace', 9)
        self._log_header_font.setBold(True)
        self._log_font = QFont('monospace', 10)
        self._color_ok = QColor('#50b070')
        self._color_active = QColor('#e0c040')
        self._color_dim = QColor('#888888')
        
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(300)
        self._hold_timer.timeout.connect(self._start_blink)
        
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(400)
        self._blink_timer.timeout.connect(self._blink_tick)
        
        self._loupe = _LoupeOverlay(self)

    # --- Splash Control Methods ---
    def set_progress(self, pct):
        self._progress_pct = pct
        self.update()

    def add_step(self, label, status="ACTIVE"):
        if self._steps and self._steps[-1][1] == "ACTIVE":
            self._steps[-1] = (self._steps[-1][0], "OK")
        self._steps.append((label, status))
        self.update()

    def finish_steps(self):
        if self._steps and self._steps[-1][1] == "ACTIVE":
            self._steps[-1] = (self._steps[-1][0], "OK")
        self._progress_pct = 100
        self.update()

    def clear_steps(self):
        self._steps = []
        self._progress_pct = 0
        self.update()

    # --- Image Methods ---
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
        self.clear_steps()
        self.update()
        self.unsetCursor()
        self._loupe.setVisible(False)

    def _current_source_px(self):
        if self._showing_orig:
            return self._orig_px
        return self._post_px

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        
        px = self._current_source_px()
        if px:
            # Draw Image
            p.fillRect(self.rect(), QColor('#111'))
            cw, ch = self.width(), self.height()
            pw, ph = px.width(), px.height()
            if cw > 0 and ch > 0 and pw > 0 and ph > 0:
                s = min(cw / pw, ch / ph)
                dw, dh = int(pw * s), int(ph * s)
                p.drawPixmap((cw - dw) // 2, (ch - dh) // 2, dw, dh, px)
        else:
            # Draw Splash Screen (Integrated)
            grad = QRadialGradient(self.width() * 0.4, self.height() * 0.4, self.width() * 0.8)
            grad.setColorAt(0.0, QColor('#d0d0d0'))
            grad.setColorAt(0.3, QColor('#a8a8a8'))
            grad.setColorAt(0.7, QColor('#787878'))
            grad.setColorAt(1.0, QColor('#505050'))
            p.fillRect(self.rect(), grad)
            
            center_x = self.width() // 2
            curr_y = 40
            
            if not self._logo.isNull():
                lw = self.width()
                lh = int(lw * (self._logo.height() / self._logo.width()))
                p.drawPixmap(0, curr_y, lw, lh, self._logo)
                curr_y += lh + 10
            
            p.setPen(QPen(QColor(0, 0, 0, 40), 1))
            p.drawLine(center_x - 30, curr_y, center_x + 30, curr_y)
            curr_y += 15

            brand_f = QFont(self._brand_font)
            brand_f.setLetterSpacing(QFont.AbsoluteSpacing, 10)
            p.setFont(brand_f)
            p.setPen(QColor(255, 255, 255, 80)) # Shadow
            p.drawText(QRect(0, curr_y + 1, self.width(), 40), Qt.AlignCenter, "REVEAL")
            p.setPen(QColor('#444444')) # Main
            p.drawText(QRect(0, curr_y, self.width(), 40), Qt.AlignCenter, "REVEAL")
            curr_y += 45
            
            p.setFont(self._tagline_font)
            p.setPen(QColor('#555555'))
            p.drawText(QRect(0, curr_y, self.width(), 20), Qt.AlignCenter, "COLOR SEPARATION ENGINE")
            curr_y += 40
            
            box_w = min(400, self.width() - 40)
            box_h = 220
            box_x = center_x - box_w // 2
            box_y = curr_y
            
            p.setPen(QPen(QColor(80, 80, 80, 128), 1))
            p.setBrush(QColor(0, 0, 0, 140))
            p.drawRoundedRect(box_x, box_y, box_w, box_h, 6, 6)
            
            p.setFont(self._log_header_font)
            p.setPen(QColor('#aaaaaa'))
            p.drawText(box_x + 20, box_y + 30, "PROCESS LOG [v1.0.2]")
            
            p.setFont(self._log_font)
            step_y = box_y + 60
            steps_to_show = self._steps[-6:] if self._steps else [("Awaiting Document", "READY")]
            for label, status in steps_to_show:
                p.setPen(QColor('#cccccc'))
                p.drawText(box_x + 20, step_y, label.upper() + "...")
                if status == "OK": p.setPen(self._color_ok); st = "[OK]"
                elif status == "ACTIVE": p.setPen(self._color_active); st = "[ACTIVE]"
                else: p.setPen(self._color_dim); st = f"[{status}]"
                p.drawText(box_x + box_w - 80, step_y, st)
                step_y += 20
                
            bar_y, bar_h = box_y + box_h - 40, 6
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 25))
            p.drawRoundedRect(box_x + 20, bar_y, box_w - 40, bar_h, 3, 3)
            
            if self._progress_pct > 0:
                prog_grad = QLinearGradient(box_x + 20, 0, box_x + 20 + int((box_w - 40) * (self._progress_pct / 100.0)), 0)
                prog_grad.setColorAt(0.0, QColor('#888888')); prog_grad.setColorAt(1.0, QColor('#cccccc'))
                p.setBrush(prog_grad)
                p.drawRoundedRect(box_x + 20, bar_y, int((box_w - 40) * (self._progress_pct / 100.0)), bar_h, 3, 3)

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
