#!/usr/bin/env python3
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import nb  # noqa: E402

SOURCE = Path(nb.ROOT)


class NbValidationTests(unittest.TestCase):
    def test_sector_id_validation(self) -> None:
        with self.assertRaises(ValueError):
            nb.cmd_rename_settlement(
                argparse_namespace(sector_id="bad", name="X", dry_run=True)
            )

    def test_transfer_wrong_owner(self) -> None:
        world = {
            "nations": [
                {"id": 1, "name": "A", "bbox": [4000, 0, 5000, 5000], "capital_xy": [4500, 2500], "provinces": [10]},
                {"id": 2, "name": "B", "bbox": [4000, 0, 5000, 5000], "capital_xy": [4500, 2500], "provinces": []},
            ],
            "provinces": {
                "10": {"nation_id": 1, "bbox": [4000, 0, 5000, 5000], "xy": [4500, 2500]},
            },
        }
        with mock.patch.object(nb, "load_world", return_value=(world, {10: world["provinces"]["10"]}, {1: world["nations"][0], 2: world["nations"][1]})):
            with self.assertRaises(ValueError):
                nb.cmd_transfer(
                    argparse_namespace(from_nation="2", to_nation="1", provinces=["10"], cycle="0", dry_run=True)
                )


class NbWriteTests(unittest.TestCase):
    def test_claim_assign_dry_run(self) -> None:
        with mock.patch.object(nb, "safe_write") as sw:
            nb.cmd_claim_assign(
                argparse_namespace(nation="25", player="Alice", short=None, legacy_string=False, dry_run=False)
            )
            sw.assert_called_once()
            new_text = sw.call_args[0][1]
            data = json.loads(new_text)
            self.assertEqual(data["25"]["player"], "Alice")


def argparse_namespace(**kwargs):
    class NS:
        pass

    ns = NS()
    for key, val in kwargs.items():
        setattr(ns, key, val)
    return ns


def tiny_png(width: int = 1, height: int = 1, rgb: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def copy_min_repo(dest: Path) -> None:
    shutil.copytree(SOURCE / "data", dest / "data")
    (dest / "scripts").mkdir(parents=True)
    shutil.copy2(SOURCE / "scripts" / "check_history.py", dest / "scripts" / "check_history.py")
    flag_dir = dest / "assets" / "flags"
    flag_dir.mkdir(parents=True)
    for png in (SOURCE / "assets" / "flags").glob("*.png"):
        shutil.copy2(png, flag_dir / png.name)
    shutil.copy2(SOURCE / "README.md", dest / "README.md")
    shutil.copy2(SOURCE / ".gitignore", dest / ".gitignore")
    backups = dest / "data" / ".nb-backups"
    if backups.exists():
        shutil.rmtree(backups)


def git_quiet(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or f"git {args[0]} failed")
    return proc.stdout


def init_git(repo: Path) -> str:
    git_quiet(repo, "init", "-b", "main")
    git_quiet(repo, "config", "user.email", "nb-test@example.com")
    git_quiet(repo, "config", "user.name", "nb test")
    git_quiet(repo, "config", "commit.gpgsign", "false")
    git_quiet(repo, "config", "core.autocrlf", "false")
    (repo / "NOTES.txt").write_text("tracked\n", encoding="utf-8")
    git_quiet(repo, "add", "-A")
    git_quiet(repo, "commit", "-m", "init")
    return git_quiet(repo, "rev-parse", "HEAD").strip()


class RepoCopyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = Path(tempfile.mkdtemp(prefix="nb-template-"))
        copy_min_repo(cls.template)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.template, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nb-case-"))
        self.repo = self.tmp / "repo"
        shutil.copytree(self.template, self.repo)
        self.saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(self.repo)

    def tearDown(self) -> None:
        nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)


