#!/usr/bin/env python3
"""Per-nation deposit income from the published deposit list.

This reads public files only. It does not open the reveal bundle or a sheet.

    python3 scripts/deposit_income.py data/deposits.json owners.csv data/buildings.csv data/trade_routes.json

``owners.csv`` is ``province,nation,status`` with status
``none`` / ``outpost`` / ``colony`` / ``integrated``.
``none`` and ``integrated`` pay the full deposit.
A colony pays half of each number, rounded down. An outpost pays nothing.

A Mine on silver (Ag) or gems (Gm) pays gold and does not pay iron.
Luxury trade adds 2 gold for each distinct luxury named in an active
standing deal's ``goods``, to both endpoints.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from reveal import DEPOSIT_INFO, LUXURY_NAMES

RESOURCES = ("iron", "gold", "powder", "food", "manpower", "timber", "horses")

# effects: building alias -> yield. stack False means Farm/Market (and the
# other slash pairs) pay once. Silver's mine and market are different lines
# and both pay when both buildings are present.
RULES = {
    "Fe": {"stack": False, "effects": {"mine": {"iron": 2}}},
    "Ag": {"stack": True, "effects": {"mine": {"gold": 5}, "market": {"gold": 1}}},
    "Gm": {"stack": False, "effects": {"mine": {"gold": 3}}},
    "S": {"stack": False, "effects": {"mine": {"iron": 1, "powder": 1}}},
    "Gt": {"stack": False, "effects": {"farm": {"food": 2, "manpower": 50}}},
    "Na": {"stack": False, "effects": {"farm": {"food": 2, "gold": 1, "powder": 1}, "market": {"food": 2, "gold": 1, "powder": 1}}},
    "Sp": {"stack": False, "effects": {"market": {"gold": 5}}},
    "Dy": {"stack": False, "effects": {"lumber": {"gold": 2}, "market": {"gold": 2}}},
    "Hw": {"stack": False, "effects": {"lumber": {"timber": 2}}},
    "Hr": {"stack": False, "effects": {"farm": {"food": 1, "horses": 1}}},
    "Fu": {"stack": False, "effects": {"lumber": {"gold": 3}, "port": {"gold": 3}}},
    "Pe": {"stack": False, "effects": {"port": {"gold": 3}}},
    "Wh": {"stack": False, "effects": {"port": {"gold": 2, "food": 1}}},
    "Sf": {"stack": False, "effects": {"mine": {"iron": 3}}},
}

# Buildings that must not also pay the generic iron yield. The table already
# pays gold instead. deposit_income never adds that iron.
GOLD_INSTEAD_OF_IRON = {"Ag", "Gm"}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def nation_key(value) -> str:
    return str(value).strip()


def norm_building(value: str) -> str:
    text = " ".join(str(value or "").strip().lower().replace("_", " ").split())
    if text in {"lumber camp", "lumbercamp"}:
        return "lumber"
    return text


def norm_status(value: str) -> str:
    text = str(value or "").strip().lower()
    if text not in {"none", "outpost", "colony", "integrated"}:
        fail(f"status {value!r} must be none, outpost, colony, or integrated")
    return text


def scale_yield(payout: dict[str, int], status: str) -> dict[str, int]:
    if status == "outpost":
        return {}
    factor = 0.5 if status == "colony" else 1
    scaled = {}
    for key, amount in payout.items():
        value = int(amount * factor) if factor < 1 else int(amount)
        if factor < 1:
            value = int(amount) // 2
        if value:
            scaled[key] = value
    return scaled


def _add(total: dict[str, int], payout: dict[str, int]) -> None:
    for key, amount in payout.items():
        total[key] = total.get(key, 0) + int(amount)


def deposit_payout(code: str, buildings: set[str], status: str) -> dict[str, int]:
    """One deposit, given the building aliases present on its province."""
    if code not in RULES:
        fail(f"unknown deposit code {code!r}")
    spec = RULES[code]
    effects = spec["effects"]
    matched: dict[str, int] = {}
    if spec["stack"]:
        for alias, payout in effects.items():
            if alias in buildings:
                _add(matched, payout)
    else:
        for alias, payout in effects.items():
            if alias in buildings:
                matched = dict(payout)
                break
    if not matched:
        return {}
    # Ag/Gm mine pays gold. The table does not add iron on top.
    if code in GOLD_INSTEAD_OF_IRON and "mine" in buildings and matched.get("iron"):
        fail(f"{code} mine must pay gold, not iron")
    return scale_yield(matched, status)


def luxury_names(goods: str) -> list[str]:
    text = str(goods or "").lower()
    found = []
    for name in LUXURY_NAMES:
        if name.lower() in text:
            found.append(name)
    return found


def compute_income(deposits, owners, buildings, routes=None, nation_labels=None) -> dict[str, dict[str, int]]:
    """Return ``{nation: {resource: amount}}``.

    ``nation_labels`` maps a trade-route nation id to the owner-table key.
    Keys also match on their string form, so ``25`` and ``"25"`` are the same.
    """
    labels = {nation_key(key): nation_key(value) for key, value in (nation_labels or {}).items()}
    owner_of: dict[int, tuple[str, str]] = {}
    for row in owners:
        try:
            pid = int(row["province"])
        except (KeyError, TypeError, ValueError):
            fail(f"owner row has a bad province: {row!r}")
        nation = nation_key(row.get("nation", ""))
        if not nation:
            fail(f"owner row for province {pid} has no nation")
        status = norm_status(row.get("status", ""))
        if pid in owner_of:
            fail(f"province {pid} has two owners")
        owner_of[pid] = (nation, status)

    built: dict[int, set[str]] = {}
    for row in buildings:
        try:
            pid = int(row["province"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = norm_building(row.get("building", ""))
        if not kind or kind == "fort":
            continue
        built.setdefault(pid, set()).add(kind)

    totals: dict[str, dict[str, int]] = {}
    for nation, _status in owner_of.values():
        totals.setdefault(nation, {key: 0 for key in RESOURCES})

    for deposit in deposits:
        if not isinstance(deposit, dict):
            fail("deposit row must be an object")
        code = deposit.get("code")
        if code not in DEPOSIT_INFO and code not in RULES:
            fail(f"unknown deposit code {code!r}")
        try:
            pid = int(deposit["province"])
        except (KeyError, TypeError, ValueError):
            fail(f"deposit {code} has a bad province")
        if pid not in owner_of:
            continue
        nation, status = owner_of[pid]
        payout = deposit_payout(str(code), built.get(pid, set()), status)
        _add(totals[nation], payout)

    for route in routes or []:
        if not isinstance(route, dict):
            continue
        if route.get("public") is False:
            continue
        if str(route.get("status") or "").lower() != "active":
            continue
        names = luxury_names(route.get("goods") or "")
        if not names:
            continue
        bonus = 2 * len(names)
        ends = []
        for side in ("from", "to"):
            endpoint = route.get(side) or {}
            if not isinstance(endpoint, dict):
                continue
            raw = endpoint.get("nation")
            if raw is None or raw == "":
                continue
            key = labels.get(nation_key(raw), nation_key(raw))
            ends.append(key)
        for key in ends:
            totals.setdefault(key, {name: 0 for name in RESOURCES})
            totals[key]["gold"] = totals[key].get("gold", 0) + bonus
    return totals


def format_table(totals: dict[str, dict[str, int]]) -> str:
    header = ["nation", *RESOURCES]
    lines = [",".join(header)]
    for nation in sorted(totals):
        row = totals[nation]
        lines.append(",".join([nation, *[str(int(row.get(key, 0))) for key in RESOURCES]]))
    return "\n".join(lines) + "\n"


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        fail("usage: deposit_income.py deposits.json owners.csv buildings.csv [trade_routes.json]")
    deposits_path, owners_path, buildings_path = args[:3]
    deposits = json.loads(Path(deposits_path).read_text(encoding="utf-8"))
    if isinstance(deposits, dict):
        deposits = deposits.get("deposits") or []
    routes = []
    if len(args) >= 4:
        raw = json.loads(Path(args[3]).read_text(encoding="utf-8"))
        routes = raw.get("routes") if isinstance(raw, dict) else raw
    totals = compute_income(deposits, _read_csv(Path(owners_path)), _read_csv(Path(buildings_path)), routes or [])
    sys.stdout.write(format_table(totals))


if __name__ == "__main__":
    main()
