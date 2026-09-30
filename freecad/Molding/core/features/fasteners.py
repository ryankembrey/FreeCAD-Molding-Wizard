# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Clamping hardware and the slots you need to get the mould open again."""

import math

import FreeCAD as App
import Part

from ..models import BOLTS
from ..split import secondary_axes

NUT_POCKET_CLEARANCE = 2.0  # extra diameter so a socket or spanner can reach the nut


def add_bolts(pieces, points, block_bottom, block_top, size, clearance, counterbore, nut_trap):
    """Through bolts running the full height of the stack.

    The head sinks into the top piece and a cylindrical nut pocket in the
    bottom gives tool access to tighten or remove the nut, so the pieces
    clamp together with nothing proud of either printed face.
    """
    spec = BOLTS.get(size)
    if spec is None or not points:
        return 0

    hole_r = (spec["clearance"] + clearance) / 2.0
    height = block_top - block_bottom + 2.0
    made = 0

    for point in points:
        base = App.Vector(point.x, point.y, block_bottom - 1.0)
        shaft = Part.makeCylinder(hole_r, height, base, App.Vector(0, 0, 1))
        for piece in pieces:
            piece.cut(shaft)

        if counterbore:
            depth = spec["head_h"] + 0.4
            bore = Part.makeCylinder(
                spec["head"] / 2.0 + clearance / 2.0,
                depth + 1.0,
                App.Vector(point.x, point.y, block_top - depth),
                App.Vector(0, 0, 1),
            )
            for piece in pieces:
                piece.cut(bore)

        if nut_trap:
            # Cylindrical pocket: across-corners of the nut plus clearance
            # for a socket or spanner to reach in.
            across_corners = spec["nut_af"] / math.cos(math.radians(30))
            pocket_r = (across_corners + NUT_POCKET_CLEARANCE) / 2.0
            pocket_h = spec["nut_h"] + 0.5
            pocket = Part.makeCylinder(
                pocket_r,
                pocket_h,
                App.Vector(point.x, point.y, block_bottom - 0.5),
                App.Vector(0, 0, 1),
            )
            for piece in pieces:
                piece.cut(pocket)
        made += 1
    return made


def bolt_candidates(bound_box, inset, style_is_cylinder, parting_height, density=24):
    """Generate a dense ring of candidate bolt positions.

    Returns many more candidates than the final bolt count so the caller
    can filter by cavity clearance and feature proximity, then greedily
    pick the best-spaced subset.

    *density* controls how many candidates are placed around the perimeter
    (default 24, giving 15-degree spacing on a cylinder).
    """
    if density < 4:
        density = 4

    centre_x = bound_box.Center.x
    centre_y = bound_box.Center.y

    if style_is_cylinder:
        # The bounding box of a cylinder has XLength == YLength == diameter.
        # Use the actual cylinder radius, not the bounding box diagonal.
        radius = max(min(bound_box.XLength, bound_box.YLength) / 2.0 - inset, 1.0)
        return [
            App.Vector(
                centre_x + radius * math.cos(math.radians(360.0 * i / density)),
                centre_y + radius * math.sin(math.radians(360.0 * i / density)),
                parting_height,
            )
            for i in range(density)
        ]

    # Box: walk the inset rectangle perimeter
    x0 = bound_box.XMin + inset
    x1 = bound_box.XMax - inset
    y0 = bound_box.YMin + inset
    y1 = bound_box.YMax - inset
    if x1 <= x0 or y1 <= y0:
        return []

    w = x1 - x0
    h = y1 - y0
    perimeter = 2.0 * (w + h)
    step = perimeter / density

    points = []
    for i in range(density):
        d = (step * i) % perimeter
        if d < w:
            points.append(App.Vector(x0 + d, y0, parting_height))
        elif d < w + h:
            points.append(App.Vector(x1, y0 + (d - w), parting_height))
        elif d < 2 * w + h:
            points.append(App.Vector(x1 - (d - w - h), y1, parting_height))
        else:
            points.append(App.Vector(x0, y1 - (d - 2 * w - h), parting_height))
    return points


