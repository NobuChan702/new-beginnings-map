#!/usr/bin/env python3
"""Placement of settlement markers inside a sector, on land."""

import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import marker_placement as mp  # noqa: E402
import nb  # noqa: E402


def index_for(ids: np.ndarray, polygon) -> mp.MapIndex:
    return mp.MapIndex(root=ROOT, scale=4, ids=ids, sectors={"1-A": {"province": 1, "geom": polygon}}, provinces={1: polygon})


class PoleTests(unittest.TestCase):
    def test_open_square_sits_in_the_middle(self) -> None:
        ids = np.ones((20, 20), dtype=np.uint16)
        point = mp.inland_pole(index_for(ids, box(0, 0, 40, 40)), "1-A")
        self.assertIsNotNone(point)
        x, y, clearance = point
        self.assertLess(abs(x - 20), 2)
        self.assertLess(abs(y - 20), 2)
        self.assertGreater(clearance, 15)

    def test_water_on_the_right_pushes_the_marker_inland(self) -> None:
        ids = np.ones((20, 20), dtype=np.uint16)
        ids[:, 8:] = 0
        point = mp.inland_pole(index_for(ids, box(0, 0, 40, 40)), "1-A")
        self.assertIsNotNone(point)
        x, y, clearance = point
        self.assertLess(x, 20)
        self.assertGreater(clearance, 4)
        self.assertLess(abs(y - 20), 4)

    def test_slika_leaves_the_border(self) -> None:
        index = mp.load_index(ROOT)
        point = mp.inland_pole(index, "878-B")
        self.assertIsNotNone(point)
        x, y, clearance = point
        self.assertGreater(clearance, 10)
        self.assertGreater(math.hypot(x - 5442, y - 1662), 20)
        self.assertTrue(index.land(x, y, 878))


class MoveCommandTests(unittest.TestCase):
    def test_auto_dry_run_does_not_write(self) -> None:
        infra = (ROOT / "data" / "infrastructure.json").read_bytes()
        world = (ROOT / "data" / "world.json").read_bytes()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "nb.py"), "--dry-run", "settlement", "move", "878-B", "--auto"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(
            "(dry-run: not writing)" in proc.stdout or "No marker moves" in proc.stdout or "Nothing to write" in proc.stdout
        )
        self.assertEqual((ROOT / "data" / "infrastructure.json").read_bytes(), infra)
        self.assertEqual((ROOT / "data" / "world.json").read_bytes(), world)

    def test_xy_outside_the_sector_is_refused(self) -> None:
        with self.assertRaises(SystemExit):
            nb.main(["settlement", "move", "878-B", "--xy", "0", "0"])


if __name__ == "__main__":
    unittest.main()
