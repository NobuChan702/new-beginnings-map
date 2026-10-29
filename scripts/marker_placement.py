#!/usr/bin/env python3
"""Place a settlement marker inside its sector and its province, on land.

The point is the distance-transform maximum of the cells that are inside both
polygons and whose lookup pixel belongs to that province. That is the point
farthest from the sector edge, the province edge, and the water.

PR #30 drew icons in the browser from the sector centroid whenever that point
had a quarter of the sector inradius of clearance, and it never rewrote the
stored coordinates. The centroid can sit on the province border, and a ledger
level of "none" (Slika, sector 878-B) was not redrawn at all, so the town
coordinate in infrastructure.json stayed on the sector edge.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from shapely import contains_xy, distance
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]


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


def _edt_1d(values: np.ndarray) -> np.ndarray:
    """Squared distance transform along one axis. `values` is 0 on obstacles."""
    count = int(values.shape[0])
    if count == 0:
        return values.astype(np.float64)
    sites = np.zeros(count, dtype=np.int32)
    bounds = np.full(count + 1, np.inf)
    bounds[0] = -np.inf
    k = 0
    for q in range(1, count):
        vk = int(sites[k])
        denom = 2.0 * q - 2.0 * vk
        s = ((values[q] + q * q) - (values[vk] + vk * vk)) / denom if denom else -np.inf
        while s <= bounds[k]:
            k -= 1
            vk = int(sites[k])
            denom = 2.0 * q - 2.0 * vk
            s = ((values[q] + q * q) - (values[vk] + vk * vk)) / denom if denom else -np.inf
        k += 1
        sites[k] = q
        bounds[k] = s
    out = np.empty(count, dtype=np.float64)
    k = 0
    for q in range(count):
        while bounds[k + 1] < q:
            k += 1
        site = int(sites[k])
        delta = q - site
        out[q] = delta * delta + values[site]
    return out


def distance_to_outside(mask: np.ndarray) -> np.ndarray:
    """Euclidean distance of each True cell to the nearest False cell, in cell units."""
    if mask.size == 0 or not mask.any():
        return np.zeros(mask.shape, dtype=np.float64)
    height, width = mask.shape
    huge = 1e12
    grid = np.where(mask, huge, 0.0)
    along = np.empty_like(grid)
    for y in range(height):
        along[y] = _edt_1d(grid[y])
    out = np.empty_like(along)
    for x in range(width):
        out[:, x] = _edt_1d(along[:, x])
    return np.sqrt(np.maximum(out, 0.0))


def rect_distance(x: float, y: float, x0: float, y0: float, x1: float, y1: float) -> float:
    dx = 0.0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
    dy = 0.0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.hypot(dx, dy)


@dataclass
class MapIndex:
    root: Path
    scale: int
    ids: np.ndarray
    sectors: dict
    provinces: dict

    def land(self, x: float, y: float, province: int) -> bool:
        lx = int(x) // self.scale
        ly = int(y) // self.scale
        height, width = self.ids.shape
        if lx < 0 or ly < 0 or lx >= width or ly >= height:
            return False
        return int(self.ids[ly, lx]) == province


def load_index(root: Path | None = None) -> MapIndex:
    root = root or ROOT
    meta = json.loads((root / "data" / "meta.json").read_text(encoding="utf-8"))
    image = Image.open(root / "data" / "lookup.png")
    raw = np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(image.height, image.width, 3)
    ids = raw[:, :, 0].astype(np.uint16) + (raw[:, :, 1].astype(np.uint16) << 8)
    sectors = {}
    for feature in json.loads((root / "data" / "sectors.geojson").read_text(encoding="utf-8"))["features"]:
        props = feature.get("properties") or {}
        sid = str(props.get("id") or "")
        if not sid:
            continue
        geom = clean(shape(feature["geometry"]))
        if geom is None:
            continue
        sectors[sid] = {"province": int(props.get("province") or 0), "geom": geom}
    provinces = {}
    flat = json.loads((root / "data" / "flat.geojson").read_text(encoding="utf-8"))
    for feature in flat["features"]:
        props = feature.get("properties") or {}
        if props.get("kind") != "province":
            continue
        geom = clean(shape(feature["geometry"]))
        if geom is not None:
            provinces[int(props["id"])] = geom
    return MapIndex(root=root, scale=int(meta["lookup_scale"]), ids=ids, sectors=sectors, provinces=provinces)


def region_for(index: MapIndex, sector_id: str):
    sector = index.sectors[sector_id]
    province_id = sector["province"]
    geom = sector["geom"]
    province = index.provinces.get(province_id)
    if province is None:
        return geom, province_id
    try:
        both = clean(geom.intersection(province))
    except Exception:
        both = None
    if both is None or both.is_empty:
        return geom, province_id
    return both, province_id


def _candidate_points(index: MapIndex, region, province: int) -> tuple[np.ndarray, np.ndarray]:
    minx, miny, maxx, maxy = region.bounds
    scale = index.scale
    height, width = index.ids.shape
    lx0 = max(0, int(math.floor(minx)) // scale)
    lx1 = min(width - 1, int(math.floor(maxx)) // scale)
    ly0 = max(0, int(math.floor(miny)) // scale)
    ly1 = min(height - 1, int(math.floor(maxy)) // scale)
    if lx1 < lx0 or ly1 < ly0:
        return np.empty(0), np.empty(0)
    window = index.ids[ly0 : ly1 + 1, lx0 : lx1 + 1]
    ys, xs = np.where(window == province)
    if xs.size == 0:
        return np.empty(0), np.empty(0)
    # Four samples in each land cell, then keep those inside the polygon.
    offsets = (0.5, 1.5, 2.5, 3.5) if scale == 4 else tuple((i + 0.5) for i in range(scale))
    px = []
    py = []
    base_x = (xs + lx0) * scale
    base_y = (ys + ly0) * scale
    for oy in offsets:
        for ox in offsets:
            px.append(base_x + ox)
            py.append(base_y + oy)
    points_x = np.concatenate(px)
    points_y = np.concatenate(py)
    inside = contains_xy(region, points_x, points_y)
    return points_x[inside], points_y[inside]


def _water_distance(index: MapIndex, x: float, y: float, province: int) -> float:
    scale = index.scale
    height, width = index.ids.shape
    lx = int(x) // scale
    ly = int(y) // scale
    if lx < 0 or ly < 0 or lx >= width or ly >= height or int(index.ids[ly, lx]) != province:
        return 0.0
    best = 1e9
    for radius in range(1, 48):
        if (radius - 1) * scale >= best:
            break
        for dy in range(-radius, radius + 1):
            row_edge = abs(dy) == radius
            dx_values = range(-radius, radius + 1) if row_edge else (-radius, radius)
            for dx in dx_values:
                cx = lx + dx
                cy = ly + dy
                if 0 <= cx < width and 0 <= cy < height and int(index.ids[cy, cx]) == province:
                    continue
                if 0 <= cx < width and 0 <= cy < height:
                    dist = rect_distance(x, y, cx * scale, cy * scale, (cx + 1) * scale, (cy + 1) * scale)
                else:
                    dist = min(x, y, width * scale - x, height * scale - y)
                    dist = max(dist, 0.0)
                if dist < best:
                    best = dist
    return best


def inland_pole(index: MapIndex, sector_id: str) -> tuple[float, float, float] | None:
    """Return (x, y, clearance) farthest from sector, province, and water edges."""
    if sector_id not in index.sectors:
        return None
    region, province = region_for(index, sector_id)
    if region is None or region.is_empty:
        return None
    xs, ys = _candidate_points(index, region, province)
    if xs.size == 0:
        # The province has no lookup land inside this sector (a sliver thinner
        # than the lookup cell, or open water). Stay inside the polygon.
        part = region
        if part.geom_type == "MultiPolygon":
            part = max(part.geoms, key=lambda geom: geom.area)
        if part.geom_type != "Polygon":
            return None
        try:
            from shapely.ops import polylabel

            pole = polylabel(part, tolerance=0.4)
        except Exception:
            return None
        return float(pole.x), float(pole.y), float(part.boundary.distance(pole))
    # Coarse pick: prefer samples far from the polygon edge, then score water too.
    edge = np.asarray(distance(np.array([Point(x, y) for x, y in zip(xs, ys)]), region.boundary), dtype=float)
    # Keep the best few dozen polygon-clear samples. Water may pull the winner inland.
    order = np.argsort(edge)[::-1][:40]
    boundary = region.boundary
    best = None
    best_score = -1.0
    for index_at in order:
        x = float(xs[index_at])
        y = float(ys[index_at])
        score = min(float(edge[index_at]), _water_distance(index, x, y, province))
        if score > best_score:
            best_score = score
            best = (x, y, score)
    if best is None:
        return None
    # One local step toward the polygon pole if it stays on land and improves clearance.
    try:
        from shapely.ops import polylabel

        part = region
        if part.geom_type == "MultiPolygon":
            part = max(part.geoms, key=lambda geom: geom.area)
        if part.geom_type == "Polygon":
            pole = polylabel(part, tolerance=0.4)
            if index.land(pole.x, pole.y, province) and contains_xy(region, pole.x, pole.y):
                score = min(float(boundary.distance(pole)), _water_distance(index, pole.x, pole.y, province))
                if score > best[2]:
                    best = (float(pole.x), float(pole.y), score)
    except Exception:
        pass
    return best


@dataclass
class Marker:
    sector: str
    province: int
    name: str
    kind: str
    x: float | None
    y: float | None
    target: str
    place_id: str | None
    nation_id: int | None


def load_markers(root: Path | None = None) -> list[Marker]:
    root = root or ROOT
    world = json.loads((root / "data" / "world.json").read_text(encoding="utf-8"))
    infra = json.loads((root / "data" / "infrastructure.json").read_text(encoding="utf-8"))
    places = {}
    for place in infra.get("places") or []:
        key = (place.get("type"), place.get("province"), place.get("name"))
        places[key] = place
    capitals = {}
    for nation in world["nations"]:
        xy = nation.get("capital_xy") or [None, None]
        capitals[int(nation["capital_province"])] = (int(nation["id"]), xy[0], xy[1])
    found: list[Marker] = []
    with (root / "data" / "settlements.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sector = (row.get("sector_id") or "").strip()
            kind = (row.get("type") or "").strip().lower()
            name = (row.get("name") or "").strip()
            if not sector or not name or not str(row.get("province") or "").isdigit():
                continue
            province = int(row["province"])
            if kind == "capital":
                nation_id, x, y = capitals.get(province, (None, None, None))
                found.append(
                    Marker(sector, province, name, kind, _num(x), _num(y), "capital", None, nation_id)
                )
                continue
            place = places.get((kind, province, name))
            if place is None:
                found.append(Marker(sector, province, name, kind, None, None, "missing", None, None))
                continue
            found.append(
                Marker(
                    sector,
                    province,
                    name,
                    kind,
                    _num(place.get("x")),
                    _num(place.get("y")),
                    "infrastructure",
                    str(place.get("id") or ""),
                    place.get("nation") if isinstance(place.get("nation"), int) else None,
                )
            )
    return found


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def round_point(x: float, y: float) -> tuple[float, float]:
    return round(x, 2), round(y, 2)


def auto_moves(index: MapIndex, markers: list[Marker] | None = None, sector: str | None = None) -> list[dict]:
    markers = markers if markers is not None else load_markers(index.root)
    moves = []
    for marker in markers:
        if sector and marker.sector != sector:
            continue
        if marker.target == "missing":
            moves.append({**_marker_dict(marker), "ok": False, "reason": "no stored place"})
            continue
        placed = inland_pole(index, marker.sector)
        if placed is None:
            moves.append({**_marker_dict(marker), "ok": False, "reason": "no inland point"})
            continue
        x, y, clearance = placed
        x, y = round_point(x, y)
        moved = marker.x is None or marker.y is None or math.hypot(x - marker.x, y - marker.y) >= 0.05
        moves.append(
            {
                **_marker_dict(marker),
                "ok": True,
                "x": x,
                "y": y,
                "clearance": round(clearance, 2),
                "moved": moved,
            }
        )
    return moves


def _marker_dict(marker: Marker) -> dict:
    return {
        "sector": marker.sector,
        "province": marker.province,
        "name": marker.name,
        "kind": marker.kind,
        "old_x": marker.x,
        "old_y": marker.y,
        "target": marker.target,
        "place_id": marker.place_id,
        "nation_id": marker.nation_id,
    }


def apply_moves(root: Path, moves: list[dict]) -> tuple[dict, dict]:
    """Return updated (world, infrastructure) objects. Does not write."""
    world = json.loads((root / "data" / "world.json").read_text(encoding="utf-8"))
    infra = json.loads((root / "data" / "infrastructure.json").read_text(encoding="utf-8"))
    by_id = {str(place.get("id")): place for place in infra.get("places") or []}
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    for move in moves:
        if not move.get("ok") or not move.get("moved"):
            continue
        if move["target"] == "capital":
            nation = nations.get(int(move["nation_id"]))
            if nation is not None:
                nation["capital_xy"] = [move["x"], move["y"]]
        elif move["target"] == "infrastructure" and move.get("place_id") in by_id:
            by_id[move["place_id"]]["x"] = move["x"]
            by_id[move["place_id"]]["y"] = move["y"]
    return world, infra


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Compute inland settlement marker positions.")
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    place = sub.add_parser("place")
    place.add_argument("--sector", default=None)
    sub.add_parser("check")
    args = parser.parse_args(argv)
    if args.command == "place":
        index = load_index(args.root)
        moves = auto_moves(index, sector=args.sector)
        json.dump(moves, sys.stdout)
        sys.stdout.write("\n")
        return
    raise SystemExit(f"unknown command {args.command}")


if __name__ == "__main__":
    main()
