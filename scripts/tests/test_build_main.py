"""main() must not delete a name it never binds."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "scripts" / "build_map.py"


class MainDelTest(unittest.TestCase):
    def test_main_deletes_only_names_it_assigns(self):
        tree = ast.parse(BUILD.read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        assigned = set()
        deleted = []
        for stmt in main.body:
            if isinstance(stmt, ast.Delete):
                for target in stmt.targets:
                    self.assertIsInstance(target, ast.Name)
                    deleted.append(target.id)
                    self.assertIn(target.id, assigned, f"main() deletes unbound name {target.id}")
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    assigned.add(node.id)
        self.assertNotIn("count", deleted)
        self.assertIn("water", deleted)


if __name__ == "__main__":
    unittest.main()
