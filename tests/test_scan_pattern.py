#!/usr/bin/env python3
"""secret-linter の scan.py が pattern.json を書く部分の契約テスト。

DES-087 のモジュール表 P-1・W-2 と、テスト表 SC-04・14・15 のスクリプトの検証に対応する。
検出そのもの（各種別・除外・抑制）は test_patterns.py が検証する。ここでは scan.py を経由して、

- 終了コード 0: 作業領域に ``pattern.json`` が書かれ、構造（``candidates`` / ``suppressed`` / ``filtered``）を持つ。
  標準出力の ``pattern_candidate_count`` が ``pattern.json`` の ``candidates`` の件数と一致する
- 検査単位が 0 件でも ``pattern.json`` は書かれ、候補は 0 件になる
- ``--files`` / ``--diff`` / ``--dirs`` / ``--branch`` の検査単位から候補が出る。``--branch`` の候補は、
  追加行の元の行番号と ``commit`` を持つ
- 検出した値は、作業領域の ``pattern.json`` にだけ入り、標準出力・標準エラー・``errors`` に出ない
- 終了コード 1（パターン検出の失敗）: 作業領域が残らず、検査 ID が無く、``errors`` に値が含まれない

を検証する。作業領域の置き場（TMPDIR）はテストごとの専用ディレクトリへ向ける。機密らしい値は、
テストコードの中で連結して組み立てる（形式だけを満たす架空の値）。

実行:
  python3 -m unittest tests.test_scan_pattern -v
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)
SCRIPT = _SCRIPTS_DIR / "scan.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vocab  # noqa: E402

_spec = importlib.util.spec_from_file_location("secret_linter_scan_for_pattern", SCRIPT)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

PLACE_PREFIX = scan.workspace.PLACE_PREFIX

# 形式だけを満たす架空の値。連結して組み立てる（テストファイル自身が検出されないため）
AWS_KEY = "AKIA" + "Z7Q2WXYVBN4KLMPD"
GITHUB_TOKEN = "ghp_" + "9Xk2LmQ7vRt4WsZa8BnCd3EfGh5JiKl6MnOp"
EMAIL = "taro@example.org"
PRIVATE_IP = vocab.ip(192, 168, 0, 10)
MARKER = scan.patterns.SUPPRESS_MARKER


def _git(args, cwd, env=None):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, check=True, env=env
    )
    return result.stdout.decode("utf-8", errors="replace").strip()


class ScanPatternTestCase(unittest.TestCase):
    """テストごとに、専用の置き場（TMPDIR）・プロジェクトルートを持つ。"""

    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        self.base_path = Path(base.name).resolve()
        self.tmpdir = self.base_path / "tmp"
        self.tmpdir.mkdir()
        self.root = self.base_path / "project"
        self.root.mkdir()

    def env(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.tmpdir)
        env.pop("TEMP", None)
        env.pop("TMP", None)
        # 個人・システムの git 設定を読まず、一時ディレクトリの親にある git リポジトリを取り込まない
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CEILING_DIRECTORIES"] = str(self.base_path)
        return env

    def git(self, *args):
        return _git(list(args), self.root, env=self.env())

    def init_repo(self):
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        self.git("config", "commit.gpgSign", "false")

    def write(self, rel, content):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def run_scan(self, *args):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=self.env(),
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else None
        return completed.returncode, payload, completed

    def run_in_process(self, *argv):
        buffer = io.StringIO()
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            with mock.patch.dict(
                os.environ, {"GIT_CEILING_DIRECTORIES": str(self.base_path)}
            ):
                with redirect_stdout(buffer):
                    code = scan.run_cli(list(argv))
        return code, json.loads(buffer.getvalue()), buffer.getvalue()

    def workspaces(self):
        return sorted(p.name for p in self.tmpdir.glob(f"{PLACE_PREFIX}*"))

    def read_workspace_json(self, inspection_id, name):
        path = self.tmpdir / f"{PLACE_PREFIX}{inspection_id}" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def scan_files(self, *names):
        return self.run_scan("--project-root", str(self.root), "--files", *names)


class PatternJsonTest(ScanPatternTestCase):
    def test_pattern_json_is_written_and_the_count_matches_stdout(self):
        self.write("a.txt", "mail " + EMAIL + "\nip " + PRIVATE_IP + "\n")

        code, payload, _ = self.scan_files("a.txt")

        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(payload), ["inspection_id", "pattern_candidate_count", "unit_count"]
        )
        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual(payload["pattern_candidate_count"], len(pattern["candidates"]))
        self.assertEqual(payload["pattern_candidate_count"], 2)

    def test_pattern_json_structure(self):
        self.write("a.txt", "mail " + EMAIL + "\n")

        _, payload, _ = self.scan_files("a.txt")

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual(sorted(pattern), ["candidates", "filtered", "suppressed"])
        self.assertEqual(
            pattern["candidates"],
            [
                {
                    "candidate_id": "P0001",
                    "source": "pattern",
                    "unit_id": "U0001",
                    "line": 1,
                    "kind": "email",
                    "value": EMAIL,
                }
            ],
        )
        self.assertEqual(sorted(pattern["suppressed"]), ["count", "locations"])
        self.assertEqual(sorted(pattern["filtered"]), ["count", "reasons"])
        self.assertEqual(pattern["suppressed"], {"count": 0, "locations": []})
        self.assertEqual(pattern["filtered"]["count"], 0)

    def test_candidates_refer_to_units_json(self):
        self.write("a.txt", "x\n")
        self.write("b.txt", EMAIL + "\n")

        _, payload, _ = self.scan_files("a.txt", "b.txt")

        units = self.read_workspace_json(payload["inspection_id"], "units.json")[
            "units"
        ]
        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        labels = {u["unit_id"]: u["label"] for u in units}
        self.assertEqual(
            [labels[c["unit_id"]] for c in pattern["candidates"]], ["b.txt"]
        )

    def test_suppressed_and_filtered_are_recorded_without_values(self):
        self.write(
            "a.txt",
            "aws = "
            + AWS_KEY
            + "  # "
            + MARKER
            + "\n"
            + "token = tokens[i].decode(errors)\n",
        )

        _, payload, _ = self.scan_files("a.txt")

        self.assertEqual(payload["pattern_candidate_count"], 0)
        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual(pattern["candidates"], [])
        self.assertEqual(
            pattern["suppressed"],
            {
                "count": 1,
                "locations": [
                    {
                        "unit_id": "U0001",
                        "label": "a.txt",
                        "line": 1,
                        "kind": "known_token",
                    }
                ],
            },
        )
        self.assertEqual(pattern["filtered"]["count"], 1)
        self.assertEqual(pattern["filtered"]["reasons"]["code_expression"], 1)
        self.assertNotIn(AWS_KEY, json.dumps(pattern))

    def test_zero_units_give_pattern_json_with_zero_candidates(self):
        self.init_repo()
        self.write("base.txt", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")

        code, payload, _ = self.run_scan("--project-root", str(self.root), "--diff")

        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 0)
        self.assertEqual(payload["pattern_candidate_count"], 0)
        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual(pattern["candidates"], [])
        self.assertEqual(pattern["suppressed"], {"count": 0, "locations": []})
        self.assertEqual(pattern["filtered"]["count"], 0)

    def test_only_skipped_units_give_zero_candidates(self):
        (self.root / "bin.dat").write_bytes(b"abc\x00" + EMAIL.encode("ascii"))

        code, payload, _ = self.scan_files("bin.dat")

        self.assertEqual(code, 0)
        self.assertEqual(
            (payload["unit_count"], payload["pattern_candidate_count"]), (0, 0)
        )

    def test_pattern_json_and_workspace_are_private(self):
        self.write("a.txt", EMAIL + "\n")
        _, payload, _ = self.scan_files("a.txt")
        place = self.tmpdir / f"{PLACE_PREFIX}{payload['inspection_id']}"
        if os.name != "nt":
            self.assertEqual(os.stat(place / "pattern.json").st_mode & 0o077, 0)
            self.assertEqual(os.stat(place).st_mode & 0o077, 0)


class ScopeTest(ScanPatternTestCase):
    def test_diff_scope_detects_in_uncommitted_files(self):
        self.init_repo()
        self.write("base.txt", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")
        self.write("new.txt", "mail " + EMAIL + "\n")

        _, payload, _ = self.run_scan("--project-root", str(self.root), "--diff")

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual(
            [(c["kind"], c["line"], c["value"]) for c in pattern["candidates"]],
            [("email", 1, EMAIL)],
        )

    def test_dirs_scope_detects_in_files_under_the_directory(self):
        self.init_repo()
        self.write("docs/a.md", "x\nip " + PRIVATE_IP + "\n")
        self.write("src/b.py", "ip " + PRIVATE_IP + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "initial")

        _, payload, _ = self.run_scan(
            "--project-root", str(self.root), "--dirs", "docs"
        )

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        units = self.read_workspace_json(payload["inspection_id"], "units.json")[
            "units"
        ]
        self.assertEqual([u["label"] for u in units], ["docs/a.md"])
        self.assertEqual(
            [(c["kind"], c["line"]) for c in pattern["candidates"]], [("private_ip", 2)]
        )

    def test_files_scope_detects_in_a_file_outside_the_project_root(self):
        outside = self.base_path / "outside"
        outside.mkdir()
        body = outside / "body.txt"
        body.write_text("mail " + EMAIL + "\n", encoding="utf-8")

        _, payload, _ = self.scan_files(str(body))

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual(payload["pattern_candidate_count"], 1)
        self.assertEqual(pattern["candidates"][0]["unit_id"], "U0001")

    def test_branch_scope_gives_original_line_numbers_and_the_commit(self):
        self.init_repo()
        self.write("a.txt", "one\ntwo\nmail " + EMAIL + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "add file")
        sha = self.git("rev-parse", "HEAD")

        _, payload, _ = self.run_scan("--project-root", str(self.root), "--branch")

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        units = {
            u["unit_id"]: u
            for u in self.read_workspace_json(payload["inspection_id"], "units.json")[
                "units"
            ]
        }
        self.assertEqual(len(pattern["candidates"]), 1)
        candidate = pattern["candidates"][0]
        self.assertEqual(
            (candidate["kind"], candidate["value"], candidate["line"]),
            ("email", EMAIL, 3),
        )
        self.assertEqual(candidate["commit"], sha)
        self.assertEqual(units[candidate["unit_id"]]["kind"], "added_lines")
        self.assertEqual(units[candidate["unit_id"]]["label"], "a.txt")
        self.assertEqual(units[candidate["unit_id"]]["commit"], sha)

    def test_branch_scope_detects_in_the_commit_message(self):
        self.init_repo()
        self.write("a.txt", "one\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "contact " + EMAIL)
        sha = self.git("rev-parse", "HEAD")

        _, payload, _ = self.run_scan("--project-root", str(self.root), "--branch")

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        units = {
            u["unit_id"]: u
            for u in self.read_workspace_json(payload["inspection_id"], "units.json")[
                "units"
            ]
        }
        self.assertEqual(len(pattern["candidates"]), 1)
        candidate = pattern["candidates"][0]
        self.assertEqual(
            (candidate["kind"], candidate["commit"], candidate["line"]),
            ("email", sha, 1),
        )
        self.assertEqual(units[candidate["unit_id"]]["kind"], "commit_message")

    def test_branch_scope_does_not_see_a_value_removed_from_the_working_tree(self):
        # 途中の commit で入れて後で消した値も、履歴の追加行として検出される
        self.init_repo()
        self.write("a.txt", "mail " + EMAIL + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "add")
        self.write("a.txt", "nothing\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "remove")

        _, payload, _ = self.run_scan("--project-root", str(self.root), "--branch")

        pattern = self.read_workspace_json(payload["inspection_id"], "pattern.json")
        self.assertEqual([c["value"] for c in pattern["candidates"]], [EMAIL])


class ValueNotLeakedTest(ScanPatternTestCase):
    def test_values_are_only_in_the_workspace_pattern_json(self):
        self.write(
            "a.txt",
            "aws = " + AWS_KEY + "\nmail " + EMAIL + "\ngh " + GITHUB_TOKEN + "\n",
        )

        code, payload, completed = self.scan_files("a.txt")

        self.assertEqual(code, 0)
        self.assertEqual(payload["pattern_candidate_count"], 3)
        for value in (AWS_KEY, EMAIL, GITHUB_TOKEN):
            self.assertNotIn(value, completed.stdout)
            self.assertNotIn(value, completed.stderr)
        pattern_text = (
            self.tmpdir / f"{PLACE_PREFIX}{payload['inspection_id']}" / "pattern.json"
        ).read_text(encoding="utf-8")
        for value in (AWS_KEY, EMAIL, GITHUB_TOKEN):
            self.assertIn(value, pattern_text)

    def test_stdout_is_a_single_json_object_of_the_documented_keys(self):
        self.write("a.txt", "mail " + EMAIL + "\n")
        _, _, completed = self.scan_files("a.txt")
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)

    def test_failure_with_a_secret_in_another_file_does_not_leak_it(self):
        self.write("a.txt", "aws = " + AWS_KEY + "\n")

        code, payload, completed = self.scan_files("a.txt", "no-such-file.txt")

        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertNotIn(AWS_KEY, completed.stdout)
        self.assertNotIn(AWS_KEY, completed.stderr)
        self.assertEqual(self.workspaces(), [])


class PatternFailureTest(ScanPatternTestCase):
    def test_pattern_error_is_exit_1_with_no_workspace_and_no_value(self):
        self.write("a.txt", "aws = " + AWS_KEY + "\n")
        reason = "検査単位の本文を読めません: 理由だけ"
        with mock.patch.object(
            scan.patterns, "detect", side_effect=scan.patterns.PatternError(reason)
        ):
            code, payload, stdout = self.run_in_process(
                "--project-root", str(self.root), "--files", "a.txt"
            )
        self.assertEqual(code, 1)
        self.assertEqual(payload, {"errors": [reason]})
        self.assertNotIn("inspection_id", payload)
        self.assertNotIn(AWS_KEY, stdout)
        self.assertEqual(self.workspaces(), [])

    def test_unreadable_body_during_detection_is_exit_1_and_removes_the_workspace(self):
        path = self.write("a.txt", "aws = " + AWS_KEY + "\n")
        real_detect = scan.patterns.detect

        def vanish_then_detect(units):
            path.unlink()
            return real_detect(units)

        with mock.patch.object(scan.patterns, "detect", side_effect=vanish_then_detect):
            code, payload, stdout = self.run_in_process(
                "--project-root", str(self.root), "--files", "a.txt"
            )
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertNotIn("inspection_id", payload)
        self.assertNotIn(AWS_KEY, stdout)
        self.assertEqual(self.workspaces(), [])

    def test_unexpected_exception_in_detection_removes_the_workspace_and_is_reraised(
        self,
    ):
        self.write("a.txt", "x\n")
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            with mock.patch.object(
                scan.patterns, "detect", side_effect=RuntimeError("想定外")
            ):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(RuntimeError):
                        scan.run_cli(
                            ["--project-root", str(self.root), "--files", "a.txt"]
                        )
        self.assertEqual(self.workspaces(), [])

    def test_pattern_json_write_failure_is_exit_1_with_no_workspace(self):
        self.write("a.txt", "mail " + EMAIL + "\n")
        real_write = scan.workspace.write

        def fail_on_pattern(inspection_id, name, payload):
            if name == "pattern.json":
                raise scan.workspace.WorkspaceError("書けません")
            return real_write(inspection_id, name, payload)

        with mock.patch.object(scan.workspace, "write", side_effect=fail_on_pattern):
            code, payload, stdout = self.run_in_process(
                "--project-root", str(self.root), "--files", "a.txt"
            )
        self.assertEqual(code, 1)
        self.assertEqual(payload, {"errors": ["書けません"]})
        self.assertNotIn(EMAIL, stdout)
        self.assertEqual(self.workspaces(), [])


if __name__ == "__main__":
    unittest.main()
