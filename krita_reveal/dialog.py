"""
dialog.py — RevealDialog: free-floating window hosting RevealPanel.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QDialog, QVBoxLayout
from PyQt5.QtCore import Qt

from .panel import RevealPanel


class RevealDialog(QDialog):

    def __init__(self, parent=None):
        # Qt.Window keeps it as a free-floating OS window;
        # parent ties its lifetime to Krita's main window.
        super().__init__(parent, Qt.Window)
        self.setWindowTitle('Reveal Separation')
        self.setMinimumSize(520, 460)
        self.resize(680, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._panel = RevealPanel(self)
        layout.addWidget(self._panel)
