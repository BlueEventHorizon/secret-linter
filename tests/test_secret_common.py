#!/usr/bin/env python3
"""secret-linter の共通部品（secret_common.py）の契約テスト。

- ``absolute_path``: 絶対パスだけを受け付ける引数の型（``--project-root`` が使う）
- 判定・回答の script（``judge.py`` / ``answer.py``）は、入口の ``scan.py`` を import しない
  （対象の解決とパターン検出の import の連鎖を負わないため）

実行:
  python3 -m unittest tests.forge.secret-linter.test_secret_common -v
"""

import argparse
import ast
import subprocess
import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

import secret_common  # noqa: E402


class AbsolutePathTest(unittest.TestCase):
    def test_absolute_path_is_accepted_as_a_path(self):
        self.assertEqual(secret_common.absolute_path("/a/b"), Path("/a/b"))

    def test_relative_path_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            secret_common.absolute_path("a/b")

    def test_empty_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            secret_common.absolute_path("")


class ImportIndependenceTest(unittest.TestCase):
    def modules_loaded_by(self, module):
        code = (
            f"import sys; sys.path.insert(0, {str(_SCRIPTS_DIR)!r}); import {module}; "
            "print(' '.join(sorted(sys.modules)))"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True
        )
        return set(result.stdout.split())

    def test_judge_does_not_import_scan(self):
        self.assertNotIn("scan", self.modules_loaded_by("judge"))

    def test_answer_does_not_import_scan(self):
        self.assertNotIn("scan", self.modules_loaded_by("answer"))


class ScriptIndependenceTest(unittest.TestCase):
    """secret-linter の script は、標準ライブラリと同じディレクトリのモジュールだけに依存する。

    forge の共有部品（``plugins/forge/scripts/`` 等）への依存は、別のプラグインへ移すときの障害になる。
    """

    @classmethod
    def setUpClass(cls):
        cls.files = sorted(_SCRIPTS_DIR.glob("*.py"))
        cls.siblings = {path.stem for path in cls.files}

    def imported_modules(self, path):
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0, f"{path.name}: 相対 import は使わない")
                names.add(node.module.split(".")[0])
        return names

    def test_scripts_exist(self):
        self.assertTrue(self.files)

    def test_imports_are_stdlib_or_sibling_modules_only(self):
        for path in self.files:
            for name in sorted(self.imported_modules(path)):
                with self.subTest(script=path.name, module=name):
                    self.assertTrue(
                        name in sys.stdlib_module_names or name in self.siblings,
                        f"{path.name} が、標準ライブラリでも同じディレクトリのモジュールでもない {name} を import している",
                    )

    def test_sys_path_is_only_extended_with_the_own_directory(self):
        """``sys.path`` へ足してよいのは、自分のディレクトリ（``Path(__file__).resolve().parent``）だけ。"""
        allowed = "sys.path.insert(0, str(Path(__file__).resolve().parent))"
        for path in self.files:
            for line in path.read_text(encoding="utf-8").splitlines():
                code = line.split("#", 1)[0].strip()
                if "sys.path" in code:
                    with self.subTest(script=path.name, line=code):
                        self.assertEqual(code, allowed)

    def test_no_path_into_other_forge_directories(self):
        for path in self.files:
            code_lines = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("#")
            ]
            with self.subTest(script=path.name):
                self.assertFalse(
                    any("plugins/forge" in line or "plugins\" / \"forge" in line for line in code_lines)
                )


if __name__ == "__main__":
    unittest.main()
