# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""A form that edits job properties directly.

Every task panel in this workbench is a list of parameters with a preview
underneath, so rather than hand build each one, a panel declares which
properties it shows and this builds the widgets. The properties stay the
single source of truth, which means the property editor, the panels and the
saved file can never drift apart.
"""

from .qt import QtCore, QtWidgets, Signal


def group(title):
    return ("group", title)


def row(prop, label, kind="length", **options):
    return ("row", prop, label, kind, options)


def note(text):
    return ("note", text)


class ParamForm(QtWidgets.QWidget):
    """Builds widgets from a spec and writes straight back to the object."""

    changed = Signal()

    def __init__(self, obj, spec, parent=None):
        super(ParamForm, self).__init__(parent)
        self.obj = obj
        self.widgets = {}
        self._loading = False

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        form = None

        for entry in spec:
            if entry[0] == "group":
                label = QtWidgets.QLabel("<b>%s</b>" % entry[1])
                label.setContentsMargins(0, 8, 0, 2)
                layout.addWidget(label)
                form = QtWidgets.QFormLayout()
                form.setLabelAlignment(QtCore.Qt.AlignRight)
                layout.addLayout(form)
            elif entry[0] == "note":
                label = QtWidgets.QLabel(entry[1])
                label.setWordWrap(True)
                label.setStyleSheet("color: palette(mid);")
                layout.addWidget(label)
            else:
                _, prop, label, kind, options = entry
                if not hasattr(obj, prop):
                    continue
                if form is None:
                    form = QtWidgets.QFormLayout()
                    layout.addLayout(form)
                widget = self._make(prop, kind, options)
                if widget is None:
                    continue
                self.widgets[prop] = (widget, kind)
                if kind == "bool":
                    widget.setText(label)
                    form.addRow("", widget)
                else:
                    form.addRow(label + ":", widget)
        layout.addStretch(1)
        self.load()

    def _make(self, prop, kind, options):
        if kind in ("length", "float", "angle", "percent"):
            widget = QtWidgets.QDoubleSpinBox()
            widget.setDecimals(options.get("decimals", 2))
            widget.setRange(options.get("minimum", -100000.0), options.get("maximum", 100000.0))
            widget.setSingleStep(options.get("step", 0.5))
            suffix = {"length": " mm", "angle": " deg", "percent": " %"}.get(kind, "")
            widget.setSuffix(options.get("suffix", suffix))
            widget.valueChanged.connect(self._on_change)
            return widget
        if kind == "int":
            widget = QtWidgets.QSpinBox()
            widget.setRange(options.get("minimum", 0), options.get("maximum", 999))
            widget.valueChanged.connect(self._on_change)
            return widget
        if kind == "bool":
            widget = QtWidgets.QCheckBox()
            widget.toggled.connect(self._on_change)
            return widget
        if kind == "enum":
            widget = QtWidgets.QComboBox()
            widget.addItems(list(self.obj.getEnumerationsOfProperty(prop)))
            widget.currentIndexChanged.connect(self._on_change)
            return widget
        if kind == "path":
            widget = _PathChooser()
            widget.changed.connect(self._on_change)
            return widget
        return None

    def load(self):
        """Pull current property values into the widgets."""
        self._loading = True
        try:
            for prop, (widget, kind) in self.widgets.items():
                value = getattr(self.obj, prop)
                if kind in ("length", "angle"):
                    widget.setValue(float(value))
                elif kind in ("float", "percent"):
                    widget.setValue(float(value))
                elif kind == "int":
                    widget.setValue(int(value))
                elif kind == "bool":
                    widget.setChecked(bool(value))
                elif kind == "enum":
                    index = widget.findText(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                elif kind == "path":
                    widget.set_path(str(value or ""))
        finally:
            self._loading = False

    def apply(self):
        """Write widget values back to the object."""
        for prop, (widget, kind) in self.widgets.items():
            if kind in ("length", "angle", "float", "percent"):
                setattr(self.obj, prop, float(widget.value()))
            elif kind == "int":
                setattr(self.obj, prop, int(widget.value()))
            elif kind == "bool":
                setattr(self.obj, prop, bool(widget.isChecked()))
            elif kind == "enum":
                setattr(self.obj, prop, str(widget.currentText()))
            elif kind == "path":
                setattr(self.obj, prop, widget.path())

    def _on_change(self, *_args):
        if self._loading:
            return
        self.changed.emit()


class _PathChooser(QtWidgets.QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super(_PathChooser, self).__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QtWidgets.QLineEdit()
        self.button = QtWidgets.QToolButton()
        self.button.setText("Browse")
        layout.addWidget(self.edit)
        layout.addWidget(self.button)
        self.button.clicked.connect(self._browse)
        self.edit.editingFinished.connect(self.changed.emit)

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose an output folder", self.edit.text())
        if folder:
            self.edit.setText(folder)
            self.changed.emit()

    def path(self):
        return self.edit.text()

    def set_path(self, value):
        self.edit.setText(value)
