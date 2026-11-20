"""Deposit income on a synthetic realm. No production provinces."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deposit_income

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_csv(name):
    import csv

    with (FIXTURES / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class IncomeTest(unittest.TestCase):
    def test_fixture_table(self):
        deposits = json.loads((FIXTURES / "income_deposits.json").read_text(encoding="utf-8"))
        routes = json.loads((FIXTURES / "income_routes.json").read_text(encoding="utf-8"))["routes"]
        totals = deposit_income.compute_income(deposits, read_csv("income_owners.csv"), read_csv("income_buildings.csv"), routes)
        albion = totals["Albion"]
        bor = totals["Bor"]
        # Fe mine 2 iron. Ag colony mine floor(5/2)=2 gold. Gt outpost 0.
        # Na farm 2 food, 1 gold, 1 powder. Wh port (status none) 2 gold, 1 food.
        # Active deal names Spices and Furs: +4 gold. Disrupted Pearls do not pay.
        self.assertEqual(albion["iron"], 2)
        self.assertEqual(albion["gold"], 2 + 1 + 2 + 4)
        self.assertEqual(albion["powder"], 1)
        self.assertEqual(albion["food"], 3)
        self.assertEqual(albion["manpower"], 0)
        # Sp market 5 gold. S colony mine floors to 0. Gm has no mine built. Trade +4.
        self.assertEqual(bor["gold"], 9)
        self.assertEqual(bor["iron"], 0)
        self.assertEqual(bor["powder"], 0)

    def test_every_code_and_half_rounding(self):
        owners = []
        buildings = []
        deposits = []
        cases = [
            (1, "Fe", "Mine", "integrated", {"iron": 2}),
            (2, "Ag", "Mine", "integrated", {"gold": 5}),
            (3, "Ag", "Market", "integrated", {"gold": 1}),
            (4, "Gm", "Mine", "integrated", {"gold": 3}),
            (5, "S", "Mine", "integrated", {"iron": 1, "powder": 1}),
            (6, "Gt", "Farm", "integrated", {"food": 2, "manpower": 50}),
            (7, "Na", "Farm", "integrated", {"food": 2, "gold": 1, "powder": 1}),
            (8, "Sp", "Market", "integrated", {"gold": 5}),
            (9, "Dy", "Lumber Camp", "integrated", {"gold": 2}),
            (10, "Hw", "Lumber Camp", "integrated", {"timber": 2}),
            (11, "Hr", "Farm", "integrated", {"food": 1, "horses": 1}),
            (12, "Fu", "Port", "integrated", {"gold": 3}),
            (13, "Pe", "Port", "integrated", {"gold": 3}),
            (14, "Wh", "Port", "integrated", {"gold": 2, "food": 1}),
            (15, "Sf", "Mine", "integrated", {"iron": 3}),
            (16, "Gt", "Farm", "colony", {"food": 1, "manpower": 25}),
            (17, "Fe", "Mine", "outpost", {}),
            (18, "Ag", "Mine", "colony", {"gold": 2}),
        ]
        for pid, code, building, status, _expected in cases:
            owners.append({"province": pid, "nation": f"N{pid}", "status": status})
            buildings.append({"province": pid, "building": building, "nation": f"N{pid}"})
            deposits.append({"province": pid, "code": code})
        # Silver mine and market on one integrated province stack. Dyes do not.
        owners.append({"province": 19, "nation": "Stack", "status": "integrated"})
        buildings.append({"province": 19, "building": "Mine", "nation": "Stack"})
        buildings.append({"province": 19, "building": "Market", "nation": "Stack"})
        deposits.append({"province": 19, "code": "Ag"})
        owners.append({"province": 20, "nation": "Once", "status": "integrated"})
        buildings.append({"province": 20, "building": "Lumber Camp", "nation": "Once"})
        buildings.append({"province": 20, "building": "Market", "nation": "Once"})
        deposits.append({"province": 20, "code": "Dy"})
        totals = deposit_income.compute_income(deposits, owners, buildings, [])
        for pid, _code, _building, _status, expected in cases:
            row = totals[f"N{pid}"]
            for key in deposit_income.RESOURCES:
                self.assertEqual(row[key], expected.get(key, 0), f"province {pid} {key}")
        self.assertEqual(totals["Stack"]["gold"], 6)
        self.assertEqual(totals["Stack"]["iron"], 0)
        self.assertEqual(totals["Once"]["gold"], 2)

    def test_unknown_code_fails(self):
        with self.assertRaises(SystemExit):
            deposit_income.compute_income(
                [{"province": 1, "code": "Zz"}],
                [{"province": 1, "nation": "Albion", "status": "integrated"}],
                [{"province": 1, "building": "Mine"}],
                [],
            )


if __name__ == "__main__":
    unittest.main()
