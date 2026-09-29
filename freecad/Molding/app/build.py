# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""The whole mould build, start to finish.

The order matters. Hollow the block before splitting it, because a boolean
against one large solid is more reliable than the same boolean run twice
against two pieces that share a face. Split next, so every feature after that
can ask which piece it landed in rather than being told. Features go on last,
outermost first, so a later cut can always win.

Every stage is wrapped: a mould that is missing its vents is still useful, a
build that raises on the way to the vents is not.
"""

import FreeCAD as App

from ..core import analysis, block as blockmod, split as splitmod
from ..core.features import fasteners, keys, pour
from ..core.frame import local_frame, to_global, to_local
from ..core.models import BLOCK_CYLINDER, LAYOUT_TWO


class BuildResult(object):
    def __init__(self):
        self.pieces = []
        self.warnings = []
        self.notes = []
        self.feature_points = []
        self.cavity_volume_ml = 0.0
        self.suggested_pour_ml = 0.0
        self.parting_height = 0.0
        self.frame = None
        self.section_wires = []

    def piece_shapes(self):
        return [(p.key, p.label, p.shape) for p in self.pieces]


def build(job):
    """Run the pipeline for a MoldJob document object."""
    result = BuildResult()

    source = getattr(job, "Source", None)
    if source is None or not hasattr(source, "Shape") or source.Shape.isNull():
        result.warnings.append("No source part is set on this job.")
        return result

    frame = local_frame(job.PullDirection)
    result.frame = frame

    part = to_local(source.Shape, frame)
    part = blockmod.apply_shrink(part, job.Shrink)
    if not part.Solids:
        result.warnings.append(
            "The source is not a solid. Booleans need a closed solid, so the "
            "cavity may come out wrong."
        )

    part_box = part.BoundBox
    parting_height = part_box.ZMin + float(job.PartingOffset)
    parting_height = min(max(parting_height, part_box.ZMin + 1e-3), part_box.ZMax - 1e-3)
    result.parting_height = parting_height

    style_is_cylinder = job.BlockStyle == BLOCK_CYLINDER
    body = blockmod.make_block(
        part_box,
        job.BlockStyle,
        float(job.WallThickness),
        float(job.FloorThickness),
        float(job.RoofThickness),
        float(job.BlockFillet),
    )
    block_box = body.BoundBox

    try:
        hollow = body.cut(part)
    except Exception as exc:
        result.warnings.append("Could not cut the cavity out of the block: %s" % exc)
        return result

    wires = analysis.section_wires(part, parting_height)
    result.section_wires = wires
    if not wires:
        result.warnings.append(
            "The parting plane does not cut the part at this height, so there "
            "is no cavity opening and no gutter."
        )

    pieces = splitmod.split_block(
        hollow, parting_height, job.Layout, float(job.SecondaryAngle), block_box
    )
    if len(pieces) < 2:
        result.warnings.append("The parting plane did not divide the block into pieces.")

    # Gutter first: it defines the keep out zone the keys and bolts avoid.
    if job.Gutter:
        _guard(
            result,
            "overflow gutter",
            pour.add_gutter,
            pieces,
            wires,
            parting_height,
            float(job.GutterWidth),
            float(job.GutterDepth),
            float(job.GutterGap),
            job.GutterSide,
            int(job.GutterReliefs),
        )
        if wires:
            wb = wires[0].BoundBox
            gap = float(job.GutterGap)
            gw = float(job.GutterWidth)
            result.feature_points.append({
                "name": "Gutter",
                "type": "gutter",
                "point": App.Vector(
                    wb.XMax + gap + gw / 2.0,
                    (wb.YMin + wb.YMax) / 2.0,
                    parting_height,
                ),
            })

    if job.RegistrationKeys:
        params = {
            "diameter": float(job.KeyDiameter),
            "height": float(job.KeyHeight),
            "clearance": float(job.KeyClearance),
        }
        margin = float(job.GutterWidth) + float(job.GutterGap) if job.Gutter else 1.0
        placed = 0
        key_positions = []
        for point in keys.parting_key_points(
            block_box, parting_height, float(job.KeyInset), style_is_cylinder
        ):
            if _too_close(wires, point, float(job.KeyDiameter) / 2.0 + margin):
                continue
            if keys.add_key_pair(pieces, point, App.Vector(0, 0, 1), params, part):
                key_positions.append(point)
                placed += 1
        if job.Layout != LAYOUT_TWO:
            for piece in list(pieces):
                if piece.split_normal is None:
                    continue
                for point in keys.secondary_key_points(piece, float(job.KeyInset)):
                    if keys.add_key_pair(pieces, point, piece.split_normal, params, part):
                        key_positions.append(point)
                        placed += 1
        result.notes.append("Registration keys placed: %d" % placed)
        if placed == 0:
            result.warnings.append(
                "No registration keys fitted. Reduce the key inset or diameter, "
                "or add wall thickness."
            )
        for kp in key_positions:
            result.feature_points.append({
                "name": "Reg. Key",
                "type": "key",
                "point": kp,
            })

    # Injection port (syringe adapter) replaces the old pour port
    if getattr(job, "InjectionPort", False):
        from ..core.features import injection

        custom_inj = None
        if getattr(job, "UseCustomInjectionPos", False):
            try:
                gp = App.Vector(job.InjectionPosition)
                custom_inj = frame.inverse().multVec(gp)
            except Exception:
                pass

        inj_centre = _guard(
            result,
            "injection port",
            injection.add_injection_port,
            pieces,
            wires,
            parting_height,
            block_box.ZMax,
            getattr(job, "SyringeSize", "10 mL"),
            float(getattr(job, "InjectionDiameter", 3.0)),
            float(getattr(job, "InjectionChannelLength", 5.0)),
            custom_inj,
        )
        if inj_centre is not None:
            result.feature_points.append({
                "name": "Injection Port",
                "type": "injection",
                "point": App.Vector(inj_centre.x, inj_centre.y, block_box.ZMax),
            })

    # Legacy pour port support
    if getattr(job, "PourPort", False):
        _guard(
            result,
            "pour port",
            pour.add_pour_port,
            pieces,
            wires,
            parting_height,
            block_box.ZMax,
            float(job.PourDiameter),
            float(job.FunnelDiameter),
            float(job.FunnelDepth),
        )

    if int(job.VentCount) > 0:
        custom_vent_pos = None
        if getattr(job, "UseCustomVentPositions", False):
            try:
                raw = list(job.VentPositions)
                if raw:
                    custom_vent_pos = [
                        frame.inverse().multVec(App.Vector(v))
                        for v in raw
                    ]
            except Exception:
                pass

        vent_result = _guard(
            result,
            "vents",
            pour.add_vents,
            pieces,
            wires,
            parting_height,
            block_box.ZMax,
            int(job.VentCount),
            float(job.VentDiameter),
            getattr(job, "VentShape", "Cylinder"),
            float(getattr(job, "VentWidth", 2.0)),
            float(getattr(job, "VentLength", 4.0)),
            custom_vent_pos,
        )
        if vent_result is not None:
            vent_count, vent_positions = vent_result
            for vp in vent_positions:
                result.feature_points.append({
                    "name": "Vent",
                    "type": "vent",
                    "point": vp,
                })

    if getattr(job, "Bolts", False):
        points = [
            p
            for p in fasteners.bolt_points(
                block_box, float(job.BoltInset), style_is_cylinder, parting_height
            )
            if not _too_close(wires, p, 2.0)
        ]
        _guard(
            result,
            "bolt holes",
            fasteners.add_bolts,
            pieces,
            points,
            block_box.ZMin,
            block_box.ZMax,
            job.BoltSize,
            float(job.BoltClearance),
            bool(job.Counterbore),
            bool(job.NutTrap),
        )

    if job.PrySlots:
        _guard(
            result,
            "pry slots",
            fasteners.add_pry_slots,
            pieces,
            block_box,
            parting_height,
            float(job.PrySlotWidth),
            float(job.PrySlotDepth),
        )
        # Pry slots sit on opposite sides of the block at the parting height
        cx = (block_box.XMin + block_box.XMax) / 2.0
        cy = (block_box.YMin + block_box.YMax) / 2.0
        reach = max(block_box.XMax - cx, block_box.YMax - cy)
        result.feature_points.append({
            "name": "Pry Slot",
            "type": "pry",
            "point": App.Vector(cx + reach, cy, parting_height),
        })
        result.feature_points.append({
            "name": "Pry Slot",
            "type": "pry",
            "point": App.Vector(cx - reach, cy, parting_height),
        })

    _measure(result, part, parting_height, float(job.OverpourPercent))

    # Transform feature label points from local frame back to global coords
    for fp in result.feature_points:
        fp["point"] = frame.multVec(fp["point"])

    for piece in pieces:
        piece.shape = to_global(piece.shape, frame)
    result.pieces = pieces
    return result


def _measure(result, part, parting_height, overpour):
    volume = analysis.volume_ml(part)
    result.cavity_volume_ml = volume
    result.suggested_pour_ml = volume * (1.0 + overpour / 100.0)

    for direction, name in ((1, "upper"), (-1, "lower")):
        report = analysis.undercut_report(part, parting_height, direction)
        if report["count"]:
            result.notes.append(
                "%d face(s) undercut the %s half (worst %.1f degrees). "
                "Silicone will flex off these; only a concern for rigid moulds."
                % (report["count"], name, report["worst_angle"])
            )
        draft = analysis.draft_report(part, parting_height, direction)
        if draft is not None and draft < 1.0:
            result.notes.append(
                "Minimum draft on the %s half is %.1f degrees, so expect it to "
                "grip." % (name, draft)
            )


def _too_close(wires, point, margin):
    """Keep features off the cavity opening."""
    if not wires:
        return False
    try:
        import Part

        vertex = Part.Vertex(App.Vector(point.x, point.y, wires[0].BoundBox.ZMin))
        for wire in wires:
            if vertex.distToShape(wire)[0] < margin:
                return True
            face = analysis.wire_face(wire)
            if face is not None and face.isInside(vertex.Point, 1e-3, True):
                return True
    except Exception:
        return False
    return False


def _guard(result, label, function, *args):
    try:
        return function(*args)
    except Exception as exc:
        result.warnings.append("Skipped the %s: %s" % (label, exc))
        return None
