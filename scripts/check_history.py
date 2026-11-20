#!/usr/bin/env python3
"""Refuse history, infrastructure, trade, sector, or settlement data that publishes the hidden west.

A trade route in the catalog or a snapshot has to be public. Anything else fails the build.
A sector controller has to be a nation already on the published map, or blank for unclaimed.
A village has to sit inside its sector, and every sector needs a settlement.
A fort has to name a published nation and a sector that already has a settlement.
An alliance row has to name a published nation, and each nation can be in one bloc.
Building limits may list only published provinces, and never a hidden ledger field.
River curves stay on the published side of the fog line.
"""

import csv
import json
import math
import sys
from pathlib import Path

import reveal

ROOT = Path(__file__).resolve().parents[1]
WEST = 3592
WORK_MIN = 9000
PROVINCE_BBOXES = []
FOG_RINGS = []


def point_in_ring(x, y, ring):
    if not ring or len(ring) < 3:
        return False
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            denom = yj - yi
            if denom != 0 and x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def revealed_bbox_contains(x, y):
    for minx, miny, maxx, maxy, state in PROVINCE_BBOXES:
        if state not in {"sighted", "charted", "explored"}:
            continue
        if minx <= x <= maxx and miny <= y <= maxy:
            return True
    return False


def in_fog(x, y):
    for rings in FOG_RINGS:
        if not rings or not rings[0]:
            continue
        if not point_in_ring(x, y, rings[0]):
            continue
        if any(point_in_ring(x, y, hole) for hole in rings[1:] if hole):
            continue
        return True
    return False


def secret_point(x, y):
    """None when the point may be published. 'west' or 'fog' when it may not.

    A point inside a published province bbox is the revealed area. Everything
    west of the legacy line, and everything inside a fog polygon, is hidden.
    """
    if isinstance(x, bool) or isinstance(y, bool):
        return "bad"
    try:
        xf = float(x)
        yf = float(y)
    except (TypeError, ValueError):
        return "bad"
    if revealed_bbox_contains(xf, yf):
        return None
    if xf < WEST:
        return "west"
    if in_fog(xf, yf):
        return "fog"
    return None


def anchor_secret(box, xy, prov=None):
    if isinstance(prov, dict) and prov.get("state") == "hidden":
        return True
    points = []
    if isinstance(box, (list, tuple)) and len(box) >= 2:
        points.append((box[0], box[1]))
    if isinstance(xy, (list, tuple)) and len(xy) >= 2:
        points.append((xy[0], xy[1]))
    elif isinstance(xy, (list, tuple)) and len(xy) == 1:
        points.append((xy[0], 0))
    for x, y in points:
        if secret_point(x, y):
            return True
    return False


def load_secret_geometry(provinces):
    global PROVINCE_BBOXES, FOG_RINGS
    PROVINCE_BBOXES = []
    for prov in provinces.values():
        box = (prov or {}).get("bbox")
        if isinstance(box, list) and len(box) >= 4:
            PROVINCE_BBOXES.append(
                (float(box[0]), float(box[1]), float(box[2]), float(box[3]), str((prov or {}).get("state") or ""))
            )
    FOG_RINGS = []
    path = ROOT / "data" / "flat.geojson"
    if not path.is_file():
        return
    for feature in load(path).get("features") or []:
        props = (feature or {}).get("properties") or {}
        if props.get("kind") != "fog":
            continue
        geom = (feature or {}).get("geometry") or {}
        coords = geom.get("coordinates") or []
        if geom.get("type") == "Polygon":
            FOG_RINGS.append(coords)
        elif geom.get("type") == "MultiPolygon":
            FOG_RINGS.extend(coords)


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
            reason = secret_point(x, y)
            if reason == "west":
                fail(f"{where}: x={x} is west of {WEST}")
            if reason == "fog":
                fail(f"{where}: point {(x, y)} is inside a hidden province")
            if x > width or y < 0 or y > height or x < 0:
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
    reason = secret_point(x, y)
    if reason == "west":
        fail(f"{where}: x={x} is west of {WEST}")
    if reason == "fog":
        fail(f"{where}: point {(x, y)} is inside a hidden province")
    if x > width or y < 0 or y > height or x < 0:
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
    if anchor_secret(box, xy):
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
            if anchor_secret(box, xy):
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


