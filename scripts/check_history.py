#!/usr/bin/env python3
"""Refuse history, infrastructure, trade, sector, or settlement data that publishes the hidden west.

A trade route in the catalog or a snapshot has to be public. Anything else fails the build.
A sector controller has to be a nation already on the published map, or blank for unclaimed.
A village has to sit inside its sector, and every sector needs a settlement.
"""

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEST = 3592
WORK_MIN = 9000


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load(path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def as_rings(value, where):
    if not isinstance(value, list) or not value:
        fail(f"{where}: missing rings")
    if isinstance(value[0], list) and value[0] and isinstance(value[0][0], (int, float)) and not isinstance(value[0][0], bool):
        return [value]
    if (
        isinstance(value[0], list)
        and value[0]
        and isinstance(value[0][0], list)
        and value[0][0]
        and isinstance(value[0][0][0], (int, float))
        and not isinstance(value[0][0][0], bool)
    ):
        return value
    fail(f"{where}: rings must be map pixel coordinates")


def check_rings(value, where, width, height):
    for ring in as_rings(value, where):
        if len(ring) < 3:
            fail(f"{where}: a ring needs at least 3 points")
        for pt in ring:
            if not isinstance(pt, list) or len(pt) < 2:
                fail(f"{where}: bad point")
            x, y = pt[0], pt[1]
            if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                fail(f"{where}: point is not a number")
            if x < WEST:
                fail(f"{where}: x={x} is west of {WEST}")
            if x > width or y < 0 or y > height:
                fail(f"{where}: point {(x, y)} is outside the map")


PLACE_TYPES = {
    "capital",
    "city",
    "town",
    "village",
    "fort",
    "port",
    "shipyard",
    "market",
    "farm",
    "mine",
    "lumber",
    "lumber_camp",
}


def optional_int(value, where):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{where}: cycle and round must be whole numbers")
    return value


def check_point(x, y, where, width, height):
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        fail(f"{where}: point is not a number")
    if x < WEST:
        fail(f"{where}: x={x} is west of {WEST}")
    if x > width or y < 0 or y > height:
        fail(f"{where}: point {(x, y)} is outside the map")


def check_endpoint(value, where, nations):
    if not isinstance(value, dict):
        fail(f"{where}: endpoint must be an object")
    nation = value.get("nation")
    if isinstance(nation, bool) or not isinstance(nation, int) or nation not in nations:
        fail(f"{where}: nation {nation} is not on the published map")
    record = nations[nation]
    box = record.get("bbox") or [0, 0, 0, 0]
    xy = record.get("capital_xy") or [0, 0]
    if box[0] < WEST or xy[0] < WEST:
        fail(f"{where}: nation {nation} is west of the fog line")
    name = value.get("settlement", value.get("name"))
    if not isinstance(name, str) or not name.strip():
        fail(f"{where}: settlement name is missing")


def check_trade_list(routes, where, nations, width, height):
    if routes is None:
        return
    if not isinstance(routes, list):
        fail(f"{where}: trade routes must be a list")
    for index, row in enumerate(routes):
        if not isinstance(row, dict):
            fail(f"{where}: trade route must be an object")
        label = row.get("id", index)
        spot = f"{where} route {label}"
        if row.get("public") is not True:
            fail(f"{spot}: a published trade route must be public")
        kind = row.get("kind")
        if kind not in ("land", "sea"):
            fail(f"{spot}: kind must be land or sea")
        status = row.get("status")
        if status not in ("active", "disrupted"):
            fail(f"{spot}: status must be active or disrupted")
        goods = row.get("goods")
        if not isinstance(goods, str) or not goods.strip():
            fail(f"{spot}: goods text is missing")
        check_endpoint(row.get("from"), f"{spot} from", nations)
        check_endpoint(row.get("to"), f"{spot} to", nations)
        optional_int(row.get("cycle"), f"{spot} cycle")
        optional_int(row.get("round"), f"{spot} round")
        for pt in row.get("points") or []:
            if not isinstance(pt, list) or len(pt) < 2:
                fail(f"{spot}: route point is incomplete")
            check_point(pt[0], pt[1], spot, width, height)


def check_sectors(nations, width, height, province_id):
    geo_path = ROOT / "data" / "sectors.geojson"
    csv_path = ROOT / "data" / "sector_control.csv"
    if not geo_path.is_file():
        fail("missing data/sectors.geojson")
    if not csv_path.is_file():
        fail("missing data/sector_control.csv")
    collection = load(geo_path)
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        fail("data/sectors.geojson must be a FeatureCollection")
    features = collection.get("features")
    if not isinstance(features, list) or not features:
        fail("data/sectors.geojson has no sectors")
    seen = {}
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            fail("sectors.geojson: every entry must be a Feature")
        props = feature.get("properties") or {}
        sid = props.get("id")
        if not isinstance(sid, str) or "-" not in sid:
            fail(f"sectors.geojson: bad sector id {sid!r}")
        prefix, letter = sid.rsplit("-", 1)
        if letter not in ("A", "B", "C") or not prefix.isdigit():
            fail(f"sectors.geojson: sector id {sid} must look like 562-A")
        pid = int(prefix)
        if props.get("province") != pid:
            fail(f"sectors.geojson: {sid} province does not match its id")
        province_id(pid, f"sectors.geojson {sid}")
        if props.get("role") not in ("city", "town", "interior"):
            fail(f"sectors.geojson: {sid} role must be city, town, or interior")
        box = props.get("bbox")
        if not isinstance(box, list) or len(box) != 4:
            fail(f"sectors.geojson: {sid} bbox is missing")
        check_point(box[0], box[1], f"sectors.geojson {sid} bbox", width, height)
        check_point(box[2], box[3], f"sectors.geojson {sid} bbox", width, height)
        if box[0] > box[2] or box[1] > box[3]:
            fail(f"sectors.geojson: {sid} bbox is reversed")
        geom = feature.get("geometry") or {}
        kind = geom.get("type")
        coords = geom.get("coordinates")
        if kind == "Polygon":
            check_rings(coords, f"sectors.geojson {sid}", width, height)
        elif kind == "MultiPolygon":
            if not isinstance(coords, list) or not coords:
                fail(f"sectors.geojson: {sid} geometry is empty")
            for poly in coords:
                check_rings(poly, f"sectors.geojson {sid}", width, height)
        else:
            fail(f"sectors.geojson: {sid} geometry must be a polygon")
        if sid in seen:
            fail(f"sectors.geojson: duplicate sector id {sid}")
        seen[sid] = pid
    covered = set()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["sector_id", "controller", "cycle"]:
        fail("sector_control.csv: header must be sector_id,controller,cycle")
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 3:
            fail(f"sector_control.csv:{index}: expected 3 columns")
        sid, raw, cycle_text = (part.strip() for part in row)
        if sid not in seen:
            fail(f"sector_control.csv:{index}: {sid} is not in sectors.geojson")
        covered.add(sid)
        if raw != "":
            if not raw.isdigit():
                fail(f"sector_control.csv:{index}: controller {raw} is not a published nation")
            nation_id = int(raw)
            nation = nations.get(nation_id)
            if nation is None:
                fail(f"sector_control.csv:{index}: controller {nation_id} is not on the published map")
            box = nation.get("bbox") or [0, 0, 0, 0]
            xy = nation.get("capital_xy") or [0, 0]
            if box[0] < WEST or xy[0] < WEST:
                fail(f"sector_control.csv:{index}: controller {nation_id} is west of the fog line")
            province_id(seen[sid], f"sector_control.csv {sid}")
        if not cycle_text.isdigit():
            fail(f"sector_control.csv:{index}: cycle must be a whole number")
    missing = [sid for sid in seen if sid not in covered]
    if missing:
        fail(f"sector_control.csv: missing {missing[0]}")


def point_in_ring(x, y, ring):
    inside = False
    previous = len(ring) - 1
    for index, point in enumerate(ring):
        xi, yi = point[0], point[1]
        xj, yj = ring[previous][0], ring[previous][1]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        previous = index
    return inside


def ring_distance(x, y, ring):
    best = None
    for index in range(len(ring) - 1):
        ax, ay = ring[index][0], ring[index][1]
        bx, by = ring[index + 1][0], ring[index + 1][1]
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span == 0:
            dist = math.hypot(x - ax, y - ay)
        else:
            along = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / span))
            dist = math.hypot(x - (ax + along * dx), y - (ay + along * dy))
        if best is None or dist < best:
            best = dist
    return 0 if best is None else best


