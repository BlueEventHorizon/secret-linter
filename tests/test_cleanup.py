#!/usr/bin/env python3
"""secret-linter の cleanup.py の契約テスト。

DES-087 のモジュール表 W-5（消す）と、テスト表 SC-14・SC-16 の作業領域に関する検証に対応する。

- 置き場を中身ごと削除し、置き場がファイルとして残らない（SC-14）
- 置き場が無くても終了コード 0（標準出力は ``{}``）
- 不正な検査 ID・シンボリックリンクの置き場は、何も削除せず終了コード 1（標準出力の ``errors``）
- 引数が無いときは終了コード 2（argparse）
- エラーの理由に、作業領域の中身（値）を含めない（SC-16）

作業領域は OS の一時ディレクトリに作られるため、テストが作った置き場は tearDown で必ず削除する。
テストの入力値は、明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.test_cleanup -v
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)
SCRIPT = _SCRIPTS_DIR / "cleanup.py"

_spec = importlib.util.spec_from_file_location(
    "secret_linter_workspace", _SCRIPTS_DIR / "workspace.py"
)
workspace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(workspace)

PLACEHOLDER = {"value": "EXAMPLE_PLACEHOLDER_VALUE"}


def run(*args):
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True
    )
    payload = json.loads(completed.stdout) if completed.stdout.strip() else None
    return completed.returncode, payload, completed


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        for inspection_id in self._created:
            path = workspace.workspace_path(inspection_id)
            if path.is_symlink():
                path.unlink()
            else:
                shutil.rmtree(path, ignore_errors=True)

    def create(self):
        inspection_id = workspace.create()
        self._created.append(inspection_id)
        return inspection_id

    def test_deletes_place_with_contents(self):
        inspection_id = self.create()
        workspace.write(inspection_id, "units.json", PLACEHOLDER)
        workspace.write_next(inspection_id, "judgment", PLACEHOLDER)
        code, payload, _ = run(inspection_id)
        self.assertEqual((code, payload), (0, {}))

    def test_place_does_not_remain_as_file_or_directory(self):
        # SC-14: 結果（値）がファイルとして残らない
        inspection_id = self.create()
        place = workspace.workspace_path(inspection_id)
        workspace.write(inspection_id, "pattern.json", PLACEHOLDER)
        run(inspection_id)
        self.assertFalse(os.path.lexists(place))
        leftovers = list(
            Path(tempfile.gettempdir()).glob(
                f"{workspace.PLACE_PREFIX}{inspection_id}*"
            )
        )
        self.assertEqual(leftovers, [])

    def test_succeeds_when_place_does_not_exist(self):
        code, payload, _ = run("a" * 32)
        self.assertEqual((code, payload), (0, {}))

    def test_succeeds_when_run_twice(self):
        inspection_id = self.create()
        run(inspection_id)
        code, payload, _ = run(inspection_id)
        self.assertEqual((code, payload), (0, {}))

    def test_keeps_other_inspections(self):
        mine = self.create()
        other = self.create()
        workspace.write(other, "units.json", PLACEHOLDER)
        run(mine)
        self.assertTrue((workspace.workspace_path(other) / "units.json").is_file())

    def test_rejects_invalid_inspection_id_with_exit_1(self):
        victim = Path(tempfile.mkdtemp(prefix="secret-linter-test-victim-"))
        try:
            for bad in ("../" + victim.name, "not-an-id", "A" * 32, "a" * 31):
                with self.subTest(inspection_id=bad):
                    code, payload, _ = run(bad)
                    self.assertEqual(code, 1)
                    self.assertIsInstance(payload["errors"], list)
                    self.assertTrue(payload["errors"])
                    self.assertTrue(victim.is_dir())
        finally:
            shutil.rmtree(victim, ignore_errors=True)

    def test_error_message_does_not_echo_the_invalid_id(self):
        bad = "SECRET-LIKE-ID-PLACEHOLDER"
        code, payload, completed = run(bad)
        self.assertEqual(code, 1)
        self.assertNotIn(bad, completed.stdout)

    def test_refuses_symlink_place_without_deleting_target(self):
        inspection_id = "b" * 32
        link = workspace.workspace_path(inspection_id)
        self._created.append(inspection_id)
        with tempfile.TemporaryDirectory() as outside:
            victim = Path(outside) / "victim.txt"
            victim.write_text(PLACEHOLDER["value"], encoding="utf-8")
            link.symlink_to(outside, target_is_directory=True)
            code, payload, completed = run(inspection_id)
            self.assertEqual(code, 1)
            self.assertTrue(payload["errors"])
            self.assertNotIn(PLACEHOLDER["value"], completed.stdout)
            self.assertTrue(victim.is_file())
            self.assertTrue(link.is_symlink())

    def test_missing_argument_exits_2_without_stdout(self):
        code, payload, completed = run()
        self.assertEqual(code, 2)
        self.assertEqual(completed.stdout.strip(), "")

    def test_extra_argument_exits_2(self):
        inspection_id = self.create()
        code, _, _ = run(inspection_id, "extra")
        self.assertEqual(code, 2)
        self.assertTrue(workspace.workspace_path(inspection_id).is_dir())

    def test_stdout_is_single_json_object(self):
        inspection_id = self.create()
        _, _, completed = run(inspection_id)
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertIsInstance(json.loads(completed.stdout), dict)


if __name__ == "__main__":
    unittest.main()
