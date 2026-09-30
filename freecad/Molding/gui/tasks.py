# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Single wizard task panel for the entire mould workflow.

One panel, one command.  Two modes of interaction:

  * **Full panel** (default): every setting is visible at once for users who
    know what they want.
  * **Step-by-step wizard**: guides through each group one at a time, with
    Next / Back navigation.  Good for first time use.

The user picks a solid, sets the pull direction, toggles features on and off,
adjusts dimensions, and generates.  Every input lands on the MoldJob document
object as a property so it can be changed later and the mould recomputes.
"""

import os

import FreeCAD as App
import FreeCADGui as Gui  # type: ignore

from ..app import mold_job
from ..core import analysis
from ..core.frame import local_frame, to_local
from ..core.models import (
    BLOCK_STYLES,
    BOLT_SIZES,
    DEFAULTS,
    EMBOSS_PLACEMENTS,
    EMBOSS_SIDE_WALL,
    GUTTER_SIDES,
    INJECTION_LUER_LOCK,
    INJECTION_STYLES,
    LAYOUTS,
    LAYOUT_TWO,
    SYRINGE_SIZE_LIST,
    VENT_DIR_UP,
    VENT_DIRECTIONS,
    VENT_SHAPES,
)
from . import pick
from .qt import QtCore, QtGui, QtWidgets

ICONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resources",
    "icons",
)

PICK_BUTTON_STYLE = """
QPushButton:checked { border: 2px solid palette(highlight); font-weight: bold; }
"""

_WARNING_ICON = "⚠"   # ⚠
_SUCCESS_ICON = "✔"   # ✔
_INFO_ICON    = "ℹ"   # ℹ


# =============================================================================
# Event filters
# =============================================================================

class _EscapeFilter(QtCore.QObject):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() == QtCore.Qt.Key.Key_Escape and self._callback():
                return True
        return False


class _SpinBoxEnterFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.KeyPress:
            if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if hasattr(obj, "interpretText"):
                    obj.interpretText()
                obj.clearFocus()
                return True
        return False


# =============================================================================
# Wizard panel
# =============================================================================

# The ordered list of wizard steps. Each entry is (group builder name suffix,
# human label shown in the step header).
_WIZARD_STEPS = [
    ("object",    "Select Object"),
    ("pull",      "Pull Direction"),
    ("parting",   "Parting"),
    ("block",     "Mold Block"),
    ("filling",   "Filling Method"),
    ("overflow",  "Overflow Gutter"),
    ("vents",     "Vents"),
    ("keys",      "Registration Keys"),
    ("hardware",  "Hardware"),
    ("pry",       "Pry Slots"),
    ("emboss",    "Embossment"),
]


class MoldWizardPanel(object):
    """All-in-one wizard panel for creating and editing a mould job."""

    def __init__(self, job=None):
        self.job = job
        self.target_object = None
        self.target_shape = None
        self.pull_dir = App.Vector(0, 0, 1)
        self.pull_ref = "+Z (default)"
        self.pull_flipped = False
        self.picking_mode = None
        self.cursor_overridden = False
        self._built = False
        self._spinbox_enter_filter = _SpinBoxEnterFilter()

        # Custom picked positions
        self._custom_injection_pos = None   # App.Vector or None
        self._custom_vent_positions = []    # list of App.Vector

        # Last build result for feature label positions
        self._last_result = None

        # Viewport visuals (imported lazily so the module stays loadable
        # without pivy during unit tests).
        self._direction_indicator = None
        self._feature_labels = None

        # Wizard step tracking
        self._wizard_mode = False
        self._wizard_step = 0
        self._step_widgets = []    # populated after build

        self._build_form()
        Gui.Selection.addObserver(self)
        self._escape_filter = _EscapeFilter(self._on_escape)
        for panel in self.form:
            panel.installEventFilter(self._escape_filter)

        if job is not None:
            self._load_from_job(job)
        else:
            self._auto_select()
        self._update_generate_state()

    # ------------------------------------------------------------------
    # Layout helpers (matching the DFM panel style)
    # ------------------------------------------------------------------

    def _grid(self, box):
        g = QtWidgets.QGridLayout(box)
        g.setContentsMargins(8, 8, 8, 8)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(6)
        g.setColumnStretch(0, 1)
        g.setColumnStretch(1, 1)
        return g

    @staticmethod
    def _compact_combo(combo):
        combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(6)
        combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )

    def _spin(self, value=0.0, minimum=0.0, maximum=999.0, step=1.0,
              decimals=2, suffix=" mm"):
        sb = QtWidgets.QDoubleSpinBox()
        sb.setRange(minimum, maximum)
        sb.setDecimals(decimals)
        sb.setSingleStep(step)
        if suffix:
            sb.setSuffix(suffix)
        sb.setValue(value)
        sb.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        sb.installEventFilter(self._spinbox_enter_filter)
        return sb

    def _int_spin(self, value=0, minimum=0, maximum=64):
        sb = QtWidgets.QSpinBox()
        sb.setRange(minimum, maximum)
        sb.setValue(value)
        sb.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        sb.installEventFilter(self._spinbox_enter_filter)
        return sb

    # ------------------------------------------------------------------
    # Build the form
    # ------------------------------------------------------------------

    def _build_form(self):
        # FreeCAD stacks multiple form widgets as collapsible task panels.
        # Panel 1: Viewport (mode, explode, overlay toggles)
        # Panel 2: Wizard   (all feature groups + generate/clear)
        # Panel 3: Diagnostics (build results in a rich text browser)

        # -- Panel 1: Viewport --
        self.viewport_panel = QtWidgets.QWidget()
        self.viewport_panel.setWindowTitle("Viewport")
        self.viewport_panel.setWindowIcon(
            QtGui.QIcon(os.path.join(ICONS, "mold_wizard.svg"))
        )
        vp_layout = QtWidgets.QVBoxLayout(self.viewport_panel)
        vp_layout.setContentsMargins(6, 6, 6, 6)
        vp_layout.setSpacing(6)

        # Mode selector row
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(4)
        lbl_mode = QtWidgets.QLabel("Mode")
        lbl_mode.setToolTip(
            "Switch between seeing all settings or stepping "
            "through them one at a time."
        )
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems(["All settings", "Step-by-step"])
        self.cb_mode.setToolTip(
            "All settings: every section visible at once.\n"
            "Step-by-step: walk through each section with guided prompts."
        )
        self._compact_combo(self.cb_mode)
        self.cb_mode.currentIndexChanged.connect(
            lambda idx: self._toggle_wizard_mode(idx == 1)
        )
        mode_row.addWidget(lbl_mode, 1)
        mode_row.addWidget(self.cb_mode, 1)
        vp_layout.addLayout(mode_row)

        # Explode slider
        self._build_viewport_controls(vp_layout)

        # -- Panel 2: Wizard (scrollable feature groups + buttons) --
        self.wizard_panel = QtWidgets.QWidget()
        self.wizard_panel.setWindowTitle("Create Mold")

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        inner = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(inner)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        # Build each feature group
        self._build_object_group(root)
        self._build_pull_group(root)
        self._build_parting_group(root)
        self._build_block_group(root)
        self._build_filling_group(root)
        self._build_overflow_group(root)
        self._build_vents_group(root)
        self._build_keys_group(root)
        self._build_hardware_group(root)
        self._build_pry_group(root)
        self._build_emboss_group(root)
        root.addStretch(1)

        # Wizard navigation (hidden until wizard mode is on)
        self.wizard_nav = QtWidgets.QWidget()
        nav_layout = QtWidgets.QHBoxLayout(self.wizard_nav)
        nav_layout.setContentsMargins(0, 4, 0, 0)
        self.pb_wiz_back = QtWidgets.QPushButton("Back")
        self.pb_wiz_back.setMinimumHeight(28)
        self.pb_wiz_back.setToolTip("Return to the previous step.")
        self.pb_wiz_back.clicked.connect(self._wizard_back)
        self.pb_wiz_next = QtWidgets.QPushButton("Next")
        self.pb_wiz_next.setMinimumHeight(28)
        self.pb_wiz_next.setToolTip("Continue to the next step.")
        self.pb_wiz_next.clicked.connect(self._wizard_next)
        self.wiz_step_label = QtWidgets.QLabel()
        self.wiz_step_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        nav_layout.addWidget(self.pb_wiz_back)
        nav_layout.addWidget(self.wiz_step_label, 1)
        nav_layout.addWidget(self.pb_wiz_next)
        self.wizard_nav.hide()
        root.addWidget(self.wizard_nav)

        scroll_area.setWidget(inner)

        outer = QtWidgets.QVBoxLayout(self.wizard_panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(scroll_area, 1)

        # Generate / Clear buttons (always visible, below the scroll)
        btn_area = QtWidgets.QWidget()
        btn_layout = QtWidgets.QVBoxLayout(btn_area)
        btn_layout.setContentsMargins(6, 6, 6, 6)
        btn_layout.setSpacing(6)

        row = QtWidgets.QHBoxLayout()
        self.pb_generate = QtWidgets.QPushButton("Generate Mold")
        self.pb_generate.setMinimumHeight(32)
        self.pb_generate.setToolTip(
            "Build the mold bodies from the current settings. "
            "Re-run any time to update after changing parameters."
        )
        self.pb_generate.clicked.connect(self._on_generate)
        self.pb_clear = QtWidgets.QPushButton("Clear")
        self.pb_clear.setMinimumHeight(32)
        self.pb_clear.setEnabled(False)
        self.pb_clear.setToolTip("Remove the generated mold from the document.")
        self.pb_clear.clicked.connect(self._on_clear)
        row.addWidget(self.pb_generate, 1)
        row.addWidget(self.pb_clear, 1)
        btn_layout.addLayout(row)

        self.progress = QtWidgets.QProgressBar()
        self.progress.hide()
        btn_layout.addWidget(self.progress)

        outer.addWidget(btn_area)

        # -- Panel 3: Diagnostics (rich text browser for build results) --
        self.diag_panel = QtWidgets.QWidget()
        self.diag_panel.setWindowTitle("Diagnostics")
        diag_layout = QtWidgets.QVBoxLayout(self.diag_panel)
        diag_layout.setContentsMargins(0, 0, 0, 0)
        diag_layout.setSpacing(0)

        self.diag_browser = QtWidgets.QTextBrowser()
        self.diag_browser.setOpenExternalLinks(False)
        self.diag_browser.setMinimumHeight(60)
        self.diag_browser.setMaximumHeight(200)
        self.diag_browser.setPlaceholderText("Build results will appear here.")
        diag_layout.addWidget(self.diag_browser)

        # Expose all three panels as the form list
        self.form = [self.viewport_panel, self.wizard_panel, self.diag_panel]

    # --- Object selection ---

    def _build_object_group(self, root):
        box = QtWidgets.QGroupBox("Object")
        box.setToolTip("The solid body that will be encased in the mold.")
        g = self._grid(box)
        self.pb_object = QtWidgets.QPushButton("Select Object")
        self.pb_object.setCheckable(True)
        self.pb_object.setMinimumHeight(28)
        self.pb_object.setStyleSheet(PICK_BUTTON_STYLE)
        self.pb_object.setToolTip(
            "Click, then pick a solid body in the viewport. "
            "Or pre-select it before opening this panel."
        )
        self.pb_object.clicked.connect(self._on_pick_object)
        self.le_object = QtWidgets.QLineEdit()
        self.le_object.setReadOnly(True)
        self.le_object.setMinimumHeight(28)
        self.le_object.setPlaceholderText("No object selected")
        g.addWidget(self.pb_object, 0, 0)
        g.addWidget(self.le_object, 0, 1)

        self.sb_shrink = self._spin(
            value=DEFAULTS.get("Shrink", 0.0), minimum=0.0, maximum=10.0,
            step=0.1, decimals=3, suffix=" %",
        )
        self.sb_shrink.setToolTip(
            "Percentage the cavity is enlarged to compensate for material "
            "shrinkage during cure. Platinum cure silicone barely shrinks, "
            "so zero is usually fine."
        )
        g.addWidget(QtWidgets.QLabel("Cure shrink"), 1, 0)
        g.addWidget(self.sb_shrink, 1, 1)
        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Pull direction ---

    def _build_pull_group(self, root):
        box = QtWidgets.QGroupBox("Pull Direction")
        box.setToolTip(
            "The axis along which the mold halves separate. "
            "Pick a face normal or edge to define it, or use the default +Z."
        )
        g = self._grid(box)
        self.pb_pull = QtWidgets.QPushButton("Select Pull Direction")
        self.pb_pull.setCheckable(True)
        self.pb_pull.setMinimumHeight(28)
        self.pb_pull.setStyleSheet(PICK_BUTTON_STYLE)
        self.pb_pull.setToolTip(
            "Click a planar face to use its normal, or a straight edge "
            "to use its direction. Defaults to +Z."
        )
        self.pb_pull.clicked.connect(self._on_pick_pull)

        field = QtWidgets.QHBoxLayout()
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(4)
        self.le_pull = QtWidgets.QLineEdit()
        self.le_pull.setReadOnly(True)
        self.le_pull.setMinimumHeight(28)
        self.le_pull.setText(self.pull_ref)

        # Flip button: larger, readable, with proper font size
        self.flip_btn = QtWidgets.QPushButton("⇅  Flip")
        self.flip_btn.setMinimumHeight(28)
        self.flip_btn.setMinimumWidth(60)
        self.flip_btn.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.flip_btn.setToolTip("Reverse the pull direction by 180 degrees.")
        self.flip_btn.clicked.connect(self._on_flip_pull)
        field.addWidget(self.le_pull, 1)
        field.addWidget(self.flip_btn, 0)
        fw = QtWidgets.QWidget()
        fw.setLayout(field)

        g.addWidget(self.pb_pull, 0, 0)
        g.addWidget(fw, 0, 1)
        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Parting ---

    def _build_parting_group(self, root):
        box = QtWidgets.QGroupBox("Parting")
        box.setToolTip(
            "How the mold splits apart. Two piece is the standard "
            "top/bottom split. Three piece adds a second vertical cut "
            "through one half for parts with deep undercuts."
        )
        g = self._grid(box)

        lbl_layout = QtWidgets.QLabel("Layout")
        lbl_layout.setToolTip("Number of mold pieces.")
        g.addWidget(lbl_layout, 0, 0)
        self.cb_layout = QtWidgets.QComboBox()
        self.cb_layout.addItems(LAYOUTS)
        self.cb_layout.setCurrentText(LAYOUT_TWO)
        self._compact_combo(self.cb_layout)
        self.cb_layout.setToolTip(
            "Two piece: simple top/bottom.\n"
            "Three piece: one half is split again by a vertical plane, "
            "useful when the part cannot be pulled straight out."
        )
        self.cb_layout.currentTextChanged.connect(self._on_layout_changed)
        g.addWidget(self.cb_layout, 0, 1)

        self.sb_parting = self._spin(
            value=0.0, minimum=-500.0, maximum=500.0, step=0.5
        )
        self.sb_parting.setToolTip(
            "Height of the parting plane measured from the bottom of the part "
            "along the pull direction. The auto-suggest places it at the widest "
            "cross section."
        )
        g.addWidget(QtWidgets.QLabel("Parting offset"), 1, 0)
        g.addWidget(self.sb_parting, 1, 1)

        lbl_angle = QtWidgets.QLabel("Secondary angle")
        lbl_angle.setToolTip(
            "Rotation of the second splitting plane around the pull axis. "
            "Only used for three piece layouts."
        )
        g.addWidget(lbl_angle, 2, 0)
        self.sb_secondary_angle = self._spin(
            value=0.0, minimum=0.0, maximum=360.0, step=5.0,
            decimals=1, suffix="°"
        )
        self.sb_secondary_angle.setToolTip(
            "Angle in degrees. 0 splits along X, 90 along Y, etc."
        )
        self.sb_secondary_angle.setEnabled(False)
        g.addWidget(self.sb_secondary_angle, 2, 1)

        self.pb_parting_pick = QtWidgets.QPushButton("Pick Parting Geometry")
        self.pb_parting_pick.setCheckable(True)
        self.pb_parting_pick.setMinimumHeight(28)
        self.pb_parting_pick.setStyleSheet(PICK_BUTTON_STYLE)
        self.pb_parting_pick.setToolTip(
            "Click a vertex, edge, or face on the part to place the parting "
            "plane at that height along the pull direction. The plane passes "
            "through the centroid of the picked geometry."
        )
        self.pb_parting_pick.clicked.connect(self._on_pick_parting)
        g.addWidget(self.pb_parting_pick, 3, 0, 1, 2)

        root.addWidget(box)
        self._step_widgets.append(box)

    def _on_layout_changed(self, text):
        three_piece = text != LAYOUT_TWO
        self.sb_secondary_angle.setEnabled(three_piece)

    # --- Block ---

    def _build_block_group(self, root):
        box = QtWidgets.QGroupBox("Mold Block")
        box.setToolTip(
            "Shape and wall dimensions of the outer mold body that "
            "surrounds the part."
        )
        g = self._grid(box)

        lbl = QtWidgets.QLabel("Shape")
        lbl.setToolTip("Box gives flat clamping surfaces. Cylinder saves material on round parts.")
        g.addWidget(lbl, 0, 0)
        self.cb_block_style = QtWidgets.QComboBox()
        self.cb_block_style.addItems(BLOCK_STYLES)
        self.cb_block_style.setToolTip(
            "Box: rectangular block with flat faces for clamping.\n"
            "Cylinder: round outer profile, saves material on round parts."
        )
        self._compact_combo(self.cb_block_style)
        g.addWidget(self.cb_block_style, 0, 1)

        self.sb_wall = self._spin(value=DEFAULTS["WallThickness"])
        self.sb_wall.setToolTip("Thickness of the mold walls around the sides of the cavity.")
        self.sb_floor = self._spin(value=DEFAULTS["FloorThickness"])
        self.sb_floor.setToolTip("Thickness below the part (bottom of the lower mold half).")
        self.sb_roof = self._spin(value=DEFAULTS["RoofThickness"])
        self.sb_roof.setToolTip("Thickness above the part (top of the upper mold half).")
        self.sb_fillet = self._spin(value=0.0, step=0.5)
        self.sb_fillet.setToolTip(
            "Corner radius on the outer block edges. "
            "Zero gives sharp corners. A small fillet makes the print friendlier."
        )

        g.addWidget(QtWidgets.QLabel("Wall"), 1, 0)
        g.addWidget(self.sb_wall, 1, 1)
        g.addWidget(QtWidgets.QLabel("Floor"), 2, 0)
        g.addWidget(self.sb_floor, 2, 1)
        g.addWidget(QtWidgets.QLabel("Roof"), 3, 0)
        g.addWidget(self.sb_roof, 3, 1)
        g.addWidget(QtWidgets.QLabel("Fillet"), 4, 0)
        g.addWidget(self.sb_fillet, 4, 1)
        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Injection ---

    def _build_filling_group(self, root):
        box = QtWidgets.QGroupBox("Filling Method")
        box.setToolTip(
            "How silicone enters the mold. Syringe injection uses a "
            "Luer lock port; pour port is an open funnel at the top; "
            "or choose None for a plain sealed mold."
        )
        g = self._grid(box)

        # Method selector
        lbl_method = QtWidgets.QLabel("Method")
        lbl_method.setToolTip("Choose how to fill the mold cavity.")
        g.addWidget(lbl_method, 0, 0)
        self.cb_fill_method = QtWidgets.QComboBox()
        self.cb_fill_method.addItems(["Syringe injection", "Pour port", "None"])
        self.cb_fill_method.setCurrentText("Syringe injection")
        self._compact_combo(self.cb_fill_method)
        self.cb_fill_method.setToolTip(
            "Syringe injection: Luer lock adapter recess in the block wall.\n"
            "Pour port: open funnel from the top of the block.\n"
            "None: no filling port (e.g. for open-face molds)."
        )
        self.cb_fill_method.currentTextChanged.connect(self._on_fill_method_changed)
        g.addWidget(self.cb_fill_method, 0, 1)

        # Overpour %
        self.sb_overpour = self._spin(
            value=DEFAULTS["OverpourPercent"],
            minimum=0.0, maximum=100.0, step=1.0,
            decimals=0, suffix=" %"
        )
        self.sb_overpour.setToolTip(
            "Extra silicone beyond the cavity volume to account for "
            "waste, sprue, and shrinkage."
        )
        g.addWidget(QtWidgets.QLabel("Overpour"), 1, 0)
        g.addWidget(self.sb_overpour, 1, 1)

        # -- Syringe sub-widgets --
        self.injection_widgets = []

        lbl_syr = QtWidgets.QLabel("Syringe size")
        lbl_syr.setToolTip("Barrel volume sets the Luer taper dimensions (ISO 80369-7).")
        g.addWidget(lbl_syr, 2, 0)
        self.cb_syringe = QtWidgets.QComboBox()
        self.cb_syringe.addItems(SYRINGE_SIZE_LIST)
        self.cb_syringe.setCurrentText("10 mL")
        self._compact_combo(self.cb_syringe)
        self.cb_syringe.setToolTip(
            "Barrel size of the syringe you will use. Syringes up to 20 mL "
            "share the same tip; 30 mL and above are slightly larger."
        )
        g.addWidget(self.cb_syringe, 2, 1)
        self.injection_widgets.extend([lbl_syr, self.cb_syringe])

        lbl_style = QtWidgets.QLabel("Adapter style")
        lbl_style.setToolTip("How the syringe connects to the mold.")
        g.addWidget(lbl_style, 3, 0)
        self.cb_inj_style = QtWidgets.QComboBox()
        self.cb_inj_style.addItems(INJECTION_STYLES)
        self.cb_inj_style.setCurrentText(INJECTION_LUER_LOCK)
        self._compact_combo(self.cb_inj_style)
        self.cb_inj_style.setToolTip(
            "Luer Lock: collar recess so a locking syringe clicks in.\n"
            "Friction Fit: plain tapered hole for a slip-tip syringe."
        )
        g.addWidget(self.cb_inj_style, 3, 1)
        self.injection_widgets.extend([lbl_style, self.cb_inj_style])

        self.sb_inj_dia = self._spin(value=DEFAULTS["InjectionDiameter"], step=0.5)
        self.sb_inj_dia.setToolTip(
            "Bore diameter of the injection channel from the cavity to the "
            "adapter seat. 3 mm is typical for low viscosity silicone."
        )
        lbl_cd = QtWidgets.QLabel("Channel diameter")
        g.addWidget(lbl_cd, 4, 0)
        g.addWidget(self.sb_inj_dia, 4, 1)
        self.injection_widgets.extend([lbl_cd, self.sb_inj_dia])

        self.sb_inj_len = self._spin(value=DEFAULTS["InjectionChannelLength"], step=1.0)
        self.sb_inj_len.setToolTip(
            "Length of the channel between the cavity opening and the adapter "
            "recess. Longer channels let you trim the sprue more cleanly."
        )
        lbl_cl = QtWidgets.QLabel("Channel length")
        g.addWidget(lbl_cl, 5, 0)
        g.addWidget(self.sb_inj_len, 5, 1)
        self.injection_widgets.extend([lbl_cl, self.sb_inj_len])

        # Custom placement button
        self.pb_inj_place = QtWidgets.QPushButton("Pick Location")
        self.pb_inj_place.setCheckable(True)
        self.pb_inj_place.setMinimumHeight(28)
        self.pb_inj_place.setStyleSheet(PICK_BUTTON_STYLE)
        self.pb_inj_place.setToolTip(
            "Click a point on the parting line to place the injection port "
            "there instead of the default position."
        )
        self.pb_inj_place.clicked.connect(self._on_pick_injection)
        self.le_inj_pos = QtWidgets.QLineEdit()
        self.le_inj_pos.setReadOnly(True)
        self.le_inj_pos.setMinimumHeight(28)
        self.le_inj_pos.setPlaceholderText("Auto (centre)")
        g.addWidget(self.pb_inj_place, 6, 0)
        g.addWidget(self.le_inj_pos, 6, 1)
        self.injection_widgets.extend([self.pb_inj_place, self.le_inj_pos])

        # -- Pour sub-widgets --
        self.pour_widgets = []

        self.sb_pour_dia = self._spin(value=8.0, step=0.5)
        self.sb_pour_dia.setToolTip("Bore diameter of the pour hole.")
        lbl_pd = QtWidgets.QLabel("Pour diameter")
        g.addWidget(lbl_pd, 7, 0)
        g.addWidget(self.sb_pour_dia, 7, 1)
        self.pour_widgets.extend([lbl_pd, self.sb_pour_dia])

        self.sb_funnel_dia = self._spin(value=18.0, step=1.0)
        self.sb_funnel_dia.setToolTip("Mouth diameter of the funnel cone at the top of the block.")
        lbl_fd = QtWidgets.QLabel("Funnel diameter")
        g.addWidget(lbl_fd, 8, 0)
        g.addWidget(self.sb_funnel_dia, 8, 1)
        self.pour_widgets.extend([lbl_fd, self.sb_funnel_dia])

        self.sb_funnel_depth = self._spin(value=6.0, step=0.5)
        self.sb_funnel_depth.setToolTip("Depth of the funnel cone from the block top surface.")
        lbl_fdp = QtWidgets.QLabel("Funnel depth")
        g.addWidget(lbl_fdp, 9, 0)
        g.addWidget(self.sb_funnel_depth, 9, 1)
        self.pour_widgets.extend([lbl_fdp, self.sb_funnel_depth])

        # Hide pour widgets by default (syringe is selected)
        for w in self.pour_widgets:
            w.hide()

        root.addWidget(box)
        self._step_widgets.append(box)

        # Keep a reference to the group box for _apply_to_job compatibility
        self.grp_injection = box

    def _on_fill_method_changed(self, text):
        is_syringe = text == "Syringe injection"
        is_pour = text == "Pour port"
        for w in self.injection_widgets:
            w.setVisible(is_syringe)
        for w in self.pour_widgets:
            w.setVisible(is_pour)

    # --- Overflow gutter ---

    def _build_overflow_group(self, root):
        box = QtWidgets.QGroupBox("Overflow Gutter")
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(
            "A channel around the cavity at the parting line to catch "
            "excess material and help air escape. Uncheck to skip."
        )
        self.grp_gutter = box
        g = self._grid(box)

        self.sb_gutter_w = self._spin(value=DEFAULTS["GutterWidth"], step=0.5)
        self.sb_gutter_w.setToolTip("Width of the overflow channel.")
        self.sb_gutter_d = self._spin(value=DEFAULTS["GutterDepth"], step=0.5)
        self.sb_gutter_d.setToolTip("Depth of the overflow channel below the parting surface.")
        self.sb_gutter_gap = self._spin(value=DEFAULTS["GutterGap"], step=0.1, decimals=2)
        self.sb_gutter_gap.setToolTip(
            "Width of the land between the cavity edge and the gutter. "
            "A thin land lets air through but holds the silicone back. "
            "0.4 to 0.8 mm is typical."
        )
        self.sb_gutter_reliefs = self._int_spin(value=4, maximum=32)
        self.sb_gutter_reliefs.setToolTip(
            "Number of small channels cut through the land to let trapped "
            "air escape into the gutter. More reliefs help prevent bubbles."
        )

        g.addWidget(QtWidgets.QLabel("Width"), 0, 0)
        g.addWidget(self.sb_gutter_w, 0, 1)
        g.addWidget(QtWidgets.QLabel("Depth"), 1, 0)
        g.addWidget(self.sb_gutter_d, 1, 1)
        g.addWidget(QtWidgets.QLabel("Land gap"), 2, 0)
        g.addWidget(self.sb_gutter_gap, 2, 1)

        lbl = QtWidgets.QLabel("Cut into")
        lbl.setToolTip("Which mold half the gutter is machined into.")
        g.addWidget(lbl, 3, 0)
        self.cb_gutter_side = QtWidgets.QComboBox()
        self.cb_gutter_side.addItems(GUTTER_SIDES)
        self.cb_gutter_side.setToolTip(
            "Which mold half the gutter is cut into. 'Both' splits the "
            "channel across the parting line."
        )
        self._compact_combo(self.cb_gutter_side)
        g.addWidget(self.cb_gutter_side, 3, 1)

        g.addWidget(QtWidgets.QLabel("Relief channels"), 4, 0)
        g.addWidget(self.sb_gutter_reliefs, 4, 1)
        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Vents ---

    def _build_vents_group(self, root):
        box = QtWidgets.QGroupBox("Vents")
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(
            "Small channels to let trapped air escape during injection. "
            "More vents reduce the chance of bubbles in the casting. "
            "Uncheck to omit."
        )
        self.grp_vents = box
        g = self._grid(box)

        self.sb_vent_count = self._int_spin(value=4, maximum=64)
        self.sb_vent_count.setToolTip(
            "Number of vent holes distributed around the cavity. "
            "Four is a good starting point; add more for complex shapes."
        )

        g.addWidget(QtWidgets.QLabel("Count"), 0, 0)
        g.addWidget(self.sb_vent_count, 0, 1)

        # Vent cross section shape
        lbl_shape = QtWidgets.QLabel("Shape")
        lbl_shape.setToolTip("Cross section of each vent channel.")
        g.addWidget(lbl_shape, 1, 0)
        self.cb_vent_shape = QtWidgets.QComboBox()
        self.cb_vent_shape.addItems(VENT_SHAPES)
        self.cb_vent_shape.setToolTip(
            "Cylinder: round bore (default, easy to print).\n"
            "Rectangular: flat slot, useful for thin parts where "
            "a round hole would be too deep."
        )
        self._compact_combo(self.cb_vent_shape)
        self.cb_vent_shape.currentTextChanged.connect(self._on_vent_shape_changed)
        g.addWidget(self.cb_vent_shape, 1, 1)

        # Vent direction
        lbl_dir = QtWidgets.QLabel("Direction")
        lbl_dir.setToolTip("Which way the vent channels exit the mold.")
        g.addWidget(lbl_dir, 2, 0)
        self.cb_vent_direction = QtWidgets.QComboBox()
        self.cb_vent_direction.addItems(VENT_DIRECTIONS)
        self.cb_vent_direction.setCurrentText(VENT_DIR_UP)
        self._compact_combo(self.cb_vent_direction)
        self.cb_vent_direction.setToolTip(
            "Up: through the roof (default).\n"
            "Down: through the floor.\n"
            "Nearest wall: horizontally to the closest block wall."
        )
        g.addWidget(self.cb_vent_direction, 2, 1)

        # Diameter (shown for Cylinder)
        self.lbl_vent_dia = QtWidgets.QLabel("Diameter")
        self.sb_vent_dia = self._spin(value=DEFAULTS["VentDiameter"], step=0.25)
        self.sb_vent_dia.setToolTip(
            "Bore diameter of each vent. Keep this small enough that "
            "silicone does not leak through (1.0 to 2.0 mm typical)."
        )
        g.addWidget(self.lbl_vent_dia, 3, 0)
        g.addWidget(self.sb_vent_dia, 3, 1)

        # Width and Length (shown for Rectangular)
        self.lbl_vent_width = QtWidgets.QLabel("Width")
        self.sb_vent_width = self._spin(value=DEFAULTS["VentWidth"], step=0.5)
        self.sb_vent_width.setToolTip("Width of the rectangular vent slot.")
        g.addWidget(self.lbl_vent_width, 4, 0)
        g.addWidget(self.sb_vent_width, 4, 1)

        self.lbl_vent_length = QtWidgets.QLabel("Length")
        self.sb_vent_length = self._spin(value=DEFAULTS["VentLength"], step=0.5)
        self.sb_vent_length.setToolTip("Length of the rectangular vent slot.")
        g.addWidget(self.lbl_vent_length, 5, 0)
        g.addWidget(self.sb_vent_length, 5, 1)

        # Hide rectangular fields by default
        self.lbl_vent_width.hide()
        self.sb_vent_width.hide()
        self.lbl_vent_length.hide()
        self.sb_vent_length.hide()

        # Custom vent placement
        self.pb_vent_place = QtWidgets.QPushButton("Pick Locations")
        self.pb_vent_place.setCheckable(True)
        self.pb_vent_place.setMinimumHeight(28)
        self.pb_vent_place.setStyleSheet(PICK_BUTTON_STYLE)
        self.pb_vent_place.setToolTip(
            "Click points on the part surface to place vents at specific "
            "locations (e.g. high spots where air collects). Leave unset "
            "for automatic even distribution."
        )
        self.pb_vent_place.clicked.connect(self._on_pick_vents)
        self.le_vent_pos = QtWidgets.QLineEdit()
        self.le_vent_pos.setReadOnly(True)
        self.le_vent_pos.setMinimumHeight(28)
        self.le_vent_pos.setPlaceholderText("Auto (distributed)")

        # Clear button for custom vent positions
        self.pb_vent_clear = QtWidgets.QPushButton("Clear")
        self.pb_vent_clear.setMinimumHeight(28)
        self.pb_vent_clear.setToolTip("Reset to automatic vent placement.")
        self.pb_vent_clear.clicked.connect(self._on_clear_vent_positions)
        self.pb_vent_clear.hide()

        pick_row = QtWidgets.QHBoxLayout()
        pick_row.setContentsMargins(0, 0, 0, 0)
        pick_row.setSpacing(4)
        pick_row.addWidget(self.le_vent_pos, 1)
        pick_row.addWidget(self.pb_vent_clear, 0)
        pick_widget = QtWidgets.QWidget()
        pick_widget.setLayout(pick_row)

        g.addWidget(self.pb_vent_place, 6, 0)
        g.addWidget(pick_widget, 6, 1)

        root.addWidget(box)
        self._step_widgets.append(box)

    def _on_vent_shape_changed(self, text):
        is_rect = text == "Rectangular"
        self.lbl_vent_dia.setVisible(not is_rect)
        self.sb_vent_dia.setVisible(not is_rect)
        self.lbl_vent_width.setVisible(is_rect)
        self.sb_vent_width.setVisible(is_rect)
        self.lbl_vent_length.setVisible(is_rect)
        self.sb_vent_length.setVisible(is_rect)

    def _on_clear_vent_positions(self):
        self._custom_vent_positions = []
        self.le_vent_pos.clear()
        self.pb_vent_clear.hide()

    # --- Registration keys ---

    def _build_keys_group(self, root):
        box = QtWidgets.QGroupBox("Registration Keys")
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(
            "Conical pegs and pockets at the parting line that keep the "
            "two mold halves aligned. Uncheck to omit."
        )
        self.grp_keys = box
        g = self._grid(box)

        self.sb_key_dia = self._spin(value=DEFAULTS["KeyDiameter"], step=0.5)
        self.sb_key_dia.setToolTip("Base diameter of the alignment cone.")
        self.sb_key_height = self._spin(value=DEFAULTS["KeyHeight"], step=0.5)
        self.sb_key_height.setToolTip("How far the cone protrudes above the parting surface.")
        self.sb_key_clearance = self._spin(
            value=DEFAULTS["KeyClearance"], step=0.05, decimals=2
        )
        self.sb_key_clearance.setToolTip(
            "Extra diameter added to the pocket so printed parts fit. "
            "0.2 to 0.3 mm is typical for FDM; less for resin."
        )
        self.sb_key_inset = self._spin(value=DEFAULTS["KeyInset"])
        self.sb_key_inset.setToolTip(
            "Distance from the block edge to the key centre. "
            "Keep keys clear of the cavity opening."
        )

        g.addWidget(QtWidgets.QLabel("Diameter"), 0, 0)
        g.addWidget(self.sb_key_dia, 0, 1)
        g.addWidget(QtWidgets.QLabel("Height"), 1, 0)
        g.addWidget(self.sb_key_height, 1, 1)
        g.addWidget(QtWidgets.QLabel("Clearance"), 2, 0)
        g.addWidget(self.sb_key_clearance, 2, 1)
        g.addWidget(QtWidgets.QLabel("Inset"), 3, 0)
        g.addWidget(self.sb_key_inset, 3, 1)
        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Hardware (bolts) ---

    def _build_hardware_group(self, root):
        box = QtWidgets.QGroupBox("Hardware")
        box.setCheckable(True)
        box.setChecked(False)
        box.setToolTip(
            "Through-bolt holes for clamping the mold halves together "
            "with socket head cap screws. Uncheck for no bolts."
        )
        self.grp_hardware = box
        g = self._grid(box)

        # Bolt count
        self.sb_bolt_count = self._int_spin(value=4, minimum=1, maximum=12)
        self.sb_bolt_count.setToolTip(
            "Number of bolts spaced evenly around the block perimeter. "
            "Any that land too close to the cavity are skipped and a "
            "warning tells you how many were actually placed."
        )
        g.addWidget(QtWidgets.QLabel("Count"), 0, 0)
        g.addWidget(self.sb_bolt_count, 0, 1)

        # Bolt size
        self.cb_bolt_size = QtWidgets.QComboBox()
        self.cb_bolt_size.addItems(BOLT_SIZES)
        self.cb_bolt_size.setCurrentText("M4")
        self._compact_combo(self.cb_bolt_size)
        self.cb_bolt_size.setToolTip(
            "Metric bolt thread size. M4 is a good default for small "
            "to medium molds; go up for larger blocks."
        )
        g.addWidget(QtWidgets.QLabel("Bolt Size"), 1, 0)
        g.addWidget(self.cb_bolt_size, 1, 1)

        # Bolt inset
        self.sb_bolt_inset = self._spin(value=DEFAULTS["BoltInset"])
        self.sb_bolt_inset.setToolTip(
            "Distance from the block edge to the bolt hole centre. "
            "Keep bolts clear of the cavity."
        )
        g.addWidget(QtWidgets.QLabel("Inset"), 2, 0)
        g.addWidget(self.sb_bolt_inset, 2, 1)

        # Bolt clearance
        self.sb_bolt_clearance = self._spin(
            value=0.3, minimum=0.0, maximum=2.0, step=0.05, decimals=2
        )
        self.sb_bolt_clearance.setToolTip(
            "Extra diameter added to the through-hole for print tolerance. "
            "0.3 mm is typical for FDM."
        )
        g.addWidget(QtWidgets.QLabel("Clearance"), 3, 0)
        g.addWidget(self.sb_bolt_clearance, 3, 1)

        # Counterbore + Nut trap checkboxes side by side
        hw_row = QtWidgets.QHBoxLayout()
        hw_row.setContentsMargins(0, 0, 0, 0)
        hw_row.setSpacing(12)
        self.chk_counterbore = QtWidgets.QCheckBox("Counterbore")
        self.chk_counterbore.setChecked(True)
        self.chk_counterbore.setToolTip(
            "Pocket at the top of the block so the bolt head sits flush."
        )
        self.chk_nut_trap = QtWidgets.QCheckBox("Nut Trap")
        self.chk_nut_trap.setChecked(True)
        self.chk_nut_trap.setToolTip(
            "Cylindrical pocket at the bottom so a socket or spanner "
            "can reach the captive nut."
        )
        hw_row.addWidget(self.chk_counterbore, 1)
        hw_row.addWidget(self.chk_nut_trap, 1)
        hw_wrap = QtWidgets.QWidget()
        hw_wrap.setLayout(hw_row)
        g.addWidget(hw_wrap, 3, 0, 1, 2)

        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Pry slots ---

    def _build_pry_group(self, root):
        box = QtWidgets.QGroupBox("Pry Slots")
        box.setCheckable(True)
        box.setChecked(True)
        box.setToolTip(
            "Notches at the parting seam where you can insert a flat "
            "screwdriver or spudger to lever the mold halves apart. "
            "Uncheck to omit."
        )
        self.grp_pry = box
        g = self._grid(box)

        self.sb_pry_w = self._spin(value=DEFAULTS["PrySlotWidth"])
        self.sb_pry_w.setToolTip("Width of the pry slot along the seam.")
        self.sb_pry_d = self._spin(value=DEFAULTS["PrySlotDepth"])
        self.sb_pry_d.setToolTip("How deep the slot cuts into the mold body.")

        g.addWidget(QtWidgets.QLabel("Width"), 0, 0)
        g.addWidget(self.sb_pry_w, 0, 1)
        g.addWidget(QtWidgets.QLabel("Depth"), 1, 0)
        g.addWidget(self.sb_pry_d, 1, 1)
        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Embossment ---

    def _build_emboss_group(self, root):
        box = QtWidgets.QGroupBox("Embossment")
        box.setCheckable(True)
        box.setChecked(False)
        box.setToolTip(
            "Deboss identification text into the side walls of each mold "
            "piece, so you can tell upper from lower at a glance. The text "
            "is placed on the front wall, away from the print bed faces."
        )
        self.grp_emboss = box
        g = self._grid(box)

        lbl_text = QtWidgets.QLabel("Label text")
        lbl_text.setToolTip(
            "Custom text to cut into each piece. Leave empty for automatic "
            "labels (UPPER, LOWER, LEFT, RIGHT)."
        )
        g.addWidget(lbl_text, 0, 0)
        self.le_emboss_text = QtWidgets.QLineEdit()
        self.le_emboss_text.setMinimumHeight(28)
        self.le_emboss_text.setPlaceholderText("Auto (UPPER / LOWER)")
        self.le_emboss_text.setToolTip(
            "Type your own label or leave blank for automatic piece names."
        )
        g.addWidget(self.le_emboss_text, 0, 1)

        self.sb_emboss_size = self._spin(
            value=5.0, minimum=2.0, maximum=30.0, step=0.5
        )
        self.sb_emboss_size.setToolTip("Height of each letter in mm.")
        g.addWidget(QtWidgets.QLabel("Font size"), 1, 0)
        g.addWidget(self.sb_emboss_size, 1, 1)

        self.sb_emboss_depth = self._spin(
            value=0.8, minimum=0.2, maximum=5.0, step=0.1
        )
        self.sb_emboss_depth.setToolTip(
            "How deep the text is cut into the wall. 0.6 to 1.0 mm "
            "gives a crisp imprint without weakening the wall."
        )
        g.addWidget(QtWidgets.QLabel("Cut depth"), 2, 0)
        g.addWidget(self.sb_emboss_depth, 2, 1)

        lbl_place = QtWidgets.QLabel("Placement")
        lbl_place.setToolTip("Which surface the text is cut into.")
        g.addWidget(lbl_place, 3, 0)
        self.cb_emboss_placement = QtWidgets.QComboBox()
        self.cb_emboss_placement.addItems(EMBOSS_PLACEMENTS)
        self.cb_emboss_placement.setCurrentText(EMBOSS_SIDE_WALL)
        self._compact_combo(self.cb_emboss_placement)
        self.cb_emboss_placement.setToolTip(
            "Side wall: outer side face, readable from outside.\n"
            "Outer face: floor bottom / roof top.\n"
            "Inner face: parting surfaces visible when the mold is open."
        )
        g.addWidget(self.cb_emboss_placement, 3, 1)

        root.addWidget(box)
        self._step_widgets.append(box)

    # --- Viewport controls (exploded view, overlay toggles) ---

    def _build_viewport_controls(self, layout):
        """Add explode slider and overlay checkboxes to *layout*.

        Called from ``_build_form`` to populate the Viewport task panel.
        """
        # Exploded view slider row
        explode_row = QtWidgets.QHBoxLayout()
        explode_row.setContentsMargins(0, 0, 0, 0)
        explode_row.setSpacing(4)
        lbl = QtWidgets.QLabel("Explode")
        lbl.setToolTip("Slide the mold halves apart to see the cavity inside.")
        self.slider_explode = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_explode.setRange(0, 100)
        self.slider_explode.setValue(0)
        self.slider_explode.setToolTip(
            "Drag to separate the mold halves for inspection. "
            "0 = closed, 100 = fully apart."
        )
        self.slider_explode.valueChanged.connect(self._on_explode_changed)
        self.lbl_explode_val = QtWidgets.QLabel("0")
        self.lbl_explode_val.setFixedWidth(28)
        self.lbl_explode_val.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        explode_row.addWidget(lbl)
        explode_row.addWidget(self.slider_explode, 1)
        explode_row.addWidget(self.lbl_explode_val)
        layout.addLayout(explode_row)

        # Feature labels + pull arrow checkboxes, side by side
        overlay_row = QtWidgets.QHBoxLayout()
        overlay_row.setContentsMargins(0, 0, 0, 0)
        overlay_row.setSpacing(12)
        self.chk_labels = QtWidgets.QCheckBox("Feature labels")
        self.chk_labels.setChecked(False)
        self.chk_labels.setToolTip(
            "Overlay badges in the 3D viewport showing where each mold "
            "feature is (injection port, vents, keys, pry slots)."
        )
        self.chk_labels.toggled.connect(self._on_toggle_labels)
        self.chk_direction = QtWidgets.QCheckBox("Pull arrow")
        self.chk_direction.setChecked(True)
        self.chk_direction.setToolTip(
            "Show a 3D arrow in the viewport indicating the mold pull "
            "direction."
        )
        self.chk_direction.toggled.connect(self._on_toggle_direction)
        overlay_row.addWidget(self.chk_labels, 1)
        overlay_row.addWidget(self.chk_direction, 1)
        layout.addLayout(overlay_row)

    # ------------------------------------------------------------------
    # Wizard (step-by-step) mode
    # ------------------------------------------------------------------

    def _toggle_wizard_mode(self, on):
        self._wizard_mode = on
        if on:
            self._wizard_step = 0
            self.wizard_nav.show()
            self._apply_wizard_visibility()
        else:
            self.wizard_nav.hide()
            for w in self._step_widgets:
                w.show()

    def _apply_wizard_visibility(self):
        for i, w in enumerate(self._step_widgets):
            w.setVisible(i == self._wizard_step)
        total = len(self._step_widgets)
        current = self._wizard_step + 1
        step_name = _WIZARD_STEPS[self._wizard_step][1] if self._wizard_step < len(_WIZARD_STEPS) else ""
        self.wiz_step_label.setText(
            "Step %d of %d: %s" % (current, total, step_name)
        )
        self.pb_wiz_back.setEnabled(self._wizard_step > 0)
        is_last = self._wizard_step >= total - 1
        self.pb_wiz_next.setText("Generate" if is_last else "Next")

    def _wizard_next(self):
        total = len(self._step_widgets)
        if self._wizard_step >= total - 1:
            self._on_generate()
            return
        self._wizard_step += 1
        self._apply_wizard_visibility()

    def _wizard_back(self):
        if self._wizard_step > 0:
            self._wizard_step -= 1
            self._apply_wizard_visibility()

    # ------------------------------------------------------------------
    # Selection observer
    # ------------------------------------------------------------------

    def addSelection(self, *args):
        if self.picking_mode:
            QtCore.QTimer.singleShot(30, self._process_pick)

    def removeSelection(self, *args):
        pass

    def clearSelection(self, *args):
        pass

    def setSelection(self, *args):
        pass

    def _process_pick(self):
        if self.picking_mode == "object":
            obj = pick.selected_solid()
            if obj is not None:
                self._apply_object(obj)
                self._end_pick()
        elif self.picking_mode == "pull":
            direction = pick.direction_from_selection()
            if direction is not None:
                self._apply_pull(direction)
                self._end_pick()
        elif self.picking_mode == "injection":
            self._apply_custom_position("injection")
            self._end_pick()
        elif self.picking_mode == "parting":
            self._apply_parting_from_pick()
            self._end_pick()
        elif self.picking_mode == "vents":
            self._apply_custom_position("vents")
            # Don't end pick for vents; user can keep clicking multiple spots
            # They press Escape or uncheck to finish.

    def _apply_object(self, obj):
        self.target_object = obj
        self.target_shape = obj.Shape
        self.le_object.setText(obj.Label)
        self.pb_object.setChecked(False)
        self._run_slow_update()
        self._update_generate_state()

    def _apply_pull(self, direction):
        self.pull_dir = direction
        self.pull_flipped = False
        self.pull_ref = "%.3f, %.3f, %.3f" % (direction.x, direction.y, direction.z)
        self.le_pull.setText(self.pull_ref)
        self.pb_pull.setChecked(False)
        self._run_slow_update()

    def _apply_custom_position(self, mode):
        """Read the clicked point from the selection for injection or vent placement."""
        try:
            sel_ex = Gui.Selection.getSelectionEx()
            if not sel_ex:
                return
            for s in sel_ex:
                if s.PickedPoints:
                    pt = App.Vector(s.PickedPoints[0])
                    if mode == "injection":
                        self._custom_injection_pos = pt
                        self.le_inj_pos.setText(
                            "%.1f, %.1f, %.1f" % (pt.x, pt.y, pt.z)
                        )
                    elif mode == "vents":
                        self._custom_vent_positions.append(pt)
                        count = len(self._custom_vent_positions)
                        self.le_vent_pos.setText(
                            "%d point%s picked" % (count, "s" if count != 1 else "")
                        )
                        self.pb_vent_clear.show()
                    return
        except Exception:
            pass

    def _apply_parting_from_pick(self):
        """Set the parting offset from the picked vertex, edge, or face.

        Uses the existing ``pick.height_from_selection`` which transforms the
        picked geometry into the local frame and returns the height offset
        relative to the bottom of the part.
        """
        if self.target_object is None:
            self._show_status("warning", "Select a solid body first.")
            return
        offset = pick.height_from_selection(
            self.pull_dir, self.target_object.Shape
        )
        if offset is not None:
            self.sb_parting.setValue(offset)
            self.pb_parting_pick.setChecked(False)
        else:
            self._show_status("warning", "Could not read height from selection.")

    def _run_slow_update(self):
        """Run parting suggestion + direction indicator with a wait cursor.

        These operations involve ``transformShape`` and analysis passes that
        can take several seconds on complex geometry, so we show the wait
        cursor and a status message while they run.
        """
        self._show_status("info", "Analysing geometry…")
        QtWidgets.QApplication.setOverrideCursor(
            QtCore.Qt.CursorShape.WaitCursor
        )
        QtWidgets.QApplication.processEvents()
        try:
            self._suggest_parting()
            self._update_direction_indicator()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.diag_browser.clear()

    def _suggest_parting(self):
        """Drop the parting plane on the widest section of the current part."""
        if self.target_object is None:
            return
        try:
            frame = local_frame(self.pull_dir)
            local = to_local(self.target_object.Shape, frame)
            height = analysis.suggest_parting_height(local)
            offset = height - local.BoundBox.ZMin
            self.sb_parting.setValue(offset)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Pick mode management
    # ------------------------------------------------------------------

    def _on_pick_object(self):
        if self.pb_object.isChecked():
            self._start_pick("object")
        else:
            self._end_pick()

    def _on_pick_pull(self):
        if self.pb_pull.isChecked():
            self._start_pick("pull")
        else:
            self._end_pick()

    def _on_pick_injection(self):
        if self.pb_inj_place.isChecked():
            self._start_pick("injection")
        else:
            self._end_pick()

    def _on_pick_vents(self):
        if self.pb_vent_place.isChecked():
            self._start_pick("vents")
        else:
            self._end_pick()

    def _on_pick_parting(self):
        if self.pb_parting_pick.isChecked():
            self._start_pick("parting")
        else:
            self._end_pick()

    def _start_pick(self, mode):
        self.picking_mode = mode
        # uncheck other pick buttons
        buttons = {
            self.pb_object: "object",
            self.pb_pull: "pull",
            self.pb_inj_place: "injection",
            self.pb_vent_place: "vents",
            self.pb_parting_pick: "parting",
        }
        for btn, btn_mode in buttons.items():
            if btn_mode != mode:
                btn.setChecked(False)
        if not self.cursor_overridden:
            QtWidgets.QApplication.setOverrideCursor(
                QtCore.Qt.CursorShape.CrossCursor
            )
            self.cursor_overridden = True

    def _end_pick(self):
        self.picking_mode = None
        self.pb_object.setChecked(False)
        self.pb_pull.setChecked(False)
        self.pb_inj_place.setChecked(False)
        self.pb_vent_place.setChecked(False)
        self.pb_parting_pick.setChecked(False)
        if self.cursor_overridden:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.cursor_overridden = False

    def _on_flip_pull(self):
        self.pull_dir = self.pull_dir * -1.0
        self.pull_flipped = not self.pull_flipped
        self.pull_ref = "%.3f, %.3f, %.3f" % (
            self.pull_dir.x, self.pull_dir.y, self.pull_dir.z,
        )
        self.le_pull.setText(self.pull_ref)
        self._run_slow_update()

    def _on_escape(self):
        if self.picking_mode:
            self._end_pick()
            return True
        return False

    def _auto_select(self):
        sel = Gui.Selection.getSelection()
        if sel:
            obj = pick.selected_solid()
            if obj is None and sel:
                obj = sel[0] if hasattr(sel[0], "Shape") else None
            if obj is not None:
                self._apply_object(obj)
            Gui.Selection.clearSelection()

    # ------------------------------------------------------------------
    # Viewport visuals
    # ------------------------------------------------------------------

    def _update_direction_indicator(self):
        """Show or update the pull direction arrow in the 3D view."""
        if not self.chk_direction.isChecked():
            return
        if self.target_object is None:
            return

        try:
            from .visuals import DirectionIndicator
        except ImportError:
            return

        bb = self.target_object.Shape.BoundBox
        centre = App.Vector(
            (bb.XMin + bb.XMax) / 2.0,
            (bb.YMin + bb.YMax) / 2.0,
            (bb.ZMin + bb.ZMax) / 2.0,
        )

        if self._direction_indicator is None:
            self._direction_indicator = DirectionIndicator(
                color=(0.20, 0.60, 0.95), label="Pull"
            )
        self._direction_indicator.show(centre, self.pull_dir)

    def _remove_direction_indicator(self):
        if self._direction_indicator is not None:
            self._direction_indicator.remove()
            self._direction_indicator = None

    def _on_toggle_direction(self, checked):
        if checked:
            self._update_direction_indicator()
        else:
            self._remove_direction_indicator()

    def _update_feature_labels(self):
        """Show or update in-viewport feature labels."""
        if not self.chk_labels.isChecked():
            return
        if self.job is None:
            return

        try:
            from .visuals import MoldFeatureLabels
        except ImportError:
            return

        features = self._collect_feature_points()
        if not features:
            return

        if self._feature_labels is None:
            self._feature_labels = MoldFeatureLabels()
        self._feature_labels.update(features)

    def _collect_feature_points(self):
        """Gather 3D positions for each mold feature from the build result.

        Uses the actual geometry positions computed during the build rather
        than rough bounding box estimates, so labels land right on the
        features they describe.
        """
        if self._last_result is not None and self._last_result.feature_points:
            return list(self._last_result.feature_points)

        # Fallback: no build result yet, return empty (labels appear after
        # first generate)
        return []

    def _remove_feature_labels(self):
        if self._feature_labels is not None:
            self._feature_labels.remove()
            self._feature_labels = None

    def _on_toggle_labels(self, checked):
        if checked:
            self._update_feature_labels()
        else:
            self._remove_feature_labels()

    # ------------------------------------------------------------------
    # Exploded view
    # ------------------------------------------------------------------

    def _on_explode_changed(self, value):
        self.lbl_explode_val.setText(str(value))
        if self.job is not None and hasattr(self.job, "Exploded"):
            self.job.Exploded = float(value)
            try:
                self.job.Document.recompute()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Generate / Clear
    # ------------------------------------------------------------------

    def _update_generate_state(self):
        has_object = self.target_object is not None
        self.pb_generate.setEnabled(has_object)

    def _on_generate(self):
        if self.target_object is None:
            self._show_status("warning", "Select a solid body first.")
            return

        self.progress.setRange(0, 0)
        self.progress.show()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            job = self._ensure_job()
            self._apply_to_job(job)
            result = mold_job.rebuild(job)
            self._last_result = result
            self._show_result(result)
            self.pb_clear.setEnabled(True)
            self._built = True

            if App.GuiUp and hasattr(self.target_object, "ViewObject"):
                self.target_object.ViewObject.Transparency = 70

            try:
                Gui.ActiveDocument.ActiveView.fitAll()
            except Exception:
                pass

            # Update viewport overlays
            self._update_direction_indicator()
            self._update_feature_labels()

        except Exception as exc:
            self._show_status("error", "Build failed: %s" % exc)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.progress.hide()

    def _on_clear(self):
        if self.job is not None:
            doc = self.job.Document
            try:
                for child in list(self.job.Group):
                    doc.removeObject(child.Name)
                doc.removeObject(self.job.Name)
            except Exception:
                pass
            self.job = None
        self.pb_clear.setEnabled(False)
        self._built = False
        self._show_status("info", "Mold cleared.")

        if self.target_object is not None:
            try:
                self.target_object.ViewObject.Transparency = 0
            except Exception:
                pass

        self._remove_feature_labels()
        self.slider_explode.setValue(0)

    def _ensure_job(self):
        """Get the existing job or create a new one."""
        if self.job is not None:
            return self.job
        doc = App.ActiveDocument
        if doc is None:
            doc = App.newDocument("Mold")
        doc.openTransaction("Create mold")
        job = mold_job.create(doc, self.target_object)
        if self.target_object is not None:
            job.Label = "Mold of %s" % self.target_object.Label
        doc.commitTransaction()
        self.job = job
        return job

    def _apply_to_job(self, job):
        """Push every widget value onto the job's properties."""
        if self.target_object is not None:
            job.Source = self.target_object
        job.PullDirection = self.pull_dir
        job.Shrink = self.sb_shrink.value()

        # Parting
        job.Layout = self.cb_layout.currentText()
        job.PartingOffset = self.sb_parting.value()
        job.SecondaryAngle = self.sb_secondary_angle.value()

        # Block
        job.BlockStyle = self.cb_block_style.currentText()
        job.WallThickness = self.sb_wall.value()
        job.FloorThickness = self.sb_floor.value()
        job.RoofThickness = self.sb_roof.value()
        job.BlockFillet = self.sb_fillet.value()

        # Filling method
        method = self.cb_fill_method.currentText()
        job.InjectionPort = method == "Syringe injection"
        job.PourPort = method == "Pour port"
        job.OverpourPercent = self.sb_overpour.value()

        # Injection specifics
        job.SyringeSize = self.cb_syringe.currentText()
        job.InjectionStyle = self.cb_inj_style.currentText()
        job.InjectionDiameter = self.sb_inj_dia.value()
        job.InjectionChannelLength = self.sb_inj_len.value()
        if self._custom_injection_pos is not None:
            job.InjectionPosition = self._custom_injection_pos
            job.UseCustomInjectionPos = True
        else:
            job.UseCustomInjectionPos = False

        # Pour port specifics
        job.PourDiameter = self.sb_pour_dia.value()
        job.FunnelDiameter = self.sb_funnel_dia.value()
        job.FunnelDepth = self.sb_funnel_depth.value()

        # Overflow gutter
        job.Gutter = self.grp_gutter.isChecked()
        job.GutterWidth = self.sb_gutter_w.value()
        job.GutterDepth = self.sb_gutter_d.value()
        job.GutterGap = self.sb_gutter_gap.value()
        job.GutterSide = self.cb_gutter_side.currentText()
        job.GutterReliefs = self.sb_gutter_reliefs.value()

        # Vents
        vent_on = self.grp_vents.isChecked()
        job.VentCount = self.sb_vent_count.value() if vent_on else 0
        job.VentDiameter = self.sb_vent_dia.value()
        job.VentShape = self.cb_vent_shape.currentText()
        job.VentDirection = self.cb_vent_direction.currentText()
        job.VentWidth = self.sb_vent_width.value()
        job.VentLength = self.sb_vent_length.value()
        if self._custom_vent_positions:
            job.VentPositions = self._custom_vent_positions
            job.UseCustomVentPositions = True
        else:
            job.UseCustomVentPositions = False

        # Registration keys
        job.RegistrationKeys = self.grp_keys.isChecked()
        job.KeyDiameter = self.sb_key_dia.value()
        job.KeyHeight = self.sb_key_height.value()
        job.KeyClearance = self.sb_key_clearance.value()
        job.KeyInset = self.sb_key_inset.value()

        # Hardware (bolts)
        job.Bolts = self.grp_hardware.isChecked()
        job.BoltCount = self.sb_bolt_count.value()
        job.BoltSize = self.cb_bolt_size.currentText()
        job.BoltInset = self.sb_bolt_inset.value()
        job.BoltClearance = self.sb_bolt_clearance.value()
        job.Counterbore = self.chk_counterbore.isChecked()
        job.NutTrap = self.chk_nut_trap.isChecked()

        # Pry slots
        job.PrySlots = self.grp_pry.isChecked()
        job.PrySlotWidth = self.sb_pry_w.value()
        job.PrySlotDepth = self.sb_pry_d.value()

        # Embossment
        job.Emboss = self.grp_emboss.isChecked()
        job.EmbossText = self.le_emboss_text.text()
        job.EmbossFontSize = self.sb_emboss_size.value()
        job.EmbossDepth = self.sb_emboss_depth.value()
        job.EmbossPlacement = self.cb_emboss_placement.currentText()

        # Exploded view
        job.Exploded = float(self.slider_explode.value())

        # Keep auto update on
        job.AutoUpdate = True

    def _load_from_job(self, job):
        """Populate every widget from an existing job (for re-editing)."""
        source = getattr(job, "Source", None)
        if source is not None:
            self.target_object = source
            self.target_shape = source.Shape if hasattr(source, "Shape") else None
            self.le_object.setText(source.Label)

        pull = App.Vector(job.PullDirection)
        self.pull_dir = pull
        self.pull_ref = "%.3f, %.3f, %.3f" % (pull.x, pull.y, pull.z)
        self.le_pull.setText(self.pull_ref)

        self.sb_shrink.setValue(float(job.Shrink))

        # Parting
        if hasattr(job, "Layout"):
            self.cb_layout.setCurrentText(job.Layout)
            self._on_layout_changed(job.Layout)
        self.sb_parting.setValue(float(job.PartingOffset))
        if hasattr(job, "SecondaryAngle"):
            self.sb_secondary_angle.setValue(float(job.SecondaryAngle))

        self.cb_block_style.setCurrentText(job.BlockStyle)
        self.sb_wall.setValue(float(job.WallThickness))
        self.sb_floor.setValue(float(job.FloorThickness))
        self.sb_roof.setValue(float(job.RoofThickness))
        self.sb_fillet.setValue(float(job.BlockFillet))

        # Filling method
        inj = bool(getattr(job, "InjectionPort", False))
        pour = bool(getattr(job, "PourPort", False))
        if inj:
            fill_method = "Syringe injection"
        elif pour:
            fill_method = "Pour port"
        else:
            fill_method = "None"
        self.cb_fill_method.setCurrentText(fill_method)
        self._on_fill_method_changed(fill_method)

        self.sb_overpour.setValue(
            float(getattr(job, "OverpourPercent", DEFAULTS["OverpourPercent"]))
        )

        # Syringe specifics
        if hasattr(job, "SyringeSize"):
            self.cb_syringe.setCurrentText(job.SyringeSize)
        if hasattr(job, "InjectionStyle"):
            self.cb_inj_style.setCurrentText(job.InjectionStyle)
        self.sb_inj_dia.setValue(
            float(getattr(job, "InjectionDiameter", DEFAULTS["InjectionDiameter"]))
        )
        self.sb_inj_len.setValue(
            float(getattr(job, "InjectionChannelLength", DEFAULTS["InjectionChannelLength"]))
        )
        if getattr(job, "UseCustomInjectionPos", False):
            try:
                pos = App.Vector(job.InjectionPosition)
                if pos.Length > 1e-9:
                    self._custom_injection_pos = pos
                    self.le_inj_pos.setText(
                        "%.1f, %.1f, %.1f" % (pos.x, pos.y, pos.z)
                    )
            except Exception:
                pass

        # Pour port specifics
        self.sb_pour_dia.setValue(float(getattr(job, "PourDiameter", 8.0)))
        self.sb_funnel_dia.setValue(float(getattr(job, "FunnelDiameter", 18.0)))
        self.sb_funnel_depth.setValue(float(getattr(job, "FunnelDepth", 6.0)))

        self.grp_gutter.setChecked(bool(job.Gutter))
        self.sb_gutter_w.setValue(float(job.GutterWidth))
        self.sb_gutter_d.setValue(float(job.GutterDepth))
        self.sb_gutter_gap.setValue(float(job.GutterGap))
        self.cb_gutter_side.setCurrentText(job.GutterSide)
        self.sb_gutter_reliefs.setValue(int(job.GutterReliefs))

        vent_on = int(job.VentCount) > 0
        self.grp_vents.setChecked(vent_on)
        self.sb_vent_count.setValue(int(job.VentCount) if vent_on else 4)
        self.sb_vent_dia.setValue(float(job.VentDiameter))
        if hasattr(job, "VentShape"):
            self.cb_vent_shape.setCurrentText(job.VentShape)
            self._on_vent_shape_changed(job.VentShape)
        if hasattr(job, "VentDirection"):
            self.cb_vent_direction.setCurrentText(job.VentDirection)
        if hasattr(job, "VentWidth"):
            self.sb_vent_width.setValue(float(job.VentWidth))
        if hasattr(job, "VentLength"):
            self.sb_vent_length.setValue(float(job.VentLength))
        if getattr(job, "UseCustomVentPositions", False):
            try:
                self._custom_vent_positions = [App.Vector(v) for v in job.VentPositions]
                count = len(self._custom_vent_positions)
                if count > 0:
                    self.le_vent_pos.setText(
                        "%d point%s picked" % (count, "s" if count != 1 else "")
                    )
                    self.pb_vent_clear.show()
            except Exception:
                pass

        self.grp_keys.setChecked(bool(job.RegistrationKeys))
        self.sb_key_dia.setValue(float(job.KeyDiameter))
        self.sb_key_height.setValue(float(job.KeyHeight))
        self.sb_key_clearance.setValue(float(job.KeyClearance))
        self.sb_key_inset.setValue(float(job.KeyInset))

        self.grp_hardware.setChecked(bool(getattr(job, "Bolts", False)))
        self.sb_bolt_count.setValue(int(getattr(job, "BoltCount", 4)))
        if hasattr(job, "BoltSize"):
            self.cb_bolt_size.setCurrentText(job.BoltSize)
        self.sb_bolt_inset.setValue(
            float(getattr(job, "BoltInset", DEFAULTS["BoltInset"]))
        )
        self.sb_bolt_clearance.setValue(
            float(getattr(job, "BoltClearance", 0.3))
        )
        self.chk_counterbore.setChecked(bool(getattr(job, "Counterbore", True)))
        self.chk_nut_trap.setChecked(bool(getattr(job, "NutTrap", True)))

        self.grp_pry.setChecked(bool(job.PrySlots))
        self.sb_pry_w.setValue(float(job.PrySlotWidth))
        self.sb_pry_d.setValue(float(job.PrySlotDepth))

        # Embossment
        self.grp_emboss.setChecked(bool(getattr(job, "Emboss", False)))
        self.le_emboss_text.setText(str(getattr(job, "EmbossText", "")))
        self.sb_emboss_size.setValue(float(getattr(job, "EmbossFontSize", 5.0)))
        self.sb_emboss_depth.setValue(float(getattr(job, "EmbossDepth", 0.8)))
        if hasattr(job, "EmbossPlacement"):
            self.cb_emboss_placement.setCurrentText(job.EmbossPlacement)

        # Exploded view
        exploded = float(getattr(job, "Exploded", 0.0))
        self.slider_explode.setValue(int(exploded))

        self._built = bool(job.Group)
        self.pb_clear.setEnabled(self._built)
        self._update_generate_state()
        self._update_direction_indicator()

    # ------------------------------------------------------------------
    # Status display (redesigned)
    # ------------------------------------------------------------------

    def _show_status(self, level, text):
        """Write a status message into the diagnostics browser as HTML.

        level: "warning", "error", "success", "info"
        """
        colours = {
            "warning": "#c07000",
            "error":   "#c03030",
            "success": "#207840",
            "info":    "#306090",
        }
        icons = {
            "warning": _WARNING_ICON,
            "error":   _WARNING_ICON,
            "success": _SUCCESS_ICON,
            "info":    _INFO_ICON,
        }
        colour = colours.get(level, "#306090")
        icon = icons.get(level, _INFO_ICON)
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_body = escaped.replace("\n", "<br>")
        html = (
            '<div style="color:%s; font-size:11px; padding:4px;">'
            '<b>%s</b>&nbsp; %s</div>' % (colour, icon, html_body)
        )
        self.diag_browser.setHtml(html)

    def _show_result(self, result):
        if result is None:
            return

        summary = "%d piece(s) built. Cavity %.1f mL, mix %.1f mL." % (
            len(result.pieces), result.cavity_volume_ml, result.suggested_pour_ml,
        )

        if result.warnings:
            parts = list(result.warnings)
            if result.notes:
                parts.append("")
                for note in result.notes:
                    parts.append("%s %s" % (_INFO_ICON, note))
            self._show_status("warning", "\n".join(parts))
        elif result.notes:
            parts = [summary, ""]
            for note in result.notes:
                parts.append("%s %s" % (_INFO_ICON, note))
            self._show_status("success", "\n".join(parts))
        else:
            self._show_status("success", summary)

    # ------------------------------------------------------------------
    # FreeCAD task panel protocol
    # ------------------------------------------------------------------

    def getStandardButtons(self):
        combined = (
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        # PySide6 flag enums are not directly castable with int();
        # use .value when available (PySide6), fall back for PySide2.
        return combined.value if hasattr(combined, "value") else int(combined)

    def accept(self):
        self._end_pick()
        if self.target_object is not None and not self._built:
            self._on_generate()
        self._cleanup()
        Gui.Control.closeDialog()
        return True

    def reject(self):
        self._end_pick()
        if not self._built and self.job is not None:
            self._on_clear()
        self._cleanup()
        Gui.Control.closeDialog()
        return True

    def _cleanup(self):
        """Remove selection observer and viewport overlays."""
        try:
            Gui.Selection.removeObserver(self)
        except Exception:
            pass
        self._remove_direction_indicator()
        self._remove_feature_labels()

    def needsFullSpace(self):
        return False


# ------------------------------------------------------------------
# Convenience to open the wizard
# ------------------------------------------------------------------

def show_wizard(job=None):
    """Open the wizard, closing whatever panel was open before."""
    if Gui.Control.activeDialog():
        Gui.Control.closeDialog()
    Gui.Control.showDialog(MoldWizardPanel(job))