def strip_tier(name):
    text = str(name or "").strip()
    if text.endswith(")") and " (" in text:
        return text[: text.rfind(" (")].strip()
    return text


def nation_labels(nation):
    """Id, short name, full name, and the full name without a trailing tier."""
    labels = set()
    if nation.get("id") is not None:
        labels.add(str(nation["id"]))
    short = str(nation.get("short_name") or "").strip()
    full = str(nation.get("name") or "").strip()
    for text in (short, full, strip_tier(full)):
        if text:
            labels.add(text)
    return labels


def check_settlements(provinces, nations, places, width, height):
    path = ROOT / "data" / "settlements.csv"
    if not path.is_file():
        fail("missing data/settlements.csv")
    sectors = load(ROOT / "data" / "sectors.geojson")
    geoms = {}
    for feature in sectors.get("features") or []:
        props = feature.get("properties") or {}
        geoms[props.get("id")] = feature.get("geometry") or {}
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
        if anchor_secret(box, xy):
            fail(f"settlements.csv:{index}: province {pid} is west of the fog line")
        if kind not in ("capital", "city", "town", "village"):
            fail(f"settlements.csv:{index}: type {kind} is not a settlement")
        if port not in ("yes", "no"):
            fail(f"settlements.csv:{index}: port must be yes or no")
        if not name:
            fail(f"settlements.csv:{index}: name is missing")
        owner = provinces[pid].get("nation_id")
        if owner is None:
            if nation_name != "Unclaimed":
                fail(f"settlements.csv:{index}: {nation_name} does not own province {pid}")
            nation = None
        else:
            nation = nations[int(owner)]
            if nation_name not in nation_labels(nation):
                fail(f"settlements.csv:{index}: {nation_name} does not own province {pid}")
        if nation is not None:
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