def select_bolt_positions(candidates, count, feature_points=None, min_feature_dist=8.0):
    """Greedily pick *count* positions that maximise mutual spacing.

    *feature_points* is an optional list of ``App.Vector`` positions of
    existing features (keys, vents, injection port, gutter).  Candidates
    closer than *min_feature_dist* to any feature are penalised rather
    than discarded outright, so a crowded mold still gets some bolts.
    """
    if not candidates:
        return []
    if count <= 0:
        return []

    # Score each candidate: distance to nearest feature (lower = worse).
    # Candidates well clear of features get score 1.0; those within
    # min_feature_dist get a fractional score that deprioritises them.
    scores = []
    for pt in candidates:
        score = 1.0
        if feature_points:
            for fp in feature_points:
                dx = pt.x - fp.x
                dy = pt.y - fp.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < min_feature_dist:
                    score = min(score, dist / min_feature_dist)
        scores.append(score)

    # Greedy selection: repeatedly pick the candidate with the best
    # combined score (feature clearance * distance to already-selected).
    selected = []
    used = [False] * len(candidates)
    for _ in range(min(count, len(candidates))):
        best_idx = -1
        best_merit = -1.0
        for i, pt in enumerate(candidates):
            if used[i]:
                continue
            if scores[i] < 0.01:
                continue  # essentially on top of a feature
            # Distance to nearest already-selected bolt
            if selected:
                nearest = min(
                    math.sqrt(
                        (pt.x - s.x) ** 2 + (pt.y - s.y) ** 2
                    )
                    for s in selected
                )
            else:
                nearest = 1e6  # first pick: all equally good spatially
            merit = nearest * scores[i]
            if merit > best_merit:
                best_merit = merit
                best_idx = i
        if best_idx < 0:
            break
        selected.append(candidates[best_idx])
        used[best_idx] = True
    return selected


def bolt_points(bound_box, inset, style_is_cylinder, parting_height, count=4):
    """Legacy wrapper: return *count* evenly spaced bolt positions.

    New code should use ``bolt_candidates`` + ``select_bolt_positions``
    for better results with feature avoidance.
    """
    candidates = bolt_candidates(bound_box, inset, style_is_cylinder, parting_height, density=count)
    return candidates[:count]


def add_pry_slots(pieces, bound_box, parting_height, width, depth):
    """Notches across the seam, so a screwdriver has somewhere to go.

    A cured silicone part grips hard and a printed mould has no draft on the
    outside, so without these the only way to open a mould is to lever on the
    parting face and chip it.
    """
    if width <= 0 or depth <= 0:
        return 0
    height = max(depth, 3.0) * 1.6
    made = 0
    centre = bound_box.Center
    reach = max(bound_box.XLength, bound_box.YLength) * 0.5 + depth
    for angle in (0.0, 180.0):
        radians = math.radians(angle)
        direction = App.Vector(math.cos(radians), math.sin(radians), 0.0)
        across = App.Vector(-direction.y, direction.x, 0.0)
        outer = App.Vector(centre.x, centre.y, parting_height) + direction * reach
        tool = Part.makeBox(depth * 2.0, width, height)
        tool.Placement = App.Placement(
            App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 0, 1), angle)
        )
        corner = (
            outer
            - direction * (depth * 2.0)
            - across * (width / 2.0)
            - App.Vector(0, 0, height / 2.0)
        )
        tool.translate(corner)
        for piece in pieces:
            piece.cut(tool)
        made += 1
    return made


def secondary_bolt_points(piece, inset):
    """Two positions on the vertical face of a clamshell half."""
    if piece.split_normal is None:
        return []
    box = piece.shape.BoundBox
    across, _ = secondary_axes(piece.split_normal)
    reach = max(0.5 * math.hypot(box.XLength, box.YLength) - inset, 1.0) * 0.75
    centre = App.Vector(box.Center.x, box.Center.y, box.Center.z)
    return [centre + across * reach, centre - across * reach]
