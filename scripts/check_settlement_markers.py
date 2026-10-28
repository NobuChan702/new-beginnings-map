#!/usr/bin/env python3
"""Fail if a settlement marker sits outside its sector or too close to an edge.

Icons follow the ledger level in province_slots.json (city, town, village).
Open land (level "none") has no marker. A province missing from the ledger
uses the start defaults: sector A is a village, a capital's sector A is a
town (a city when the nation is major), and sectors B and C are open land.
The generator map_label is not an icon.

Each marker is the centroid when that point lies inside with at least a
quarter of the sector's inradius of clearance, otherwise the pole of
inaccessibility. It must be on that province's land, inside the sector, and
at least that far from the sector edge.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MARGIN = 0.25


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


def centroid_of(geom: dict) -> tuple[float, float]:
    area = 0.0
    sx = 0.0
    sy = 0.0
    for rings in rings_of(geom):
        if not rings:
            continue
        ring = rings[0]
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[i + 1][0], ring[i + 1][1]
            cross = x1 * y2 - x2 * y1
            area += cross
            sx += (x1 + x2) * cross
            sy += (y1 + y2) * cross
    if abs(area) < 1e-9:
        ring = rings_of(geom)[0][0]
        return ring[0][0], ring[0][1]
    area *= 0.5
    return sx / (6.0 * area), sy / (6.0 * area)


def bounds_of(geom: dict) -> tuple[float, float, float, float]:
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for rings in rings_of(geom):
        for ring in rings:
            for x, y in ring:
                minx = min(minx, x)
                miny = min(miny, y)
                maxx = max(maxx, x)
                maxy = max(maxy, y)
    return minx, miny, maxx, maxy


def pole_of(geom: dict, land) -> tuple[tuple[float, float], float]:
    """Largest-clearance inland point, refined on a shrinking grid."""
    minx, miny, maxx, maxy = bounds_of(geom)
    span = max(maxx - minx, maxy - miny, 1.0)
    step = span / 10.0
    best = None
    best_d = -1.0
    window = (minx, miny, maxx, maxy)
    for _ in range(5):
        x0, y0, x1, y1 = window
        x = x0
        while x <= x1 + step * 0.01:
            y = y0
            while y <= y1 + step * 0.01:
                if contains(x, y, geom) and land(x, y):
                    dist = boundary_distance(x, y, geom)
                    if dist > best_d:
                        best_d = dist
                        best = (x, y)
                y += step
            x += step
        if best is None:
            break
        step = max(step / 3.0, 0.35)
        window = (best[0] - step * 4, best[1] - step * 4, best[0] + step * 4, best[1] + step * 4)
    if best is None:
        return None, 0.0
    return best, best_d


def marker_point(geom: dict, land):
    """Centroid when it is inland with clearance, otherwise the inland pole."""
    pole, inradius = pole_of(geom, land)
    if pole is None:
        return None
    centre = centroid_of(geom)
    if land(centre[0], centre[1]) and contains(centre[0], centre[1], geom):
        clearance = boundary_distance(centre[0], centre[1], geom)
        if inradius <= 0 or clearance >= MARGIN * inradius:
            return centre[0], centre[1], inradius
    return pole[0], pole[1], inradius


def levels_for(slots: dict, nations: dict, sectors: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    provinces = (slots or {}).get("provinces") or {}
    for key, row in provinces.items():
        for sector in (row or {}).get("sectors") or []:
            sid = str(sector.get("sector") or "")
            level = str(sector.get("level") or "none")
            if sid:
                out[sid] = level if level in ("city", "town", "village", "none") else "none"
    capital_of = {}
    tier_of = {}
    for nation in nations.values():
        capital_of[int(nation["capital_province"])] = nation
        tier_of[int(nation["capital_province"])] = nation.get("tier")
    for feature in sectors:
        props = feature.get("properties") or {}
        sid = str(props.get("id") or "")
        if not sid or sid in out:
            continue
        letter = str(props.get("letter") or "")
        pid = int(props.get("province") or 0)
        if letter == "A" and pid in capital_of:
            out[sid] = "city" if tier_of.get(pid) == "major" else "town"
        elif letter == "A":
            out[sid] = "village"
        else:
            out[sid] = "none"
    return out


def main() -> None:
    meta = json.loads((ROOT / "data" / "meta.json").read_text(encoding="utf-8"))
    world = json.loads((ROOT / "data" / "world.json").read_text(encoding="utf-8"))
    slots = json.loads((ROOT / "data" / "province_slots.json").read_text(encoding="utf-8"))
    sectors = json.loads((ROOT / "data" / "sectors.geojson").read_text(encoding="utf-8"))["features"]
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    image = Image.open(ROOT / "data" / "lookup.png")
    raw = image.tobytes()
    width = image.width
    scale = int(meta["lookup_scale"])
    levels = levels_for(slots, nations, sectors)
    counts = {"city": 0, "town": 0, "village": 0, "none": 0}
    checked = 0
    omitted = 0
    for feature in sectors:
        props = feature.get("properties") or {}
        sid = str(props.get("id") or "")
        pid = int(props.get("province") or 0)
        level = levels.get(sid, "none")
        if level == "none":
            counts["none"] += 1
            continue
        geom = feature.get("geometry") or {}

        def on_land(px: float, py: float) -> bool:
            lx = int(px) // scale
            ly = int(py) // scale
            if lx < 0 or ly < 0 or lx >= width or ly >= image.height:
                return False
            cell = raw[(ly * width + lx) * 3 : (ly * width + lx) * 3 + 3]
            return cell[0] + (cell[1] << 8) == pid

        placed = marker_point(geom, on_land)
        if placed is None:
            omitted += 1
            continue
        x, y, inradius = placed
        counts[level] = counts.get(level, 0) + 1
        checked += 1
        where = f"{sid} ({level})"
        if not contains(x, y, geom):
            fail(f"{where}: marker is outside its sector")
        clearance = boundary_distance(x, y, geom)
        if inradius > 0 and clearance + 1e-6 < MARGIN * inradius:
            fail(f"{where}: marker is too close to the sector edge ({clearance:.2f} < {MARGIN * inradius:.2f})")
        lx = int(x) // scale
        ly = int(y) // scale
        if lx < 0 or ly < 0 or lx >= width or ly >= image.height:
            fail(f"{where}: marker is off the lookup")
        cell = raw[(ly * width + lx) * 3 : (ly * width + lx) * 3 + 3]
        found = cell[0] + (cell[1] << 8)
        if found == 0:
            fail(f"{where}: marker is in the water")
        if found != pid:
            fail(f"{where}: marker is in province {found}, not {pid}")
    print(
        f"settlement icons city={counts.get('city', 0)} town={counts.get('town', 0)} "
        f"village={counts.get('village', 0)} open_land={counts.get('none', 0)} "
        f"checked={checked} omitted_no_land={omitted}"
    )


if __name__ == "__main__":
    main()
