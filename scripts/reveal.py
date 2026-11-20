#!/usr/bin/env python3
"""Revealed-only bundle rules, fog states, and east-west wrap math.

The private master map stays outside the repo. ``source/reveal/`` is a
gitignored bundle the GM builds. Anything this module publishes is either
already on the public side of the legacy fog line or listed in that bundle.
A failed check raises SystemExit. Nothing here warns and continues.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Mapping, Sequence

FOG_STATES = ("hidden", "sighted", "charted", "explored")
FOG_RANK = {name: index for index, name in enumerate(FOG_STATES)}

# Legend swatches. The five new classes use the brief's colours. Older
# classes are legend-only; they are not baked into tiles.
TERRAIN_COLORS = {
    "forest": "#2f6b3a",
    "jungle": "#1d6a45",
    "plains": "#c6b47a",
    "hills": "#8d7344",
    "desert": "#d2b48c",
    "snow": "#e7eef2",
    "mountains": "#6d6875",
    "marsh": "#6f8f72",
    "grassland": "#8fad62",
    "tundra": "#b7c4be",
    "steppe": "#AAAA70",
    "savanna": "#B8AA62",
    "taiga": "#346244",
    "dunes": "#EED698",
    "volcanic": "#5C504C",
}
NEW_TERRAINS = ("steppe", "savanna", "taiga", "dunes", "volcanic")
TERRAIN_CLASSES = tuple(TERRAIN_COLORS)

DEPOSIT_INFO = {
    "Fe": {"name": "Iron", "luxury": False},
    "Ag": {"name": "Silver", "luxury": False},
    "Gm": {"name": "Gems", "luxury": True},
    "S": {"name": "Sulfur", "luxury": False},
    "Gt": {"name": "Grain", "luxury": False},
    "Na": {"name": "Nitre", "luxury": False},
    "Sp": {"name": "Spices", "luxury": True},
    "Dy": {"name": "Dyes", "luxury": True},
    "Hw": {"name": "Hardwood", "luxury": False},
    "Hr": {"name": "Horses", "luxury": False},
    "Fu": {"name": "Furs", "luxury": True},
    "Pe": {"name": "Pearls", "luxury": True},
    "Wh": {"name": "Whales", "luxury": False},
    "Sf": {"name": "Salt", "luxury": False},
}
DEPOSIT_CODES = tuple(DEPOSIT_INFO)
LUXURY_NAMES = tuple(info["name"] for info in DEPOSIT_INFO.values() if info["luxury"])

# Bookkeeping kept on a bundle record at every revealed state. rgb maps the
# id raster. centroid is the source of the published xy.
BUNDLE_ALWAYS = {"id", "rgb", "centroid", "state"}
SIGHTED_FIELDS = BUNDLE_ALWAYS | {"coastal", "xy", "deposits"}
CHARTED_FIELDS = SIGHTED_FIELDS | {"terrain", "mix", "river", "water", "bbox", "native_power"}
EXPLORED_FIELDS = CHARTED_FIELDS | {
    "area_px",
    "area_km2",
    "landmass",
    "neighbors",
    "sea_neighbors",
    "region",
    "name",
    "nation_id",
    "owner",
}

# What world.json is allowed to carry. Sighted is the coastline record only.
PUBLIC_SIGHTED = {"id", "state", "coastal", "xy", "deposits"}
PUBLIC_CHARTED = PUBLIC_SIGHTED | {"terrain", "mix", "river", "water", "bbox", "native_power"}
PUBLIC_EXPLORED = PUBLIC_CHARTED | {
    "area_km2",
    "area_px",
    "landmass",
    "neighbors",
    "sea_neighbors",
    "region",
    "name",
    "nation_id",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def parse_state(value, where: str) -> str:
    if not isinstance(value, str):
        fail(f"secrecy: {where} has fog state {value!r}")
    text = value.strip().lower()
    if text not in FOG_RANK:
        fail(f"secrecy: {where} has fog state {value!r}")
    return text


def parse_fog(raw) -> dict[int, str]:
    """``{"<id>": "<state>"}``. Absent ids are hidden. Bad input fails."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        fail("secrecy: fog.json must be an object of id to state")
    out: dict[int, str] = {}
    for key, value in raw.items():
        try:
            pid = int(key)
        except (TypeError, ValueError):
            fail(f"secrecy: fog.json key {key!r} is not a province id")
        if isinstance(key, bool) or pid <= 0:
            fail(f"secrecy: fog.json key {key!r} is not a province id")
        out[pid] = parse_state(value, f"fog.json province {pid}")
    return out


