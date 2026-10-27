#!/usr/bin/env python3
"""Generate the cycle-0 settlement catalog.

Every published province gets one major city and one medium town. The
capital province's major city is the capital star already drawn from the
nation record, so it is not given a second marker. Coastal cities sit on
the coast; river provinces keep the city inland, near the province center.
Names are derived from the nation's own syllables and written into the
data so they stay fixed.

Roads stay inside one nation. They are a minimum spanning tree of that
nation's settlements on land, plus a few short extra links. A road never
crosses the sea or another nation's land. Each coastal nation gets one
port, on the coast beside its capital when that province is coastal, and
otherwise beside its coastal city nearest the capital.

Writes data/infrastructure.json and data/settlements.csv.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCALE = 4
WEST = 3592
VOWELS = set("aeiouy")
ONSETS = {
    "qu", "sh", "th", "ph", "ch", "br", "cr", "dr", "fr", "gr", "kr", "pr", "tr",
    "bl", "cl", "fl", "gl", "pl", "sl", "sn", "st", "sw",
}
STOP = {
    "of",
    "the",
    "and",
    "empire",
    "kingdom",
    "holy",
    "grand",
    "republic",
    "north",
    "south",
    "east",
    "west",
}
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


def syllables(word: str) -> list[str]:
    raw = "".join(ch for ch in word.lower() if "a" <= ch <= "z")
    if not raw:
        return ["mar"]
    parts: list[str] = []
    cur = ""
    seen = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        is_vowel = ch in VOWELS
        if seen and not is_vowel and any(c in VOWELS for c in raw[i:]):
            k = i
            while k < len(raw) and raw[k] not in VOWELS:
                k += 1
            if k < len(raw):
                cluster = raw[i:k]
                give = 2 if len(cluster) >= 2 and cluster[-2:] in ONSETS else 1
                onset = k - give
                if onset > i:
                    cur += raw[i:onset]
                    parts.append(cur)
                    cur = ""
                    seen = False
                    i = onset
                    continue
                parts.append(cur)
                cur = ""
                seen = False
                continue
        cur += ch
        if is_vowel:
            seen = True
        i += 1
    if cur:
        parts.append(cur)
    merged: list[str] = []
    index = 0
    while index < len(parts):
        part = parts[index]
        if len(part) == 1 and part in VOWELS:
            if merged:
                merged[-1] += part
            elif index + 1 < len(parts):
                parts[index + 1] = part + parts[index + 1]
            else:
                merged.append(part)
        else:
            merged.append(part)
        index += 1
    if len(merged) >= 2 and len(merged[0]) == 1 and merged[0] in VOWELS:
        merged[1] = merged[0] + merged[1]
        merged.pop(0)
    return merged or [raw]


def stem_of(name: str) -> str:
    words = [w for w in "".join(ch if ch.isalpha() else " " for ch in name).split() if w.lower() not in STOP]
    if not words:
        words = [w for w in "".join(ch if ch.isalpha() else " " for ch in name).split()] or ["Mar"]
    return max(words, key=len)


def onc(syllable: str) -> tuple[str, str, str]:
    i = 0
    while i < len(syllable) and syllable[i] not in VOWELS:
        i += 1
    j = i
    while j < len(syllable) and syllable[j] in VOWELS:
        j += 1
    onset, nucleus, coda = syllable[:i], syllable[i:j], syllable[j:]
    if not nucleus:
        nucleus = "a"
    return onset, nucleus, coda


def title_name(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def syllable_pool(parts: list[str], lengths: tuple[int, ...], repeats: bool) -> list[str]:
    """Join the nation's own syllables. Shorter joins come first."""
    from itertools import product

    out: list[str] = []
    seen: set[str] = set()
    for length in lengths:
        for seq in product(parts, repeat=length):
            repeated = any(seq[i] == seq[i + 1] for i in range(length - 1))
            if repeated != repeats:
                continue
            word = "".join(seq)
            if word in seen or not (2 <= len(word) <= 18):
                continue
            seen.add(word)
            out.append(word)
    return out


def name_options(nation_name: str) -> tuple[list[str], list[str]]:
    parts = syllables(stem_of(nation_name)) or ["mar"]
    towns = syllable_pool(parts, (1, 2, 3, 4), False) + syllable_pool(parts, (2, 3), True)
    cities = syllable_pool(parts, (2, 3, 4, 5), False) + syllable_pool(parts, (2, 3, 4), True)
    return towns or ["mara"], cities or towns or ["mara"]


