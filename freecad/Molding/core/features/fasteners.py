# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Clamping hardware and the slots you need to get the mould open again."""

import math

import FreeCAD as App
import Part

from ..models import BOLTS
from ..split import secondary_axes

NUT_SLOP = 0.2  # printed hex pockets come out tight without this


def add_bolts(pieces, points, block_bottom, block_top, size, clearance, counterbore, nut_trap):
    """Through bolts running the full height of the stack.

    The head sinks into the top piece and a hex nut drops into the bottom, so
    the pieces clamp together with nothing proud of either printed face.
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
            pocket = _hex_prism(
                App.Vector(point.x, point.y, block_bottom - 0.5),
                spec["nut_af"] + NUT_SLOP,
                spec["nut_h"] + 0.5,
            )
            for piece in pieces:
                piece.cut(pocket)
        made += 1
    return made


def _hex_prism(base, across_flats, height):
    radius = across_flats / math.sqrt(3.0)
    points = []
    for i in range(7):
        angle = math.radians(60.0 * i)
        points.append(
            App.Vector(base.x + radius * math.cos(angle), base.y + radius * math.sin(angle), base.z)
        )
    wire = Part.makePolygon(points)
    return Part.Face(wire).extrude(App.Vector(0, 0, height))


def bolt_points(bound_box, inset, style_is_cylinder, parting_height):
    """Corner positions, offset a touch from the registration keys."""
    centre_x = bound_box.Center.x
    centre_y = bound_box.Center.y
    if style_is_cylinder:
        radius = max(0.5 * math.hypot(bound_box.XLength, bound_box.YLength) - inset, 1.0)
        return [
            App.Vector(
                centre_x + radius * math.cos(math.radians(a)),
                centre_y + radius * math.sin(math.radians(a)),
                parting_height,
            )
            for a in (0, 90, 180, 270)
        ]
    return [
        App.Vector(bound_box.XMin + inset, bound_box.YMin + inset, parting_height),
        App.Vector(bound_box.XMax - inset, bound_box.YMin + inset, parting_height),
        App.Vector(bound_box.XMax - inset, bound_box.YMax - inset, parting_height),
        App.Vector(bound_box.XMin + inset, bound_box.YMax - inset, parting_height),
    ]


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
