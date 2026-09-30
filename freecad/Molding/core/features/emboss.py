# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Debossed identification text on mold pieces.

Each piece gets a label cut into a chosen surface so the user can tell
upper from lower (or left from right in a three piece mold) at a glance.

Three placement modes:

* **Side wall** : the text goes on the -Y outer wall face, readable
  from outside the mold.
* **Outer face** : the text goes on the floor bottom (lower piece) and
  roof top (upper piece), the surfaces you see from outside.
* **Inner face** : the text goes on the parting surfaces, visible when
  the mold halves are separated.

FreeCAD's ``Part.makeWireString`` needs a TrueType font file on disk.
A search of common system font directories finds one automatically; if
no font is found the emboss step is skipped with a warning.
"""

import os

import FreeCAD as App
import Part

from ..models import EMBOSS_INNER, EMBOSS_OUTER, EMBOSS_SIDE_WALL


# ---- Font discovery ----

_FONT_CANDIDATES = [
    # Linux (Debian / Ubuntu)
    ("/usr/share/fonts/truetype/dejavu", "DejaVuSans.ttf"),
    ("/usr/share/fonts/truetype/freefont", "FreeSans.ttf"),
    ("/usr/share/fonts/truetype/liberation", "LiberationSans-Regular.ttf"),
    # Fedora / RHEL
    ("/usr/share/fonts/dejavu-sans-fonts", "DejaVuSans.ttf"),
    ("/usr/share/fonts/liberation-sans", "LiberationSans-Regular.ttf"),
    # macOS
    ("/System/Library/Fonts/Supplemental", "Arial.ttf"),
    ("/System/Library/Fonts", "Helvetica.ttc"),
    ("/Library/Fonts", "Arial.ttf"),
    # Windows
    ("C:/Windows/Fonts", "arial.ttf"),
]


def _find_font():
    """Return ``(font_dir, font_file)`` or ``None``."""
    for font_dir, font_file in _FONT_CANDIDATES:
        if os.path.isfile(os.path.join(font_dir, font_file)):
            return font_dir, font_file
    # Walk common parent directories as a last resort
    for parent in ("/usr/share/fonts", "/usr/local/share/fonts"):
        if not os.path.isdir(parent):
            continue
        for root, _dirs, files in os.walk(parent):
            for f in files:
                if f.lower().endswith((".ttf", ".otf")):
                    return root, f
    return None


# ---- Text geometry ----

def _make_text_solid(text, font_dir, font_file, height, depth):
    """Extrude *text* into a solid slab.

    The text sits in the XY plane with the baseline along X, letter
    height along Y.  The extrusion runs along +Z.

    Each character is extruded individually as a solid, then all
    character solids are fused together.

    Returns a ``Part.Shape`` (solid) or ``None`` on failure.
    """
    # Part.makeWireString concatenates dir+file with no separator,
    # so the directory must end with a slash.
    if not font_dir.endswith("/") and not font_dir.endswith("\\"):
        font_dir = font_dir + "/"

    try:
        wire_lists = Part.makeWireString(text, font_dir, font_file, height, 0)
    except Exception as exc:
        App.Console.PrintWarning(
            "Emboss: Part.makeWireString raised: %s\n" % exc
        )
        return None

    if not wire_lists:
        App.Console.PrintWarning(
            "Emboss: Part.makeWireString returned no wires for '%s' "
            "(font: %s%s)\n" % (text, font_dir, font_file)
        )
        return None

    App.Console.PrintMessage(
        "Emboss: makeWireString produced %d glyph(s) for '%s'\n"
        % (len(wire_lists), text)
    )

    # Use a generous extrusion thickness so the boolean tool clearly
    # intersects the mold wall.  Positioning controls the visible depth.
    extrude_depth = depth + 10.0

    solids = []
    for idx, char_wires in enumerate(wire_lists):
        if not char_wires:
            continue
        solid = _extrude_character(char_wires, extrude_depth, idx)
        if solid is not None:
            solids.append(solid)

    if not solids:
        App.Console.PrintWarning(
            "Emboss: no character solids could be created from the wires\n"
        )
        return None

    App.Console.PrintMessage(
        "Emboss: extruded %d character solid(s)\n" % len(solids)
    )

    if len(solids) == 1:
        return solids[0]

    # Fuse all character solids together
    try:
        fused = solids[0].multiFuse(solids[1:])
        if fused.Volume > 1e-9:
            return fused
    except Exception:
        pass

    # Fallback: iterative pairwise fuse
    try:
        result = solids[0]
        for s in solids[1:]:
            result = result.fuse(s)
        if result.Volume > 1e-9:
            return result
    except Exception as exc:
        App.Console.PrintWarning(
            "Emboss: could not fuse character solids: %s\n" % exc
        )

    # Last resort: make a compound (not fused, but still cuttable)
    try:
        compound = Part.makeCompound(solids)
        if compound.Volume > 1e-9:
            App.Console.PrintMessage(
                "Emboss: using compound instead of fused solid\n"
            )
            return compound
    except Exception:
        pass

    return None


def _extrude_character(char_wires, depth, index):
    """Extrude one character's wires into a solid.

    Tries several face-making strategies to handle characters with
    interior holes (P, O, D, B, etc.) robustly.

    Returns a ``Part.Shape`` (solid) or ``None``.
    """
    extrusion = App.Vector(0, 0, depth)

    # Method 1: BullseyeFaceMaker automatically sorts outer boundaries
    # and holes.  This is the most reliable path for characters with
    # enclosed regions (P, O, B, D, A, etc.).
    for maker in ("Part::FaceMakerBullseye", "Part::FaceMakerCheese"):
        try:
            face = Part.makeFace(char_wires, maker)
            solid = face.extrude(extrusion)
            if solid.Volume > 1e-9:
                return solid
        except Exception:
            continue

    # Method 2: direct Face from all wires (simple characters)
    try:
        face = Part.Face(char_wires)
        solid = face.extrude(extrusion)
        if solid.Volume > 1e-9:
            return solid
    except Exception:
        pass

    # Method 3: outer wire extruded, then subtract hole solids
    if len(char_wires) >= 2:
        try:
            outer_solid = Part.Face(char_wires[0]).extrude(extrusion)
            for hw in char_wires[1:]:
                try:
                    outer_solid = outer_solid.cut(
                        Part.Face(hw).extrude(extrusion)
                    )
                except Exception:
                    pass
            if outer_solid.Volume > 1e-9:
                return outer_solid
        except Exception:
            pass

    # Method 4: just the first wire, losing any holes but at least
    # producing a visible glyph outline.
    try:
        solid = Part.Face(char_wires[0]).extrude(extrusion)
        if solid.Volume > 1e-9:
            App.Console.PrintWarning(
                "Emboss: character %d extruded without holes\n" % index
            )
            return solid
    except Exception:
        pass

    App.Console.PrintWarning(
        "Emboss: character %d could not be extruded\n" % index
    )
    return None


# ---- Labels ----

def _side_label(piece):
    """Human readable label for a piece based on its pull direction."""
    side = getattr(piece, "side", "")
    if side == "above":
        return "UPPER"
    if side == "below":
        return "LOWER"
    key = getattr(piece, "key", "")
    if key.endswith("_a"):
        return "LEFT"
    if key.endswith("_b"):
        return "RIGHT"
    return side.upper() if side else "MOLD"


# ---- Main entry point ----

def add_emboss(pieces, block_box, parting_height, text, font_size, depth,
               placement=EMBOSS_SIDE_WALL):
    """Deboss identification text into the chosen surface of each piece.

    Parameters
    ----------
    pieces : list
        The mold pieces (with ``.shape``, ``.side``, ``.cut()``).
    block_box : BoundBox
        Bounding box of the outer mold block.
    parting_height : float
        Z height of the parting plane.
    text : str
        Custom label text.  Empty string means automatic per-piece labels.
    font_size : float
        Letter height in mm.
    depth : float
        How deep the text is cut into the surface, in mm.
    placement : str
        One of ``EMBOSS_SIDE_WALL``, ``EMBOSS_OUTER``, ``EMBOSS_INNER``.

    Returns
    -------
    int
        Number of pieces that received embossed text.
    """
    font = _find_font()
    if font is None:
        raise RuntimeError(
            "No TrueType font found on this system. Install DejaVu Sans, "
            "Liberation Sans, or any .ttf font under /usr/share/fonts."
        )

    font_dir, font_file = font
    App.Console.PrintMessage(
        "Emboss: font = %s/%s, placement = %s\n"
        % (font_dir, font_file, placement)
    )

    embossed = 0
    reasons = []

    for piece in pieces:
        label = text if text else _side_label(piece)
        App.Console.PrintMessage(
            "Emboss: piece '%s' (side=%s), label='%s'\n"
            % (piece.key, piece.side, label)
        )

        text_solid = _make_text_solid(
            label, font_dir, font_file, font_size, depth
        )
        if text_solid is None:
            reasons.append("'%s': text geometry failed" % piece.key)
            continue

        App.Console.PrintMessage(
            "Emboss: text solid for '%s': volume=%.4f, bb=%s\n"
            % (piece.key, text_solid.Volume, text_solid.BoundBox)
        )

        placed = _position_text(
            text_solid, piece, block_box, parting_height, depth, placement
        )
        if placed is None:
            reasons.append("'%s': positioning failed" % piece.key)
            continue

        # Perform the cut and verify it changed the shape
        old_volume = piece.shape.Volume
        piece.cut(placed)
        new_volume = piece.shape.Volume
        delta = old_volume - new_volume

        if delta > 1e-6:
            App.Console.PrintMessage(
                "Emboss: piece '%s' cut OK, removed %.4f mm^3\n"
                % (piece.key, delta)
            )
            embossed += 1
        else:
            App.Console.PrintWarning(
                "Emboss: piece '%s' boolean cut had no effect "
                "(volume delta=%.9f). The tool may not intersect "
                "the piece.\n" % (piece.key, delta)
            )
            reasons.append(
                "'%s': boolean cut removed no material" % piece.key
            )

    if embossed == 0 and reasons:
        raise RuntimeError(
            "Embossment failed on all pieces: %s" % "; ".join(reasons)
        )

    return embossed


# ---- Placement helpers ----

def _position_text(text_solid, piece, block_box, parting_height, depth,
                   placement):
    """Choose a face and call the right positioning function."""
    if placement == EMBOSS_OUTER:
        # Lower piece: floor bottom (-Z).  Upper piece: roof top (+Z).
        if piece.side == "below":
            return _place_on_bottom(text_solid, piece, depth)
        else:
            return _place_on_top(text_solid, piece, depth)

    if placement == EMBOSS_INNER:
        # The parting face has the cavity opening in the centre, so the
        # text must be offset to the wall area near the block edge.
        return _place_on_inner(
            text_solid, piece, block_box, parting_height, depth
        )

    # Default: side wall
    return _place_on_side_wall(
        text_solid, piece, block_box, parting_height, depth
    )


def _place_on_side_wall(text_solid, piece, block_box, parting_height, depth):
    """Place text on the -Y (front) side wall of the block.

    The text is rotated 90 degrees around X so it stands upright on the
    wall and reads left-to-right when viewed from outside.

    Vertical centering uses the visible wall extent (block floor to parting
    line, or parting line to block roof) rather than the piece bounding box,
    which may include key protrusions above or pockets below the parting
    surface.
    """
    try:
        pb = piece.shape.BoundBox
        face_cx = (pb.XMin + pb.XMax) / 2.0

        # Use the visible side wall extent, not the full piece bounding
        # box (which includes registration key protrusions).
        if piece.side == "below":
            wall_zmin = block_box.ZMin
            wall_zmax = parting_height
        else:
            wall_zmin = parting_height
            wall_zmax = block_box.ZMax
        face_cz = (wall_zmin + wall_zmax) / 2.0
        wall_height = wall_zmax - wall_zmin

        # Rotate 90 degrees around X:
        #   width stays along +X (reads L-to-R from outside the -Y face)
        #   letter height moves from +Y to +Z (text stands upright)
        #   extrusion moves from +Z to -Y (points outward from the wall)
        placed = text_solid.copy()
        placed.rotate(App.Vector(0, 0, 0), App.Vector(1, 0, 0), 90)

        rb = placed.BoundBox

        if not _fits(rb.XLength, rb.ZLength, pb.XLength, wall_height, piece):
            return None

        # Centre horizontally and vertically on the visible wall face
        dx = face_cx - (rb.XMin + rb.XMax) / 2.0
        dz = face_cz - (rb.ZMin + rb.ZMax) / 2.0

        # After rotation the extrusion runs in -Y.  The tool's Y range
        # is roughly [-(thick), 0].  Position so that *depth* mm of it
        # sits inside the wall (Y > wall_y) and the rest hangs outside.
        wall_y = block_box.YMin
        desired_ymax = wall_y + depth
        dy = desired_ymax - rb.YMax

        placed.translate(App.Vector(dx, dy, dz))

        App.Console.PrintMessage(
            "Emboss: side wall, piece '%s'. Wall Z: %.2f to %.2f, "
            "centre Z=%.2f, Wall Y=%.2f, "
            "tool Y: %.2f to %.2f (%.2f mm inside)\n"
            % (
                piece.key, wall_zmin, wall_zmax, face_cz, wall_y,
                placed.BoundBox.YMin, placed.BoundBox.YMax,
                placed.BoundBox.YMax - wall_y,
            )
        )
        return placed
    except Exception as exc:
        App.Console.PrintWarning("Emboss: side wall error: %s\n" % exc)
        return None


def _place_on_top(text_solid, piece, depth):
    """Place text on the +Z face of *piece*, readable from above.

    Looking down at the top surface the text reads normally.
    """
    try:
        pb = piece.shape.BoundBox
        face_cx = (pb.XMin + pb.XMax) / 2.0
        face_cy = (pb.YMin + pb.YMax) / 2.0
        face_z = pb.ZMax

        # No rotation: the text is already in XY, extruded along +Z.
        # Width +X (reads L-to-R looking down), height +Y.
        placed = text_solid.copy()
        rb = placed.BoundBox

        if not _fits(rb.XLength, rb.YLength, pb.XLength, pb.YLength, piece):
            return None

        dx = face_cx - (rb.XMin + rb.XMax) / 2.0
        dy = face_cy - (rb.YMin + rb.YMax) / 2.0

        # Position so *depth* mm of the tool sits inside the piece
        # (below the top surface) and the rest extends above.
        desired_zmax = face_z + (rb.ZLength - depth)
        dz = desired_zmax - rb.ZMax

        placed.translate(App.Vector(dx, dy, dz))

        App.Console.PrintMessage(
            "Emboss: top face, piece '%s'. Face Z=%.2f, "
            "tool Z: %.2f to %.2f (%.2f mm inside)\n"
            % (
                piece.key, face_z,
                placed.BoundBox.ZMin, placed.BoundBox.ZMax,
                face_z - placed.BoundBox.ZMin,
            )
        )
        return placed
    except Exception as exc:
        App.Console.PrintWarning("Emboss: top face error: %s\n" % exc)
        return None


def _place_on_bottom(text_solid, piece, depth):
    """Place text on the -Z face of *piece*, readable from below.

    Looking up at the bottom surface the text must read normally, so the
    X axis is mirrored (180 degree rotation around Y).
    """
    try:
        pb = piece.shape.BoundBox
        face_cx = (pb.XMin + pb.XMax) / 2.0
        face_cy = (pb.YMin + pb.YMax) / 2.0
        face_z = pb.ZMin

        # Rotate 180 degrees around Y:
        #   X flips (text mirrors so it reads correctly from below)
        #   Z flips (extrusion now runs -Z, toward the interior)
        placed = text_solid.copy()
        placed.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), 180)

        rb = placed.BoundBox

        if not _fits(rb.XLength, rb.YLength, pb.XLength, pb.YLength, piece):
            return None

        dx = face_cx - (rb.XMin + rb.XMax) / 2.0
        dy = face_cy - (rb.YMin + rb.YMax) / 2.0

        # After 180 deg Y the extrusion runs in -Z.  The tool's Z range
        # is roughly [-(thick), 0].  Position so *depth* mm sits inside
        # the piece (above the bottom face) and the rest hangs below.
        desired_zmax = face_z + depth
        dz = desired_zmax - rb.ZMax

        placed.translate(App.Vector(dx, dy, dz))

        App.Console.PrintMessage(
            "Emboss: bottom face, piece '%s'. Face Z=%.2f, "
            "tool Z: %.2f to %.2f (%.2f mm inside)\n"
            % (
                piece.key, face_z,
                placed.BoundBox.ZMin, placed.BoundBox.ZMax,
                placed.BoundBox.ZMax - face_z,
            )
        )
        return placed
    except Exception as exc:
        App.Console.PrintWarning("Emboss: bottom face error: %s\n" % exc)
        return None


def _place_on_inner(text_solid, piece, block_box, parting_height, depth):
    """Place text on the parting (inner) face of *piece*.

    The parting surface has the cavity opening in the centre, so the text
    is positioned near the -Y block wall where there is solid material
    between the block perimeter and the cavity/gutter channel.

    For the lower piece the text sits on the top face (at parting height),
    readable when looking down after opening the mold.  For the upper
    piece the text sits on the bottom face (also at parting height) and is
    rotated 180 degrees around Y so it reads correctly when the upper half
    is flipped over.
    """
    try:
        pb = piece.shape.BoundBox

        # Centre the text in X on the piece.
        face_cx = (pb.XMin + pb.XMax) / 2.0

        # Push the text toward the -Y block wall.  A 2 mm inset from the
        # block edge keeps the text fully on material without touching the
        # outer wall chamfer or fillet.
        wall_inset = 2.0
        target_ymin = block_box.YMin + wall_inset

        if piece.side == "below":
            # Lower piece: text on the top face at parting height.
            # No rotation needed, text reads L-to-R when looking down.
            face_z = parting_height
            placed = text_solid.copy()
            rb = placed.BoundBox

            if not _fits(rb.XLength, rb.YLength, pb.XLength, pb.YLength,
                         piece):
                return None

            dx = face_cx - (rb.XMin + rb.XMax) / 2.0
            # Align text bottom edge near the -Y wall
            dy = target_ymin - rb.YMin

            # Position so *depth* mm of tool sits inside (below parting Z)
            desired_zmax = face_z + (rb.ZLength - depth)
            dz = desired_zmax - rb.ZMax

            placed.translate(App.Vector(dx, dy, dz))

            App.Console.PrintMessage(
                "Emboss: inner (top) face, piece '%s'. "
                "Face Z=%.2f, tool Z: %.2f to %.2f (%.2f mm inside), "
                "tool Y: %.2f to %.2f\n"
                % (
                    piece.key, face_z,
                    placed.BoundBox.ZMin, placed.BoundBox.ZMax,
                    face_z - placed.BoundBox.ZMin,
                    placed.BoundBox.YMin, placed.BoundBox.YMax,
                )
            )
        else:
            # Upper piece: text on the bottom face at parting height.
            # Rotate 180 degrees around Y so text reads correctly when
            # the upper half is flipped open.
            face_z = parting_height
            placed = text_solid.copy()
            placed.rotate(App.Vector(0, 0, 0), App.Vector(0, 1, 0), 180)
            rb = placed.BoundBox

            if not _fits(rb.XLength, rb.YLength, pb.XLength, pb.YLength,
                         piece):
                return None

            dx = face_cx - (rb.XMin + rb.XMax) / 2.0
            # After 180 deg Y rotation, Y is unchanged but X is mirrored.
            # Align text bottom edge near the -Y wall.
            dy = target_ymin - rb.YMin

            # After 180 deg Y the extrusion runs -Z.  Position so *depth*
            # mm sits inside (above parting Z for the upper piece).
            desired_zmax = face_z + depth
            dz = desired_zmax - rb.ZMax

            placed.translate(App.Vector(dx, dy, dz))

            App.Console.PrintMessage(
                "Emboss: inner (bottom) face, piece '%s'. "
                "Face Z=%.2f, tool Z: %.2f to %.2f (%.2f mm inside), "
                "tool Y: %.2f to %.2f\n"
                % (
                    piece.key, face_z,
                    placed.BoundBox.ZMin, placed.BoundBox.ZMax,
                    placed.BoundBox.ZMax - face_z,
                    placed.BoundBox.YMin, placed.BoundBox.YMax,
                )
            )

        return placed
    except Exception as exc:
        App.Console.PrintWarning("Emboss: inner face error: %s\n" % exc)
        return None


def _fits(text_w, text_h, face_w, face_h, piece):
    """Check the text fits on the face with some margin."""
    available_w = face_w * 0.85
    available_h = face_h * 0.6
    if text_w > available_w or text_h > available_h:
        App.Console.PrintWarning(
            "Emboss: text too large for piece '%s'. "
            "Text: %.1f x %.1f mm, available: %.1f x %.1f mm\n"
            % (piece.key, text_w, text_h, available_w, available_h)
        )
        return False
    return True
