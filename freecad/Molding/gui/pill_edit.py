# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""A read-only "line edit" that shows its entries as removable pills.

Used next to the Pick Location(s) buttons: every picked element (``Vertex3``,
``Face12`` ...) becomes a small pill with an x on it, so one pick can be
dropped without clearing the rest.
"""

from .qt import QtCore, QtGui, QtWidgets, Signal


class FlowLayout(QtWidgets.QLayout):
    """Left to right layout that wraps onto new rows when it runs out of width."""

    def __init__(self, parent=None, h_spacing=4, v_spacing=4):
        super().__init__(parent)
        self._items = []
        self._h = h_spacing
        self._v = v_spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        # Tell the layout engine we want horizontal space
        return QtCore.Qt.Orientation.Horizontal

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, dry_run=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QtCore.QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, dry_run):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = area.x()
        y = area.y()
        row_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if next_x > area.right() + 1 and row_height > 0:
                x = area.x()
                y += row_height + self._v
                next_x = x + hint.width()
                row_height = 0
            if not dry_run:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x = next_x + self._h
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + m.bottom()


def _mix(a, b, t):
    """Colour *t* of the way from *a* to *b* (t = 0 gives a, t = 1 gives b)."""
    return QtGui.QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class _CloseLabel(QtWidgets.QLabel):
    """A simple clickable label to replace QToolButton, bypassing button stylesheets."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__("\u00d7", parent)  # multiplication sign
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        # --- TEXT SIZE CONTROL ---
        font = self.font()
        # Bold removed so the "x" stays thin and crisp

        # Safely increase font size so it's larger but not overwhelmingly chunky
        if font.pointSize() > 0:
            font.setPointSize(font.pointSize() + 4)
        elif font.pixelSize() > 0:
            font.setPixelSize(font.pixelSize() + 4)
        else:
            font.setPointSize(14)

        self.setFont(font)
        # -------------------------

        self.setMinimumSize(18, 18)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class _Pill(QtWidgets.QWidget):
    """One entry: its text and a remove button.

    Inherits from QWidget (not QFrame) to ensure the app's global stylesheets
    do not overwrite or hide our custom paintEvent background and border.
    """

    def __init__(self, text, on_remove, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)

        label = QtWidgets.QLabel(text)

        button = _CloseLabel()
        button.setToolTip("Remove %s" % text)
        button.clicked.connect(on_remove)

        layout.addWidget(label)
        layout.addWidget(button)

    def paintEvent(self, event):
        palette = self.palette()
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        base = palette.color(QtGui.QPalette.ColorRole.Base)

        if base.lightness() < 128 and text.lightness() < 128:
            text = QtGui.QColor(230, 230, 230)
        elif base.lightness() >= 128 and text.lightness() >= 128:
            text = QtGui.QColor(30, 30, 30)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Mix 15% text color into the base for the pill background
        painter.setBrush(_mix(base, text, 0.15))

        # Mix 60% text color into the base for a strong, highly visible border
        painter.setPen(QtGui.QPen(_mix(base, text, 0.60), 1))

        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 4.0
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class PillEdit(QtWidgets.QFrame):
    """Read-only field whose entries are pills with a remove button each."""

    # Emitted with the index of the pill whose x was pressed.
    pillRemoved = Signal(int)

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self._labels = []

        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
        self.setAutoFillBackground(True)
        self.setBackgroundRole(QtGui.QPalette.ColorRole.Base)
        self.setMinimumHeight(28)

        # Policy Minimum instead of Preferred makes it tightly hug wrapped rows
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )

        self._flow = FlowLayout(self)
        self._flow.setContentsMargins(4, 3, 4, 3)

        self._placeholder = QtWidgets.QLabel(placeholder)
        palette = self._placeholder.palette()
        text = palette.color(QtGui.QPalette.ColorRole.Text)
        base = palette.color(QtGui.QPalette.ColorRole.Base)
        hint = QtGui.QColor(
            (text.red() + base.red()) // 2,
            (text.green() + base.green()) // 2,
            (text.blue() + base.blue()) // 2,
        )
        palette.setColor(QtGui.QPalette.ColorRole.WindowText, hint)
        self._placeholder.setPalette(palette)
        self._rebuild()

    # -- Dynamic layout forwarding ------------------------------------------

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._flow.heightForWidth(width)

    def sizeHint(self):
        # Force the panel to request horizontal space so it doesn't
        # squish our flow layout into a single vertical column
        w = 200
        return QtCore.QSize(w, self._flow.heightForWidth(w))

    def minimumSizeHint(self):
        return self._flow.minimumSize()

    # -- QLineEdit-like API -------------------------------------------------

    def setPlaceholderText(self, text):
        self._placeholder.setText(text)

    def labels(self):
        return list(self._labels)

    def setLabels(self, labels):
        self._labels = [str(label) for label in labels]
        self._rebuild()

    def clear(self):
        self.setLabels([])

    # -- internals ----------------------------------------------------------

    def _rebuild(self):
        while self._flow.count():
            item = self._flow.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._placeholder:
                widget.setParent(None)
                widget.deleteLater()
            elif widget is self._placeholder:
                widget.hide()

        if not self._labels:
            self._flow.addWidget(self._placeholder)
            self._placeholder.show()
        else:
            for index, text in enumerate(self._labels):
                pill = _Pill(text, lambda i=index: self.pillRemoved.emit(i), self)
                self._flow.addWidget(pill)
                pill.show()
        self._flow.invalidate()
        self.updateGeometry()