def interior_gap(geom, x, y):
    kind = geom.get("type")
    coords = geom.get("coordinates") or []
    polygons = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
    best = None
    for rings in polygons:
        if not rings or not point_in_ring(x, y, rings[0]):
            continue
        if any(point_in_ring(x, y, hole) for hole in rings[1:]):
            continue
        gap = min(ring_distance(x, y, ring) for ring in rings)
        if best is None or gap > best:
            best = gap
    return best


def check_settlements(provinces, nations, places, width, height):
    path = ROOT / "data" / "settlements.csv"
    if not path.is_file():
        fail("missing data/settlements.csv")
    sectors = load(ROOT / "data" / "sectors.geojson")
    geoms = {}
    for feature in sectors.get("features") or []:
        props = feature.get("properties") or {}
        geoms[props.get("id")] = feature.get("geometry") or {}
    by_name = {nation["short_name"]: nation for nation in nations.values()}
    owner_name = {}
    for pid, prov in provinces.items():
        owner = prov.get("nation_id")
        owner_name[pid] = "Unclaimed" if owner is None else nations[int(owner)]["short_name"]
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["nation", "province", "name", "type", "port", "sector_id"]:
        fail("settlements.csv: header must be nation,province,name,type,port,sector_id")
    held = {}
    villages = {}
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 6:
            fail(f"settlements.csv:{index}: expected 6 columns")
        nation_name, province_text, name, kind, port, sector_id = (part.strip() for part in row)
        if not province_text.isdigit():
            fail(f"settlements.csv:{index}: province must be an integer")
        pid = int(province_text)
        if pid not in provinces:
            fail(f"settlements.csv:{index}: province {pid} is not on the published map")
        box = provinces[pid].get("bbox") or [0, 0, 0, 0]
        xy = provinces[pid].get("xy") or [0, 0]
        if box[0] < WEST or xy[0] < WEST:
            fail(f"settlements.csv:{index}: province {pid} is west of the fog line")
        if kind not in ("capital", "city", "town", "village"):
            fail(f"settlements.csv:{index}: type {kind} is not a settlement")
        if port not in ("yes", "no"):
            fail(f"settlements.csv:{index}: port must be yes or no")
        if not name:
            fail(f"settlements.csv:{index}: name is missing")
        if nation_name != owner_name[pid]:
            fail(f"settlements.csv:{index}: {nation_name} does not own province {pid}")
        if nation_name != "Unclaimed":
            nation = by_name.get(nation_name)
            if nation is None:
                fail(f"settlements.csv:{index}: nation {nation_name} is not on the published map")
            nation_box = nation.get("bbox") or [0, 0, 0, 0]
            nation_xy = nation.get("capital_xy") or [0, 0]
            if nation_box[0] < WEST or nation_xy[0] < WEST:
                fail(f"settlements.csv:{index}: nation {nation_name} is west of the fog line")
        prefix, _, letter = sector_id.rpartition("-")
        if prefix != str(pid) or letter not in ("A", "B", "C") or sector_id not in geoms:
            fail(f"settlements.csv:{index}: {sector_id} is not a sector of province {pid}")
        if kind in ("capital", "city") and letter != "A":
            fail(f"settlements.csv:{index}: the major city belongs in sector {pid}-A")
        held.setdefault(sector_id, set()).add(kind)
        if kind == "village":
            if sector_id in villages:
                fail(f"settlements.csv:{index}: two villages in {sector_id}")
            villages[sector_id] = name
    missing = [sid for sid in geoms if sid not in held]
    if missing:
        fail(f"settlements.csv: sector {missing[0]} has no settlement")
    for sector_id, kinds in held.items():
        if "village" in kinds and kinds != {"village"}:
            fail(f"settlements.csv: {sector_id} has a village beside another settlement")
    places_by_id = {}
    for place in places:
        if place.get("type") != "village":
            continue
        places_by_id[str(place.get("id"))] = place
    if set(places_by_id) != {"village-" + sid for sid in villages}:
        fail("infrastructure.json villages do not match settlements.csv")
    for sector_id, name in villages.items():
        place = places_by_id["village-" + sector_id]
        if place.get("name") != name or place.get("province") != int(sector_id.split("-", 1)[0]):
            fail(f"infrastructure.json village {sector_id} does not match its settlement")
        x, y = place.get("x"), place.get("y")
        check_point(x, y, f"village {sector_id}", width, height)
        gap = interior_gap(geoms[sector_id], x, y)
        if gap is None or gap < 0.2:
            fail(f"village {sector_id} is not inside its sector")