def resolve_state(pid: int, fog: Mapping[int, str], legacy_east: Iterable[int] = ()) -> str:
    """Bundle state wins. A legacy eastern province stays explored if unlisted.

    Everything else is hidden. The legacy set is the base map's provinces that
    sit entirely east of the old fog line, so an empty bundle keeps today's
    published world.
    """
    if pid in fog:
        return fog[pid]
    if pid in set(legacy_east):
        return "explored"
    return "hidden"


def rank_at_least(state: str, need: str) -> bool:
    return FOG_RANK[state] >= FOG_RANK[need]


def is_coastal_deposit(deposit: Mapping) -> bool:
    """Pearls and whales are coastal. Furs are coastal when worked from a port."""
    if deposit.get("coastal") is True:
        return True
    if deposit.get("coastal") is False:
        return False
    code = str(deposit.get("code") or "")
    if code in {"Pe", "Wh"}:
        return True
    worked = str(deposit.get("worked_by") or "").lower()
    return code == "Fu" and "port" in worked


def _fields_for(state: str) -> set[str]:
    if state == "sighted":
        return SIGHTED_FIELDS
    if state == "charted":
        return CHARTED_FIELDS
    if state == "explored":
        return EXPLORED_FIELDS
    return set()


def assert_province_fields(record: Mapping, state: str, where: str) -> None:
    """Fail when a bundle province carries a field its fog state does not allow."""
    if state == "hidden":
        fail(f"secrecy: {where} is hidden and cannot be published")
    if not isinstance(record, Mapping):
        fail(f"secrecy: {where} is not an object")
    allowed = _fields_for(state)
    extra = sorted(str(key) for key in record.keys() if key not in allowed)
    if extra:
        fail(f"secrecy: {where} state {state} leaks fields {', '.join(extra)}")
    native = record.get("native_power")
    if native not in (None, "") and state == "sighted":
        fail(f"secrecy: {where} names a native power before it is charted")
    if isinstance(native, dict) and state != "explored":
        fail(f"secrecy: {where} native record needs explored")
    if state == "charted" and native not in (None, "") and not isinstance(native, str):
        fail(f"secrecy: {where} charted native_power must be a name")
    deposits = record.get("deposits") or []
    if deposits and state == "sighted":
        for deposit in deposits:
            if not isinstance(deposit, Mapping) or not is_coastal_deposit(deposit):
                fail(f"secrecy: {where} sighted deposit is not coastal")


def assert_deposit(deposit: Mapping, state: str, where: str) -> None:
    if not isinstance(deposit, Mapping):
        fail(f"secrecy: {where} is not an object")
    code = deposit.get("code")
    if code not in DEPOSIT_INFO:
        fail(f"secrecy: {where} has unknown deposit code {code!r}")
    if state == "hidden":
        fail(f"secrecy: {where} is on a hidden province")
    if state == "sighted" and not is_coastal_deposit(deposit):
        fail(f"secrecy: {where} is not a coastal deposit")
    if FOG_RANK[state] < FOG_RANK["sighted"]:
        fail(f"secrecy: {where} is below the sighted reveal")


