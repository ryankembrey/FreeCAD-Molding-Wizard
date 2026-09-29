# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""The block of material the cavity gets carved out of."""

import math

import FreeCAD as App
import Part

from .models import BLOCK_CYLINDER


def apply_shrink(shape, percent):
    """Scale the part about its own centre to compensate for shrinkage.

    Scaling has to go through transformGeometry, which rebuilds surfaces, so
    the identity case is short circuited to keep exact geometry whenever the
    user leaves shrink at zero.
    """
    factor = 1.0 + (percent or 0.0) / 100.0
    if abs(factor - 1.0) < 1e-12:
        return shape
    centre = shape.BoundBox.Center
    to_origin = App.Matrix()
    to_origin.move(centre * -1.0)
    scale = App.Matrix()
    scale.scale(factor, factor, factor)
    back = App.Matrix()
    back.move(centre)
    return shape.transformGeometry(back.multiply(scale).multiply(to_origin))


def make_block(bound_box, style, wall, floor, roof, fillet=0.0):
    """The outer mould body, sized around the part's bounding box.

    ``wall`` pads sideways, ``floor`` pads below and ``roof`` above, all
    measured in the pull aligned frame. A cylinder is often the better choice
    for a round part: less print time and no corner that adds nothing.
    """
    z_min = bound_box.ZMin - floor
    z_max = bound_box.ZMax + roof
    height = max(z_max - z_min, 0.001)
    centre_x = (bound_box.XMin + bound_box.XMax) / 2.0
    centre_y = (bound_box.YMin + bound_box.YMax) / 2.0

    if style == BLOCK_CYLINDER:
        radius = 0.5 * math.hypot(bound_box.XLength, bound_box.YLength) + wall
        block = Part.makeCylinder(
            radius, height, App.Vector(centre_x, centre_y, z_min), App.Vector(0, 0, 1)
        )
    else:
        length = bound_box.XLength + 2.0 * wall
        width = bound_box.YLength + 2.0 * wall
        block = Part.makeBox(
            length,
            width,
            height,
            App.Vector(centre_x - length / 2.0, centre_y - width / 2.0, z_min),
        )

    if fillet and fillet > 0.0:
        block = _fillet_vertical_edges(block, fillet)
    return block


def _fillet_vertical_edges(block, radius):
    """Round the upright corners only, so the parting faces stay flat."""
    edges = []
    for edge in block.Edges:
        try:
            direction = edge.Vertexes[-1].Point - edge.Vertexes[0].Point
        except Exception:
            continue
        if direction.Length < 1e-9:
            continue
        direction.normalize()
        if abs(direction.z) > 0.999:
            edges.append(edge)
    if not edges:
        return block
    try:
        return block.makeFillet(radius, edges)
    except Exception:
        return block
