#!/usr/bin/env python3
"""Cycle-0 baselines for provinces that later change hands."""

import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_history  # noqa: E402


def load_snaps():
    index = json.loads((ROOT / "data" / "history" / "index.json").read_text(encoding="utf-8"))
    snaps = []
    for name in index:
        snaps.append(json.loads((ROOT / "data" / "history" / name).read_text(encoding="utf-8")))
    return snaps


class TransferBaselineTests(unittest.TestCase):
    def test_published_history_matches_the_baked_owners(self) -> None:
        check_history.check_transfer_baselines(load_snaps(), check_history.baked_province_nations())

    def test_a_later_transfer_without_a_cycle_0_owner_is_refused(self) -> None:
        snaps = [
            {"transfers": [{"province": 2200, "owner": 276}]},
            {"transfers": [{"province": 2200, "owner": 21}, {"province": 2206, "owner": 21}]},
        ]
        baked = {2200: 276, 2206: 210}
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit):
                check_history.check_transfer_baselines(snaps, baked)
        text = err.getvalue()
        self.assertIn("2206", text)
        self.assertIn("cycle-0", text)

    def test_a_current_owner_written_into_cycle_0_is_refused(self) -> None:
        snaps = [
            {"transfers": [{"province": 2200, "owner": 21}]},
            {"transfers": [{"province": 2200, "owner": 21}]},
        ]
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit):
                check_history.check_transfer_baselines(snaps, {2200: 276})
        text = err.getvalue()
        self.assertIn("276", text)
        self.assertIn("2200", text)

    def test_check_history_script_passes(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_history.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("history secrecy ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
