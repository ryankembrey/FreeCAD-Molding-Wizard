# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Syringe injection port with multiple adapter styles.

Two connection styles:

* **Luer Lock** (default): a Luer taper bore plus a cylindrical collar
  recess. The syringe clicks in and the collar wings hold it.
* **Friction Fit**: a plain tapered hole sized for a Luer slip (non-
  locking) syringe.  The user pushes the tip in and the taper grips.

The injection channel is a "through all" cut scoped to the upper mold
half. The halves are already separate bodies by the time this runs, so a
straight cylinder from the top of the block down through the whole upper
body always opens into the cavity, with no need to probe for where the
cavity ceiling is.
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
        try:
            point = face.CenterOfMass
        except AttributeError:
            # Compound from fused faces has no CenterOfMass; fall back
            # to the bounding box centre.
            point = face.BoundBox.Center
        centre = App.Vector(point.x, point.y, parting_height)

        # Make sure the point is inside the cavity
        if not face.isInside(App.Vector(point.x, point.y, parting_height), 1e-3, True):
            try:
                centre = App.Vector(face.Vertexes[0].Point)
                centre.z = parting_height
            except Exception:
                return None

    # Injection channel
    # A through-all cut scoped to the upper piece(s): the cylinder starts
    # below the lowest face of any upper piece and runs up to just under the
    # adapter seat. Whatever material lies in that column (the block wall
    # above the cavity) is removed, and the bore breaks into the cavity
    # wherever it happens to be, however curved the cavity ceiling is.
    uppers = [piece for piece in pieces if piece.side == "above"]
    if not uppers:
        return None

    channel_r = channel_diameter / 2.0
    adapter_seat_z = block_top - adapter_depth

    channel_bottom = min(piece.shape.BoundBox.ZMin for piece in uppers) - 1.0
    # Overlap the adapter seat slightly so the bore and the adapter recess
    # merge into one clean opening.
    channel_top = adapter_seat_z + 0.5
    if channel_top <= channel_bottom:
        # Block shallower than the adapter recess: run the bore to the top.
        channel_top = block_top + 1.0

    channel = Part.makeCylinder(
        channel_r,
        channel_top - channel_bottom,
        App.Vector(centre.x, centre.y, channel_bottom),
        App.Vector(0, 0, 1),
    )

    # Build the tool list depending on style
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

    # Cut all geometry into the upper pieces only. Pieces the tools do not
    # reach (for example the far half of a three piece layout) are left
    # untouched.
    cut_any = False
    reach = _tools_bound_box(tools)
    for piece in uppers:
        if not piece.shape.BoundBox.intersect(reach):
            continue
        volume_before = piece.shape.Volume
        for tool in tools:
            piece.cut(tool)
        if piece.shape.Volume < volume_before - 1e-9:
            cut_any = True
    return centre if cut_any else None


def _tools_bound_box(tools):
    """Combined bounding box of every cutting tool."""
    box = App.BoundBox()
    for tool in tools:
        box.add(tool.BoundBox)
    return box
