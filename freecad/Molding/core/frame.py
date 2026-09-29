# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Pull direction aligned working space.

Every geometric operation in this workbench is easier if the pull direction is
+Z. Rather than write each one to cope with an arbitrary axis, the build maps
the part into a local frame where +Z is the pull direction, does all the work
there, and maps the finished pieces back out at the end.
"""

import FreeCAD as App
import Part


def normalized(vector, fallback=(0.0, 0.0, 1.0)):
    """Return a unit vector, falling back if the input is degenerate."""
    v = App.Vector(vector)
    if v.Length < 1e-9:
        return App.Vector(*fallback)
    v.normalize()
    return v


def local_frame(direction, origin=None):
    """Placement whose local +Z is ``direction``.

    The choice of local X is arbitrary but stable, which is all that matters:
    nothing downstream cares where X points, only that it does not spin
    between rebuilds.
    """
    z = normalized(direction)
    rotation = App.Rotation(App.Vector(0, 0, 1), z)
    return App.Placement(origin or App.Vector(0, 0, 0), rotation)


def to_local(shape, frame):
    """Map a shape from document space into the pull aligned frame.

    The transform is baked into the geometry so the returned shape has an
    identity Placement.  This keeps every downstream boolean in a single
    coordinate system and avoids relying on OCC to honour Locations during
    cuts and fuses.
    """
    out = shape.copy()
    combined = frame.inverse().multiply(out.Placement)
    out.Placement = App.Placement()
    out.transformShape(combined.toMatrix())
    return out


def to_global(shape, frame):
    """Map a shape from the pull aligned frame back into document space.

    As with *to_local*, the transform is baked into the geometry so the
    returned shape has an identity Placement.
    """
    out = shape.copy()
    combined = frame.multiply(out.Placement)
    out.Placement = App.Placement()
    out.transformShape(combined.toMatrix())
    return out


def half_space(origin, normal, size):
    """A block big enough to act as a half space on the ``normal`` side.

    Booleans against a large solid are far more dependable in OCC than
    trimming with an infinite plane, and the cost is negligible at these
    sizes.
    """
    n = normalized(normal)
    box = Part.makeBox(size, size, size)
    place = App.Placement(App.Vector(origin), App.Rotation(App.Vector(0, 0, 1), n))
    offset = App.Placement(App.Vector(-size / 2.0, -size / 2.0, 0.0), App.Rotation())
    box.Placement = place.multiply(offset)
    return box


def working_size(bound_box):
    """A length comfortably larger than anything in the job."""
    return max(bound_box.DiagonalLength * 4.0, 100.0)


def face_normal(face):
    """Outward normal at the middle of a face, honouring its orientation."""
    u0, u1, v0, v1 = face.ParameterRange
    normal = face.normalAt((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    if face.Orientation == "Reversed":
        normal = normal * -1.0
    return normalized(normal)


def is_planar(face):
    return face.Surface.TypeId == "Part::GeomPlane"
