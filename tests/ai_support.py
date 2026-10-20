#!/usr/bin/env python3
"""secret-linter の AI 検出の script（ai_units・ai_add・ai_finish・ai_check）のテストが共有する部品。

``test_ai_*.py`` が importlib で読み込む（ディレクトリ名にハイフンを含むため、通常の import はできない）。
テストではない（``test_*.py`` に一致しない）。

作業領域は OS の一時ディレクトリに作られる。テストごとに TMPDIR を専用ディレクトリへ向け、他のテスト・
他の検査と共有しない。入力は明白なプレースホルダだけを使う。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)

_spec = importlib.util.spec_from_file_location(
    "secret_linter_workspace_for_ai_tests", SCRIPTS_DIR / "workspace.py"
)
workspace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(workspace)

#: 検査単位の本文に置く、明白なプレースホルダの値
VALUE_A = "PLACEHOLDER-PERSON-A"
VALUE_B = "PLACEHOLDER-COMPANY-B"

FILE_TEXT = f"first line\nowner is {VALUE_A} here\nthird line\n"
ADDED_TEXT = f"added one\nadded two {VALUE_B} end\n"
COMMIT_TEXT = f"subject line\n\nbody mentions {VALUE_B}\n"

#: ``ADDED_TEXT`` の各行に対応する、元のファイルの行番号
ADDED_LINE_NUMBERS = [10, 25]


class AiTestCase(unittest.TestCase):
    """テストごとに専用の一時ディレクトリの置き場と、3 種類の検査単位を持つ作業領域を作る。"""

    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        base_path = Path(base.name).resolve()
        self.tmpdir = base_path / "tmp"
        self.tmpdir.mkdir()
        self.bodies = base_path / "bodies"
        self.bodies.mkdir()
        patcher = mock.patch.object(tempfile, "tempdir", str(self.tmpdir))
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.tmpdir)
        env.pop("TEMP", None)
        env.pop("TMP", None)
        return env

    def body(self, name, text):
        path = self.bodies / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def make_workspace(self):
        """U0001（file）・U0002（added_lines）・U0003（commit_message）を持つ作業領域を作り、検査 ID を返す。"""
        inspection_id = workspace.create()
        units = [
            {
                "unit_id": "U0001",
                "kind": "file",
                "label": "a.txt",
                "text_path": self.body("a.txt", FILE_TEXT),
            },
            {
                "unit_id": "U0002",
                "kind": "added_lines",
                "label": "b.txt",
                "commit": "COMMIT-ID-1",
                "text_path": self.body("b.txt", ADDED_TEXT),
                "line_numbers": ADDED_LINE_NUMBERS,
            },
            {
                "unit_id": "U0003",
                "kind": "commit_message",
                "label": "コミットメッセージ",
                "commit": "COMMIT-ID-2",
                "text_path": self.body("c.txt", COMMIT_TEXT),
            },
        ]
        workspace.write(inspection_id, "units.json", {"units": units, "skipped": []})
        return inspection_id

    def place(self, inspection_id):
        return workspace.workspace_path(inspection_id)

    def events(self, inspection_id):
        """``ai_events/`` のイベントを連番順に返す。"""
        directory = self.place(inspection_id) / "ai_events"
        if not directory.is_dir():
            return []
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(directory.glob("*.json"))
        ]

    def run_script(self, script, *args, stdin=""):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), *args],
            input=stdin.encode("utf-8"),
            capture_output=True,
            env=self.env(),
        )
        stdout = completed.stdout.decode("utf-8")
        payload = json.loads(stdout) if stdout.strip() else None
        return completed.returncode, payload, stdout, completed.stderr.decode("utf-8")

    def add(self, inspection_id, value, unit_id="U0001", kind="person_name", line=None):
        args = [inspection_id, "--unit-id", unit_id, "--kind", kind]
        if line is not None:
            args += ["--line", str(line)]
        return self.run_script("ai_add.py", *args, stdin=value + "\n")

    def reject_once(self, inspection_id):
        """本文に含まれない値を 1 回報告し、拒否させる。"""
        code, _, _, _ = self.add(inspection_id, "NOT-IN-THE-TEXT-PLACEHOLDER")
        assert code == 1