def check_trade_block(block, where, nations, width, height):
    if block is None:
        return
    if not isinstance(block, dict):
        fail(f"{where}: trade must be an object")
    check_trade_list(block.get("routes"), where, nations, width, height)
    for item in block.get("remove") or []:
        if item is None or item == "" or not isinstance(item, (str, int)) or isinstance(item, bool):
            fail(f"{where}: remove ids must be strings or numbers")


def check_infra_block(block, where, width, height, province_id):
    if block is None:
        return
    if not isinstance(block, dict):
        fail(f"{where}: infrastructure must be an object")
    for row in block.get("places") or []:
        if not isinstance(row, dict):
            fail(f"{where}: place must be an object")
        kind = str(row.get("type") or row.get("kind") or "").lower().replace(" ", "_").replace("-", "_")
        if kind not in PLACE_TYPES:
            fail(f"{where}: unknown place type {kind!r}")
        optional_int(row.get("cycle"), f"{where} {kind} cycle")
        optional_int(row.get("round"), f"{where} {kind} round")
        has_xy = row.get("x") is not None or row.get("y") is not None
        if row.get("province") is not None:
            province_id(row.get("province"), f"{where} {kind}")
        elif not has_xy:
            fail(f"{where}: {kind} needs a province or a point")
        if has_xy:
            check_point(row.get("x"), row.get("y"), f"{where} {kind}", width, height)
    for row in block.get("roads") or []:
        if not isinstance(row, dict):
            fail(f"{where}: road must be an object")
        optional_int(row.get("cycle"), f"{where} road cycle")
        optional_int(row.get("round"), f"{where} road round")
        provinces = row.get("provinces") or []
        points = row.get("points") or row.get("coordinates") or []
        if not isinstance(provinces, list) or not isinstance(points, list):
            fail(f"{where}: road provinces and points must be lists")
        for pid in provinces:
            province_id(pid, f"{where} road")
        for pt in points:
            if not isinstance(pt, list) or len(pt) < 2:
                fail(f"{where}: road point is incomplete")
            check_point(pt[0], pt[1], f"{where} road", width, height)
        if len(provinces) < 2 and len(points) < 2:
            fail(f"{where}: a road needs two provinces or two points")
    for item in block.get("remove") or []:
        if item is None or item == "" or not isinstance(item, (str, int)) or isinstance(item, bool):
            fail(f"{where}: remove ids must be strings or numbers")


