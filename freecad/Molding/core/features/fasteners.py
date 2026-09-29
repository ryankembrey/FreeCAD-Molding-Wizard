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


def bolt_points(bound_box, inset, style_is_cylinder, parting_height, count=4):
    """Distribute *count* bolt positions around the block perimeter.

    Cylinder blocks: evenly spaced on a circle, offset by half a step from
    the registration key angles (45/135/225/315) so bolts and keys never
    share a position.

    Box blocks: walk the rectangle perimeter and space *count* points
    evenly along it, staying *inset* from the outer face.  The walk starts
    at the midpoint of the bottom edge so that with count=4 you get one
    bolt per side, nicely centred.
    """
    if count < 1:
        return []

    centre_x = bound_box.Center.x
    centre_y = bound_box.Center.y

    if style_is_cylinder:
        radius = max(0.5 * math.hypot(bound_box.XLength, bound_box.YLength) - inset, 1.0)
        # Registration keys sit at 45 + n*90.  Offset bolt ring by
        # half a step so they interleave.
        step = 360.0 / count
        start = step / 2.0
        return [
            App.Vector(
                centre_x + radius * math.cos(math.radians(start + step * i)),
                centre_y + radius * math.sin(math.radians(start + step * i)),
                parting_height,
            )
            for i in range(count)
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
    step = perimeter / count

    # Offset by half a step so bolts land between the corners where
    # registration keys sit.
    start = step / 2.0
    points = []
    for i in range(count):
        d = (start + step * i) % perimeter
        if d < w:
            # bottom edge, left to right
            points.append(App.Vector(x0 + d, y0, parting_height))
        elif d < w + h:
            # right edge, bottom to top
            points.append(App.Vector(x1, y0 + (d - w), parting_height))
        elif d < 2 * w + h:
            # top edge, right to left
            points.append(App.Vector(x1 - (d - w - h), y1, parting_height))
        else:
            # left edge, top to bottom
            points.append(App.Vector(x0, y1 - (d - 2 * w - h), parting_height))
    return points


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