def assert_landmark(landmark: Mapping, state: str, where: str) -> None:
    if not isinstance(landmark, Mapping):
        fail(f"secrecy: {where} is not an object")
    if state in {"hidden", "sighted"}:
        fail(f"secrecy: {where} is on a {state} province")
    charted = landmark.get("charted") is True
    if state == "charted" and not charted:
        fail(f"secrecy: {where} is not marked charted")
    if state not in {"charted", "explored"}:
        fail(f"secrecy: {where} is below the charted reveal")


def assert_native(native: Mapping, state: str, where: str) -> None:
    """A natives.json object is the full record. Charted publishes the name only."""
    if not isinstance(native, Mapping):
        fail(f"secrecy: {where} is not an object")
    if state != "explored":
        fail(f"secrecy: {where} native record needs explored, got {state}")


def publish_province(pid: int, record: Mapping, state: str, deposits: Sequence[Mapping] | None = None) -> dict:
    """World.json record for one revealed province. Hidden returns nothing.

    This strips. It does not forgive a bundle that already failed
    ``assert_province_fields``. Deposits on the record are the published list.
    """
    if state == "hidden":
        fail(f"secrecy: province {pid} is hidden")
    deps = [dict(item) for item in (deposits or [])]
    for deposit in deps:
        assert_deposit(deposit, state, f"province {pid} deposit {deposit.get('code')}")
    xy = record.get("xy")
    if xy is None and record.get("centroid") is not None:
        centroid = record["centroid"]
        xy = [round(float(centroid[0]), 1), round(float(centroid[1]), 1)]
    if state == "sighted":
        return {
            "id": pid,
            "state": "sighted",
            "coastal": True,
            "xy": xy,
            "deposits": deps,
        }
    published = {
        "id": pid,
        "state": state,
        "coastal": bool(record.get("coastal")),
        "xy": xy,
        "deposits": deps,
    }
    for key in ("terrain", "mix", "river", "water", "bbox"):
        if key in record and record.get(key) not in (None, ""):
            published[key] = record[key]
    native = record.get("native_power")
    if isinstance(native, str) and native.strip():
        published["native_power"] = native.strip()
    elif isinstance(native, dict) and state == "explored":
        name = native.get("name") or native.get("power")
        if name:
            published["native_power"] = str(name)
    if state == "explored":
        for key in ("area_km2", "area_px", "landmass", "neighbors", "sea_neighbors", "region", "name", "nation_id"):
            if key in record and record.get(key) not in (None, ""):
                published[key] = record[key]
    return published


def _pid(value, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        fail(f"secrecy: {where} has a bad province id")
    try:
        pid = int(value)
    except (TypeError, ValueError):
        fail(f"secrecy: {where} has a bad province id")
    if pid <= 0:
        fail(f"secrecy: {where} has a bad province id")
    return pid


def _state_of(pid: int, fog: Mapping[int, str], legacy_east: Iterable[int]) -> str:
    return resolve_state(pid, fog, legacy_east)


def _walk_coords(value, found: list[tuple[float, float]]) -> None:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[0], (int, float)) and not isinstance(value[0], bool):
            if isinstance(value[1], (int, float)) and not isinstance(value[1], bool):
                found.append((float(value[0]), float(value[1])))
                return
        for item in value:
            _walk_coords(item, found)


def assert_coords(value, where: str, hidden_contains: Callable[[float, float], bool] | None) -> None:
    if hidden_contains is None:
        return
    found: list[tuple[float, float]] = []
    _walk_coords(value, found)
    for x, y in found:
        if hidden_contains(x, y):
            fail(f"secrecy: {where} coordinate ({x}, {y}) is inside a hidden province")


