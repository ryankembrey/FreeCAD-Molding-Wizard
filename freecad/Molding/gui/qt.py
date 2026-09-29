# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""One import site for Qt, so the rest of the GUI never has to care which
binding this FreeCAD build shipped with."""

try:
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401

    BINDING = "PySide6"
except ImportError:  # pragma: no cover
    try:
        from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401

        BINDING = "PySide2"
    except ImportError:  # pragma: no cover
        from PySide import QtCore, QtGui  # noqa: F401

        QtWidgets = QtGui
        BINDING = "PySide"

Signal = getattr(QtCore, "Signal", None) or QtCore.pyqtSignal
