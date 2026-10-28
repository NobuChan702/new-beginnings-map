#!/usr/bin/env python3
"""Turn painted terrain rivers into stable meandering curves.

The terrain tiles draw many rivers as short straight strokes. This traces
those strokes, keeps mouths, sources, and junctions fixed, bends the long
spans inside the original corridor, and smooths the result. The painted
pixels are then lifted out of the tiles so the vector rivers are the ones
on the map.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WEST = 3592
WIDTH = 12000
HEIGHT = 5600
TILE = 256


def log(msg: str) -> None:
    print(msg, flush=True)


def river_mask(tile: np.ndarray) -> np.ndarray:
    image = tile.astype(np.int16)
    red, green, blue = image[:, :, 0], image[:, :, 1], image[:, :, 2]
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


def collect_pixels() -> np.ndarray:
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    root = ROOT / "tiles" / "terrain" / "6"
    for path in root.glob("*/*.webp"):
        tx = int(path.parent.name)
        ty = int(path.stem)
        tile = np.asarray(Image.open(path).convert("RGB"))
        hit = river_mask(tile)
        if not hit.any():
            continue
        ys, xs = np.nonzero(hit)
        gy = ty * TILE + ys
        gx = tx * TILE + xs
        ok = (gy >= 0) & (gy < HEIGHT) & (gx >= WEST) & (gx < WIDTH)
        mask[gy[ok], gx[ok]] = 1
    return mask


def thin(mask: np.ndarray) -> np.ndarray:
    work = np.pad(mask.astype(bool), 1)
    for _ in range(6):
        changed = False
        for step in (0, 1):
            ys, xs = np.nonzero(work)
            remove = []
            for y, x in zip(ys.tolist(), xs.tolist()):
                if y == 0 or x == 0 or y == work.shape[0] - 1 or x == work.shape[1] - 1:
                    continue
                n = [
                    work[y - 1, x],
                    work[y - 1, x + 1],
                    work[y, x + 1],
                    work[y + 1, x + 1],
                    work[y + 1, x],
                    work[y + 1, x - 1],
                    work[y, x - 1],
                    work[y - 1, x - 1],
                ]
                count = int(sum(bool(item) for item in n))
                if count < 2 or count > 6:
                    continue
                transitions = sum(1 for i in range(8) if (not n[i]) and n[(i + 1) % 8])
                if transitions != 1:
                    continue
                if step == 0 and (n[0] and n[2] and n[4] or n[2] and n[4] and n[6]):
                    continue
                if step == 1 and (n[0] and n[2] and n[6] or n[0] and n[4] and n[6]):
                    continue
                remove.append((y, x))
            if remove:
                changed = True
                for y, x in remove:
                    work[y, x] = False
        if not changed:
            break
    return work[1:-1, 1:-1]


def neighbors(point: tuple[int, int], points: set[tuple[int, int]]) -> list[tuple[int, int]]:
    x, y = point
    found = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if not dx and not dy:
                continue
            nxt = (x + dx, y + dy)
            if nxt in points:
                found.append(nxt)
    return found


def trace(skel: np.ndarray) -> list[list[tuple[int, int]]]:
    ys, xs = np.nonzero(skel)
    points = {(int(x), int(y)) for x, y in zip(xs, ys)}
    nbrs = {point: neighbors(point, points) for point in points}
    used = set()

    def edge(a, b):
        return (a, b) if a <= b else (b, a)

    def walk(start, nxt):
        line = [start, nxt]
        used.add(edge(start, nxt))
        prev, cur = start, nxt
        while True:
            if len(nbrs[cur]) != 2:
                break
            onward = [item for item in nbrs[cur] if item != prev]
            if len(onward) != 1:
                break
            nxt = onward[0]
            if edge(cur, nxt) in used:
                break
            used.add(edge(cur, nxt))
            line.append(nxt)
            prev, cur = cur, nxt
        return line

    lines = []
    for point, near in nbrs.items():
        if len(near) != 1:
            continue
        for nxt in near:
            if edge(point, nxt) not in used:
                lines.append(walk(point, nxt))
    for point, near in nbrs.items():
        for nxt in near:
            if edge(point, nxt) not in used:
                lines.append(walk(point, nxt))
    return [line for line in lines if len(line) >= 2]


def rdp(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    ax, ay = points[0]
    bx, by = points[-1]
    length = math.hypot(bx - ax, by - ay) or 1.0
    worst = 0.0
    index = 0
    for i, (x, y) in enumerate(points[1:-1], start=1):
        dist = abs((by - ay) * x - (bx - ax) * y + bx * ay - by * ax) / length
        if dist > worst:
            worst = dist
            index = i
    if worst <= epsilon:
        return [points[0], points[-1]]
    return rdp(points[: index + 1], epsilon)[:-1] + rdp(points[index:], epsilon)


def subdivide(points: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points
    out = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dist = math.hypot(bx - ax, by - ay)
        pieces = max(1, int(math.ceil(dist / step)))
        for i in range(1, pieces):
            t = i / pieces
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        out.append((bx, by))
    return out


def hash32(*parts: int) -> int:
    value = 2166136261
    for part in parts:
        value ^= part & 0xFFFFFFFF
        value = (value * 16777619) & 0xFFFFFFFF
    return value


def unit_noise(seed: int) -> float:
    value = hash32(seed, 0x9E3779B9)
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    return (value / 4294967295) * 2 - 1


def segment_span(points: list[tuple[float, float]], x: float, y: float) -> float:
    best = 10.0
    best_d = 1e18
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1.0
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
        px, py = ax + dx * t, ay + dy * t
        dist = (px - x) ** 2 + (py - y) ** 2
        if dist < best_d:
            best_d = dist
            best = math.hypot(dx, dy)
    return best


def closest_on(points: list[tuple[float, float]], x: float, y: float) -> tuple[float, float]:
    best = points[0]
    best_d = 1e18
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length2))
        px, py = ax + dx * t, ay + dy * t
        dist = (px - x) ** 2 + (py - y) ** 2
        if dist < best_d:
            best_d = dist
            best = (px, py)
    return best


def chaikin(points: list[tuple[float, float]], iterations: int = 2) -> list[tuple[float, float]]:
    for _ in range(iterations):
        if len(points) < 3:
            return points
        nxt = [points[0]]
        for (ax, ay), (bx, by) in zip(points, points[1:]):
            nxt.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
            nxt.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
        nxt.append(points[-1])
        points = nxt
    return points


class Land:
    def __init__(self) -> None:
        image = np.asarray(Image.open(ROOT / "data" / "lookup.png"))
        self.ids = image[:, :, 0].astype(np.uint16) + (image[:, :, 1].astype(np.uint16) << 8)

    def land(self, x: float, y: float) -> bool:
        if x < WEST or y < 0 or x >= WIDTH or y >= HEIGHT:
            return False
        lx = int(x) // 4
        ly = int(y) // 4
        if ly < 0 or lx < 0 or ly >= self.ids.shape[0] or lx >= self.ids.shape[1]:
            return False
        return int(self.ids[ly, lx]) != 0


def meander(points: list[tuple[float, float]], river_id: int, land: Land, foreign) -> list[tuple[float, float]]:
    simple = rdp([(float(x), float(y)) for x, y in points], 1.15)
    if len(simple) < 2:
        return simple
    dense = subdivide(simple, 9.0)
    if len(dense) < 3:
        return dense
    ends = (dense[0], dense[-1])
    bent = [dense[0]]
    for index in range(1, len(dense) - 1):
        ax, ay = dense[index - 1]
        bx, by = dense[index + 1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        px, py = -dy / length, dx / length
        ox, oy = dense[index]
        span = segment_span(simple, ox, oy)
        amp = min(12.0, max(3.0, 0.16 * span))
        sway = unit_noise(hash32(river_id, index, int(ox), int(oy)))
        placed = (ox, oy)
        for scale in (1.0, 0.55, 0.25, 0.0):
            nx = ox + px * sway * amp * scale
            ny = oy + py * sway * amp * scale
            if not land.land(nx, ny):
                continue
            back = closest_on(simple, nx, ny)
            if math.hypot(nx - back[0], ny - back[1]) > amp + 1.5:
                continue
            if foreign(river_id, nx, ny, ends):
                continue
            placed = (nx, ny)
            break
        bent.append(placed)
    bent.append(dense[-1])
    smooth = chaikin(bent, 2)
    clamped = [smooth[0]]
    for x, y in smooth[1:-1]:
        if land.land(x, y) and not foreign(river_id, x, y, ends):
            back = closest_on(simple, x, y)
            if math.hypot(x - back[0], y - back[1]) <= 14:
                clamped.append((x, y))
                continue
        clamped.append(closest_on(simple, x, y))
    clamped.append(smooth[-1])
    return clamped


def round_point(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 1), round(point[1], 1)]


def erase_paint(mask: np.ndarray) -> int:
    changed = 0
    root = ROOT / "tiles" / "terrain" / "6"
    dilated = mask.copy()
    padded = np.pad(mask, 2)
    for dy in range(5):
        for dx in range(5):
            dilated = np.maximum(dilated, padded[dy : dy + mask.shape[0], dx : dx + mask.shape[1]])
    ys, xs = np.nonzero(dilated)
    by_tile = defaultdict(list)
    for x, y in zip(xs.tolist(), ys.tolist()):
        by_tile[(x // TILE, y // TILE)].append((x, y))
    for (tx, ty), pixels in by_tile.items():
        path = root / str(tx) / f"{ty}.webp"
        if not path.is_file():
            continue
        tile = np.asarray(Image.open(path).convert("RGB")).copy()
        local = np.zeros(tile.shape[:2], bool)
        for x, y in pixels:
            lx, ly = x - tx * TILE, y - ty * TILE
            if 0 <= ly < tile.shape[0] and 0 <= lx < tile.shape[1]:
                local[ly, lx] = True
        height, width = local.shape
        red = tile[:, :, 0].astype(np.int16)
        green = tile[:, :, 1].astype(np.int16)
        blue = tile[:, :, 2].astype(np.int16)
        stroke = local & (blue > red + 18) & (blue > 140) & (red < 200) & (green > 110)
        for y, x in zip(*np.nonzero(stroke)):
            colors = []
            for dy in range(-5, 6):
                for dx in range(-5, 6):
                    yy, xx = y + dy, x + dx
                    if yy < 0 or xx < 0 or yy >= height or xx >= width or stroke[yy, xx]:
                        continue
                    sample = tile[yy, xx]
                    if int(sample[0]) + int(sample[1]) + int(sample[2]) > 520 and int(sample[2]) < int(sample[0]) + 25:
                        colors.append((int(sample[0]), int(sample[1]), int(sample[2])))
            if not colors:
                continue
            colors.sort()
            tile[y, x] = colors[len(colors) // 2]
            changed += 1
        Image.fromarray(tile).save(path, "WEBP", quality=92, method=4)
    return changed


def main() -> None:
    log("Reading painted rivers…")
    mask = collect_pixels()
    log(f"river pixels {int(mask.sum())}")
    skel = thin(mask)
    lines = trace(skel)
    kept = []
    for line in lines:
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if len(line) >= 8 or span >= 10:
            kept.append(line)
    log(f"strokes {len(kept)}")
    land = Land()
    originals = []
    for index, line in enumerate(kept):
        simple = rdp([(float(x), float(y)) for x, y in line], 1.15)
        originals.append((index, simple))
    grid = defaultdict(list)
    for river_id, points in originals:
        for x, y in points:
            grid[(int(x) // 8, int(y) // 8)].append((river_id, x, y))

    def foreign(river_id: int, x: float, y: float, ends: tuple) -> bool:
        if min(math.hypot(x - ex, y - ey) for ex, ey in ends) < 8:
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
    for river_id, points in originals:
        curved = meander(kept[river_id], river_id, land, foreign)
        # meander already simplifies; pass the traced line
        coords = [round_point(p) for p in curved]
        deduped = [coords[0]]
        for point in coords[1:]:
            if point != deduped[-1]:
                deduped.append(point)
        if len(deduped) < 2:
            continue
        if any(point[0] < WEST for point in deduped):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"id": river_id},
                "geometry": {"type": "LineString", "coordinates": deduped},
            }
        )
    out = ROOT / "data" / "rivers.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")) + "\n")
    log(f"wrote {len(features)} rivers")
    log("Lifting the old paint out of the terrain tiles…")
    log(f"repainted {erase_paint(mask)} pixels")


if __name__ == "__main__":
    main()
