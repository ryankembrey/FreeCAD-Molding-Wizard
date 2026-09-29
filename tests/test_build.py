# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileNotice: Part of the Molding addon for FreeCAD.

"""Build checks that need a real OCC kernel, so they run inside FreeCAD.

Run them with the console build:

    freecadcmd tests/test_build.py

They make their own test parts, so nothing has to be modelled first.
"""

import os
import sys
import unittest

import FreeCAD as App
import Part

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))

from freecad.Molding.app import build as buildmod  # noqa: E402
from freecad.Molding.app import mold_job  # noqa: E402
from freecad.Molding.core import analysis  # noqa: E402
from freecad.Molding.core.frame import local_frame, to_local  # noqa: E402
from freecad.Molding.core.models import (  # noqa: E402
    LAYOUT_THREE_TOP,
    LAYOUT_TWO,
)


def make_document(name="MoldTests"):
    if name in App.listDocuments():
        App.closeDocument(name)
    return App.newDocument(name)


def add_solid(document, shape, label):
    obj = document.addObject("Part::Feature", "Src")
    obj.Label = label
    obj.Shape = shape
    document.recompute()
    return obj


class TestAnalysis(unittest.TestCase):
    def setUp(self):
        self.document = make_document()

    def tearDown(self):
        App.closeDocument(self.document.Name)

    def test_widest_section_of_a_sphere_is_the_equator(self):
        sphere = Part.makeSphere(20.0)
        local = to_local(sphere, local_frame(App.Vector(0, 0, 1)))
        height = analysis.suggest_parting_height(local)
        # The equator sits at the centre, within one sampling step.
        self.assertLess(abs(height), local.BoundBox.ZLength / 32.0)

    def test_section_area_matches_the_circle(self):
        cylinder = Part.makeCylinder(10.0, 30.0)
        area = analysis.section_area(cylinder, 15.0)
        self.assertAlmostEqual(area, 3.14159265 * 100.0, delta=1.0)

    def test_volume_in_millilitres(self):
        box = Part.makeBox(100.0, 100.0, 100.0)  # one litre
        self.assertAlmostEqual(analysis.volume_ml(box), 1000.0, delta=0.1)

    def test_a_sphere_undercuts_nothing_at_its_equator(self):
        sphere = Part.makeSphere(20.0)
        report = analysis.undercut_report(sphere, 0.0, 1)
        self.assertEqual(report["count"], 0)


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.document = make_document()
        self.source = add_solid(self.document, Part.makeSphere(15.0), "Ball")
        self.job = mold_job.create(self.document, self.source)
        self.job.AutoUpdate = False

    def tearDown(self):
        App.closeDocument(self.document.Name)

    def build(self):
        result = mold_job.rebuild(self.job)
        self.assertIsNotNone(result)
        return result

    def test_two_pieces_come_out_of_a_two_piece_layout(self):
        self.job.Layout = LAYOUT_TWO
        self.job.PartingOffset = 15.0
        result = self.build()
        self.assertEqual(len(result.pieces), 2)
        for piece in result.pieces:
            self.assertGreater(piece.shape.Volume, 0.0)

    def test_three_piece_layout_splits_the_top(self):
        self.job.Layout = LAYOUT_THREE_TOP
        self.job.PartingOffset = 15.0
        result = self.build()
        self.assertEqual(len(result.pieces), 3)
        above = [p for p in result.pieces if p.side == "above"]
        self.assertEqual(len(above), 2)

    def test_the_pieces_add_up_to_less_than_the_solid_block(self):
        """The cavity, gutter and slots all remove material, so the sum of the
        pieces has to come in under a block of the same outside size."""
        self.job.PartingOffset = 15.0
        result = self.build()
        total = sum(p.shape.Volume for p in result.pieces)
        box = self.source.Shape.BoundBox
        outer = (
            (box.XLength + 2 * float(self.job.WallThickness))
            * (box.YLength + 2 * float(self.job.WallThickness))
            * (box.ZLength + float(self.job.FloorThickness) + float(self.job.RoofThickness))
        )
        self.assertLess(total, outer)
        self.assertGreater(total, outer * 0.4)

    def test_cavity_volume_is_reported_in_millilitres(self):
        self.build()
        expected = self.source.Shape.Volume / 1000.0
        self.assertAlmostEqual(self.job.CavityVolume, expected, delta=0.05)
        self.assertGreater(self.job.SuggestedPour, self.job.CavityVolume)

    def test_child_pieces_appear_in_the_group(self):
        self.build()
        self.assertEqual(len(self.job.Group), 2)
        for child in self.job.Group:
            self.assertTrue(child.PieceKey)
            self.assertIn(child.PieceSide, ("above", "below"))

    def test_a_sideways_pull_direction_still_builds(self):
        self.job.PullDirection = App.Vector(1, 0, 0)
        self.job.PartingOffset = 15.0
        result = self.build()
        self.assertEqual(len(result.pieces), 2)
        self.assertFalse([w for w in result.warnings if "did not divide" in w])

    def test_shrink_grows_the_cavity(self):
        self.job.PartingOffset = 15.0
        self.build()
        plain = self.job.CavityVolume
        self.job.Shrink = 2.0
        self.build()
        self.assertGreater(self.job.CavityVolume, plain)

    def test_missing_source_is_reported_not_raised(self):
        self.job.Source = None
        result = buildmod.build(self.job)
        self.assertEqual(result.pieces, [])
        self.assertTrue(result.warnings)


class TestExport(unittest.TestCase):
    def setUp(self):
        self.document = make_document()
        self.source = add_solid(self.document, Part.makeSphere(12.0), "Ball")
        self.job = mold_job.create(self.document, self.source)
        self.job.PartingOffset = 12.0
        mold_job.rebuild(self.job)

    def tearDown(self):
        App.closeDocument(self.document.Name)

    def test_pieces_land_on_the_bed(self):
        from freecad.Molding.app import export as exportmod

        for child in self.job.Group:
            oriented = exportmod.orient_for_print(
                child.Shape, child.PieceSide, self.job.PullDirection
            )
            self.assertAlmostEqual(oriented.BoundBox.ZMin, 0.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main(exit=False, verbosity=2)
