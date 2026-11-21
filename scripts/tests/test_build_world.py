"""Hand-edited nation fields survive a rebuild. Synthetic nations only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

import build_map

EAST_BOX = {"minx": 4000, "miny": 2000, "maxx": 4200, "maxy": 2300}


def province(pid: int, xy: list[float], area: float = 100) -> dict:
    return {"id": pid, "area_km2": area, "centroid": list(xy)}


class HandEditedNationTest(unittest.TestCase):
    def setUp(self):
        build_map.PAINTED_OWNERS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(build_map.PAINTED_OWNERS.clear)

    def build(self, nations: str, provinces: list[dict], allowed: set[int], boxes: dict[int, dict]) -> dict:
        root = Path(self.tmp.name)
        paths = {
            "provinces": root / "provinces.json",
            "terrain_data": root / "terrain.json",
            "nations": root / "nations.yaml",
        }
        paths["provinces"].write_text(
            json.dumps({"km_per_px": 5.5, "provinces": provinces}),
            encoding="utf-8",
        )
        paths["terrain_data"].write_text(json.dumps({"provinces": []}), encoding="utf-8")
        paths["nations"].write_text(nations, encoding="utf-8")
        return build_map.build_world(paths, np.zeros((1, 1), np.uint16), allowed, boxes)

    def nation(self, world: dict, nid: int) -> dict:
        return next(item for item in world["nations"] if item["id"] == nid)

    def test_capital_xy_override_beats_the_centroid(self):
        world = self.build(
            """
nations:
  - id: 7
    name: "Realm of Test (major)"
    type: major
    color: "#112233"
    capital: 10
    capital_xy: [4000.25, 2100.75]
    provinces: [10]
  - id: 8
    name: "Plain Realm (minor)"
    type: minor
    color: "#445566"
    capital: 11
    provinces: [11]
""",
            [
                province(10, [4100.24, 2200.44]),
                province(11, [4300.26, 2400.34]),
            ],
            {10, 11},
            {10: dict(EAST_BOX), 11: dict(EAST_BOX)},
        )
        placed = self.nation(world, 7)
        plain = self.nation(world, 8)
        self.assertEqual(placed["capital_xy"], [4000.25, 2100.75])
        self.assertEqual(placed["capital_province"], 10)
        self.assertEqual(plain["capital_xy"], [4300.3, 2400.3])

    def test_capital_xy_west_of_the_fog_line_keeps_the_centroid(self):
        world = self.build(
            """
nations:
  - id: 7
    name: "Realm of Test (major)"
    type: major
    color: "#112233"
    capital: 10
    capital_xy: [100.5, 2200.5]
    provinces: [10]
""",
            [province(10, [4100.24, 2200.44])],
            {10},
            {10: dict(EAST_BOX)},
        )
        self.assertEqual(self.nation(world, 7)["capital_xy"], [4100.2, 2200.4])

    def test_short_name_passes_through(self):
        world = self.build(
            """
nations:
  - id: 7
    name: "Realm of Test (major)"
    short_name: "Testmark"
    type: major
    color: "#112233"
    capital: 10
    provinces: [10]
  - id: 8
    name: "Plain Realm (minor)"
    short_name: ""
    type: minor
    color: "#445566"
    capital: 11
    provinces: [11]
  - id: 9
    name: "Bare Realm (regional)"
    type: regional
    color: "#778899"
    capital: 12
    provinces: [12]
""",
            [
                province(10, [4100.2, 2200.4]),
                province(11, [4300.2, 2400.4]),
                province(12, [4500.2, 2500.4]),
            ],
            {10, 11, 12},
            {10: dict(EAST_BOX), 11: dict(EAST_BOX), 12: dict(EAST_BOX)},
        )
        self.assertEqual(self.nation(world, 7)["short_name"], "Testmark")
        self.assertEqual(self.nation(world, 8)["short_name"], "Plain Realm")
        self.assertEqual(self.nation(world, 9)["short_name"], "Bare Realm")

    def test_subtitle_passes_through(self):
        world = self.build(
            """
nations:
  - id: 7
    name: "Realm of Test (major)"
    subtitle: "Hand-edited subtitle"
    type: major
    color: "#112233"
    capital: 10
    provinces: [10]
  - id: 8
    name: "Plain Realm (minor)"
    subtitle: ""
    type: minor
    color: "#445566"
    capital: 11
    provinces: [11]
""",
            [
                province(10, [4100.2, 2200.4]),
                province(11, [4300.2, 2400.4]),
            ],
            {10, 11},
            {10: dict(EAST_BOX), 11: dict(EAST_BOX)},
        )
        self.assertEqual(self.nation(world, 7)["subtitle"], "Hand-edited subtitle")
        self.assertNotIn("subtitle", self.nation(world, 8))

    def test_keep_empty_nation_is_published_without_provinces(self):
        world = self.build(
            """
nations:
  - id: 41
    name: "Annexed Realm (minor)"
    short_name: "Annexed"
    subtitle: "Lost in cycle 3"
    type: minor
    color: "#abcdef"
    capital: 99
    capital_xy: [5000.5, 1800.25]
    bbox: [4800, 1600, 5200, 2000]
    area_km2: 1234.5
    keep_empty: true
    former_names: ["Old Annexed"]
