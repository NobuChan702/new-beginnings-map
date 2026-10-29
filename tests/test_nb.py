#!/usr/bin/env python3
import hashlib
import io
import json
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


def normalize_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


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
    (repo / "NOTES.txt").write_bytes(b"tracked\n")
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

    def test_json_rewrite_keeps_crlf_and_changes_one_line(self) -> None:
        flags = self.repo / "data" / "flags.json"
        original = flags.read_bytes().replace(b"\n", b"\r\n")
        flags.write_bytes(original)
        src = self.tmp / "zaneth.png"
        src.write_bytes(tiny_png())
        nb.cmd_flag_set(argparse_namespace(nation="8", png=str(src), dry_run=False))
        updated = flags.read_bytes()
        self.assertTrue(updated.endswith(b"\r\n"))
        self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))
        old_lines = original.split(b"\r\n")
        new_lines = updated.split(b"\r\n")
        self.assertEqual(new_lines[:3], old_lines[:3])
        self.assertEqual(new_lines[3], old_lines[3] + b",")
        self.assertEqual(new_lines[4], b'  "8": "assets/flags/zaneth.png"')
        self.assertEqual(new_lines[5:], [b"}", b""])


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
        self.assertEqual(nb.default_export_dir("nt"), Path(nb.WINDOWS_EXPORT_DIR))
        self.assertEqual(nb.default_export_dir("posix"), Path("sync") / "out")

    def test_dry_run_runs_checks_and_does_not_write_the_zip(self) -> None:
        head = init_git(self.repo)
        readme = self.repo / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nnote\n")
        out = self.tmp / "bundle"
        buf = io.StringIO()
        with redirect_stdout(buf):
            nb.main(["--dry-run", "export", "--out", str(out)])
            nb.main(["export", "--dry-run", "--out", str(out)])
        text = buf.getvalue()
        self.assertIn("history secrecy ok", text)
        self.assertIn("No building-limit violations found.", text)
        self.assertIn(str(out), text)
        self.assertIn("modified README.md", text)
        self.assertIn(head, text)
        self.assertIn("(dry-run: not writing)", text)
        self.assertFalse(out.exists())
        self.assertEqual(git_quiet(self.repo, "rev-parse", "HEAD").strip(), head)

    def test_zip_lists_status_and_sha256_without_committing(self) -> None:
        head = init_git(self.repo)
        notes_bytes = (self.repo / "NOTES.txt").read_bytes()
        claims_path = self.repo / "data" / "claims.json"
        claims_path.write_bytes(claims_path.read_bytes().replace(b'"player": "Tom"', b'"player": "Thomas"', 1))
        extra_path = self.repo / "data" / "extra.txt"
        extra_path.write_bytes(b"hello\n")
        (self.repo / "scratch.txt").write_bytes(b"nope\n")
        (self.repo / "assets" / "flags" / "spare.png").write_bytes(b"spare")
        readme = self.repo / "README.md"
        readme.write_bytes(readme.read_bytes() + b"\nExported.\n")
        (self.repo / "NOTES.txt").unlink()
        out = self.tmp / "bundle"
        nb.cmd_export(argparse_namespace(dry_run=False, export_dry_run=False, out=str(out)))
        zips = list(out.glob("map-*.zip"))
        self.assertEqual(len(zips), 1)
        self.assertRegex(zips[0].name, r"^map-\d{8}T\d{6}Z\.zip$")
        with zipfile.ZipFile(zips[0]) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            claims_zip = archive.read("data/claims.json")
            extra_zip = archive.read("data/extra.txt")
            readme_zip = archive.read("README.md")
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
        blob = subprocess.run(
            ["git", "-C", str(self.repo), "show", "HEAD:NOTES.txt"],
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(normalize_newlines(notes_bytes), b"tracked\n")
        self.assertEqual(normalize_newlines(blob), b"tracked\n")
        self.assertEqual(by_path["NOTES.txt"]["sha256"], hashlib.sha256(blob).hexdigest())
        self.assertEqual(by_path["data/claims.json"]["sha256"], hashlib.sha256(claims_path.read_bytes()).hexdigest())
        self.assertEqual(by_path["README.md"]["sha256"], hashlib.sha256(readme.read_bytes()).hexdigest())
        self.assertEqual(by_path["data/extra.txt"]["sha256"], hashlib.sha256(extra_path.read_bytes()).hexdigest())
        self.assertEqual(normalize_newlines(claims_zip), normalize_newlines(claims_path.read_bytes()))
        self.assertEqual(normalize_newlines(extra_zip), b"hello\n")
        self.assertEqual(normalize_newlines(readme_zip), normalize_newlines(readme.read_bytes()))
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


if __name__ == "__main__":
    unittest.main()