def validate_bundle(
    bundle: Mapping,
    hidden_contains: Callable[[float, float], bool] | None = None,
    legacy_east: Iterable[int] = (),
) -> None:
    """Fail the build if the revealed-only bundle leaks.

    ``bundle`` keys: fog, provinces, deposits, landmarks, natives,
    winds_currents, hazards, raster_ids. A planted leak must raise SystemExit.
    """
    if not isinstance(bundle, Mapping):
        fail("secrecy: reveal bundle is not an object")
    fog = parse_fog(bundle.get("fog") or {})
    legacy = set(legacy_east)

    provinces = bundle.get("provinces") or []
    if not isinstance(provinces, list):
        fail("secrecy: reveal provinces must be a list")
    seen: set[int] = set()
    for record in provinces:
        if not isinstance(record, Mapping):
            fail("secrecy: reveal province is not an object")
        pid = _pid(record.get("id"), "reveal province")
        seen.add(pid)
        state = _state_of(pid, fog, legacy)
        if pid not in fog and pid not in legacy:
            fail(f"secrecy: published province {pid} is not in fog.json")
        if state == "hidden":
            fail(f"secrecy: hidden province {pid} is in the reveal bundle")
        assert_province_fields(record, state, f"province {pid}")

    raster_ids = bundle.get("raster_ids") or []
    if not isinstance(raster_ids, (list, tuple, set)):
        fail("secrecy: raster_ids must be a list")
    for raw in raster_ids:
        pid = _pid(raw, "raster")
        state = _state_of(pid, fog, legacy)
        if state == "hidden" or (pid not in fog and pid not in legacy):
            fail(f"secrecy: hidden province {pid} is painted in a raster")

    deposits = bundle.get("deposits") or []
    if not isinstance(deposits, list):
        fail("secrecy: deposits must be a list")
    for index, deposit in enumerate(deposits):
        if not isinstance(deposit, Mapping):
            fail(f"secrecy: deposit {index} is not an object")
        pid = _pid(deposit.get("province"), f"deposit {index}")
        state = _state_of(pid, fog, legacy)
        if pid not in fog and pid not in legacy:
            fail(f"secrecy: deposit {index} province {pid} is not in fog.json")
        assert_deposit(deposit, state, f"deposit {index} on province {pid}")
        assert_coords(deposit.get("xy") or deposit.get("centroid"), f"deposit {index}", hidden_contains)

    landmarks = bundle.get("landmarks") or []
    if not isinstance(landmarks, list):
        fail("secrecy: landmarks must be a list")
    for index, landmark in enumerate(landmarks):
        if not isinstance(landmark, Mapping):
            fail(f"secrecy: landmark {index} is not an object")
        pid = _pid(landmark.get("province"), f"landmark {index}") if landmark.get("province") not in (None, "") else None
        state = _state_of(pid, fog, legacy) if pid is not None else "hidden"
        if pid is not None and pid not in fog and pid not in legacy:
            fail(f"secrecy: landmark {index} province {pid} is not in fog.json")
        if pid is None:
            fail(f"secrecy: landmark {index} has no province")
        assert_landmark(landmark, state, f"landmark {index}")
        assert_coords([landmark.get("x"), landmark.get("y")] if landmark.get("x") is not None else landmark.get("points"), f"landmark {index}", hidden_contains)

    natives = bundle.get("natives") or []
    if not isinstance(natives, list):
        fail("secrecy: natives must be a list")
    for index, native in enumerate(natives):
        if not isinstance(native, Mapping):
            fail(f"secrecy: native {index} is not an object")
        pid = _pid(native.get("province"), f"native {index}")
        state = _state_of(pid, fog, legacy)
        if pid not in fog and pid not in legacy:
            fail(f"secrecy: native {index} province {pid} is not in fog.json")
        assert_native(native, state, f"native {index}")
        assert_coords(native.get("xy") or native.get("centroid"), f"native {index}", hidden_contains)

    winds = bundle.get("winds_currents") or {}
    if winds is None:
        winds = {}
    if not isinstance(winds, Mapping):
        fail("secrecy: winds_currents must be an object")
    for kind in ("winds", "currents"):
        rows = winds.get(kind) or []
        if not isinstance(rows, list):
            fail(f"secrecy: winds_currents.{kind} must be a list")
        for index, row in enumerate(rows):
            assert_coords(row, f"{kind} {index}", hidden_contains)
            if isinstance(row, Mapping) and row.get("province") not in (None, ""):
                pid = _pid(row.get("province"), f"{kind} {index}")
                if _state_of(pid, fog, legacy) == "hidden":
                    fail(f"secrecy: {kind} {index} is on hidden province {pid}")

    hazards = bundle.get("hazards") or {}
    if hazards is None:
        hazards = {}
    if not isinstance(hazards, Mapping):
        fail("secrecy: hazards must be an object")
    for kind in ("areas", "chokepoints"):
        rows = hazards.get(kind) or []
        if not isinstance(rows, list):
            fail(f"secrecy: hazards.{kind} must be a list")
        for index, row in enumerate(rows):
            assert_coords(row, f"hazard {kind} {index}", hidden_contains)

    for pid, state in fog.items():
        if state != "hidden" and pid not in seen and pid not in legacy and pid not in {int(v) for v in raster_ids}:
            # A fog entry with no geometry is not a leak. Publishing it would be.
            continue


