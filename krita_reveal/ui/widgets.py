"""
widgets.py — Generic reusable UI controls for Krita Reveal.
"""

from __future__ import annotations
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy, QSlider, QComboBox, QCheckBox, QLineEdit, QApplication,
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal as Signal, QPoint, QRect, QRectF
from PyQt5.QtGui import (
    QImage, QPixmap, QColor, QPainter, QPen, QPalette, QPainterPath, 
    QFont, QFontDatabase, QRadialGradient, QLinearGradient, QBrush
)


class _LoupeOverlay(QWidget):
    RADIUS = 60
    ZOOM = 0

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        
        # Ensure it behaves like a transparent overlay window
        self.setWindowFlags(Qt.SubWindow | Qt.FramelessWindowHint)
        self.setFixedSize(self.RADIUS * 2, self.RADIUS * 2)
        
        # Physical circular mask using QBitmap to guarantee clipping
        from PyQt5.QtGui import QBitmap
        mask = QBitmap(self.RADIUS * 2, self.RADIUS * 2)
        mask.clear()
        mp = QPainter(mask)
        mp.setBrush(Qt.color1) # '1' is opaque in mask
        mp.drawEllipse(0, 0, self.RADIUS * 2, self.RADIUS * 2)
        mp.end()
        self.setMask(mask)
        
        self.setVisible(False)
        self._loupe_px = None

    def update_loupe(self, cursor_pos, source_px, label_widget):
        if source_px is None:
            self.setVisible(False)
            return
        img_w = source_px.width()
        img_h = source_px.height()
        lbl_w = label_widget.width()
        lbl_h = label_widget.height()
        scale = min(lbl_w / img_w, lbl_h / img_h)
        disp_w = int(img_w * scale)
        disp_h = int(img_h * scale)
        rel_x = cursor_pos.x() - (lbl_w - disp_w) / 2.0
        rel_y = cursor_pos.y() - (lbl_h - disp_h) / 2.0
        if rel_x < 0 or rel_y < 0 or rel_x >= disp_w or rel_y >= disp_h:
            self.setVisible(False)
            return
        sample_r = self.RADIUS / (self.ZOOM * scale)
        rx = max(0, int(rel_x / scale - sample_r))
        ry = max(0, int(rel_y / scale - sample_r))
        rw = min(int(sample_r * 2), img_w - rx)
        rh = min(int(sample_r * 2), img_h - ry)
        if rw <= 0 or rh <= 0:
            self.setVisible(False)
            return
        self._loupe_px = source_px.copy(rx, ry, rw, rh).scaled(
            self.RADIUS * 2, self.RADIUS * 2,
            Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        x = cursor_pos.x() + 20
        y = cursor_pos.y() - self.RADIUS * 2 - 10
        if x + self.RADIUS * 2 > self.parentWidget().width():
            x = cursor_pos.x() - self.RADIUS * 2 - 20
        if y < 0:
            y = cursor_pos.y() + 20
        self.move(x, y)
        self.setVisible(True)
        self.update()

    def paintEvent(self, e):
        if self._loupe_px is None:
            return
        d = self.RADIUS * 2
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # Force clear with transparency
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        
        path = QPainterPath()
        path.addEllipse(0, 0, d, d)
        p.setClipPath(path)
        p.drawPixmap(0, 0, self._loupe_px)
        p.setClipping(False)
        p.setPen(QPen(QColor('#888'), 2))
        p.drawEllipse(1, 1, d - 2, d - 2)
        p.setPen(QPen(QColor(255, 255, 255, 100), 1))
        p.drawLine(self.RADIUS - 5, self.RADIUS, self.RADIUS + 5, self.RADIUS)
        p.drawLine(self.RADIUS, self.RADIUS - 5, self.RADIUS, self.RADIUS + 5)


class _RevealSlider(QWidget):
    valueChanged = Signal()

    def __init__(self, key, label, min_val, max_val, default, step, fmt_fn, help_text=None):
        super().__init__()
        self._key = key
        self._default = default
        self._archetype_default = default
        self._fmt = fmt_fn
        self._programmatic = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        
        row = QHBoxLayout()
        row.setSpacing(4)
        self._label = QLabel(label)
        self._label.setStyleSheet('color: #e0e0e0; font-size: 11px;')
        row.addWidget(self._label, 1)
        
        self._revert_btn = QPushButton('↻')
        self._revert_btn.setFixedSize(18, 18)
        self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #666; font-size: 13px; }')
        self._revert_btn.setVisible(False)
        self._revert_btn.clicked.connect(self._revert)
        row.addWidget(self._revert_btn)
        
        self._val_label = QLabel()
        self._val_label.setStyleSheet('color: #ccc; font-size: 11px;')
        self._val_label.setMinimumWidth(36)
        self._val_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._val_label)
        
        self._help_btn = QPushButton('?')
        self._help_btn.setFixedSize(14, 14)
        self._help_btn.setStyleSheet(
            'QPushButton { background: none; border: 1px solid #444; color: #777; '
            'font-size: 9px; border-radius: 7px; } QPushButton:hover { color: #bbb; border-color: #666; }'
        )
        self._help_btn.clicked.connect(self._toggle_help)
        row.addWidget(self._help_btn)
        layout.addLayout(row)
        
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setFocusPolicy(Qt.StrongFocus)
        self._slider.installEventFilter(self)
        self._slider.setMinimumHeight(18)
        self._step = step
        self._min = min_val
        self._max = max_val
        self._ticks = round((max_val - min_val) / step)
        self._slider.setRange(0, self._ticks)
        self._slider.setValue(round((default - self._min) / self._step))
        self._slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._slider)
        
        self._help = None
        if help_text:
            self._help = QLabel(help_text, wordWrap=True)
            self._help.setStyleSheet(
                'color: #1a1a1a; background: #fff3b0; border: 1px solid #e0c860; '
                'border-radius: 4px; padding: 6px 8px; margin: 4px 0 6px 0; '
                'font-size: 11px; line-height: 1.4;'
            )
            self._help.setVisible(False)
            layout.addWidget(self._help)
        self._update_display()

    def value(self):
        return round(self._min + self._slider.value() * self._step, 6)

    def set_value(self, val, programmatic=True):
        self._programmatic = programmatic
        self._slider.setValue(round((val - self._min) / self._step))
        self._programmatic = False

    def set_archetype_default(self, val):
        self._archetype_default = val
        self._update_display()

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
            self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }')

    def _revert(self):
        self.set_value(self._archetype_default, programmatic=False)

    def _toggle_help(self):
        if self._help:
            self._help.setVisible(not self._help.isVisible())

    def set_help_visible(self, visible):
        if self._help:
            self._help.setVisible(visible)

    def eventFilter(self, obj, event):
        if obj is self._slider and event.type() == event.Wheel and not self._slider.hasFocus():
            return True
        return super().eventFilter(obj, event)


