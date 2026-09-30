# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Syringe injection port with multiple adapter styles.

Two connection styles:

* **Luer Lock** (default): a Luer taper bore plus a cylindrical collar
  recess. The syringe clicks in and the collar wings hold it.
* **Friction Fit**: a plain tapered hole sized for a Luer slip (non-
  locking) syringe.  The user pushes the tip in and the taper grips.

The injection channel runs from the top surface of the upper mold half
straight down until it meets the cavity (like SolidWorks "Up to Next").
"""

import FreeCAD as App
import Part

from ..analysis import outer_wires, section_face
from ..models import (
    INJECTION_FRICTION,
    INJECTION_LUER_LOCK,
    SYRINGE_SIZES,
    DEFAULT_SYRINGE,
)


def _find_cavity_z(pieces, x, y, block_top):
    """Ray-cast downward from (x, y, block_top) to find the cavity ceiling.

    Steps down through the upper mold piece and returns the Z where the
    first solid-to-void transition occurs, i.e. the ceiling of the cavity
    at that XY position. A binary search refines the result to sub-micron
    precision after the coarse scan finds the right interval.

    Falls back to *None* if no intersection is found.
    """
    for piece in pieces:
        if piece.side != "above":
            continue
        shape = piece.shape
        try:
            step = 0.1
            # Start slightly inside the top face so isInside reliably
            # reports True for the mold wall.
            z = block_top - 0.05
            inside_prev = shape.isInside(App.Vector(x, y, z), 1e-4, True)
            while z > shape.BoundBox.ZMin:
                z -= step
                inside_now = shape.isInside(App.Vector(x, y, z), 1e-4, True)
                if inside_prev and not inside_now:
                    # Transitioned from solid to void: this is the cavity
                    # ceiling.  Refine with binary search.
                    lo, hi = z, z + step
                    for _ in range(20):
                        mid = (lo + hi) / 2.0
                        if shape.isInside(App.Vector(x, y, mid), 1e-4, True):
                            hi = mid
                        else:
                            lo = mid
                    return lo
                inside_prev = inside_now
        except Exception:
            pass
    return None


def add_injection_port(
    pieces,
    wires,
    parting_height,
    block_top,
    syringe_size=DEFAULT_SYRINGE,
    channel_diameter=3.0,
    channel_length=5.0,
    custom_position=None,
    style=INJECTION_LUER_LOCK,
):
    """Cut an injection channel and adapter recess into the upper piece.

    *style* selects the adapter geometry:

    * ``INJECTION_LUER_LOCK``: Luer taper + collar recess + entry chamfer.
    * ``INJECTION_FRICTION``: tapered hole for a slip-tip syringe.

    When *custom_position* is an ``App.Vector`` in local frame coordinates,
    its X and Y are used as the channel centre instead of the cavity centroid.

    Returns the centre point on success, or ``None`` on failure.
    """
    if not wires:
        return None

    spec = SYRINGE_SIZES.get(syringe_size)
    if spec is None:
        spec = SYRINGE_SIZES[DEFAULT_SYRINGE]

    tip_od = spec["tip_od"]
    collar_od = spec["collar_od"]
    adapter_depth = spec["depth"]

    # Determine the placement centre
    if custom_position is not None:
        centre = App.Vector(custom_position.x, custom_position.y, parting_height)
    else:
        face = section_face(wires)
        if face is None:
            return None
        point = face.CenterOfMass
        centre = App.Vector(point.x, point.y, parting_height)

        # Make sure the point is inside the cavity
        if not face.isInside(App.Vector(point.x, point.y, parting_height), 1e-3, True):
            try:
                centre = App.Vector(face.Vertexes[0].Point)
                centre.z = parting_height
            except Exception:
                return None

    # -- Injection channel --
    # Ray-cast downward to find where the channel meets the cavity
    # ("Up to Next"), so the bore always connects regardless of how far
    # the part extends above the parting height.
    channel_r = channel_diameter / 2.0
    adapter_seat_z = block_top - adapter_depth

    cavity_z = _find_cavity_z(pieces, centre.x, centre.y, block_top)
    if cavity_z is None:
        cavity_z = parting_height

    # Start the channel slightly below the cavity ceiling so the bore
    # fully breaks into the void, and run it up to the adapter seat.
    channel_bottom = cavity_z - 0.5
    channel_height = adapter_seat_z - channel_bottom + 0.5
    if channel_height <= 0:
        channel_height = block_top - channel_bottom + 1.0
        adapter_seat_z = channel_bottom + channel_height - 1.0

    channel = Part.makeCylinder(
        channel_r,
        channel_height,
        App.Vector(centre.x, centre.y, channel_bottom),
        App.Vector(0, 0, 1),
    )

    # -- Build the tool list depending on style --
    tools = [channel]

    if style == INJECTION_FRICTION:
        # Friction Fit: a tapered hole matching the Luer slip taper.
        # Wider at the top (entry), narrowing to grip the syringe tip.
        taper_top_r = tip_od / 2.0 + adapter_depth * 0.06 + 0.15
        taper_bottom_r = tip_od / 2.0 - 0.1
        taper = Part.makeCone(
            taper_bottom_r,
            taper_top_r,
            adapter_depth,
            App.Vector(centre.x, centre.y, adapter_seat_z),
            App.Vector(0, 0, 1),
        )
        tools.append(taper)

        # Small lead-in chamfer at the top surface
        chamfer_r = taper_top_r + 1.0
        chamfer = Part.makeCone(
            taper_top_r,
            chamfer_r,
            1.0,
            App.Vector(centre.x, centre.y, block_top - 0.5),
            App.Vector(0, 0, 1),
        )
        tools.append(chamfer)

    else:
        # Luer Lock
        # 1. Luer taper recess: conical seat for the syringe tip.
        taper_bottom_r = tip_od / 2.0
        taper_top_r = taper_bottom_r + adapter_depth * 0.06
        taper = Part.makeCone(
            taper_bottom_r,
            taper_top_r,
            adapter_depth,
            App.Vector(centre.x, centre.y, adapter_seat_z),
            App.Vector(0, 0, 1),
        )
        tools.append(taper)

        # 2. Collar recess: cylindrical counterbore for the lock collar.
        collar_r = collar_od / 2.0 + 0.3  # clearance for print fit
        collar_depth = 3.5
        collar_z = block_top - collar_depth
        collar = Part.makeCylinder(
            collar_r,
            collar_depth + 0.5,  # break through the top face
            App.Vector(centre.x, centre.y, collar_z),
            App.Vector(0, 0, 1),
        )
        tools.append(collar)

        # 3. Entry chamfer: 45 degree lead-in.
        chamfer_r = collar_r + 1.0
        chamfer = Part.makeCone(
            collar_r,
            chamfer_r,
            1.0,
            App.Vector(centre.x, centre.y, block_top - 0.5),
            App.Vector(0, 0, 1),
        )
        tools.append(chamfer)

    # Cut all geometry into upper pieces only
    cut_any = False
    for piece in pieces:
        if piece.side != "above":
            continue
        for tool in tools:
            piece.cut(tool)
        cut_any = True
    return centre if cut_any else None
