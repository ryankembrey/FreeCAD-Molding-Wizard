# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Cutting the hollowed block into the pieces that get printed."""

import math

import FreeCAD as App

from .frame import half_space, working_size
from .models import LAYOUT_THREE_BOTTOM, LAYOUT_THREE_TOP


class Piece(object):
    """One printed piece of the mould.

    ``side`` records which way the piece has to travel to come off the part,
    which is what the exporter uses to lay it parting face down, and what the
    key placement uses to decide male or female.
    """

    def __init__(self, key, label, shape, side, split_normal=None):
        self.key = key
        self.label = label
        self.shape = shape
        self.side = side  # "below" or "above" the parting plane
        self.split_normal = split_normal  # set when a second cut made this piece

    @property
    def pull(self):
        return 1.0 if self.side == "above" else -1.0

    def contains(self, point, tolerance=1e-6):
        try:
            return self.shape.isInside(App.Vector(point), tolerance, True)
        except Exception:
            return False

    def cut(self, tool):
        try:
            result = self.shape.cut(tool)
            if result.Volume > 1e-9:
                self.shape = result
        except Exception:
            pass

    def fuse(self, tool):
        try:
            result = self.shape.fuse(tool)
            if result.Volume > 1e-9:
                self.shape = result.removeSplitter()
        except Exception:
            pass


def split_block(block, parting_height, layout, secondary_angle_deg, bound_box):
    """Cut the block at the parting plane, then optionally once more.

    The second cut runs parallel to the pull direction, so the nominated half
    opens sideways like a clamshell. That is the arrangement worth bolting
    together: the two clamshell halves stay clamped while the silicone cures,
    then come apart sideways and release a part that a single split could not.
    """
    size = working_size(bound_box)
    centre = App.Vector(bound_box.Center.x, bound_box.Center.y, parting_height)

    upper = _common(block, half_space(centre, App.Vector(0, 0, 1), size))
    lower = _common(block, half_space(centre, App.Vector(0, 0, -1), size))

    pieces = []
    if lower is not None:
        pieces.append(Piece("lower", "Lower half", lower, "below"))
    if upper is not None:
        pieces.append(Piece("upper", "Upper half", upper, "above"))

    if layout in (LAYOUT_THREE_TOP, LAYOUT_THREE_BOTTOM):
        target_side = "above" if layout == LAYOUT_THREE_TOP else "below"
        pieces = _apply_secondary(pieces, target_side, secondary_angle_deg, centre, size)

    _reassign_orphans(pieces)
    return pieces


def _apply_secondary(pieces, target_side, angle_deg, centre, size):
    angle = math.radians(angle_deg or 0.0)
    normal = App.Vector(math.cos(angle), math.sin(angle), 0.0)
    result = []
    for piece in pieces:
        if piece.side != target_side:
            result.append(piece)
            continue
        positive = _common(piece.shape, half_space(centre, normal, size))
        negative = _common(piece.shape, half_space(centre, normal * -1.0, size))
        if positive is None or negative is None:
            # The plane missed the piece, so leave it whole rather than
            # silently returning one empty solid.
            result.append(piece)
            continue
        base = piece.label.replace(" half", "")
        result.append(Piece(piece.key + "_a", base + " half, side A", negative, piece.side, normal))
        result.append(Piece(piece.key + "_b", base + " half, side B", positive, piece.side, normal))
    return result


def _reassign_orphans(pieces):
    """Move disconnected solid islands to the opposing mold half.

    After the parting plane split, small pockets of mold material can end
    up on the wrong side.  For example, if a cup shaped part is split at
    mid height, the mold material that fills the cup interior above the
    parting line is geometrically *above* the cut, so it lands in the upper
    piece.  But it is physically disconnected from the upper block shell
    and should belong to the lower piece, which it touches at the parting
    plane.

    For every piece that contains more than one disjoint solid, we keep
    the largest solid (the outer block shell) and fuse the smaller orphans
    into the nearest opposing piece.
    """
    above = [p for p in pieces if p.side == "above"]
    below = [p for p in pieces if p.side == "below"]
    if not above or not below:
        return

    transfers = []
    for piece in pieces:
        solids = piece.shape.Solids
        if len(solids) <= 1:
            continue
        by_vol = sorted(solids, key=lambda s: s.Volume, reverse=True)
        main_body = by_vol[0]
        orphans = by_vol[1:]

        targets = below if piece.side == "above" else above
        piece.shape = main_body
        for orphan in orphans:
            best = min(
                targets,
                key=lambda t: (
                    App.Vector(t.shape.BoundBox.Center)
                    - App.Vector(orphan.BoundBox.Center)
                ).Length,
            )
            transfers.append((orphan, best))

    for orphan, target in transfers:
        target.fuse(orphan)


def _common(shape, tool):
    try:
        result = shape.common(tool)
    except Exception:
        return None
    if result is None or result.Volume < 1e-6:
        return None
    result = result.removeSplitter()
    # When the parting plane slices through geometry that creates
    # disconnected volumes on one side (e.g. a dome that produces a ring
    # and a central island), fuse them so each piece is always one body.
    solids = result.Solids
    if len(solids) > 1:
        try:
            fused = solids[0].multiFuse(solids[1:])
            fused = fused.removeSplitter()
            if fused.Volume > 1e-6:
                result = fused
        except Exception:
            pass
    return result


def secondary_axes(normal):
    """In plane directions for a vertical split face: across, then up."""
    across = App.Vector(-normal.y, normal.x, 0.0)
    if across.Length < 1e-9:
        across = App.Vector(1, 0, 0)
    across.normalize()
    return across, App.Vector(0, 0, 1)
