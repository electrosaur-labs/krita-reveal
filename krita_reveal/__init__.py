"""
krita_reveal — Reveal colour separation plugin for Krita.

Entry point: registers the RevealDock panel and the
"Separate Colours" action on plugin load.
"""

import sys
import os

# Vendor pyreveal (no pip in Krita's bundled Python)
_vendor = os.path.join(os.path.dirname(__file__), 'vendor')
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)

from krita import DockWidgetFactory, DockWidgetFactoryBase
from .dock import RevealDock

DOCKER_ID = 'krita_reveal_dock'


def createInstance():
    return Application


def setup():
    app = Krita.instance()
    dock_factory = DockWidgetFactory(
        DOCKER_ID,
        DockWidgetFactoryBase.DockRight,
        RevealDock,
    )
    app.addDockWidgetFactory(dock_factory)
