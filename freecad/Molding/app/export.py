# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Writing the pieces out for the slicer.

Orientation is the part of this worth caring about. A mould half wants to be
printed with its parting face on the bed and its cavity opening upward: the
cavity is then a pocket, which needs no support and takes its finish from the
walls rather than from a support interface. Printed the other way up, the
cavity becomes an overhang and the surface that matters is the one the
supports were touching.
"""

import os
import re

import FreeCAD as App

from ..core.frame import local_frame
from ..core.models import EXPORT_BOTH, EXPORT_STEP, EXPORT_STL


def safe_name(text):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return cleaned.strip("_") or "piece"


def orient_for_print(shape, side, pull_direction):
    """Put the parting face on the bed.

    The lower piece already has its parting face upward, so it gets rolled
    over; the upper piece is the right way up as built.
    """
    frame = local_frame(pull_direction)
    out = shape.copy()
    out.Placement = frame.inverse().multiply(out.Placement)
    if side == "below":
        out.rotate(out.BoundBox.Center, App.Vector(1, 0, 0), 180.0)
    box = out.BoundBox
    out.translate(App.Vector(-box.Center.x, -box.Center.y, -box.ZMin))
    return out


def export_pieces(job, directory=None, formats=None, deviation=None, orient=None):
    """Write one file per piece. Returns the list of paths written."""
    directory = directory or str(job.OutputDirectory or "")
    if not directory:
        raise ValueError("Choose an output folder before exporting.")
    if not os.path.isdir(directory):
        os.makedirs(directory)

    formats = formats or job.ExportFormat
    deviation = float(job.MeshDeviation if deviation is None else deviation)
    orient = bool(job.OrientForPrint if orient is None else orient)

    written = []
    prefix = safe_name(job.Label)
    for child in job.Group:
        if not hasattr(child, "Shape") or child.Shape.isNull():
            continue
        shape = child.Shape
        if orient:
            shape = orient_for_print(shape, getattr(child, "PieceSide", ""), job.PullDirection)
        stem = os.path.join(directory, "%s_%s" % (prefix, safe_name(getattr(child, "PieceKey", child.Name))))

        if formats in (EXPORT_STL, EXPORT_BOTH):
            path = stem + ".stl"
            _write_stl(shape, path, deviation)
            written.append(path)
        if formats in (EXPORT_STEP, EXPORT_BOTH):
            path = stem + ".step"
            shape.exportStep(path)
            written.append(path)
    return written


def _write_stl(shape, path, deviation):
    try:
        import Mesh

        points, facets = shape.tessellate(max(deviation, 0.001))
        mesh = Mesh.Mesh()
        for facet in facets:
            mesh.addFacet(points[facet[0]], points[facet[1]], points[facet[2]])
        mesh.write(path)
    except Exception:
        # Part's own writer uses a coarser default, but it always works.
        shape.exportStl(path)
