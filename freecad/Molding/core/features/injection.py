# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Syringe injection port with Luer lock adapter geometry.

Instead of pouring silicone into an open cavity, this creates an injection
channel sized for a disposable syringe tip, with a Luer lock collar recess
so the syringe clicks into the mold and stays put while you push the
plunger. The channel runs from the top surface of the upper mold half
straight down into the cavity.
"""

import math

import FreeCAD as App
import Part

from ..analysis import outer_wires, section_face
from ..models import SYRINGE_SIZES, DEFAULT_SYRINGE


def add_injection_port(
    pieces,
    wires,
    parting_height,
    block_top,
    syringe_size=DEFAULT_SYRINGE,
    channel_diameter=3.0,
    channel_length=5.0,
    custom_position=None,
):
    """Cut an injection channel and Luer lock recess into the upper piece.

    The channel is a narrow bore from the cavity up through the mold wall,
    sized so silicone flows but flash stays minimal. On top sits the Luer
    lock adapter recess: a tapered bore matching the male Luer taper, plus
    an annular groove for the lock collar wings.

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

    # 1. Injection channel: narrow bore from cavity up to the adapter seat
    channel_r = channel_diameter / 2.0
    adapter_seat_z = block_top - adapter_depth
    channel_height = adapter_seat_z - parting_height + 1.0
    if channel_height <= 0:
        channel_height = block_top - parting_height + 1.0
        adapter_seat_z = parting_height + channel_height - 1.0

    channel = Part.makeCylinder(
        channel_r,
        channel_height,
        App.Vector(centre.x, centre.y, parting_height - 0.5),
        App.Vector(0, 0, 1),
    )

    # 2. Luer taper recess: the conical seat the syringe tip pushes into.
    # Male Luer taper is 6% per side (ISO 80369-7), so the bore tapers
    # from tip_od/2 at the bottom to slightly wider at the top.
    taper_bottom_r = tip_od / 2.0
    taper_top_r = taper_bottom_r + adapter_depth * 0.06
    taper = Part.makeCone(
        taper_bottom_r,
        taper_top_r,
        adapter_depth,
        App.Vector(centre.x, centre.y, adapter_seat_z),
        App.Vector(0, 0, 1),
    )

    # 3. Collar recess: a shallow cylindrical counterbore at the top for the
    # lock collar wings. This is what lets the syringe click and stay.
    collar_r = collar_od / 2.0 + 0.3  # clearance for printed fit
    collar_depth = 3.5  # enough for the collar wings to engage
    collar_z = block_top - collar_depth
    collar = Part.makeCylinder(
        collar_r,
        collar_depth + 0.5,  # break through the top face
        App.Vector(centre.x, centre.y, collar_z),
        App.Vector(0, 0, 1),
    )

    # 4. Entry chamfer: a small 45 degree lead-in to guide the syringe tip
    chamfer_r = collar_r + 1.0
    chamfer_depth = 1.0
    chamfer = Part.makeCone(
        collar_r,
        chamfer_r,
        chamfer_depth,
        App.Vector(centre.x, centre.y, block_top - 0.5),
        App.Vector(0, 0, 1),
    )

    # Cut all geometry into upper pieces only
    tools = [channel, taper, collar, chamfer]
    cut_any = False
    for piece in pieces:
        if piece.side != "above":
            continue
        for tool in tools:
            piece.cut(tool)
        cut_any = True
    return centre if cut_any else None
