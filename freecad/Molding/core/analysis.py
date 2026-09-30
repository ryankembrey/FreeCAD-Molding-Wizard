# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Measurement and inspection of the part, ahead of any mould geometry.

All functions here expect a shape that has already been mapped into the pull
aligned frame, so "height" always means Z.
"""

import FreeCAD as App
import Part

from .frame import face_normal
from .models import UNDERCUT_TOLERANCE_DEG

import math


def section_wires(shape, height):
    """Closed wires where the plane Z = height cuts the shape."""
    try:
        return list(shape.slice(App.Vector(0, 0, 1), height))
    except Exception:
        return []


def wire_face(wire):
    """Best effort planar face from a single closed wire."""
    if not wire.isClosed():
        return None
    try:
        return Part.Face(wire)
    except Exception:
        return None


def section_face(wires):
    """One face for the whole section, with islands and holes resolved."""
    if not wires:
        return None
    try:
        return Part.makeFace(wires, "Part::FaceMakerBullseye")
    except Exception:
        pass
    faces = [f for f in (wire_face(w) for w in wires) if f is not None]
    if not faces:
        return None
    result = faces[0]
    for face in faces[1:]:
        try:
            result = result.fuse(face)
        except Exception:
            pass
    return result


def section_area(shape, height):
    face = section_face(section_wires(shape, height))
    return face.Area if face is not None else 0.0


def outer_wires(wires):
    """Keep only wires that are not enclosed by another wire.

    Bounding box containment is crude but the sections here come from real
    solids, so a wire fully inside another wire's box is a hole or an inner
    boundary in every practical case.
    """
    kept = []
    boxes = [w.BoundBox for w in wires]
    for i, wire in enumerate(wires):
        inner = False
        for j, other in enumerate(boxes):
            if i == j:
                continue
            if _box_contains(other, boxes[i]):
                inner = True
                break
        if not inner:
            kept.append(wire)
    return kept or list(wires)


def _box_contains(outer, inner, tol=1e-6):
    return (
        outer.XMin <= inner.XMin + tol
        and outer.YMin <= inner.YMin + tol
        and outer.XMax >= inner.XMax - tol
        and outer.YMax >= inner.YMax - tol
        and (outer.XLength > inner.XLength or outer.YLength > inner.YLength)
    )


def parting_profile(shape, samples=64):
    """Cross section area sampled up the pull axis.

    Returns a list of (height, area). Useful both for picking a parting height
    and for showing the user why that height was picked.
    """
    box = shape.BoundBox
    span = box.ZLength
    if span <= 0:
        return []
    profile = []
    for i in range(samples):
        # Stay off the very top and bottom, where the section degenerates.
        t = (i + 0.5) / float(samples)
        z = box.ZMin + span * t
        profile.append((z, section_area(shape, z)))
    return profile


def suggest_parting_height(shape, samples=64):
    """Height of the widest cross section.

    For the blobby, organic parts that get silicone moulded this lands on the
    natural parting line almost every time: it is the height at which the part
    is widest, so it is the height at which neither half has to reach around
    the widest point to let go.
    """
    profile = parting_profile(shape, samples)
    if not profile:
        box = shape.BoundBox
        return box.ZMin + box.ZLength / 2.0
    best = max(profile, key=lambda item: item[1])
    return best[0]


def undercut_report(shape, height, direction=1):
    """Faces that a rigid mould half could not release.

    ``direction`` is +1 for the upper half pulling up, and -1 for the lower
    half pulling down. Silicone tolerates a fair amount of this because the
    mould flexes, so the result is advisory rather than a hard error.
    """
    total = 0.0
    count = 0
    worst = 0.0
    for face in shape.Faces:
        centre = face.CenterOfMass
        on_this_side = (centre.z - height) * direction > 0
        if not on_this_side:
            continue
        normal = face_normal(face)
        along = normal.z * direction
        angle = math.degrees(math.asin(max(-1.0, min(1.0, along))))
        if angle < -UNDERCUT_TOLERANCE_DEG:
            total += face.Area
            count += 1
            worst = min(worst, angle)
    return {"count": count, "area": total, "worst_angle": worst}


def draft_report(shape, height, direction=1):
    """Smallest draft angle on the releasing faces of one half."""
    smallest = None
    for face in shape.Faces:
        centre = face.CenterOfMass
        if (centre.z - height) * direction <= 0:
            continue
        normal = face_normal(face)
        along = normal.z * direction
        angle = math.degrees(math.asin(max(-1.0, min(1.0, along))))
        if angle < 0:
            continue
        smallest = angle if smallest is None else min(smallest, angle)
    return smallest


def volume_ml(shape):
    """Solid volume in millilitres, which is what a mixing cup is marked in."""
    try:
        return abs(shape.Volume) / 1000.0
    except Exception:
        return 0.0


def centroid_xy(wires):
    """Plan view centre of a section, used to place the pour port."""
    face = section_face(wires)
    if face is None:
        return App.Vector(0, 0, 0)
    try:
        centre = face.CenterOfMass
    except AttributeError:
        centre = face.BoundBox.Center
    return App.Vector(centre.x, centre.y, 0.0)
