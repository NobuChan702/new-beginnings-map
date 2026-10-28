#!/usr/bin/env python3
"""Build rivers from the terrain and erase the old painted strokes.

Height comes from each province's terrain: mountains and hills are high,
plains and coasts are low, and the sea is zero. Sinks are filled, water
follows the steepest descent, and cells above --threshold become one tree of
rivers that run to the sea. The old paint only lowers the ground a little, so
the big channels stay near where they were. Each river is then one polyline
from its source to its mouth, with a seeded meander and a smooth curve.

    python3 tools/gen_rivers.py --seed 20261028 --threshold 140
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCALE = 4
WEST = 3592
TILE = 256

HEIGHTS = {
    "mountains": 72.0,
    "hills": 40.0,
    "jungle": 18.0,
    "forest": 16.0,
    "snow": 14.0,
    "tundra": 12.0,
    "grassland": 9.0,
    "plains": 8.0,
    "desert": 7.0,
    "marsh": 4.0,
    "lake": 1.0,
}


def log(msg: str) -> None:
    print(msg, flush=True)


def load_ids() -> np.ndarray:
    image = np.asarray(Image.open(ROOT / "data" / "lookup.png"))
    return image[:, :, 0].astype(np.int32) + (image[:, :, 1].astype(np.int32) << 8)


def ink_mask(tile: np.ndarray) -> np.ndarray:
    red = tile[:, :, 0].astype(np.int16)
    green = tile[:, :, 1].astype(np.int16)
    blue = tile[:, :, 2].astype(np.int16)
    bright = red + green + blue
    ink = (blue > 175) & (green > 145) & (red > 70) & (red < 165) & (blue > red + 28) & (green > red + 12) & (blue < 245)
    land = np.pad((bright > 500).astype(np.uint8), 2)
    water = np.pad(((blue > red + 15) & (red < 80) & (bright < 320)).astype(np.uint8), 2)
    land_count = np.zeros(ink.shape, np.int16)
    water_count = np.zeros(ink.shape, np.int16)
    for dy in range(5):
        for dx in range(5):
            land_count += land[dy : dy + ink.shape[0], dx : dx + ink.shape[1]]
            water_count += water[dy : dy + ink.shape[0], dx : dx + ink.shape[1]]
    return ink & (land_count >= 10) & (water_count <= 1)


def painted_bias(height: int, width: int) -> np.ndarray:
    """Lookup cells that carried the old river paint, used only as a hint.

    Prefer an untouched copy of the tiles when the working copies have already
    had the paint scraped off.
    """
    bias = np.zeros((height, width), np.uint8)
    archived = Path("/tmp/orig-terrain/tiles/terrain/6")
    root = archived if archived.is_dir() else ROOT / "tiles" / "terrain" / "6"
    for path in root.glob("*/*.webp"):
        tx, ty = int(path.parent.name), int(path.stem)
        tile = np.asarray(Image.open(path).convert("RGB"))
        hit = ink_mask(tile)
        if not hit.any():
            continue
        ys, xs = np.nonzero(hit)
        gx = tx * TILE + xs
        gy = ty * TILE + ys
        ok = (gx >= WEST) & (gx < width * SCALE) & (gy >= 0) & (gy < height * SCALE)
        bias[gy[ok] // SCALE, gx[ok] // SCALE] = 1
    return bias


def erase_wide(ids: np.ndarray) -> int:
    """Clear the darker river strokes the tight mask leaves behind, away from the shore."""
    removed = 0
    sea = ids == 0
    padded = np.pad(sea, 2)
    near = np.zeros_like(sea)
    height, width = sea.shape
    for dy in range(5):
        for dx in range(5):
            near |= padded[dy : dy + height, dx : dx + width]
    near &= ~sea
    root = ROOT / "tiles" / "terrain"
    for path in root.glob("*/*/*.webp"):
        zoom = int(path.parents[1].name)
        scale = 2 ** (6 - zoom)
        tx, ty = int(path.parent.name), int(path.stem)
        tile = np.asarray(Image.open(path).convert("RGB")).copy()
        th, tw = tile.shape[:2]
        red = tile[:, :, 0].astype(np.int16)
        green = tile[:, :, 1].astype(np.int16)
        blue = tile[:, :, 2].astype(np.int16)
        color = (red > 45) & (red < 200) & (green > 100) & (blue > 120) & (blue > red + 12) & (blue < 250)
        if not color.any():
            continue
        ys, xs = np.indices(color.shape)
        gx = tx * TILE * scale + xs * scale
        gy = ty * TILE * scale + ys * scale
        lx = np.clip(gx // SCALE, 0, ids.shape[1] - 1)
        ly = np.clip(gy // SCALE, 0, ids.shape[0] - 1)
        land = (ids[ly, lx] != 0) & (gx >= WEST) & ~near[ly, lx]
        local = color & land
        if not local.any():
            continue
        padded_ink = np.pad(local, 2)
        dilated = local.copy()
        for dy in range(5):
            for dx in range(5):
                dilated |= padded_ink[dy : dy + th, dx : dx + tw]
        stroke = dilated & (blue > red + 12) & (blue > 130) & (red < 190)
        changed = False
        for y, x in zip(*np.nonzero(stroke)):
            colors = []
            for dy in range(-12, 13):
                for dx in range(-12, 13):
                    yy, xx = int(y) + dy, int(x) + dx
                    if yy < 0 or xx < 0 or yy >= th or xx >= tw or stroke[yy, xx]:
                        continue
                    sample = tile[yy, xx]
                    # Any nearby land that is not itself blue, including dark forest.
                    if int(sample[2]) < int(sample[0]) + 18 and int(sample[2]) < 190:
                        colors.append((int(sample[0]), int(sample[1]), int(sample[2])))
            if not colors:
                continue
            colors.sort()
            tile[y, x] = colors[len(colors) // 2]
            removed += 1
            changed = True
        if changed:
            Image.fromarray(tile).save(path, "WEBP", quality=92, method=4)
    return removed


def erase_paint() -> int:
    """Remove the blocky river ink from every terrain zoom."""
    removed = 0
    root = ROOT / "tiles" / "terrain"
    for path in root.glob("*/*/*.webp"):
        tile = np.asarray(Image.open(path).convert("RGB")).copy()
        height, width = tile.shape[:2]
        local = ink_mask(tile)
        if not local.any():
            continue
        padded = np.pad(local, 2)
        dilated = local.copy()
        for dy in range(5):
            for dx in range(5):
                dilated |= padded[dy : dy + height, dx : dx + width]
        red = tile[:, :, 0].astype(np.int16)
        green = tile[:, :, 1].astype(np.int16)
        blue = tile[:, :, 2].astype(np.int16)
        stroke = dilated & (blue > red + 18) & (blue > 140) & (red < 200) & (green > 110)
        if not stroke.any():
            continue
        changed = False
        for y, x in zip(*np.nonzero(stroke)):
            colors = []
            for dy in range(-5, 6):
                for dx in range(-5, 6):
                    yy, xx = int(y) + dy, int(x) + dx
                    if yy < 0 or xx < 0 or yy >= height or xx >= width or stroke[yy, xx]:
                        continue
                    sample = tile[yy, xx]
                    total = int(sample[0]) + int(sample[1]) + int(sample[2])
                    if total > 520 and int(sample[2]) < int(sample[0]) + 25:
                        colors.append((int(sample[0]), int(sample[1]), int(sample[2])))
            if not colors:
                continue
            colors.sort()
            tile[y, x] = colors[len(colors) // 2]
            removed += 1
            changed = True
        if changed:
            Image.fromarray(tile).save(path, "WEBP", quality=92, method=4)
    return removed


def hash32(*parts: int) -> int:
    value = 2166136261
    for part in parts:
        value ^= part & 0xFFFFFFFF
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def noise(seed: int) -> float:
    value = hash32(seed, 0x9E3779B9)
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    return (value / 4294967295.0) * 2.0 - 1.0


def build_height(ids: np.ndarray, bias: np.ndarray, seed: int):
    world = json.loads((ROOT / "data" / "world.json").read_text(encoding="utf-8"))
    provinces = {int(key): value for key, value in world["provinces"].items()}
    max_id = int(ids.max())
    height_of = np.zeros(max_id + 1, np.float32)
    lake = np.zeros(max_id + 1, bool)
    for pid, prov in provinces.items():
        if pid > max_id:
            continue
        mix = prov.get("mix") or []
        if mix:
            total = 0.0
            height = 0.0
            for name, pct in mix:
                total += float(pct)
                height += HEIGHTS.get(str(name), 8.0) * float(pct)
                if name == "lake" and float(pct) >= 12:
                    lake[pid] = True
            height_of[pid] = height / total if total else HEIGHTS.get(str(prov.get("terrain")), 8.0)
        else:
            height_of[pid] = HEIGHTS.get(str(prov.get("terrain")), 8.0)
        if prov.get("coastal"):
            height_of[pid] = max(1.0, float(height_of[pid]) * 0.45)
    grid = np.zeros(ids.shape, np.float32)
    raw_land = ids > 0
    grid[raw_land] = height_of[ids[raw_land]]
    channel = raw_land & bias.astype(bool)
    grid[channel] = np.maximum(1.0, grid[channel] - 16.0)
    rng = np.random.default_rng(seed)
    grid += rng.uniform(-0.35, 0.35, grid.shape).astype(np.float32)
    east = np.arange(ids.shape[1])[None, :] >= WEST // SCALE
    lake_cells = lake[np.clip(ids, 0, max_id)]
    land = raw_land & ~lake_cells & east
    outlets = ~land
    grid[~land] = 0.0
    return grid, land, outlets


def touches(mask: np.ndarray, other: np.ndarray) -> np.ndarray:
    padded = np.pad(other, 1)
    hit = np.zeros(mask.shape, bool)
    height, width = mask.shape
    for dy in range(3):
        for dx in range(3):
            if dx == 1 and dy == 1:
                continue
            hit |= padded[dy : dy + height, dx : dx + width]
    return mask & hit


def fill_sinks(elev: np.ndarray, land: np.ndarray, outlets: np.ndarray):
    height, width = elev.shape
    filled = elev.copy()
    visited = outlets.copy()
    down = np.full(height * width, -1, np.int32)
    heap: list[tuple[float, int]] = []
    border = touches(outlets, land)
    ys, xs = np.nonzero(border)
    for y, x in zip(ys.tolist(), xs.tolist()):
        heapq.heappush(heap, (0.0, int(y) * width + int(x)))
    neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    while heap:
        level, index = heapq.heappop(heap)
        y, x = divmod(index, width)
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height or visited[ny, nx] or not land[ny, nx]:
                continue
            if dx and dy and not (land[y, nx] or outlets[y, nx]) and not (land[ny, x] or outlets[ny, x]):
                continue
            visited[ny, nx] = True
            spill = max(float(elev[ny, nx]), level)
            filled[ny, nx] = spill
            down[ny * width + nx] = index if land[y, x] else -1
            heapq.heappush(heap, (spill, ny * width + nx))
    return filled, down


def flow(filled: np.ndarray, land: np.ndarray, outlets: np.ndarray, down: np.ndarray) -> np.ndarray:
    height, width = filled.shape
    neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
    ys, xs = np.nonzero(land)
    for y, x in zip(ys.tolist(), xs.tolist()):
        here = float(filled[y, x])
        best_drop = 1e-4
        best = None
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if dx and dy and not (land[y, nx] or outlets[y, nx]) and not (land[ny, x] or outlets[ny, x]):
                continue
            if outlets[ny, nx]:
                drop = here
                dest = -1
            elif land[ny, nx] and float(filled[ny, nx]) < here - 1e-4:
                drop = here - float(filled[ny, nx])
                dest = ny * width + nx
            else:
                continue
            if drop > best_drop:
                best_drop = drop
                best = dest
        if best is not None:
            down[y * width + x] = best
    return down


def accumulate(down: np.ndarray, land: np.ndarray) -> np.ndarray:
    count = down.size
    acc = np.ones(count, np.float32)
    indeg = np.zeros(count, np.int32)
    land_flat = land.ravel()
    for index, dest in enumerate(down.tolist()):
        if land_flat[index] and dest >= 0 and land_flat[dest]:
            indeg[dest] += 1
    queue = [index for index in range(count) if land_flat[index] and indeg[index] == 0]
    head = 0
    while head < len(queue):
        index = queue[head]
        head += 1
        dest = int(down[index])
        if dest >= 0 and land_flat[dest]:
            acc[dest] += acc[index]
            indeg[dest] -= 1
            if indeg[dest] == 0:
                queue.append(dest)
    missed = int(land_flat.sum()) - head
    if missed:
        log(f"drainage left {missed} cells in a cycle")
    return acc


def sea_distance(ids: np.ndarray, land: np.ndarray) -> np.ndarray:
    height, width = land.shape
    dist = np.full(height * width, 1_000_000, np.int32)
    sea = (ids == 0) & (np.arange(width)[None, :] >= WEST // SCALE)
    queue = deque()
    ys, xs = np.nonzero(touches(sea, land))
    for y, x in zip(ys.tolist(), xs.tolist()):
        index = int(y) * width + int(x)
        dist[index] = 0
        queue.append(index)
    neighbors = ((1, 0), (-1, 0), (0, 1), (0, -1))
    while queue:
        index = queue.popleft()
        y, x = divmod(index, width)
        base = int(dist[index]) + 1
        for dx, dy in neighbors:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= width or ny >= height or not land[ny, nx]:
                continue
            nxt = ny * width + nx
            if dist[nxt] > base:
                dist[nxt] = base
                queue.append(nxt)
    return dist


def _same_stream(down: np.ndarray, src: int, dst: int) -> bool:
    current = src
    for _ in range(8):
        nxt = int(down[current])
        if nxt < 0:
            return False
        if nxt == dst:
            return True
        current = nxt
    return False


def thin_channels(river: np.ndarray, down: np.ndarray, acc: np.ndarray, width: int):
    keep = river.copy()
    height = river.size // width
    join = np.full(down.size, -1, np.int32)
    for _pass in range(2):
        drop = []
        for index in np.nonzero(keep)[0].tolist():
            y, x = divmod(index, width)
            stronger = -1
            stronger_acc = float(acc[index]) + 0.5
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    other = ny * width + nx
                    if not keep[other]:
                        continue
                    if _same_stream(down, index, other) or _same_stream(down, other, index):
                        continue
                    if acc[other] > stronger_acc:
                        stronger = other
                        stronger_acc = float(acc[other])
            if stronger >= 0:
                drop.append(index)
                join[index] = stronger
        for index in drop:
            keep[index] = False
    repaired = down.copy()
    for index in np.nonzero(keep)[0].tolist():
        dest = int(down[index])
        seen: set[int] = set()
        while dest >= 0 and not keep[dest] and dest not in seen:
            seen.add(dest)
            if join[dest] >= 0 and keep[int(join[dest])]:
                dest = int(join[dest])
                break
            dest = int(down[dest])
        repaired[index] = dest if dest < 0 or keep[dest] else -1
    return keep, repaired


def trace_tree(down: np.ndarray, river: np.ndarray, acc: np.ndarray, sea_dist: np.ndarray, width: int, ids: np.ndarray):
    count = down.size
    land_river = river
    upstream = np.zeros(count, np.int16)
    dominant = np.full(count, -1, np.int32)
    dominant_acc = np.full(count, -1.0, np.float32)
    for index, dest in enumerate(down.tolist()):
        if not land_river[index] or dest < 0 or not land_river[dest]:
            continue
        upstream[dest] += 1
        if acc[index] > dominant_acc[dest]:
            dominant_acc[dest] = acc[index]
            dominant[dest] = index

    def adjacent_water(index: int) -> bool:
        y, x = divmod(index, width)
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                ny, nx = y + dy, x + dx
                if 0 <= ny < ids.shape[0] and 0 <= nx < ids.shape[1] and int(ids[ny, nx]) == 0 and nx >= WEST // SCALE:
                    return True
        return False

    def walk_to_sea(path: list[int]) -> None:
        if adjacent_water(path[-1]):
            return
        current = path[-1]
        seen = set(path)
        for _ in range(48):
            if adjacent_water(current):
                return
            y, x = divmod(current, width)
            best = None
            best_dist = int(sea_dist[current])
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= ny < ids.shape[0] and 0 <= nx < ids.shape[1]):
                    continue
                nxt = ny * width + nx
                if nxt in seen or int(ids[ny, nx]) == 0:
                    continue
                if int(sea_dist[nxt]) < best_dist:
                    best_dist = int(sea_dist[nxt])
                    best = nxt
            if best is None:
                return
            path.append(best)
            seen.add(best)
            current = best

    def follow(start: int) -> list[int]:
        path = [start]
        current = start
        seen = {start}
        while True:
            dest = int(down[current])
            if dest < 0 or not land_river[dest]:
                walk_to_sea(path)
                break
            if dest in seen:
                break
            path.append(dest)
            seen.add(dest)
            if upstream[dest] > 1 and dominant[dest] != current:
                break
            current = dest
        return path

    lines = []
    for index in range(count):
        if land_river[index] and upstream[index] == 0:
            path = follow(index)
            if len(path) >= 8:
                lines.append((path, float(acc[path[-1]])))
    return lines


def to_xy(index: int, width: int) -> tuple[float, float]:
    y, x = divmod(index, width)
    return ((x + 0.5) * SCALE, (y + 0.5) * SCALE)


def land_at(ids: np.ndarray, x: float, y: float) -> bool:
    if x < WEST or y < 0 or y >= ids.shape[0] * SCALE or x >= ids.shape[1] * SCALE:
        return False
    return int(ids[int(y) // SCALE, int(x) // SCALE]) != 0


def closest(points, x: float, y: float):
    best = points[0]
    best_d = 1e18
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1.0
        tee = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
        px, py = ax + dx * tee, ay + dy * tee
        dist = (px - x) ** 2 + (py - y) ** 2
        if dist < best_d:
            best_d = dist
            best = (px, py)
    return best


def pull_inland(x, y, ax, ay, ids):
    best = (ax, ay)
    lo, hi = 0.0, 1.0
    for _ in range(8):
        mid = (lo + hi) * 0.5
        mx = ax + (x - ax) * mid
        my = ay + (y - ay) * mid
        if land_at(ids, mx, my):
            best = (mx, my)
            lo = mid
        else:
            hi = mid
    return best


def turn_angle(a, b, c) -> float:
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0
    dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(dot))


def smooth_noise(seed: int, distance: float) -> float:
    cell = distance / 72.0
    base = math.floor(cell)
    tee = cell - base
    tee = tee * tee * (3.0 - 2.0 * tee)
    return noise(hash32(seed, int(base))) * (1.0 - tee) + noise(hash32(seed, int(base) + 1)) * tee


def sample_line(points, step: float):
    if len(points) < 2:
        return list(points)
    dense = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dist = math.hypot(bx - ax, by - ay)
        steps = max(1, int(math.ceil(dist / step)))
        for index in range(1, steps + 1):
            tee = index / steps
            dense.append((ax + (bx - ax) * tee, ay + (by - ay) * tee))
    return dense


def chaikin(points, ids, passes: int):
    current = list(points)
    for _ in range(passes):
        if len(current) < 3:
            break
        nxt = [current[0]]
        for (ax, ay), (bx, by) in zip(current, current[1:]):
            qx, qy = 0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by
            rx, ry = 0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by
            if not land_at(ids, qx, qy):
                qx, qy = pull_inland(qx, qy, ax, ay, ids)
            if not land_at(ids, rx, ry):
                rx, ry = pull_inland(rx, ry, bx, by, ids)
            nxt.append((qx, qy))
            nxt.append((rx, ry))
        nxt.append(current[-1])
        current = nxt
    return current


def meander_span(points, river_id, ids, foreign):
    if len(points) < 2:
        return list(points)
    dense = sample_line(points, 9.0)
    if len(dense) < 3:
        return dense
    lengths = [0.0]
    for (ax, ay), (bx, by) in zip(dense, dense[1:]):
        lengths.append(lengths[-1] + math.hypot(bx - ax, by - ay))
    total = lengths[-1] or 1.0
    ends = (points[0], points[-1])
    # Big enough that a straight drainage run reads as a river, not a ruler line.
    amp = min(22.0, max(8.0, total * 0.09))
    bent = [dense[0]]
    for index in range(1, len(dense) - 1):
        window = math.sin(math.pi * lengths[index] / total) ** 0.65
        ax, ay = dense[index - 1]
        bx, by = dense[index + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        px, py = -dy / length, dx / length
        sway = smooth_noise(river_id + 17, lengths[index])
        sway += 0.45 * smooth_noise(river_id + 91, lengths[index] * 2.3)
        ox, oy = dense[index]
        placed = (ox, oy)
        for scale in (1.0, 0.7, 0.4, 0.0):
            nx = ox + px * sway * amp * window * scale
            ny = oy + py * sway * amp * window * scale
            if not land_at(ids, nx, ny):
                continue
            back = closest(points, nx, ny)
            if math.hypot(nx - back[0], ny - back[1]) > 26.0:
                continue
            if foreign(river_id, nx, ny, ends):
                continue
            placed = (nx, ny)
            break
        bent.append(placed)
    bent.append(dense[-1])
    curved = chaikin(bent, ids, 4)
    curved[0] = points[0]
    curved[-1] = points[-1]
    return curved


def curve_river(points, river_id, ids, foreign, pins):
    spans = [[points[0]]]
    for point in points[1:]:
        spans[-1].append(point)
        key = (round(point[0], 1), round(point[1], 1))
        if key in pins and point is not points[-1]:
            spans.append([point])
    curved = []
    for span in spans:
        piece = meander_span(span, river_id, ids, foreign)
        if curved and piece:
            curved.extend(piece[1:])
        else:
            curved.extend(piece)
    if curved:
        curved[0] = points[0]
        curved[-1] = points[-1]
    return curved


def smooth_angles(coords, ids, pins):
    pts = [tuple(point) for point in coords]
    stuck: set[int] = set()
    for _ in range(30):
        worst = 0.0
        at = None
        for index in range(1, len(pts) - 1):
            if index in stuck:
                continue
            span_a = math.hypot(pts[index][0] - pts[index - 1][0], pts[index][1] - pts[index - 1][1])
            span_b = math.hypot(pts[index + 1][0] - pts[index][0], pts[index + 1][1] - pts[index][1])
            if span_a < 4 or span_b < 4:
                continue
            angle = turn_angle(pts[index - 1], pts[index], pts[index + 1])
            if angle > worst:
                worst = angle
                at = index
        if at is None or worst <= 50:
            break
        index = at
        key = (round(pts[index][0], 1), round(pts[index][1], 1))
        if key not in pins:
            mx = (pts[index - 1][0] + pts[index + 1][0]) * 0.5
            my = (pts[index - 1][1] + pts[index + 1][1]) * 0.5
            nx = pts[index][0] * 0.25 + mx * 0.75
            ny = pts[index][1] * 0.25 + my * 0.75
            if land_at(ids, nx, ny):
                pts[index] = (nx, ny)
                stuck.clear()
                continue
            stuck.add(index)
            continue
        px, py = pts[index]
        ax, ay = pts[index - 1]
        bx, by = pts[index + 1]
        inx, iny = px - ax, py - ay
        in_len = math.hypot(inx, iny) or 1.0
        out_len = math.hypot(bx - px, by - py) or 1.0
        moved = False
        if (round(bx, 1), round(by, 1)) not in pins:
            nx = bx * 0.65 + (px + inx / in_len * out_len) * 0.35
            ny = by * 0.65 + (py + iny / in_len * out_len) * 0.35
            if land_at(ids, nx, ny):
                pts[index + 1] = (nx, ny)
                moved = True
        if (round(ax, 1), round(ay, 1)) not in pins:
            nx = ax * 0.65 + (px - (bx - px) / out_len * in_len) * 0.35
            ny = ay * 0.65 + (py - (by - py) / out_len * in_len) * 0.35
            if land_at(ids, nx, ny):
                pts[index - 1] = (nx, ny)
                moved = True
        if moved:
            stuck.clear()
        else:
            stuck.add(index)
    return [list(point) for point in pts]


def drop_parallel(features) -> list:
    """Remove a river that merely runs beside a stronger one."""
    kept = []
    grid = defaultdict(list)
    ranked = sorted(features, key=lambda feature: -float(feature["properties"].get("flow") or 0))
    for feature in ranked:
        coords = feature["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        beside = 0
        checked = 0
        for x, y in coords[1:-1]:
            checked += 1
            cx, cy = int(x) // 16, int(y) // 16
            hit = False
            for ix in range(cx - 1, cx + 2):
                for iy in range(cy - 1, cy + 2):
                    for ox, oy in grid.get((ix, iy), ()):
                        if (ox - x) ** 2 + (oy - y) ** 2 <= 18 * 18:
                            hit = True
            if hit:
                beside += 1
        if checked >= 6 and beside / checked > 0.55:
            continue
        kept.append(feature)
        for x, y in coords:
            grid[(int(x) // 16, int(y) // 16)].append((x, y))
    return kept


def snap_ends(features) -> None:
    """Pull a loose end onto a nearby river so a tributary actually meets its stem."""
    grid = defaultdict(list)
    for index, feature in enumerate(features):
        for x, y in feature["geometry"]["coordinates"]:
            grid[(int(x) // 16, int(y) // 16)].append((x, y, index))
    for index, feature in enumerate(features):
        coords = feature["geometry"]["coordinates"]
        for end in (0, -1):
            x, y = coords[end]
            shared = False
            best = None
            best_d = 14.0
            cx, cy = int(x) // 16, int(y) // 16
            for ix in range(cx - 1, cx + 2):
                for iy in range(cy - 1, cy + 2):
                    for ox, oy, other in grid.get((ix, iy), ()):
                        if other == index:
                            continue
                        dist = math.hypot(ox - x, oy - y)
                        if dist < 0.4:
                            shared = True
                        elif dist < best_d:
                            best_d = dist
                            best = (ox, oy)
            if not shared and best is not None:
                coords[end] = [round(best[0], 1), round(best[1], 1)]


def accept(features, ids) -> list[str]:
    problems = []
    for feature in features:
        coords = feature["geometry"]["coordinates"]
        for (ax, ay), (bx, by) in zip(coords, coords[1:]):
            if math.hypot(bx - ax, by - ay) > 150:
                problems.append(f"river {feature['properties']['id']} has a long straight span")
                break
        for index in range(1, len(coords) - 1):
            n1 = math.hypot(coords[index][0] - coords[index - 1][0], coords[index][1] - coords[index - 1][1])
            n2 = math.hypot(coords[index + 1][0] - coords[index][0], coords[index + 1][1] - coords[index][1])
            if n1 < 4 or n2 < 4:
                continue
            if turn_angle(coords[index - 1], coords[index], coords[index + 1]) > 58:
                problems.append(f"river {feature['properties']['id']} turns sharply")
                break
        for x, y in coords[:-1]:
            if x < WEST or not land_at(ids, x, y):
                problems.append(f"river {feature['properties']['id']} leaves the land")
                break
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate drainage rivers for the published map.")
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--threshold", type=int, default=140)
    parser.add_argument("--keep-paint", action="store_true")
    args = parser.parse_args()
    ids = load_ids()
    height, width = ids.shape
    log("Reading the old river paint for a soft bias…")
    bias = painted_bias(height, width)
    log(f"bias cells {int(bias.sum())}")
    elev, land, outlets = build_height(ids, bias, args.seed)
    log("Filling sinks…")
    filled, down = fill_sinks(elev, land, outlets)
    log("Flowing downhill…")
    down = flow(filled, land, outlets, down)
    acc = accumulate(down, land)
    distances = sea_distance(ids, land)
    river = land.ravel() & (acc >= args.threshold)
    river, down = thin_channels(river, down, acc, width)
    log(f"river cells {int(river.sum())} threshold {args.threshold}")
    raw = trace_tree(down, river, acc, distances, width, ids)
    log(f"branches {len(raw)}")
    guides = []
    for river_id, (path, flow_at_mouth) in enumerate(raw):
        guides.append((river_id, [to_xy(index, width) for index in path], flow_at_mouth))
    grid = defaultdict(list)
    pins = set()
    for river_id, points, _flow in guides:
        pins.add((round(points[0][0], 1), round(points[0][1], 1)))
        pins.add((round(points[-1][0], 1), round(points[-1][1], 1)))
        for x, y in points:
            grid[(int(x) // 8, int(y) // 8)].append((river_id, x, y))

    def foreign(river_id, x, y, ends):
        if min(math.hypot(x - ex, y - ey) for ex, ey in ends) < 12:
            return False
        cx, cy = int(x) // 8, int(y) // 8
        for ix in range(cx - 1, cx + 2):
            for iy in range(cy - 1, cy + 2):
                for other, ox, oy in grid.get((ix, iy), ()):
                    if other == river_id:
                        continue
                    if (ox - x) ** 2 + (oy - y) ** 2 <= 36:
                        return True
        return False

    features = []
    for river_id, points, flow_at_mouth in guides:
        curved = curve_river(points, river_id, ids, foreign, pins)
        curved = smooth_angles(curved, ids, pins)
        deduped = [curved[0]]
        for point in curved[1:]:
            key = (round(point[0], 1), round(point[1], 1))
            if key in pins or math.hypot(point[0] - deduped[-1][0], point[1] - deduped[-1][1]) >= 3.5:
                deduped.append(point)
        if deduped[-1] != curved[-1]:
            deduped.append(curved[-1])
        coords = [[round(x, 1), round(y, 1)] for x, y in deduped]
        if len(coords) < 2 or any(point[0] < WEST for point in coords):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"id": river_id, "flow": round(flow_at_mouth, 1)},
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        )
    for feature in features:
        feature["geometry"]["coordinates"] = smooth_angles(feature["geometry"]["coordinates"], ids, pins)
    features = drop_parallel(features)
    snap_ends(features)
    features = [
        feature
        for feature in features
        if all(land_at(ids, x, y) for x, y in feature["geometry"]["coordinates"][:-1])
    ]
    problems = accept(features, ids)
    if problems:
        log(f"acceptance issues {len(problems)}")
        for line in problems[:12]:
            log("  " + line)
    out = ROOT / "data" / "rivers.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")) + "\n")
    log(f"wrote {len(features)} rivers")
    if not args.keep_paint:
        log("Deleting the old river paint at every zoom…")
        log(f"repainted {erase_paint()} pixels, then {erase_wide(ids)} darker strokes")
    if problems:
        raise SystemExit(f"{len(problems)} acceptance checks failed")


if __name__ == "__main__":
    main()
