# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Single toolbar command: Create Mold.

Everything lives in one wizard panel now. This module registers that one
command and provides helpers for the view provider context menu.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui  # type: ignore

from ..app import mold_job
from . import tasks

ICONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "icons"
)

_registered = False


def icon(name):
    return os.path.join(ICONS, name)


def active_job():
    """The job to act on: the selected one, else the only one in the document."""
    document = App.ActiveDocument
    if document is None:
        return None
    for obj in Gui.Selection.getSelection():
        if mold_job.is_job(obj):
            return obj
        parent = getattr(obj, "InList", [])
        for candidate in parent:
            if mold_job.is_job(candidate):
                return candidate
    jobs = [obj for obj in document.Objects if mold_job.is_job(obj)]
    if len(jobs) == 1:
        return jobs[0]
    return None


class CreateMold(object):
    """Open the mold wizard. If a job already exists, re-edit it."""

    name = "Mold_Create"

    def GetResources(self):
        return {
            "Pixmap": icon("mold_wizard.svg"),
            "MenuText": "Create Mold",
            "ToolTip": "Open the mold wizard to design a two-piece silicone injection mold",
        }

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        job = active_job()
        tasks.show_wizard(job)


COMMANDS = [CreateMold]


def register():
    global _registered
    if _registered:
        return
    for cls in COMMANDS:
        Gui.addCommand(cls.name, cls())
    _registered = True


def run(command_name):
    Gui.runCommand(command_name, 0)