class FlagCommandTests(RepoCopyCase):
    def test_slug_matches_stored_flag_names(self) -> None:
        self.assertEqual(nb.nation_slug("Holy Empire of Sanbreque (major)"), "holy-empire-of-sanbreque")
        self.assertEqual(nb.nation_slug("Verillian Empire (major)"), "verillian-empire")
        self.assertEqual(nb.nation_slug("The Sevarian League (minor)"), "the-sevarian-league")

    def test_rejects_a_file_that_is_not_a_png(self) -> None:
        bogus = self.tmp / "not.png"
        bogus.write_bytes(b"not a png")
        flags = (self.repo / "data" / "flags.json").read_bytes()
        with self.assertRaises(ValueError):
            nb.cmd_flag_set(argparse_namespace(nation="8", png=str(bogus), dry_run=False))
        self.assertEqual((self.repo / "data" / "flags.json").read_bytes(), flags)
        self.assertFalse((self.repo / "assets" / "flags" / "zaneth.png").exists())
        self.assertFalse((self.repo / "data" / ".nb-backups").exists())

    def test_rejects_a_png_with_a_bad_chunk_crc(self) -> None:
        raw = bytearray(tiny_png())
        raw[40] ^= 0xFF
        broken = self.tmp / "bad.png"
        broken.write_bytes(raw)
        with self.assertRaises(ValueError) as ctx:
            nb.assert_png(broken)
        self.assertIn("CRC", str(ctx.exception))

    def test_dry_run_does_not_copy_or_edit_flags(self) -> None:
        src = self.tmp / "zaneth.png"
        src.write_bytes(tiny_png())
        before = (self.repo / "data" / "flags.json").read_text(encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            nb.cmd_flag_set(argparse_namespace(nation="8", png=str(src), dry_run=True))
        text = buf.getvalue()
        self.assertIn("assets/flags/zaneth.png", text)
        self.assertIn("(dry-run: not writing)", text)
        self.assertEqual((self.repo / "data" / "flags.json").read_text(encoding="utf-8"), before)
        self.assertFalse((self.repo / "assets" / "flags" / "zaneth.png").exists())
        self.assertFalse((self.repo / "data" / ".nb-backups").exists())

    def test_set_copies_png_and_updates_flags_json(self) -> None:
        src = self.tmp / "zaneth.png"
        payload = tiny_png(2, 2, (1, 2, 3))
        src.write_bytes(payload)
        nb.cmd_flag_set(argparse_namespace(nation="8", png=str(src), dry_run=False))
        dest = self.repo / "assets" / "flags" / "zaneth.png"
        self.assertEqual(dest.read_bytes(), payload)
        flags = json.loads((self.repo / "data" / "flags.json").read_text(encoding="utf-8"))
        self.assertEqual(flags["8"], "assets/flags/zaneth.png")
        self.assertEqual(list(flags)[-1], "8")
        self.assertTrue(any((self.repo / "data" / ".nb-backups").glob("flags.json.*.bak")))

    def test_replacing_an_existing_flag_keeps_the_slug_path(self) -> None:
        src = self.tmp / "verillia.png"
        payload = tiny_png(1, 1, (9, 9, 9))
        src.write_bytes(payload)
        before = (self.repo / "data" / "flags.json").read_text(encoding="utf-8")
        nb.cmd_flag_set(argparse_namespace(nation="21", png=str(src), dry_run=False))
        dest = self.repo / "assets" / "flags" / "verillian-empire.png"
        self.assertEqual(dest.read_bytes(), payload)
        self.assertEqual((self.repo / "data" / "flags.json").read_text(encoding="utf-8"), before)
        self.assertTrue(any((self.repo / "data" / ".nb-backups").glob("verillian-empire.png.*.bak")))

    def test_secrecy_failure_restores_the_backup(self) -> None:
        script = self.repo / "scripts" / "check_history.py"
        script.write_text("import sys\nsys.stderr.write('boom secrecy\\n')\nraise SystemExit(1)\n", encoding="utf-8")
        original = (self.repo / "data" / "flags.json").read_bytes()
        src = self.tmp / "zaneth.png"
        src.write_bytes(tiny_png())
        with self.assertRaises(SystemExit) as ctx:
            nb.cmd_flag_set(argparse_namespace(nation="8", png=str(src), dry_run=False))
        self.assertIn("boom secrecy", str(ctx.exception))
        self.assertEqual((self.repo / "data" / "flags.json").read_bytes(), original)
        self.assertFalse((self.repo / "assets" / "flags" / "zaneth.png").exists())
        self.assertTrue(any((self.repo / "data" / ".nb-backups").glob("flags.json.*.bak")))

    def test_unknown_nation_is_rejected(self) -> None:
        src = self.tmp / "zaneth.png"
        src.write_bytes(tiny_png())
        with self.assertRaises(ValueError):
            nb.cmd_flag_set(argparse_namespace(nation="999999", png=str(src), dry_run=True))


class CapitalCommandTests(RepoCopyCase):
    def test_dry_run_leaves_settlements_and_overrides_unchanged(self) -> None:
        settlements = (self.repo / "data" / "settlements.csv").read_bytes()
        overrides = (self.repo / "data" / "settlement_name_overrides.csv").read_bytes()
        world = (self.repo / "data" / "world.json").read_bytes()
        buf = io.StringIO()
        with redirect_stdout(buf):
            nb.cmd_capital_set(argparse_namespace(nation="8", name="Zaneth Hold", dry_run=True))
        self.assertIn("(dry-run: not writing)", buf.getvalue())
        self.assertEqual((self.repo / "data" / "settlements.csv").read_bytes(), settlements)
        self.assertEqual((self.repo / "data" / "settlement_name_overrides.csv").read_bytes(), overrides)
        self.assertEqual((self.repo / "data" / "world.json").read_bytes(), world)
        self.assertFalse((self.repo / "data" / ".nb-backups").exists())

    def test_set_renames_the_settlement_row_and_adds_the_override(self) -> None:
        world = (self.repo / "data" / "world.json").read_bytes()
        cycle = (self.repo / "data" / "history" / "cycle-0.json").read_bytes()
        infra = (self.repo / "data" / "infrastructure.json").read_bytes()
        trade = {
            "routes": [
                {
                    "id": "zaneth-grain",
                    "public": True,
                    "kind": "land",
                    "status": "active",
                    "goods": "Grain",
                    "from": {"nation": 8, "settlement": "Zaneth"},
                    "to": {"nation": 25, "settlement": "Constantinople"},
                }
            ]
        }
        (self.repo / "data" / "trade_routes.json").write_text(
            json.dumps(trade, indent=2) + "\n", encoding="utf-8"
        )
        history = json.loads(cycle)
        history["trade"] = {
            "routes": [
                {
                    "id": "zaneth-wool",
                    "public": True,
                    "kind": "land",
                    "status": "active",
                    "goods": "Wool",
                    "from": {"nation": 9, "settlement": "Zaneth"},
                    "to": {"nation": 8, "settlement": "Zaneth"},
                }
            ]
        }
        (self.repo / "data" / "history" / "cycle-0.json").write_text(
            json.dumps(history, indent=2) + "\n", encoding="utf-8"
        )
        nb.cmd_capital_set(argparse_namespace(nation="8", name="Zaneth Hold", dry_run=False))
        raw = (self.repo / "data" / "settlements.csv").read_bytes()
        self.assertIn(b"Zaneth,1459,Zaneth Hold,capital,no,1459-A\r\n", raw)
        self.assertNotIn(b"Zaneth,1459,Zaneth,capital,no,1459-A", raw)
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        overrides = (self.repo / "data" / "settlement_name_overrides.csv").read_text(encoding="utf-8")
        self.assertIn("1459-A,Zaneth Hold\n", overrides)
        routes = json.loads((self.repo / "data" / "trade_routes.json").read_text(encoding="utf-8"))
        self.assertEqual(routes["routes"][0]["from"]["settlement"], "Zaneth Hold")
        self.assertEqual(routes["routes"][0]["to"]["settlement"], "Constantinople")
        snap = json.loads((self.repo / "data" / "history" / "cycle-0.json").read_text(encoding="utf-8"))
        ends = snap["trade"]["routes"][0]
        self.assertEqual(ends["from"]["settlement"], "Zaneth")
        self.assertEqual(ends["to"]["settlement"], "Zaneth Hold")
        self.assertEqual((self.repo / "data" / "world.json").read_bytes(), world)
        self.assertEqual((self.repo / "data" / "infrastructure.json").read_bytes(), infra)
        self.assertTrue(any((self.repo / "data" / ".nb-backups").glob("settlements.csv.*.bak")))

    def test_set_updates_an_existing_override_row(self) -> None:
        nb.cmd_capital_set(argparse_namespace(nation="21", name="New Verillia", dry_run=False))
        settlements = (self.repo / "data" / "settlements.csv").read_text(encoding="utf-8")
        self.assertIn("Verillian Empire,2246,New Verillia,capital,no,2246-A", settlements)
        overrides = (self.repo / "data" / "settlement_name_overrides.csv").read_text(encoding="utf-8")
        self.assertIn("2246-A,New Verillia\n", overrides)
        self.assertNotIn("2246-A,Verillia\n", overrides)

    def test_rejects_commas_and_the_reserved_name(self) -> None:
        before = (self.repo / "data" / "settlements.csv").read_bytes()
        with self.assertRaises(ValueError):
            nb.cmd_capital_set(argparse_namespace(nation="8", name="Zaneth, Hold", dry_run=False))
        with self.assertRaises(ValueError):
            nb.cmd_capital_set(argparse_namespace(nation="8", name="Terillia", dry_run=False))
        self.assertEqual((self.repo / "data" / "settlements.csv").read_bytes(), before)


class ExportCommandTests(RepoCopyCase):
    def test_windows_default_directory_constant(self) -> None:
        self.assertEqual(nb.WINDOWS_EXPORT_DIR, r"D:\NewBeginnings\sync\out")
        self.assertEqual(nb.default_export_dir(), Path("sync") / "out")

    def test_dry_run_runs_checks_and_does_not_write_the_zip(self) -> None:
        head = init_git(self.repo)
        (self.repo / "README.md").write_text(
            (self.repo / "README.md").read_text(encoding="utf-8") + "\nnote\n", encoding="utf-8"
        )
        out = self.tmp / "bundle"
        previous = Path.cwd()
        os.chdir(self.repo)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                nb.main(["--dry-run", "export"])
                nb.main(["export", "--dry-run", "--out", str(out)])
        finally:
            os.chdir(previous)
        text = buf.getvalue()
        self.assertIn("history secrecy ok", text)
        self.assertIn("No building-limit violations found.", text)
        self.assertIn("sync/out", text)
        self.assertIn("modified README.md", text)
        self.assertIn(head, text)
        self.assertIn("(dry-run: not writing)", text)
        self.assertFalse(out.exists())
        self.assertFalse((self.repo / "sync").exists())
        self.assertEqual(git_quiet(self.repo, "rev-parse", "HEAD").strip(), head)

    def test_zip_lists_status_and_sha256_without_committing(self) -> None:
        head = init_git(self.repo)
        notes_bytes = (self.repo / "NOTES.txt").read_bytes()
        claims_path = self.repo / "data" / "claims.json"
        claims_path.write_text(
            claims_path.read_text(encoding="utf-8").replace('"player": "Tom"', '"player": "Thomas"', 1),
            encoding="utf-8",
        )
        (self.repo / "data" / "extra.txt").write_text("hello\n", encoding="utf-8")
        (self.repo / "scratch.txt").write_text("nope\n", encoding="utf-8")
        (self.repo / "assets" / "flags" / "spare.png").write_bytes(b"spare")
        readme = self.repo / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nExported.\n", encoding="utf-8")
        (self.repo / "NOTES.txt").unlink()
        out = self.tmp / "bundle"
        nb.cmd_export(argparse_namespace(dry_run=False, export_dry_run=False, out=str(out)))
        zips = list(out.glob("map-*.zip"))
        self.assertEqual(len(zips), 1)
        self.assertRegex(zips[0].name, r"^map-\d{8}T\d{6}Z\.zip$")
        with zipfile.ZipFile(zips[0]) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["base"], head)
        by_path = {row["path"]: row for row in manifest["files"]}
        self.assertEqual(by_path["README.md"]["status"], "modified")
        self.assertEqual(by_path["data/claims.json"]["status"], "modified")
        self.assertEqual(by_path["data/extra.txt"]["status"], "added")
        self.assertEqual(by_path["assets/flags/spare.png"]["status"], "added")
        self.assertEqual(by_path["NOTES.txt"]["status"], "deleted")
        self.assertNotIn("scratch.txt", by_path)
        self.assertNotIn("NOTES.txt", names)
        self.assertNotIn("scratch.txt", names)
        self.assertEqual(by_path["NOTES.txt"]["sha256"], hashlib.sha256(notes_bytes).hexdigest())
        self.assertEqual(by_path["data/claims.json"]["sha256"], hashlib.sha256(claims_path.read_bytes()).hexdigest())
        self.assertEqual(by_path["README.md"]["sha256"], hashlib.sha256(readme.read_bytes()).hexdigest())
        with zipfile.ZipFile(zips[0]) as archive:
            self.assertEqual(archive.read("data/claims.json"), claims_path.read_bytes())
            self.assertEqual(archive.read("data/extra.txt"), b"hello\n")
        self.assertEqual(git_quiet(self.repo, "rev-parse", "HEAD").strip(), head)
        status = git_quiet(self.repo, "status", "--porcelain")
        self.assertIn("README.md", status)
        self.assertIn("NOTES.txt", status)
        self.assertNotIn("commit", git_quiet(self.repo, "log", "-1", "--format=%s"))

    def test_refuses_when_check_history_fails(self) -> None:
        head = init_git(self.repo)
        (self.repo / "data" / "flags.json").unlink()
        out = self.tmp / "bundle"
        with self.assertRaises(SystemExit) as ctx:
            nb.cmd_export(argparse_namespace(dry_run=False, export_dry_run=False, out=str(out)))
        self.assertIn("export refused: check_history.py failed", str(ctx.exception))
        self.assertFalse(out.exists())
        self.assertEqual(git_quiet(self.repo, "rev-parse", "HEAD").strip(), head)

    def test_refuses_when_limits_check_fails(self) -> None:
        head = init_git(self.repo)
        slots_path = self.repo / "data" / "province_slots.json"
        slots = json.loads(slots_path.read_text(encoding="utf-8"))
        slots["provinces"]["7"]["limits"]["Farm"]["built"] = 99
        slots_path.write_text(json.dumps(slots), encoding="utf-8")
        out = self.tmp / "bundle"
        err = io.StringIO()
        with redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                nb.cmd_export(argparse_namespace(dry_run=False, export_dry_run=False, out=str(out)))
        self.assertIn("export refused: building limits check failed", str(ctx.exception))
        self.assertIn("export refused: building limits check failed", err.getvalue())
        self.assertFalse(out.exists())
        self.assertEqual(git_quiet(self.repo, "rev-parse", "HEAD").strip(), head)


