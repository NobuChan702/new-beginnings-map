"""Fog state filtering. Synthetic provinces only."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reveal

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FogFilterTest(unittest.TestCase):
    def test_order_and_empty_bundle_keeps_legacy_east(self):
        fog = reveal.parse_fog(json.loads((FIXTURES / "fog_empty.json").read_text(encoding="utf-8")))
        self.assertEqual(fog, {})
        legacy, states = reveal.publication_plan(
            base_ids=[1, 2],
            minx_of={1: 40, 2: 300},
            fog=fog,
            west=200,
        )
        self.assertEqual(legacy, {2})
        self.assertEqual(states[1], "hidden")
        self.assertEqual(states[2], "explored")
        self.assertLess(reveal.FOG_RANK["hidden"], reveal.FOG_RANK["sighted"])
        self.assertLess(reveal.FOG_RANK["sighted"], reveal.FOG_RANK["charted"])
        self.assertLess(reveal.FOG_RANK["charted"], reveal.FOG_RANK["explored"])

    def test_bundle_state_overrides_the_line(self):
        fog = reveal.parse_fog({"1": "sighted", "3": "charted"})
        _legacy, states = reveal.publication_plan([1, 2], {1: 40, 2: 300}, fog, 200)
        self.assertEqual(states[1], "sighted")
        self.assertEqual(states[2], "explored")
        self.assertEqual(states[3], "charted")

    def test_sighted_record_is_coastline_only(self):
        record = reveal.publish_province(
            7,
            {"centroid": [12.2, 40], "terrain": "steppe", "coastal": True, "native_power": "Nope"},
            "sighted",
            [{"code": "Wh", "name": "Whales", "province": 7, "worked_by": "Port"}],
        )
        self.assertEqual(set(record), {"id", "state", "coastal", "xy", "deposits"})
        self.assertTrue(record["coastal"])
        self.assertEqual(record["deposits"][0]["code"], "Wh")
        self.assertNotIn("terrain", record)
        self.assertNotIn("native_power", record)

    def test_sighted_inland_deposit_fails(self):
        with self.assertRaises(SystemExit):
            reveal.publish_province(7, {"centroid": [1, 1]}, "sighted", [{"code": "Fe", "province": 7, "worked_by": "Mine"}])

    def test_charted_keeps_terrain_and_name_only(self):
        record = reveal.publish_province(
            8,
            {
                "centroid": [4, 5],
                "terrain": "savanna",
                "mix": [["savanna", 80], ["dunes", 20]],
                "native_power": "Grass court",
                "nation_id": 3,
                "name": "Secret name",
                "bbox": [0, 0, 9, 9],
                "coastal": False,
            },
            "charted",
            [{"code": "Hw", "province": 8, "worked_by": "Lumber Camp"}],
        )
        self.assertEqual(record["terrain"], "savanna")
        self.assertEqual(record["native_power"], "Grass court")
        self.assertNotIn("nation_id", record)
        self.assertNotIn("name", record)
        self.assertIn("bbox", record)

    def test_explored_keeps_owner(self):
        record = reveal.publish_province(
            9,
            {"centroid": [4, 5], "terrain": "volcanic", "nation_id": 3, "region": "Rim", "coastal": False, "bbox": [1, 1, 2, 2]},
            "explored",
            [],
        )
        self.assertEqual(record["nation_id"], 3)
        self.assertEqual(record["region"], "Rim")
        self.assertEqual(record["terrain"], "volcanic")

    def test_hidden_publishes_nothing(self):
        with self.assertRaises(SystemExit):
            reveal.publish_province(1, {"centroid": [1, 1]}, "hidden", [])

    def test_bundle_province_with_extra_field_fails(self):
        with self.assertRaises(SystemExit):
            reveal.assert_province_fields(
                {"id": 4, "rgb": [1, 0, 0], "centroid": [1, 1], "terrain": "taiga"},
                "sighted",
                "province 4",
            )

    def test_new_terrain_colors(self):
        self.assertEqual(reveal.TERRAIN_COLORS["steppe"], "#AAAA70")
        self.assertEqual(reveal.TERRAIN_COLORS["savanna"], "#B8AA62")
        self.assertEqual(reveal.TERRAIN_COLORS["taiga"], "#346244")
        self.assertEqual(reveal.TERRAIN_COLORS["dunes"], "#EED698")
        self.assertEqual(reveal.TERRAIN_COLORS["volcanic"], "#5C504C")
        for name in reveal.NEW_TERRAINS:
            self.assertIn(name, reveal.TERRAIN_CLASSES)

    def test_bad_state_fails(self):
        with self.assertRaises(SystemExit):
            reveal.parse_fog({"1": "visible"})


if __name__ == "__main__":
    unittest.main()