class _RevealCombo(QWidget):
    valueChanged = Signal()

    def __init__(self, key, label, options, default, help_text=None):
        super().__init__()
        self._key = key
        self._default = default
        self._archetype_default = default
        self._programmatic = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        
        row = QHBoxLayout()
        row.setSpacing(6)
        self._label = QLabel(label)
        self._label.setStyleSheet('color: #e0e0e0; font-size: 11px;')
        row.addWidget(self._label)
        
        self._revert_btn = QPushButton('↻')
        self._revert_btn.setFixedSize(18, 18)
        self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #666; font-size: 13px; }')
        self._revert_btn.setVisible(False)
        self._revert_btn.clicked.connect(self._revert)
        row.addStretch()
        row.addWidget(self._revert_btn)
        
        self._combo = QComboBox()
        self._combo.setFocusPolicy(Qt.StrongFocus)
        self._combo.installEventFilter(self)
        self._combo.setStyleSheet(
            'QComboBox { background: #2a2a2a; border: 1px solid #555; color: #ccc; '
            'font-size: 11px; padding: 3px 6px; border-radius: 3px; }'
        )
        for val, txt in options:
            self._combo.addItem(txt, val)
        idx = self._combo.findData(default)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        else:
            self._combo.setCurrentIndex(0)
        self._combo.currentIndexChanged.connect(self._on_changed)
        row.addWidget(self._combo)
        
        self._help_btn = QPushButton('?')
        self._help_btn.setFixedSize(14, 14)
        self._help_btn.setStyleSheet(
            'QPushButton { background: none; border: 1px solid #444; color: #777; '
            'font-size: 9px; border-radius: 7px; } QPushButton:hover { color: #bbb; border-color: #666; }'
        )
        self._help_btn.clicked.connect(self._toggle_help)
        row.addWidget(self._help_btn)
        
        layout.addLayout(row)
        self._help = None
        if help_text:
            self._help = QLabel(help_text, wordWrap=True)
            self._help.setStyleSheet(
                'color: #1a1a1a; background: #fff3b0; border: 1px solid #e0c860; '
                'border-radius: 4px; padding: 6px 8px; margin: 4px 0 6px 0; '
                'font-size: 11px; line-height: 1.4;'
            )
            self._help.setVisible(False)
            layout.addWidget(self._help)

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
        is_dirty = str(self.value()) != str(self._archetype_default)
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }')

    def _on_changed(self, idx):
        is_dirty = str(self.value()) != str(self._archetype_default)
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }')
        if not self._programmatic:
            self.valueChanged.emit()

    def _revert(self):
        self.set_value(self._archetype_default, programmatic=False)

    def _toggle_help(self):
        if self._help:
            self._help.setVisible(not self._help.isVisible())

    def set_help_visible(self, visible):
        if self._help:
            self._help.setVisible(visible)

    def eventFilter(self, obj, event):
        if obj is self._combo and event.type() == event.Wheel and not self._combo.hasFocus():
            return True
        return super().eventFilter(obj, event)


