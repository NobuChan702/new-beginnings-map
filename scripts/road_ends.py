#!/usr/bin/env python3
"""Put road endpoints on the settlement markers they connect.

Cycle-0 roads were routed to the settlement cell centers that
``build_settlements.py`` stored. Marker placement later moved each city,
town, and capital to the inland point drawn on the map, and left the road
coordinates where they were. A road then ended in empty space or ran beside
its icon.

This module moves each stored endpoint onto the city, town, or capital in
the same province, and replaces a segment that would leave the nation with
a path that stays on that nation's land. Province borders and ownership are
not touched.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ROAD_KINDS = ("city", "town", "capital")
# The farthest current endpoint sits about 97px from its marker.
MAX_SNAP = 120.0
DIRS = (
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
)


def quant(value: float) -> int | float:
    value = round(float(value), 2)
    if abs(value - round(value)) < 1e-9:
        return int(round(value))
    return value


def quant_point(point: tuple[float, float]) -> list[int | float]:
    return [quant(point[0]), quant(point[1])]


class Land:
    def __init__(self, root: Path = ROOT):
        meta = json.loads((root / "data" / "meta.json").read_text(encoding="utf-8"))
        world = json.loads((root / "data" / "world.json").read_text(encoding="utf-8"))
        image = Image.open(root / "data" / "lookup.png").convert("RGB")
        raw = np.asarray(image)
        self.scale = int(meta["lookup_scale"])
        self.west = float(meta["hidden_west_of"])
        self.ids = raw[:, :, 0].astype(np.uint16) + (raw[:, :, 1].astype(np.uint16) << 8)
        self.nation_of: dict[int, int] = {}
        for key, prov in world["provinces"].items():
            if prov.get("nation_id") is not None:
                self.nation_of[int(key)] = int(prov["nation_id"])
        self.height, self.width = self.ids.shape

    def province_at(self, x: float, y: float) -> int:
        if x < self.west:
            return 0
        lx = int(math.floor(x)) // self.scale
        ly = int(math.floor(y)) // self.scale
        if ly < 0 or lx < 0 or ly >= self.height or lx >= self.width:
            return 0
        return int(self.ids[ly, lx])

    def owns(self, x: float, y: float, nation: int | None) -> bool:
        if x < self.west:
            return False
        pid = self.province_at(x, y)
        if pid <= 0:
            return False
        owner = self.nation_of.get(pid)
        if nation is None:
            return owner is None
        return owner == nation

    def segment_ok(self, a: tuple[float, float], b: tuple[float, float], nation: int | None) -> bool:
        dist = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, int(math.ceil(dist / 2)))
        for i in range(steps + 1):
            t = i / steps
            if not self.owns(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, nation):
                return False
        return True

    def walk(self, lx: int, ly: int, nation: int | None) -> bool:
        if ly < 0 or lx < 0 or ly >= self.height or lx >= self.width:
            return False
        if lx * self.scale < self.west:
            return False
        pid = int(self.ids[ly, lx])
        if pid <= 0:
            return False
        owner = self.nation_of.get(pid)
        if nation is None:
            return owner is None
        return owner == nation


def load_markers(root: Path = ROOT) -> list[dict]:
    world = json.loads((root / "data" / "world.json").read_text(encoding="utf-8"))
    infra = json.loads((root / "data" / "infrastructure.json").read_text(encoding="utf-8"))
    places = {}
    for place in infra.get("places") or []:
        places[(place.get("type"), place.get("province"), place.get("name"))] = place
    capitals = {}
    for nation in world["nations"]:
        xy = nation.get("capital_xy") or [None, None]
        capitals[int(nation["capital_province"])] = (int(nation["id"]), xy[0], xy[1])
    found = []
    with (root / "data" / "settlements.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            kind = (row.get("type") or "").strip().lower()
            name = (row.get("name") or "").strip()
            if kind not in ROAD_KINDS or not str(row.get("province") or "").isdigit():
                continue
            province = int(row["province"])
            if kind == "capital":
                nation, x, y = capitals.get(province, (None, None, None))
            else:
                place = places.get((kind, province, name))
                if place is None:
                    continue
                x, y = place.get("x"), place.get("y")
                nation = place.get("nation")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            found.append(
                {
                    "type": kind,
                    "name": name,
                    "province": province,
                    "x": float(x),
                    "y": float(y),
                    "nation": int(nation) if isinstance(nation, int) else None,
                }
            )
    return found


def markers_by_province(markers: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    for marker in markers:
        grouped[marker["province"]].append(marker)
    return grouped


def match_end(land: Land, grouped: dict[int, list[dict]], x: float, y: float, nation: int | None) -> dict | None:
    province = land.province_at(x, y)
    cands = grouped.get(province) or []
    if nation is not None:
        same = [marker for marker in cands if marker["nation"] == nation]
        if same:
            cands = same
    if not cands:
        return None
    marker = min(cands, key=lambda item: ((item["x"] - x) ** 2 + (item["y"] - y) ** 2, item["name"]))
    if math.hypot(marker["x"] - x, marker["y"] - y) > MAX_SNAP:
        return None
    return marker


def _nearest_cell(land: Land, x: float, y: float, nation: int | None, radius: int = 8) -> tuple[int, int] | None:
    lx = int(math.floor(x)) // land.scale
    ly = int(math.floor(y)) // land.scale
    if land.walk(lx, ly, nation):
        return lx, ly
    for ring in range(1, radius + 1):
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                if max(abs(dx), abs(dy)) != ring:
                    continue
                if land.walk(lx + dx, ly + dy, nation):
                    return lx + dx, ly + dy
    return None


def _bfs(land: Land, start: tuple[int, int], goal: tuple[int, int], nation: int | None) -> list[tuple[int, int]] | None:
    sep = max(abs(goal[0] - start[0]), abs(goal[1] - start[1]))
    pad = max(12, sep + 6)
    for _attempt in range(4):
        cells = _bfs_box(land, start, goal, nation, pad)
        if cells is not None:
            return cells
        pad = min(pad * 2, pad + 400)
    return None


def _bfs_box(
    land: Land,
    start: tuple[int, int],
    goal: tuple[int, int],
    nation: int | None,
    pad: int,
) -> list[tuple[int, int]] | None:
    minx = max(0, min(start[0], goal[0]) - pad)
    maxx = min(land.width - 1, max(start[0], goal[0]) + pad)
    miny = max(0, min(start[1], goal[1]) - pad)
    maxy = min(land.height - 1, max(start[1], goal[1]) + pad)
    if not (minx <= start[0] <= maxx and minx <= goal[0] <= maxx and miny <= start[1] <= maxy and miny <= goal[1] <= maxy):
        return None
    spanx = maxx - minx + 1
    spany = maxy - miny + 1
    count = spanx * spany
    if count < 1 or count > 450000:
        return None
    parent = np.full(count, -1, dtype=np.int32)

    def at(x: int, y: int) -> int:
        return (y - miny) * spanx + (x - minx)

    source = at(start[0], start[1])
    parent[source] = source
    qx = np.empty(count, dtype=np.int32)
    qy = np.empty(count, dtype=np.int32)
    head = 0
    tail = 1
    qx[0] = start[0]
    qy[0] = start[1]
    found = False
    while head < tail:
        x = int(qx[head])
        y = int(qy[head])
        head += 1
        if x == goal[0] and y == goal[1]:
            found = True
            break
        for dx, dy in DIRS:
            nx = x + dx
            ny = y + dy
            if nx < minx or ny < miny or nx > maxx or ny > maxy:
                continue
            if not land.walk(nx, ny, nation):
                continue
            if dx and dy and (not land.walk(x + dx, y, nation) or not land.walk(x, y + dy, nation)):
                continue
            nxt = at(nx, ny)
            if parent[nxt] != -1:
                continue
            parent[nxt] = at(x, y)
            qx[tail] = nx
            qy[tail] = ny
            tail += 1
    if not found:
        return None
    cells = []
    cur = at(goal[0], goal[1])
    guard = 0
    while guard <= count:
        cells.append((minx + (cur % spanx), miny + (cur // spanx)))
        if cur == source:
            break
        cur = int(parent[cur])
        if cur < 0:
            return None
        guard += 1
    if cells[-1] != start:
        return None
    cells.reverse()
    return cells


def _perp(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    return abs((point[0] - a[0]) * dy - (point[1] - a[1]) * dx) / length


def _simplify(points: list[tuple[float, float]], tolerance: float, ok) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        max_d = 0.0
        index = -1
        for i in range(start + 1, end):
            dist = _perp(points[i], points[start], points[end])
            if dist > max_d:
                max_d = dist
                index = i
        shortcut = max_d <= tolerance and ok(points[start], points[end])
        if not shortcut and index != -1:
            keep[index] = True
            if index - start > 1:
                stack.append((start, index))
            if end - index > 1:
                stack.append((index, end))
    return [points[i] for i in range(len(points)) if keep[i]]


def _dedupe(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not points:
        return []
    out = [points[0]]
    for point in points[1:]:
        if math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) >= 0.5:
            out.append(point)
    return out


def route(land: Land, a: tuple[float, float], b: tuple[float, float], nation: int | None) -> list[tuple[float, float]] | None:
    """Land path from a to b inside one nation. Ends are the exact coordinates."""
    if land.segment_ok(a, b, nation):
        return [a, b]
    start = _nearest_cell(land, a[0], a[1], nation)
    goal = _nearest_cell(land, b[0], b[1], nation)
    if start is None or goal is None:
        return None
    cells = _bfs(land, start, goal, nation)
    if not cells:
        return None
    scale = land.scale
    points = [(cell[0] * scale + scale / 2, cell[1] * scale + scale / 2) for cell in cells]
    points[0] = a
    points[-1] = b
    points = _dedupe(points)
    simple = _simplify(points, 12, lambda p, q: land.segment_ok(p, q, nation))
    simple = _dedupe(simple)
    if len(simple) < 2:
        return None
    if any(not land.segment_ok(simple[i], simple[i + 1], nation) for i in range(len(simple) - 1)):
        if any(not land.segment_ok(points[i], points[i + 1], nation) for i in range(len(points) - 1)):
            return None
        return points
    return simple


def _as_points(road: dict) -> list[tuple[float, float]]:
    points = []
    for pt in road.get("points") or []:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        x, y = pt[0], pt[1]
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            points.append((float(x), float(y)))
    return points


def _join(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not left:
        return right
    if not right:
        return left
    if math.hypot(left[-1][0] - right[0][0], left[-1][1] - right[0][1]) < 0.5:
        return left + right[1:]
    return left + right


def snap_road(land: Land, grouped: dict[int, list[dict]], road: dict) -> bool:
    """Move this road's ends onto its markers. Returns True when the line changes."""
    points = _as_points(road)
    if len(points) < 2:
        return False
    nation = road.get("nation")
    if isinstance(nation, bool) or not isinstance(nation, int):
        nation = None
    start = match_end(land, grouped, points[0][0], points[0][1], nation)
    end = match_end(land, grouped, points[-1][0], points[-1][1], nation)
    new_start = (start["x"], start["y"]) if start else points[0]
    new_end = (end["x"], end["y"]) if end else points[-1]
    # A city and its town can share one inland point. The link between them
    # has nowhere to go, so the road sits on that marker instead of beside it.
    if math.hypot(new_start[0] - new_end[0], new_start[1] - new_end[1]) < 0.5:
        encoded = [quant_point(new_start), quant_point(new_start)]
        if encoded == road.get("points"):
            return False
        road["points"] = encoded
        return True
    if len(points) == 2:
        path = route(land, new_start, new_end, nation)
        if path is None:
            return False
    else:
        left = route(land, new_start, points[1], nation)
        right = route(land, points[-2], new_end, nation)
        if left is None or right is None:
            return False
        path = _join(left, points[2:-1])
        path = _join(path, right)
    path = _dedupe(path)
    if len(path) < 2:
        return False
    if any(not land.segment_ok(path[i], path[i + 1], nation) for i in range(len(path) - 1)):
        return False
    encoded = [quant_point(pt) for pt in path]
    if encoded == road.get("points"):
        return False
    road["points"] = encoded
    return True


