#!/usr/bin/env python3
"""Give every sector a settlement and write the village markers.

Sector A keeps the province's major city (the capital, on a capital
province). The existing town stays in whichever sector contains its
marker. A sector that still has neither gets a village at an interior
point, named from the owning nation's syllables in the same way as the
other settlements.

Updates data/settlements.csv (adds sector_id, and village rows) and
appends the village places to data/infrastructure.json. Existing cities,
towns, roads, and ports are left where they are.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]
TYPE_ORDER = {"capital": 0, "city": 1, "town": 2, "village": 3}


def load_names():
    spec = importlib.util.spec_from_file_location("build_settlements", ROOT / "scripts" / "build_settlements.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean(geom):
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        parts = [part for part in geom.geoms if part.geom_type in ("Polygon", "MultiPolygon")]
        if not parts:
            return None
        geom = max(parts, key=lambda part: part.area)
    if geom.is_empty:
        return None
    return geom


def load_sectors():
    collection = json.loads((ROOT / "data" / "sectors.geojson").read_text())
    by_province = defaultdict(list)
    for feature in collection["features"]:
        props = feature["properties"]
        geom = clean(shape(feature["geometry"]))
        if geom is None:
            raise SystemExit(f"sector {props['id']} has no geometry")
        by_province[int(props["province"])].append(
            {"id": props["id"], "letter": props["letter"], "geom": geom}
        )
    for sectors in by_province.values():
        sectors.sort(key=lambda item: item["letter"])
    return by_province


def sector_at(sectors, x: float, y: float):
    point = Point(float(x), float(y))
    inside = [sector for sector in sectors if sector["geom"].contains(point)]
    if len(inside) == 1:
        return inside[0]
    if len(inside) > 1:
        return max(inside, key=lambda sector: sector["geom"].boundary.distance(point))
    covered = [sector for sector in sectors if sector["geom"].covers(point)]
    if covered:
        return covered[0]
    return min(sectors, key=lambda sector: sector["geom"].distance(point))


def village_point(geom, avoid):
    minx, miny, maxx, maxy = geom.bounds
    span = max(maxx - minx, maxy - miny, 1.0)
    step = max(1.5, span / 18.0)
    best = None
    best_score = -1.0
    y = miny + step * 0.5
    while y < maxy:
        x = minx + step * 0.5
        while x < maxx:
            point = Point(x, y)
            if geom.contains(point):
                edge = geom.boundary.distance(point)
                if edge >= 0.45:
                    near = min((point.distance(Point(ax, ay)) for ax, ay in avoid), default=80.0)
                    score = min(edge, 6.0) + min(near, 14.0)
                    if score > best_score:
                        best_score = score
                        best = point
            x += step
        y += step
    if best is not None:
        return best
    point = geom.representative_point()
    if geom.contains(point):
        return point
    centre = geom.centroid
    for step_in in (0.15, 0.35, 0.6, 1.0):
        moved = Point(point.x + (centre.x - point.x) * step_in, point.y + (centre.y - point.y) * step_in)
        if geom.contains(moved):
            return moved
    return point


def main():
    names = load_names()
    world = json.loads((ROOT / "data" / "world.json").read_text())
    provinces = {int(key): value for key, value in world["provinces"].items()}
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    nation_of = {}
    for pid, prov in provinces.items():
        owner = prov.get("nation_id")
        nation_of[pid] = None if owner is None else int(owner)
    sectors = load_sectors()
    infra = json.loads((ROOT / "data" / "infrastructure.json").read_text())
    places = [row for row in infra.get("places") or [] if row.get("type") != "village"]

    towns = {}
    cities = {}
    for row in places:
        kind = row.get("type")
        pid = row.get("province")
        if pid is None or kind not in ("city", "town"):
            continue
        if kind == "town":
            towns[int(pid)] = (float(row["x"]), float(row["y"]))
        else:
            cities[int(pid)] = (float(row["x"]), float(row["y"]))

    held = defaultdict(set)
    town_sector = {}
    city_outside = 0
    for pid, sectors_here in sectors.items():
        by_id = {sector["id"]: sector for sector in sectors_here}
        city_id = f"{pid}-A"
        if city_id not in by_id:
            raise SystemExit(f"province {pid} has no sector A")
        held[pid].add(city_id)
        city = cities.get(pid)
        if city is not None and not by_id[city_id]["geom"].covers(Point(*city)):
            city_outside += 1
        town = towns.get(pid)
        if town is None:
            continue
        sector = sector_at(sectors_here, town[0], town[1])
        town_sector[pid] = sector["id"]
        held[pid].add(sector["id"])

    avoid = defaultdict(list)
    for pid, point in cities.items():
        avoid[pid].append(point)
    for pid, point in towns.items():
        avoid[pid].append(point)
    for nation in world["nations"]:
        pid = int(nation["capital_province"])
        xy = nation.get("capital_xy") or [0, 0]
        avoid[pid].append((float(xy[0]), float(xy[1])))

    used = defaultdict(set)
    blocked = {}
    pools = {}
    for nation in world["nations"]:
        nid = int(nation["id"])
        short = nation["short_name"]
        blocked[nid] = {
            names.stem_of(short).lower(),
            "".join(ch for ch in short.lower() if ch.isalpha()),
        }
        if nid in names.CAPITAL_NAMES:
            blocked[nid].add(names.CAPITAL_NAMES[nid].lower())
        shorts, longs = names.name_options(short)
        pools[nid] = shorts + longs
    with (ROOT / "data" / "settlements.csv").open(newline="") as handle:
        existing = list(csv.DictReader(handle))
    for row in existing:
        owner = nation_of.get(int(row["province"]))
        if owner is not None:
            used[owner].add(row["name"].lower())

    global_parts = []
    seen_parts = set()
    for nation in world["nations"]:
        for part in names.syllables(names.stem_of(nation["short_name"])):
            if part not in seen_parts:
                seen_parts.add(part)
                global_parts.append(part)
    free_pool = global_parts or ["mar"]
    free_used = {row["name"].lower() for row in existing if nation_of.get(int(row["province"])) is None}

    villages = []
    for pid in sorted(sectors):
        owner = nation_of.get(pid)
        for sector in sectors[pid]:
            if sector["id"] in held[pid]:
                continue
            point = village_point(sector["geom"], avoid[pid])
            if point is None or not sector["geom"].contains(point):
                raise SystemExit(f"no interior point for {sector['id']}")
            if sector["geom"].boundary.distance(point) < 0.2:
                raise SystemExit(f"village for {sector['id']} sits on the border")
            letter_n = {"A": 1, "B": 2, "C": 3}[sector["letter"]]
            if owner is None:
                village_name = names.take_name(free_pool, pid * 10 + letter_n, "town", free_used, set())
                free_used.add(village_name.lower())
                nation_name = "Unclaimed"
            else:
                village_name = names.take_name(pools[owner], pid * 10 + letter_n, "town", used[owner], blocked[owner])
                used[owner].add(village_name.lower())
                nation_name = nations[owner]["short_name"]
            x = round(float(point.x), 2)
            y = round(float(point.y), 2)
            place = {
                "id": "village-" + sector["id"],
                "type": "village",
                "name": village_name,
                "province": pid,
                "x": x,
                "y": y,
                "cycle": 0,
            }
            if owner is not None:
                place["nation"] = owner
            villages.append(place)
            avoid[pid].append((x, y))

    rows = []
    for row in existing:
        pid = int(row["province"])
        kind = row["type"]
        if kind in ("capital", "city"):
            sector_id = f"{pid}-A"
        elif kind == "town":
            sector_id = town_sector.get(pid, f"{pid}-A")
        else:
            raise SystemExit(f"unexpected settlement type {kind}")
        rows.append(
            {
                "nation": row["nation"],
                "province": pid,
                "name": row["name"],
                "type": kind,
                "port": row["port"],
                "sector_id": sector_id,
            }
        )
    for place in villages:
        pid = int(place["province"])
        owner = nation_of.get(pid)
        rows.append(
            {
                "nation": "Unclaimed" if owner is None else nations[owner]["short_name"],
                "province": pid,
                "name": place["name"],
                "type": "village",
                "port": "no",
                "sector_id": place["id"].replace("village-", "", 1),
            }
        )
    overrides = names.load_name_overrides()
    for row in rows:
        renamed = overrides.get(row["sector_id"])
        if renamed:
            row["name"] = renamed
    for place in villages:
        sector_id = str(place.get("id", "")).replace("village-", "", 1)
        renamed = overrides.get(sector_id)
        if renamed:
            place["name"] = renamed
    named = {(int(row["province"]), row["type"]): row["name"] for row in rows}
    for place in places:
        kind = place.get("type")
        pid = place.get("province")
        if kind not in ("city", "town") or pid is None:
            continue
        renamed = named.get((int(pid), kind))
        if renamed:
            place["name"] = renamed
    host_name = {}
    for row in rows:
        if row["type"] in ("capital", "city") and f"{int(row['province'])}-A" in overrides:
            host_name[int(row["province"])] = row["name"]
    for place in places:
        if place.get("type") != "port" or place.get("province") is None:
            continue
        host = host_name.get(int(place["province"]))
        if host:
            place["name"] = "Port of " + host
    rows.sort(key=lambda item: (item["nation"], item["province"], TYPE_ORDER[item["type"]], item["name"]))
    with (ROOT / "data" / "settlements.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["nation", "province", "name", "type", "port", "sector_id"])
        writer.writeheader()
        writer.writerows(rows)

    infra["places"] = places + villages
    (ROOT / "data" / "infrastructure.json").write_text(json.dumps(infra, separators=(",", ":")) + "\n")
    covered = set()
    for row in rows:
        covered.add(row["sector_id"])
    missing = [sector["id"] for group in sectors.values() for sector in group if sector["id"] not in covered]
    print(
        f"villages {len(villages)} settlements {len(rows)} "
        f"city markers outside A {city_outside} missing {len(missing)}"
    )
    if missing:
        raise SystemExit("sectors without a settlement: " + ", ".join(missing[:8]))


if __name__ == "__main__":
    main()
