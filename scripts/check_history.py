#!/usr/bin/env python3
"""Refuse history snapshots that publish anything west of the fog line."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEST = 3592
WORK_MIN = 9000


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def as_rings(value, where):
    if not isinstance(value, list) or not value:
        fail(f"{where}: missing rings")
    if isinstance(value[0], list) and value[0] and isinstance(value[0][0], (int, float)) and not isinstance(value[0][0], bool):
        return [value]
    if (
        isinstance(value[0], list)
        and value[0]
        and isinstance(value[0][0], list)
        and value[0][0]
        and isinstance(value[0][0][0], (int, float))
        and not isinstance(value[0][0][0], bool)
    ):
        return value
    fail(f"{where}: rings must be map pixel coordinates")


def check_rings(value, where, width, height):
    for ring in as_rings(value, where):
        if len(ring) < 3:
            fail(f"{where}: a ring needs at least 3 points")
        for pt in ring:
            if not isinstance(pt, list) or len(pt) < 2:
                fail(f"{where}: bad point")
            x, y = pt[0], pt[1]
            if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                fail(f"{where}: point is not a number")
            if x < WEST:
                fail(f"{where}: x={x} is west of {WEST}")
            if x > width or y < 0 or y > height:
                fail(f"{where}: point {(x, y)} is outside the map")


def main():
    meta = load(ROOT / "data" / "meta.json")
    global WEST
    WEST = meta["hidden_west_of"]
    width = meta["width"]
    height = meta["height"]
    world = load(ROOT / "data" / "world.json")
    provinces = {int(key): value for key, value in world["provinces"].items()}
    index = load(ROOT / "data" / "history" / "index.json")
    files = index["snapshots"] if isinstance(index, dict) else index
    if not isinstance(files, list) or not files:
        fail("data/history/index.json needs a list of snapshot files")

    def province_id(value, where):
        if isinstance(value, bool) or not isinstance(value, int):
            fail(f"{where}: province id must be an integer")
        if value >= WORK_MIN:
            fail(f"{where}: {value} is reserved for man-made land")
        prov = provinces.get(value)
        if prov is None:
            fail(f"{where}: province {value} is not on the published map")
        box = prov.get("bbox") or [0, 0, 0, 0]
        xy = prov.get("xy") or [0, 0]
        if box[0] < WEST or xy[0] < WEST:
            fail(f"{where}: province {value} is west of the fog line")

    for name in files:
        if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
            fail(f"bad history file name: {name}")
        path = ROOT / "data" / "history" / name
        if not path.is_file():
            fail(f"missing {path}")
        snap = load(path)
        for row in snap.get("transfers") or []:
            province_id(row.get("province", row.get("id")), f"{name} transfer")
        for row in snap.get("occupations") or []:
            province_id(row.get("province", row.get("id")), f"{name} occupation")
        for row in snap.get("colonies") or []:
            province_id(row.get("province", row.get("id")), f"{name} colony")
        reveals = snap.get("reveals") or {}
        for pid in reveals.get("provinces") or []:
            province_id(pid, f"{name} reveal")
        fog = snap.get("fog") or {}
        for pid in fog.get("provinces") or snap.get("conceal") or []:
            province_id(pid, f"{name} fog")
        for work in snap.get("works") or []:
            wid = work.get("id")
            if isinstance(wid, bool) or not isinstance(wid, int) or wid < WORK_MIN:
                fail(f"{name} work id {wid} must be an integer >= {WORK_MIN}")
            if wid in provinces:
                fail(f"{name} work {wid} uses an existing province id")
            if work.get("status") == "removed" and not work.get("rings"):
                continue
            if work.get("rings"):
                check_rings(work["rings"], f"{name} work {wid}", width, height)
        for sea in snap.get("seas") or []:
            check_rings(sea.get("rings"), f"{name} sea {sea.get('id')}", width, height)
        for marker in snap.get("markers") or []:
            x = marker.get("x")
            y = marker.get("y")
            if isinstance(x, bool) or not isinstance(x, (int, float)) or x < WEST:
                fail(f"{name} marker x={x} is west of the fog line")
            if isinstance(y, bool) or not isinstance(y, (int, float)) or y < 0 or y > height or x > width:
                fail(f"{name} marker is outside the map")
    print(f"history secrecy ok ({len(files)} snapshots)")


if __name__ == "__main__":
    main()