""",
            [],
            set(),
            {},
        )
        self.assertEqual(
            self.nation(world, 41),
            {
                "id": 41,
                "name": "Annexed Realm (minor)",
                "short_name": "Annexed",
                "tier": "minor",
                "color": "#abcdef",
                "capital_province": 99,
                "capital_xy": [5000.5, 1800.25],
                "bbox": [4800, 1600, 5200, 2000],
                "province_count": 0,
                "area_km2": 1234.5,
                "provinces": [],
                "former_names": ["Old Annexed"],
                "subtitle": "Lost in cycle 3",
            },
        )
        self.assertEqual(world["provinces"], {})

    def test_keep_empty_on_the_fog_line_is_published(self):
        line = build_map.HIDDEN_WEST_OF
        world = self.build(
            f"""
nations:
  - id: 41
    name: "Annexed Realm (minor)"
    type: minor
    color: "#abcdef"
    capital: 99
    capital_xy: [{line}, 1800]
    bbox: [{line}, 1600, 5200, 2000]
    keep_empty: true
""",
            [],
            set(),
            {},
        )
        entry = self.nation(world, 41)
        self.assertEqual(entry["capital_xy"], [line, 1800])
        self.assertEqual(entry["bbox"][0], line)
        self.assertEqual(entry["province_count"], 0)
        self.assertEqual(entry["short_name"], "Annexed Realm")

    def test_keep_empty_west_of_the_fog_line_raises(self):
        line = build_map.HIDDEN_WEST_OF
        cases = {
            "capital": ([line - 1, 1800], [4800, 1600, 5200, 2000]),
            "bbox": ([5000, 1800], [line - 1, 1600, 5200, 2000]),
        }
        for label, (capital_xy, bbox) in cases.items():
            with self.subTest(label=label):
                nations = f"""
nations:
  - id: 41
    name: "Annexed Realm (minor)"
    short_name: "Annexed"
    type: minor
    color: "#abcdef"
    capital: 99
    capital_xy: {capital_xy}
    bbox: {bbox}
    keep_empty: true
"""
                with self.assertRaisesRegex(SystemExit, "secrecy: empty nation 41 sits west of the fog line"):
                    self.build(nations, [], set(), {})

    def test_painted_owners_recolors_annexed_provinces(self):
        provinces = [
            province(10, [3628, 16], area=40),
            province(11, [3656, 16], area=40),
            province(12, [3684, 16], area=40),
        ]
        boxes = {
            10: {"minx": 3620, "miny": 10, "maxx": 3636, "maxy": 22},
            11: {"minx": 3648, "miny": 10, "maxx": 3664, "maxy": 22},
            12: {"minx": 3676, "miny": 10, "maxx": 3692, "maxy": 22},
        }
        world = self.build(
            """
nations:
  - id: 1
    name: "Old Realm (minor)"
    type: minor
    color: "#00BB00"
    capital: 11
    provinces: [11]
  - id: 2
    name: "New Realm (major)"
    type: major
    color: "#AA0000"
    capital: 12
    provinces: [10, 12]
painted_owners:
  "10": 1
  "12": 99
""",
            provinces,
            {10, 11, 12},
            boxes,
        )
        self.assertEqual(build_map.PAINTED_OWNERS, {10: 1, 12: 99})
        self.assertEqual(world["provinces"]["10"]["nation_id"], 2)
        self.assertEqual(world["provinces"]["11"]["nation_id"], 1)
        self.assertEqual(world["provinces"]["12"]["nation_id"], 2)

        height, width = 48, 3720
        ids = np.zeros((height, width), np.uint16)
        ids[10:22, 3620:3636] = 10
        ids[10:22, 3648:3664] = 11
        ids[10:22, 3676:3692] = 12
        root = Path(self.tmp.name)
        political = root / "political.png"
        Image.new("RGB", (2500, 500), (196, 180, 138)).save(political)
        dest = root / "flat.geojson"
        build_map.write_flat_layer(ids, world, width, height, political, dest)

        collection = json.loads(dest.read_text(encoding="utf-8"))
        painted = {
            feat["properties"]["id"]: feat["properties"]
            for feat in collection["features"]
            if feat["properties"]["kind"] == "province"
        }
        self.assertEqual(set(painted), {10, 11, 12})
        self.assertEqual(painted[10]["nation_id"], 1)
        self.assertEqual(painted[10]["color"], "#00bb00")
        self.assertEqual(painted[11]["nation_id"], 1)
        self.assertEqual(painted[11]["color"], "#00bb00")
        self.assertEqual(painted[12]["nation_id"], 2)
        self.assertEqual(painted[12]["color"], "#aa0000")

    def test_painted_owners_clear_when_the_map_is_omitted(self):
        self.build(
            """
nations: []
painted_owners:
  "10": 1
""",
            [],
            set(),
            {},
        )
        self.assertEqual(build_map.PAINTED_OWNERS, {10: 1})
        self.build("nations: []\n", [], set(), {})
        self.assertEqual(build_map.PAINTED_OWNERS, {})


if __name__ == "__main__":
    unittest.main()
