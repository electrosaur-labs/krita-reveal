"""
krita_reveal — Reveal colour separation plugin for Krita.

Registers a DockWidget (Settings > Dockers > Reveal Separation).
"""

import sys
import os

_vendor = os.path.join(os.path.dirname(__file__), 'vendor')
if _vendor not in sys.path:
    sys.path.insert(0, _vendor)

# Compiled packages (numpy) live outside the app bundle in the user data dir
from .platform_paths import krita_data_dir
_packages = os.path.join(krita_data_dir(), 'python_packages')
if os.path.isdir(_packages) and _packages not in sys.path:
    sys.path.insert(0, _packages)

from krita import DockWidgetFactory, DockWidgetFactoryBase

from .dock import RevealDock
from .extension import RevealExtension

# Register DockWidget (UI panel)
Application.addDockWidgetFactory(
    DockWidgetFactory(
        'reveal_separation_v2',
        DockWidgetFactoryBase.DockMinimized,
        RevealDock,
    )
)

# Register Extension (Tools > Scripts entry)
Krita.instance().addExtension(RevealExtension(Krita.instance()))


def setup():
    pass