class CsvRewriteTests(unittest.TestCase):
    def test_unchanged_rows_keep_bytes_and_crlf(self) -> None:
        raw = b"province,name\r\n200,Beta\r\n10,Alpha\r\n"
        path = Path(tempfile.mkdtemp()) / "names.csv"
        path.write_bytes(raw)
        header, body = nb.read_csv_rows(path)
        text = nb.write_csv_rows(path, header, body)
        self.assertEqual(text.encode("utf-8"), raw)

    def test_rename_keeps_row_order_and_line_endings(self) -> None:
        raw = b"province,name\r\n200,Beta\r\n10,Alpha\r\n"
        directory = Path(tempfile.mkdtemp())
        path = directory / "names.csv"
        path.write_bytes(raw)
        header, body = nb.read_csv_rows(path)
        for row in body:
            if row[0] == "10":
                row[1] = "Alpine"
        text = nb.write_csv_rows(path, header, body)
        self.assertEqual(
            text.encode("utf-8"),
            b"province,name\r\n200,Beta\r\n10,Alpine\r\n",
        )

    def test_rename_command_noop_does_not_touch_the_file(self) -> None:
        directory = Path(tempfile.mkdtemp())
        data = directory / "data"
        data.mkdir()
        raw = b"province,name\r\n200,Beta\r\n10,Alpha\r\n"
        (data / "province_names.csv").write_bytes(raw)
        world = {
            "nations": [],
            "provinces": {
                "10": {"bbox": [4000, 0, 4200, 100], "xy": [4100, 40]},
                "200": {"bbox": [4300, 0, 4500, 100], "xy": [4400, 40]},
            },
        }
        (data / "world.json").write_text(json.dumps(world), encoding="utf-8")
        saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(directory)
        try:
            nb.cmd_rename_province(argparse_namespace(province="10", name="Alpha", dry_run=False))
            self.assertEqual((data / "province_names.csv").read_bytes(), raw)
            with mock.patch.object(nb, "run_secrecy_check"):
                nb.cmd_rename_province(argparse_namespace(province="200", name="Gamma", dry_run=False))
            updated = (data / "province_names.csv").read_bytes()
            self.assertEqual(updated, b"province,name\r\n200,Gamma\r\n10,Alpha\r\n")
        finally:
            nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = saved
            shutil.rmtree(directory)


class CapitalMoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.world = {
            "nations": [
                {
                    "id": 75,
                    "name": "Holy Order of Eternea (minor)",
                    "short_name": "Holy Order of Eternea",
                    "capital_province": 1315,
                    "capital_xy": [5100.0, 2100.0],
                    "bbox": [4000, 0, 5000, 500],
                    "provinces": [1315, 1333],
                },
                {
                    "id": 8,
                    "name": "Other",
                    "short_name": "Other",
                    "capital_province": 50,
                    "capital_xy": [4100.0, 40.0],
                    "bbox": [4000, 0, 4200, 80],
                    "provinces": [50],
                },
            ],
            "provinces": {
                "1315": {"nation_id": 75, "bbox": [4000, 0, 4300, 200], "xy": [4100, 80]},
                "1333": {"nation_id": 75, "bbox": [4400, 0, 4800, 200], "xy": [4600, 80]},
                "50": {"nation_id": 8, "bbox": [4000, 0, 4200, 80], "xy": [4100, 40]},
            },
        }
        (self.data / "world.json").write_text(json.dumps(self.world, separators=(",", ":")) + "\n", encoding="utf-8")
        settlements = (
            "nation,province,name,type,port,sector_id\r\n"
            "Holy Order of Eternea,1315,Holy Order of Eternea,capital,no,1315-A\r\n"
            "Holy Order of Eternea,1315,Lii,town,no,1315-B\r\n"
            "Holy Order of Eternea,1333,Liiliiliilii,city,no,1333-A\r\n"
            "Holy Order of Eternea,1333,Liiliiliitham,town,no,1333-B\r\n"
        )
        self.settlements = settlements.encode("utf-8")
        (self.data / "settlements.csv").write_bytes(self.settlements)
        infra = {
            "capitals": True,
            "places": [
                {"id": "city-1333", "type": "city", "name": "Liiliiliilii", "province": 1333, "x": 5400.5, "y": 2200.5, "cycle": 0, "nation": 75},
                {"id": "town-1333", "type": "town", "name": "Liiliiliitham", "province": 1333, "x": 5300, "y": 2300, "cycle": 0, "nation": 75},
            ],
            "roads": [],
        }
        (self.data / "infrastructure.json").write_text(json.dumps(infra, separators=(",", ":")) + "\n", encoding="utf-8")
        slots = {
            "provinces": {
                "1315": {
                    "capital": True,
                    "sectors": [{"sector": "1315-A", "name": "Holy Order of Eternea", "map_label": "capital", "level": "town"}],
                },
                "1333": {
                    "capital": False,
                    "sectors": [{"sector": "1333-A", "name": "Liiliiliilii", "map_label": "city", "level": "village"}],
                },
            }
        }
        (self.data / "province_slots.json").write_text(json.dumps(slots, separators=(",", ":")) + "\n", encoding="utf-8")
        (self.data / "settlement_name_overrides.csv").write_bytes(b"sector_id,name\r\n")
        (self.data / "claims.json").write_text('{"75": {"player": "Aiden", "short": "Aiden"}}\n', encoding="utf-8")
        (self.data / "trade_routes.json").write_text('{"routes": []}\n', encoding="utf-8")
        self.saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(self.tmp)

    def tearDown(self) -> None:
        nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = self.saved
        shutil.rmtree(self.tmp)

    def _bytes(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in self.data.iterdir() if path.is_file()}

    def test_refuses_when_the_nation_does_not_own_the_province(self) -> None:
        before = self._bytes()
        with self.assertRaises(ValueError) as caught:
            nb.cmd_capital_move(argparse_namespace(nation="75", province="50", name=None, dry_run=True))
        self.assertIn("does not own province 50", str(caught.exception))
        self.assertEqual(self._bytes(), before)

    def test_dry_run_prints_changes_and_writes_nothing(self) -> None:
        before = self._bytes()
        buf = io.StringIO()
        with redirect_stdout(buf):
            nb.main(["--dry-run", "capital", "move", "75", "1333", "--name", "Lighthollow"])
        text = buf.getvalue()
        self.assertIn("province 1315 -> 1333", text)
        self.assertIn("capital_xy [5100.0, 2100.0] -> [5400.5, 2200.5]", text)
        self.assertIn("1315-A: type capital -> city", text)
        self.assertIn("1333-A:", text)
        self.assertIn("Lighthollow", text)
        self.assertIn("ledger province 1315: capital false", text)
        self.assertIn("ledger province 1333: capital true", text)
        self.assertIn("claims.json does not record a capital", text)
        self.assertIn("(dry-run: not writing)", text)
        self.assertEqual(self._bytes(), before)

    def test_move_updates_settlements_world_ledger_and_overrides(self) -> None:
        with mock.patch.object(nb, "run_secrecy_check"):
            nb.cmd_capital_move(argparse_namespace(nation="75", province="1333", name="Lighthollow", dry_run=False))
        rows = (self.data / "settlements.csv").read_bytes().split(b"\r\n")
        self.assertIn(b"Holy Order of Eternea,1315,Holy Order of Eternea,city,no,1315-A", rows)
        self.assertIn(b"Holy Order of Eternea,1333,Lighthollow,capital,no,1333-A", rows)
        self.assertEqual(rows[1], b"Holy Order of Eternea,1315,Holy Order of Eternea,city,no,1315-A")
        self.assertEqual(rows[3], b"Holy Order of Eternea,1333,Lighthollow,capital,no,1333-A")
        world = json.loads((self.data / "world.json").read_text(encoding="utf-8"))
        nation = next(item for item in world["nations"] if item["id"] == 75)
        self.assertEqual(nation["capital_province"], 1333)
        self.assertEqual(nation["capital_xy"], [5400.5, 2200.5])
        slots = json.loads((self.data / "province_slots.json").read_text(encoding="utf-8"))
        self.assertFalse(slots["provinces"]["1315"]["capital"])
        self.assertEqual(slots["provinces"]["1315"]["sectors"][0]["map_label"], "city")
        self.assertTrue(slots["provinces"]["1333"]["capital"])
        self.assertEqual(slots["provinces"]["1333"]["sectors"][0]["map_label"], "capital")
        self.assertEqual(slots["provinces"]["1333"]["sectors"][0]["name"], "Lighthollow")
        overrides = (self.data / "settlement_name_overrides.csv").read_bytes()
        self.assertEqual(overrides, b"sector_id,name\r\n1333-A,Lighthollow\r\n")
        infra = json.loads((self.data / "infrastructure.json").read_text(encoding="utf-8"))
        city = next(place for place in infra["places"] if place["id"] == "city-1333")
        self.assertEqual(city["name"], "Lighthollow")
        former = next(place for place in infra["places"] if place["id"] == "city-1315")
        self.assertEqual(former["x"], 5100.0)
        self.assertEqual(former["y"], 2100.0)
        self.assertEqual(former["type"], "city")
        claims = (self.data / "claims.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(claims)["75"]["player"], "Aiden")
        self.assertNotIn("capital", json.loads(claims)["75"])

    def test_second_move_to_the_same_capital_is_a_noop(self) -> None:
        with mock.patch.object(nb, "run_secrecy_check"):
            nb.cmd_capital_move(argparse_namespace(nation="75", province="1333", name="Lighthollow", dry_run=False))
            before = self._bytes()
            nb.cmd_capital_move(argparse_namespace(nation="75", province="1333", name="Lighthollow", dry_run=False))
        self.assertEqual(self._bytes(), before)


class ReligionCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.raw = b"target,id,religion,color\r\n"
        (self.data / "religions.csv").write_bytes(self.raw)
        self.world = {
            "nations": [
                {"id": 25, "name": "North", "bbox": [4000, 0, 5000, 200], "capital_xy": [4500, 80]},
            ],
            "provinces": {
                "25": {"bbox": [4000, 0, 4100, 80], "xy": [4050, 40]},
                "878": {"bbox": [5200, 1500, 5600, 1900], "xy": [5400, 1700]},
            },
        }
        self.provinces = {int(key): value for key, value in self.world["provinces"].items()}
        self.nations = {nation["id"]: nation for nation in self.world["nations"]}
        self.saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(self.tmp)

    def tearDown(self) -> None:
        nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = self.saved
        shutil.rmtree(self.tmp)

    def test_set_nation_and_province_override(self) -> None:
        with mock.patch.object(nb, "load_world", return_value=(self.world, self.provinces, self.nations)), mock.patch.object(nb, "run_secrecy_check"):
            nb.cmd_religion_set(argparse_namespace(target="nation:25", name="Old Faith", color=None, dry_run=False))
            nb.cmd_religion_set(argparse_namespace(target="province:878", name="Local Faith", color="#abc", dry_run=False))
        text = (self.data / "religions.csv").read_bytes()
        self.assertEqual(
            text,
            b"target,id,religion,color\r\nnation,25,Old Faith,\r\nprovince,878,Local Faith,#abc\r\n",
        )

    def test_same_name_does_not_rewrite(self) -> None:
        with mock.patch.object(nb, "load_world", return_value=(self.world, self.provinces, self.nations)), mock.patch.object(nb, "run_secrecy_check"):
            nb.cmd_religion_set(argparse_namespace(target="nation:25", name="Old Faith", color=None, dry_run=False))
            before = (self.data / "religions.csv").read_bytes()
            backups = list((self.data / ".nb-backups").glob("religions.csv.*.bak"))
            nb.cmd_religion_set(argparse_namespace(target="nation:25", name="Old Faith", color=None, dry_run=False))
        self.assertEqual((self.data / "religions.csv").read_bytes(), before)
        self.assertEqual(list((self.data / ".nb-backups").glob("religions.csv.*.bak")), backups)

    def test_dry_run_and_ambiguous_id(self) -> None:
        with mock.patch.object(nb, "load_world", return_value=(self.world, self.provinces, self.nations)):
            nb.main(["--dry-run", "religion", "set", "nation:25", "Old Faith"])
            self.assertEqual((self.data / "religions.csv").read_bytes(), self.raw)
            with self.assertRaises(SystemExit):
                nb.main(["religion", "set", "25", "Other"])
        self.assertEqual((self.data / "religions.csv").read_bytes(), self.raw)


class TrailingNewlineTests(unittest.TestCase):
    def test_safe_write_ignores_only_a_trailing_newline(self) -> None:
        directory = Path(tempfile.mkdtemp())
        data = directory / "data"
        data.mkdir()
        path = data / "province_slots.json"
        raw = b'{"provinces":{}}'
        path.write_bytes(raw)
        saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(directory)
        try:
            nb.safe_write(path, raw.decode("utf-8") + "\n", False)
            self.assertEqual(path.read_bytes(), raw)
            self.assertFalse((data / ".nb-backups").exists())
            crlf = data / "notes.csv"
            crlf.write_bytes(b"a\r\nb\r\n")
            nb.safe_write(crlf, "a\r\nb\n", False)
            self.assertEqual(crlf.read_bytes(), b"a\r\nb\r\n")
            with mock.patch.object(nb, "run_secrecy_check"):
                nb.safe_write(path, '{"provinces":{"1":1}}\n', False)
            self.assertEqual(path.read_bytes(), b'{"provinces":{"1":1}}\n')
            with mock.patch.object(nb, "run_secrecy_check"):
                nb.safe_write_many([(crlf, "a\nb\n")], False)
            self.assertEqual(crlf.read_bytes(), b"a\nb\n")
        finally:
            nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = saved
            shutil.rmtree(directory)


class ApplyBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.world = {
            "nations": [
                {
                    "id": 75,
                    "name": "Holy Order of Eternea (minor)",
                    "short_name": "Holy Order of Eternea",
                    "capital_province": 1315,
                    "capital_xy": [5100.0, 2100.0],
                    "bbox": [4000, 0, 5000, 500],
                    "provinces": [1315, 1333],
                },
                {
                    "id": 8,
                    "name": "Other",
                    "short_name": "Other",
                    "capital_province": 50,
                    "capital_xy": [4100.0, 40.0],
                    "bbox": [4000, 0, 4200, 80],
                    "provinces": [50],
                },
            ],
            "provinces": {
                "1315": {"nation_id": 75, "bbox": [4000, 0, 4300, 200], "xy": [4100, 80]},
                "1333": {"nation_id": 75, "bbox": [4400, 0, 4800, 200], "xy": [4600, 80]},
                "50": {"nation_id": 8, "bbox": [4000, 0, 4200, 80], "xy": [4100, 40]},
            },
        }
        (self.data / "world.json").write_text(json.dumps(self.world, separators=(",", ":")) + "\n", encoding="utf-8")
        settlements = (
            "nation,province,name,type,port,sector_id\r\n"
            "Holy Order of Eternea,1315,Holy Order of Eternea,capital,no,1315-A\r\n"
            "Holy Order of Eternea,1333,Liiliiliilii,city,no,1333-A\r\n"
        )
        (self.data / "settlements.csv").write_bytes(settlements.encode("utf-8"))
        infra = {
            "capitals": True,
            "places": [
                {"id": "city-1333", "type": "city", "name": "Liiliiliilii", "province": 1333, "x": 5400.5, "y": 2200.5, "cycle": 0, "nation": 75},
            ],
            "roads": [],
        }
        (self.data / "infrastructure.json").write_text(json.dumps(infra, separators=(",", ":")) + "\n", encoding="utf-8")
        slots = {
            "provinces": {
                "50": {"nation_id": 8, "nation": "Other"},
                "1315": {
                    "capital": True,
                    "sectors": [{"sector": "1315-A", "name": "Holy Order of Eternea", "map_label": "capital"}],
                },
                "1333": {
                    "capital": False,
                    "sectors": [{"sector": "1333-A", "name": "Liiliiliilii", "map_label": "city"}],
                },
            }
        }
        (self.data / "province_slots.json").write_bytes(json.dumps(slots, separators=(",", ":")).encode("utf-8"))
        (self.data / "settlement_name_overrides.csv").write_bytes(b"sector_id,name\r\n")
        (self.data / "claims.json").write_text("{}\n", encoding="utf-8")
        (self.data / "trade_routes.json").write_text('{"routes":[]}\n', encoding="utf-8")
        (self.data / "province_names.csv").write_bytes(b"province,name\r\n1315,Old Hold\r\n")
        (self.data / "religions.csv").write_bytes(b"target,id,religion,color\r\n")
        (self.data / "sector_control.csv").write_bytes(
            b"sector_id,controller,cycle\r\n50-A,8,0\r\n50-B,8,0\r\n50-C,8,0\r\n"
        )
        (self.data / "wars.csv").write_bytes(b"nation_a,nation_b,state\r\n")
        self.saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(self.tmp)

    def tearDown(self) -> None:
        nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = self.saved
        shutil.rmtree(self.tmp)

    def _bytes(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in self.data.iterdir() if path.is_file()}

    def _batch(self, text: str, suffix: str = ".json") -> Path:
        path = self.tmp / f"season{suffix}"
        path.write_text(text, encoding="utf-8")
        return path

    def test_dry_run_json_and_child_flag_write_nothing(self) -> None:
        path = self._batch(
            json.dumps(
                {
                    "changes": [
                        {"op": "transfer", "province": 50, "nation": 75},
                        {"op": "rename", "province": 1333, "name": "Bright Shore"},
                        {"op": "religion", "target": "nation:75", "name": "Dawn", "color": "#abc"},
                        {"op": "capital", "nation": 75, "province": 1333, "name": "Lighthollow"},
                        {"op": "war", "action": "start", "nation_a": 75, "nation_b": 8},
                    ]
                }
            )
        )
        before = self._bytes()
        buf = io.StringIO()
        with redirect_stdout(buf):
            nb.main(["--dry-run", "apply-batch", str(path)])
        summary = json.loads(buf.getvalue())
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["changes"][0]["op"], "transfer")
        self.assertEqual(summary["changes"][0]["from"], 8)
        self.assertEqual(summary["changes"][0]["nation"], 75)
        self.assertEqual(summary["changes"][2]["name"], "Dawn")
        self.assertEqual(summary["changes"][4]["nation_a"], 8)
        self.assertEqual(summary["changes"][4]["nation_b"], 75)
        self.assertIn("data/world.json", summary["files"])
        self.assertIn("data/wars.csv", summary["files"])
        self.assertIn("data/province_names.csv", summary["files"])
        self.assertEqual(self._bytes(), before)

        buf = io.StringIO()
        with redirect_stdout(buf):
            nb.main(["apply-batch", str(path), "--dry-run"])
        again = json.loads(buf.getvalue())
        self.assertEqual(again["count"], 5)
        self.assertEqual(self._bytes(), before)

    def test_apply_writes_every_op_and_keeps_slots_without_only_a_newline(self) -> None:
        slots_before = (self.data / "province_slots.json").read_bytes()
        self.assertFalse(slots_before.endswith(b"\n"))
        path = self._batch(
            "op,province,nation,name,target,color,nation_a,nation_b,action\n"
            "transfer,50,75,,,,,,\n"
            "rename,1333,,Bright Shore,,,,,\n"
            "religion,,,Dawn,nation:75,#abc,,,\n"
            "capital,1333,75,Lighthollow,,,,,\n"
            "war,,,,,,8,75,start\n",
            ".csv",
        )
        with mock.patch.object(nb, "run_secrecy_check"):
            nb.main(["apply-batch", str(path)])
        world = json.loads((self.data / "world.json").read_text(encoding="utf-8"))
        self.assertEqual(world["provinces"]["50"]["nation_id"], 75)
        nation75 = next(item for item in world["nations"] if item["id"] == 75)
        nation8 = next(item for item in world["nations"] if item["id"] == 8)
        self.assertIn(50, nation75["provinces"])
        self.assertNotIn(50, nation8["provinces"])
        self.assertEqual(nation75["capital_province"], 1333)
        names = (self.data / "province_names.csv").read_bytes()
        self.assertIn(b"1333,Bright Shore", names)
        self.assertIn(b"1315,Old Hold", names)
        self.assertTrue(names.startswith(b"province,name\r\n"))
        religions = (self.data / "religions.csv").read_bytes()
        self.assertIn(b"nation,75,Dawn,#abc", religions)
        wars = (self.data / "wars.csv").read_bytes()
        self.assertEqual(wars, b"nation_a,nation_b,state\r\n8,75,active\r\n")
        control = (self.data / "sector_control.csv").read_text(encoding="utf-8")
        self.assertIn("50-A,75,0", control.replace("\r\n", "\n"))
        slots = json.loads((self.data / "province_slots.json").read_text(encoding="utf-8"))
        self.assertEqual(slots["provinces"]["50"]["nation_id"], 75)
        self.assertEqual(slots["provinces"]["50"]["nation"], "Holy Order of Eternea (minor)")
        self.assertTrue(slots["provinces"]["1333"]["capital"])
        self.assertFalse(slots["provinces"]["1315"]["capital"])
        rows = (self.data / "settlements.csv").read_bytes().split(b"\r\n")
        self.assertIn(b"Holy Order of Eternea,1333,Lighthollow,capital,no,1333-A", rows)
        self.assertIn(b"Holy Order of Eternea,1315,Holy Order of Eternea,city,no,1315-A", rows)

    def test_validation_errors_are_complete_and_atomic(self) -> None:
        before = self._bytes()
        path = self._batch(
            json.dumps(
                {
                    "changes": [
                        {"op": "transfer", "province": 50, "nation": 75},
                        {"op": "transfer", "province": 50, "nation": 8},
                        {"op": "transfer", "province": 50, "nation": 75},
                        {"op": "rename", "province": 999999, "name": "Nope"},
                        {"op": "war", "action": "start", "nation_a": 75, "nation_b": 424242},
                        {"op": "war", "action": "end", "nation_a": 8, "nation_b": 75},
                        {"op": "capital", "nation": 8, "province": 1333},
                    ]
                }
            )
        )
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            nb.main(["apply-batch", str(path)])
        self.assertEqual(caught.exception.code, 2)
        text = err.getvalue()
        self.assertIn("conflicts with row 1", text)
        self.assertIn("duplicate of row 1", text)
        self.assertIn("999999", text)
        self.assertIn("424242", text)
        self.assertIn("not at war", text)
        self.assertIn("does not own province 1333", text)
        self.assertEqual(self._bytes(), before)

    def test_examples_parse(self) -> None:
        import batch_ops

        for name in ("apply-batch.json", "apply-batch.csv"):
            ops, errors = batch_ops.parse_batch(ROOT / "examples" / name)
            self.assertEqual(errors, [])
            self.assertEqual(
                [op["op"] for op in ops],
                ["transfer", "rename", "religion", "religion", "capital", "war"],
            )


