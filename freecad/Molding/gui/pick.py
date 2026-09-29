# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Reading intent out of the 3D selection.

Clicking a face to say "pull this way" is faster and less error prone than
typing a vector, and it is what every other mould tool does.
"""

import FreeCAD as App
import FreeCADGui as Gui  # type: ignore

from ..core.frame import face_normal, is_planar


def sub_shapes():
    """Every picked sub element, as (object, shape) pairs."""
    found = []
    for selection in Gui.Selection.getSelectionEx():
        obj = selection.Object
        if selection.SubObjects:
            for sub in selection.SubObjects:
                found.append((obj, sub))
        elif hasattr(obj, "Shape"):
            found.append((obj, obj.Shape))
    return found


def selected_solid():
    """First selected object that carries a usable solid."""
    for selection in Gui.Selection.getSelectionEx():
        obj = selection.Object
        if hasattr(obj, "Shape") and not obj.Shape.isNull() and obj.Shape.Solids:
            return obj
    return None


def direction_from_selection():
    """Pull direction implied by the selection.

    A planar face gives its normal. A straight edge gives its own direction,
    which is how you nominate a draw axis off a chamfer or a shaft. Anything
    else gives nothing, and the caller keeps whatever it had.
    """
    for _obj, shape in sub_shapes():
        if shape.ShapeType == "Face" and is_planar(shape):
            return face_normal(shape)
        if shape.ShapeType == "Edge" and shape.Curve.TypeId == "Part::GeomLine":
            direction = shape.Vertexes[-1].Point - shape.Vertexes[0].Point
            if direction.Length > 1e-9:
                direction.normalize()
                return direction
    return None


def height_from_selection(pull_direction, origin_shape=None):
    """Height along the pull axis of whatever is selected.

    Averaging the picked geometry's centre is deliberate: picking the ring of
    edges around the widest part of a blob and letting the average settle is
    a natural way to place a parting plane by eye.
    """
    from ..core.frame import local_frame, to_local

    frame = local_frame(pull_direction)
    picks = sub_shapes()
    if not picks:
        return None
    total = 0.0
    count = 0
    for _obj, shape in picks:
        try:
            local = to_local(shape, frame)
            total += local.BoundBox.Center.z
            count += 1
        except Exception:
            continue
    if not count:
        return None
    height = total / count
    if origin_shape is not None:
        local = to_local(origin_shape, frame)
        return height - local.BoundBox.ZMin
    return height


def view_direction():
    """Direction the camera is looking, negated so it points at the viewer."""
    try:
        view = Gui.ActiveDocument.ActiveView
        direction = view.getViewDirection()
        return App.Vector(direction) * -1.0
    except Exception:
        return App.Vector(0, 0, 1)
