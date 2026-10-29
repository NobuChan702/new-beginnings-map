"""Season batch for nb.py. Imported after nb is loaded."""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import nb

WAR_HEADER = ["nation_a", "nation_b", "state"]
OP_ALIASES = {
    "transfer": "transfer",
    "rename": "rename",
    "religion": "religion",
    "capital": "capital",
    "capital_move": "capital",
    "capital-move": "capital",
    "war": "war",
}


class BatchRejected(ValueError):
    def __init__(self, errors: list[str], label: str = "apply-batch refused"):
        self.errors = list(errors)
        text = label + ":\n" + "\n".join(f"- {item}" for item in self.errors)
        super().__init__(text)


class MapBundle:
    """In-memory copy of the files a season batch can edit."""

    def __init__(self) -> None:
        self.world: dict[str, Any] | None = None
        self.provinces: dict[int, dict] = {}
        self.nations: dict[int, dict] = {}
        self.settlement_header: list[str] | None = None
        self.settlement_body: list[list[str]] | None = None
        self.infra: dict[str, Any] | None = None
        self.slots: dict[str, Any] = {"provinces": {}}
        self.slots_existed = False
        self.claims: dict[str, Any] | None = None
        self.claims_existed = False
        self.override_header = ["sector_id", "name"]
        self.override_body: list[list[str]] = []
        self.override_existed = False
        self.names_header = ["province", "name"]
        self.names_body: list[list[str]] = []
        self.names_existed = False
        self.religion_header = ["target", "id", "religion", "color"]
        self.religion_body: list[list[str]] = []
        self.religion_existed = False
        self.control_header: list[str] | None = None
        self.control_body: list[list[str]] | None = None
        self.control_existed = False
        self.war_header = list(WAR_HEADER)
        self.war_body: list[list[str]] = []
        self.war_existed = False
        self.route_pending: dict[Path, str] = {}
        self.touch_settlements = False
        self.touch_world = False
        self.touch_infra = False
        self.touch_slots = False
        self.touch_overrides = False
        self.touch_claims = False
        self.touch_names = False
        self.touch_religions = False
        self.touch_control = False
        self.touch_wars = False
        self.capital_noop = False

    def _index_world(self) -> None:
        assert self.world is not None
        self.provinces = {int(key): value for key, value in self.world["provinces"].items()}
        self.nations = {int(nation["id"]): nation for nation in self.world["nations"]}

    @classmethod
    def from_disk(cls) -> MapBundle:
        bundle = cls()
        bundle.world, bundle.provinces, bundle.nations = nb.load_world()
        bundle._read_csv("province_names.csv", ["province", "name"], "names")
        bundle._read_csv("religions.csv", ["target", "id", "religion", "color"], "religion")
        bundle._read_csv("settlement_name_overrides.csv", ["sector_id", "name"], "override")
        bundle._read_csv("settlements.csv", ["nation", "province", "name", "type", "port", "sector_id"], "settlement")
        bundle._read_csv("sector_control.csv", ["sector_id", "controller", "cycle"], "control")
        bundle._read_csv("wars.csv", list(WAR_HEADER), "war")
        slots_path = nb.DATA / "province_slots.json"
        if slots_path.is_file():
            bundle.slots = json.loads(slots_path.read_text(encoding="utf-8"))
            bundle.slots_existed = True
        infra_path = nb.DATA / "infrastructure.json"
        if infra_path.is_file():
            bundle.infra = json.loads(infra_path.read_text(encoding="utf-8"))
        claims_path = nb.DATA / "claims.json"
        if claims_path.is_file():
            claims = json.loads(claims_path.read_text(encoding="utf-8"))
            if isinstance(claims, dict):
                bundle.claims = claims
                bundle.claims_existed = True
        return bundle

    def _read_csv(self, name: str, default_header: list[str], kind: str) -> None:
        path = nb.DATA / name
        if path.is_file():
            header, body = nb.read_csv_rows(path)
            existed = True
        else:
            header, body = list(default_header), []
            existed = False
        if kind == "names":
            self.names_header, self.names_body, self.names_existed = header, body, existed
        elif kind == "religion":
            self.religion_header, self.religion_body, self.religion_existed = header, body, existed
        elif kind == "override":
            self.override_header, self.override_body, self.override_existed = header, body, existed
        elif kind == "settlement":
            self.settlement_header, self.settlement_body = (header, body) if existed else (None, None)
        elif kind == "control":
            self.control_header, self.control_body = (header, body) if existed else (None, None)
            self.control_existed = existed
        elif kind == "war":
            self.war_header, self.war_body, self.war_existed = header, body, existed

    def clone(self) -> MapBundle:
        other = MapBundle()
        other.world = json.loads(json.dumps(self.world))
        other._index_world()
        other.settlement_header = None if self.settlement_header is None else list(self.settlement_header)
        other.settlement_body = None if self.settlement_body is None else json.loads(json.dumps(self.settlement_body))
        other.infra = None if self.infra is None else json.loads(json.dumps(self.infra))
        other.slots = json.loads(json.dumps(self.slots))
        other.slots_existed = self.slots_existed
        other.claims = None if self.claims is None else json.loads(json.dumps(self.claims))
        other.claims_existed = self.claims_existed
        other.override_header = list(self.override_header)
        other.override_body = json.loads(json.dumps(self.override_body))
        other.override_existed = self.override_existed
        other.names_header = list(self.names_header)
        other.names_body = json.loads(json.dumps(self.names_body))
        other.names_existed = self.names_existed
        other.religion_header = list(self.religion_header)
        other.religion_body = json.loads(json.dumps(self.religion_body))
        other.religion_existed = self.religion_existed
        other.control_header = None if self.control_header is None else list(self.control_header)
        other.control_body = None if self.control_body is None else json.loads(json.dumps(self.control_body))
        other.control_existed = self.control_existed
        other.war_header = list(self.war_header)
        other.war_body = json.loads(json.dumps(self.war_body))
        other.war_existed = self.war_existed
        other.route_pending = dict(self.route_pending)
        for name in (
            "touch_settlements",
            "touch_world",
            "touch_infra",
            "touch_slots",
            "touch_overrides",
            "touch_claims",
            "touch_names",
            "touch_religions",
            "touch_control",
            "touch_wars",
            "capital_noop",
        ):
            setattr(other, name, getattr(self, name))
        return other

    def payloads(self) -> list[tuple[Path, str]]:
        items: list[tuple[Path, str]] = []
        if self.touch_settlements and self.settlement_header is not None and self.settlement_body is not None:
            path = nb.DATA / "settlements.csv"
            items.append((path, nb.write_csv_rows(path, self.settlement_header, self.settlement_body)))
        if self.touch_world and self.world is not None:
            path = nb.DATA / "world.json"
            items.append((path, nb.dumps_json(self.world, path)))
        if self.touch_infra and self.infra is not None:
            path = nb.DATA / "infrastructure.json"
            items.append((path, nb.dumps_json(self.infra, path)))
        if self.touch_slots:
            path = nb.DATA / "province_slots.json"
            items.append((path, nb.dumps_json(self.slots, path)))
        if self.touch_overrides:
            path = nb.DATA / "settlement_name_overrides.csv"
            items.append((path, nb.write_csv_rows(path, self.override_header, self.override_body)))
        if self.touch_claims and self.claims is not None:
            items.append((nb.DATA / "claims.json", nb.dumps_claims(self.claims)))
        if self.touch_names:
            path = nb.DATA / "province_names.csv"
            items.append((path, nb.write_csv_rows(path, self.names_header, self.names_body)))
        if self.touch_religions:
            path = nb.DATA / "religions.csv"
            items.append((path, nb.write_csv_rows(path, self.religion_header, self.religion_body)))
        if self.touch_control and self.control_header is not None and self.control_body is not None:
            path = nb.DATA / "sector_control.csv"
            items.append((path, nb.write_csv_rows(path, self.control_header, self.control_body)))
        if self.touch_wars:
            path = nb.DATA / "wars.csv"
            items.append((path, nb.write_csv_rows(path, self.war_header, self.war_body)))
        for path in sorted(self.route_pending, key=lambda item: item.as_posix()):
            items.append((path, self.route_pending[path]))
        return items


