"""Reveal-bundle leaks must abort. The world in these fixtures is invented."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reveal
from PIL import Image

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Synthetic 32 by 16 map. x < 16 is the hidden rectangle used as the mask.
HIDDEN = reveal.hidden_mask_contains(reveal.rect_mask(32, 16, [(0, 0, 16, 16)]))


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class BundleLeakTest(unittest.TestCase):
    def test_clean_bundle_passes(self):
        bundle = {
            "fog": {"2": "explored", "3": "sighted"},
            "provinces": [
                {"id": 2, "rgb": [2, 0, 0], "centroid": [24, 4], "terrain": "dunes", "area_km2": 12, "coastal": True},
                {"id": 3, "rgb": [3, 0, 0], "centroid": [28, 8], "coastal": True},
            ],
            "deposits": [
                {"province": 2, "code": "Sf", "name": "Salt", "worked_by": "Mine"},
                {"province": 3, "code": "Pe", "name": "Pearls", "worked_by": "Port", "coastal": True},
            ],
            "landmarks": [{"id": "cape", "province": 2, "name": "Cape", "x": 24, "y": 4, "charted": True}],
            "natives": [{"province": 2, "name": "Dune folk"}],
            "raster_ids": [2, 3],
        }
        reveal.validate_bundle(bundle, HIDDEN, legacy_east=[])

    def test_hidden_id_in_raster_fails(self):
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(load("leak_hidden_province.json"), HIDDEN, legacy_east=[])

    def test_sighted_inland_deposit_fails(self):
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(load("leak_deposit.json"), HIDDEN, legacy_east=[])

    def test_uncharted_landmark_fails(self):
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(load("leak_landmark.json"), HIDDEN, legacy_east=[])

    def test_native_record_before_explored_fails(self):
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(load("leak_native.json"), HIDDEN, legacy_east=[])

    def test_coordinate_inside_hidden_mask_fails(self):
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(load("leak_coordinate.json"), HIDDEN, legacy_east=[])

    def test_published_id_missing_from_fog_fails(self):
        bundle = {"fog": {}, "provinces": [{"id": 4, "rgb": [4, 0, 0], "centroid": [20, 2]}]}
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(bundle, HIDDEN, legacy_east=[])

    def test_fog_color_leak_fails(self):
        with self.assertRaises(SystemExit):
            reveal.assert_fog_color("leak.png", (20, 40, 90))
        reveal.assert_fog_color("fog.png", (210, 190, 150))

    def test_raster_paints_a_hidden_id(self):
        provinces = [
            {"id": 2, "rgb": [0, 255, 0]},
            {"id": 9, "rgb": [255, 0, 0]},
        ]
        image = Image.new("RGB", (4, 2), (0, 0, 0))
        image.putpixel((0, 0), (255, 0, 0))
        found = reveal.raster_ids_from_rgb(image, provinces)
        self.assertEqual(found, {9})
        with self.assertRaises(SystemExit):
            reveal.validate_bundle(
                {"fog": {"2": "explored"}, "provinces": [], "raster_ids": sorted(found)},
                HIDDEN,
                legacy_east=[],
            )


class HistoryGeometryTest(unittest.TestCase):
    def test_secret_point_generalises_the_west_line(self):
        import check_history

        check_history.WEST = 100
        check_history.PROVINCE_BBOXES = [(120, 0, 200, 40, "explored")]
        check_history.FOG_RINGS = [[[[300, 0], [400, 0], [400, 40], [300, 40], [300, 0]]]]
        try:
            self.assertEqual(check_history.secret_point(40, 10), "west")
            self.assertIsNone(check_history.secret_point(150, 10))
            self.assertEqual(check_history.secret_point(350, 10), "fog")
            check_history.PROVINCE_BBOXES.append((10, 10, 40, 30, "sighted"))
            self.assertIsNone(check_history.secret_point(20, 20))
        finally:
            check_history.WEST = 3592
            check_history.PROVINCE_BBOXES = []
            check_history.FOG_RINGS = []


if __name__ == "__main__":
    unittest.main()
