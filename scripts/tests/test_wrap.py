"""East-west wrap math for a synthetic 512 by 256 world."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reveal

WIDTH = 512
HEIGHT = 256


class WrapTest(unittest.TestCase):
    def test_coord_wraps_both_edges(self):
        self.assertEqual(reveal.wrap_coord(0, WIDTH), 0)
        self.assertEqual(reveal.wrap_coord(WIDTH, WIDTH), 0)
        self.assertEqual(reveal.wrap_coord(-1, WIDTH), WIDTH - 1)
        self.assertEqual(reveal.wrap_coord(WIDTH + 15, WIDTH), 15)
        self.assertEqual(reveal.wrap_coord(-WIDTH - 4, WIDTH), WIDTH - 4)

    def test_tile_index_wraps(self):
        count = 4
        self.assertEqual(reveal.tile_x(0, count), 0)
        self.assertEqual(reveal.tile_x(4, count), 0)
        self.assertEqual(reveal.tile_x(-1, count), 3)
        self.assertEqual(reveal.tile_x(5, count), 1)

    def test_native_zoom_follows_the_raster(self):
        self.assertEqual(reveal.native_zoom_for(WIDTH, HEIGHT), 1)
        self.assertEqual(reveal.native_zoom_for(12000, 5600), 6)
        self.assertEqual(reveal.native_zoom_for(22400, 5600), 7)
        columns, rows = reveal.tile_counts(22400, 5600, 7, 7)
        self.assertEqual(columns, 88)
        self.assertEqual(rows, 22)

    def test_path_nearest_the_view_crosses_the_seam(self):
        # A current that leaves the east edge and re-enters the west edge.
        path = reveal.nearest_path([500, 10], center=20, width=WIDTH)
        self.assertLess(path[0], path[1])
        self.assertAlmostEqual(path[1] - path[0], 22)
        self.assertLess(abs((path[0] + path[1]) / 2 - 20), WIDTH / 2)

    def test_view_shifts_and_minimap_split(self):
        self.assertEqual(reveal.view_shifts(10, 200, WIDTH), [0])
        self.assertEqual(reveal.view_shifts(-40, 80, WIDTH), [-1, 0])
        rects = reveal.minimap_rects(-30, 50, WIDTH)
        self.assertEqual(len(rects), 2)
        self.assertAlmostEqual(rects[0][0], WIDTH - 30)
        self.assertAlmostEqual(rects[0][1], WIDTH)
        self.assertAlmostEqual(rects[1][0], 0)
        self.assertAlmostEqual(rects[1][1], 50)
        whole = reveal.minimap_rects(0, WIDTH, WIDTH)
        self.assertEqual(whole, [(0.0, float(WIDTH))])

    def test_bad_width_fails(self):
        with self.assertRaises(SystemExit):
            reveal.wrap_coord(1, 0)
        with self.assertRaises(SystemExit):
            reveal.tile_x(1, 0)


if __name__ == "__main__":
    unittest.main()
