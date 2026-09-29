# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

import os

import FreeCADGui as Gui  # type: ignore

from .gui import commands as _mold_commands

_ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons")

_mold_commands.register()


class MoldingWorkbench(Gui.Workbench):
    MenuText = "Molding"
    ToolTip = "Design a printable two-piece silicone injection mold around any solid"
    Icon = os.path.join(_ICONS, "mold_wizard.svg")

    def Initialize(self):
        self.appendToolbar("Molding", ["Mold_Create"])
        self.appendMenu("Molding", ["Mold_Create"])

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def ContextMenu(self, recipient):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(MoldingWorkbench())