def raster_ids_from_rgb(image, provinces: Sequence[Mapping]) -> set[int]:
    """Read a province-id PNG. ``image`` is a Pillow image or an RGB array-like."""
    lut: dict[tuple[int, int, int], int] = {}
    for record in provinces:
        rgb = record.get("rgb")
        if not rgb or len(rgb) < 3:
            continue
        lut[(int(rgb[0]), int(rgb[1]), int(rgb[2]))] = int(record["id"])
    if hasattr(image, "convert"):
        image = image.convert("RGB")
        reader = image.get_flattened_data if hasattr(image, "get_flattened_data") else image.getdata
        pixels = list(reader())
    else:
        pixels = []
        for row in image:
            for pixel in row:
                pixels.append((int(pixel[0]), int(pixel[1]), int(pixel[2])))
    found = set()
    for pixel in pixels:
        pid = lut.get((int(pixel[0]), int(pixel[1]), int(pixel[2])))
        if pid:
            found.add(pid)
    return found


def hidden_mask_contains(mask, width: int | None = None) -> Callable[[float, float], bool]:
    """True when a coordinate sits inside a hidden province, not merely on its edge.

    ``mask`` is a sequence of rows of truthy pixels, or a callable.
    A vertex on the shared border (any neighbor that is not hidden) is allowed,
    so a revealed outline is not mistaken for a traced hidden coast.
    """
    if callable(mask):
        return mask
    height = len(mask)
    row_width = width if width is not None else (len(mask[0]) if height else 0)

    def contains(x: float, y: float) -> bool:
        ix = int(round(float(x)))
        iy = int(round(float(y)))
        saw = False
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                xx = ix + dx
                yy = iy + dy
                if xx < 0 or yy < 0 or xx >= row_width or yy >= height:
                    continue
                saw = True
                if not mask[yy][xx]:
                    return False
        return saw

    return contains


def publication_plan(base_ids: Iterable[int], minx_of: Mapping[int, int], fog: Mapping[int, str], west: int) -> tuple[set[int], dict[int, str]]:
    """Legacy eastern provinces stay explored. The fog line still hides the rest.

    ``minx_of`` is the westernmost pixel of each base province. A province that
    touches x < west is not legacy. Bundle ids that are not in the base set are
    hidden until fog.json names them.
    """
    base = {int(pid) for pid in base_ids}
    legacy = {pid for pid in base if int(minx_of.get(pid, west)) >= int(west)}
    states = {}
    for pid in base | set(fog):
        states[pid] = resolve_state(pid, fog, legacy)
    return legacy, states


