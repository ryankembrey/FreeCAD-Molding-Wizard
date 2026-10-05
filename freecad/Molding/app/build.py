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


def build(job, progress_fn=None):
    """Run the pipeline for a MoldJob document object.

    *progress_fn*, when provided, is called with ``(step_number, total_steps,
    description)`` before each major stage so the GUI can update a progress
    bar.  Disabled features are skipped entirely so the bar only counts
    stages that do real work.
    """
    result = BuildResult()

    source = getattr(job, "Source", None)
    if source is None or not hasattr(source, "Shape") or source.Shape.isNull():
        result.warnings.append("No source part is set on this job.")
        return result

    # Pre-check which optional features are enabled so the progress bar
    # only counts steps that will actually do work.
    has_gutter = bool(job.Gutter)
    has_keys = bool(job.RegistrationKeys)
    has_injection = bool(getattr(job, "InjectionPort", False))
    has_pour = bool(getattr(job, "PourPort", False))
    has_vents = int(job.VentCount) > 0
    has_bolts = bool(getattr(job, "Bolts", False))
    has_pry = bool(job.PrySlots)
    has_emboss = bool(getattr(job, "Emboss", False))

    n_steps = 5  # prepare, block, cavity, split, finish (always run)
    if has_gutter:
        n_steps += 1
    if has_keys:
        n_steps += 1
    if has_injection:
        n_steps += 1
    if has_pour:
        n_steps += 1
    if has_vents:
        n_steps += 1
    if has_bolts:
        n_steps += 1
    if has_pry:
        n_steps += 1
    if has_emboss:
        n_steps += 1

    last_step = n_steps - 1
    step = [0]

    def _step(text):
        if progress_fn is not None:
            progress_fn(step[0], last_step, text)
        step[0] += 1

    _step("Preparing geometry…")

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

    _step("Building mold block…")

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

    _step("Cutting cavity…")

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

    _step("Splitting at parting line…")

    pieces = splitmod.split_block(
        hollow, parting_height, job.Layout, float(job.SecondaryAngle), block_box
    )
    if len(pieces) < 2:
        result.warnings.append("The parting plane did not divide the block into pieces.")

    # Gutter first: it defines the keep-out zone the keys and bolts avoid.
    if has_gutter:
        _step("Adding overflow gutter…")
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

    if has_keys:
        _step("Adding registration keys…")
        params = {
            "diameter": float(job.KeyDiameter),
            "height": float(job.KeyHeight),
            "clearance": float(job.KeyClearance),
        }
        placed = 0
        key_positions = []

        custom_key_pos = None
        if getattr(job, "UseCustomKeyPositions", False):
            try:
                raw = list(job.KeyPositions)
                if raw:
                    custom_key_pos = [
                        frame.inverse().multVec(App.Vector(v))
                        for v in raw
                    ]
            except Exception:
                pass

        if custom_key_pos is not None:
            # User-picked positions: place keys at each point
            for point in custom_key_pos:
                point_at_parting = App.Vector(point.x, point.y, parting_height)
                if keys.add_key_pair(pieces, point_at_parting, App.Vector(0, 0, 1), params, part):
                    key_positions.append(point_at_parting)
                    placed += 1
        else:
            # Automatic placement: generate a dense ring of candidates and
            # greedily try each one, same strategy as bolts.
            margin = float(job.GutterWidth) + float(job.GutterGap) if job.Gutter else 1.0
            key_r = float(job.KeyDiameter) / 2.0
            target_count = 4

            candidates = keys.key_candidates(
                block_box, parting_height, float(job.KeyInset),
                style_is_cylinder, density=max(target_count * 8, 32),
            )
            # Pre-filter: drop candidates too close to the cavity opening
            candidates = [
                p for p in candidates
                if not _too_close(wires, p, key_r + margin)
            ]

            # Collect existing feature positions for soft avoidance
            feature_xy = [
                App.Vector(fp["point"].x, fp["point"].y, parting_height)
                for fp in result.feature_points
            ]
            min_feat_dist = key_r * 2.0 + 2.0

            # Greedy try-and-place: score candidates by spacing and feature
            # distance, then attempt add_key_pair at the best; skip on
            # failure and try the next best.
            ranked = keys.select_key_positions(
                candidates, len(candidates),
                feature_xy, min_feat_dist,
            )
            for point in ranked:
                if placed >= target_count:
                    break
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

    # Injection port (syringe adapter) replaces the old pour port.
    if has_injection:
        _step("Adding injection port…")
        from ..core.features import injection

        custom_inj = None
        if getattr(job, "UseCustomInjectionPos", False):
            try:
                gp = App.Vector(job.InjectionPosition)
                custom_inj = frame.inverse().multVec(gp)
            except Exception:
                pass

        inj_style = getattr(job, "InjectionStyle", "Luer Lock")

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
            inj_style,
        )
        if inj_centre is not None:
            result.feature_points.append({
                "name": "Injection Port",
                "type": "injection",
                "point": App.Vector(inj_centre.x, inj_centre.y, block_box.ZMax),
            })

    # Legacy pour port support
    if has_pour:
        _step("Adding pour port…")
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

    if has_vents:
        _step("Adding vents…")
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
            str(getattr(job, "VentDirection", "Up")),
            block_box,
        )
        if vent_result is not None:
            vent_count, vent_positions = vent_result
            for vp in vent_positions:
                result.feature_points.append({
                    "name": "Vent",
                    "type": "vent",
                    "point": vp,
                })

    if has_bolts:
        _step("Adding bolt holes…")
        requested = int(getattr(job, "BoltCount", 4))
        bolt_spec = fasteners.BOLTS.get(job.BoltSize, {})
        bolt_hole_r = (bolt_spec.get("clearance", 4.5) + float(job.BoltClearance)) / 2.0

        custom_bolt_pos = None
        if getattr(job, "UseCustomBoltPositions", False):
            try:
                raw = list(job.BoltPositions)
                if raw:
                    custom_bolt_pos = [
                        frame.inverse().multVec(App.Vector(v))
                        for v in raw
                    ]
            except Exception:
                pass

        if custom_bolt_pos is not None:
            # User-picked positions: use directly (XY at parting height)
            points = [
                App.Vector(p.x, p.y, parting_height)
                for p in custom_bolt_pos
            ]
        else:
            # Automatic placement: generate candidates and select evenly
            candidates = fasteners.bolt_candidates(
                block_box, float(job.BoltInset), style_is_cylinder,
                parting_height, density=max(requested * 6, 24),
            )
            candidates = [
                p for p in candidates
                if not _bolt_hits_part(part, p, bolt_hole_r, block_box)
            ]

            # Collect existing feature positions (XY only) for avoidance
            feature_xy = [
                App.Vector(fp["point"].x, fp["point"].y, parting_height)
                for fp in result.feature_points
            ]
            min_feat_dist = bolt_hole_r * 2.0 + 2.0  # bolt radius + structural margin

            points = fasteners.select_bolt_positions(
                candidates, requested, feature_xy, min_feat_dist,
            )

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
        placed = len(points)
        if custom_bolt_pos is None and placed < requested:
            result.warnings.append(
                "Only %d of %d bolt(s) placed; the rest were too close to "
                "the cavity or other features. Increase the wall thickness "
                "or reduce the bolt count." % (placed, requested)
            )
        result.notes.append("Bolts placed: %d" % placed)
        for bp in points:
            result.feature_points.append({
                "name": "Bolt",
                "type": "bolt",
                "point": App.Vector(bp.x, bp.y, parting_height),
            })

    if has_pry:
        _step("Adding pry slots…")
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

    if has_emboss:
        _step("Adding embossment…")
        from ..core.features import emboss

        _guard(
            result,
            "text embossment",
            emboss.add_emboss,
            pieces,
            block_box,
            parting_height,
            str(getattr(job, "EmbossText", "")),
            float(getattr(job, "EmbossFontSize", 5.0)),
            float(getattr(job, "EmbossDepth", 0.8)),
            str(getattr(job, "EmbossPlacement", "Side wall")),
        )

    _step("Finishing up…")

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


def _bolt_hits_part(part_shape, point, hole_radius, block_box):
    """True when a vertical bolt hole at *point* would break into the part."""
    if not part_shape.Solids:
        return False
    try:
        import Part

        margin = hole_radius + 0.5  # half-mm structural wall minimum
        vertex = Part.Vertex(App.Vector(point.x, point.y, point.z))
        dist = vertex.distToShape(part_shape)[0]
        return dist < margin
    except Exception:
        return False


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
