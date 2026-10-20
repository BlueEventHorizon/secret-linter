#!/usr/bin/env python3
"""secret-linter の ai_units.py の契約テスト。

DES-087 のモジュール表 W-6（検査単位のパスを返す）の検証に対応する。

- 検査単位の一覧ファイル（units.json）の絶対パスを標準出力の units_path で返す。返したパスのファイルを、
  そのまま読める。本文は標準出力に出ない
- 作業領域が無い・検査 ID が不正・units.json が無いときは 1（標準出力の errors に理由）
- 引数が無いときは 2（argparse）

実行:
  python3 -m unittest tests.forge.secret-linter.test_ai_units -v
"""

import importlib.util
import json
import unittest
from pathlib import Path

_support_spec = importlib.util.spec_from_file_location(
    "secret_linter_ai_support", Path(__file__).resolve().parent / "ai_support.py"
)
support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(support)


class UnitsTest(support.AiTestCase):
    def test_returns_absolute_path_of_units_json(self):
        iid = self.make_workspace()
        code, payload, _, _ = self.run_script("ai_units.py", iid)
        self.assertEqual(code, 0)
        self.assertEqual(list(payload), ["units_path"])
        path = Path(payload["units_path"])
        self.assertTrue(path.is_absolute())
        self.assertEqual(path, (self.place(iid) / "units.json").resolve())

    def test_returned_path_can_be_read_as_is(self):
        iid = self.make_workspace()
        _, payload, _, _ = self.run_script("ai_units.py", iid)
        data = json.loads(Path(payload["units_path"]).read_text(encoding="utf-8"))
        self.assertEqual([u["unit_id"] for u in data["units"]], ["U0001", "U0002", "U0003"])

    def test_does_not_print_body_text(self):
        iid = self.make_workspace()
        _, _, stdout, _ = self.run_script("ai_units.py", iid)
        self.assertNotIn(support.VALUE_A, stdout)
        self.assertNotIn(support.VALUE_B, stdout)

    def test_stdout_is_single_json_object(self):
        iid = self.make_workspace()
        _, _, stdout, _ = self.run_script("ai_units.py", iid)
        self.assertEqual(len(stdout.strip().splitlines()), 1)

    def test_exits_1_when_workspace_is_missing(self):
        code, payload, _, _ = self.run_script("ai_units.py", "a" * 32)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertNotIn("units_path", payload)

    def test_exits_1_for_invalid_inspection_id(self):
        for bad in ("not-an-id", "../" + "a" * 32, "A" * 32):
            with self.subTest(inspection_id=bad):
                code, payload, _, _ = self.run_script("ai_units.py", bad)
                self.assertEqual(code, 1)
                self.assertTrue(payload["errors"])

    def test_exits_1_when_units_json_is_missing(self):
        iid = support.workspace.create()
        code, payload, _, _ = self.run_script("ai_units.py", iid)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_exits_1_when_units_json_is_broken(self):
        iid = support.workspace.create()
        (self.place(iid) / "units.json").write_text("{not json", encoding="utf-8")
        code, _, _, _ = self.run_script("ai_units.py", iid)
        self.assertEqual(code, 1)

    def test_missing_argument_exits_2(self):
        code, _, stdout, _ = self.run_script("ai_units.py")
        self.assertEqual(code, 2)
        self.assertEqual(stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
