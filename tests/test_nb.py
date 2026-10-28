#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import nb  # noqa: E402


class NbValidationTests(unittest.TestCase):
    def test_sector_id_validation(self) -> None:
        with self.assertRaises(ValueError):
            nb.cmd_rename_settlement(
                argparse_namespace(sector_id="bad", name="X", dry_run=True)
            )

    def test_transfer_wrong_owner(self) -> None:
        world = {
            "nations": [
                {"id": 1, "name": "A", "bbox": [4000, 0, 5000, 5000], "capital_xy": [4500, 2500], "provinces": [10]},
                {"id": 2, "name": "B", "bbox": [4000, 0, 5000, 5000], "capital_xy": [4500, 2500], "provinces": []},
            ],
            "provinces": {
                "10": {"nation_id": 1, "bbox": [4000, 0, 5000, 5000], "xy": [4500, 2500]},
            },
        }
        with mock.patch.object(nb, "load_world", return_value=(world, {10: world["provinces"]["10"]}, {1: world["nations"][0], 2: world["nations"][1]})):
            with self.assertRaises(ValueError):
                nb.cmd_transfer(
                    argparse_namespace(from_nation="2", to_nation="1", provinces=["10"], cycle="0", dry_run=True)
                )


class NbWriteTests(unittest.TestCase):
    def test_claim_assign_dry_run(self) -> None:
        with mock.patch.object(nb, "safe_write") as sw:
            nb.cmd_claim_assign(
                argparse_namespace(nation="25", player="Alice", short=None, legacy_string=False, dry_run=False)
            )
            sw.assert_called_once()
            new_text = sw.call_args[0][1]
            data = json.loads(new_text)
            self.assertEqual(data["25"]["player"], "Alice")


def argparse_namespace(**kwargs):
    class NS:
        pass

    ns = NS()
    for key, val in kwargs.items():
        setattr(ns, key, val)
    return ns


if __name__ == "__main__":
    unittest.main()
