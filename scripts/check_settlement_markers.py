#!/usr/bin/env python3
"""Fail if a stored settlement marker is outside its sector or too close to an edge.

Positions live on the settlement, not in the browser. City, town, and village
coordinates are the x,y of the matching place in data/infrastructure.json.
A capital is the nation's capital_xy in data/world.json. The marker has to sit
inside its sector polygon and its province polygon, on that province's land
when the lookup has any, and at least --min-px from those borders and from water.

PR #30 drew ledger icons from a sector centroid and did not move these coordinates.
A ledger level of "none" was not redrawn, so towns such as Slika stayed on the edge.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(message)


def rings_of(geom: dict) -> list[list[list[float]]]:
    kind = geom.get("type")
    coords = geom.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return coords
    return []


def point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    count = len(ring)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            denom = yj - yi
            if denom != 0 and x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def contains(x: float, y: float, geom: dict) -> bool:
    for rings in rings_of(geom):
        if not rings or not point_in_ring(x, y, rings[0]):
            continue
        if any(point_in_ring(x, y, hole) for hole in rings[1:]):
            continue
        return True
    return False


def segment_distance(x: float, y: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span == 0:
        return math.hypot(x - ax, y - ay)
    along = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / span))
    return math.hypot(x - (ax + along * dx), y - (ay + along * dy))


def boundary_distance(x: float, y: float, geom: dict) -> float:
    best = None
    for rings in rings_of(geom):
        for ring in rings:
            for i in range(len(ring) - 1):
                dist = segment_distance(x, y, ring[i][0], ring[i][1], ring[i + 1][0], ring[i + 1][1])
                if best is None or dist < best:
                    best = dist
    return 0.0 if best is None else best


def rect_distance(x: float, y: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx = 0.0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
    dy = 0.0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.hypot(dx, dy)


def cell_id(raw: bytes, width: int, height: int, lx: int, ly: int) -> int | None:
    if lx < 0 or ly < 0 or lx >= width or ly >= height:
        return None
    offset = (ly * width + lx) * 3
    cell = raw[offset : offset + 3]
    return cell[0] + (cell[1] << 8)


def water_distance(x: float, y: float, province: int, raw: bytes, width: int, height: int, scale: int) -> float:
    lx = int(x) // scale
    ly = int(y) // scale
    if cell_id(raw, width, height, lx, ly) != province:
        return 0.0
    best = 1e9
    for radius in range(1, 48):
        if (radius - 1) * scale >= best:
            break
        for dy in range(-radius, radius + 1):
            span = range(-radius, radius + 1) if abs(dy) == radius else (-radius, radius)
            for dx in span:
                cx = lx + dx
                cy = ly + dy
                found = cell_id(raw, width, height, cx, cy)
                if found == province:
                    continue
                if found is None:
                    dist = min(x, y, width * scale - x, height * scale - y)
                    dist = max(dist, 0.0)
                else:
                    dist = rect_distance(x, y, cx * scale, cy * scale, (cx + 1) * scale, (cy + 1) * scale)
                if dist < best:
                    best = dist
    return best


def load_stored_markers(root: Path) -> list[dict]:
    world = json.loads((root / "data" / "world.json").read_text(encoding="utf-8"))
    infra = json.loads((root / "data" / "infrastructure.json").read_text(encoding="utf-8"))
    places = {}
    for place in infra.get("places") or []:
        places[(place.get("type"), place.get("province"), place.get("name"))] = place
    capitals = {}
    for nation in world["nations"]:
        xy = nation.get("capital_xy") or [None, None]
        capitals[int(nation["capital_province"])] = (xy[0], xy[1], nation.get("short_name"))
    found = []
    with (root / "data" / "settlements.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sector = (row.get("sector_id") or "").strip()
            kind = (row.get("type") or "").strip().lower()
            name = (row.get("name") or "").strip()
            if not sector or not str(row.get("province") or "").isdigit():
                continue
            province = int(row["province"])
            if kind == "capital":
                xy = capitals.get(province)
                found.append(
                    {
                        "sector": sector,
                        "province": province,
                        "name": name,
                        "kind": kind,
                        "x": None if xy is None else xy[0],
                        "y": None if xy is None else xy[1],
                    }
                )
                continue
            place = places.get((kind, province, name))
            if place is None:
                found.append({"sector": sector, "province": province, "name": name, "kind": kind, "x": None, "y": None})
                continue
            found.append(
                {
                    "sector": sector,
                    "province": province,
                    "name": name,
                    "kind": kind,
                    "x": place.get("x"),
                    "y": place.get("y"),
                }
            )
    return found


def provinces_with_land(raw: bytes) -> set[int]:
    found: set[int] = set()
    for index in range(0, len(raw), 3):
        province = raw[index] + (raw[index + 1] << 8)
        if province:
            found.add(province)
    return found


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check stored settlement markers.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--min-px", type=float, default=2.0, help="Flag markers closer than this to a border (default 2).")
    parser.add_argument("--limit", type=int, default=40, help="How many problems to print.")
    parser.add_argument("--sector", default=None, help="Check one sector instead of every marker.")
    parser.add_argument("--xy", nargs=2, type=float, metavar=("X", "Y"), help="With --sector, check this point instead of the stored one.")
    args = parser.parse_args(argv)
    root = args.root
    meta = json.loads((root / "data" / "meta.json").read_text(encoding="utf-8"))
    sectors = {}
    for feature in json.loads((root / "data" / "sectors.geojson").read_text(encoding="utf-8"))["features"]:
        props = feature.get("properties") or {}
        sid = str(props.get("id") or "")
        if sid:
            sectors[sid] = feature.get("geometry") or {}
    provinces = {}
    flat = json.loads((root / "data" / "flat.geojson").read_text(encoding="utf-8"))
    for feature in flat["features"]:
        props = feature.get("properties") or {}
        if props.get("kind") == "province":
            provinces[int(props["id"])] = feature.get("geometry") or {}
    image = Image.open(root / "data" / "lookup.png")
    raw = image.tobytes()
    width = image.width
    height = image.height
    scale = int(meta["lookup_scale"])
    has_land = provinces_with_land(raw)
    problems = []
    checked = 0
    markers = load_stored_markers(root)
    if args.sector:
        markers = [marker for marker in markers if marker["sector"] == args.sector]
        if not markers:
            fail(f"{args.sector}: no settlement")
        if args.xy is not None:
            for marker in markers:
                marker["x"], marker["y"] = args.xy
    for marker in markers:
        where = f"{marker['sector']} {marker['kind']} {marker['name']}"
        x, y = marker["x"], marker["y"]
        province = marker["province"]
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or isinstance(x, bool):
            problems.append(f"{where}: no coordinates")
            continue
        sector_geom = sectors.get(marker["sector"])
        province_geom = provinces.get(province)
        if sector_geom is None:
            problems.append(f"{where}: sector polygon is missing")
            continue
        if not contains(float(x), float(y), sector_geom):
            problems.append(f"{where}: outside its sector ({x}, {y})")
            continue
        if province_geom is not None and not contains(float(x), float(y), province_geom):
            problems.append(f"{where}: outside its province polygon")
            continue
        found = cell_id(raw, width, height, int(x) // scale, int(y) // scale)
        if province in has_land:
            if found == 0:
                problems.append(f"{where}: in the water")
                continue
            if found != province:
                problems.append(f"{where}: on province {found}, not {province}")
                continue
        elif found not in (None, 0, province):
            problems.append(f"{where}: on province {found}, and province {province} has no land")
            continue
        clearance = boundary_distance(float(x), float(y), sector_geom)
        if province_geom is not None:
            clearance = min(clearance, boundary_distance(float(x), float(y), province_geom))
        if province in has_land:
            clearance = min(clearance, water_distance(float(x), float(y), province, raw, width, height, scale))
        checked += 1
        if args.min_px > 0 and clearance + 1e-6 < args.min_px:
            why = "this is as far inland as the sector's land allows" if clearance >= 1 else "on the border"
            problems.append(f"{where}: {clearance:.2f}px from a border (minimum {args.min_px:g}; {why})")
    print(f"checked {checked} settlement markers, flagged {len(problems)}")
    for line in problems[: args.limit]:
        print(line)
    if len(problems) > args.limit:
        print(f"... {len(problems) - args.limit} more")
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