def _clean_capital_name(name: str | None) -> str | None:
    if name is None:
        return None
    new_name = str(name).strip()
    if not new_name or new_name.lower() == "terillia":
        raise ValueError("name is missing or reserved")
    if any(ch in new_name for ch in ",\"\r\n"):
        raise ValueError("capital name cannot contain commas or quotes (the map splits settlements.csv on commas)")
    return new_name


def apply_capital_move(bundle: MapBundle, nid: int, pid: int, name: str | None) -> list[str]:
    """Move a capital inside ``bundle``. Returns the same log lines the command prints."""
    provinces = bundle.provinces
    nations = bundle.nations
    nb.assert_public_nation(nid, nations, "capital move")
    nb.assert_public_province(pid, provinces, "capital move")
    nation = nations[nid]
    owner = provinces[pid].get("nation_id")
    if owner != nid:
        owner_text = "unclaimed" if owner is None else f"nation {owner}"
        raise ValueError(
            f"nation {nid} ({nation['short_name']}) does not own province {pid}; it is {owner_text}"
        )
    new_name = _clean_capital_name(name)
    old_pid = int(nation["capital_province"])
    if bundle.settlement_header is None or bundle.settlement_body is None:
        raise ValueError("missing data/settlements.csv")
    header = bundle.settlement_header
    body = bundle.settlement_body
    if header != ["nation", "province", "name", "type", "port", "sector_id"]:
        raise ValueError("settlements.csv: header must be nation,province,name,type,port,sector_id")
    short = nation["short_name"]
    capital_rows = [
        index
        for index, row in enumerate(body)
        if len(row) >= 6 and row[0].strip() == short and row[3].strip().lower() == "capital"
    ]
    if len(capital_rows) != 1:
        raise ValueError(f"nation {nid} has {len(capital_rows)} capital settlement rows")
    old_row = body[capital_rows[0]]
    old_capital_name = old_row[2]
    if old_pid == pid and new_name is None:
        bundle.capital_noop = True
        return [f"Province {pid} is already the capital of nation {nid} ({short})."]

    target_sector = f"{pid}-A"
    target_indexes = [index for index, row in enumerate(body) if len(row) >= 6 and row[5].strip() == target_sector]
    created = False
    if target_indexes:
        target = body[target_indexes[0]]
        if target[0].strip() != short:
            raise ValueError(f"{target_sector} belongs to {target[0].strip()!r}, not {short}")
        source_name = target[2]
        source_kind = target[3].strip().lower()
    else:
        city_rows = [
            row
            for row in body
            if len(row) >= 6
            and row[1].strip() == str(pid)
            and row[3].strip().lower() == "city"
            and row[0].strip() == short
        ]
        main_rows = [row for row in body if len(row) >= 6 and row[1].strip() == str(pid) and row[0].strip() == short]
        source = city_rows[0] if city_rows else (main_rows[0] if main_rows else None)
        if source is None:
            raise ValueError(f"province {pid} has no city or settlement to turn into a capital")
        source_name = source[2]
        source_kind = source[3].strip().lower()
        target = [short, str(pid), source_name, "capital", source[4].strip() or "no", target_sector]
        body.append(target)
        created = True

    if not created:
        target[3] = "capital"
    if new_name:
        target[2] = new_name
    if old_pid != pid:
        old_row[3] = "city"

    if bundle.infra is None:
        raise ValueError("missing data/infrastructure.json")
    infra = bundle.infra
    places = infra.setdefault("places", [])
    if not isinstance(places, list):
        raise ValueError("infrastructure.json places must be a list")
    marker = nb._find_place(places, pid, source_name, "city") or nb._find_place(places, pid, source_name, source_kind)
    marker_xy = nb._xy_pair([marker.get("x"), marker.get("y")]) if marker else None
    if marker_xy is None:
        marker_xy = nb._xy_pair(provinces[pid].get("xy"))
    if marker_xy is None:
        raise ValueError(f"province {pid} has no marker position in infrastructure.json")
    if new_name and marker is not None:
        marker["name"] = new_name

    old_xy = nb._xy_pair(nation.get("capital_xy")) or marker_xy
    added_place = None
    if old_pid != pid and nb._find_place(places, old_pid, old_row[2], "city") is None:
        place_id = f"city-{old_pid}"
        taken = {str(place.get("id")) for place in places}
        if place_id in taken:
            place_id = f"city-{old_pid}-former-capital"
        added_place = {
            "id": place_id,
            "type": "city",
            "name": old_row[2],
            "province": old_pid,
            "x": old_xy[0],
            "y": old_xy[1],
            "cycle": 0,
            "nation": nid,
        }
        places.append(added_place)

    if old_pid != pid:
        nation["capital_province"] = pid
        nation["capital_xy"] = marker_xy

    slots = bundle.slots if isinstance(bundle.slots, dict) else {"provinces": {}}
    bundle.slots = slots
    slot_provinces = slots.setdefault("provinces", {})
    ledger_notes: list[str] = []
    for slot_pid, is_capital, label in ((old_pid, False, "city"), (pid, True, "capital")):
        if old_pid == pid and not is_capital:
            continue
        row = slot_provinces.get(str(slot_pid))
        if not isinstance(row, dict):
            continue
        if row.get("capital") is not is_capital:
            row["capital"] = is_capital
            ledger_notes.append(f"ledger province {slot_pid}: capital {str(is_capital).lower()}")
        for sector in row.get("sectors") or []:
            if not isinstance(sector, dict):
                continue
            if str(sector.get("sector") or "") != f"{slot_pid}-A":
                continue
            if sector.get("map_label") != label:
                sector["map_label"] = label
                ledger_notes.append(f"ledger {slot_pid}-A: map_label {label}")
            if is_capital and new_name and sector.get("name") != new_name:
                sector["name"] = new_name
                ledger_notes.append(f"ledger {slot_pid}-A: name {new_name}")

    claims_note = None
    if bundle.claims_existed and isinstance(bundle.claims, dict):
        entry = bundle.claims.get(str(nid))
        if isinstance(entry, dict) and "capital" in entry and entry.get("capital") != pid:
            entry["capital"] = pid
            claims_note = f"claims {nid}: capital {pid}"
            bundle.touch_claims = True

    if new_name:
        if bundle.override_header != ["sector_id", "name"]:
            raise ValueError("settlement_name_overrides.csv: header must be sector_id,name")
        replaced = False
        for ov in bundle.override_body:
            if ov and ov[0].strip() == target_sector:
                ov[1] = new_name
                replaced = True
        if not replaced:
            bundle.override_body.append([target_sector, new_name])
        bundle.touch_overrides = True

    lines: list[str] = []
    if old_pid != pid:
        lines.append(f"Capital of nation {nid} ({short}): province {old_pid} -> {pid}")
        lines.append(f"capital_xy {old_xy} -> {marker_xy}")
        lines.append(f"settlements {old_row[5].strip()}: type capital -> city ({old_row[2]})")
    else:
        lines.append(f"Capital of nation {nid} ({short}) stays in province {pid}")
    action = "create" if created else f"type {source_kind} -> capital"
    lines.append(f"settlements {target_sector}: {action} ({target[2]})")
    if new_name and new_name != source_name:
        lines.append(f"name {source_name!r} -> {new_name!r} in settlements.csv and settlement_name_overrides.csv")
    if added_place is not None:
        lines.append(f"infrastructure add {added_place['id']} at {old_xy}")
    lines.extend(ledger_notes)
    if claims_note:
        lines.append(claims_note)
    else:
        lines.append("claims.json does not record a capital")

    bundle.touch_settlements = True
    bundle.touch_world = True
    bundle.touch_infra = True
    if bundle.slots_existed or ledger_notes:
        bundle.touch_slots = True
    if new_name and new_name != source_name:
        nb._retarget_capital_routes(source_name, new_name, nid, bundle.route_pending)
    if new_name and old_capital_name != new_name and old_capital_name != source_name:
        nb._retarget_capital_routes(old_capital_name, new_name, nid, bundle.route_pending)
    bundle.capital_noop = False
    return lines


