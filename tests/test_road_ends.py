#!/usr/bin/env python3
"""Road endpoints sit on the settlement markers they connect."""

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import road_ends as roads  # noqa: E402


def _endpoint_gap(land, grouped, road) -> float:
    points = roads._as_points(road)
    nation = road.get("nation") if isinstance(road.get("nation"), int) else None
    gap = 0.0
    for point in (points[0], points[-1]):
        marker = roads.match_end(land, grouped, point[0], point[1], nation)
        if marker is None:
            return 9999.0
        gap = max(gap, math.hypot(marker["x"] - point[0], marker["y"] - point[1]))
    return gap


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.land = roads.Land(ROOT)
        cls.markers = roads.load_markers(ROOT)
        cls.grouped = roads.markers_by_province(cls.markers)
        cls.infra = json.loads((ROOT / "data" / "infrastructure.json").read_text(encoding="utf-8"))

    def test_every_road_ends_on_a_marker(self) -> None:
        self.assertGreater(len(self.infra["roads"]), 1000)
        for road in self.infra["roads"]:
            gap = _endpoint_gap(self.land, self.grouped, road)
            self.assertLess(gap, 0.02, road.get("id"))

    def test_roads_stay_inside_their_nation(self) -> None:
        for road in self.infra["roads"]:
            points = roads._as_points(road)
            nation = road.get("nation") if isinstance(road.get("nation"), int) else None
            for start, end in zip(points, points[1:]):
                if math.hypot(end[0] - start[0], end[1] - start[1]) < 0.5:
                    self.assertTrue(self.land.owns(start[0], start[1], nation), road.get("id"))
                    continue
                self.assertTrue(self.land.segment_ok(start, end, nation), road.get("id"))

    def test_snap_is_idempotent(self) -> None:
        sample = json.loads(json.dumps(self.infra))
        changed = roads.snap_catalog(sample, self.land, self.markers)
        self.assertEqual(changed, 0)


class ShiftTests(unittest.TestCase):
    def test_moving_a_marker_pulls_its_road_end(self) -> None:
        land = roads.Land(ROOT)
        infra = json.loads((ROOT / "data" / "infrastructure.json").read_text(encoding="utf-8"))
        road = next(row for row in infra["roads"] if len(row["points"]) >= 2 and row["points"][0] != row["points"][-1])
        old = (float(road["points"][0][0]), float(road["points"][0][1]))
        nation = road["nation"]
        # Step a few pixels and keep the sample inside the same nation cell when possible.
        scale = land.scale
        lx = int(math.floor(old[0])) // scale
        ly = int(math.floor(old[1])) // scale
        new = (lx * scale + scale / 2, ly * scale + scale / 2)
        if math.hypot(new[0] - old[0], new[1] - old[1]) < 0.2:
            new = (old[0] + 1.0, old[1])
        moved = roads.shift_marker(infra, old, new, nation, land)
        self.assertGreaterEqual(moved, 1)
        self.assertEqual(road["points"][0][0], roads.quant(new[0]))
        self.assertEqual(road["points"][0][1], roads.quant(new[1]))
        points = roads._as_points(road)
        self.assertTrue(land.segment_ok(points[0], points[1], nation))


if __name__ == "__main__":
    unittest.main()
