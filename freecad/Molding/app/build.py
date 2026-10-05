# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""The whole mould build, start to finish.

The order matters. Hollow the block before splitting it, because a boolean
against one large solid is more reliable than the same boolean run twice
against two pieces that share a face. Split next, so every feature after that
can ask which piece it landed in rather than being told. Features go on last,
outermost first, so a later cut can always win.

Every stage is wrapped: a mould that is missing its vents is still useful, a
build that raises on the way to the vents is not.

Lazy computation
~~~~~~~~~~~~~~~~

The build is split into two phases. Phase 1 (geometry) is the expensive part:
transforming the source shape, cutting the cavity, and splitting into pieces.
Phase 2 (features) cuts simple primitives into the already-split pieces and is
comparatively cheap.

Three levels of caching avoid redundant work:

1. **GeometryCache** (Phase 1 output): when only feature parameters change the
   geometry phase is skipped and the cached post-split pieces are restored.

2. **FeatureCache** (incremental Phase 2): piece snapshots are stored after
   each feature step. When a single feature parameter changes, pieces are
   restored from the step just before the change and only the remaining
   features re-run.

3. **FullBuildCache** (complete result): when nothing has changed at all the
   entire result is returned from cache in under 20 ms.
"""

import FreeCAD as App

from ..core import analysis, block as blockmod, split as splitmod
from ..core.features import fasteners, keys, pour
from ..core.frame import local_frame, to_global, to_local
from ..core.models import BLOCK_CYLINDER, LAYOUT_TWO


class BuildResult(object):
    def __init__(self):
        self.pieces = []
        self.warnings = []
        self.notes = []
        self.feature_points = []
        self.cavity_volume_ml = 0.0
        self.suggested_pour_ml = 0.0
        self.parting_height = 0.0
        self.frame = None
        self.section_wires = []

    def piece_shapes(self):
        return [(p.key, p.label, p.shape) for p in self.pieces]


# ---- Geometry cache (Phase 1 output) ----------------------------------------

# Parameters that define the geometry phase.  A change in any of these
# invalidates the cache and triggers a full rebuild; everything else is
# a feature parameter and only Phase 2 needs to re-run.
_GEOMETRY_PROPS = frozenset({
    "Source", "PullDirection", "Shrink", "PartingOffset",
    "BlockStyle", "WallThickness", "FloorThickness", "RoofThickness",
    "BlockFillet", "Layout", "SecondaryAngle",
})


def is_geometry_prop(prop_name):
    """True when *prop_name* affects the geometry phase of the build."""
    return prop_name in _GEOMETRY_PROPS


class GeometryCache(object):
    """Snapshot of the expensive Phase 1 results.

    Storing these lets Phase 2 restart from a clean copy of the post-split
    pieces without redoing the cavity boolean and the split, which together
    account for most of the build time.
    """

    __slots__ = ("key", "frame", "part", "parting_height", "style_is_cylinder",
                 "block_box", "wires", "pieces", "warnings")

    def __init__(self, key, frame, part, parting_height, style_is_cylinder,
                 block_box, wires, pieces, warnings):
        self.key = key
        self.frame = frame
        self.part = part
        self.parting_height = parting_height
        self.style_is_cylinder = style_is_cylinder
        self.block_box = block_box
        self.wires = wires
        # Store a frozen snapshot: each piece's shape is copied so later
        # feature cuts on the working set do not corrupt the cache.
        self.pieces = [
            (p.key, p.label, p.shape.copy(), p.side, p.split_normal)
            for p in pieces
        ]
        self.warnings = list(warnings)

    def restore_pieces(self):
        """Return fresh ``Piece`` objects whose shapes are independent copies."""
        return [
            splitmod.Piece(key, label, shape.copy(), side, sn)
            for key, label, shape, side, sn in self.pieces
        ]


def _geometry_key(job):
    """Hashable key for the geometry parameters of a job.

    The source shape is fingerprinted by its bounding box, volume, and
    face count. This is not a perfect hash but it catches every practical
    change (modifying the source, switching to a different body) without
    the cost of hashing the entire BRep.
    """
    source = getattr(job, "Source", None)
    if source is None or not hasattr(source, "Shape") or source.Shape.isNull():
        return None
    shape = source.Shape
    bb = shape.BoundBox
    shape_fp = (
        round(bb.XMin, 6), round(bb.YMin, 6), round(bb.ZMin, 6),
        round(bb.XMax, 6), round(bb.YMax, 6), round(bb.ZMax, 6),
        round(shape.Volume, 6), len(shape.Faces),
    )
    return (
        shape_fp,
        (round(job.PullDirection.x, 9),
         round(job.PullDirection.y, 9),
         round(job.PullDirection.z, 9)),
        round(float(job.Shrink), 9),
        round(float(job.PartingOffset), 9),
        str(job.BlockStyle),
        round(float(job.WallThickness), 9),
        round(float(job.FloorThickness), 9),
        round(float(job.RoofThickness), 9),
        round(float(job.BlockFillet), 9),
        str(job.Layout),
        round(float(job.SecondaryAngle), 9),
    )


# ---- Feature keys (Phase 2 parameters) --------------------------------------

def _feature_key(job):
    """Flat hashable key covering every parameter that affects Phase 2.

    Used by ``FullBuildCache`` to detect identical rebuilds.
    """
    # Piggy-back on the group keys and add OverpourPercent at the end.
    return _feature_group_keys(job) + (round(float(job.OverpourPercent), 9),)


# Feature group order: features are applied in this sequence.
_FEATURE_GROUP_NAMES = (
    "gutter", "keys", "injection", "pour",
    "vents", "bolts", "pry", "emboss",
)


def _feature_group_keys(job):
    """Per-feature-group parameter keys, returned as a tuple of tuples.

    Element *i* fingerprints the parameters for ``_FEATURE_GROUP_NAMES[i]``.
    The incremental ``FeatureCache`` uses these to find how far into Phase 2
    the previous build's results can be reused.
    """
    def _vt(v):
        return (round(v.x, 6), round(v.y, 6), round(v.z, 6))

    def _vl(prop_name):
        try:
            raw = list(getattr(job, prop_name, []))
            return tuple(_vt(App.Vector(v)) for v in raw) if raw else ()
        except Exception:
            return ()

    uck = bool(getattr(job, "UseCustomKeyPositions", False))
    uci = bool(getattr(job, "UseCustomInjectionPos", False))
    ucv = bool(getattr(job, "UseCustomVentPositions", False))
    ucb = bool(getattr(job, "UseCustomBoltPositions", False))

    gutter = (
        bool(job.Gutter),
        round(float(job.GutterWidth), 9),
        round(float(job.GutterDepth), 9),
        round(float(job.GutterGap), 9),
        str(job.GutterSide),
        int(job.GutterReliefs),
    )

    reg_keys = (
        bool(job.RegistrationKeys),
        round(float(job.KeyDiameter), 9),
        round(float(job.KeyHeight), 9),
        round(float(job.KeyClearance), 9),
        round(float(job.KeyInset), 9),
        uck,
        _vl("KeyPositions") if uck else (),
    )

    inj = (
        bool(getattr(job, "InjectionPort", False)),
        str(getattr(job, "InjectionStyle", "")),
        str(getattr(job, "SyringeSize", "")),
        round(float(getattr(job, "InjectionDiameter", 3.0)), 9),
        round(float(getattr(job, "InjectionChannelLength", 5.0)), 9),
        uci,
        _vt(App.Vector(job.InjectionPosition)) if uci else (),
    )

    pour_k = (
        bool(getattr(job, "PourPort", False)),
        round(float(job.PourDiameter), 9),
        round(float(job.FunnelDiameter), 9),
        round(float(job.FunnelDepth), 9),
    )

    vents = (
        int(job.VentCount),
        round(float(job.VentDiameter), 9),
        str(getattr(job, "VentShape", "")),
        str(getattr(job, "VentDirection", "")),
        round(float(getattr(job, "VentWidth", 2.0)), 9),
        round(float(getattr(job, "VentLength", 4.0)), 9),
        ucv,
        _vl("VentPositions") if ucv else (),
    )

    bolts = (
        bool(getattr(job, "Bolts", False)),
        int(getattr(job, "BoltCount", 4)),
        str(job.BoltSize),
        round(float(job.BoltInset), 9),
        round(float(job.BoltClearance), 9),
        bool(job.Counterbore),
        bool(job.NutTrap),
        ucb,
        _vl("BoltPositions") if ucb else (),
    )

    pry = (
        bool(job.PrySlots),
        round(float(job.PrySlotWidth), 9),
        round(float(job.PrySlotDepth), 9),
    )

    emboss = (
        bool(getattr(job, "Emboss", False)),
        str(getattr(job, "EmbossText", "")),
        round(float(getattr(job, "EmbossFontSize", 5.0)), 9),
        round(float(getattr(job, "EmbossDepth", 0.8)), 9),
        str(getattr(job, "EmbossPlacement", "")),
    )

    return (gutter, reg_keys, inj, pour_k, vents, bolts, pry, emboss)


# ---- Full build cache (Phase 1 + Phase 2 output) ----------------------------

class FullBuildCache(object):
    """Snapshot of the entire build result, both phases.

    When neither geometry nor feature parameters have changed, the build
    can skip all boolean operations and return a deep copy of the cached
    pieces instantly.  This turns a ~18 second rebuild into < 0.2 seconds.
    """

    __slots__ = ("geo_key", "feat_key", "pieces", "warnings", "notes",
                 "feature_points", "cavity_volume_ml", "suggested_pour_ml",
                 "parting_height", "frame", "section_wires")

    def __init__(self, geo_key, feat_key, result):
        self.geo_key = geo_key
        self.feat_key = feat_key
        # Deep-copy piece shapes so the cache stays independent of later edits
        self.pieces = [
            (p.key, p.label, p.shape.copy(), p.side, p.split_normal)
            for p in result.pieces
        ]
        self.warnings = list(result.warnings)
        self.notes = list(result.notes)
        self.feature_points = [dict(fp) for fp in result.feature_points]
        self.cavity_volume_ml = result.cavity_volume_ml
        self.suggested_pour_ml = result.suggested_pour_ml
        self.parting_height = result.parting_height
        self.frame = result.frame
        self.section_wires = list(result.section_wires)

    def restore(self):
        """Return a fresh ``BuildResult`` with independent shape copies."""
        r = BuildResult()
        r.pieces = [
            splitmod.Piece(key, label, shape.copy(), side, sn)
            for key, label, shape, side, sn in self.pieces
        ]
        r.warnings = list(self.warnings)
        r.notes = list(self.notes)
        r.feature_points = [dict(fp) for fp in self.feature_points]
        r.cavity_volume_ml = self.cavity_volume_ml
        r.suggested_pour_ml = self.suggested_pour_ml
        r.parting_height = self.parting_height
        r.frame = self.frame
        r.section_wires = list(self.section_wires)
        return r


# ---- Incremental feature cache (per-step Phase 2 snapshots) -----------------

class _FeatureSnapshot(object):
    """Piece and result state captured after one feature step."""

    __slots__ = ("cumulative_key", "pieces", "feature_points",
                 "notes", "warnings")

    def __init__(self, cumulative_key, pieces, result):
        self.cumulative_key = cumulative_key
        self.pieces = [
            (p.key, p.label, p.shape.copy(), p.side, p.split_normal)
            for p in pieces
        ]
        self.feature_points = [dict(fp) for fp in result.feature_points]
        self.notes = list(result.notes)
        self.warnings = list(result.warnings)

    def restore_pieces(self):
        return [
            splitmod.Piece(k, l, s.copy(), side, sn)
            for k, l, s, side, sn in self.pieces
        ]


class FeatureCache(object):
    """Incremental cache for Phase 2.

    Stores a ``_FeatureSnapshot`` after each feature step, keyed by the
    cumulative group-key tuple up to that step. When a feature parameter
    changes, the latest step whose cumulative key still matches is
    restored and only the remaining features re-run.
    """

    __slots__ = ("geo_key", "steps")

    def __init__(self, geo_key):
        self.geo_key = geo_key
        # Indexed by step number (0..7). ``None`` for steps not yet stored.
        self.steps = [None] * len(_FEATURE_GROUP_NAMES)

    def save(self, step_idx, cumulative_key, pieces, result):
        self.steps[step_idx] = _FeatureSnapshot(cumulative_key, pieces, result)

    def find_restore_point(self, geo_key, group_keys):
        """Return ``(step_index, snapshot)`` for the latest usable step.

        Returns ``(-1, None)`` when no cached step matches.
        """
        if self.geo_key != geo_key:
            return -1, None
        best = -1
        best_snap = None
        for i, snap in enumerate(self.steps):
            if snap is None:
                break
            if snap.cumulative_key == group_keys[:i + 1]:
                best = i
                best_snap = snap
            else:
                # Keys diverge here; everything after is invalid.
                break
        return best, best_snap


# ---- The build --------------------------------------------------------------

def build(job, progress_fn=None, cache=None, full_cache=None, feat_cache=None):
    """Run the pipeline for a MoldJob document object.

    *progress_fn*, when provided, is called with ``(step_number, total_steps,
    description)`` before each major stage so the GUI can update a progress
    bar.  Disabled features are skipped entirely so the bar only counts
    stages that do real work.

    *cache*, when provided, is a ``GeometryCache`` from a previous build.
    If the geometry parameters have not changed the expensive Phase 1 is
    skipped and the cached post-split pieces are restored instead.

    *full_cache*, when provided, is a ``FullBuildCache`` from a previous
    build.  If neither geometry nor feature parameters have changed, the
    entire build is skipped and the cached result is returned instantly.

    *feat_cache*, when provided, is a ``FeatureCache`` from a previous
    build.  When the geometry is unchanged but a feature parameter changed,
    pieces are restored from the latest unaffected feature step and only
    the remaining features re-run.

    The caller should store the returned
    ``(result, new_geo_cache, new_full_cache, new_feat_cache)`` for the
    next invocation.
    """
    import time as _time
    _build_t0 = _time.perf_counter()

    result = BuildResult()

    source = getattr(job, "Source", None)
    if source is None or not hasattr(source, "Shape") or source.Shape.isNull():
        result.warnings.append("No source part is set on this job.")
        return result, None, None, None

    # ---- Compute all cache keys up front ----
    geo_key = _geometry_key(job)
    feat_key = _feature_key(job)
    group_keys = _feature_group_keys(job)

    # ---- Full result cache: skip everything when nothing changed ----
    if (full_cache is not None
            and geo_key is not None
            and full_cache.geo_key == geo_key
            and full_cache.feat_key == feat_key):
        _t0 = _time.perf_counter()
        restored = full_cache.restore()
        _t1 = _time.perf_counter()
        App.Console.PrintMessage(
            "[Molding] Full cache hit: restored complete result in %.3f s "
            "(skipped all geometry + features).\n" % (_t1 - _t0,)
        )
        if progress_fn is not None:
            progress_fn(1, 1, "Using cached result")
        return restored, cache, full_cache, feat_cache

    # Pre-check which optional features are enabled so the progress bar
    # only counts steps that will actually do work.
    has_gutter = bool(job.Gutter)
    has_keys = bool(job.RegistrationKeys)
    has_injection = bool(getattr(job, "InjectionPort", False))
    has_pour = bool(getattr(job, "PourPort", False))
    has_vents = int(job.VentCount) > 0
    has_bolts = bool(getattr(job, "Bolts", False))
    has_pry = bool(job.PrySlots)
    has_emboss = bool(getattr(job, "Emboss", False))
    _has = [has_gutter, has_keys, has_injection, has_pour,
            has_vents, has_bolts, has_pry, has_emboss]

    # Decide whether the geometry cache is still valid.
    cache_hit = (
        cache is not None
        and geo_key is not None
        and cache.key == geo_key
    )

    # ---- Incremental feature cache: find the latest reusable step ----
    _skip_to = -1
    _skip_snap = None
    if feat_cache is not None and geo_key is not None:
        _skip_to, _skip_snap = feat_cache.find_restore_point(geo_key, group_keys)

    # Count steps, excluding features that will be restored from cache.
    geo_steps = 1 if cache_hit else 4
    feat_steps = sum(1 for i, h in enumerate(_has) if h and _skip_to < i)
    if _skip_to >= 0:
        feat_steps += 1  # one step for "Restoring cached features"
    n_steps = geo_steps + feat_steps + 1  # +1 for "Finishing up"

    last_step = n_steps - 1
    step = [0]

    def _step(text):
        if progress_fn is not None:
            progress_fn(step[0], last_step, text)
        step[0] += 1

    # ---- Phase 1: geometry (expensive) or cache restore (cheap) ----
    _phase1_t0 = _time.perf_counter()

    if cache_hit:
        _step("Restoring cached geometry…")
        frame = cache.frame
        part = cache.part
        parting_height = cache.parting_height
        style_is_cylinder = cache.style_is_cylinder
        block_box = cache.block_box
        wires = cache.wires
        pieces = cache.restore_pieces()
        result.warnings.extend(cache.warnings)
        new_cache = cache  # unchanged
    else:
        geo_warnings = []

        _step("Preparing geometry…")

        frame = local_frame(job.PullDirection)

        part = to_local(source.Shape, frame)
        part = blockmod.apply_shrink(part, job.Shrink)
        if not part.Solids:
            geo_warnings.append(
                "The source is not a solid. Booleans need a closed solid, so the "
                "cavity may come out wrong."
            )

        part_box = part.BoundBox
        parting_height = part_box.ZMin + float(job.PartingOffset)
        parting_height = min(max(parting_height, part_box.ZMin + 1e-3), part_box.ZMax - 1e-3)

        _step("Building mold block…")

        style_is_cylinder = job.BlockStyle == BLOCK_CYLINDER
        body = blockmod.make_block(
            part_box,
            job.BlockStyle,
            float(job.WallThickness),
            float(job.FloorThickness),
            float(job.RoofThickness),
            float(job.BlockFillet),
        )
        block_box = body.BoundBox

        _step("Cutting cavity…")

        try:
            hollow = body.cut(part)
        except Exception as exc:
            result.warnings.append("Could not cut the cavity out of the block: %s" % exc)
            return result, None, None, None

        wires = analysis.section_wires(part, parting_height)
        if not wires:
            geo_warnings.append(
                "The parting plane does not cut the part at this height, so there "
                "is no cavity opening and no gutter."
            )

        _step("Splitting at parting line…")

        pieces = splitmod.split_block(
            hollow, parting_height, job.Layout, float(job.SecondaryAngle), block_box
        )
        if len(pieces) < 2:
            geo_warnings.append("The parting plane did not divide the block into pieces.")

        result.warnings.extend(geo_warnings)

        # Build a new cache for next time.
        new_cache = GeometryCache(
            geo_key, frame, part, parting_height, style_is_cylinder,
            block_box, wires, pieces, geo_warnings,
        )

    _phase1_t1 = _time.perf_counter()
    App.Console.PrintMessage(
        "[Molding:DEBUG] Phase 1 (geometry) took %.3f s  (cache_hit=%s)\n"
        % (_phase1_t1 - _phase1_t0, cache_hit)
    )

    result.frame = frame
    result.parting_height = parting_height
    result.section_wires = wires

    # ---- Feature cache restore ----
    if _skip_to >= 0 and _skip_snap is not None:
        _step("Restoring cached features…")
        pieces = _skip_snap.restore_pieces()
        result.feature_points = [dict(fp) for fp in _skip_snap.feature_points]
        result.notes = list(_skip_snap.notes)
        result.warnings = list(_skip_snap.warnings)
        skipped = [_FEATURE_GROUP_NAMES[i] for i in range(_skip_to + 1) if _has[i]]
        App.Console.PrintMessage(
            "[Molding] Feature cache: restored through step %d (%s), "
            "skipping %s.\n"
            % (_skip_to, _FEATURE_GROUP_NAMES[_skip_to],
               ", ".join(skipped) if skipped else "nothing")
        )

    _new_fc = FeatureCache(geo_key)
    # Carry forward cached steps that were skipped (not re-run) so the next
    # build can still restore from them.
    if feat_cache is not None and _skip_to >= 0:
        for _ci in range(_skip_to + 1):
            _new_fc.steps[_ci] = feat_cache.steps[_ci]
    _phase2_t0 = _time.perf_counter()

    # ---- Feature 0: Gutter ----
    _fidx = 0
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_gutter:
            _step("Adding overflow gutter…")
            _guard(
                result,
                "overflow gutter",
                pour.add_gutter,
                pieces,
                wires,
                parting_height,
                float(job.GutterWidth),
                float(job.GutterDepth),
                float(job.GutterGap),
                job.GutterSide,
                int(job.GutterReliefs),
            )
            if wires:
                wb = wires[0].BoundBox
                gap = float(job.GutterGap)
                gw = float(job.GutterWidth)
                result.feature_points.append({
                    "name": "Gutter",
                    "type": "gutter",
                    "point": App.Vector(
                        wb.XMax + gap + gw / 2.0,
                        (wb.YMin + wb.YMax) / 2.0,
                        parting_height,
                    ),
                })
        _ft1 = _time.perf_counter()
        if has_gutter:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   gutter: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 1: Registration keys ----
    _fidx = 1
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_keys:
            _step("Adding registration keys…")
            params = {
                "diameter": float(job.KeyDiameter),
                "height": float(job.KeyHeight),
                "clearance": float(job.KeyClearance),
            }
            placed = 0
            key_positions = []

            custom_key_pos = None
            if getattr(job, "UseCustomKeyPositions", False):
                try:
                    raw = list(job.KeyPositions)
                    if raw:
                        custom_key_pos = [
                            frame.inverse().multVec(App.Vector(v))
                            for v in raw
                        ]
                except Exception:
                    pass

            if custom_key_pos is not None:
                # User-picked positions: place keys at each point
                for point in custom_key_pos:
                    point_at_parting = App.Vector(point.x, point.y, parting_height)
                    if keys.add_key_pair(pieces, point_at_parting, App.Vector(0, 0, 1), params, part):
                        key_positions.append(point_at_parting)
                        placed += 1
            else:
                # Automatic placement: generate a dense ring of candidates and
                # greedily try each one, same strategy as bolts.
                margin = float(job.GutterWidth) + float(job.GutterGap) if job.Gutter else 1.0
                key_r = float(job.KeyDiameter) / 2.0
                target_count = 4

                candidates = keys.key_candidates(
                    block_box, parting_height, float(job.KeyInset),
                    style_is_cylinder, density=max(target_count * 8, 32),
                )
                # Pre-filter: drop candidates too close to the cavity opening
                candidates = [
                    p for p in candidates
                    if not _too_close(wires, p, key_r + margin)
                ]

                # Collect existing feature positions for soft avoidance
                feature_xy = [
                    App.Vector(fp["point"].x, fp["point"].y, parting_height)
                    for fp in result.feature_points
                ]
                min_feat_dist = key_r * 2.0 + 2.0

                # Greedy try-and-place: score candidates by spacing and feature
                # distance, then attempt add_key_pair at the best; skip on
                # failure and try the next best.
                ranked = keys.select_key_positions(
                    candidates, len(candidates),
                    feature_xy, min_feat_dist,
                )
                for point in ranked:
                    if placed >= target_count:
                        break
                    if keys.add_key_pair(pieces, point, App.Vector(0, 0, 1), params, part):
                        key_positions.append(point)
                        placed += 1
                if job.Layout != LAYOUT_TWO:
                    for piece in list(pieces):
                        if piece.split_normal is None:
                            continue
                        for point in keys.secondary_key_points(piece, float(job.KeyInset)):
                            if keys.add_key_pair(pieces, point, piece.split_normal, params, part):
                                key_positions.append(point)
                                placed += 1

            result.notes.append("Registration keys placed: %d" % placed)
            if placed == 0:
                result.warnings.append(
                    "No registration keys fitted. Reduce the key inset or diameter, "
                    "or add wall thickness."
                )
            for kp in key_positions:
                result.feature_points.append({
                    "name": "Reg. Key",
                    "type": "key",
                    "point": kp,
                })
        _ft1 = _time.perf_counter()
        if has_keys:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   keys: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 2: Injection port ----
    _fidx = 2
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_injection:
            _step("Adding injection port…")
            from ..core.features import injection

            custom_inj = None
            if getattr(job, "UseCustomInjectionPos", False):
                try:
                    gp = App.Vector(job.InjectionPosition)
                    custom_inj = frame.inverse().multVec(gp)
                except Exception:
                    pass

            inj_style = getattr(job, "InjectionStyle", "Luer Lock")

            inj_centre = _guard(
                result,
                "injection port",
                injection.add_injection_port,
                pieces,
                wires,
                parting_height,
                block_box.ZMax,
                getattr(job, "SyringeSize", "10 mL"),
                float(getattr(job, "InjectionDiameter", 3.0)),
                float(getattr(job, "InjectionChannelLength", 5.0)),
                custom_inj,
                inj_style,
            )
            if inj_centre is not None:
                result.feature_points.append({
                    "name": "Injection Port",
                    "type": "injection",
                    "point": App.Vector(inj_centre.x, inj_centre.y, block_box.ZMax),
                })
        _ft1 = _time.perf_counter()
        if has_injection:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   injection: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 3: Pour port (legacy) ----
    _fidx = 3
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_pour:
            _step("Adding pour port…")
            _guard(
                result,
                "pour port",
                pour.add_pour_port,
                pieces,
                wires,
                parting_height,
                block_box.ZMax,
                float(job.PourDiameter),
                float(job.FunnelDiameter),
                float(job.FunnelDepth),
            )
        _ft1 = _time.perf_counter()
        if has_pour:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   pour: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 4: Vents ----
    _fidx = 4
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_vents:
            _step("Adding vents…")
            custom_vent_pos = None
            if getattr(job, "UseCustomVentPositions", False):
                try:
                    raw = list(job.VentPositions)
                    if raw:
                        custom_vent_pos = [
                            frame.inverse().multVec(App.Vector(v))
                            for v in raw
                        ]
                except Exception:
                    pass

            vent_result = _guard(
                result,
                "vents",
                pour.add_vents,
                pieces,
                wires,
                parting_height,
                block_box.ZMax,
                int(job.VentCount),
                float(job.VentDiameter),
                getattr(job, "VentShape", "Cylinder"),
                float(getattr(job, "VentWidth", 2.0)),
                float(getattr(job, "VentLength", 4.0)),
                custom_vent_pos,
                str(getattr(job, "VentDirection", "Up")),
                block_box,
            )
            if vent_result is not None:
                vent_count, vent_positions = vent_result
                for vp in vent_positions:
                    result.feature_points.append({
                        "name": "Vent",
                        "type": "vent",
                        "point": vp,
                    })
        _ft1 = _time.perf_counter()
        if has_vents:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   vents: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 5: Bolts ----
    _fidx = 5
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_bolts:
            _step("Adding bolt holes…")
            requested = int(getattr(job, "BoltCount", 4))
            bolt_spec = fasteners.BOLTS.get(job.BoltSize, {})
            bolt_hole_r = (bolt_spec.get("clearance", 4.5) + float(job.BoltClearance)) / 2.0

            custom_bolt_pos = None
            if getattr(job, "UseCustomBoltPositions", False):
                try:
                    raw = list(job.BoltPositions)
                    if raw:
                        custom_bolt_pos = [
                            frame.inverse().multVec(App.Vector(v))
                            for v in raw
                        ]
                except Exception:
                    pass

            if custom_bolt_pos is not None:
                # User-picked positions: use directly (XY at parting height)
                points = [
                    App.Vector(p.x, p.y, parting_height)
                    for p in custom_bolt_pos
                ]
            else:
                # Automatic placement: generate candidates and select evenly
                candidates = fasteners.bolt_candidates(
                    block_box, float(job.BoltInset), style_is_cylinder,
                    parting_height, density=max(requested * 6, 24),
                )
                candidates = [
                    p for p in candidates
                    if not _bolt_hits_part(part, p, bolt_hole_r, block_box)
                ]

                # Collect existing feature positions (XY only) for avoidance
                feature_xy = [
                    App.Vector(fp["point"].x, fp["point"].y, parting_height)
                    for fp in result.feature_points
                ]
                min_feat_dist = bolt_hole_r * 2.0 + 2.0  # bolt radius + structural margin

                points = fasteners.select_bolt_positions(
                    candidates, requested, feature_xy, min_feat_dist,
                )

            _guard(
                result,
                "bolt holes",
                fasteners.add_bolts,
                pieces,
                points,
                block_box.ZMin,
                block_box.ZMax,
                job.BoltSize,
                float(job.BoltClearance),
                bool(job.Counterbore),
                bool(job.NutTrap),
            )
            placed = len(points)
            if custom_bolt_pos is None and placed < requested:
                result.warnings.append(
                    "Only %d of %d bolt(s) placed; the rest were too close to "
                    "the cavity or other features. Increase the wall thickness "
                    "or reduce the bolt count." % (placed, requested)
                )
            result.notes.append("Bolts placed: %d" % placed)
            for bp in points:
                result.feature_points.append({
                    "name": "Bolt",
                    "type": "bolt",
                    "point": App.Vector(bp.x, bp.y, parting_height),
                })
        _ft1 = _time.perf_counter()
        if has_bolts:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   bolts: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 6: Pry slots ----
    _fidx = 6
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_pry:
            _step("Adding pry slots…")
            _guard(
                result,
                "pry slots",
                fasteners.add_pry_slots,
                pieces,
                block_box,
                parting_height,
                float(job.PrySlotWidth),
                float(job.PrySlotDepth),
            )
            # Pry slots sit on opposite sides of the block at the parting height
            cx = (block_box.XMin + block_box.XMax) / 2.0
            cy = (block_box.YMin + block_box.YMax) / 2.0
            reach = max(block_box.XMax - cx, block_box.YMax - cy)
            result.feature_points.append({
                "name": "Pry Slot",
                "type": "pry",
                "point": App.Vector(cx + reach, cy, parting_height),
            })
            result.feature_points.append({
                "name": "Pry Slot",
                "type": "pry",
                "point": App.Vector(cx - reach, cy, parting_height),
            })
        _ft1 = _time.perf_counter()
        if has_pry:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   pry: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Feature 7: Embossment ----
    _fidx = 7
    if _skip_to < _fidx:
        _ft0 = _time.perf_counter()
        if has_emboss:
            _step("Adding embossment…")
            from ..core.features import emboss

            _guard(
                result,
                "text embossment",
                emboss.add_emboss,
                pieces,
                block_box,
                parting_height,
                str(getattr(job, "EmbossText", "")),
                float(getattr(job, "EmbossFontSize", 5.0)),
                float(getattr(job, "EmbossDepth", 0.8)),
                str(getattr(job, "EmbossPlacement", "Side wall")),
            )
        _ft1 = _time.perf_counter()
        if has_emboss:
            App.Console.PrintMessage(
                "[Molding:DEBUG]   emboss: %.3f s\n" % (_ft1 - _ft0,))
        _new_fc.save(_fidx, group_keys[:_fidx + 1], pieces, result)

    # ---- Finishing ----
    _step("Finishing up…")

    _phase2_t1 = _time.perf_counter()
    App.Console.PrintMessage(
        "[Molding:DEBUG] Phase 2 (features) took %.3f s\n"
        % (_phase2_t1 - _phase2_t0,)
    )

    _measure(result, part, parting_height, float(job.OverpourPercent))

    # Transform feature label points from local frame back to global coords
    for fp in result.feature_points:
        fp["point"] = frame.multVec(fp["point"])

    for piece in pieces:
        piece.shape = to_global(piece.shape, frame)
    result.pieces = pieces

    _build_t1 = _time.perf_counter()
    App.Console.PrintMessage(
        "[Molding:DEBUG] Total build() took %.3f s\n" % (_build_t1 - _build_t0,)
    )

    # Build the full result cache for next time.
    new_full_cache = FullBuildCache(geo_key, feat_key, result)
    return result, new_cache, new_full_cache, _new_fc


def _measure(result, part, parting_height, overpour):
    volume = analysis.volume_ml(part)
    result.cavity_volume_ml = volume
    result.suggested_pour_ml = volume * (1.0 + overpour / 100.0)

    for direction, name in ((1, "upper"), (-1, "lower")):
        report = analysis.undercut_report(part, parting_height, direction)
        if report["count"]:
            result.notes.append(
                "%d face(s) undercut the %s half (worst %.1f degrees). "
                "Silicone will flex off these; only a concern for rigid moulds."
                % (report["count"], name, report["worst_angle"])
            )
        draft = analysis.draft_report(part, parting_height, direction)
        if draft is not None and draft < 1.0:
            result.notes.append(
                "Minimum draft on the %s half is %.1f degrees, so expect it to "
                "grip." % (name, draft)
            )


def _bolt_hits_part(part_shape, point, hole_radius, block_box):
    """True when a vertical bolt hole at *point* would break into the part."""
    if not part_shape.Solids:
        return False
    try:
        import Part

        margin = hole_radius + 0.5  # half-mm structural wall minimum
        vertex = Part.Vertex(App.Vector(point.x, point.y, point.z))
        dist = vertex.distToShape(part_shape)[0]
        return dist < margin
    except Exception:
        return False


def _too_close(wires, point, margin):
    """Keep features off the cavity opening."""
    if not wires:
        return False
    try:
        import Part

        vertex = Part.Vertex(App.Vector(point.x, point.y, wires[0].BoundBox.ZMin))
        for wire in wires:
            if vertex.distToShape(wire)[0] < margin:
                return True
            face = analysis.wire_face(wire)
            if face is not None and face.isInside(vertex.Point, 1e-3, True):
                return True
    except Exception:
        return False
    return False


def _guard(result, label, function, *args):
    try:
        return function(*args)
    except Exception as exc:
        result.warnings.append("Skipped the %s: %s" % (label, exc))
        return None
