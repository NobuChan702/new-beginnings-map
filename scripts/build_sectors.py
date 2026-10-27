#!/usr/bin/env python3
"""Split every published province into sectors and write the control table.

A province is cut by a Voronoi diagram whose seeds are its city (the capital,
on a capital province), its town, and one interior point. The cells are
clipped to the province. Provinces under SMALL_KM2 get city and town only.
Provinces under TINY_KM2 stay one piece. Sector ids are <province>-A/B/C and
do not change unless the seeds or the province shape change.

Writes data/sectors.geojson and data/sector_control.csv. Every sector starts
controlled by the province's published owner. Nothing here changes ownership.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from shapely.geometry import MultiPoint, Point, mapping, shape
from shapely.ops import unary_union, voronoi_diagram
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]
TINY_KM2 = 20000
SMALL_KM2 = 80000
LETTERS = ("A", "B", "C")


def clean(geom):
    if geom is None or geom.is_empty:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    if geom.geom_type == "GeometryCollection":
        parts = [part for part in geom.geoms if part.geom_type in ("Polygon", "MultiPolygon")]
        geom = unary_union(parts) if parts else None
    if geom is None or geom.is_empty:
        return None
    return geom


def force_inside(poly, x: float, y: float) -> Point:
    point = Point(float(x), float(y))
    if poly.contains(point):
        return point
    if poly.covers(point):
        centre = poly.representative_point()
        for step in (0.04, 0.12, 0.3, 0.6):
            moved = Point(point.x + (centre.x - point.x) * step, point.y + (centre.y - point.y) * step)
            if poly.contains(moved):
                return moved
    centre = poly.representative_point()
    edge = poly.boundary.interpolate(poly.boundary.project(point))
    for step in (0.08, 0.2, 0.4, 0.7, 1):
        moved = Point(edge.x + (centre.x - edge.x) * step, edge.y + (centre.y - edge.y) * step)
        if poly.contains(moved):
            return moved
    return centre


def separate(poly, points: list[Point]) -> list[Point]:
    placed: list[Point] = []
    for point in points:
        moved = point
        for _ in range(6):
            if all(moved.distance(other) >= 4 for other in placed):
                break
            moved = force_inside(poly, moved.x + 5, moved.y + 3)
        placed.append(moved)
    return placed


def interior_point(poly, seeds: list[Point]) -> Point:
    minx, miny, maxx, maxy = poly.bounds
    span = max(maxx - minx, maxy - miny, 1)
    step = max(4.0, span / 24)
    best = None
    best_d = -1.0
    y = miny + step * 0.5
    while y < maxy:
        x = minx + step * 0.5
        while x < maxx:
            point = Point(x, y)
            if poly.contains(point):
                dist = min(point.distance(seed) for seed in seeds)
                if dist > best_d:
                    best_d = dist
                    best = point
            x += step
        y += step
    return best if best is not None else poly.representative_point()


def round_coords(value):
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, (list, tuple)):
        return [round_coords(item) for item in value]
    return value


def bbox_of(geom) -> list[float]:
    minx, miny, maxx, maxy = geom.bounds
    return [round(minx, 2), round(miny, 2), round(maxx, 2), round(maxy, 2)]


def split_province(poly, seeds: list[Point]):
    if len(seeds) == 1:
        return [poly]
    try:
        diagram = voronoi_diagram(MultiPoint(seeds), envelope=poly)
    except Exception:
        return [poly]
    parts: list = [None] * len(seeds)
    for cell in diagram.geoms:
        piece = clean(cell.intersection(poly))
        if piece is None:
            continue
        anchor = piece.representative_point()
        index = min(range(len(seeds)), key=lambda i: anchor.distance(seeds[i]))
        parts[index] = piece if parts[index] is None else clean(unary_union([parts[index], piece]))
    covered = [part for part in parts if part is not None]
    if covered:
        leftover = clean(poly.difference(unary_union(covered)))
        if leftover is not None:
            if leftover.geom_type == "Polygon":
                bits = [leftover]
            elif hasattr(leftover, "geoms"):
                bits = list(leftover.geoms)
            else:
                bits = [leftover]
            for bit in bits:
                bit = clean(bit)
                if bit is None:
                    continue
                anchor = bit.representative_point()
                index = min(range(len(seeds)), key=lambda i: anchor.distance(seeds[i]))
                parts[index] = bit if parts[index] is None else clean(unary_union([parts[index], bit]))
    if any(part is None for part in parts):
        return [poly]
    return parts


def main():
    world = json.loads((ROOT / "data" / "world.json").read_text())
    infra = json.loads((ROOT / "data" / "infrastructure.json").read_text())
    flat = json.loads((ROOT / "data" / "flat.geojson").read_text())
    provinces = {int(key): value for key, value in world["provinces"].items()}
    cities: dict[int, tuple[float, float]] = {}
    towns: dict[int, tuple[float, float]] = {}
    for place in infra.get("places") or []:
        province = place.get("province")
        if province is None:
            continue
        point = (float(place["x"]), float(place["y"]))
        if place.get("type") == "city":
            cities[int(province)] = point
        elif place.get("type") == "town":
            towns[int(province)] = point
    for nation in world["nations"]:
        province = int(nation["capital_province"])
        xy = nation.get("capital_xy")
        if province not in cities and xy and len(xy) >= 2:
            cities[province] = (float(xy[0]), float(xy[1]))

    shapes = {}
    for feature in flat["features"]:
        props = feature.get("properties") or {}
        if props.get("kind") != "province":
            continue
        shapes[int(props["id"])] = clean(shape(feature["geometry"]))

    features = []
    rows = []
    counts = {1: 0, 2: 0, 3: 0}
    failed = []
    for index, (pid, prov) in enumerate(sorted(provinces.items())):
        poly = shapes.get(pid)
        if poly is None or poly.is_empty or poly.area < 1:
            failed.append(pid)
            continue
        area = float(prov.get("area_km2") or 0)
        want = 1 if area < TINY_KM2 else 2 if area < SMALL_KM2 else 3
        centre = prov.get("xy") or [poly.representative_point().x, poly.representative_point().y]
        city = cities.get(pid, (float(centre[0]), float(centre[1])))
        town = towns.get(pid)
        seeds = [force_inside(poly, city[0], city[1])]
        roles = ["city"]
        if want >= 2 and town is not None:
            seeds.append(force_inside(poly, town[0], town[1]))
            roles.append("town")
        if want >= 3:
            seeds.append(interior_point(poly, seeds))
            roles.append("interior")
        if len(seeds) == 1:
            roles = ["city"]
        seeds = separate(poly, seeds)
        parts = split_province(poly, seeds)
        if len(parts) != len(seeds):
            parts = [poly]
            seeds = seeds[:1]
            roles = ["city"]
        owner = prov.get("nation_id")
        for letter, role, part in zip(LETTERS, roles, parts):
            part = clean(part)
            if part is None:
                failed.append(pid)
                continue
            sector_id = f"{pid}-{letter}"
            geometry = mapping(part)
            geometry["coordinates"] = round_coords(geometry["coordinates"])
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "id": sector_id,
                        "province": pid,
                        "letter": letter,
                        "role": role,
                        "bbox": bbox_of(part),
                    },
                    "geometry": geometry,
                }
            )
            rows.append(
                {
                    "sector_id": sector_id,
                    "controller": "" if owner is None else str(int(owner)),
                    "cycle": "0",
                }
            )
        counts[len(parts)] = counts.get(len(parts), 0) + 1
        if index and index % 400 == 0:
            print(f"split {index} provinces", flush=True)

    features.sort(key=lambda feature: (feature["properties"]["province"], feature["properties"]["letter"]))
    rows.sort(key=lambda row: row["sector_id"])
    collection = {"type": "FeatureCollection", "features": features}
    (ROOT / "data" / "sectors.geojson").write_text(json.dumps(collection, separators=(",", ":")) + "\n")
    with (ROOT / "data" / "sector_control.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sector_id", "controller", "cycle"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"sectors {len(features)} one {counts.get(1, 0)} two {counts.get(2, 0)} three {counts.get(3, 0)} skipped {len(set(failed))}")


if __name__ == "__main__":
    main()
