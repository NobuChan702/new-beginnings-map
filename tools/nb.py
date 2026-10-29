#!/usr/bin/env python3
"""Safe local edits to published map data (claims, names, transfers, building limits, flags, capitals, season batches).

Every mutating command validates inputs, shows a diff summary, supports --dry-run,
backs up the target file, runs scripts/check_history.py, then saves (or restores on failure).

export runs those checks plus the building-limits check and zips the working tree
diff. It does not commit or push.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BACKUP_DIR = DATA / ".nb-backups"
CHECK_HISTORY = ROOT / "scripts" / "check_history.py"
WEST = 3592
WORK_MIN = 9000
# Windows sync drop. On any other OS the default is ./sync/out (relative to the cwd).
WINDOWS_EXPORT_DIR = r"D:\NewBeginnings\sync\out"
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def set_root(root: Path | None = None) -> None:
    """Point later commands at another checkout. Tests pass a temp copy of the repo."""
    global ROOT, DATA, BACKUP_DIR, CHECK_HISTORY
    ROOT = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[1]
    DATA = ROOT / "data"
    BACKUP_DIR = DATA / ".nb-backups"
    CHECK_HISTORY = ROOT / "scripts" / "check_history.py"


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
    if any(len(line) > 4000 for line in before_lines[:3] + after_lines[:3]):
        print(f"Diff {label}: one long line, {len(before)} -> {len(after)} bytes")
        return
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


def text_changed(old: str, new: str) -> bool:
    """False when the texts match, or the only difference is a trailing newline.

    province_slots.json is one line with no newline at the end. Re-serializing
    it used to append ``\\n`` and rewrite the file even though the JSON was the same.
    """
    if old == new:
        return False
    return old.rstrip("\r\n") != new.rstrip("\r\n")


def safe_write(path: Path, new_text: str, dry_run: bool) -> None:
    # Compare and write the raw bytes. read_text/write_text translate \n to
    # os.linesep, so a CRLF file looked changed and was saved with different endings.
    old_text = path.read_bytes().decode("utf-8") if path.is_file() else ""
    if not text_changed(old_text, new_text):
        print("No changes.")
        print("Nothing to write.")
        return
    diff_summary(old_text, new_text, path.relative_to(ROOT).as_posix())
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
    tmp.write_bytes(new_text.encode("utf-8"))
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


def csv_lineterminator(path: Path) -> str:
    """Keep a CSV on its current newline so a one-cell edit does not rewrite the file."""
    if path.is_file() and b"\r\n" in path.read_bytes():
        return "\r\n"
    return "\n"


def _csv_line(row: list[str]) -> str:
    import io

    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(row)
    return buf.getvalue()


def _split_csv_lines(original: str, newline: str) -> list[str]:
    lines = original.split(newline)
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return [line for line in lines if line != ""]


def write_csv_rows(path: Path, header: list[str], body: list[list[str]]) -> str:
    """Serialize CSV without reordering rows or touching unchanged lines.

    When the parsed rows already match, the original file text is returned so
    a no-op edit does not rewrite the file or its line endings.
    """
    import io

    new_rows = [list(header)] + [list(row) for row in body]
    raw = path.read_bytes() if path.is_file() else b""
    if not raw:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        for row in new_rows:
            writer.writerow(row)
        return buf.getvalue()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    original = raw.decode("utf-8")
    kept = _split_csv_lines(original, newline)
    parsed = [next(csv.reader([line])) for line in kept]
    if parsed == new_rows:
        return original
    out: list[str] = []
    shared = min(len(kept), len(new_rows))
    for index in range(shared):
        if parsed[index] == new_rows[index]:
            out.append(kept[index])
        else:
            out.append(_csv_line(new_rows[index]))
    for row in new_rows[shared:]:
        out.append(_csv_line(row))
    text = newline.join(out)
    if original.endswith(newline):
        text += newline
    return text


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


def apply_rename_province(
    header: list[str],
    body: list[list[str]],
    provinces: dict[int, dict],
    pid: int,
    name: str,
) -> None:
    if header != ["province", "name"]:
        raise ValueError("province_names.csv: header must be province,name")
    assert_public_province(pid, provinces, "rename province")
    name = name.strip()
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


def cmd_rename_province(args: argparse.Namespace) -> None:
    path = DATA / "province_names.csv"
    header, body = read_csv_rows(path)
    _, provinces, _ = load_world()
    apply_rename_province(header, body, provinces, int(args.province), args.name)
    safe_write(path, write_csv_rows(path, header, body), args.dry_run)


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


def apply_transfer(
    world: dict[str, Any],
    provinces: dict[int, dict],
    nations: dict[int, dict],
    slots: dict[str, Any],
    header: list[str],
    body: list[list[str]],
    from_id: int | None,
    to_id: int,
    pids: list[int],
    cycle: str | int,
) -> None:
    """Move provinces to another nation. Mutates world, slots, and the sector-control rows."""
    assert_public_nation(to_id, nations, "transfer --to")
    if from_id is not None:
        assert_public_nation(from_id, nations, "transfer --from")
        if from_id == to_id:
            raise ValueError("from and to nation must differ")
    if not pids:
        raise ValueError("list at least one province id")
    resolved_from: list[int] = []
    for pid in pids:
        assert_public_province(pid, provinces, "transfer")
        owner = provinces[pid].get("nation_id")
        if from_id is None:
            if owner is None:
                raise ValueError(f"province {pid} is unclaimed")
            source = int(owner)
            assert_public_nation(source, nations, "transfer --from")
            if source == to_id:
                raise ValueError("from and to nation must differ")
        else:
            source = from_id
            if owner != source:
                raise ValueError(f"province {pid} is owned by nation {owner}, not {source}")
        resolved_from.append(source)

    to_name = nations[to_id]["name"]
    owners = set(resolved_from)
    for source in owners:
        from_n = nations[source]
        from_list = list(from_n.get("provinces") or [])
        for pid, pid_source in zip(pids, resolved_from):
            if pid_source == source and pid in from_list:
                from_list.remove(pid)
        from_list.sort()
        from_n["provinces"] = from_list
        from_n["province_count"] = len(from_list)
    to_n = nations[to_id]
    to_list = list(to_n.get("provinces") or [])
    for pid in pids:
        world["provinces"][str(pid)]["nation_id"] = to_id
        if pid not in to_list:
            to_list.append(pid)
    to_list.sort()
    to_n["provinces"] = to_list
    to_n["province_count"] = len(to_list)

    slot_provs = slots.get("provinces") or {}
    for pid in pids:
        row = slot_provs.get(str(pid))
        if isinstance(row, dict):
            row["nation_id"] = to_id
            row["nation"] = to_name

    if header != ["sector_id", "controller", "cycle"]:
        raise ValueError("sector_control.csv: header must be sector_id,controller,cycle")
    cycle_text = str(int(cycle))
    targets = {f"{pid}-{s}" for pid in pids for s in ("A", "B", "C")}
    for row in body:
        if row and row[0].strip() in targets:
            row[1] = str(to_id)
            row[2] = cycle_text


def cmd_transfer(args: argparse.Namespace) -> None:
    world, provinces, nations = load_world()
    from_id = int(args.from_nation)
    to_id = int(args.to_nation)
    pids = [int(x) for x in args.provinces]
    slots_path = DATA / "province_slots.json"
    slots = json.loads(slots_path.read_text(encoding="utf-8"))
    control_path = DATA / "sector_control.csv"
    header, body = read_csv_rows(control_path)
    apply_transfer(world, provinces, nations, slots, header, body, from_id, to_id, pids, args.cycle)

    world_text = dumps_json(world, DATA / "world.json")
    slots_text = dumps_json(slots, slots_path)
    control_text = write_csv_rows(control_path, header, body)
    files = (
        (DATA / "world.json", world_text),
        (slots_path, slots_text),
        (control_path, control_text),
    )
    changed_files: list[tuple[Path, str, str]] = []
    for path, text in files:
        old = path.read_bytes().decode("utf-8") if path.is_file() else ""
        if text_changed(old, text):
            changed_files.append((path, text, old))
        else:
            print("No changes.")
    if not changed_files:
        print("Nothing to write.")
        return

    if args.dry_run:
        for path, text, old in changed_files:
            diff_summary(old, text, path.relative_to(ROOT).as_posix())
        print("(dry-run: not writing)")
        return

    # Write in order; restore all on failure after first successful write.
    written: list[tuple[Path, Path | None]] = []
    try:
        for path, text, _old in changed_files:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = None
            if path.is_file():
                backup = BACKUP_DIR / f"{path.name}.{stamp}.bak"
                shutil.copy2(path, backup)
                print(f"Backup: {backup.relative_to(ROOT)}")
            tmp = path.with_suffix(path.suffix + ".nb.tmp")
            tmp.write_bytes(text.encode("utf-8"))
            os.replace(tmp, path)
            written.append((path, backup))
        run_secrecy_check()
    except RuntimeError as err:
        for path, backup in reversed(written):
            if backup and backup.is_file():
                shutil.copy2(backup, path)
        raise SystemExit(str(err)) from err
    names = ", ".join(path.relative_to(ROOT).as_posix() for path, _text, _old in changed_files)
    print(f"Wrote {names}")


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


def _unique_backup(path: Path, stamp: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    candidate = BACKUP_DIR / f"{path.name}.{stamp}.bak"
    n = 2
    while candidate.exists():
        candidate = BACKUP_DIR / f"{path.name}.{stamp}.{n}.bak"
        n += 1
    return candidate


def _rollback(written: list[tuple[Path, Path | None]]) -> None:
    for path, backup in reversed(written):
        if backup is not None and backup.is_file():
            shutil.copy2(backup, path)
            eprint(f"Secrecy check failed; restored {path.name} from backup.")
        else:
            path.unlink(missing_ok=True)
            eprint(f"Secrecy check failed; removed new {path.name}.")


def safe_write_many(items: list[tuple[Path, str | bytes]], dry_run: bool) -> None:
    """Write several files with one diff, one backup pass, and one secrecy check."""
    prepared: list[tuple[Path, bytes, bytes, bool]] = []
    changed = False
    for path, data in items:
        new_b = data.encode("utf-8") if isinstance(data, str) else data
        old_b = path.read_bytes() if path.is_file() else b""
        rel = path.relative_to(ROOT).as_posix()
        if isinstance(data, str):
            old_text = old_b.decode("utf-8") if old_b else ""
            this_changed = text_changed(old_text, data)
            if this_changed:
                diff_summary(old_text, data, rel)
            else:
                print("No changes.")
        elif old_b == new_b:
            this_changed = False
            print(f"No changes to {rel}.")
        else:
            this_changed = True
            action = "replace" if path.is_file() else "add"
            print(f"Binary {rel}: {action} ({len(new_b)} bytes)")
        if this_changed:
            changed = True
        prepared.append((path, new_b, old_b, this_changed))
    if not changed:
        print("Nothing to write.")
        return
    if dry_run:
        print("(dry-run: not writing)")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    written: list[tuple[Path, Path | None]] = []
    try:
        for path, new_b, old_b, this_changed in prepared:
            if not this_changed:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if path.is_file():
                backup = _unique_backup(path, stamp)
                shutil.copy2(path, backup)
                print(f"Backup: {backup.relative_to(ROOT)}")
            tmp = path.with_suffix(path.suffix + ".nb.tmp")
            tmp.write_bytes(new_b)
            try:
                os.replace(tmp, path)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            written.append((path, backup))
        run_secrecy_check()
    except RuntimeError as err:
        _rollback(written)
        raise SystemExit(str(err)) from err
    except Exception:
        _rollback(written)
        raise
    names = ", ".join(path.relative_to(ROOT).as_posix() for path, _backup in written)
    print(f"Wrote {names}")


def nation_slug(name: str) -> str:
    """Slug of the nation name, matching assets/flags/*.png.

    world.json `name` ends with a tier parenthesis such as " (major)". The files
    already in the repo drop that suffix (which is `short_name` for every nation)
    and turn everything else that is not a letter or digit into a hyphen.
    """
    base = re.sub(r"\s*\([^)]*\)\s*$", "", name.strip())
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    if not slug or slug in {".", ".."}:
        raise ValueError(f"nation name {name!r} does not yield a flag slug")
    return slug


def assert_png(path: Path) -> tuple[int, int]:
    """Reject anything that is not a PNG: signature, IHDR, chunk CRCs, and IEND."""
    data = path.read_bytes()
    if len(data) < 8 or data[:8] != PNG_SIG:
        raise ValueError(f"{path}: not a PNG (bad signature)")
    valid_depth = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    pos = 8
    saw_ihdr = False
    saw_iend = False
    width = height = 0
    first = True
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        if length > len(data) or pos + 12 + length > len(data):
            raise ValueError(f"{path}: truncated PNG chunk")
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        crc_got = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        crc_calc = zlib.crc32(ctype + chunk) & 0xFFFFFFFF
        if crc_got != crc_calc:
            raise ValueError(f"{path}: PNG chunk CRC mismatch")
        if first and ctype != b"IHDR":
            raise ValueError(f"{path}: PNG must start with IHDR")
        first = False
        if ctype == b"IHDR":
            if length != 13 or saw_ihdr:
                raise ValueError(f"{path}: bad IHDR")
            width, height, bit_depth, color_type, comp, filt, inter = struct.unpack(">IIBBBBB", chunk)
            if width <= 0 or height <= 0:
                raise ValueError(f"{path}: PNG has empty dimensions")
            if color_type not in valid_depth or bit_depth not in valid_depth[color_type]:
                raise ValueError(f"{path}: PNG color type or bit depth is invalid")
            if comp != 0 or filt != 0 or inter not in (0, 1):
                raise ValueError(f"{path}: PNG IHDR flags are invalid")
            saw_ihdr = True
        if ctype == b"IEND":
            if length != 0:
                raise ValueError(f"{path}: IEND must be empty")
            saw_iend = True
            pos += 12 + length
            break
        pos += 12 + length
    if not saw_ihdr or not saw_iend:
        raise ValueError(f"{path}: PNG is missing IHDR or IEND")
    if pos != len(data):
        raise ValueError(f"{path}: trailing bytes after IEND")
    return width, height


def cmd_flag_set(args: argparse.Namespace) -> None:
    """Copy a real PNG to assets/flags/<slug>.png and point data/flags.json at it."""
    _world, _provinces, nations = load_world()
    nid = int(args.nation)
    assert_public_nation(nid, nations, "flag set")
    src = Path(args.png).expanduser()
    if not src.is_file():
        raise ValueError(f"PNG not found: {src}")
    width, height = assert_png(src)
    nation = nations[nid]
    slug = nation_slug(nation["name"])
    rel = f"assets/flags/{slug}.png"
    dest = (ROOT / "assets" / "flags" / f"{slug}.png").resolve()
    flag_dir = (ROOT / "assets" / "flags").resolve()
    if flag_dir not in dest.parents:
        raise ValueError("flag path escapes assets/flags")
    flags_path = DATA / "flags.json"
    raw = json.loads(flags_path.read_text(encoding="utf-8")) if flags_path.is_file() else {}
    if not isinstance(raw, dict):
        raise ValueError("flags.json must be an object")
    for key, existing in raw.items():
        if existing == rel and str(key) != str(nid):
            raise ValueError(f"{rel} is already used by nation {key}")
    raw[str(nid)] = rel
    print(f"Flag for nation {nid} ({nation['short_name']}): {rel} ({width}x{height})")
    safe_write_many([(dest, src.read_bytes()), (flags_path, dumps_json(raw, flags_path))], args.dry_run)


def _rename_settlement_fields(node: Any, old: str, new: str, nation_id: int) -> bool:
    changed = False
    if isinstance(node, dict):
        nation = node.get("nation")
        same_nation = nation == nation_id or (
            isinstance(nation, str) and nation.isdigit() and int(nation) == nation_id
        )
        if same_nation and node.get("settlement") == old:
            node["settlement"] = new
            changed = True
        for value in node.values():
            if _rename_settlement_fields(value, old, new, nation_id):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _rename_settlement_fields(item, old, new, nation_id):
                changed = True
    return changed


def _retarget_capital_routes(
    old: str,
    new: str,
    nation_id: int,
    pending: dict[Path, str] | None = None,
) -> list[tuple[Path, str]]:
    """Keep trade endpoints that named this capital pointing at the new name."""
    if not old or old == new:
        return []
    paths = [DATA / "trade_routes.json"]
    history = DATA / "history"
    if history.is_dir():
        paths.extend(sorted(history.glob("*.json")))
    if pending:
        for path in pending:
            if path not in paths:
                paths.append(path)
    found: list[tuple[Path, str]] = []
    for path in paths:
        if pending is not None and path in pending:
            data = json.loads(pending[path])
        elif path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            continue
        if not _rename_settlement_fields(data, old, new, nation_id):
            continue
        text = dumps_json(data, path)
        if pending is not None:
            pending[path] = text
        found.append((path, text))
    return found


def cmd_capital_set(args: argparse.Namespace) -> None:
    """Rename the capital the page renders.

    data/world.json stores where the capital sits (`capital_province` and
    `capital_xy`) and does not store the place name. js/map.js reads the name
    from the type=capital row in data/settlements.csv for that province
    (loadCapitalNames). Claim markers at the capital point use short_name, not
    this place name. data/infrastructure.json does not list capital markers;
    the page synthesizes the star from the nation record plus that settlement
    name. data/settlement_name_overrides.csv is what build_settlements.py
    reapplies, so the rename is written there too (sector <province>-A).
    """
    _world, provinces, nations = load_world()
    nid = int(args.nation)
    assert_public_nation(nid, nations, "capital set")
    name = str(args.name).strip()
    if not name or name.lower() == "terillia":
        raise ValueError("name is missing or reserved")
    if any(ch in name for ch in ",\"\r\n"):
        raise ValueError("capital name cannot contain commas or quotes (the map splits settlements.csv on commas)")
    nation = nations[nid]
    cap_pid = int(nation["capital_province"])
    assert_public_province(cap_pid, provinces, "capital set")

    settlements_path = DATA / "settlements.csv"
    header, body = read_csv_rows(settlements_path)
    if header != ["nation", "province", "name", "type", "port", "sector_id"]:
        raise ValueError("settlements.csv: header must be nation,province,name,type,port,sector_id")
    matches = [
        index
        for index, row in enumerate(body)
        if len(row) >= 6 and row[1].strip() == str(cap_pid) and row[3].strip().lower() == "capital"
    ]
    if len(matches) != 1:
        raise ValueError(f"nation {nid} has {len(matches)} capital rows for province {cap_pid}")
    row = body[matches[0]]
    if row[0].strip() != nation["short_name"]:
        raise ValueError(
            f"capital row for province {cap_pid} belongs to {row[0].strip()!r}, not {nation['short_name']}"
        )
    sector_id = row[5].strip()
    if sector_id != f"{cap_pid}-A":
        raise ValueError(f"capital row sector is {sector_id}, expected {cap_pid}-A")
    old_name = row[2]
    row[2] = name

    overrides_path = DATA / "settlement_name_overrides.csv"
    ov_header, ov_body = read_csv_rows(overrides_path)
    if ov_header != ["sector_id", "name"]:
        raise ValueError("settlement_name_overrides.csv: header must be sector_id,name")
    replaced = False
    for ov in ov_body:
        if ov and ov[0].strip() == sector_id:
            ov[1] = name
            replaced = True
    if not replaced:
        ov_body.append([sector_id, name])

    print(
        f"Capital of nation {nid} ({nation['short_name']}), province {cap_pid}: {old_name!r} -> {name!r}"
    )
    items: list[tuple[Path, str | bytes]] = [
        (settlements_path, write_csv_rows(settlements_path, header, body)),
        (overrides_path, write_csv_rows(overrides_path, ov_header, ov_body)),
    ]
    items.extend(_retarget_capital_routes(old_name, name, nid))
    safe_write_many(items, args.dry_run)


def _xy_pair(value: Any) -> list[Any] | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    if isinstance(value[0], bool) or isinstance(value[1], bool):
        return None
    if not isinstance(value[0], (int, float)) or not isinstance(value[1], (int, float)):
        return None
    return [value[0], value[1]]


def _find_place(places: list[dict[str, Any]], province: int, name: str, kind: str) -> dict[str, Any] | None:
    for place in places:
        if place.get("province") == province and place.get("name") == name and place.get("type") == kind:
            return place
    return None


def cmd_capital_move(args: argparse.Namespace) -> None:
    """Move a nation's capital to another province it owns.

    The old capital settlement becomes a city. The target province's city
    sector (sector A, or its city / main settlement if that row is missing)
    becomes type=capital. capital_xy is that settlement's marker in
    infrastructure.json. The province ledger's capital flag and map_label
    follow the move. Claims do not store a capital. --name is written to
    settlement_name_overrides.csv and to the capital row the page reads.
    """
    from batch_ops import run_capital_move

    run_capital_move(args)


def default_export_dir() -> Path:
    if os.name == "nt":
        return Path(WINDOWS_EXPORT_DIR)
    return Path("sync") / "out"


def export_directory(arg: str | None) -> Path:
    path = Path(arg) if arg else default_export_dir()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _git_bytes(root: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_export_files(root: Path) -> tuple[str, list[dict[str, str]]]:
    """Tracked changes versus HEAD, plus untracked files under data/ and assets/."""
    head_proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
    )
    if head_proc.returncode != 0:
        detail = head_proc.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"export needs a git checkout with a HEAD commit ({detail})")
    base = head_proc.stdout.decode("utf-8").strip()
    if _git_bytes(root, "ls-files", "-u").strip():
        raise ValueError("export refused: unmerged files")

    status_of: dict[str, str] = {}
    raw = _git_bytes(root, "diff", "-z", "--name-status", "--no-renames", "HEAD")
    parts = raw.split(b"\0")
    index = 0
    while index + 1 < len(parts):
        code = parts[index].decode("utf-8", "surrogateescape")[:1]
        rel = parts[index + 1].decode("utf-8", "surrogateescape")
        index += 2
        if not rel:
            continue
        if code == "A":
            status_of[rel] = "added"
        elif code == "D":
            status_of[rel] = "deleted"
        else:
            status_of[rel] = "modified"

    others = _git_bytes(root, "ls-files", "-z", "--others", "--exclude-standard", "--", "data", "assets")
    for part in others.split(b"\0"):
        if not part:
            continue
        rel = part.decode("utf-8", "surrogateescape")
        if rel.startswith("data/") or rel.startswith("assets/"):
            status_of.setdefault(rel, "added")

    records: list[dict[str, str]] = []
    root_resolved = root.resolve()
    for rel in sorted(status_of):
        if "\\" in rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise ValueError(f"export refused: unsafe path {rel}")
        status = status_of[rel]
        full = root / rel
        if status == "deleted" or not full.exists():
            blob = _git_bytes(root, "show", f"HEAD:{rel}")
            records.append({"path": rel, "sha256": _sha256(blob), "status": "deleted"})
            continue
        resolved = full.resolve()
        if root_resolved != resolved and root_resolved not in resolved.parents:
            raise ValueError(f"export refused: {rel} escapes the repository")
        if not full.is_file():
            raise ValueError(f"export refused: {rel} is not a regular file")
        records.append({"path": rel, "sha256": _file_sha256(full), "status": status})
    return base, records


def _print_export_plan(dest: Path, base: str, records: list[dict[str, str]]) -> None:
    print(f"Export {dest}")
    print(f"base {base}")
    if not records:
        print("No changed files.")
        return
    for row in records:
        print(f"{row['status']} {row['path']} {row['sha256']}")


def write_export_zip(dest: Path, root: Path, base: str, records: list[dict[str, str]]) -> None:
    if any(row["path"] == "manifest.json" for row in records):
        raise ValueError("export refused: a changed path is named manifest.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"base": base, "files": records}
    payload = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp = dest.with_suffix(dest.suffix + ".nb.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", payload)
            for row in records:
                if row["status"] == "deleted":
                    continue
                archive.write(root / row["path"], arcname=row["path"])
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def run_limits_check() -> None:
    try:
        cmd_limits_check(argparse.Namespace(default_max=None, type=None))
    except SystemExit as err:
        if err.code not in (0, None):
            eprint("export refused: building limits check failed")
            raise SystemExit("export refused: building limits check failed") from err


def cmd_export(args: argparse.Namespace) -> None:
    """Zip the working-tree diff. Never commits and never pushes."""
    dry = bool(getattr(args, "dry_run", False) or getattr(args, "export_dry_run", False))
    try:
        run_secrecy_check()
    except RuntimeError as err:
        eprint(f"export refused: check_history.py failed: {err}")
        raise SystemExit(f"export refused: check_history.py failed: {err}") from err
    run_limits_check()

    base, records = collect_export_files(ROOT)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = export_directory(getattr(args, "out", None))
    dest = directory / f"map-{stamp}.zip"
    if dest.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        dest = directory / f"map-{stamp}.zip"
    _print_export_plan(dest, base, records)
    if dry:
        print("(dry-run: not writing)")
        return
    write_export_zip(dest, ROOT, base, records)
    print(f"Wrote {dest}")


def _marker_rows() -> list[dict[str, Any]]:
    world = json.loads((DATA / "world.json").read_text(encoding="utf-8"))
    infra = json.loads((DATA / "infrastructure.json").read_text(encoding="utf-8"))
    places = {}
    for place in infra.get("places") or []:
        places[(place.get("type"), place.get("province"), place.get("name"))] = place
    capitals = {}
    for nation in world["nations"]:
        xy = nation.get("capital_xy") or [None, None]
        capitals[int(nation["capital_province"])] = {
            "nation_id": int(nation["id"]),
            "x": xy[0],
            "y": xy[1],
            "name": nation.get("short_name"),
        }
    rows = []
    with (DATA / "settlements.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sector = (row.get("sector_id") or "").strip()
            kind = (row.get("type") or "").strip().lower()
            name = (row.get("name") or "").strip()
            if not sector or not str(row.get("province") or "").isdigit():
                continue
            province = int(row["province"])
            if kind == "capital":
                cap = capitals.get(province) or {}
                rows.append(
                    {
                        "sector": sector,
                        "province": province,
                        "name": name,
                        "kind": kind,
                        "x": cap.get("x"),
                        "y": cap.get("y"),
                        "target": "capital",
                        "place_id": None,
                        "nation_id": cap.get("nation_id"),
                    }
                )
                continue
            place = places.get((kind, province, name))
            rows.append(
                {
                    "sector": sector,
                    "province": province,
                    "name": name,
                    "kind": kind,
                    "x": None if place is None else place.get("x"),
                    "y": None if place is None else place.get("y"),
                    "target": "missing" if place is None else "infrastructure",
                    "place_id": None if place is None else place.get("id"),
                    "nation_id": None if place is None else place.get("nation"),
                }
            )
    return rows


def _follow_marker_roads(infra: dict[str, Any], moves: list[dict[str, Any]]) -> int:
    """Keep a road end on a settlement marker when that marker moves."""
    pending = []
    for move in moves:
        if not move.get("ok", True) or not move.get("moved", True):
            continue
        old_x, old_y = move.get("old_x"), move.get("old_y")
        new_x, new_y = move.get("x"), move.get("y")
        if not isinstance(old_x, (int, float)) or not isinstance(new_x, (int, float)):
            continue
        if not isinstance(old_y, (int, float)) or not isinstance(new_y, (int, float)):
            continue
        pending.append(move)
    if not pending:
        return 0
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from road_ends import Land, shift_marker

    land = Land(ROOT)
    total = 0
    for move in pending:
        total += shift_marker(
            infra,
            (move["old_x"], move["old_y"]),
            (move["x"], move["y"]),
            move.get("nation_id"),
            land,
        )
    if total:
        print(f"Road ends following markers: {total}")
    return total


def _apply_marker_moves(moves: list[dict[str, Any]], dry_run: bool) -> None:
    world = json.loads((DATA / "world.json").read_text(encoding="utf-8"))
    infra = json.loads((DATA / "infrastructure.json").read_text(encoding="utf-8"))
    by_id = {str(place.get("id")): place for place in infra.get("places") or []}
    nations = {int(nation["id"]): nation for nation in world["nations"]}
    summary = []
    changed = False
    for move in moves:
        if not move.get("ok", True):
            summary.append(f"{move.get('sector')}: {move.get('reason', 'not moved')}")
            continue
        if not move.get("moved", True):
            continue
        old = (move.get("old_x"), move.get("old_y"))
        summary.append(
            f"{move['sector']} {move.get('kind')} {move.get('name')}: {old} -> ({move['x']}, {move['y']})"
        )
        if move.get("target") == "capital" and move.get("nation_id") in nations:
            nations[int(move["nation_id"])]["capital_xy"] = [move["x"], move["y"]]
            changed = True
        elif move.get("target") == "infrastructure" and str(move.get("place_id")) in by_id:
            place = by_id[str(move["place_id"])]
            place["x"] = move["x"]
            place["y"] = move["y"]
            changed = True
    roads_changed = _follow_marker_roads(infra, moves) > 0
    if roads_changed:
        changed = True
    if not summary:
        print("No marker moves.")
        return
    for line in summary[:40]:
        print(line)
    if len(summary) > 40:
        print(f"... {len(summary) - 40} more")
    if not changed:
        print("Nothing to write.")
        return
    items: list[tuple[Path, str | bytes]] = []
    if any(move.get("target") == "capital" and move.get("moved") for move in moves):
        items.append((DATA / "world.json", dumps_json(world, DATA / "world.json")))
    if roads_changed or any(move.get("target") == "infrastructure" and move.get("moved") for move in moves):
        items.append((DATA / "infrastructure.json", dumps_json(infra, DATA / "infrastructure.json")))
    # These catalogs are one long line. A unified diff of that line is not readable.
    for path, text in items:
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        if old == text:
            continue
        print(f"Will rewrite {path.relative_to(ROOT).as_posix()} ({len(text)} bytes)")
    safe_write_many(items, dry_run)


def cmd_check_markers(args: argparse.Namespace) -> None:
    script = ROOT / "scripts" / "check_settlement_markers.py"
    if not script.is_file():
        raise ValueError(f"missing {script}")
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(ROOT), "--min-px", str(args.min_px)],
        cwd=ROOT,
    )
    raise SystemExit(proc.returncode)


def cmd_settlement_move(args: argparse.Namespace) -> None:
    sector = getattr(args, "sector_id", None)
    auto = bool(getattr(args, "auto", False))
    entire = bool(getattr(args, "all_markers", False))
    xy = getattr(args, "xy", None)
    if auto == (xy is not None):
        raise ValueError("use either --auto or --xy X Y")
    if entire and not auto:
        raise ValueError("--all is only valid with --auto")
    if not entire and not sector:
        raise ValueError("give a sector id, or pass --all --auto")
    if xy is not None:
        script = ROOT / "scripts" / "check_settlement_markers.py"
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--root",
                str(ROOT),
                "--sector",
                sector,
                "--xy",
                str(xy[0]),
                str(xy[1]),
                "--min-px",
                "0",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stdout or proc.stderr or "point is outside the sector").strip()
            raise ValueError(detail)
        moves = []
        for row in _marker_rows():
            if row["sector"] != sector:
                continue
            if row["target"] == "missing":
                raise ValueError(f"{sector}: no infrastructure place for {row['kind']} {row['name']}")
            moves.append(
                {
                    **row,
                    "ok": True,
                    "old_x": row["x"],
                    "old_y": row["y"],
                    "x": round(float(xy[0]), 2),
                    "y": round(float(xy[1]), 2),
                    "moved": True,
                }
            )
        if not moves:
            raise ValueError(f"{sector}: no settlement")
        _apply_marker_moves(moves, args.dry_run)
        return

    script = ROOT / "scripts" / "marker_placement.py"
    if not script.is_file():
        raise ValueError(f"missing {script}")
    command = [sys.executable, str(script), "--root", str(ROOT), "place"]
    if not entire:
        command.extend(["--sector", sector])
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "placement failed").strip()
        raise ValueError(detail)
    moves = json.loads(proc.stdout)
    bad = [move for move in moves if not move.get("ok")]
    for move in bad:
        print(f"{move.get('sector')}: {move.get('reason', 'not placed')}")
    _apply_marker_moves(moves, args.dry_run)


def parse_religion_target(token: str, provinces: dict[int, dict], nations: dict[int, dict]) -> tuple[str, int]:
    text = token.strip()
    kind = None
    raw = text
    lower = text.lower()
    if lower.startswith("province:"):
        kind = "province"
        raw = text.split(":", 1)[1].strip()
    elif lower.startswith("nation:"):
        kind = "nation"
        raw = text.split(":", 1)[1].strip()
    if not raw.isdigit():
        raise ValueError(f"{token} is not a nation or province id")
    number = int(raw)
    is_nation = number in nations
    is_province = number in provinces
    if kind is None:
        if is_nation and is_province:
            raise ValueError(f"{number} is both a nation and a province; use nation:{number} or province:{number}")
        if is_nation:
            kind = "nation"
        elif is_province:
            kind = "province"
        else:
            raise ValueError(f"{number} is not a published nation or province")
    if kind == "nation":
        assert_public_nation(number, nations, "religion set")
    else:
        assert_public_province(number, provinces, "religion set")
    return kind, number


def apply_religion(
    header: list[str],
    body: list[list[str]],
    provinces: dict[int, dict],
    nations: dict[int, dict],
    target: str,
    name: str,
    color: str | None,
) -> tuple[str, int, str]:
    """Set one religion row. ``color`` None keeps an existing colour and leaves a new row blank."""
    if header != ["target", "id", "religion", "color"]:
        raise ValueError("religions.csv: header must be target,id,religion,color")
    kind, number = parse_religion_target(str(target), provinces, nations)
    religion = str(name).strip()
    if not religion or religion.lower() == "terillia":
        raise ValueError("religion name is missing or reserved")
    if any(ch in religion for ch in ",\"\r\n"):
        raise ValueError("religion name cannot contain commas or quotes")
    given = None if color is None else str(color).strip()
    if given and not re.fullmatch(r"#[0-9A-Fa-f]{3}|#[0-9A-Fa-f]{6}", given):
        raise ValueError("color must be a hex colour such as #2f6fbe")
    replaced = False
    for row in body:
        while len(row) < 4:
            row.append("")
        if row[0].strip().lower() == kind and row[1].strip() == str(number):
            row[0] = kind
            row[1] = str(number)
            row[2] = religion
            if given is not None:
                row[3] = given
            replaced = True
            break
    if not replaced:
        body.append([kind, str(number), religion, given or ""])
    shown = given if given is not None else ""
    return kind, number, shown


def cmd_religion_set(args: argparse.Namespace) -> None:
    """Map a nation or a province to a religion name. A province row overrides its nation."""
    _, provinces, nations = load_world()
    path = DATA / "religions.csv"
    if path.is_file():
        header, body = read_csv_rows(path)
    else:
        header, body = ["target", "id", "religion", "color"], []
    kind, number, shown = apply_religion(header, body, provinces, nations, str(args.target), str(args.name), args.color)
    print(f"Religion {kind} {number}: {args.name.strip()}" + (f" {shown}" if shown else ""))
    safe_write(path, write_csv_rows(path, header, body), args.dry_run)


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

    flag = sub.add_parser("flag", help="Nation flags (data/flags.json and assets/flags).")
    flag_sub = flag.add_subparsers(dest="flag_cmd", required=True)
    flag_set = flag_sub.add_parser("set", help="Copy a PNG to assets/flags/<slug>.png and record it.")
    flag_set.add_argument("nation", help="Nation id (published map).")
    flag_set.add_argument("png", help="Path to a PNG file.")
    flag_set.set_defaults(func=cmd_flag_set)

    capital = sub.add_parser("capital", help="Capital place name shown on the map.")
    capital_sub = capital.add_subparsers(dest="capital_cmd", required=True)
    capital_set = capital_sub.add_parser(
        "set",
        help="Rename the capital in settlements.csv and the sector-A override.",
    )
    capital_set.add_argument("nation", help="Nation id (published map).")
    capital_set.add_argument("name", help="Capital place name.")
    capital_set.set_defaults(func=cmd_capital_set)
    capital_move = capital_sub.add_parser(
        "move",
        help="Move a nation's capital to a province it owns.",
    )
    capital_move.add_argument("nation", help="Nation id.")
    capital_move.add_argument("province", help="Province id the nation must already own.")
    capital_move.add_argument("--name", default=None, help="Capital place name, stored in settlement_name_overrides.csv.")
    capital_move.set_defaults(func=cmd_capital_move)

    export = sub.add_parser(
        "export",
        help="Zip files changed vs HEAD plus untracked data/ and assets/. Does not commit or push.",
    )
    export.add_argument(
        "--dry-run",
        action="store_true",
        dest="export_dry_run",
        help="Run the checks and print the manifest, but do not write the zip.",
    )
    export.add_argument(
        "--out",
        default=None,
        help="Directory for map-<stamp>.zip. Default: D:\\NewBeginnings\\sync\\out on Windows, else ./sync/out.",
    )
    export.set_defaults(func=cmd_export)

    check = sub.add_parser("check", help="Read-only checks.")
    check_sub = check.add_subparsers(dest="check_cmd", required=True)
    markers = check_sub.add_parser("markers", help="Report settlement markers on a border or off their polygon.")
    markers.add_argument("--min-px", type=float, default=2.0, help="Flag markers closer than N pixels to a border (default 2).")
    markers.set_defaults(func=cmd_check_markers)

    settlement = sub.add_parser("settlement", help="Move a settlement marker.")
    settlement_sub = settlement.add_subparsers(dest="settlement_cmd", required=True)
    move = settlement_sub.add_parser("move", help="Move one sector marker, or every marker with --all --auto.")
    move.add_argument("sector_id", nargs="?", help="Sector id, for example 878-B.")
    move.add_argument("--all", dest="all_markers", action="store_true", help="With --auto, place every settlement.")
    move.add_argument("--auto", action="store_true", help="Use the inland pole of the sector and province.")
    move.add_argument("--xy", nargs=2, type=float, metavar=("X", "Y"), help="Map-pixel coordinates.")
    move.set_defaults(func=cmd_settlement_move)

    religion = sub.add_parser("religion", help="Religion names for nations and provinces (data/religions.csv).")
    religion_sub = religion.add_subparsers(dest="religion_cmd", required=True)
    religion_set = religion_sub.add_parser("set", help="Set the religion for a nation or province id.")
    religion_set.add_argument("target", help="Nation or province id. Use nation:25 or province:878 when the number is both.")
    religion_set.add_argument("name", help="Religion name. Nobu chooses the real names.")
    religion_set.add_argument("--color", default=None, help="Optional hex colour, such as #2f6fbe. Omit to keep or leave blank.")
    religion_set.set_defaults(func=cmd_religion_set)

    from batch_ops import cmd_apply_batch, cmd_armies

    batch = sub.add_parser(
        "apply-batch",
        help="Apply a season batch of transfers, wars, capital moves, religions, and renames.",
    )
    batch.add_argument("file", help="CSV or JSON batch file.")
    batch.add_argument(
        "--dry-run",
        action="store_true",
        dest="batch_dry_run",
        help="Print a JSON summary and do not write. Also accepted before the subcommand.",
    )
    batch.set_defaults(func=cmd_apply_batch)

    armies = sub.add_parser(
        "armies",
        help="Replace public army and fleet markers from the Armies & Fleets sheet.",
    )
    armies.add_argument("file", help="CSV export of the Armies & Fleets tab.")
    armies.add_argument(
        "--dry-run",
        action="store_true",
        dest="armies_dry_run",
        help="Print a JSON summary and do not write. Also accepted before the subcommand.",
    )
    armies.set_defaults(func=cmd_armies)

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
