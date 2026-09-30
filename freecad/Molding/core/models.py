# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Tables and named constants shared by the geometry code and the GUI.

Nothing in here touches FreeCAD, so it can be imported and unit tested from a
plain Python interpreter.
"""

# Layout options.  The wizard defaults to two piece, but the split code
# supports three piece layouts for parts with side undercuts.
LAYOUT_TWO = "Two piece"
LAYOUT_THREE_TOP = "Three piece (split upper)"
LAYOUT_THREE_BOTTOM = "Three piece (split lower)"
LAYOUTS = [LAYOUT_TWO, LAYOUT_THREE_TOP, LAYOUT_THREE_BOTTOM]

BLOCK_BOX = "Box"
BLOCK_CYLINDER = "Cylinder"
BLOCK_STYLES = [BLOCK_BOX, BLOCK_CYLINDER]

GUTTER_ABOVE = "Upper piece"
GUTTER_BELOW = "Lower piece"
GUTTER_BOTH = "Both pieces"
GUTTER_SIDES = [GUTTER_ABOVE, GUTTER_BELOW, GUTTER_BOTH]

VENT_CYLINDER = "Cylinder"
VENT_RECTANGULAR = "Rectangular"
VENT_SHAPES = [VENT_CYLINDER, VENT_RECTANGULAR]

VENT_DIR_UP = "Up"
VENT_DIR_DOWN = "Down"
VENT_DIR_NEAREST_WALL = "Nearest wall"
VENT_DIRECTIONS = [VENT_DIR_UP, VENT_DIR_DOWN, VENT_DIR_NEAREST_WALL]

INJECTION_LUER_LOCK = "Luer Lock"
INJECTION_FRICTION = "Friction Fit"
INJECTION_STYLES = [INJECTION_LUER_LOCK, INJECTION_FRICTION]

EMBOSS_SIDE_WALL = "Side wall"
EMBOSS_OUTER = "Outer face"
EMBOSS_INNER = "Inner face"
EMBOSS_PLACEMENTS = [EMBOSS_SIDE_WALL, EMBOSS_OUTER, EMBOSS_INNER]

EXPORT_STL = "STL"
EXPORT_STEP = "STEP"
EXPORT_BOTH = "STL and STEP"
EXPORT_FORMATS = [EXPORT_STL, EXPORT_STEP, EXPORT_BOTH]

# Metric socket head cap screw dimensions in mm.
BOLTS = {
    "M3": {"clearance": 3.4, "head": 6.2, "head_h": 3.2, "nut_af": 5.5, "nut_h": 2.4},
    "M4": {"clearance": 4.5, "head": 8.2, "head_h": 4.2, "nut_af": 7.0, "nut_h": 3.2},
    "M5": {"clearance": 5.6, "head": 10.2, "head_h": 5.2, "nut_af": 8.0, "nut_h": 4.7},
    "M6": {"clearance": 6.6, "head": 12.2, "head_h": 6.2, "nut_af": 10.0, "nut_h": 5.2},
}
BOLT_SIZES = ["M3", "M4", "M5", "M6"]

PLANAR_TOLERANCE = 1e-6
UNDERCUT_TOLERANCE_DEG = 0.5
DEFAULT_SHRINK_PERCENT = 0.0

# Luer lock syringe tip sizes (ISO 80369-7). The outer diameter of the
# male luer taper at the tip, which is what the injection channel must
# clear, plus the thread root diameter for the lock collar bore.
# All values in mm.
SYRINGE_SIZES = {
    "1 mL":  {"tip_od": 4.3, "collar_od": 7.5, "depth": 8.0},
    "3 mL":  {"tip_od": 4.3, "collar_od": 7.5, "depth": 8.0},
    "5 mL":  {"tip_od": 4.3, "collar_od": 7.5, "depth": 8.0},
    "10 mL": {"tip_od": 4.3, "collar_od": 7.5, "depth": 8.0},
    "20 mL": {"tip_od": 4.3, "collar_od": 7.5, "depth": 8.0},
    "30 mL": {"tip_od": 4.7, "collar_od": 8.0, "depth": 9.0},
    "50 mL": {"tip_od": 4.7, "collar_od": 8.0, "depth": 9.0},
    "60 mL": {"tip_od": 4.7, "collar_od": 8.0, "depth": 9.0},
}
SYRINGE_SIZE_LIST = list(SYRINGE_SIZES.keys())
DEFAULT_SYRINGE = "10 mL"

# Sensible starting point for a printed hobby mould, in mm.
DEFAULTS = {
    "WallThickness": 8.0,
    "FloorThickness": 8.0,
    "RoofThickness": 8.0,
    "KeyDiameter": 6.0,
    "KeyHeight": 3.0,
    "KeyClearance": 0.25,
    "KeyInset": 7.0,
    "GutterWidth": 4.0,
    "GutterDepth": 2.0,
    "GutterGap": 0.6,
    "InjectionDiameter": 3.0,
    "InjectionChannelLength": 5.0,
    "VentDiameter": 1.5,
    "VentWidth": 2.0,
    "VentLength": 4.0,
    "BoltInset": 7.0,
    "PrySlotWidth": 12.0,
    "PrySlotDepth": 4.0,
    "OverpourPercent": 15.0,
    "MeshDeviation": 0.05,
}
