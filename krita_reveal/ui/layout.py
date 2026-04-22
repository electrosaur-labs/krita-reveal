"""
layout.py — Construction of the main RevealDock user interface.
"""

from __future__ import annotations
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QFrame,
    QSizePolicy, QComboBox, QScrollArea, QApplication,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette

from .preview import _PreviewLabel


class LayoutManager:
    def __init__(self, dock):
        self.dock = dock

    def build_ui(self):
        root = QWidget(self.dock._root_container)
        self.setup_root_style(root)
        self.dock._main_layout.addWidget(root)
        
        mlay = QHBoxLayout(root)
        mlay.setContentsMargins(0, 0, 0, 0)
        mlay.setSpacing(0)

        self.build_left_column(mlay)
        self.build_right_column(mlay)

    def setup_root_style(self, root):
        pal = root.palette()
        color_map = [
            (QPalette.Window, '#323232'),
            (QPalette.WindowText, '#e0e0e0'),
            (QPalette.Base, '#2a2a2a'),
            (QPalette.Text, '#e0e0e0'),
            (QPalette.Button, '#3a3a3a'),
            (QPalette.ButtonText, '#e0e0e0')
        ]
        for role, color in color_map:
            pal.setColor(role, QColor(color))
        root.setPalette(pal)
        root.setAutoFillBackground(True)

    def line_sep(self):
        s = QFrame()
        s.setFixedHeight(3)
        s.setStyleSheet('background-color: #666; margin-top: 10px; margin-bottom: 10px;')
        return s

    def build_left_column(self, parent_layout):
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setContentsMargins(8, 8, 8, 8)
        llay.setSpacing(6)
        
        crow = QHBoxLayout()
        crow.setSpacing(8)
        self.dock._stat_archetype = QLabel('')
        self.dock._stat_archetype.setStyleSheet('color: #e0e0e0; font-weight: 700; font-size: 16px;')
        self.dock._stat_colors = QLabel('')
        self.dock._stat_colors.setStyleSheet('color: #ccc; font-size: 14px;')
        self.dock._stat_delta = QLabel('')
        self.dock._stat_delta.setStyleSheet('color: #8bb8e8; font-weight: 600; font-size: 14px;')
        self.dock._stat_dna = QLabel('')
        self.dock._stat_dna.setStyleSheet('color: #ccc; font-size: 14px;')
        self.dock._stat_match = QLabel('')
        self.dock._stat_match.setStyleSheet('color: #ccc; font-size: 14px;')

        def dot():
            l = QLabel('·')
            l.setStyleSheet('color: #555; font-size: 14px; margin: 0 3px;')
            return l

        self.dock._sep_colors = dot()
        self.dock._sep_delta = dot()
        self.dock._sep_dna = dot()
        self.dock._sep_match = dot()
        
        stat_widgets = [
            self.dock._stat_archetype, self.dock._sep_colors, self.dock._stat_colors, 
            self.dock._sep_delta, self.dock._stat_delta, self.dock._sep_dna, 
            self.dock._stat_dna, self.dock._sep_match, self.dock._stat_match
        ]
        for w in stat_widgets:
            crow.addWidget(w)
            w.setVisible(False)
        
        crow.addStretch()
        cs = 'QComboBox { background: #2a2a2a; border: 1px solid #444; color: #ccc; font-size: 10px; padding: 1px 4px; border-radius: 2px; }'
        ls = 'color: #888; font-size: 10px;'
        
        crow.addWidget(QLabel('Resolution', styleSheet=ls))
        self.dock._proxy_combo = QComboBox()
        self.dock._proxy_combo.setStyleSheet(cs)
        self.dock._proxy_combo.blockSignals(True)
        for v, t in [('1000', '1000'), ('1500', '1500'), ('2000', '2000')]:
            self.dock._proxy_combo.addItem(t, v)
        self.dock._proxy_combo.setCurrentIndex(0)
        self.dock._proxy_combo.blockSignals(False)
        self.dock._proxy_combo.currentIndexChanged.connect(self.dock._on_separate)
        crow.addWidget(self.dock._proxy_combo)
        
        crow.addWidget(QLabel('Loupe', styleSheet=ls))
        self.dock._loupe_mag_combo = QComboBox()
        self.dock._loupe_mag_combo.setStyleSheet(cs)
        for v, t in [(0, 'None'), (1, '1:1'), (2, '1:2'), (4, '1:4'), (8, '1:8')]:
            self.dock._loupe_mag_combo.addItem(t, v)
        self.dock._loupe_mag_combo.setCurrentIndex(0)
        self.dock._loupe_mag_combo.currentIndexChanged.connect(self.dock._on_loupe_mag_changed)
        crow.addWidget(self.dock._loupe_mag_combo)
        llay.addLayout(crow)

        self.dock._preview = _PreviewLabel()
        self.dock._preview.clicked.connect(self.dock._on_preview_clicked)
        llay.addWidget(self.dock._preview, 1)
        
        self.dock._status_bar = QLabel('Ready')
        self.dock._status_bar.setStyleSheet('color: #bbb; font-size: 11px;')
        llay.addWidget(self.dock._status_bar)

        self.dock._surgeon_widget = QWidget()
        self.dock._surgeon_widget.setStyleSheet('background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 3px;')
        self.dock._surgeon_layout = QHBoxLayout(self.dock._surgeon_widget)
        self.dock._surgeon_layout.setContentsMargins(4, 4, 4, 4)
        self.dock._surgeon_layout.setSpacing(2)
        self.dock._surgeon_widget.setVisible(False)
        llay.addWidget(self.dock._surgeon_widget)
        
        self.dock._add_color_btn = QPushButton('+')
        self.dock._add_color_btn.setFixedSize(24, 24)
        self.dock._add_color_btn.setStyleSheet(
            'QPushButton { background: #2a2a2a; border: 1px solid #555; color: #aaa; '
            'font-size: 14px; font-weight: bold; border-radius: 3px; } '
            'QPushButton:hover { color: #fff; border-color: #888; background: #3a3a3a; }'
        )
        self.dock._add_color_btn.setVisible(False)
        self.dock._add_color_btn.clicked.connect(self.dock._on_add_color)

        self.dock._suggested_widget = QWidget()
        self.dock._suggested_widget.setVisible(False)
        slay = QVBoxLayout(self.dock._suggested_widget)
        slay.setContentsMargins(0, 4, 0, 0)
        slay.addWidget(QLabel('SUGGESTED', styleSheet='color: #aaa; font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px;'))
        self.dock._suggested_grid = QHBoxLayout()
        self.dock._suggested_grid.setSpacing(4)
        slay.addLayout(self.dock._suggested_grid)
        llay.addWidget(self.dock._suggested_widget)
        parent_layout.addWidget(left, 1)

    def build_right_column(self, parent_layout):
        right = QWidget()
        right.setFixedWidth(320)
        right.setStyleSheet('background: #323232; border-left: 1px solid #3a3a3a;')
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        
        bcol = QVBoxLayout()
        bcol.setContentsMargins(7, 8, 7, 8)
        bcol.setSpacing(4)
        bs = 'QPushButton { border-radius: 3px; font-size: 11px; font-weight: 600; padding: 6px 8px; }'
        
        self.dock._btn_reread = QPushButton('Reread Document')
        self.dock._btn_reread.setStyleSheet(bs + 'QPushButton { background: #1a2a3a; border: 1px solid #3070a0; color: #60b0ff; }')
        self.dock._btn_reread.clicked.connect(self.dock._on_reread)
        bcol.addWidget(self.dock._btn_reread)
        
        self.dock._btn_reset = QPushButton('Reset to Defaults')
        self.dock._btn_reset.setStyleSheet(bs + 'QPushButton { background: #3a3020; border: 1px solid #7a6530; color: #e0a030; }')
        self.dock._btn_reset.clicked.connect(self.dock._reset_all)
        bcol.addWidget(self.dock._btn_reset)
        
        self.dock._btn_separate = QPushButton('Separate')
        self.dock._btn_separate.setStyleSheet(
            bs + 'QPushButton { background: #1a3a2a; border: 1px solid #307050; color: #60c090; } '
            'QPushButton:disabled { background: #222; border-color: #444; color: #666; }'
        )
        self.dock._btn_separate.setEnabled(False)
        self.dock._btn_separate.clicked.connect(self.dock._on_build_layers)
        bcol.addWidget(self.dock._btn_separate)
        rlay.addLayout(bcol)

        self.dock._main_scroll = QScrollArea()
        self.dock._main_scroll.setWidgetResizable(True)
        self.dock._main_scroll.setFrameShape(QFrame.NoFrame)
        self.dock._main_scroll.setStyleSheet('background: transparent;')
        scrw = QWidget()
        self.dock._scroll_layout = QVBoxLayout(scrw)
        self.dock._scroll_layout.setContentsMargins(8, 4, 8, 8)
        self.dock._scroll_layout.setSpacing(0)
        
        hs = (
            'QPushButton { background: none; border: none; color: #e0e0e0; font-size: 14px; '
            'font-weight: 600; text-align: left; padding: 6px 0; margin-top: 4px; '
            'letter-spacing: 0.5px; } QPushButton:hover { color: #fff; }'
        )
        
        hrow = QHBoxLayout()
        hrow.setContentsMargins(0, 4, 0, 4)
        hrow.addWidget(QLabel('Basic', styleSheet='color: #e0e0e0; font-size: 14px; font-weight: 600; letter-spacing: 0.5px;'))
        hrow.addStretch()
        hbtn = QPushButton('?')
        hbtn.setFixedSize(20, 18)
        hbtn.setStyleSheet('QPushButton { background: none; border: 1px solid #444; color: #777; font-size: 9px; border-radius: 3px; }')
        hbtn.clicked.connect(self.dock._toggle_help)
        hrow.addWidget(hbtn)
        self.dock._scroll_layout.addLayout(hrow)
        self.dock._scroll_layout.addWidget(self.line_sep())
        
        arch = QVBoxLayout()
        arch.setContentsMargins(0, 4, 0, 4)
        arch.setSpacing(2)
        arch.addWidget(QLabel('Archetype', styleSheet='color: #e0e0e0; font-size: 11px; letter-spacing: 0.6px;'))
        self.dock._archetype_combo = QComboBox()
        self.dock._archetype_combo.setStyleSheet(
            'QComboBox { background: #252525; border: 1px solid #3a6a3a; color: #80c080; '
            'font-size: 11px; padding: 2px 5px; border-radius: 3px; }'
        )
        self.dock._archetype_combo.currentIndexChanged.connect(self.dock._on_archetype_changed)
        arch.addWidget(self.dock._archetype_combo)
        self.dock._scroll_layout.addLayout(arch)

        self.dock._basic_container = QWidget()
        self.dock._basic_layout = QVBoxLayout(self.dock._basic_container)
        self.dock._basic_layout.setContentsMargins(0, 0, 0, 0)
        self.dock._basic_layout.setSpacing(0)
        self.dock._scroll_layout.addWidget(self.dock._basic_container)
        
        self.dock._sp_toggle = QPushButton('▶  Screen Printing')
        self.dock._sp_toggle.setStyleSheet(hs)
        self.dock._sp_toggle.clicked.connect(self.dock._toggle_sp)
        self.dock._scroll_layout.addWidget(self.dock._sp_toggle)
        self.dock._sp_container = QWidget()
        self.dock._sp_layout = QVBoxLayout(self.dock._sp_container)
        self.dock._sp_layout.setContentsMargins(0, 0, 0, 8)
        self.dock._sp_layout.setSpacing(0)
        self.dock._sp_container.setVisible(False)
        self.dock._scroll_layout.addWidget(self.dock._sp_container)
        
        self.dock._adv_toggle = QPushButton('▶  Advanced')
        self.dock._adv_toggle.setStyleSheet(hs)
        self.dock._adv_toggle.clicked.connect(self.dock._toggle_advanced)
        self.dock._scroll_layout.addWidget(self.dock._adv_toggle)
        self.dock._adv_container = QWidget()
        self.dock._advanced_layout = QVBoxLayout(self.dock._adv_container)
        self.dock._advanced_layout.setContentsMargins(0, 0, 0, 8)
        self.dock._advanced_layout.setSpacing(0)
        self.dock._adv_container.setVisible(False)
        self.dock._scroll_layout.addWidget(self.dock._adv_container)
        
        self.dock._scroll_layout.addStretch()
        self.dock._main_scroll.setWidget(scrw)
        rlay.addWidget(self.dock._main_scroll, 1)
        self.dock._side_status = QLabel('')
        self.dock._side_status.setWordWrap(True)
        self.dock._side_status.setStyleSheet('color: #bbb; font-size: 11px; margin: 4px 7px;')
        rlay.addWidget(self.dock._side_status)
        parent_layout.addWidget(right)