def run_capital_move(args: Any) -> None:
    bundle = MapBundle.from_disk()
    lines = apply_capital_move(bundle, int(args.nation), int(args.province), None if args.name is None else str(args.name))
    for line in lines:
        print(line)
    if bundle.capital_noop:
        print("Nothing to write.")
        return
    nb.safe_write_many(bundle.payloads(), args.dry_run)


def apply_war(bundle: MapBundle, action: str, nation_a: int, nation_b: int) -> tuple[int, int]:
    nb.assert_public_nation(nation_a, bundle.nations, "war")
    nb.assert_public_nation(nation_b, bundle.nations, "war")
    if nation_a == nation_b:
        raise ValueError("a nation cannot be at war with itself")
    lo, hi = (nation_a, nation_b) if nation_a < nation_b else (nation_b, nation_a)
    verb = action.strip().lower()
    if verb not in ("start", "end"):
        raise ValueError("war action must be start or end")
    if bundle.war_header != WAR_HEADER:
        raise ValueError("wars.csv: header must be nation_a,nation_b,state")
    index = None
    for i, row in enumerate(bundle.war_body):
        if len(row) < 2:
            continue
        left, right = row[0].strip(), row[1].strip()
        if {left, right} == {str(lo), str(hi)}:
            index = i
            break
    if verb == "start":
        if index is not None:
            raise ValueError(f"nations {lo} and {hi} are already at war")
        bundle.war_body.append([str(lo), str(hi), "active"])
    else:
        if index is None:
            raise ValueError(f"nations {lo} and {hi} are not at war")
        del bundle.war_body[index]
    bundle.touch_wars = True
    return lo, hi


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ValueError(f"{label} must be an integer")
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"{label} must be an integer")
    return int(text)