def assert_fog_color(name: str, mean_rgb: Sequence[float]) -> None:
    """Parchment fog is warm and light. A leak of sea or land fails the build."""
    if len(mean_rgb) < 3:
        fail(f"secrecy: {name} has no colour sample")
    red, _green, blue = (float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2]))
    if red < 120 or red < blue + 15:
        shown = [round(float(channel), 1) for channel in mean_rgb[:3]]
        fail(f"{name} does not look like the fog artwork (mean RGB {shown}). Refusing to publish.")


def rect_mask(width: int, height: int, rects: Sequence[tuple[int, int, int, int]]):
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            hit = False
            for minx, miny, maxx, maxy in rects:
                if minx <= x < maxx and miny <= y < maxy:
                    hit = True
                    break
            row.append(hit)
        rows.append(row)
    return rows


# --- wrap math (pure; the Leaflet client mirrors this) ---


def wrap_coord(x: float, width: float) -> float:
    if width <= 0:
        fail("secrecy: map width must be positive")
    value = float(x) % float(width)
    if value < 0:
        value += float(width)
    return value


def tile_x(index: int, count: int) -> int:
    """``((x % n) + n) % n`` for a tile column."""
    if count <= 0:
        fail("secrecy: tile count must be positive")
    return ((int(index) % int(count)) + int(count)) % int(count)


def tile_counts(width: int, height: int, native_zoom: int, zoom: int, tile: int = 256) -> tuple[int, int]:
    """Tile columns and rows at ``zoom``. Native zoom is the full raster."""
    scale = 1 << max(0, int(native_zoom) - int(zoom))
    view_w = max(1, int(round(width / scale)))
    view_h = max(1, int(round(height / scale)))
    return max(1, math.ceil(view_w / tile)), max(1, math.ceil(view_h / tile))


def native_zoom_for(width: int, height: int, tile: int = 256) -> int:
    need = max(int(width), int(height))
    zoom = 0
    while tile * (1 << zoom) < need:
        zoom += 1
    return zoom


def view_shifts(west: float, east: float, width: float) -> list[int]:
    """World copies (k such that x+k*width) that intersect [west, east)."""
    if width <= 0:
        fail("secrecy: map width must be positive")
    if east <= west:
        return [0]
    first = math.floor(float(west) / float(width))
    last = math.floor((float(east) - 1e-9) / float(width))
    if last < first:
        last = first
    if last - first > 6:
        center = int(round(((west + east) / 2) / width))
        first = center - 3
        last = center + 3
    return list(range(first, last + 1))


def unwrap_path(xs: Sequence[float], width: float) -> list[float]:
    """Make consecutive points differ by at most half the world."""
    if not xs:
        return []
    if width <= 0:
        fail("secrecy: map width must be positive")
    out = [float(xs[0])]
    for raw in xs[1:]:
        value = float(raw)
        while value - out[-1] > width / 2:
            value -= width
        while out[-1] - value > width / 2:
            value += width
        out.append(value)
    return out


def nearest_path(xs: Sequence[float], center: float, width: float) -> list[float]:
    """The copy of a path whose midpoint is nearest the view center."""
    unwrapped = unwrap_path(xs, width)
    if not unwrapped:
        return []
    mid = sum(unwrapped) / len(unwrapped)
    shift = int(round((float(center) - mid) / float(width)))
    return [value + shift * float(width) for value in unwrapped]


def minimap_rects(west: float, east: float, width: float) -> list[tuple[float, float]]:
    """One or two overview spans. A view across the seam splits in two."""
    if width <= 0:
        fail("secrecy: map width must be positive")
    span = float(east) - float(west)
    if span >= width:
        return [(0.0, float(width))]
    if span <= 0:
        point = wrap_coord(west, width)
        return [(point, point)]
    start = wrap_coord(west, width)
    end = start + span
    if end <= width + 1e-9:
        return [(start, min(end, float(width)))]
    return [(start, float(width)), (0.0, end - float(width))]
