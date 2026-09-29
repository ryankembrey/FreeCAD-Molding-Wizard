# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Tree presentation for a mould job."""

import os

ICONS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "icons")


class MoldJobViewProvider(object):
    def __init__(self, view_object):
        view_object.Proxy = self

    def attach(self, view_object):
        self.ViewObject = view_object
        self.Object = view_object.Object

    def getIcon(self):
        return os.path.join(ICONS, "mold_job.svg")

    def setupContextMenu(self, view_object, menu):
        from . import tasks

        obj = view_object.Object
        menu.addAction("Edit mold", lambda: tasks.show_wizard(obj))

    def doubleClicked(self, view_object):
        from . import tasks

        tasks.show_wizard(view_object.Object)
        return True

    def claimChildren(self):
        try:
            return list(self.Object.Group)
        except Exception:
            return []

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps
    __setstate__ = loads