def take_name(options: list[str], pid: int, kind: str, used: set[str], blocked: set[str]) -> str:
    head_n = min(len(options), 8 if kind == "town" else 10)
    head = options[:head_n]
    tail = options[head_n:]
    shift = pid % len(head)
    ordered = head[shift:] + head[:shift] + tail
    minimum = 2 if kind == "town" else 4
    for word in ordered:
        if len(word) < minimum or word in used or word in blocked:
            continue
        used.add(word)
        return title_name(word)
    for word in ordered:
        if word in used or word in blocked:
            continue
        used.add(word)
        return title_name(word)
    # The nation's own syllables are used up. Reuse a short name; the province
    # id keeps the settlement unique in the catalog.
    reusable = [word for word in ordered if word not in blocked and len(word) >= minimum] or options
    return title_name(reusable[pid % len(reusable)])


def load_lookup(path: Path) -> np.ndarray:
    image = Image.open(path).convert("RGB")
    arr = np.asarray(image)
    return arr[:, :, 0].astype(np.uint16) + (arr[:, :, 1].astype(np.uint16) << 8)


def components(cells: list[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    remaining = set(cells)
    found: list[list[tuple[int, int]]] = []
    while remaining:
        start = remaining.pop()
        queue = [start]
        comp = [start]
        head = 0
        while head < len(queue):
            x, y = queue[head]
            head += 1
            for dx, dy in DIRS:
                nxt = (x + dx, y + dy)
                if nxt in remaining:
                    remaining.remove(nxt)
                    queue.append(nxt)
                    comp.append(nxt)
        found.append(comp)
    found.sort(key=len, reverse=True)
    return found


def closest(cells: list[tuple[int, int]], x: float, y: float) -> tuple[int, int]:
    return min(cells, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)


def farthest(cells: list[tuple[int, int]], x: float, y: float) -> tuple[int, int]:
    return max(cells, key=lambda p: (p[0] - x) ** 2 + (p[1] - y) ** 2)


def cell_xy(lx: int, ly: int) -> tuple[int, int]:
    return lx * SCALE + SCALE // 2, ly * SCALE + SCALE // 2


def segment_on_mask(a: tuple[int, int], b: tuple[int, int], mask: np.ndarray, origin: tuple[int, int]) -> bool:
    ax, ay = a
    bx, by = b
    dist = math.hypot(bx - ax, by - ay)
    steps = max(1, int(math.ceil(dist / 2)))
    height, width = mask.shape
    ox, oy = origin
    for i in range(steps + 1):
        t = i / steps
        x = ax + (bx - ax) * t
        y = ay + (by - ay) * t
        lx = int(x // SCALE) - ox
        ly = int(y // SCALE) - oy
        if lx < 0 or ly < 0 or lx >= width or ly >= height or mask[ly, lx] == 0:
            return False
        if x < WEST:
            return False
    return True


def dedupe_points(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = [points[0]]
    for point in points[1:]:
        if point != out[-1]:
            out.append(point)
    return out


def drop_collinear(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(points) < 3:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[i]
        cx, cy = points[i + 1]
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) != 0:
            out.append(points[i])
    out.append(points[-1])
    return out


def simplify(points: list[tuple[int, int]], tol: float, ok) -> list[tuple[int, int]]:
    points = dedupe_points(points)
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tol * tol
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        ax, ay = points[i]
        bx, by = points[j]
        abx, aby = bx - ax, by - ay
        ab2 = abx * abx + aby * aby
        far_k = i + 1
        far_d = -1.0
        for k in range(i + 1, j):
            px, py = points[k]
            if ab2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * abx + (py - ay) * aby) / ab2
                if t < 0:
                    t = 0
                elif t > 1:
                    t = 1
                dx = ax + t * abx - px
                dy = ay + t * aby - py
                d = dx * dx + dy * dy
            if d > far_d:
                far_d = d
                far_k = k
        shortcut = ok(points[i], points[j]) and far_d <= tol2
        if shortcut:
            continue
        keep[far_k] = True
        stack.append((i, far_k))
        stack.append((far_k, j))
    return [points[k] for k in range(len(points)) if keep[k]]


class UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, a: int, b: int) -> bool:
        a = self.find(a)
        b = self.find(b)
        if a == b:
            return False
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1
        return True


def route_nation(mask: np.ndarray, origin: tuple[int, int], nodes: list[dict]) -> list[list[tuple[int, int]]]:
    """Land MST of nodes, plus a few short extra links. Points are map pixels."""
    height, width = mask.shape
    count = len(nodes)
    if count < 2:
        return []
    seed_of = list(range(count))
    taken: dict[tuple[int, int], int] = {}
    for i, node in enumerate(nodes):
        cell = (node["lx"], node["ly"])
        if cell in taken:
            seed_of[i] = taken[cell]
        else:
            taken[cell] = i
    seeds = [i for i in range(count) if seed_of[i] == i]
    if len(seeds) < 2:
        return []
    flat = mask.reshape(-1)
    owner = np.full(flat.size, -1, np.int32)
    dist = np.full(flat.size, 0, np.int32)
    parent = np.full(flat.size, -1, np.int32)
    queue: list[int] = []
    for i in seeds:
        idx = nodes[i]["ly"] * width + nodes[i]["lx"]
        owner[idx] = i
        parent[idx] = -1
        queue.append(idx)
    pairs: list[tuple[int, int, int, int, int]] = []
    seen_pairs: set[tuple[int, int]] = set()
    head = 0
    while head < len(queue):
        idx = queue[head]
        head += 1
        x = idx % width
        y = idx // width
        base = int(dist[idx])
        here = int(owner[idx])
        for dx, dy in DIRS:
            nx = x + dx
            ny = y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            nidx = ny * width + nx
            if flat[nidx] == 0:
                continue
            if dx and dy and (flat[y * width + nx] == 0 or flat[ny * width + x] == 0):
                continue
            other = int(owner[nidx])
            if other == -1:
                owner[nidx] = here
                dist[nidx] = base + 1
                parent[nidx] = idx
                queue.append(nidx)
            elif other != here:
                a, b = (here, other) if here < other else (other, here)
                if (a, b) in seen_pairs:
                    continue
                seen_pairs.add((a, b))
                pairs.append((base + int(dist[nidx]) + 1, a, b, idx, nidx))
    if not pairs:
        return []

    def trace(start: int) -> list[int]:
        out = [start]
        guard = 0
        while parent[start] != -1 and guard < flat.size:
            start = int(parent[start])
            out.append(start)
            guard += 1
        return out

    def geometry(idx: int, nidx: int) -> list[tuple[int, int]]:
        cells = trace(idx)[::-1] + trace(nidx)
        ox, oy = origin
        points = []
        for cell in cells:
            cx = cell % width
            cy = cell // width
            points.append(((cx + ox) * SCALE + SCALE // 2, (cy + oy) * SCALE + SCALE // 2))
        start_owner = int(owner[cells[0]])
        end_owner = int(owner[cells[-1]])
        points[0] = (nodes[start_owner]["x"], nodes[start_owner]["y"])
        points[-1] = (nodes[end_owner]["x"], nodes[end_owner]["y"])
        points = dedupe_points(points)

        def ok(a, b):
            return segment_on_mask(a, b, mask, origin)

        simple = simplify(drop_collinear(points), 12, ok)
        simple = dedupe_points(simple)
        if len(simple) >= 2 and all(ok(simple[i], simple[i + 1]) for i in range(len(simple) - 1)):
            return simple
        return dedupe_points(drop_collinear(points))

    pairs.sort()
    union = UnionFind(count)
    for i in range(count):
        if seed_of[i] != i:
            union.union(i, seed_of[i])
    tree: list[tuple[int, int, int, int, int]] = []
    spare: list[tuple[int, int, int, int, int]] = []
    for edge in pairs:
        _length, a, b, _idx, _nidx = edge
        if union.union(a, b):
            tree.append(edge)
        else:
            spare.append(edge)
    extras: list[tuple[int, int, int, int, int]] = []
    if tree and len(seeds) >= 4:
        lengths = sorted(edge[0] for edge in tree)
        median = lengths[len(lengths) // 2]
        budget = max(1, len(seeds) // 6)
        for edge in spare:
            if budget <= 0:
                break
            if edge[0] <= median * 1.75:
                extras.append(edge)
                budget -= 1
    roads = []
    for edge in tree + extras:
        line = geometry(edge[3], edge[4])
        if len(line) >= 2:
            roads.append(line)
    return roads


def main() -> None:
    meta = json.loads((ROOT / "data" / "meta.json").read_text())
    world = json.loads((ROOT / "data" / "world.json").read_text())
    lut = load_lookup(ROOT / "data" / "lookup.png")
    if lut.shape != (meta["lookup_height"], meta["lookup_width"]):
        raise SystemExit("lookup.png does not match meta.json")
    provinces = {int(pid): prov for pid, prov in world["provinces"].items()}
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    nation_of = {}
    for pid, prov in provinces.items():
        if prov.get("nation_id") is not None:
            nation_of[pid] = int(prov["nation_id"])
    by_nation: dict[int, list[int]] = defaultdict(list)
    unclaimed: list[int] = []
    for pid, prov in provinces.items():
        if prov.get("nation_id") is None:
            unclaimed.append(pid)
        else:
            by_nation[int(prov["nation_id"])].append(pid)

    west_col = (WEST - SCALE // 2 + SCALE - 1) // SCALE
    land = lut != 0
    land[:, :west_col] = False
    coast = np.zeros(lut.shape, dtype=bool)
    sea = lut == 0
    coast[1:, :] |= sea[:-1, :]
    coast[:-1, :] |= sea[1:, :]
    coast[:, 1:] |= sea[:, :-1]
    coast[:, :-1] |= sea[:, 1:]
    coast &= land

    ys, xs = np.nonzero(land)
    ids = lut[ys, xs]
    order = np.argsort(ids, kind="mergesort")
    ys, xs, ids = ys[order], xs[order], ids[order]
    splits = np.flatnonzero(np.diff(ids)) + 1
    bounds = np.concatenate(([0], splits, [len(ids)]))
    pixels: dict[int, list[tuple[int, int]]] = {}
    for start, end in zip(bounds[:-1], bounds[1:]):
        pid = int(ids[start])
        if pid not in provinces:
            continue
        pixels[pid] = list(zip(xs[start:end].tolist(), ys[start:end].tolist()))
    # A province thinner than one lookup pixel can disappear from the raster.
    # Snap it to the nearest land cell of the same nation so it still gets a
    # settlement. The marker stays on land, a few pixels from the centroid.
    borrowed: set[int] = set()
    missing = [pid for pid in provinces if pid not in pixels]
    height, width = lut.shape
    for pid in missing:
        prov = provinces[pid]
        nid = prov.get("nation_id")
        lx = int(prov["xy"][0] // SCALE)
        ly = int(prov["xy"][1] // SCALE)
        found = None
        for radius in range(0, 32):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    x, y = lx + dx, ly + dy
                    if x < west_col or y < 0 or x >= width or y >= height:
                        continue
                    other = int(lut[y, x])
                    if other == 0:
                        continue
                    if nid is not None and nation_of.get(other) != int(nid):
                        continue
                    found = (x, y)
                    break
                if found:
                    break
            if found:
                break
        if not found:
            raise SystemExit(f"province {pid} has no land nearby")
        pixels[pid] = [found]
        borrowed.add(pid)
    has_coast = {int(pid) for pid in np.unique(lut[coast]).tolist() if int(pid) in provinces}
    for pid in borrowed:
        lx, ly = pixels[pid][0]
        if coast[ly, lx]:
            has_coast.add(pid)

    global_parts: list[str] = []
    seen_parts: set[str] = set()
    for nation in world["nations"]:
        for part in syllables(stem_of(nation["short_name"])):
            if part not in seen_parts:
                seen_parts.add(part)
                global_parts.append(part)
    if not global_parts:
        global_parts = ["mar"]

    print(f"land provinces {len(pixels)} borrowed {len(borrowed)}", flush=True)
    settlements: list[dict] = []
    nodes_by_nation: dict[int, list[dict]] = defaultdict(list)

    def add_settlement(kind: str, pid: int, name: str, lx: int, ly: int, nation_id: int | None, port: bool) -> dict:
        x, y = cell_xy(lx, ly)
        row = {
            "kind": kind,
            "pid": pid,
            "name": name,
            "x": x,
            "y": y,
            "lx": lx - 0,  # filled after snap into the local grid
            "ly": ly,
            "gx": lx,
            "gy": ly,
            "nation": nation_id,
            "port": port,
        }
        settlements.append(row)
        if nation_id is not None and kind != "port":
            nodes_by_nation[nation_id].append(row)
        return row

    def place_pair(pid: int, prov: dict, nation_name: str, nation_id: int | None, used: set[str], blocked: set[str], capital: dict | None) -> None:
        cells = pixels[pid]
        comps = components(cells)
        main = comps[0]
        coast_cells = [cell for cell in main if coast[cell[1], cell[0]]]
        cx = prov["xy"][0] / SCALE
        cy = prov["xy"][1] / SCALE
        river = bool(prov.get("river"))
        if capital is not None:
            city_cell = closest(main, capital["xy"][0] / SCALE, capital["xy"][1] / SCALE)
            city_name = capital["name"]
            kind = "capital"
        elif coast_cells:
            city_cell = closest(coast_cells, cx, cy)
            kind = "city"
            city_name = ""
        elif river:
            city_cell = closest(main, cx, cy)
            kind = "city"
            city_name = ""
        else:
            city_cell = closest(main, cx, cy)
            kind = "city"
            city_name = ""
        shorts, longs = name_options(nation_name) if nation_name else (global_parts, global_parts)
        if kind == "city":
            city_name = take_name(longs, pid, "city", used, blocked)
        town_pool = [cell for cell in main if cell != city_cell]
        if capital is not None:
            marker = (int(capital["xy"][0] // SCALE), int(capital["xy"][1] // SCALE))
            away = [cell for cell in town_pool if max(abs(cell[0] - marker[0]), abs(cell[1] - marker[1])) >= 3]
            if away:
                town_pool = away
        town_cell = farthest(town_pool or main, city_cell[0], city_cell[1])
        if town_cell == city_cell and len(main) > 1:
            town_cell = farthest(main, city_cell[0] + 0.1, city_cell[1] - 0.1)
        town_name = take_name(shorts + longs, pid + 101, "town", used, blocked)
        if kind == "capital":
            # The star is the city. Only the town is a new marker, but the
            # capital still joins the road network at its land cell.
            capital_node = {
                "kind": "capital",
                "pid": pid,
                "name": city_name,
                "x": cell_xy(*city_cell)[0],
                "y": cell_xy(*city_cell)[1],
                "gx": city_cell[0],
                "gy": city_cell[1],
                "nation": nation_id,
                "port": False,
            }
            if nation_id is not None:
                nodes_by_nation[nation_id].append(capital_node)
            settlements.append({**capital_node, "lx": 0, "ly": 0})
        else:
            add_settlement("city", pid, city_name, city_cell[0], city_cell[1], nation_id, False)
        add_settlement("town", pid, town_name, town_cell[0], town_cell[1], nation_id, False)

    for nation in world["nations"]:
        nid = int(nation["id"])
        used: set[str] = set()
        blocked = {stem_of(nation["short_name"]).lower(), "".join(ch for ch in nation["short_name"].lower() if ch.isalpha())}
        cap_pid = int(nation["capital_province"])
        for pid in by_nation[nid]:
            prov = provinces[pid]
            capital = None
            if pid == cap_pid:
                capital = {"xy": nation["capital_xy"], "name": nation["short_name"]}
            place_pair(pid, prov, nation["short_name"], nid, used, blocked, capital)

    free_used: set[str] = set()
    for pid in unclaimed:
        place_pair(pid, provinces[pid], "", None, free_used, set(), None)

    print(f"placed {len(settlements)} settlements", flush=True)
    # Ports. One per nation that actually touches the sea.
    host_key: dict[tuple[int, int], dict] = {}
    for row in settlements:
        host_key[(row["nation"] if row["nation"] is not None else -1, row["pid"], row["kind"])] = row
    ports = 0
    for nation in world["nations"]:
        nid = int(nation["id"])
        pids = by_nation[nid]
        coastal_pids = [pid for pid in pids if pid in has_coast]
        if not coastal_pids:
            continue
        cap_pid = int(nation["capital_province"])
        host = None
        if cap_pid in coastal_pids:
            host = host_key.get((nid, cap_pid, "capital"))
        if host is None:
            cx, cy = nation["capital_xy"]
            best = None
            for pid in coastal_pids:
                city = host_key.get((nid, pid, "city"))
                if not city:
                    continue
                d = (city["x"] - cx) ** 2 + (city["y"] - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, city)
            host = best[1] if best else None
        if host is None:
            continue
        coast_cells = [(lx, ly) for lx, ly in pixels[host["pid"]] if coast[ly, lx]]
        if not coast_cells:
            continue
        hx = host["gx"]
        hy = host["gy"]
        band = []
        nearest = None
        farthest_cell = None
        for lx, ly in coast_cells:
            if (lx, ly) == (hx, hy):
                continue
            d2 = (lx - hx) ** 2 + (ly - hy) ** 2
            if nearest is None or d2 < nearest[0]:
                nearest = (d2, (lx, ly))
            if farthest_cell is None or d2 > farthest_cell[0]:
                farthest_cell = (d2, (lx, ly))
            if 16 <= d2 <= 18 * 18:
                band.append((d2, (lx, ly)))
        if band:
            port_cell = min(band)[1]
        elif nearest is None:
            port_cell = (hx, hy)
        elif nearest[0] < 16 and farthest_cell and farthest_cell[0] > nearest[0]:
            port_cell = farthest_cell[1]
        else:
            port_cell = nearest[1]
        host["port"] = True
        px, py = cell_xy(*port_cell)
        settlements.append(
            {
                "kind": "port",
                "pid": host["pid"],
                "name": "Port of " + host["name"],
                "x": px,
                "y": py,
                "lx": 0,
                "ly": 0,
                "gx": port_cell[0],
                "gy": port_cell[1],
                "nation": nid,
                "port": True,
            }
        )
        ports += 1

    # Roads.
    print("routing roads", flush=True)
    roads_out = []
    multi_component = 0
    routed = 0
    for nid, nodes in nodes_by_nation.items():
        pids = by_nation[nid]
        xs_all = []
        ys_all = []
        for pid in pids:
            for lx, ly in pixels[pid]:
                xs_all.append(lx)
                ys_all.append(ly)
        if not xs_all or len(nodes) < 2:
            continue
        minx, maxx = min(xs_all), max(xs_all)
        miny, maxy = min(ys_all), max(ys_all)
        mask = np.zeros((maxy - miny + 1, maxx - minx + 1), np.uint8)
        for pid in pids:
            for lx, ly in pixels[pid]:
                mask[ly - miny, lx - minx] = 1
        local_nodes = []
        for node in nodes:
            lx = node["gx"] - minx
            ly = node["gy"] - miny
            if not (0 <= lx < mask.shape[1] and 0 <= ly < mask.shape[0] and mask[ly, lx]):
                found = None
                for radius in range(1, 40):
                    for dy in range(-radius, radius + 1):
                        for dx in range(-radius, radius + 1):
                            if max(abs(dx), abs(dy)) != radius:
                                continue
                            nx, ny = lx + dx, ly + dy
                            if 0 <= nx < mask.shape[1] and 0 <= ny < mask.shape[0] and mask[ny, nx]:
                                found = (nx, ny)
                                break
                        if found:
                            break
                    if found:
                        break
                if not found:
                    continue
                lx, ly = found
                node = dict(node)
                node["x"], node["y"] = cell_xy(lx + minx, ly + miny)
            local = dict(node)
            local["lx"] = lx
            local["ly"] = ly
            local_nodes.append(local)
        lines = route_nation(mask, (minx, miny), local_nodes)
        # Count components roughly: a nation whose roads are fewer than nodes-1
        # and which has room to connect may be split by sea. Reported below.
        if len(local_nodes) >= 2 and len(lines) + 1 < len({(n["lx"], n["ly"]) for n in local_nodes}):
            multi_component += 1
        for index, line in enumerate(lines):
            roads_out.append(
                {
                    "id": f"road-{nid}-{index}",
                    "nation": nid,
                    "points": [[int(x), int(y)] for x, y in line],
                    "cycle": 0,
                }
            )
        routed += 1
        if routed % 40 == 0:
            print(f"routed {routed} nations, {len(roads_out)} roads", flush=True)

    places = []
    for row in settlements:
        if row["kind"] == "capital":
            continue
        place = {
            "id": f"{row['kind']}-{row['pid']}" if row["kind"] != "port" else f"port-{row['nation']}",
            "type": row["kind"],
            "name": row["name"],
            "province": int(row["pid"]),
            "x": int(row["x"]),
            "y": int(row["y"]),
            "cycle": 0,
        }
        if row["nation"] is not None:
            place["nation"] = int(row["nation"])
        places.append(place)
    places.sort(key=lambda item: (item["province"], item["type"], item["id"]))
    roads_out.sort(key=lambda item: item["id"])

    # Validate against the lookup and the fog line.
    def cell_pid(x: int, y: int) -> int:
        lx = int(x // SCALE)
        ly = int(y // SCALE)
        if ly < 0 or lx < 0 or ly >= lut.shape[0] or lx >= lut.shape[1]:
            return -1
        return int(lut[ly, lx])

    problems = []
    for place in places:
        if place["x"] < WEST:
            problems.append(f"{place['id']} is west of the fog")
        pid = cell_pid(place["x"], place["y"])
        if pid != place["province"]:
            home = provinces[place["province"]]
            nid = home.get("nation_id")
            same = nid is not None and nation_of.get(pid) == int(nid)
            near = math.hypot(place["x"] - home["xy"][0], place["y"] - home["xy"][1]) <= 48
            if not (place["province"] in borrowed and same and near and pid > 0):
                problems.append(f"{place['id']} sits on province {pid}, not {place['province']}")
        if place["type"] == "port":
            lx = int(place["x"] // SCALE)
            ly = int(place["y"] // SCALE)
            if not coast[ly, lx]:
                problems.append(f"{place['id']} is not on the coast")
    for road in roads_out:
        nid = road["nation"]
        pts = road["points"]
        for x, y in pts:
            if x < WEST:
                problems.append(f"{road['id']} crosses the fog")
                break
            pid = cell_pid(x, y)
            if nation_of.get(pid) != nid:
                problems.append(f"{road['id']} leaves nation {nid} at {(x, y)} (province {pid})")
                break
        else:
            for a, b in zip(pts, pts[1:]):
                dist = math.hypot(b[0] - a[0], b[1] - a[1])
                steps = max(1, int(math.ceil(dist / 2)))
                bad = False
                for i in range(steps + 1):
                    t = i / steps
                    x = a[0] + (b[0] - a[0]) * t
                    y = a[1] + (b[1] - a[1]) * t
                    if x < WEST or nation_of.get(cell_pid(int(x), int(y))) != nid:
                        problems.append(f"{road['id']} segment leaves the nation")
                        bad = True
                        break
                if bad:
                    break
    if problems:
        print("\n".join(problems[:30]))
        raise SystemExit(f"{len(problems)} settlement checks failed")

    catalog = {"capitals": True, "places": places, "roads": roads_out}
    (ROOT / "data" / "infrastructure.json").write_text(json.dumps(catalog, separators=(",", ":")) + "\n")

    def nation_name(nid: int | None) -> str:
        if nid is None:
            return "Unclaimed"
        return nations[nid]["short_name"]

    csv_rows = []
    for row in settlements:
        if row["kind"] == "port":
            continue
        csv_rows.append(
            {
                "nation": nation_name(row["nation"]),
                "province": int(row["pid"]),
                "name": row["name"],
                "type": row["kind"],
                "port": "yes" if row["port"] else "no",
            }
        )
    csv_rows.sort(key=lambda item: (item["nation"], item["province"], {"capital": 0, "city": 1, "town": 2}[item["type"]]))
    with (ROOT / "data" / "settlements.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["nation", "province", "name", "type", "port"])
        writer.writeheader()
        writer.writerows(csv_rows)

    cities = sum(1 for row in settlements if row["kind"] == "city")
    towns = sum(1 for row in settlements if row["kind"] == "town")
    capitals = sum(1 for row in settlements if row["kind"] == "capital")
    print(f"cities {cities} towns {towns} capitals {capitals} ports {ports} roads {len(roads_out)}")
    print(f"nations split by sea {multi_component}")
    print(f"csv rows {len(csv_rows)}")
    for sample_id, label in ((25, "Haldortel"), (21, "Verillian"), (23, "Sanbreque"), (8, "Zaneth")):
        names = [row["name"] for row in settlements if row["nation"] == sample_id and row["kind"] in ("city", "town")][:8]
        print(label, ", ".join(names))


if __name__ == "__main__":
    main()