def main():
    meta = load(ROOT / "data" / "meta.json")
    global WEST
    WEST = meta["hidden_west_of"]
    width = meta["width"]
    height = meta["height"]
    world = load(ROOT / "data" / "world.json")
    provinces = {int(key): value for key, value in world["provinces"].items()}
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    index = load(ROOT / "data" / "history" / "index.json")
    files = index["snapshots"] if isinstance(index, dict) else index
    if not isinstance(files, list) or not files:
        fail("data/history/index.json needs a list of snapshot files")

    def province_id(value, where):
        if isinstance(value, bool) or not isinstance(value, int):
            fail(f"{where}: province id must be an integer")
        if value >= WORK_MIN:
            fail(f"{where}: {value} is reserved for man-made land")
        prov = provinces.get(value)
        if prov is None:
            fail(f"{where}: province {value} is not on the published map")
        box = prov.get("bbox") or [0, 0, 0, 0]
        xy = prov.get("xy") or [0, 0]
        if box[0] < WEST or xy[0] < WEST:
            fail(f"{where}: province {value} is west of the fog line")

    catalog_path = ROOT / "data" / "infrastructure.json"
    if not catalog_path.is_file():
        fail("missing data/infrastructure.json")
    catalog = load(catalog_path)
    if not isinstance(catalog, dict):
        fail("data/infrastructure.json must be an object")
    if "capitals" in catalog and not isinstance(catalog["capitals"], bool):
        fail("data/infrastructure.json capitals must be true or false")
    check_infra_block(catalog, "infrastructure.json", width, height, province_id)

    trade_path = ROOT / "data" / "trade_routes.json"
    if not trade_path.is_file():
        fail("missing data/trade_routes.json")
    trade = load(trade_path)
    if not isinstance(trade, dict):
        fail("data/trade_routes.json must be an object")
    check_trade_list(trade.get("routes"), "trade_routes.json", nations, width, height)
    check_sectors(nations, width, height, province_id)
    check_settlements(provinces, nations, catalog.get("places") or [], width, height)

    for name in files:
        if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
            fail(f"bad history file name: {name}")
        path = ROOT / "data" / "history" / name
        if not path.is_file():
            fail(f"missing {path}")
        snap = load(path)
        for row in snap.get("transfers") or []:
            province_id(row.get("province", row.get("id")), f"{name} transfer")
        for row in snap.get("occupations") or []:
            province_id(row.get("province", row.get("id")), f"{name} occupation")
        for row in snap.get("colonies") or []:
            province_id(row.get("province", row.get("id")), f"{name} colony")
        reveals = snap.get("reveals") or {}
        for pid in reveals.get("provinces") or []:
            province_id(pid, f"{name} reveal")
        fog = snap.get("fog") or {}
        for pid in fog.get("provinces") or snap.get("conceal") or []:
            province_id(pid, f"{name} fog")
        for work in snap.get("works") or []:
            wid = work.get("id")
            if isinstance(wid, bool) or not isinstance(wid, int) or wid < WORK_MIN:
                fail(f"{name} work id {wid} must be an integer >= {WORK_MIN}")
            if wid in provinces:
                fail(f"{name} work {wid} uses an existing province id")
            if work.get("status") == "removed" and not work.get("rings"):
                continue
            if work.get("rings"):
                check_rings(work["rings"], f"{name} work {wid}", width, height)
        for sea in snap.get("seas") or []:
            check_rings(sea.get("rings"), f"{name} sea {sea.get('id')}", width, height)
        for marker in snap.get("markers") or []:
            x = marker.get("x")
            y = marker.get("y")
            if isinstance(x, bool) or not isinstance(x, (int, float)) or x < WEST:
                fail(f"{name} marker x={x} is west of the fog line")
            if isinstance(y, bool) or not isinstance(y, (int, float)) or y < 0 or y > height or x > width:
                fail(f"{name} marker is outside the map")
        if "infrastructure" in snap:
            check_infra_block(snap.get("infrastructure"), f"{name} infrastructure", width, height, province_id)
        if "trade" in snap:
            check_trade_block(snap.get("trade"), f"{name} trade", nations, width, height)
    print(f"history secrecy ok ({len(files)} snapshots)")


if __name__ == "__main__":
    main()
