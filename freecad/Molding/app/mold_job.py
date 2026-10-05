# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""The MoldJob document object.

A job owns the parameters and the resulting pieces. The pieces are ordinary
Part::Feature children so they behave like any other solid in the tree: you
can measure them, section them, hand them to Mesh, or drag one out and edit it
by hand when the automatic result is not quite right.
"""

import FreeCAD as App

from ..core.models import (
    BLOCK_BOX,
    BLOCK_STYLES,
    BOLT_SIZES,
    DEFAULT_SHRINK_PERCENT,
    DEFAULT_SYRINGE,
    DEFAULTS,
    EMBOSS_PLACEMENTS,
    EMBOSS_SIDE_WALL,
    EXPORT_FORMATS,
    EXPORT_STL,
    GUTTER_ABOVE,
    GUTTER_SIDES,
    INJECTION_LUER_LOCK,
    INJECTION_STYLES,
    LAYOUT_TWO,
    LAYOUTS,
    SYRINGE_SIZE_LIST,
    VENT_CYLINDER,
    VENT_DIR_UP,
    VENT_DIRECTIONS,
    VENT_SHAPES,
)
from . import build as buildmod

PIECE_COLOURS = [
    (0.85, 0.55, 0.25),
    (0.35, 0.60, 0.85),
    (0.55, 0.75, 0.45),
    (0.80, 0.45, 0.70),
]


def create(document, source=None, label="Mold"):
    obj = document.addObject("App::DocumentObjectGroupPython", "MoldJob")
    obj.Label = label
    MoldJob(obj)
    if App.GuiUp:
        from ..gui.view_provider import MoldJobViewProvider

        MoldJobViewProvider(obj.ViewObject)
    if source is not None:
        obj.Source = source
    return obj


def is_job(obj):
    return hasattr(obj, "Proxy") and getattr(obj.Proxy, "Type", None) == "MoldJob"


class MoldJob(object):
    Type = "MoldJob"

    def __init__(self, obj):
        obj.Proxy = self
        self.building = False
        add_properties(obj)

    def onDocumentRestored(self, obj):
        obj.Proxy = self
        self.building = False
        add_properties(obj)

    def dumps(self):
        return None

    def loads(self, state):
        self.building = False
        return None

    __getstate__ = dumps
    __setstate__ = loads

    # Properties that do NOT require a geometry rebuild.
    _SKIP_REBUILD = frozenset({
        "AutoUpdate", "Exploded",
        "OutputDirectory", "ExportFormat", "MeshDeviation", "OrientForPrint",
        "CavityVolume", "SuggestedPour", "Warnings", "Notes",
        "Visibility", "Label", "Label2", "ExpressionEngine",
    })

    def onChanged(self, obj, prop):
        """Filter property changes so view/export/results edits skip rebuild."""
        if prop in self._SKIP_REBUILD:
            obj.purgeTouched()
            if prop == "Exploded":
                _update_explode(obj)

    def execute(self, obj):
        if getattr(self, "building", False):
            return
        if not obj.AutoUpdate:
            return
        rebuild(obj)


def rebuild(obj, progress_fn=None):
    """Run the build and push the results into the child objects.

    *progress_fn*, when provided, is forwarded to ``build()`` so the GUI
    can update a progress bar with ``(step, total, description)`` tuples.
    """
    proxy = obj.Proxy
    if getattr(proxy, "building", False):
        return None
    proxy.building = True
    try:
        result = buildmod.build(obj, progress_fn=progress_fn)
        proxy._last_result = result
        _sync_children(obj, result)
        obj.CavityVolume = round(result.cavity_volume_ml, 3)
        obj.SuggestedPour = round(result.suggested_pour_ml, 3)
        obj.Warnings = list(result.warnings)
        obj.Notes = list(result.notes)
        obj.purgeTouched()
        for message in result.warnings:
            App.Console.PrintWarning("Mold: %s\n" % message)
        return result
    finally:
        proxy.building = False


def _sync_children(obj, result):
    document = obj.Document
    existing = {}
    for child in list(obj.Group):
        key = getattr(child, "PieceKey", None)
        if key:
            existing[key] = child

    wanted = []
    for index, (key, label, shape) in enumerate(result.piece_shapes()):
        child = existing.pop(key, None)
        if child is None:
            child = document.addObject("Part::Feature", "MoldPiece")
            child.addProperty(
                "App::PropertyString", "PieceKey", "Mold", "Identifies this piece to its job"
            )
            child.PieceKey = key
            child.addProperty(
                "App::PropertyString", "PieceSide", "Mold", "Which way this piece pulls off"
            )
            obj.addObject(child)
            if App.GuiUp:
                colour = PIECE_COLOURS[index % len(PIECE_COLOURS)]
                child.ViewObject.ShapeColor = colour
                child.ViewObject.Transparency = 35
        child.Label = "%s %s" % (obj.Label, label)
        child.Shape = shape
        child.PieceSide = _side_of(result, key)
        child.purgeTouched()
        wanted.append(child)

    for stale in existing.values():
        try:
            document.removeObject(stale.Name)
        except Exception:
            pass

    _explode(obj, wanted, result)


def _side_of(result, key):
    for piece in result.pieces:
        if piece.key == key:
            return piece.side
    return ""


def _explode(obj, children, result):
    """Slide the pieces apart for viewing, without touching the geometry.

    The pull axis is derived from the build frame rather than from
    ``obj.PullDirection`` so that the explode direction is guaranteed to
    match the coordinate system the geometry was built in.
    """
    distance = float(obj.Exploded)
    if result.frame is not None:
        axis = result.frame.Rotation.multVec(App.Vector(0, 0, 1))
    else:
        axis = App.Vector(obj.PullDirection)
    if axis.Length < 1e-9:
        axis = App.Vector(0, 0, 1)
    axis.normalize()
    for child, piece in zip(children, result.pieces):
        offset = App.Vector(0, 0, 0)
        if distance > 0:
            offset = axis * (distance * piece.pull)
            if piece.split_normal is not None:
                sideways = piece.split_normal
                sign = 1.0 if piece.key.endswith("_b") else -1.0
                if result.frame is not None:
                    sideways = result.frame.Rotation.multVec(sideways)
                offset = offset + sideways * (distance * 0.8 * sign)
        child.Placement = App.Placement(offset, App.Rotation())


def _update_explode(obj):
    """Re-run the explode without a full geometry rebuild.

    Called from ``onChanged`` when only the Exploded slider moves.
    Falls back silently when there is no cached result yet.
    """
    proxy = obj.Proxy
    result = getattr(proxy, "_last_result", None)
    if result is None:
        return
    children = []
    for piece in result.pieces:
        for child in obj.Group:
            if getattr(child, "PieceKey", None) == piece.key:
                children.append(child)
                break
        else:
            return  # child list doesn't match, need a full rebuild first
    _explode(obj, children, result)


def add_properties(obj):
    def prop(kind, name, group, doc, default=None, enum=None, read_only=False):
        if hasattr(obj, name):
            return
        obj.addProperty(kind, name, group, doc)
        if enum is not None:
            setattr(obj, name, enum)
        if default is not None:
            setattr(obj, name, default)
        if read_only:
            obj.setEditorMode(name, 1)

    # -- Source --
    prop("App::PropertyLink", "Source", "Source", "The part to be moulded")
    prop(
        "App::PropertyFloat",
        "Shrink",
        "Source",
        "Percentage the cavity is grown by, to compensate for cure shrinkage",
        DEFAULT_SHRINK_PERCENT,
    )
    prop(
        "App::PropertyVector",
        "PullDirection",
        "Source",
        "Direction the upper piece lifts away in",
        App.Vector(0, 0, 1),
    )

    # -- Parting --
    prop(
        "App::PropertyEnumeration",
        "Layout",
        "Parting",
        "How many pieces the mould comes apart into",
        LAYOUT_TWO,
        LAYOUTS,
    )
    prop(
        "App::PropertyDistance",
        "PartingOffset",
        "Parting",
        "Height of the parting plane above the bottom of the part",
        0.0,
    )
    prop(
        "App::PropertyAngle",
        "SecondaryAngle",
        "Parting",
        "Rotation of the second cut, for a three piece mould",
        0.0,
    )

    # -- Block --
    prop(
        "App::PropertyEnumeration",
        "BlockStyle",
        "Block",
        "Outer shape of the mould body",
        BLOCK_BOX,
        BLOCK_STYLES,
    )
    prop("App::PropertyDistance", "WallThickness", "Block", "Material around the sides", DEFAULTS["WallThickness"])
    prop("App::PropertyDistance", "FloorThickness", "Block", "Material below the part", DEFAULTS["FloorThickness"])
    prop("App::PropertyDistance", "RoofThickness", "Block", "Material above the part", DEFAULTS["RoofThickness"])
    prop("App::PropertyDistance", "BlockFillet", "Block", "Corner radius, zero for none", 0.0)

    # -- Registration --
    prop("App::PropertyBool", "RegistrationKeys", "Registration", "Add alignment cones at the parting line", True)
    prop("App::PropertyDistance", "KeyDiameter", "Registration", "Key base diameter", DEFAULTS["KeyDiameter"])
    prop("App::PropertyDistance", "KeyHeight", "Registration", "How far the key stands proud", DEFAULTS["KeyHeight"])
    prop("App::PropertyDistance", "KeyClearance", "Registration", "Pocket clearance for print fit", DEFAULTS["KeyClearance"])
    prop("App::PropertyDistance", "KeyInset", "Registration", "Distance from block edge to key", DEFAULTS["KeyInset"])
    prop("App::PropertyVectorList", "KeyPositions", "Registration", "Custom key placement points (global coordinates)")
    prop("App::PropertyBool", "UseCustomKeyPositions", "Registration", "Use manually picked key positions", False)

    # -- Injection (syringe) --
    prop("App::PropertyBool", "InjectionPort", "Injection", "Add a syringe injection port", True)
    prop(
        "App::PropertyEnumeration",
        "InjectionStyle",
        "Injection",
        "Luer Lock: collar recess for locking syringes. "
        "Friction Fit: plain tapered hole for a slip-tip syringe.",
        INJECTION_LUER_LOCK,
        INJECTION_STYLES,
    )
    prop(
        "App::PropertyEnumeration",
        "SyringeSize",
        "Injection",
        "Syringe barrel size (determines Luer lock dimensions)",
        DEFAULT_SYRINGE,
        SYRINGE_SIZE_LIST,
    )
    prop("App::PropertyDistance", "InjectionDiameter", "Injection", "Injection channel bore diameter", DEFAULTS["InjectionDiameter"])
    prop("App::PropertyDistance", "InjectionChannelLength", "Injection", "Channel length from cavity to adapter", DEFAULTS["InjectionChannelLength"])
    prop("App::PropertyVector", "InjectionPosition", "Injection", "Custom injection port position (global coordinates)", App.Vector(0, 0, 0))
    prop("App::PropertyBool", "UseCustomInjectionPos", "Injection", "Use a manually picked injection position", False)

    # -- Overflow gutter --
    prop("App::PropertyBool", "Gutter", "Overflow", "Cut an overflow gutter around the cavity", True)
    prop("App::PropertyDistance", "GutterWidth", "Overflow", "Gutter width", DEFAULTS["GutterWidth"])
    prop("App::PropertyDistance", "GutterDepth", "Overflow", "Gutter depth", DEFAULTS["GutterDepth"])
    prop("App::PropertyDistance", "GutterGap", "Overflow", "Land between cavity and gutter", DEFAULTS["GutterGap"])
    prop(
        "App::PropertyEnumeration",
        "GutterSide",
        "Overflow",
        "Which piece the gutter is cut into",
        GUTTER_ABOVE,
        GUTTER_SIDES,
    )
    prop("App::PropertyInteger", "GutterReliefs", "Overflow", "Relief channels through the land", 4)

    # -- Vents --
    prop("App::PropertyInteger", "VentCount", "Vents", "Number of air vents", 4)
    prop("App::PropertyDistance", "VentDiameter", "Vents", "Vent bore diameter", DEFAULTS["VentDiameter"])
    prop(
        "App::PropertyEnumeration",
        "VentShape",
        "Vents",
        "Cross section shape of each vent (Cylinder or Rectangular)",
        VENT_CYLINDER,
        VENT_SHAPES,
    )
    prop("App::PropertyDistance", "VentWidth", "Vents", "Width of rectangular vents", DEFAULTS["VentWidth"])
    prop("App::PropertyDistance", "VentLength", "Vents", "Length of rectangular vents", DEFAULTS["VentLength"])
    prop(
        "App::PropertyEnumeration",
        "VentDirection",
        "Vents",
        "Up: through the roof. Down: through the floor. "
        "Nearest wall: horizontally to the closest block wall.",
        VENT_DIR_UP,
        VENT_DIRECTIONS,
    )
    prop("App::PropertyVectorList", "VentPositions", "Vents", "Custom vent placement points (global coordinates)")
    prop("App::PropertyBool", "UseCustomVentPositions", "Vents", "Use manually picked vent positions", False)

    # -- Pry slots --
    prop("App::PropertyBool", "PrySlots", "Opening", "Notches at the seam for a lever", True)
    prop("App::PropertyDistance", "PrySlotWidth", "Opening", "Pry slot width", DEFAULTS["PrySlotWidth"])
    prop("App::PropertyDistance", "PrySlotDepth", "Opening", "How far the slot reaches in", DEFAULTS["PrySlotDepth"])

    # -- Legacy pour port (hidden, kept for old files) --
    prop("App::PropertyBool", "PourPort", "Pouring", "Add a pour hole and funnel", False)
    prop("App::PropertyDistance", "PourDiameter", "Pouring", "Pour hole diameter", 8.0)
    prop("App::PropertyDistance", "FunnelDiameter", "Pouring", "Funnel mouth", 18.0)
    prop("App::PropertyDistance", "FunnelDepth", "Pouring", "Funnel depth", 6.0)
    prop("App::PropertyFloat", "OverpourPercent", "Pouring", "Extra silicone beyond cavity volume", DEFAULTS["OverpourPercent"])

    # -- Hardware (bolts, kept but off by default) --
    prop("App::PropertyBool", "Bolts", "Hardware", "Add clamping bolt holes", False)
    prop("App::PropertyInteger", "BoltCount", "Hardware", "Number of bolts around the perimeter", 4)
    prop("App::PropertyEnumeration", "BoltSize", "Hardware", "Bolt thread size", "M4", BOLT_SIZES)
    prop("App::PropertyDistance", "BoltInset", "Hardware", "Inset from block edge", DEFAULTS["BoltInset"])
    prop("App::PropertyDistance", "BoltClearance", "Hardware", "Extra diameter for print fit", 0.3)
    prop("App::PropertyBool", "Counterbore", "Hardware", "Sink the bolt head", True)
    prop("App::PropertyBool", "NutTrap", "Hardware", "Nut pocket for captive nut", True)
    prop("App::PropertyVectorList", "BoltPositions", "Hardware", "Custom bolt placement points (global coordinates)")
    prop("App::PropertyBool", "UseCustomBoltPositions", "Hardware", "Use manually picked bolt positions", False)

    # -- Build --
    prop("App::PropertyBool", "AutoUpdate", "Build", "Rebuild whenever a parameter changes", True)
    prop("App::PropertyDistance", "Exploded", "Build", "Separation for exploded view", 0.0)

    # -- Results --
    prop("App::PropertyFloat", "CavityVolume", "Results", "Volume in millilitres", 0.0, read_only=True)
    prop("App::PropertyFloat", "SuggestedPour", "Results", "Cavity plus overpour, in ml", 0.0, read_only=True)
    prop("App::PropertyStringList", "Warnings", "Results", "Problems from the build", read_only=True)
    prop("App::PropertyStringList", "Notes", "Results", "Observations from the build", read_only=True)

    # -- Embossment --
    prop("App::PropertyBool", "Emboss", "Embossment", "Deboss identification text on mold pieces", False)
    prop(
        "App::PropertyEnumeration",
        "EmbossPlacement",
        "Embossment",
        "Side wall: outer side face. "
        "Outer face: floor bottom / roof top. "
        "Inner face: parting surfaces visible when the mold is open.",
        EMBOSS_SIDE_WALL,
        EMBOSS_PLACEMENTS,
    )
    prop("App::PropertyString", "EmbossText", "Embossment", "Custom label (empty for automatic UPPER/LOWER)", "")
    prop("App::PropertyFloat", "EmbossFontSize", "Embossment", "Letter height in mm", 5.0)
    prop("App::PropertyFloat", "EmbossDepth", "Embossment", "Cut depth in mm", 0.8)

    # -- Export --
    prop("App::PropertyPath", "OutputDirectory", "Export", "Where exported pieces are written")
    prop("App::PropertyEnumeration", "ExportFormat", "Export", "File type for each piece", EXPORT_STL, EXPORT_FORMATS)
    prop("App::PropertyFloat", "MeshDeviation", "Export", "Tessellation tolerance", DEFAULTS["MeshDeviation"])
    prop("App::PropertyBool", "OrientForPrint", "Export", "Lay parting face down", True)
