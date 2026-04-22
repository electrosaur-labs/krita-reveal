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
        from ..constants import log
        log("LayoutManager: Building UI with synchronized header styling.")
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
        
        # --- HEADER ROW (FLAT & TIGHT) ---
        crow = QHBoxLayout()
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(6) # Match JS gap: 6px
        
        # 1. Stats (Left Sticky) - Exact JS CSS Mapping
        def stat_lbl(css):
            l = QLabel('')
            l.setStyleSheet(css)
            l.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            l.setContentsMargins(0, 0, 0, 0)
            return l

        # .stat-archetype-label { font-size: 16px; font-weight: 700; color: #e0e0e0; }
        self.dock._stat_archetype = stat_lbl('color: #e0e0e0; font-weight: 700; font-size: 16px;')
        
        # .stat-val { font-size: 14px; color: #ccc; }
        self.dock._stat_colors = stat_lbl('color: #ccc; font-size: 14px;')
        self.dock._stat_delta = stat_lbl('color: #8bb8e8; font-weight: 600; font-size: 14px;')
        self.dock._stat_dna = stat_lbl('color: #ccc; font-size: 14px;')
        self.dock._stat_match = stat_lbl('color: #e0c97f; font-weight: 600; font-size: 14px;')

        def dot():
            l = QLabel('·')
            l.setStyleSheet('color: #555; font-size: 14px; font-weight: bold;')
            l.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            return l

        self.dock._sep_colors = dot()
        self.dock._sep_delta = dot()
        self.dock._sep_dna = dot()
        self.dock._sep_match = dot()
        
        # Add stats items to layout
        for w in [self.dock._stat_archetype, self.dock._sep_colors, self.dock._stat_colors, 
                  self.dock._sep_delta, self.dock._stat_delta, self.dock._sep_dna, 
                  self.dock._stat_dna, self.dock._sep_match, self.dock._stat_match]:
            crow.addWidget(w, 0, Qt.AlignLeft)
            w.setVisible(False)
            
        # PUSH EVERYTHING ELSE TO THE RIGHT
        crow.addStretch(1) 
        
        # 2. Controls (Right Sticky)
        cs = 'QComboBox { background: #383838; border: 1px solid #555; color: #aaa; font-size: 10px; padding: 1px 4px; border-radius: 3px; }'
        ls = 'color: #888; font-size: 10px;'
        
        l_res = QLabel('Resolution', styleSheet=ls)
        self.dock._proxy_combo = QComboBox()
        self.dock._proxy_combo.setStyleSheet(cs)
        self.dock._proxy_combo.blockSignals(True)
        for v, t in [('1000', '1000px'), ('1500', '1500px'), ('2000', '2000px')]:
            self.dock._proxy_combo.addItem(t, v)
        self.dock._proxy_combo.setCurrentIndex(0)
        self.dock._proxy_combo.blockSignals(False)
        self.dock._proxy_combo.currentIndexChanged.connect(self.dock._on_separate)
        
        l_loupe = QLabel('Loupe', styleSheet=ls)
        self.dock._loupe_mag_combo = QComboBox()
        self.dock._loupe_mag_combo.setStyleSheet(cs)
        for v, t in [(0, 'None'), (1, '1:1'), (2, '1:2'), (4, '1:4'), (8, '1:8')]:
            self.dock._loupe_mag_combo.addItem(t, v)
        self.dock._loupe_mag_combo.setCurrentIndex(0)
        self.dock._loupe_mag_combo.currentIndexChanged.connect(self.dock._on_loupe_mag_changed)
        
        crow.addWidget(l_res, 0, Qt.AlignRight)
        crow.addWidget(self.dock._proxy_combo, 0, Qt.AlignRight)
        crow.addSpacing(6)
        crow.addWidget(l_loupe, 0, Qt.AlignRight)
        crow.addWidget(self.dock._loupe_mag_combo, 0, Qt.AlignRight)
        
        llay.addLayout(crow)

        self.dock._preview = _PreviewLabel()
        self.dock._preview.clicked.connect(self.dock._on_preview_clicked)
        llay.addWidget(self.dock._preview, 1)
        
        self.dock._status_bar = QLabel('Ready')
        self.dock._status_bar.setStyleSheet('color: #bbb; font-size: 11px;')
        llay.addWidget(self.dock._status_bar)

        def make_help(title, help_text):
            container = QWidget()
            vlay = QVBoxLayout(container)
            vlay.setContentsMargins(0, 0, 0, 0)
            vlay.setSpacing(2)
            
            hrow = QHBoxLayout()
            hrow.setContentsMargins(2, 0, 2, 0)
            hrow.addWidget(QLabel(title, styleSheet='color: #aaa; font-size: 9px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: bold;'))
            hrow.addStretch()
            
            btn = QPushButton('?')
            btn.setFixedSize(16, 16)
            btn.setStyleSheet('QPushButton { background: none; border: 1px solid #444; color: #777; font-size: 10px; border-radius: 2px; } QPushButton:hover { color: #fff; border-color: #666; } QPushButton.active { color: #4da6ff; border-color: #4da6ff; }')
            hrow.addWidget(btn)
            vlay.addLayout(hrow)
            
            htxt = QLabel(help_text)
            htxt.setWordWrap(True)
            htxt.setStyleSheet('color: #1a1a1a; background: #fff3b0; border: 1px solid #e0c860; border-radius: 4px; padding: 6px 8px; font-size: 11px; margin: 4px 0;')
            htxt.setVisible(False)
            vlay.addWidget(htxt)
            
            btn.clicked.connect(lambda: (
                htxt.setVisible(not htxt.isVisible()),
                btn.setProperty('class', 'active' if htxt.isVisible() else ''),
                btn.style().unpolish(btn), btn.style().polish(btn)
            ))
            return container, vlay

        # 1. Palette Surgeon
        pal_box, pal_vlay = make_help('Palette', 
            'Your extracted color palette. Click a swatch to isolate that color in the preview. '
            'Ctrl+click opens the color picker for manual editing. Alt+click deletes a color '
            'and redistributes its pixels to the nearest neighbor.')
        llay.addWidget(pal_box)

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

        # 2. Suggested Colors
        sug_box, sug_vlay = make_help('Suggested', 
            'Minority hues missing from the current palette. Click a swatch to see how '
            'injecting that color would affect the separation (What-if mode). '
            'Double-click to permanently add the color to your palette.')
        self.dock._suggested_widget = sug_box
        self.dock._suggested_widget.setVisible(False)
        
        self.dock._suggested_grid = QHBoxLayout()
        self.dock._suggested_grid.setSpacing(4)
        sug_vlay.addLayout(self.dock._suggested_grid)
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
        parent_layout.addWidget(right)
