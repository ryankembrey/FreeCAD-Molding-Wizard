# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Everything to do with getting silicone in and air out.

The gutter is the important one for the overfill and press method: pour more
silicone than the cavity needs, close the mould, and the surplus has to go
somewhere. Without a gutter it goes across the whole parting face, holds the
halves apart, and every part comes out with a thick flash line and a wrong
dimension across the split.
"""

import math

import FreeCAD as App
import Part

from ..analysis import outer_wires, section_face
from ..models import (
    GUTTER_ABOVE, GUTTER_BELOW, GUTTER_BOTH,
    VENT_DIR_DOWN, VENT_DIR_NEAREST_WALL, VENT_DIR_UP,
)


def add_gutter(pieces, wires, parting_height, width, depth, gap, side, relief_count=4):
    """Cut a moat around the cavity opening, on the parting face.

    ``gap`` leaves a land between the cavity and the moat so the two halves
    still seal against each other; the relief slots are the deliberate leak
    path through that land.
    """
    if width <= 0 or depth <= 0 or not wires:
        return 0

    made = 0
    for wire in outer_wires(wires):
        ring = _ring_face(wire, gap, width)
        if ring is None:
            continue
        targets = _targets(pieces, side)
        for piece, direction in targets:
            solid = ring.extrude(App.Vector(0, 0, depth * direction))
            piece.cut(solid)
        if relief_count > 0 and gap > 1e-6:
            for piece, direction in targets:
                for solid in _relief_slots(
                    wire, gap, width, depth, parting_height, relief_count, direction
                ):
                    piece.cut(solid)
        made += 1
    return made


def _targets(pieces, side):
    out = []
    for piece in pieces:
        if piece.side == "above" and side in (GUTTER_ABOVE, GUTTER_BOTH):
            out.append((piece, 1.0))
        elif piece.side == "below" and side in (GUTTER_BELOW, GUTTER_BOTH):
            out.append((piece, -1.0))
    return out


def _ring_face(wire, gap, width):
    try:
        inner = wire.makeOffset2D(gap) if gap > 1e-9 else wire.copy()
        outer = wire.makeOffset2D(gap + width)
        inner_face = section_face([inner])
        outer_face = section_face([outer])
        if inner_face is None or outer_face is None:
            return None
        ring = outer_face.cut(inner_face)
        return ring if ring.Area > 1e-9 else None
    except Exception:
        return None


def _relief_slots(wire, gap, width, depth, height, count, direction):
    """Short channels through the land, so surplus can reach the gutter.

    Each channel is a small cylinder sitting just clear of the parting plane
    on the gutter side, so it opens the land without scoring the face of the
    piece that has no gutter in it.
    """
    slots = []
    radius = max(min(depth * 0.4, width * 0.4), 0.5)
    axis_z = height + direction * (radius + 0.05)
    try:
        centre = wire.BoundBox.Center
        samples = wire.discretize(Number=180)
        length = gap + width + 3.0
        for i in range(count):
            angle = 2.0 * math.pi * i / float(count)
            ray = App.Vector(math.cos(angle), math.sin(angle), 0.0)
            hit = _point_towards(samples, centre, ray)
            if hit is None:
                continue
            start = App.Vector(hit.x, hit.y, axis_z) - ray * 1.5
            slots.append(Part.makeCylinder(radius, length, start, ray))
    except Exception:
        return []
    return slots


def _point_towards(samples, centre, ray):
    """Sample point sitting furthest along the given direction."""
    best = None
    best_score = None
    for point in samples:
        offset = App.Vector(point.x - centre.x, point.y - centre.y, 0.0)
        if offset.Length < 1e-9:
            continue
        score = (offset.x * ray.x + offset.y * ray.y) / offset.Length
        if best_score is None or score > best_score:
            best_score = score
            best = point
    return best


def add_pour_port(pieces, wires, parting_height, block_top, diameter, funnel_d, funnel_h):
    """A hole from the top of the mould down into the cavity, with a funnel.

    Only cut into pieces above the parting plane: the pour port belongs on
    whichever piece goes on last.
    """
    if diameter <= 0 or not wires:
        return False
    face = section_face(wires)
    if face is None:
        return False
    point = face.CenterOfMass
    centre = App.Vector(point.x, point.y, parting_height)
    if not face.isInside(App.Vector(point.x, point.y, parting_height), 1e-3, True):
        # A crescent shaped section can put the centroid outside the material,
        # so fall back to a point that is definitely over the cavity.
        try:
            centre = App.Vector(face.Vertexes[0].Point)
            centre.z = parting_height
        except Exception:
            return False

    height = max(block_top - parting_height + 1.0, 1.0)
    bore = Part.makeCylinder(diameter / 2.0, height, centre, App.Vector(0, 0, 1))
    tools = [bore]
    if funnel_d > diameter and funnel_h > 0:
        funnel = Part.makeCone(
            diameter / 2.0,
            funnel_d / 2.0,
            funnel_h,
            App.Vector(centre.x, centre.y, block_top - funnel_h),
            App.Vector(0, 0, 1),
        )
        tools.append(funnel)

    cut_any = False
    for piece in pieces:
        if piece.side != "above":
            continue
        for tool in tools:
            piece.cut(tool)
        cut_any = True
    return cut_any


def add_vents(pieces, wires, parting_height, block_top, count, diameter,
              vent_shape="Cylinder", vent_width=2.0, vent_length=4.0,
              custom_positions=None, direction=VENT_DIR_UP, block_box=None):
    """Thin risers at the extremities of the cavity, where air gets trapped.

    *vent_shape* selects cylindrical (default) or rectangular cross section.
    *custom_positions* is an optional list of ``App.Vector`` in local frame;
    when provided the vents are placed at those XY coordinates instead of
    being auto-distributed along the outer wires.
    *direction* controls where the vent exits:
        Up: through the upper piece (+Z, the default).
        Down: through the lower piece (-Z).
        Nearest wall: horizontally toward the closest block wall, through the
        upper piece at the parting surface.

    Returns ``(count_placed, positions)`` where *positions* is a list of the
    ``App.Vector`` base points actually used (in local coordinates).
    """
    if count <= 0 or not wires:
        return 0, []
    if diameter <= 0 and vent_shape != "Rectangular":
        return 0, []

    made = 0
    positions = []

    if custom_positions:
        all_points = list(custom_positions)
    else:
        all_points = []
        for wire in outer_wires(wires):
            all_points.extend(_spread(wire, count))

    is_rect = vent_shape == "Rectangular" and vent_width > 0 and vent_length > 0

    for point in all_points:
        base = App.Vector(point.x, point.y, parting_height)

        if direction == VENT_DIR_NEAREST_WALL:
            tool = _vent_to_wall(base, diameter, vent_shape, vent_width,
                                 vent_length, block_box)
        elif direction == VENT_DIR_DOWN:
            tool = _vent_vertical(base, diameter, vent_shape, vent_width,
                                  vent_length, parting_height, block_box,
                                  going_up=False)
        else:
            tool = _vent_vertical(base, diameter, vent_shape, vent_width,
                                  vent_length, parting_height, block_box,
                                  going_up=True)

        if tool is None:
            continue

        target_side = "below" if direction == VENT_DIR_DOWN else "above"
        for piece in pieces:
            if piece.side == target_side:
                piece.cut(tool)
        positions.append(base)
        made += 1
    return made, positions


def _vent_vertical(base, diameter, vent_shape, vent_width, vent_length,
                   parting_height, block_box, going_up=True):
    """Create a vertical vent tool going up or down from the parting plane.

    The tool overshoots the parting surface by 1 mm so the boolean cut
    does not have to deal with coplanar faces (OCC fails silently when the
    tool end face sits exactly on a piece boundary).
    """
    is_rect = vent_shape == "Rectangular" and vent_width > 0 and vent_length > 0

    if going_up:
        block_edge = block_box.ZMax if block_box else parting_height + 50.0
        height = max(block_edge - parting_height + 1.0, 1.0)
        origin_z = parting_height
    else:
        block_edge = block_box.ZMin if block_box else parting_height - 50.0
        # Start 1 mm below the block floor and extend 1 mm past the parting
        # surface, so the tool clearly pokes through both faces.
        origin_z = block_edge - 1.0
        height = max(parting_height - block_edge + 2.0, 1.0)

    origin = App.Vector(base.x, base.y, origin_z)

    if is_rect:
        return Part.makeBox(
            vent_width,
            vent_length,
            height,
            App.Vector(
                origin.x - vent_width / 2.0,
                origin.y - vent_length / 2.0,
                origin.z,
            ),
        )
    else:
        return Part.makeCylinder(diameter / 2.0, height, origin, App.Vector(0, 0, 1))


def _vent_to_wall(base, diameter, vent_shape, vent_width, vent_length,
                  block_box):
    """Create a horizontal vent tool from *base* toward the nearest block wall.

    The vent sits just above the parting surface so the full cross section
    is inside the upper piece.  This avoids the tangent / coplanar case
    where OCC's boolean engine silently produces no cut.
    """
    if block_box is None:
        return None

    is_rect = vent_shape == "Rectangular" and vent_width > 0 and vent_length > 0

    # Lift the channel so the full cross section sits inside the upper
    # piece.  For a cylinder the bottom of the bore touches the parting
    # surface; for a rectangle the bottom face does.
    radius = diameter / 2.0
    if is_rect:
        lift = vent_length / 2.0
    else:
        lift = radius
    channel_z = base.z + lift

    # Find distances to the four walls
    dist_xmin = abs(base.x - block_box.XMin)
    dist_xmax = abs(base.x - block_box.XMax)
    dist_ymin = abs(base.y - block_box.YMin)
    dist_ymax = abs(base.y - block_box.YMax)

    dists = [
        (dist_xmin, "xmin"),
        (dist_xmax, "xmax"),
        (dist_ymin, "ymin"),
        (dist_ymax, "ymax"),
    ]
    dists.sort(key=lambda d: d[0])
    nearest = dists[0][1]

    # Channel length: from the vent point to 1 mm past the block wall
    length = dists[0][0] + 1.0

    # Determine axis direction
    if nearest == "xmin":
        axis = App.Vector(-1, 0, 0)
    elif nearest == "xmax":
        axis = App.Vector(1, 0, 0)
    elif nearest == "ymin":
        axis = App.Vector(0, -1, 0)
    else:
        axis = App.Vector(0, 1, 0)

    channel_base = App.Vector(base.x, base.y, channel_z)

    if is_rect:
        # Build axis-aligned box.  The "width" of the rectangle is
        # perpendicular to the travel direction, the "length" is along it.
        if nearest in ("xmin", "xmax"):
            bx = min(base.x, base.x + axis.x * length)
            return Part.makeBox(
                length,
                vent_width,
                vent_length,
                App.Vector(
                    bx,
                    base.y - vent_width / 2.0,
                    channel_z - vent_length / 2.0,
                ),
            )
        else:
            by = min(base.y, base.y + axis.y * length)
            return Part.makeBox(
                vent_width,
                length,
                vent_length,
                App.Vector(
                    base.x - vent_width / 2.0,
                    by,
                    channel_z - vent_length / 2.0,
                ),
            )
    else:
        return Part.makeCylinder(radius, length, channel_base, axis)


def _spread(wire, count):
    try:
        samples = wire.discretize(Number=max(count * 8, 16))
    except Exception:
        return []
    step = max(len(samples) // count, 1)
    return [samples[i * step] for i in range(count) if i * step < len(samples)]