class OwnerNameTests(unittest.TestCase):
    def test_labels_cover_id_short_and_full_name(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import check_history

        labels = check_history.nation_labels(
            {"id": 276, "name": "Martellun (minor)", "short_name": "Martellun"}
        )
        self.assertEqual(labels, {"276", "Martellun", "Martellun (minor)"})
        eth = check_history.nation_labels(
            {"id": 281, "name": "Ethitham (minor)", "short_name": "Ethitham"}
        )
        self.assertIn("Ethitham", eth)
        self.assertIn("281", eth)
        self.assertNotIn("Verillian Empire", labels)

    def test_transfer_rewrites_a_full_name_settlement_row(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        data = tmp / "data"
        data.mkdir()
        world = {
            "nations": [
                {
                    "id": 276,
                    "name": "Martellun (minor)",
                    "short_name": "Martellun",
                    "capital_province": 2254,
                    "capital_xy": [5100, 2100],
                    "bbox": [4000, 0, 6000, 800],
                    "provinces": [2200, 2254],
                },
                {
                    "id": 21,
                    "name": "Verillian Empire (major)",
                    "short_name": "Verillian Empire",
                    "capital_province": 10,
                    "capital_xy": [7000, 2100],
                    "bbox": [6800, 0, 8000, 800],
                    "provinces": [10],
                },
            ],
            "provinces": {
                "2200": {"nation_id": 276, "bbox": [4000, 0, 4500, 200], "xy": [4200, 80]},
                "2254": {"nation_id": 276, "bbox": [4600, 0, 5200, 200], "xy": [4900, 80]},
                "10": {"nation_id": 21, "bbox": [6800, 0, 7200, 200], "xy": [7000, 80]},
            },
        }
        (data / "world.json").write_text(json.dumps(world), encoding="utf-8")
        (data / "province_slots.json").write_text(
            json.dumps({"provinces": {"2200": {"nation_id": 276, "nation": "Martellun"}, "2254": {"nation_id": 276, "nation": "Martellun"}}}),
            encoding="utf-8",
        )
        (data / "sector_control.csv").write_bytes(
            b"sector_id,controller,cycle\n2200-A,276,0\n2254-A,276,0\n"
        )
        (data / "settlements.csv").write_bytes(
            b"nation,province,name,type,port,sector_id\r\n"
            b"Martellun (minor),2200,Martel,city,no,2200-A\r\n"
            b"276,2254,Martellun,capital,yes,2254-A\r\n"
        )
        saved = (nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY)
        nb.set_root(tmp)
        try:
            with mock.patch.object(nb, "run_secrecy_check"):
                nb.main(["transfer", "--from", "276", "--to", "21", "--cycle", "1", "2200", "2254"])
            text = (data / "settlements.csv").read_text(encoding="utf-8")
            self.assertIn("Verillian Empire,2200,Martel,city,no,2200-A", text)
            self.assertIn("Verillian Empire,2254,Martellun,capital,yes,2254-A", text)
            self.assertNotIn("Martellun (minor)", text)
            world_out = json.loads((data / "world.json").read_text(encoding="utf-8"))
            self.assertEqual(world_out["provinces"]["2200"]["nation_id"], 21)
            self.assertEqual(world_out["provinces"]["2254"]["nation_id"], 21)
        finally:
            nb.ROOT, nb.DATA, nb.BACKUP_DIR, nb.CHECK_HISTORY = saved
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
