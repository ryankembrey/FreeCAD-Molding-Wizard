# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Registration keys, the cones that make two halves land in one place.

Truncated cones rather than hemispheres, because a cone prints without
support on a flat parting face, self centres as the halves close, and forgives
the elephant foot on the first layer of the mating pocket.
"""

import math

import FreeCAD as App
import Part

from ..frame import normalized
from ..split import secondary_axes

TAPER = 0.65  # top radius as a fraction of the base radius


def add_key_pair(pieces, point, axis, params, cavity=None):
    """Put a male cone on one side of a joint and a pocket on the other.

    Which piece gets which is decided by looking just either side of the
    point, so this works unchanged for the parting plane and for the vertical
    face of a three piece mould.
    """
    axis = normalized(axis)
    point = App.Vector(point)
    diameter = params.get("diameter", 6.0)
    height = params.get("height", 3.0)
    clearance = params.get("clearance", 0.25)
    if diameter <= 0 or height <= 0:
        return False

    base_r = diameter / 2.0
    top_r = max(base_r * TAPER, 0.4)

    male_host = _piece_at(pieces, point - axis * 0.25)
    female_host = _piece_at(pieces, point + axis * 0.25)
    if male_host is None or female_host is None or male_host is female_host:
        return False

    if cavity is not None and not _clear_of_cavity(cavity, point, base_r + clearance + 1.0):
        return False

    male = Part.makeCone(base_r, top_r, height, point, axis)
    female = Part.makeCone(
        base_r + clearance,
        top_r + clearance,
        height + clearance,
        point - axis * (clearance * 0.5),
        axis,
    )
    female_host.cut(female)
    male_host.fuse(male)
    return True


def parting_key_points(bound_box, parting_height, inset, style_is_cylinder):
    """Four candidate positions, tucked in from the outside of the block."""
    centre_x = bound_box.Center.x
    centre_y = bound_box.Center.y
    if style_is_cylinder:
        # The bounding box of a cylinder has XLength == YLength == diameter.
        # Use the actual cylinder radius, not the bounding box diagonal.
        radius = min(bound_box.XLength, bound_box.YLength) / 2.0 - inset
        radius = max(radius, 1.0)
        return [
            App.Vector(
                centre_x + radius * math.cos(math.radians(a)),
                centre_y + radius * math.sin(math.radians(a)),
                parting_height,
            )
            for a in (45, 135, 225, 315)
        ]
    x0 = bound_box.XMin + inset
    x1 = bound_box.XMax - inset
    y0 = bound_box.YMin + inset
    y1 = bound_box.YMax - inset
    return [
        App.Vector(x0, y0, parting_height),
        App.Vector(x1, y0, parting_height),
        App.Vector(x1, y1, parting_height),
        App.Vector(x0, y1, parting_height),
    ]


def secondary_key_points(piece, inset):
    """Two positions on a vertical split face, one low and one high."""
    if piece.split_normal is None:
        return []
    box = piece.shape.BoundBox
    across, _up = secondary_axes(piece.split_normal)
    centre = App.Vector(box.Center.x, box.Center.y, 0.0)
    reach = max(0.5 * math.hypot(box.XLength, box.YLength) - inset, 1.0) * 0.55
    z_low = box.ZMin + box.ZLength * 0.28
    z_high = box.ZMin + box.ZLength * 0.72
    points = []
    for offset in (-reach, reach):
        base = centre + across * offset
        points.append(App.Vector(base.x, base.y, z_low))
        points.append(App.Vector(base.x, base.y, z_high))
    return points


def _piece_at(pieces, point):
    for piece in pieces:
        if piece.contains(point):
            return piece
    return None


def _clear_of_cavity(cavity, point, margin):
    """True when the point sits far enough away from the part."""
    try:
        if cavity.isInside(point, 1e-6, True):
            return False
        vertex = Part.Vertex(point)
        return vertex.distToShape(cavity)[0] >= margin
    except Exception:
        return True