class _RevealCheck(QWidget):
    valueChanged = Signal()

    def __init__(self, key, label, default, help_text=None):
        super().__init__()
        self._key = key
        self._default = default
        self._archetype_default = default
        self._programmatic = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        
        row = QHBoxLayout()
        row.setSpacing(4)
        self._label = QLabel(label)
        self._label.setStyleSheet('color: #e0e0e0; font-size: 11px;')
        row.addWidget(self._label, 1)
        
        self._revert_btn = QPushButton('↻')
        self._revert_btn.setFixedSize(18, 18)
        self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #666; font-size: 13px; }')
        self._revert_btn.setVisible(False)
        self._revert_btn.clicked.connect(self._revert)
        row.addWidget(self._revert_btn)
        
        self._check = QCheckBox()
        self._check.setStyleSheet(
            'QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #666; '
            'border-radius: 2px; background: #2a2a2a; } QCheckBox::indicator:checked { '
            'background: #4090c0; border-color: #60b0e0; }'
        )
        self._check.setChecked(default)
        self._check.stateChanged.connect(self._on_changed)
        row.addWidget(self._check)
        
        self._help_btn = QPushButton('?')
        self._help_btn.setFixedSize(14, 14)
        self._help_btn.setStyleSheet(
            'QPushButton { background: none; border: 1px solid #444; color: #777; '
            'font-size: 9px; border-radius: 7px; } QPushButton:hover { color: #bbb; border-color: #666; }'
        )
        self._help_btn.clicked.connect(self._toggle_help)
        row.addWidget(self._help_btn)
        
        layout.addLayout(row)
        self._help = None
        if help_text:
            self._help = QLabel(help_text, wordWrap=True)
            self._help.setStyleSheet(
                'color: #1a1a1a; background: #fff3b0; border: 1px solid #e0c860; '
                'border-radius: 4px; padding: 6px 8px; margin: 4px 0 6px 0; '
                'font-size: 11px; line-height: 1.4;'
            )
            self._help.setVisible(False)
            layout.addWidget(self._help)

    def value(self):
        return self._check.isChecked()

    def set_value(self, val, programmatic=True):
        self._programmatic = programmatic
        self._check.setChecked(bool(val))
        self._programmatic = False

    def set_archetype_default(self, val):
        self._archetype_default = val
        is_dirty = self.value() != self._archetype_default
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }')

    def _on_changed(self, state):
        is_dirty = self.value() != self._archetype_default
        self._revert_btn.setVisible(is_dirty)
        if is_dirty:
            self._revert_btn.setStyleSheet('QPushButton { background: none; border: none; color: #4da6ff; font-size: 11px; }')
        if not self._programmatic:
            self.valueChanged.emit()

    def _revert(self):
        self.set_value(self._archetype_default, programmatic=False)

    def _toggle_help(self):
        if self._help:
            self._help.setVisible(not self._help.isVisible())

    def set_help_visible(self, visible):
        if self._help:
            self._help.setVisible(visible)
