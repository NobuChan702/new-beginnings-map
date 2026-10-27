#!/usr/bin/env python3
"""Rebuild the New Beginnings player map from source/.

Reads the full rasters and nation file (including anything west of the
secrecy line), then writes only player-safe outputs:

- WebP tiles for the fogged political map, the fogged terrain map,
  a depth view whose hidden west is the political fog art, and
  province-outline tiles for published provinces
- data/lookup.png, an eastern-only province id image
- data/world.json and data/meta.json
- data/flat.geojson, eastern province fills plus nation outlines for the
  close zoom (a flat board, not a sharper copy of the raster)
- assets/overview.webp, a low-resolution fogged political image

Provinces that touch x < HIDDEN_WEST_OF are omitted. The removed_americas
block in nations.yaml is ignored. Source files are never copied into the
output. claims.json and infrastructure.geojson are left untouched.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "source"
HIDDEN_WEST_OF = 3592
TILE = 256
LOOKUP_SCALE = 4
WEBP_QUALITY = 75
DEPTH_RGB = {
    0: (22, 24, 27),
    80: (46, 125, 140),
    160: (20, 74, 130),
    240: (11, 26, 74),
}
DEPTH_LABEL = {80: "shallow", 160: "mid", 240: "deep"}
BG = (13, 13, 15)


def log(msg: str) -> None:
    print(msg, flush=True)


def require_source() -> dict[str, Path]:
    names = {
        "political": "political.png",
        "terrain": "terrain.png",
        "province_ids": "province_ids.png",
        "depth": "depth_zones.png",
        "provinces": "provinces.json",
        "nations": "nations.yaml",
        "terrain_data": "terrain_by_province.json",
    }
    paths = {key: SRC / name for key, name in names.items()}
    missing = [str(path.relative_to(ROOT)) for path in paths.values() if not path.exists()]
    if missing:
        raise SystemExit(
            "Missing source files:\n  "
            + "\n  ".join(missing)
            + "\nPlace the inputs in source/ (see README.md) and run this script again."
        )
    return paths


def native_zoom_for(width: int, height: int) -> int:
    need = max(width, height)
    zoom = 0
    while TILE * (1 << zoom) < need:
        zoom += 1
    return zoom


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        arr = np.asarray(im.convert("RGB"))
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise SystemExit(f"{path.name} is not an RGB image")
    return arr


def build_id_raster(path: Path, provinces: list[dict], shape: tuple[int, int]) -> np.ndarray:
    """Map the province-id PNG (rgb field in provinces.json) to uint16 ids."""
    with Image.open(path) as im:
        rgb = np.asarray(im.convert("RGB"))
    if rgb.shape[:2] != shape:
        raise SystemExit(f"province id raster is {rgb.shape[1]}x{rgb.shape[0]}, expected {shape[1]}x{shape[0]}")
    lut = np.zeros(1 << 24, dtype=np.uint16)
    max_id = 0
    for prov in provinces:
        r, g, b = (int(v) for v in prov["rgb"])
        pid = int(prov["id"])
        if pid <= 0 or pid > 65535:
            raise SystemExit(f"province id {pid} does not fit in 16 bits")
        lut[(r << 16) | (g << 8) | b] = pid
        max_id = max(max_id, pid)
    height, width = shape
    ids = np.empty((height, width), dtype=np.uint16)
    for y0 in range(0, height, 256):
        block = rgb[y0 : y0 + 256]
        packed = (
            block[:, :, 0].astype(np.uint32) << 16
        ) | (block[:, :, 1].astype(np.uint32) << 8) | block[:, :, 2].astype(np.uint32)
        ids[y0 : y0 + 256] = lut[packed]
    del rgb, lut
    return ids


def extents(ids: np.ndarray):
    max_id = int(ids.max())
    minx = np.full(max_id + 1, 10**9, np.int32)
    maxx = np.full(max_id + 1, -1, np.int32)
    miny = np.full(max_id + 1, 10**9, np.int32)
    maxy = np.full(max_id + 1, -1, np.int32)
    count = np.zeros(max_id + 1, np.int64)
    height, width = ids.shape
    for y in range(height):
        row = ids[y]
        changes = np.flatnonzero(row[1:] != row[:-1]) + 1
        starts = np.empty(changes.size + 1, np.int32)
        starts[0] = 0
        if changes.size:
            starts[1:] = changes
        ends = np.empty_like(starts)
        if changes.size:
            ends[:-1] = changes
        ends[-1] = width
        colors = row[starts]
        for start, end, pid in zip(starts.tolist(), ends.tolist(), colors.tolist()):
            if pid == 0:
                continue
            count[pid] += end - start
            if start < minx[pid]:
                minx[pid] = start
            last = end - 1
            if last > maxx[pid]:
                maxx[pid] = last
            if y < miny[pid]:
                miny[pid] = y
            if y > maxy[pid]:
                maxy[pid] = y
    return minx, maxx, miny, maxy, count


def assert_fogged(name: str, image: np.ndarray, ids: np.ndarray) -> None:
    west = image[:, :HIDDEN_WEST_OF]
    mean = west.reshape(-1, 3).mean(axis=0)
    if mean[0] < 120 or mean[0] < mean[2] + 15:
        raise SystemExit(
            f"{name} west of x={HIDDEN_WEST_OF} does not look like the fog artwork "
            f"(mean RGB {mean.round(1).tolist()}). Refusing to publish."
        )
    step = 8
    land = ids[::step, :HIDDEN_WEST_OF:step] > 0
    bright = image[::step, :HIDDEN_WEST_OF:step].mean(axis=2)
    if land.any() and (~land).any():
        contrast = abs(float(bright[land].mean()) - float(bright[~land].mean()))
        log(f"  {name} fog land/sea brightness contrast {contrast:.2f}")
        if contrast > 12:
            raise SystemExit(
                f"{name} still shows land against sea under the western fog "
                f"(contrast {contrast:.1f}). Refusing to publish unfogged imagery."
            )
    else:
        log(f"  {name} west mean RGB {mean.round(1).tolist()}")


def shallowest_water(ids: np.ndarray, depth: np.ndarray) -> dict[int, int]:
    """Shallowest ocean depth touching each province, east of the secrecy line."""
    height, width = ids.shape
    best: dict[int, int] = {}
    land = ids > 0
    directions = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    for dy, dx in directions:
        neigh_depth = np.zeros_like(depth)
        neigh_id = np.zeros_like(ids)
        src_y = slice(max(dy, 0), height + min(dy, 0))
        src_x = slice(max(dx, 0), width + min(dx, 0))
        dst_y = slice(max(-dy, 0), height + min(-dy, 0))
        dst_x = slice(max(-dx, 0), width + min(-dx, 0))
        neigh_depth[dst_y, dst_x] = depth[src_y, src_x]
        neigh_id[dst_y, dst_x] = ids[src_y, src_x]
        mask = land & (neigh_depth > 0) & (neigh_id == 0)
        # Neighbor column x+dx must sit on the published side of the line.
        limit = HIDDEN_WEST_OF - dx
        if limit > 0:
            mask[:, : min(width, limit)] = False
        if not mask.any():
            continue
        pids = ids[mask].astype(np.int32)
        deps = neigh_depth[mask].astype(np.int16)
        order = np.argsort(pids, kind="stable")
        pids = pids[order]
        deps = deps[order]
        cuts = np.flatnonzero(pids[1:] != pids[:-1]) + 1
        starts = np.r_[0, cuts]
        mins = np.minimum.reduceat(deps, starts)
        for pid, value in zip(pids[starts].tolist(), mins.tolist()):
            prev = best.get(pid)
            if prev is None or value < prev:
                best[pid] = int(value)
    return best


def depth_word(value: int | None) -> str | None:
    if value is None:
        return None
    if value in DEPTH_LABEL:
        return DEPTH_LABEL[value]
    if value <= 80:
        return "shallow"
    if value <= 160:
        return "mid"
    return "deep"


def short_name(name: str) -> str:
    for suffix in (" (major)", " (regional)", " (minor)", " (NPC)", " (npc)"):
        if name.endswith(suffix):
            return name[: -len(suffix)].strip() or name
    return name


def as_num(value) -> int | float:
    number = float(value)
    rounded = round(number)
    if abs(number - rounded) < 1e-6:
        return int(rounded)
    return round(number, 1)


def write_lookup(ids: np.ndarray, path: Path) -> tuple[int, int]:
    scale = LOOKUP_SCALE
    sampled = ids[scale // 2 :: scale, scale // 2 :: scale].copy()
    # A lookup column is published only when its whole source block is east of the line.
    west_cols = (HIDDEN_WEST_OF - 1) // scale + 1
    sampled[:, :west_cols] = 0
    red = (sampled & 255).astype(np.uint8)
    green = ((sampled >> 8) & 255).astype(np.uint8)
    blue = np.zeros_like(red)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([red, green, blue]), "RGB").save(path, optimize=True)
    return int(sampled.shape[1]), int(sampled.shape[0])


def edge_mask(ids: np.ndarray) -> np.ndarray:
    edge = np.zeros(ids.shape, dtype=bool)
    left = ids[:, :-1]
    right = ids[:, 1:]
    horiz = (left != right) & ((left != 0) | (right != 0))
    edge[:, :-1] |= horiz
    edge[:, 1:] |= horiz
    up = ids[:-1, :]
    down = ids[1:, :]
    vert = (up != down) & ((up != 0) | (down != 0))
    edge[:-1, :] |= vert
    edge[1:, :] |= vert
    edge[:, :HIDDEN_WEST_OF] = False
    return edge


def pool_any(src: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    src_h, src_w = src.shape
    if src_h == out_h and src_w == out_w:
        return src
    if out_h > 0 and out_w > 0 and src_h % out_h == 0 and src_w % out_w == 0:
        sy = src_h // out_h
        sx = src_w // out_w
        view = src[: out_h * sy, : out_w * sx].reshape(out_h, sy, out_w, sx)
        return view.any(axis=(1, 3))
    out = np.zeros((out_h, out_w), dtype=bool)
    ys = np.linspace(0, src_h, out_h + 1).astype(np.int32)
    xs = np.linspace(0, src_w, out_w + 1).astype(np.int32)
    for i in range(out_h):
        if ys[i] == ys[i + 1]:
            continue
        row = src[ys[i] : ys[i + 1]]
        for j in range(out_w):
            if xs[j] == xs[j + 1]:
                continue
            block = row[:, xs[j] : xs[j + 1]]
            if block.size and block.any():
                out[i, j] = True
    return out


def save_webp(arr: np.ndarray, path: Path, *, lossless: bool) -> None:
    mode = "RGBA" if arr.shape[2] == 4 else "RGB"
    im = Image.fromarray(arr, mode)
    if lossless:
        im.save(path, "WEBP", lossless=True, quality=100, method=0)
    else:
        im.save(path, "WEBP", quality=WEBP_QUALITY, method=2)


def write_blank_tiles() -> tuple[Path, Path]:
    assets = ROOT / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    empty = assets / "empty.webp"
    blank = assets / "blank.webp"
    Image.new("RGB", (TILE, TILE), BG).save(empty, "WEBP", quality=70, method=0)
    Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0)).save(blank, "WEBP", lossless=True, quality=100, method=0)
    return empty, blank


def scaled(arr: np.ndarray, scale: int) -> np.ndarray:
    if scale == 1:
        return arr
    height, width = arr.shape[:2]
    out_w = max(1, int(round(width / scale)))
    out_h = max(1, int(round(height / scale)))
    im = Image.fromarray(arr, "RGB").resize((out_w, out_h), Image.Resampling.LANCZOS)
    return np.asarray(im)


def write_rgb_tiles(arr: np.ndarray, dest: Path, zoom: int) -> int:
    height, width = arr.shape[:2]
    ntx = math.ceil(width / TILE)
    nty = math.ceil(height / TILE)
    count = 0
    for tx in range(ntx):
        column = dest / str(zoom) / str(tx)
        column.mkdir(parents=True, exist_ok=True)
        x0 = tx * TILE
        x1 = min(width, x0 + TILE)
        for ty in range(nty):
            y0 = ty * TILE
            y1 = min(height, y0 + TILE)
            crop = arr[y0:y1, x0:x1]
            if crop.shape[0] != TILE or crop.shape[1] != TILE:
                canvas = np.empty((TILE, TILE, 3), np.uint8)
                canvas[:] = BG
                canvas[: crop.shape[0], : crop.shape[1]] = crop
                crop = canvas
            save_webp(np.ascontiguousarray(crop), column / f"{ty}.webp", lossless=False)
            count += 1
    return count


def write_photo_pyramid(arr: np.ndarray, dest: Path, native: int) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    for zoom in range(native, -1, -1):
        started = time.perf_counter()
        scale = 1 << (native - zoom)
        view = scaled(arr, scale)
        count = write_rgb_tiles(view, dest, zoom)
        log(f"  z{zoom}: {count} tiles, {view.shape[1]}x{view.shape[0]} ({time.perf_counter() - started:.1f}s)")
        del view


def write_depth_pyramid(dep: np.ndarray, political: np.ndarray, dest: Path, native: int) -> None:
    if not np.array_equal(dep[:, :HIDDEN_WEST_OF], political[:, :HIDDEN_WEST_OF]):
        raise SystemExit("depth composite failed to replace the hidden west with fog art")
    if dest.exists():
        shutil.rmtree(dest)
    for zoom in range(native, -1, -1):
        started = time.perf_counter()
        scale = 1 << (native - zoom)
        view = np.array(scaled(dep, scale), copy=True)
        fog = scaled(political, scale)
        cut = min(view.shape[1], int(math.ceil(HIDDEN_WEST_OF / scale)))
        view[:, :cut] = fog[:, :cut]
        if not np.array_equal(view[:, :cut], fog[:, :cut]):
            raise SystemExit(f"depth tiles at zoom {zoom} would leak past the fog line")
        count = write_rgb_tiles(view, dest, zoom)
        log(f"  z{zoom}: {count} tiles ({time.perf_counter() - started:.1f}s)")
        del view, fog


def write_outline_pyramid(edge: np.ndarray, dest: Path, native: int, blank: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    height, width = edge.shape
    for zoom in range(native, -1, -1):
        started = time.perf_counter()
        scale = 1 << (native - zoom)
        out_w = max(1, int(round(width / scale)))
        out_h = max(1, int(round(height / scale)))
        ntx = math.ceil(out_w / TILE)
        nty = math.ceil(out_h / TILE)
        drawn = 0
        for tx in range(ntx):
            column = dest / str(zoom) / str(tx)
            column.mkdir(parents=True, exist_ok=True)
            ox0 = tx * TILE
            ox1 = min(out_w, ox0 + TILE)
            sx0 = min(width, int(round(ox0 * scale)))
            sx1 = min(width, int(round(ox1 * scale)))
            for ty in range(nty):
                path = column / f"{ty}.webp"
                oy0 = ty * TILE
                oy1 = min(out_h, oy0 + TILE)
                sy0 = min(height, int(round(oy0 * scale)))
                sy1 = min(height, int(round(oy1 * scale)))
                src = edge[sy0:sy1, sx0:sx1]
                th = oy1 - oy0
                tw = ox1 - ox0
                if src.size == 0 or not src.any():
                    shutil.copyfile(blank, path)
                    continue
                pooled = pool_any(src, th, tw)
                if not pooled.any():
                    shutil.copyfile(blank, path)
                    continue
                rgba = np.zeros((TILE, TILE, 4), np.uint8)
                ys, xs = np.nonzero(pooled)
                rgba[ys, xs, 0] = 230
                rgba[ys, xs, 1] = 230
                rgba[ys, xs, 2] = 234
                rgba[ys, xs, 3] = 220
                save_webp(rgba, path, lossless=True)
                drawn += 1
        log(f"  z{zoom}: {drawn} outlined tiles / {ntx * nty} ({time.perf_counter() - started:.1f}s)")


def colorize_depth(path: Path, shape: tuple[int, int]) -> np.ndarray:
    with Image.open(path) as im:
        depth = np.asarray(im.convert("L"))
    if depth.shape != shape:
        raise SystemExit(f"depth raster is {depth.shape[1]}x{depth.shape[0]}, expected {shape[1]}x{shape[0]}")
    rgb = np.empty(depth.shape + (3,), np.uint8)
    rgb[:] = DEPTH_RGB[0]
    for value, color in DEPTH_RGB.items():
        if value == 0:
            continue
        rgb[depth == value] = color
    unexpected = ~np.isin(depth, list(DEPTH_RGB))
    if unexpected.any():
        log(f"  warning: unexpected depth values {np.unique(depth[unexpected])[:8].tolist()}")
    return depth, rgb


def build_world(paths: dict[str, Path], ids: np.ndarray, allowed: set[int], boxes: dict[int, dict]) -> dict:
    provinces = json.loads(paths["provinces"].read_text(encoding="utf-8"))
    terrain_doc = json.loads(paths["terrain_data"].read_text(encoding="utf-8"))
    doc = yaml.safe_load(paths["nations"].read_text(encoding="utf-8"))
    removed = doc.pop("removed_americas", None) or []
    removed_names = {str(item["name"]) for item in removed if isinstance(item, dict) and "name" in item}
    del removed
    doc.pop("palette", None)
    doc.pop("unclaimed_provinces", None)

    by_id = {int(p["id"]): p for p in provinces["provinces"]}
    terrain_by_id = {int(t["province"]): t for t in terrain_doc["provinces"]}
    km = float(provinces.get("km_per_px") or terrain_doc.get("km_per_px") or 5.5)

    owner: dict[int, int] = {}
    raw_nations = doc.get("nations") or []
    for nation in raw_nations:
        nid = int(nation["id"])
        for pid in nation.get("provinces") or []:
            pid = int(pid)
            if pid in owner and owner[pid] != nid:
                log(f"  province {pid} is listed in nations {owner[pid]} and {nid}; keeping {nid}")
            owner[pid] = nid

    published_nations = []
    for nation in raw_nations:
        nid = int(nation["id"])
        kept = []
        dropped = 0
        for pid in nation.get("provinces") or []:
            pid = int(pid)
            if pid not in allowed or owner.get(pid) != nid:
                if pid not in allowed:
                    dropped += 1
                continue
            kept.append(pid)
        if dropped:
            log(f"  {nation.get('name')} dropped {dropped} hidden provinces")
        if not kept:
            log(f"  omitting {nation.get('name')} (no published provinces)")
            continue
        kept.sort()
        area = 0.0
        minx = miny = 10**9
        maxx = maxy = -1
        for pid in kept:
            area += float(by_id[pid]["area_km2"])
            box = boxes[pid]
            minx = min(minx, box["minx"])
            miny = min(miny, box["miny"])
            maxx = max(maxx, box["maxx"])
            maxy = max(maxy, box["maxy"])
        capital = int(nation["capital"])
        if capital not in kept:
            log(f"  {nation.get('name')} capital {capital} is not a published province; using {kept[0]}")
            capital = kept[0]
        centroid = by_id[capital]["centroid"]
        cx, cy = float(centroid[0]), float(centroid[1])
        if cx < HIDDEN_WEST_OF:
            cx = (minx + maxx) / 2
            cy = (miny + maxy) / 2
        tier = str(nation.get("type") or nation.get("tier") or "")
        name = str(nation["name"])
        entry = {
            "id": nid,
            "name": name,
            "short_name": short_name(name),
            "tier": tier,
            "color": str(nation.get("color") or "#E6E6EA"),
            "capital_province": capital,
            "capital_xy": [round(cx, 1), round(cy, 1)],
            "bbox": [int(minx), int(miny), int(maxx), int(maxy)],
            "province_count": len(kept),
            "area_km2": as_num(area),
            "provinces": kept,
        }
        former = nation.get("former_names") or nation.get("aliases")
        if former:
            entry["former_names"] = [str(item) for item in former if str(item).strip()]
        published_nations.append(entry)

    nation_of = {}
    for nation in published_nations:
        for pid in nation["provinces"]:
            nation_of[pid] = nation["id"]

    published_provinces = {}
    for pid in sorted(allowed):
        prov = by_id.get(pid)
        terr = terrain_by_id.get(pid, {})
        mix = terr.get("mix_pct") or {}
        mix_pairs = sorted(
            ((str(k), as_num(v)) for k, v in mix.items() if float(v) >= 1),
            key=lambda item: -float(item[1]),
        )[:5]
        box = boxes[pid]
        centroid = (prov or {}).get("centroid")
        if centroid and float(centroid[0]) >= HIDDEN_WEST_OF:
            xy = [round(float(centroid[0]), 1), round(float(centroid[1]), 1)]
        else:
            xy = [
                round((int(box["minx"]) + int(box["maxx"])) / 2, 1),
                round((int(box["miny"]) + int(box["maxy"])) / 2, 1),
            ]
        record = {
            "area_km2": as_num(prov["area_km2"] if prov else terr.get("area_km2") or 0),
            "terrain": terr.get("dominant") or "unknown",
            "mix": [[name, pct] for name, pct in mix_pairs],
            "river": bool(terr.get("river")),
            "coastal": bool(terr.get("coastal")),
            "water": depth_word(box.get("water")),
            "nation_id": nation_of.get(pid),
            "bbox": [int(box["minx"]), int(box["miny"]), int(box["maxx"]), int(box["maxy"])],
            "xy": xy,
        }
        published_provinces[str(pid)] = record

    published_nations.sort(key=lambda n: n["id"])
    return {
        "km_per_px": km,
        "removed_names": removed_names,
        "nations": published_nations,
        "provinces": published_provinces,
    }


def secrecy_check(world: dict, lookup_path: Path, allowed: set[int]) -> None:
    text = (ROOT / "data" / "world.json").read_text(encoding="utf-8")
    if "removed_americas" in text:
        raise SystemExit("secrecy: removed_americas leaked into world.json")
    published_names = {n["name"] for n in world["nations"]} | {n["short_name"] for n in world["nations"]}
    for name in world["removed_names"]:
        if name in published_names:
            continue
        if f'"{name}"' in text:
            raise SystemExit(f"secrecy: hidden name {name} leaked into world.json")
    for key, prov in world["provinces"].items():
        pid = int(key)
        if pid not in allowed:
            raise SystemExit(f"secrecy: province {pid} was published")
        if prov.get("nation_id") is not None and prov["nation_id"] not in {n["id"] for n in world["nations"]}:
            raise SystemExit(f"secrecy: province {pid} points at an unpublished nation")
        bbox = prov.get("bbox") or [HIDDEN_WEST_OF, 0, HIDDEN_WEST_OF, 0]
        xy = prov.get("xy") or [HIDDEN_WEST_OF, 0]
        if int(bbox[0]) < HIDDEN_WEST_OF or float(xy[0]) < HIDDEN_WEST_OF:
            raise SystemExit(f"secrecy: province {pid} anchor sits in the hidden west")
    for nation in world["nations"]:
        for pid in nation["provinces"]:
            if pid not in allowed:
                raise SystemExit(f"secrecy: nation {nation['id']} lists hidden province {pid}")
        if nation["bbox"][0] < HIDDEN_WEST_OF:
            raise SystemExit(f"secrecy: nation {nation['id']} bbox crosses the fog line")
    with Image.open(lookup_path) as im:
        lut = np.asarray(im.convert("RGB"))
    got = lut[:, :, 0].astype(np.uint16) + (lut[:, :, 1].astype(np.uint16) << 8)
    west_cols = (HIDDEN_WEST_OF - 1) // LOOKUP_SCALE + 1
    if int(got[:, :west_cols].sum()) != 0:
        raise SystemExit("secrecy: province lookup has ids west of the fog line")
    stray = set(int(v) for v in np.unique(got)) - {0} - allowed
    if stray:
        sample = sorted(stray)[:8]
        raise SystemExit(f"secrecy: lookup contains unpublished ids {sample}")
    log("  secrecy checks passed")


# Collapse 1px stairs, then round corners locally. Shared borders are smoothed once.
FLAT_SIMPLIFY = 1.6
FLAT_DENSIFY = 4.0
FLAT_CHAIKIN = 2
FLAT_CLEAN = 0.45
UNCLAIMED_COLOR = "#8d8678"
SEA_COLOR = "#0B1A4A"


def _fog_hex(path: Path) -> str:
    """One flat color for the hidden west, sampled from the fog artwork."""
    with Image.open(path) as im:
        right = min(2400, HIDDEN_WEST_OF - 80, im.width)
        bottom = max(1, im.height - 200)
        crop = im.crop((200, 200, max(201, right), bottom))
        small = crop.resize((32, 16), Image.Resampling.BOX)
        arr = np.asarray(small.convert("RGB")).reshape(-1, 3)
    med = np.median(arr, axis=0)
    return "#{:02x}{:02x}{:02x}".format(*(int(v) for v in med))


def _collapse_collinear(pts: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(pts) < 3:
        return pts
    closed = pts[0] == pts[-1]
    seq = list(pts[:-1] if closed else pts)
    out = [seq[0]]
    for point in seq[1:]:
        if out[-1] == point:
            continue
        if len(out) >= 2:
            ax, ay = out[-2]
            bx, by = out[-1]
            cx, cy = point
            if (bx - ax) * (cy - by) == (by - ay) * (cx - bx):
                out[-1] = point
                continue
        out.append(point)
    if not closed:
        return out
    if len(out) >= 3:
        ax, ay = out[-1]
        bx, by = out[0]
        cx, cy = out[1]
        if (bx - ax) * (cy - by) == (by - ay) * (cx - bx):
            out.pop(0)
    if len(out) >= 3:
        ax, ay = out[-2]
        bx, by = out[-1]
        cx, cy = out[0]
        if (bx - ax) * (cy - by) == (by - ay) * (cx - bx):
            out.pop()
    if len(out) >= 3 and out[0] != out[-1]:
        out.append(out[0])
    return out


def _rdp(pts: list[tuple], epsilon: float) -> list[tuple]:
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    limit = epsilon * epsilon
    while stack:
        start, end = stack.pop()
        ax, ay = pts[start]
        bx, by = pts[end]
        dx = bx - ax
        dy = by - ay
        den = dx * dx + dy * dy
        best = -1
        best_d = limit
        for index in range(start + 1, end):
            px, py = pts[index]
            if den == 0:
                dist = (px - ax) ** 2 + (py - ay) ** 2
            else:
                tee = ((px - ax) * dx + (py - ay) * dy) / den
                if tee <= 0:
                    dist = (px - ax) ** 2 + (py - ay) ** 2
                elif tee >= 1:
                    dist = (px - bx) ** 2 + (py - by) ** 2
                else:
                    qx = ax + tee * dx
                    qy = ay + tee * dy
                    dist = (px - qx) ** 2 + (py - qy) ** 2
            if dist > best_d:
                best_d = dist
                best = index
        if best != -1:
            keep[best] = True
            stack.append((start, best))
            stack.append((best, end))
    return [point for point, flag in zip(pts, keep) if flag]


def _rdp_closed(pts: list[tuple], epsilon: float) -> list[tuple]:
    ring = list(pts[:-1] if pts[0] == pts[-1] else pts)
    if len(ring) < 4:
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        return ring
    x0, y0 = ring[0]
    far = max(range(1, len(ring)), key=lambda i: (ring[i][0] - x0) ** 2 + (ring[i][1] - y0) ** 2)
    left = _rdp(ring[: far + 1], epsilon)
    right = _rdp(ring[far:] + [ring[0]], epsilon)
    merged = left[:-1] + right
    if merged[0] != merged[-1]:
        merged.append(merged[0])
    return merged


def _chaikin(pts: list[tuple], iterations: int, closed: bool) -> list[tuple]:
    if iterations <= 0 or len(pts) < 3:
        return pts
    if closed:
        ring = list(pts[:-1] if pts[0] == pts[-1] else pts)
        for _ in range(iterations):
            if len(ring) < 3:
                break
            nxt = []
            count = len(ring)
            for index in range(count):
                ax, ay = ring[index]
                bx, by = ring[(index + 1) % count]
                nxt.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
                nxt.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
            ring = nxt
        if not ring:
            return pts
        return ring + [ring[0]]
    cur = list(pts)
    for _ in range(iterations):
        if len(cur) < 3:
            break
        nxt = [cur[0]]
        for (ax, ay), (bx, by) in zip(cur, cur[1:]):
            nxt.append((0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by))
            nxt.append((0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by))
        nxt.append(cur[-1])
        cur = nxt
    return cur


def _densify(pts: list[tuple], spacing: float, closed: bool) -> list[tuple]:
    """Add points along long segments so corner rounding stays local."""
    if len(pts) < 2 or spacing <= 0:
        return pts
    ring = list(pts[:-1] if closed and pts[0] == pts[-1] else pts)
    if len(ring) < 2:
        return pts
    pairs = list(zip(ring, ring[1:] + ring[:1])) if closed else list(zip(ring, ring[1:]))
    out = [ring[0]]
    for (ax, ay), (bx, by) in pairs:
        dist = math.hypot(bx - ax, by - ay)
        steps = max(1, int(math.ceil(dist / spacing)))
        for step in range(1, steps):
            tee = step / steps
            out.append((ax + (bx - ax) * tee, ay + (by - ay) * tee))
        if not out or out[-1] != (bx, by):
            out.append((bx, by))
    if closed and out[0] != out[-1]:
        out.append(out[0])
    return out


def _smooth_arc(pts: list[tuple[int, int]]) -> list[tuple]:
    if len(pts) < 2:
        return []
    closed = pts[0] == pts[-1] and len(pts) >= 4
    if closed:
        simplified = _rdp_closed(pts, FLAT_SIMPLIFY)
        smoothed = _chaikin(_densify(simplified, FLAT_DENSIFY, True), FLAT_CHAIKIN, True)
        cleaned = _rdp_closed(smoothed, FLAT_CLEAN)
    else:
        simplified = _rdp(pts, FLAT_SIMPLIFY)
        smoothed = _chaikin(_densify(simplified, FLAT_DENSIFY, False), FLAT_CHAIKIN, False)
        cleaned = _rdp(smoothed, FLAT_CLEAN)
    rounded = []
    for x, y in cleaned:
        if x < HIDDEN_WEST_OF:
            if HIDDEN_WEST_OF - x > 2:
                raise SystemExit("secrecy: a smoothed outline stepped into the hidden west")
            x = HIDDEN_WEST_OF
        rx = round(float(x), 2)
        ry = round(float(y), 2)
        if abs(rx - round(rx)) < 1e-6:
            rx = int(round(rx))
        if abs(ry - round(ry)) < 1e-6:
            ry = int(round(ry))
        point = (rx, ry)
        if not rounded or rounded[-1] != point:
            rounded.append(point)
    if closed and rounded and rounded[0] != rounded[-1]:
        rounded.append(rounded[0])
    if len(rounded) < (4 if closed else 2):
        return []
    return rounded


def _boundary_arcs(ids: np.ndarray) -> list[dict]:
    """One smoothed polyline per shared border, oriented with `left` on the left."""
    height, width = ids.shape
    pad = np.zeros((height + 2, width + 2), dtype=ids.dtype)
    pad[1:-1, 1:-1] = ids
    adj: dict[tuple[int, int], list[tuple[tuple[int, int], int, int]]] = defaultdict(list)
    edges: list[tuple[tuple[int, int], tuple[int, int], int, int]] = []

    def add(ax: int, ay: int, bx: int, by: int, left: int, right: int) -> None:
        if left == right:
            return
        start = (ax, ay)
        end = (bx, by)
        if start == end:
            return
        adj[start].append((end, left, right))
        edges.append((start, end, left, right))

    ys, xs = np.nonzero(pad[:, 1:] != pad[:, :-1])
    if len(ys):
        west = pad[ys, xs]
        east = pad[ys, xs + 1]
        for y, x, w_id, e_id in zip(ys.tolist(), xs.tolist(), west.tolist(), east.tolist()):
            # Map-space vertical edge at x, from y-1 to y. Walking down leaves the east id on the left.
            mx = int(x)
            my = int(y) - 1
            add(mx, my, mx, my + 1, int(e_id), int(w_id))
            add(mx, my + 1, mx, my, int(w_id), int(e_id))

    ys, xs = np.nonzero(pad[1:, :] != pad[:-1, :])
    if len(ys):
        north = pad[ys, xs]
        south = pad[ys + 1, xs]
        for y, x, n_id, s_id in zip(ys.tolist(), xs.tolist(), north.tolist(), south.tolist()):
            # Map-space horizontal edge at y, from x-1 to x. Walking right leaves the north id on the left.
            mx = int(x) - 1
            my = int(y)
            add(mx, my, mx + 1, my, int(n_id), int(s_id))
            add(mx + 1, my, mx, my, int(s_id), int(n_id))

    used: set[tuple] = set()
    arcs = []
    for start, nxt, left, right in edges:
        key = (start, nxt, left, right)
        if key in used or left == right:
            continue
        points = [start]
        cur = start
        arriving = nxt
        closed = False
        for _ in range(len(edges) + 2):
            step = (cur, arriving, left, right)
            if step in used:
                break
            used.add(step)
            used.add((arriving, cur, right, left))
            points.append(arriving)
            if arriving == start and len(points) > 2:
                closed = True
                break
            incoming = (arriving[0] - cur[0], arriving[1] - cur[1])
            options = []
            for nb, n_left, n_right in adj[arriving]:
                if n_left == left and n_right == right and (arriving, nb, left, right) not in used:
                    options.append(nb)
            if not options:
                break
            if len(options) == 1:
                chosen = options[0]
            else:
                base = math.atan2(incoming[1], incoming[0])
                best_delta = None
                chosen = options[0]
                for nb in options:
                    delta = (math.atan2(nb[1] - arriving[1], nb[0] - arriving[0]) - base) % (2 * math.pi)
                    if best_delta is None or delta < best_delta:
                        best_delta = delta
                        chosen = nb
            cur = arriving
            arriving = chosen
        points = _collapse_collinear(points)
        smoothed = _smooth_arc(points)
        if smoothed:
            arcs.append({"pts": smoothed, "left": left, "right": right, "closed": closed or smoothed[0] == smoothed[-1]})
    return arcs


def _shoelace(ring: list) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        area += x1 * y2 - x2 * y1
    return area / 2


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            denom = yj - yi
            if denom != 0 and x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def _pick_turn(incoming: tuple[float, float], directed: list[list], choices: list[int]) -> int:
    base = math.atan2(incoming[1], incoming[0])
    best = choices[0]
    best_delta = None
    for index in choices:
        pts = directed[index]
        delta = (math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]) - base) % (2 * math.pi)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = index
    return best


def _rings_for_province(arcs: list[dict], pid: int) -> list[list]:
    directed: list[list] = []
    for arc in arcs:
        pts = arc["pts"]
        if len(pts) < 2:
            continue
        if arc["left"] == pid:
            directed.append(pts)
        elif arc["right"] == pid:
            directed.append(list(reversed(pts)))
    if not directed:
        return []
    starts: dict[tuple, list[int]] = defaultdict(list)
    for index, pts in enumerate(directed):
        starts[(pts[0][0], pts[0][1])].append(index)
    used: set[int] = set()
    rings = []
    for index, pts in enumerate(directed):
        if index in used:
            continue
        if pts[0] == pts[-1] and len(pts) >= 4:
            used.add(index)
            rings.append(pts)
            continue
        used.add(index)
        ring = list(pts)
        guard = 0
        while ring[0] != ring[-1] and guard <= len(directed):
            guard += 1
            end = (ring[-1][0], ring[-1][1])
            incoming = (end[0] - ring[-2][0], end[1] - ring[-2][1])
            choices = [item for item in starts[end] if item not in used]
            if not choices:
                break
            nxt = choices[0] if len(choices) == 1 else _pick_turn(incoming, directed, choices)
            used.add(nxt)
            ring.extend(directed[nxt][1:])
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def _polygons_from_rings(rings: list[list]) -> list[list]:
    info = []
    for ring in rings:
        area = abs(_shoelace(ring))
        if area < 1.5 or len(ring) < 4:
            continue
        xs = [point[0] for point in ring]
        ys = [point[1] for point in ring]
        info.append(
            {
                "ring": [[point[0], point[1]] for point in ring],
                "area": area,
                "box": (min(xs), min(ys), max(xs), max(ys)),
                "depth": 0,
            }
        )
    for outer in info:
        px, py = outer["ring"][0]
        for other in info:
            if other is outer or other["area"] <= outer["area"]:
                continue
            minx, miny, maxx, maxy = other["box"]
            if px < minx or px > maxx or py < miny or py > maxy:
                continue
            if _point_in_ring(px, py, other["ring"]):
                outer["depth"] += 1
    polys = []
    for outer in info:
        if outer["depth"] % 2:
            continue
        holes = []
        for hole in info:
            if hole["depth"] != outer["depth"] + 1:
                continue
            px, py = hole["ring"][0]
            minx, miny, maxx, maxy = outer["box"]
            if px < minx or px > maxx or py < miny or py > maxy:
                continue
            if _point_in_ring(px, py, outer["ring"]):
                holes.append(hole["ring"])
        polys.append([outer["ring"], *holes])
    return polys


def _assert_flat_secret(collection: dict, allowed: set[int], width: int, height: int) -> None:
    blob = json.dumps(collection, separators=(",", ":"))
    if "removed_americas" in blob:
        raise SystemExit("secrecy: removed_americas leaked into flat.geojson")
    seen = set()

    def check_xy(pt, kind: str):
        x, y = int(pt[0]), int(pt[1])
        if y < -1 or y > height + 1:
            raise SystemExit(f"secrecy: flat {kind} coordinate is outside the map")
        if kind == "fog":
            if x not in (0, HIDDEN_WEST_OF):
                raise SystemExit("secrecy: fog shape is not a plain western rectangle")
            return
        if x < HIDDEN_WEST_OF:
            raise SystemExit(f"secrecy: flat {kind} geometry crosses into the hidden west")
        if x > width + 1:
            raise SystemExit(f"secrecy: flat {kind} coordinate is outside the map")

    def walk(coords, kind: str, depth: int):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            check_xy(coords, kind)
            return
        for item in coords:
            walk(item, kind, depth + 1)

    for feature in collection["features"]:
        props = feature.get("properties") or {}
        kind = props.get("kind")
        if kind not in {"sea", "fog", "province", "nation"}:
            raise SystemExit(f"secrecy: unexpected flat feature {kind}")
        if kind == "province":
            pid = int(props["id"])
            if pid not in allowed:
                raise SystemExit(f"secrecy: flat layer publishes hidden province {pid}")
            seen.add(pid)
        walk(feature["geometry"]["coordinates"], kind, 0)
    missing = allowed - seen
    if len(missing) > max(20, int(len(allowed) * 0.02)):
        raise SystemExit(f"secrecy: flat layer dropped {len(missing)} published provinces")
    if missing:
        log(f"  flat layer skipped {len(missing)} tiny provinces")


def write_flat_layer(ids: np.ndarray, world: dict, width: int, height: int, political_path: Path, dest: Path) -> None:
    """Eastern province fills and nation outlines for zoom levels past the raster.

    The source sheet has no pixels finer than native zoom, so the close view is a
    flat board: solid sea, solid nation color, crisp borders, and a flat fog
    rectangle over the hidden west. No western coastline is traced. Neighboring
    provinces share one smoothed border, so the fills meet without gaps.
    """
    nation_of: dict[int, int] = {}
    color_of: dict[int, str] = {}
    for nation in world["nations"]:
        color = str(nation.get("color") or "#E6E6EA").lower()
        if len(color) != 7 or any(c not in "0123456789abcdef" for c in color[1:]):
            color = "#e6e6ea"
        for pid in nation["provinces"]:
            nation_of[int(pid)] = int(nation["id"])
            color_of[int(pid)] = color

    log("  tracing shared borders…")
    arcs = _boundary_arcs(ids)
    log(f"  {len(arcs)} shared borders")
    by_pid: dict[int, list[dict]] = defaultdict(list)
    for arc in arcs:
        if arc["left"]:
            by_pid[arc["left"]].append(arc)
        if arc["right"]:
            by_pid[arc["right"]].append(arc)

    features = [
        {
            "type": "Feature",
            "properties": {"kind": "sea", "color": SEA_COLOR},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [HIDDEN_WEST_OF, 0],
                    [width, 0],
                    [width, height],
                    [HIDDEN_WEST_OF, height],
                    [HIDDEN_WEST_OF, 0],
                ]],
            },
        }
    ]

    counts = np.bincount(ids.ravel())
    pids = sorted(int(pid) for pid in world["provinces"])
    province_count = 0
    bad_shape = 0
    for pid in pids:
        box = world["provinces"][str(pid)]["bbox"]
        if int(box[0]) < HIDDEN_WEST_OF:
            raise SystemExit(f"secrecy: flat province {pid} bbox starts in the hidden west")
        pixels = int(counts[pid]) if pid < len(counts) else 0
        if pixels < 6:
            continue
        polys = _polygons_from_rings(_rings_for_province(by_pid.get(pid, []), pid))
        if not polys:
            continue
        poly_area = 0.0
        for poly in polys:
            poly_area += abs(_shoelace(poly[0]))
            for hole in poly[1:]:
                poly_area -= abs(_shoelace(hole))
        if pixels >= 40 and not (0.45 * pixels <= poly_area <= 1.8 * pixels):
            bad_shape += 1
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "province",
                    "id": pid,
                    "nation_id": nation_of.get(pid),
                    "color": color_of.get(pid, UNCLAIMED_COLOR),
                },
                "geometry": {"type": "MultiPolygon", "coordinates": polys},
            }
        )
        province_count += 1
        if province_count % 500 == 0:
            log(f"  assembled {province_count} provinces")
    if bad_shape:
        log(f"  {bad_shape} provinces changed area more than expected")
        if bad_shape > max(30, int(len(pids) * 0.03)):
            raise SystemExit("flat borders drifted too far from the province shapes")

    nation_lines = []
    for arc in arcs:
        left_nation = nation_of.get(arc["left"], 0)
        right_nation = nation_of.get(arc["right"], 0)
        if left_nation == right_nation:
            continue
        nation_lines.append([[point[0], point[1]] for point in arc["pts"]])
    if nation_lines:
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "nation"},
                "geometry": {"type": "MultiLineString", "coordinates": nation_lines},
            }
        )

    features.append(
        {
            "type": "Feature",
            "properties": {"kind": "fog", "color": _fog_hex(political_path)},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [0, 0],
                    [HIDDEN_WEST_OF, 0],
                    [HIDDEN_WEST_OF, height],
                    [0, height],
                    [0, 0],
                ]],
            },
        }
    )

    collection = {"type": "FeatureCollection", "features": features}
    _assert_flat_secret(collection, set(pids), width, height)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(collection, separators=(",", ":")) + "\n", encoding="utf-8")
    log(
        f"  flat layer: {province_count} provinces, {len(nation_lines)} nation rings, "
        f"fog {features[-1]['properties']['color']}, {dest.stat().st_size / 1e6:.1f} MB"
    )


def main_flat_only() -> None:
    """Rebuild data/flat.geojson without touching tiles."""
    started = time.perf_counter()
    paths = require_source()
    with Image.open(paths["political"]) as im:
        width, height = im.size
    log(f"Flat layer only, map {width}x{height}")
    provinces = json.loads(paths["provinces"].read_text(encoding="utf-8"))["provinces"]
    ids = build_id_raster(paths["province_ids"], provinces, (height, width))
    del provinces
    minx, maxx, miny, maxy, count = extents(ids)
    allowed = {pid for pid in range(1, len(count)) if count[pid] and int(minx[pid]) >= HIDDEN_WEST_OF}
    drop = np.ones(len(count), dtype=bool)
    drop[0] = False
    for pid in allowed:
        drop[pid] = False
    ids[drop[ids]] = 0
    ids[:, :HIDDEN_WEST_OF] = 0
    boxes = {}
    for pid in allowed:
        boxes[pid] = {
            "minx": int(minx[pid]),
            "maxx": int(maxx[pid]),
            "miny": int(miny[pid]),
            "maxy": int(maxy[pid]),
            "water": None,
        }
    del minx, maxx, miny, maxy, count
    world = build_world(paths, ids, allowed, boxes)
    world.pop("km_per_px", None)
    world.pop("removed_names", None)
    write_flat_layer(ids, world, width, height, paths["political"], ROOT / "data" / "flat.geojson")
    log(f"Done in {time.perf_counter() - started:.0f}s")


def compare_fog_tiles() -> None:
    """A fully western native tile of the depth layer must match the political fog."""
    political = np.asarray(Image.open(ROOT / "tiles" / "political" / "6" / "2" / "2.webp").convert("RGB"))
    depth = np.asarray(Image.open(ROOT / "tiles" / "depth" / "6" / "2" / "2.webp").convert("RGB"))
    delta = np.abs(political.astype(np.int16) - depth.astype(np.int16)).mean()
    log(f"  western depth-vs-fog tile delta {delta:.2f}")
    if delta > 12:
        raise SystemExit("secrecy: a western depth tile does not match the fog artwork")


def main() -> None:
    started = time.perf_counter()
    paths = require_source()
    log("Loading province rasters…")
    with Image.open(paths["political"]) as im:
        width, height = im.size
    native = native_zoom_for(width, height)
    log(f"  map {width}x{height}, native zoom {native}, hide x < {HIDDEN_WEST_OF}")

    provinces = json.loads(paths["provinces"].read_text(encoding="utf-8"))["provinces"]
    ids = build_id_raster(paths["province_ids"], provinces, (height, width))
    del provinces

    log("Checking that the published artwork is actually fogged…")
    political = load_rgb(paths["political"])
    assert_fogged("political.png", political, ids)
    del political
    terrain = load_rgb(paths["terrain"])
    assert_fogged("terrain.png", terrain, ids)
    del terrain

    log("Measuring provinces…")
    minx, maxx, miny, maxy, count = extents(ids)
    allowed = {pid for pid in range(1, len(count)) if count[pid] and int(minx[pid]) >= HIDDEN_WEST_OF}
    omitted = {pid for pid in range(1, len(count)) if count[pid] and pid not in allowed}
    west_pixels = int((ids[:, :HIDDEN_WEST_OF] > 0).sum())
    if west_pixels and not omitted:
        raise SystemExit("secrecy: western land is present but no province was omitted")
    log(f"  published provinces {len(allowed)}, omitted {len(omitted)}, western land pixels {west_pixels}")

    drop = np.ones(len(count), dtype=bool)
    drop[0] = False
    for pid in allowed:
        drop[pid] = False
    ids[drop[ids]] = 0
    ids[:, :HIDDEN_WEST_OF] = 0

    log("Sampling nearby water depth…")
    depth_values, _ = colorize_depth(paths["depth"], (height, width))
    water = shallowest_water(ids, depth_values)
    del depth_values
    boxes = {}
    for pid in allowed:
        boxes[pid] = {
            "minx": int(minx[pid]),
            "maxx": int(maxx[pid]),
            "miny": int(miny[pid]),
            "maxy": int(maxy[pid]),
            "water": water.get(pid),
        }
    del minx, maxx, miny, maxy, count, water

    log("Writing world data…")
    world = build_world(paths, ids, allowed, boxes)
    km = world.pop("km_per_px")
    removed_names = world.pop("removed_names")
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "world.json").write_text(
        json.dumps({"nations": world["nations"], "provinces": world["provinces"]}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    lookup_path = data_dir / "lookup.png"
    lookup_w, lookup_h = write_lookup(ids, lookup_path)
    world["removed_names"] = removed_names
    secrecy_check(world, lookup_path, allowed)

    log("Writing flat close-up vectors…")
    write_flat_layer(ids, world, width, height, paths["political"], data_dir / "flat.geojson")

    log("Writing outline tiles…")
    _, blank = write_blank_tiles()
    edge = edge_mask(ids)
    del ids
    write_outline_pyramid(edge, ROOT / "tiles" / "outlines", native, blank)
    del edge

    log("Writing political tiles and overview…")
    political = load_rgb(paths["political"])
    # Spot-check a few capitals against the painted nation color.
    by_name = {n["id"]: n for n in world["nations"]}
    for nation in world["nations"][:6]:
        x, y = nation["capital_xy"]
        pixel = political[int(round(y)), int(round(x))].tolist()
        log(f"  sample {nation['short_name']} id={nation['id']} xy=({x},{y}) color={nation['color']} pixel={pixel}")
    overview_w = 960
    overview_h = max(1, int(round(height * overview_w / width)))
    overview = Image.fromarray(political, "RGB").resize((overview_w, overview_h), Image.Resampling.LANCZOS)
    overview_path = ROOT / "assets" / "overview.webp"
    overview.save(overview_path, "WEBP", quality=72, method=4)
    write_photo_pyramid(political, ROOT / "tiles" / "political", native)

    log("Writing terrain tiles…")
    terrain = load_rgb(paths["terrain"])
    if terrain.shape[:2] != (height, width):
        raise SystemExit("terrain image size does not match the political map")
    write_photo_pyramid(terrain, ROOT / "tiles" / "terrain", native)
    del terrain

    log("Writing depth tiles (hidden west replaced with fog)…")
    _, depth_rgb = colorize_depth(paths["depth"], (height, width))
    depth_rgb[:, :HIDDEN_WEST_OF] = political[:, :HIDDEN_WEST_OF]
    write_depth_pyramid(depth_rgb, political, ROOT / "tiles" / "depth", native)
    del depth_rgb, political

    meta = {
        "width": width,
        "height": height,
        "km_per_px": km,
        "native_zoom": native,
        "tile_size": TILE,
        "lookup_scale": LOOKUP_SCALE,
        "lookup_width": lookup_w,
        "lookup_height": lookup_h,
        "hidden_west_of": HIDDEN_WEST_OF,
        "overview": "assets/overview.webp",
        "overview_width": overview_w,
        "overview_height": overview_h,
    }
    (data_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if not (data_dir / "claims.json").exists():
        (data_dir / "claims.json").write_text("{}\n", encoding="utf-8")
    if not (data_dir / "infrastructure.geojson").exists():
        (data_dir / "infrastructure.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": []}, indent=2) + "\n",
            encoding="utf-8",
        )

    log("Checking a western depth tile against the fog…")
    compare_fog_tiles()
    total = 0
    files = 0
    for folder in (ROOT / "tiles", ROOT / "data", ROOT / "assets"):
        for path in folder.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
                files += 1
    log(
        f"Done in {time.perf_counter() - started:.0f}s. "
        f"{len(world['nations'])} nations, {len(world['provinces'])} provinces, "
        f"{files} output files, {total / 1e6:.1f} MB."
    )
    # Silence unused-name lint in case a sample loop is empty.
    del by_name


if __name__ == "__main__":
    try:
        if "--only-flat" in sys.argv:
            main_flat_only()
        else:
            main()
    except BrokenPipeError:
        sys.exit(0)
