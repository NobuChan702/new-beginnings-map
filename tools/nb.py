#!/usr/bin/env python3
"""Safe local edits to published map data (claims, names, transfers, building limits).

Every mutating command validates inputs, shows a diff summary, supports --dry-run,
backs up the target file, runs scripts/check_history.py, then saves (or restores on failure).
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BACKUP_DIR = DATA / ".nb-backups"
CHECK_HISTORY = ROOT / "scripts" / "check_history.py"
WEST = 3592
WORK_MIN = 9000


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def load_world() -> tuple[dict[str, Any], dict[int, dict], dict[int, dict]]:
    path = DATA / "world.json"
    world = json.loads(path.read_text(encoding="utf-8"))
    provinces = {int(k): v for k, v in world["provinces"].items()}
    nations = {int(n["id"]): n for n in world["nations"]}
    return world, provinces, nations


def assert_public_province(pid: int, provinces: dict[int, dict], where: str) -> None:
    if pid >= WORK_MIN:
        raise ValueError(f"{where}: {pid} is reserved for man-made land")
    prov = provinces.get(pid)
    if prov is None:
        raise ValueError(f"{where}: province {pid} is not on the published map")
    box = prov.get("bbox") or [0, 0, 0, 0]
    xy = prov.get("xy") or [0, 0]
    if box[0] < WEST or xy[0] < WEST:
        raise ValueError(f"{where}: province {pid} is west of the fog line")


def assert_public_nation(nid: int, nations: dict[int, dict], where: str) -> None:
    nation = nations.get(nid)
    if nation is None:
        raise ValueError(f"{where}: nation {nid} is not on the published map")
    box = nation.get("bbox") or [0, 0, 0, 0]
    xy = nation.get("capital_xy") or [0, 0]
    if box[0] < WEST or xy[0] < WEST:
        raise ValueError(f"{where}: nation {nid} is west of the fog line")


def json_style(path: Path) -> tuple[str, bool]:
    if not path.is_file():
        return "  ", True
    text = path.read_text(encoding="utf-8")
    compact = "\n" not in text.strip()[:500]
    return ("  ", not compact) if not compact else (", ", False)


def dumps_claims(data: dict[str, Any]) -> str:
    lines = ["{"]
    items = list(data.items())
    for i, (key, value) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        if isinstance(value, str):
            inner = json.dumps(value, ensure_ascii=False)
        else:
            inner = json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
            if inner.startswith("{") and inner.endswith("}"):
                inner = "{ " + inner[1:-1] + " }"
        lines.append(f'  "{key}": {inner}{comma}')
    lines.append("}\n")
    return "\n".join(lines)


def dumps_json(data: Any, path: Path) -> str:
    indent, pretty = json_style(path)
    if pretty:
        return json.dumps(data, indent=2 if indent == "  " else None, ensure_ascii=False) + "\n"
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"


def diff_summary(before: str, after: str, label: str) -> None:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    delta = list(difflib.unified_diff(before_lines, after_lines, fromfile=f"a/{label}", tofile=f"b/{label}"))
    if not delta:
        print("No changes.")
        return
    adds = sum(1 for line in delta if line.startswith("+") and not line.startswith("+++"))
    dels = sum(1 for line in delta if line.startswith("-") and not line.startswith("---"))
    print(f"Diff {label}: +{adds} -{dels} lines")
    cap = 40
    for line in delta[:cap]:
        print(line.rstrip())
    if len(delta) > cap:
        print(f"... ({len(delta) - cap} more diff lines)")


def run_secrecy_check() -> None:
    if not CHECK_HISTORY.is_file():
        raise RuntimeError(f"missing {CHECK_HISTORY}")
    proc = subprocess.run(
        [sys.executable, str(CHECK_HISTORY)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "check_history failed").strip()
        raise RuntimeError(msg)
    line = (proc.stdout or "").strip()
    if line:
        print(line)


def safe_write(path: Path, new_text: str, dry_run: bool) -> None:
    old_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    diff_summary(old_text, new_text, path.relative_to(ROOT).as_posix())
    if old_text == new_text:
        print("Nothing to write.")
        return
    if dry_run:
        print("(dry-run: not writing)")
        return

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_DIR / f"{path.name}.{stamp}.bak"
    if path.is_file():
        shutil.copy2(path, backup)
        print(f"Backup: {backup.relative_to(ROOT)}")

    tmp = path.with_suffix(path.suffix + ".nb.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    try:
        run_secrecy_check()
    except RuntimeError as err:
        if backup.is_file():
            shutil.copy2(backup, path)
            eprint(f"Secrecy check failed; restored {path.name} from backup.")
        else:
            path.unlink(missing_ok=True)
            eprint(f"Secrecy check failed; removed new {path.name}.")
        raise SystemExit(str(err)) from err
    print(f"Wrote {path.relative_to(ROOT)}")


def read_csv_rows(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError(f"{path.name}: empty file")
    return rows[0], rows[1:]


def write_csv_rows(path: Path, header: list[str], body: list[list[str]]) -> str:
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in body:
        writer.writerow(row)
    return buf.getvalue()


def cmd_claim_assign(args: argparse.Namespace) -> None:
    path = DATA / "claims.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError("claims.json must be an object")
    _, provinces, nations = load_world()
    nid = int(args.nation)
    assert_public_nation(nid, nations, "claim assign")
    player = args.player.strip()
    if not player:
        raise ValueError("player name is required")
    short = (args.short or player).strip()
    if short.lower() == "terillia":
        raise ValueError("Terillia is reserved")
    key = str(nid)
    entry: dict[str, str] | str
    if args.legacy_string:
        entry = player
    else:
        entry = {"player": player, "short": short}
    data[key] = entry
    new_text = dumps_claims(data)
    safe_write(path, new_text, args.dry_run)


def cmd_claim_unassign(args: argparse.Namespace) -> None:
    path = DATA / "claims.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    key = str(int(args.nation))
    if key not in data:
        raise ValueError(f"nation {key} has no claim")
    del data[key]
    new_text = dumps_claims(data)
    safe_write(path, new_text, args.dry_run)


def cmd_rename_province(args: argparse.Namespace) -> None:
    path = DATA / "province_names.csv"
    header, body = read_csv_rows(path)
    if header != ["province", "name"]:
        raise ValueError("province_names.csv: header must be province,name")
    _, provinces, _ = load_world()
    pid = int(args.province)
    assert_public_province(pid, provinces, "rename province")
    name = args.name.strip()
    if not name or name.lower() == "terillia":
        raise ValueError("name is missing or reserved")
    replaced = False
    for row in body:
        if row and row[0].strip() == str(pid):
            row[1] = name
            replaced = True
            break
    if not replaced:
        body.append([str(pid), name])
    body.sort(key=lambda r: int(r[0]) if r and r[0].isdigit() else 0)
    new_text = write_csv_rows(path, header, body)
    safe_write(path, new_text, args.dry_run)


def cmd_rename_settlement(args: argparse.Namespace) -> None:
    path = DATA / "settlement_name_overrides.csv"
    header, body = read_csv_rows(path)
    if header != ["sector_id", "name"]:
        raise ValueError("settlement_name_overrides.csv: header must be sector_id,name")
    sector_id = args.sector_id.strip()
    prefix, _, letter = sector_id.rpartition("-")
    if not prefix.isdigit() or letter not in ("A", "B", "C"):
        raise ValueError(f"{sector_id} is not a sector id")
    _, provinces, _ = load_world()
    assert_public_province(int(prefix), provinces, "rename settlement")
    name = args.name.strip()
    if not name or name.lower() == "terillia":
        raise ValueError("name is missing or reserved")
    replaced = False
    for row in body:
        if row and row[0].strip() == sector_id:
            row[1] = name
            replaced = True
            break
    if not replaced:
        body.append([sector_id, name])
    new_text = write_csv_rows(path, header, body)
    safe_write(path, new_text, args.dry_run)


def cmd_transfer(args: argparse.Namespace) -> None:
    world, provinces, nations = load_world()
    from_id = int(args.from_nation)
    to_id = int(args.to_nation)
    assert_public_nation(from_id, nations, "transfer --from")
    assert_public_nation(to_id, nations, "transfer --to")
    if from_id == to_id:
        raise ValueError("from and to nation must differ")
    pids = [int(x) for x in args.provinces]
    if not pids:
        raise ValueError("list at least one province id")

    for pid in pids:
        assert_public_province(pid, provinces, "transfer")
        owner = provinces[pid].get("nation_id")
        if owner != from_id:
            raise ValueError(f"province {pid} is owned by nation {owner}, not {from_id}")

    to_name = nations[to_id]["name"]
    from_n = nations[from_id]
    to_n = nations[to_id]
    from_list = list(from_n.get("provinces") or [])
    to_list = list(to_n.get("provinces") or [])

    for pid in pids:
        world["provinces"][str(pid)]["nation_id"] = to_id
        if pid in from_list:
            from_list.remove(pid)
        if pid not in to_list:
            to_list.append(pid)
    from_list.sort()
    to_list.sort()
    from_n["provinces"] = from_list
    to_n["provinces"] = to_list
    from_n["province_count"] = len(from_list)
    to_n["province_count"] = len(to_list)

    slots_path = DATA / "province_slots.json"
    slots = json.loads(slots_path.read_text(encoding="utf-8"))
    slot_provs = slots.get("provinces") or {}
    for pid in pids:
        row = slot_provs.get(str(pid))
        if isinstance(row, dict):
            row["nation_id"] = to_id
            row["nation"] = to_name

    control_path = DATA / "sector_control.csv"
    header, body = read_csv_rows(control_path)
    if header != ["sector_id", "controller", "cycle"]:
        raise ValueError("sector_control.csv: header must be sector_id,controller,cycle")
    cycle = str(int(args.cycle))
    targets = {f"{pid}-{s}" for pid in pids for s in ("A", "B", "C")}
    for row in body:
        if row and row[0].strip() in targets:
            row[1] = str(to_id)
            row[2] = cycle

    world_text = dumps_json(world, DATA / "world.json")
    slots_text = dumps_json(slots, slots_path)
    control_text = write_csv_rows(control_path, header, body)

    if args.dry_run:
        diff_summary(DATA.joinpath("world.json").read_text(encoding="utf-8"), world_text, "data/world.json")
        diff_summary(slots_path.read_text(encoding="utf-8"), slots_text, "data/province_slots.json")
        diff_summary(control_path.read_text(encoding="utf-8"), control_text, "data/sector_control.csv")
        print("(dry-run: not writing)")
        return

    # Write in order; restore all on failure after first successful write.
    written: list[tuple[Path, Path | None]] = []
    try:
        for path, text in (
            (DATA / "world.json", world_text),
            (slots_path, slots_text),
            (control_path, control_text),
        ):
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = None
            if path.is_file():
                backup = BACKUP_DIR / f"{path.name}.{stamp}.bak"
                shutil.copy2(path, backup)
                print(f"Backup: {backup.relative_to(ROOT)}")
            tmp = path.with_suffix(path.suffix + ".nb.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            written.append((path, backup))
        run_secrecy_check()
    except RuntimeError as err:
        for path, backup in reversed(written):
            if backup and backup.is_file():
                shutil.copy2(backup, path)
        raise SystemExit(str(err)) from err
    print("Wrote data/world.json, data/province_slots.json, data/sector_control.csv")


def cmd_limits_check(args: argparse.Namespace) -> None:
    slots_path = DATA / "province_slots.json"
    slots = json.loads(slots_path.read_text(encoding="utf-8"))
    _, provinces, nations = load_world()
    default_max = args.default_max
    only_type = args.type.strip() if args.type else None

    violations: list[tuple[int, str, int, int]] = []
    for key, row in (slots.get("provinces") or {}).items():
        if not str(key).isdigit():
            continue
        pid = int(key)
        if pid not in provinces:
            continue
        limits = row.get("limits") if isinstance(row, dict) else None
        if not isinstance(limits, dict):
            if default_max is None:
                continue
            built_list = row.get("built") or [] if isinstance(row, dict) else []
            counts: dict[str, int] = {}
            for item in built_list:
                counts[str(item)] = counts.get(str(item), 0) + 1
            for btype, count in counts.items():
                if only_type and btype != only_type:
                    continue
                if count > default_max:
                    violations.append((pid, btype, count, default_max))
            continue
        for btype, spec in limits.items():
            if only_type and btype != only_type:
                continue
            if not isinstance(spec, dict):
                continue
            built = int(spec.get("built") or 0)
            max_val = spec.get("max")
            if max_val is None or max_val == "":
                if default_max is not None:
                    max_val = default_max
                else:
                    continue
            max_n = int(max_val)
            if built > max_n:
                violations.append((pid, btype, built, max_n))

    if not violations:
        print("No building-limit violations found.")
        return

    by_nation: dict[int, list[tuple[int, str, int, int]]] = {}
    for pid, btype, built, max_n in violations:
        nid = provinces[pid].get("nation_id")
        if nid is None:
            nid = -1
        by_nation.setdefault(int(nid), []).append((pid, btype, built, max_n))

    print(f"Provinces over limit ({len(violations)} row(s)):")
    for pid, btype, built, max_n in sorted(violations):
        nid = provinces[pid].get("nation_id")
        nname = nations.get(int(nid), {}).get("short_name") or nations.get(int(nid), {}).get("name") if nid else "?"
        print(f"  province {pid} (nation {nid} {nname}): {btype} built={built} max={max_n}")

    print("\nBy nation:")
    for nid in sorted(by_nation):
        label = nations.get(nid, {}).get("name", "unclaimed") if nid >= 0 else "unknown"
        print(f"  nation {nid} ({label}): {len(by_nation[nid])} violation(s)")
    raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nb.py", description="Edit published map data safely.")
    parser.add_argument("--dry-run", action="store_true", help="Show diff only; do not write.")
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim", help="Player claims on nations (data/claims.json).")
    claim_sub = claim.add_subparsers(dest="claim_cmd", required=True)
    assign = claim_sub.add_parser("assign", help="Assign a player to a nation id.")
    assign.add_argument("nation", help="Nation id (published map).")
    assign.add_argument("player", help="Full player name.")
    assign.add_argument("--short", help="Short label on the map (defaults to player).")
    assign.add_argument(
        "--legacy-string",
        action="store_true",
        help="Store claim as a plain string instead of {player, short}.",
    )
    assign.set_defaults(func=cmd_claim_assign)
    unassign = claim_sub.add_parser("unassign", help="Remove a nation claim.")
    unassign.add_argument("nation", help="Nation id.")
    unassign.set_defaults(func=cmd_claim_unassign)

    rename = sub.add_parser("rename", help="Province or settlement display names.")
    rename_sub = rename.add_subparsers(dest="rename_cmd", required=True)
    rp = rename_sub.add_parser("province", help="Set a row in data/province_names.csv.")
    rp.add_argument("province", help="Province id.")
    rp.add_argument("name", help="Display name.")
    rp.set_defaults(func=cmd_rename_province)
    rs = rename_sub.add_parser("settlement", help="Set data/settlement_name_overrides.csv.")
    rs.add_argument("sector_id", help="Sector id, e.g. 2201-A.")
    rs.add_argument("name", help="Settlement name.")
    rs.set_defaults(func=cmd_rename_settlement)

    transfer = sub.add_parser("transfer", help="Move provinces to another nation (world + slots + sectors).")
    transfer.add_argument("--from", dest="from_nation", required=True, help="Current owner nation id.")
    transfer.add_argument("--to", dest="to_nation", required=True, help="New owner nation id.")
    transfer.add_argument("--cycle", default="0", help="sector_control cycle column (default 0).")
    transfer.add_argument("provinces", nargs="+", help="Province ids to move.")
    transfer.set_defaults(func=cmd_transfer)

    limits = sub.add_parser("limits", help="Building slot checks (data/province_slots.json).")
    limits_sub = limits.add_subparsers(dest="limits_cmd", required=True)
    check = limits_sub.add_parser("check", help="Report built > max (exit 1 if any).")
    check.add_argument("--type", help="Only report this building type (e.g. Fort).")
    check.add_argument(
        "--default-max",
        type=int,
        default=None,
        help="When a province has no limits object, flag types above this count in built[].",
    )
    check.set_defaults(func=cmd_limits_check)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValueError as err:
        eprint(err)
        raise SystemExit(2) from err


if __name__ == "__main__":
    main()