def snap_catalog(infra: dict, land: Land, markers: list[dict] | None = None) -> int:
    grouped = markers_by_province(markers if markers is not None else load_markers(land_root(land)))
    changed = 0
    for road in infra.get("roads") or []:
        if isinstance(road, dict) and snap_road(land, grouped, road):
            changed += 1
    return changed


def land_root(land: Land) -> Path:
    return ROOT


def shift_marker(infra: dict, old_xy: tuple[float, float], new_xy: tuple[float, float], nation: int | None, land: Land) -> int:
    """Follow a marker that moved, when a road end still sits on the old point."""
    if old_xy[0] is None or old_xy[1] is None or new_xy[0] is None or new_xy[1] is None:
        return 0
    old = (float(old_xy[0]), float(old_xy[1]))
    new = (float(new_xy[0]), float(new_xy[1]))
    if math.hypot(new[0] - old[0], new[1] - old[1]) < 0.05:
        return 0
    nation_id = int(nation) if isinstance(nation, int) else None
    changed = 0
    for road in infra.get("roads") or []:
        if not isinstance(road, dict):
            continue
        road_nation = road.get("nation")
        if nation_id is not None and road_nation != nation_id:
            continue
        points = _as_points(road)
        if len(points) < 2:
            continue
        owner = nation_id if nation_id is not None else road_nation if isinstance(road_nation, int) else None
        start_hit = math.hypot(points[0][0] - old[0], points[0][1] - old[1]) <= 0.51
        end_hit = math.hypot(points[-1][0] - old[0], points[-1][1] - old[1]) <= 0.51
        if not start_hit and not end_hit:
            continue
        if start_hit and end_hit and len(points) == 2:
            road["points"] = [quant_point(new), quant_point(new)]
            changed += 1
            continue
        updated = points
        if start_hit:
            path = route(land, new, points[1], owner)
            if path is None:
                continue
            updated = _join(path, points[2:])
        if end_hit:
            path = route(land, points[-2], new, owner)
            if path is None:
                continue
            updated = _join(updated[:-1], path)
        updated = _dedupe(updated)
        if len(updated) < 2 or any(not land.segment_ok(updated[i], updated[i + 1], owner) for i in range(len(updated) - 1)):
            continue
        road["points"] = [quant_point(pt) for pt in updated]
        changed += 1
    return changed


def _load_infra(root: Path) -> dict:
    return json.loads((root / "data" / "infrastructure.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Snap road endpoints onto settlement markers.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true", help="Rewrite data/infrastructure.json")
    args = parser.parse_args(argv)
    root = args.root
    infra = _load_infra(root)
    land = Land(root)
    markers = load_markers(root)
    changed = snap_catalog(infra, land, markers)
    print(f"roads {len(infra.get('roads') or [])} retargeted {changed}")
    if not args.write:
        print("(dry-run: not writing)")
        return
    text = json.dumps(infra, ensure_ascii=False, separators=(",", ":")) + "\n"
    (root / "data" / "infrastructure.json").write_text(text, encoding="utf-8")
    print(f"wrote data/infrastructure.json ({len(text)} bytes)")


if __name__ == "__main__":
    main()