def _optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _as_int(value, label)


def _cell(raw: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name not in raw:
            continue
        value = raw[name]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _load_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    text = path.read_text(encoding="utf-8-sig")
    suffix = path.suffix.lower()
    stripped = text.lstrip()
    if suffix == ".json" or (suffix not in {".csv", ".txt"} and stripped[:1] in "[{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise ValueError(f"{path.name}: {err}") from err
        if isinstance(data, dict):
            rows = data.get("changes")
            if not isinstance(rows, list):
                raise ValueError(f"{path.name}: JSON object must contain a changes list")
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError(f"{path.name}: JSON must be a list or an object with changes")
        loaded = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError(f"row {index}: each change must be an object")
            loaded.append((index, row))
        return loaded
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "op" not in [name.strip() for name in reader.fieldnames if name]:
        raise ValueError(f"{path.name}: CSV header must include op")
    loaded = []
    for index, row in enumerate(reader, start=2):
        if row is None:
            continue
        values = {(key or "").strip(): value for key, value in row.items() if key}
        if not any(str(value or "").strip() for value in values.values()):
            continue
        loaded.append((index, values))
    return loaded


def _normalize_row(row_number: int, raw: dict[str, Any]) -> dict[str, Any]:
    op_raw = _cell(raw, "op", "type")
    if op_raw is None:
        raise ValueError("op is required")
    op = OP_ALIASES.get(str(op_raw).strip().lower())
    if op is None:
        raise ValueError(f"unknown op {op_raw!r}")
    fields: dict[str, Any] = {}
    if op == "transfer":
        fields["province"] = _as_int(_cell(raw, "province"), "province")
        fields["nation"] = _as_int(_cell(raw, "nation", "to"), "nation")
        fields["from"] = _optional_int(_cell(raw, "from"), "from")
        cycle = _cell(raw, "cycle")
        fields["cycle"] = 0 if cycle is None else _as_int(cycle, "cycle")
    elif op == "rename":
        fields["province"] = _as_int(_cell(raw, "province"), "province")
        name = _cell(raw, "name")
        if name is None:
            raise ValueError("name is required")
        fields["name"] = str(name).strip()
    elif op == "religion":
        target = _cell(raw, "target")
        if target is None:
            raise ValueError("target is required")
        fields["target"] = str(target).strip()
        religion = _cell(raw, "name", "religion")
        if religion is None:
            raise ValueError("name is required")
        fields["name"] = str(religion).strip()
        if "color" in raw and raw.get("color") is not None:
            fields["color"] = str(raw.get("color")).strip()
        else:
            fields["color"] = None
    elif op == "capital":
        fields["nation"] = _as_int(_cell(raw, "nation"), "nation")
        fields["province"] = _as_int(_cell(raw, "province"), "province")
        name = _cell(raw, "name")
        fields["name"] = None if name is None else str(name).strip()
    else:
        action = _cell(raw, "action", "state")
        if action is None:
            raise ValueError("action is required")
        fields["action"] = str(action).strip().lower()
        fields["nation_a"] = _as_int(_cell(raw, "nation_a"), "nation_a")
        fields["nation_b"] = _as_int(_cell(raw, "nation_b"), "nation_b")
    return {"row": row_number, "op": op, "fields": fields}


def parse_batch(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        rows = _load_rows(path)
    except ValueError as err:
        return [], [str(err)]
    ops = []
    errors = []
    for row_number, raw in rows:
        try:
            ops.append(_normalize_row(row_number, raw))
        except ValueError as err:
            errors.append(f"row {row_number}: {err}")
    return ops, errors


def _conflict_key(bundle: MapBundle, op: dict[str, Any]) -> tuple[Any, ...]:
    fields = op["fields"]
    if op["op"] == "transfer":
        return ("transfer", fields["province"])
    if op["op"] == "rename":
        return ("rename", fields["province"])
    if op["op"] == "religion":
        kind, number = nb.parse_religion_target(fields["target"], bundle.provinces, bundle.nations)
        return ("religion", kind, number)
    if op["op"] == "capital":
        return ("capital", fields["nation"])
    nation_a = fields["nation_a"]
    nation_b = fields["nation_b"]
    return ("war", tuple(sorted((nation_a, nation_b))))


def _signature(op: dict[str, Any]) -> str:
    return json.dumps(op["fields"], sort_keys=True, ensure_ascii=False)


def _apply_op(bundle: MapBundle, op: dict[str, Any]) -> dict[str, Any]:
    fields = op["fields"]
    summary: dict[str, Any] = {"row": op["row"], "op": op["op"]}
    if op["op"] == "transfer":
        if bundle.control_header is None or bundle.control_body is None:
            raise ValueError("missing data/sector_control.csv")
        before = (bundle.provinces.get(fields["province"]) or {}).get("nation_id")
        nb.apply_transfer(
            bundle.world,
            bundle.provinces,
            bundle.nations,
            bundle.slots,
            bundle.control_header,
            bundle.control_body,
            fields["from"],
            fields["nation"],
            [fields["province"]],
            fields["cycle"],
        )
        bundle.touch_world = True
        bundle.touch_slots = True
        bundle.touch_control = True
        summary.update(
            {
                "province": fields["province"],
                "from": before if fields["from"] is None else fields["from"],
                "nation": fields["nation"],
                "cycle": str(int(fields["cycle"])),
            }
        )
        return summary
    if op["op"] == "rename":
        nb.apply_rename_province(
            bundle.names_header,
            bundle.names_body,
            bundle.provinces,
            fields["province"],
            fields["name"],
        )
        bundle.touch_names = True
        summary.update({"province": fields["province"], "name": fields["name"]})
        return summary
    if op["op"] == "religion":
        kind, number, shown = nb.apply_religion(
            bundle.religion_header,
            bundle.religion_body,
            bundle.provinces,
            bundle.nations,
            fields["target"],
            fields["name"],
            fields["color"],
        )
        bundle.touch_religions = True
        summary.update(
            {
                "target": f"{kind}:{number}",
                "kind": kind,
                "id": number,
                "name": fields["name"],
                "color": shown,
            }
        )
        return summary
    if op["op"] == "capital":
        lines = apply_capital_move(bundle, fields["nation"], fields["province"], fields["name"])
        summary.update(
            {
                "nation": fields["nation"],
                "province": fields["province"],
                "name": fields["name"],
                "detail": lines,
            }
        )
        return summary
    lo, hi = apply_war(bundle, fields["action"], fields["nation_a"], fields["nation_b"])
    summary.update({"action": fields["action"].strip().lower(), "nation_a": lo, "nation_b": hi})
    return summary


def _changed_paths(bundle: MapBundle) -> list[str]:
    found = []
    for path, data in bundle.payloads():
        new_b = data.encode("utf-8")
        old_b = path.read_bytes() if path.is_file() else b""
        old = old_b.decode("utf-8") if old_b else ""
        if nb.text_changed(old, data) or (not isinstance(data, str) and old_b != new_b):
            found.append(path.relative_to(nb.ROOT).as_posix())
    return found


def run_batch(path: Path, dry_run: bool) -> None:
    if not path.is_file():
        raise ValueError(f"batch file not found: {path}")
    ops, errors = parse_batch(path)
    bundle = MapBundle.from_disk()
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    usable = []
    for op in ops:
        try:
            key = _conflict_key(bundle, op)
        except ValueError as err:
            errors.append(f"row {op['row']}: {err}")
            continue
        previous = seen.get(key)
        if previous is not None:
            if _signature(previous) == _signature(op):
                errors.append(f"row {op['row']}: duplicate of row {previous['row']} ({op['op']})")
            else:
                errors.append(f"row {op['row']}: conflicts with row {previous['row']} ({op['op']})")
            continue
        seen[key] = op
        usable.append(op)

    trial = bundle.clone()
    changes = []
    for op in usable:
        snap = trial.clone()
        try:
            changes.append(_apply_op(trial, op))
        except ValueError as err:
            errors.append(f"row {op['row']}: {err}")
            trial = snap
    if errors:
        raise BatchRejected(errors)

    files = _changed_paths(trial)
    if dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "file": str(path),
                    "count": len(changes),
                    "changes": changes,
                    "files": files,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if not files:
        print("Nothing to write.")
        return
    for change in changes:
        for line in change.get("detail") or []:
            print(line)
    nb.safe_write_many(trial.payloads(), False)
    print(f"Applied {len(changes)} batch change(s).")


def cmd_apply_batch(args: Any) -> None:
    dry = bool(getattr(args, "dry_run", False) or getattr(args, "batch_dry_run", False))
    run_batch(Path(args.file).expanduser(), dry)


ARMY_HEADER = ["nation", "type", "province", "label", "x", "y"]
_HEADER_FIELDS = {
    "nation": ("nation", "nation id", "country", "realm"),
    "type": ("type", "kind", "unit", "unit type"),
    "location": ("location", "province", "province id", "where"),
    "status": ("size/status", "size / status", "size", "status", "strength", "condition"),
    "public": ("public", "published", "visible"),
}
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0"}
_LABEL_WORDS = (("fighting", "Fighting"), ("large", "Large"), ("small", "Small"))


def _norm_header(name: str) -> str:
    text = name.strip().lower().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", text)


def _map_headers(fieldnames: list[str]) -> dict[str, str]:
    aliases = {}
    for field, names in _HEADER_FIELDS.items():
        for name in names:
            aliases[_norm_header(name)] = field
    found: dict[str, str] = {}
    errors = []
    for raw in fieldnames:
        if raw is None:
            continue
        key = _norm_header(raw)
        field = aliases.get(key)
        if field is None:
            continue
        if field in found:
            errors.append(f"two {field} columns ({found[field]!r} and {raw!r})")
            continue
        found[field] = raw
    missing = [field for field in ("nation", "type", "location", "status", "public") if field not in found]
    if missing:
        errors.append("missing column(s): " + ", ".join(missing))
    if errors:
        raise ValueError("armies CSV: " + "; ".join(errors))
    return found


def _force_label(value: str) -> str:
    text = value.strip()
    hits = [label for word, label in _LABEL_WORDS if re.search(r"\b" + word + r"\b", text, re.IGNORECASE)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise ValueError(f"status {value!r} names more than one of Small, Large, and Fighting")
    raise ValueError("status must be Small, Large, or Fighting")


def _force_type(value: str) -> str:
    text = value.strip().lower()
    if text in {"army", "armies"}:
        return "army"
    if text in {"fleet", "fleets", "navy", "ship", "ships"}:
        return "fleet"
    raise ValueError(f"type must be army or fleet, not {value!r}")


def _public_flag(value: str) -> bool:
    text = value.strip().lower()
    if text in _YES:
        return True
    if text in _NO:
        return False
    raise ValueError(f"Public must be yes or no, not {value!r}")


def _resolve_nation(nations: dict[int, dict], raw: str) -> int:
    text = raw.strip()
    if text.isdigit():
        nid = int(text)
        nb.assert_public_nation(nid, nations, "armies")
        return nid
    want = text.casefold()
    matches = []
    for nid, nation in nations.items():
        names = {
            str(nation.get("short_name") or "").casefold(),
            str(nation.get("name") or "").casefold(),
            re.sub(r"\s*\([^)]*\)\s*$", "", str(nation.get("name") or "")).strip().casefold(),
        }
        if want in names:
            matches.append(nid)
    if len(matches) == 1:
        nb.assert_public_nation(matches[0], nations, "armies")
        return matches[0]
    if not matches:
        raise ValueError(f"nation {raw!r} is not on the published map")
    raise ValueError(f"nation {raw!r} matches more than one nation")


def _resolve_province(provinces: dict[int, dict], names: dict[str, list[int]], raw: str) -> int:
    text = raw.strip()
    numbered = re.fullmatch(r"province\s+(\d+)", text, re.IGNORECASE)
    if text.isdigit() or numbered:
        pid = int(text if text.isdigit() else numbered.group(1))
        nb.assert_public_province(pid, provinces, "armies")
        return pid
    matches = names.get(text.casefold()) or []
    if len(matches) == 1:
        nb.assert_public_province(matches[0], provinces, "armies")
        return matches[0]
    if not matches:
        raise ValueError(f"location {raw!r} is not a province id or name")
    raise ValueError(f"location {raw!r} matches more than one province")


def _province_names() -> dict[str, list[int]]:
    path = nb.DATA / "province_names.csv"
    found: dict[str, list[int]] = {}
    if not path.is_file():
        return found
    _header, body = nb.read_csv_rows(path)
    for row in body:
        if len(row) < 2 or not row[0].strip().isdigit():
            continue
        found.setdefault(row[1].strip().casefold(), []).append(int(row[0].strip()))
    return found


def locate_markers(root: Path, province_ids: list[int]) -> list[tuple[float, float]]:
    """Inland points, clear of the province border and the water. One load of the map index."""
    scripts = str(Path(root) / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from marker_placement import inland_province, load_index, round_point

    index = load_index(Path(root))
    avoid: dict[int, list[tuple[float, float]]] = {}
    points = []
    for pid in province_ids:
        found = inland_province(index, pid, avoid.get(pid))
        if found is None:
            raise ValueError(f"province {pid} has no inland point clear of the border and the water")
        x, y = round_point(found[0], found[1])
        avoid.setdefault(pid, []).append((x, y))
        points.append((x, y))
    return points


def _read_army_sheet(path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"{path.name}: CSV header is missing")
    columns = _map_headers(list(reader.fieldnames))
    rows = []
    for index, row in enumerate(reader, start=2):
        if row is None or not any(str(value or "").strip() for value in row.values()):
            continue
        rows.append({"row": index, "raw": row})
    return rows, columns


def plan_armies(path: Path) -> tuple[list[dict[str, Any]], list[str], int]:
    """Return public markers, errors, and the count of private rows. Does not write."""
    if not path.is_file():
        raise ValueError(f"armies file not found: {path}")
    try:
        sheet, columns = _read_army_sheet(path)
    except ValueError as err:
        return [], [str(err)], 0
    _world, provinces, nations = nb.load_world()
    names = _province_names()
    errors: list[str] = []
    parsed = []
    private = 0
    for item in sheet:
        raw = item["raw"]
        row_number = item["row"]
        try:
            nation = _resolve_nation(nations, str(raw.get(columns["nation"]) or ""))
            kind = _force_type(str(raw.get(columns["type"]) or ""))
            province = _resolve_province(provinces, names, str(raw.get(columns["location"]) or ""))
            label = _force_label(str(raw.get(columns["status"]) or ""))
            public = _public_flag(str(raw.get(columns["public"]) or ""))
        except ValueError as err:
            errors.append(f"row {row_number}: {err}")
            continue
        if not public:
            private += 1
            continue
        parsed.append(
            {"row": row_number, "nation": nation, "type": kind, "province": province, "label": label}
        )
    if errors:
        return [], errors, private
    if not parsed:
        return [], [], private
    try:
        points = locate_markers(nb.ROOT, [item["province"] for item in parsed])
    except ValueError as err:
        return [], [str(err)], private
    markers = []
    for item, (x, y) in zip(parsed, points):
        markers.append({**item, "x": x, "y": y})
    return markers, [], private


def armies_text(markers: list[dict[str, Any]]) -> str:
    path = nb.DATA / "armies.csv"
    body = [
        [str(item["nation"]), item["type"], str(item["province"]), item["label"], f"{item['x']:.2f}", f"{item['y']:.2f}"]
        for item in markers
    ]
    return nb.write_csv_rows(path, list(ARMY_HEADER), body)


def run_armies(path: Path, dry_run: bool) -> None:
    markers, errors, private = plan_armies(path)
    if errors:
        raise BatchRejected(errors, "armies refused")
    text = armies_text(markers)
    dest = nb.DATA / "armies.csv"
    old = dest.read_text(encoding="utf-8") if dest.is_file() else ""
    changed = nb.text_changed(old, text)
    summary = {
        "dry_run": dry_run,
        "file": str(path),
        "count": len(markers),
        "skipped_private": private,
        "markers": [
            {
                "row": item["row"],
                "nation": item["nation"],
                "type": item["type"],
                "province": item["province"],
                "label": item["label"],
                "x": item["x"],
                "y": item["y"],
            }
            for item in markers
        ],
        "files": ["data/armies.csv"] if changed else [],
    }
    if dry_run:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return
    if not changed:
        print("Nothing to write.")
        return
    nb.safe_write(dest, text, False)
    print(f"Replaced army and fleet markers ({len(markers)}).")


def cmd_armies(args: Any) -> None:
    dry = bool(getattr(args, "dry_run", False) or getattr(args, "armies_dry_run", False))
    run_armies(Path(args.file).expanduser(), dry)