def check_buildings(provinces, nations):
    path = ROOT / "data" / "buildings.csv"
    if not path.is_file():
        fail("missing data/buildings.csv")
    sectors = load(ROOT / "data" / "sectors.geojson")
    sector_ids = set()
    for feature in sectors.get("features") or []:
        sid = (feature.get("properties") or {}).get("id")
        if isinstance(sid, str):
            sector_ids.add(sid)
    settled = set()
    with (ROOT / "data" / "settlements.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            settled.add(row.get("sector_id"))
    by_name = {nation["short_name"]: nation for nation in nations.values()}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["nation", "province", "sector_id", "building", "cycle_built", "status"]:
        fail("buildings.csv: header must be nation,province,sector_id,building,cycle_built,status")
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 6:
            fail(f"buildings.csv:{index}: expected 6 columns")
        nation_name, province_text, sector_id, building, cycle_text, status = (part.strip() for part in row)
        nation = by_name.get(nation_name)
        if nation is None:
            fail(f"buildings.csv:{index}: nation {nation_name} is not on the published map")
        box = nation.get("bbox") or [0, 0, 0, 0]
        xy = nation.get("capital_xy") or [0, 0]
        if anchor_secret(box, xy):
            fail(f"buildings.csv:{index}: nation {nation_name} is west of the fog line")
        if not province_text.isdigit():
            fail(f"buildings.csv:{index}: province must be an integer")
        pid = int(province_text)
        prov = provinces.get(pid)
        if prov is None:
            fail(f"buildings.csv:{index}: province {pid} is not on the published map")
        prov_box = prov.get("bbox") or [0, 0, 0, 0]
        prov_xy = prov.get("xy") or [0, 0]
        if prov_box[0] < WEST or prov_xy[0] < WEST:
            fail(f"buildings.csv:{index}: province {pid} is west of the fog line")
        prefix, _, letter = sector_id.rpartition("-")
        if prefix != str(pid) or letter not in ("A", "B", "C") or sector_id not in sector_ids:
            fail(f"buildings.csv:{index}: {sector_id} is not a sector of province {pid}")
        if sector_id not in settled:
            fail(f"buildings.csv:{index}: sector {sector_id} has no settlement")
        if building != "fort":
            fail(f"buildings.csv:{index}: building must be fort")
        if not cycle_text.isdigit():
            fail(f"buildings.csv:{index}: cycle_built must be a whole number")
        if status not in ("done", "building"):
            fail(f"buildings.csv:{index}: status must be done or building")


def check_alliances(nations):
    path = ROOT / "data" / "alliances.csv"
    if not path.is_file():
        fail("missing data/alliances.csv")
    by_name = {nation["short_name"]: nation for nation in nations.values()}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["bloc", "nation", "role"]:
        fail("alliances.csv: header must be bloc,nation,role")
    seen = set()
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 3:
            fail(f"alliances.csv:{index}: expected 3 columns")
        bloc, nation_name, role = (part.strip() for part in row)
        if not bloc:
            fail(f"alliances.csv:{index}: bloc is missing")
        nation = by_name.get(nation_name)
        if nation is None:
            fail(f"alliances.csv:{index}: nation {nation_name} is not on the published map")
        box = nation.get("bbox") or [0, 0, 0, 0]
        xy = nation.get("capital_xy") or [0, 0]
        if anchor_secret(box, xy):
            fail(f"alliances.csv:{index}: nation {nation_name} is west of the fog line")
        if role not in ("leader", "ally", "vassal"):
            fail(f"alliances.csv:{index}: role must be leader, ally, or vassal")
        if nation_name in seen:
            fail(f"alliances.csv:{index}: {nation_name} is already in a bloc")
        seen.add(nation_name)


def check_province_slots(provinces):
    path = ROOT / "data" / "province_slots.json"
    if not path.is_file():
        fail("missing data/province_slots.json")
    data = load(path)
    if not isinstance(data, dict) or not isinstance(data.get("provinces"), dict):
        fail("province_slots.json needs a provinces object")
    forbidden = {
        "army",
        "armies",
        "fleet",
        "fleets",
        "stockpile",
        "stockpiles",
        "threat",
        "relation",
        "relations",
        "personality",
        "npc",
    }

    def scan(value, where):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    fail(f"{where}: {key} is not public map data")
                scan(item, where)
        elif isinstance(value, list):
            for item in value:
                scan(item, where)

    scan(data, "province_slots.json")
    for key, row in data["provinces"].items():
        if isinstance(key, bool) or not str(key).isdigit():
            fail(f"province_slots.json: {key} is not a province id")
        pid = int(key)
        prov = provinces.get(pid)
        if prov is None:
            fail(f"province_slots.json: province {pid} is not on the published map")
        box = prov.get("bbox") or [0, 0, 0, 0]
        xy = prov.get("xy") or [0, 0]
        if anchor_secret(box, xy):
            fail(f"province_slots.json: province {pid} is west of the fog line")
        for sector in (row or {}).get("sectors") or []:
            name = str((sector or {}).get("name") or "")
            if name.lower() == "terillia":
                fail("province_slots.json: Terillia is reserved")
    overrides = ROOT / "data" / "settlement_name_overrides.csv"
    if not overrides.is_file():
        fail("missing data/settlement_name_overrides.csv")
    with overrides.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["sector_id", "name"]:
            fail("settlement_name_overrides.csv: header must be sector_id,name")
        for index, item in enumerate(reader, start=2):
            sector_id = (item.get("sector_id") or "").strip()
            name = (item.get("name") or "").strip()
            if name.lower() == "terillia":
                fail("settlement_name_overrides.csv: Terillia is reserved")
            prefix, _, letter = sector_id.rpartition("-")
            if not prefix.isdigit() or letter not in ("A", "B", "C"):
                fail(f"settlement_name_overrides.csv:{index}: {sector_id} is not a sector id")
            pid = int(prefix)
            prov = provinces.get(pid)
            if prov is None:
                fail(f"settlement_name_overrides.csv:{index}: province {pid} is not on the published map")
            box = prov.get("bbox") or [0, 0, 0, 0]
            xy = prov.get("xy") or [0, 0]
            if anchor_secret(box, xy):
                fail(f"settlement_name_overrides.csv:{index}: province {pid} is west of the fog line")


def check_rivers(width, height):
    path = ROOT / "data" / "rivers.geojson"
    if not path.is_file():
        fail("missing data/rivers.geojson")
    data = load(path)
    features = data.get("features") if isinstance(data, dict) else None
    if not isinstance(features, list):
        fail("rivers.geojson needs a feature list")
    for index, feature in enumerate(features):
        coords = ((feature or {}).get("geometry") or {}).get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            fail(f"rivers.geojson:{index}: a river needs two points")
        for point in coords:
            if not isinstance(point, list) or len(point) < 2:
                fail(f"rivers.geojson:{index}: bad point")
            x, y = point[0], point[1]
            if not isinstance(x, (int, float)) or isinstance(x, bool) or not isinstance(y, (int, float)) or isinstance(y, bool):
                fail(f"rivers.geojson:{index}: bad point")
            reason = secret_point(x, y)
            if reason or x > width or y < 0 or y > height or x < 0:
                fail(f"rivers.geojson:{index}: a river leaves the published map")


def check_province_names(provinces):
    path = ROOT / "data" / "province_names.csv"
    if not path.is_file():
        fail("missing data/province_names.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["province", "name"]:
        fail("province_names.csv: header must be province,name")
    seen = set()
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 2:
            fail(f"province_names.csv:{index}: expected 2 columns")
        province_text, name = (part.strip() for part in row)
        if not province_text.isdigit():
            fail(f"province_names.csv:{index}: province must be an integer")
        pid = int(province_text)
        if pid in seen:
            fail(f"province_names.csv:{index}: province {pid} is listed twice")
        seen.add(pid)
        prov = provinces.get(pid)
        if prov is None:
            fail(f"province_names.csv:{index}: province {pid} is not on the published map")
        box = prov.get("bbox") or [0, 0, 0, 0]
        xy = prov.get("xy") or [0, 0]
        if anchor_secret(box, xy):
            fail(f"province_names.csv:{index}: province {pid} is west of the fog line")
        if not name or name.lower() == "terillia":
            fail(f"province_names.csv:{index}: name is missing or reserved")


def check_religions(provinces, nations):
    path = ROOT / "data" / "religions.csv"
    if not path.is_file():
        fail("missing data/religions.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["target", "id", "religion", "color"]:
        fail("religions.csv: header must be target,id,religion,color")
    seen = set()
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 4:
            fail(f"religions.csv:{index}: expected 4 columns")
        target, id_text, religion, color = (part.strip() for part in row)
        if target not in ("nation", "province"):
            fail(f"religions.csv:{index}: target must be nation or province")
        if not id_text.isdigit():
            fail(f"religions.csv:{index}: id must be an integer")
        number = int(id_text)
        key = (target, number)
        if key in seen:
            fail(f"religions.csv:{index}: {target} {number} is listed twice")
        seen.add(key)
        if not religion or religion.lower() == "terillia":
            fail(f"religions.csv:{index}: religion name is missing or reserved")
        if color and not (len(color) in (4, 7) and color.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in color[1:])):
            fail(f"religions.csv:{index}: color must be a hex colour or blank")
        if target == "nation":
            nation = nations.get(number)
            if nation is None:
                fail(f"religions.csv:{index}: nation {number} is not on the published map")
            box = nation.get("bbox") or [0, 0, 0, 0]
            xy = nation.get("capital_xy") or [0, 0]
            if anchor_secret(box, xy):
                fail(f"religions.csv:{index}: nation {number} is west of the fog line")
        else:
            prov = provinces.get(number)
            if prov is None:
                fail(f"religions.csv:{index}: province {number} is not on the published map")
            box = prov.get("bbox") or [0, 0, 0, 0]
            xy = prov.get("xy") or [0, 0]
            if anchor_secret(box, xy):
                fail(f"religions.csv:{index}: province {number} is west of the fog line")


def check_wars(nations):
    path = ROOT / "data" / "wars.csv"
    if not path.is_file():
        fail("missing data/wars.csv")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["nation_a", "nation_b", "state"]:
        fail("wars.csv: header must be nation_a,nation_b,state")
    seen = set()
    for index, row in enumerate(rows[1:], start=2):
        if not row or (len(row) == 1 and row[0].strip() == ""):
            continue
        if len(row) != 3:
            fail(f"wars.csv:{index}: expected 3 columns")
        a_text, b_text, state = (part.strip() for part in row)
        if not a_text.isdigit() or not b_text.isdigit():
            fail(f"wars.csv:{index}: nation ids must be integers")
        nation_a = int(a_text)
        nation_b = int(b_text)
        if nation_a >= nation_b:
            fail(f"wars.csv:{index}: nation_a must be a lower id than nation_b")
        if (nation_a, nation_b) in seen:
            fail(f"wars.csv:{index}: nations {nation_a} and {nation_b} are listed twice")
        seen.add((nation_a, nation_b))
        if state != "active":
            fail(f"wars.csv:{index}: state must be active")
        for number in (nation_a, nation_b):
            nation = nations.get(number)
            if nation is None:
                fail(f"wars.csv:{index}: nation {number} is not on the published map")
                continue
            box = nation.get("bbox") or [0, 0, 0, 0]
            xy = nation.get("capital_xy") or [0, 0]
            if anchor_secret(box, xy):
                fail(f"wars.csv:{index}: nation {number} is west of the fog line")


def check_flags(nations):
    path = ROOT / "data" / "flags.json"
    if not path.is_file():
        fail("missing data/flags.json")
    data = load(path)
    if not isinstance(data, dict):
        fail("flags.json must be an object")
    for key, rel in data.items():
        if not str(key).isdigit() or int(key) not in nations:
            fail(f"flags.json: {key} is not a published nation")
        if not isinstance(rel, str) or not rel or ".." in rel or rel.startswith(("/", "\\")):
            fail(f"flags.json: {key} has a bad path")
        file = (ROOT / rel).resolve()
        if ROOT not in file.parents or not file.is_file():
            fail(f"flags.json: missing {rel}")


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


def baked_province_nations():
    """Nation id painted on each province in the political vectors.

    flat.geojson is the cycle-0 picture. Later conquests must not be baked
    back into it, or earlier turns lose their colours.
    """
    path = ROOT / "data" / "flat.geojson"
    if not path.is_file():
        fail("missing data/flat.geojson")
    baked = {}
    for feature in load(path).get("features") or []:
        props = (feature or {}).get("properties") or {}
        if props.get("kind") != "province":
            continue
        pid = props.get("id")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        nation = props.get("nation_id")
        if nation in (None, ""):
            baked[pid] = None
        elif isinstance(nation, bool) or not isinstance(nation, int):
            fail(f"flat.geojson province {pid} has a bad nation_id")
        else:
            baked[pid] = nation
    return baked


def check_transfer_baselines(snapshots, baked_nations):
    """Transfers accumulate. Cycle 0 must name the painted original owner.

    A later snapshot only lists the provinces that change hands on that date.
    Provinces absent from the folded owner map fall back to world.json, which
    is the latest owner, so a missing cycle-0 row paints the conquest onto
    earlier turns. The baseline owner has to match the baked political map,
    not the current world.json owner.
    """
    if not snapshots:
        return
    baseline = {}
    for row in (snapshots[0] or {}).get("transfers") or []:
        if not isinstance(row, dict):
            continue
        pid = row.get("province", row.get("id"))
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        owner = row.get("owner")
        if isinstance(owner, bool) or not isinstance(owner, int):
            fail(f"cycle-0 baseline for province {pid} needs an integer owner")
        baseline[pid] = owner
    later = []
    seen = set()
    for snap in snapshots[1:]:
        for row in (snap or {}).get("transfers") or []:
            if not isinstance(row, dict):
                continue
            pid = row.get("province", row.get("id"))
            if isinstance(pid, bool) or not isinstance(pid, int) or pid in seen:
                continue
            seen.add(pid)
            later.append(pid)
    missing = [pid for pid in later if pid not in baseline]
    if missing:
        listed = ", ".join(str(pid) for pid in missing)
        fail(
            "cycle-0.json is missing the original owner for transferred province "
            + listed
            + ". Add a cycle-0 transfer for the painted owner before the later delta. "
            + "Do not copy the current world.json owner into cycle 0."
        )
    for pid in later:
        if pid not in baked_nations:
            fail(f"transferred province {pid} is not on the baked political map")
        baked = baked_nations[pid]
        if baseline[pid] != baked:
            fail(
                f"cycle-0 baseline for province {pid} is owner {baseline[pid]} "
                f"but the baked political map shows nation {baked}. "
                "Record the painted original owner, not the current owner, and do not rebuild the political tiles to erase that paint."
            )


def main():
    meta = load(ROOT / "data" / "meta.json")
    global WEST
    WEST = meta["hidden_west_of"]
    width = meta["width"]
    height = meta["height"]
    world = load(ROOT / "data" / "world.json")
    provinces = {int(key): value for key, value in world["provinces"].items()}
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    load_secret_geometry(provinces)
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
        if anchor_secret(box, xy, prov):
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
    check_buildings(provinces, nations)
    check_alliances(nations)
    check_province_slots(provinces)
    check_rivers(width, height)
    check_province_names(provinces)
    check_flags(nations)
    check_religions(provinces, nations)
    check_wars(nations)

    snaps = []
    for name in files:
        if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
            fail(f"bad history file name: {name}")
        path = ROOT / "data" / "history" / name
        if not path.is_file():
            fail(f"missing {path}")
        snap = load(path)
        snaps.append(snap)
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
            if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                fail(f"{name} marker is outside the map")
            reason = secret_point(x, y)
            if reason == "west":
                fail(f"{name} marker x={x} is west of the fog line")
            if reason == "fog":
                fail(f"{name} marker is inside a hidden province")
            if y < 0 or y > height or x < 0 or x > width:
                fail(f"{name} marker is outside the map")
        if "infrastructure" in snap:
            check_infra_block(snap.get("infrastructure"), f"{name} infrastructure", width, height, province_id)
        if "trade" in snap:
            check_trade_block(snap.get("trade"), f"{name} trade", nations, width, height)
    check_transfer_baselines(snaps, baked_province_nations())
    check_reveal_layers(provinces, width, height)
    print(f"history secrecy ok ({len(files)} snapshots)")


def _walk_points(value, found):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[0], (int, float)) and not isinstance(value[0], bool) and isinstance(value[1], (int, float)) and not isinstance(value[1], bool):
            found.append((value[0], value[1]))
            return
        for item in value:
            _walk_points(item, found)


def check_reveal_layers(provinces, width, height):
    """Published deposit, landmark, wind, and hazard files stay inside revealed land."""

    def province_state(pid, where):
        if isinstance(pid, bool) or not isinstance(pid, int) or pid not in provinces:
            fail(f"{where}: province {pid} is not on the published map")
        state = provinces[pid].get("state") or "explored"
        if state == "hidden":
            fail(f"{where}: province {pid} is hidden")
        return state

    def points_ok(value, where):
        found = []
        _walk_points(value, found)
        for x, y in found:
            check_point(x, y, where, width, height)

    deposits_path = ROOT / "data" / "deposits.json"
    if deposits_path.is_file():
        rows = load(deposits_path)
        if isinstance(rows, dict):
            rows = rows.get("deposits") or []
        if not isinstance(rows, list):
            fail("deposits.json must be a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                fail(f"deposits.json:{index} must be an object")
            state = province_state(row.get("province"), f"deposits.json:{index}")
            try:
                reveal.assert_deposit(row, state, f"deposits.json:{index}")
            except SystemExit as exc:
                fail(str(exc))
            points_ok(row.get("xy"), f"deposits.json:{index}")

    landmarks_path = ROOT / "data" / "landmarks.json"
    if landmarks_path.is_file():
        rows = load(landmarks_path)
        if isinstance(rows, dict):
            rows = rows.get("landmarks") or []
        if not isinstance(rows, list):
            fail("landmarks.json must be a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                fail(f"landmarks.json:{index} must be an object")
            state = province_state(row.get("province"), f"landmarks.json:{index}")
            try:
                reveal.assert_landmark(row, state, f"landmarks.json:{index}")
            except SystemExit as exc:
                fail(str(exc))
            if row.get("x") is not None:
                points_ok([row.get("x"), row.get("y")], f"landmarks.json:{index}")

    winds_path = ROOT / "data" / "winds_currents.json"
    if winds_path.is_file():
        doc = load(winds_path)
        if not isinstance(doc, dict):
            fail("winds_currents.json must be an object")
        for kind in ("winds", "currents"):
            for index, row in enumerate(doc.get(kind) or []):
                points_ok(row, f"winds_currents.json {kind} {index}")

    hazards_path = ROOT / "data" / "hazards.json"
    if hazards_path.is_file():
        doc = load(hazards_path)
        if not isinstance(doc, dict):
            fail("hazards.json must be an object")
        for index, row in enumerate(doc.get("areas") or []):
            points_ok(row, f"hazards.json area {index}")
        for index, row in enumerate(doc.get("chokepoints") or []):
            if isinstance(row, dict) and row.get("x") is not None:
                points_ok([row.get("x"), row.get("y")], f"hazards.json chokepoint {index}")


if __name__ == "__main__":
    main()
