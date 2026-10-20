#!/usr/bin/env python3
"""secret-linter の scan.py の ``--branch`` の契約テスト。

DES-087 のモジュール表 T-1（``--branch``）・W-2 と、テスト表 SC-01・03・16 のスクリプトの検証に対応する。
履歴の解析そのもの（hunk・マージ・パス等）は test_history.py が検証する。ここでは scan.py を経由して、

- 終了コード 0: 履歴の検査単位が units.json に書かれる。``unit_id`` は ``U0001`` からの連番で、スキップが記録され、
  標準出力は検査 ID と件数だけである。リモートを持たない・リモートへ push 済み・空のリポジトリでもエラーにならない
- 終了コード 1: git のリポジトリでない・UTF-8 として解釈できない履歴（判断待ち）。作業領域が残らず、
  ``errors`` に本文が含まれない
- 終了コード 2: ``--branch`` と他の範囲の指定の併用
- 検査単位の本文（``text_path``）が作業領域の中に在る

を検証する。作業領域の置き場（TMPDIR）はテストごとの専用ディレクトリへ向ける。入力は明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.forge.secret-linter.test_scan_branch -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"
)
SCRIPT = _SCRIPTS_DIR / "scan.py"

PLACE_PREFIX = "forge-secret-linter-"

#: 本文の代わりに置く、明白なプレースホルダ
BODY_MARKER = "PLACEHOLDER-BODY-MARKER"

#: ``commit_message`` の検査単位の label（固定の語）
COMMIT_MESSAGE_LABEL = "コミットメッセージ"


class ScanBranchTestCase(unittest.TestCase):
    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        base_path = Path(base.name).resolve()
        self.base_path = base_path
        self.tmpdir = base_path / "tmp"
        self.tmpdir.mkdir()
        self.root = base_path / "project"
        self.root.mkdir()
        self.remote = base_path / "remote.git"

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

    def git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True, check=True, env=self.env()
        )
        return result.stdout.decode("utf-8", errors="replace").strip()

    def init_repo(self, object_format=None):
        init = ["init", "-q", "-b", "main"]
        if object_format:
            init.append(f"--object-format={object_format}")
        self.git(*init)
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        self.git("config", "commit.gpgSign", "false")

    def commit(self, message, files):
        for rel, content in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_scan(self, *args):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, env=self.env()
        )
        stdout = completed.stdout.decode("utf-8")
        payload = json.loads(stdout) if stdout.strip() else None
        return completed.returncode, payload, stdout, completed.stderr.decode("utf-8")

    def scan_branch(self):
        return self.run_scan("--project-root", str(self.root), "--branch")

    def workspaces(self):
        return sorted(p.name for p in self.tmpdir.glob(f"{PLACE_PREFIX}*"))

    def read_units(self, inspection_id):
        path = self.tmpdir / f"{PLACE_PREFIX}{inspection_id}" / "units.json"
        return json.loads(path.read_text(encoding="utf-8"))


class SuccessTest(ScanBranchTestCase):
    def test_units_json_holds_the_history_units_and_stdout_only_the_id_and_count(self):
        self.init_repo()
        sha = self.commit("first message", {"a.txt": "one\ntwo\n"})

        code, payload, stdout, _ = self.scan_branch()

        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload), ["inspection_id", "pattern_candidate_count", "unit_count"])
        self.assertEqual(payload["unit_count"], 2)
        self.assertRegex(payload["inspection_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        data = self.read_units(payload["inspection_id"])
        self.assertEqual(data["skipped"], [])
        message_unit, lines_unit = data["units"]
        self.assertEqual(message_unit["unit_id"], "U0001")
        self.assertEqual(message_unit["kind"], "commit_message")
        self.assertEqual(message_unit["label"], COMMIT_MESSAGE_LABEL)
        self.assertEqual(message_unit["commit"], sha)
        self.assertNotIn("line_numbers", message_unit)
        self.assertEqual(lines_unit["unit_id"], "U0002")
        self.assertEqual(lines_unit["kind"], "added_lines")
        self.assertEqual(lines_unit["label"], "a.txt")
        self.assertEqual(lines_unit["commit"], sha)
        self.assertEqual(lines_unit["line_numbers"], [1, 2])
        self.assertEqual(Path(message_unit["text_path"]).read_text(encoding="utf-8"), "first message\n")
        self.assertEqual(Path(lines_unit["text_path"]).read_text(encoding="utf-8"), "one\ntwo\n")

    def test_label_and_commit_are_separate_fields(self):
        self.init_repo()
        sha = self.commit("m", {"dir/b.txt": "x\n"})

        _, payload, _, _ = self.scan_branch()

        units = self.read_units(payload["inspection_id"])["units"]
        self.assertEqual([(u["commit"], u["label"]) for u in units],
                         [(sha, COMMIT_MESSAGE_LABEL), (sha, "dir/b.txt")])
        self.assertRegex(sha, r"^[0-9a-f]+$")

    def test_unit_ids_are_sequential_across_commits_and_skipped_units_take_no_number(self):
        self.init_repo()
        self.commit("first", {"a.txt": "one\n", "data.bin": b"\x00\x01"})
        self.commit("second", {"b.txt": "two\n"})

        _, payload, _, _ = self.scan_branch()

        data = self.read_units(payload["inspection_id"])
        self.assertEqual(
            [u["unit_id"] for u in data["units"]], ["U0001", "U0002", "U0003", "U0004"]
        )
        self.assertEqual(payload["unit_count"], 4)
        self.assertEqual([s["reason"] for s in data["skipped"]], ["binary"])

    def test_text_files_are_written_into_the_workspace(self):
        self.init_repo()
        self.commit("m", {"a.txt": "one\n"})

        _, payload, _, _ = self.scan_branch()

        place = self.tmpdir / f"{PLACE_PREFIX}{payload['inspection_id']}"
        for unit in self.read_units(payload["inspection_id"])["units"]:
            self.assertTrue(Path(unit["text_path"]).is_file())
            self.assertIn(place, Path(unit["text_path"]).parents)
        if os.name != "nt":
            self.assertEqual(os.stat(place).st_mode & 0o077, 0)

    def test_deleted_binary_and_quoted_path_are_recorded_by_scan(self):
        self.init_repo()
        self.commit("add", {"gone.txt": "one\n"})
        sha = self.commit("change", {"二進 data.bin": b"\x00", '資料 "x".txt': "line\n"})
        self.git("rm", "-q", "--", "gone.txt")
        self.git("commit", "-q", "-m", "remove")
        removed = self.git("rev-parse", "HEAD")

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0)
        data = self.read_units(payload["inspection_id"])
        self.assertEqual(
            sorted(data["skipped"], key=lambda s: s["label"]),
            sorted(
                [
                    {"label": "二進 data.bin", "commit": sha, "reason": "binary"},
                    {"label": "gone.txt", "commit": removed, "reason": "deleted"},
                ],
                key=lambda s: s["label"],
            ),
        )
        self.assertIn((sha, '資料 "x".txt'), [(u["commit"], u["label"]) for u in data["units"]])

    def test_repository_without_remote_targets_the_whole_history(self):
        self.init_repo()
        self.commit("first", {"a.txt": "one\n"})
        self.commit("second", {"b.txt": "two\n"})

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 4)

    def test_pushed_commits_are_not_targets(self):
        self.init_repo()
        self.git("init", "-q", "--bare", "-b", "main", str(self.remote), cwd=self.base_path)
        self.git("remote", "add", "origin", str(self.remote))
        self.commit("pushed", {"pushed.txt": "one\n"})
        self.git("push", "-q", "origin", "main")
        local = self.commit("local", {"local.txt": "two\n"})

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0)
        units = self.read_units(payload["inspection_id"])["units"]
        self.assertEqual([(u["commit"], u["label"]) for u in units],
                         [(local, COMMIT_MESSAGE_LABEL), (local, "local.txt")])

    def test_zero_units_when_everything_is_pushed(self):
        self.init_repo()
        self.git("init", "-q", "--bare", "-b", "main", str(self.remote), cwd=self.base_path)
        self.git("remote", "add", "origin", str(self.remote))
        self.commit("pushed", {"pushed.txt": "one\n"})
        self.git("push", "-q", "origin", "main")

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 0)
        self.assertNotIn("errors", payload)
        self.assertEqual(self.read_units(payload["inspection_id"]), {"units": [], "skipped": []})

    def test_empty_repository_gives_zero_units(self):
        self.init_repo()

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 0)
        self.assertEqual(self.read_units(payload["inspection_id"]), {"units": [], "skipped": []})

    def test_stdout_does_not_contain_the_body(self):
        self.init_repo()
        self.commit(f"message {BODY_MARKER}", {"a.txt": f"{BODY_MARKER}\n"})

        code, _, stdout, stderr = self.scan_branch()

        self.assertEqual(code, 0)
        self.assertNotIn(BODY_MARKER, stdout)
        self.assertNotIn(BODY_MARKER, stderr)

    def test_history_with_a_message_that_looks_like_a_delimiter_is_not_broken(self):
        self.init_repo()
        fake = "0" * 32 + "0" * 8
        sha = self.commit(f"subject\n\n{fake}\n@@ -1 +1 @@", {"a.txt": f"diff --git a/x b/x\n{fake}\n"})

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0)
        data = self.read_units(payload["inspection_id"])
        self.assertEqual([(u["commit"], u["label"]) for u in data["units"]],
                         [(sha, COMMIT_MESSAGE_LABEL), (sha, "a.txt")])
        self.assertEqual(
            Path(data["units"][1]["text_path"]).read_text(encoding="utf-8"),
            f"diff --git a/x b/x\n{fake}\n",
        )


def _git_supports_sha256():
    with tempfile.TemporaryDirectory() as tmp:
        probe = subprocess.run(
            ["git", "init", "-q", "--object-format=sha256", tmp], capture_output=True
        )
        return probe.returncode == 0


class Sha256RepositoryTest(ScanBranchTestCase):
    """識別子の長さに依存しない: SHA-256 のリポジトリ（識別子が 64 桁）でも検査できる。"""

    @unittest.skipUnless(_git_supports_sha256(), "この git は --object-format=sha256 に対応しない")
    def test_scan_branch_keeps_the_64_digit_commit_in_units_and_candidates(self):
        self.init_repo(object_format="sha256")
        email = "taro@" + "example.org"
        sha = self.commit(f"contact {email}", {"a.txt": f"mail {email}\n", "data.bin": b"\x00\x01"})
        self.assertEqual(len(sha), 64)

        code, payload, _, _ = self.scan_branch()

        self.assertEqual(code, 0, payload)
        data = self.read_units(payload["inspection_id"])
        self.assertEqual([(u["commit"], u["label"]) for u in data["units"]],
                         [(sha, COMMIT_MESSAGE_LABEL), (sha, "a.txt")])
        self.assertEqual(data["skipped"], [{"label": "data.bin", "commit": sha, "reason": "binary"}])
        place = self.tmpdir / f"{PLACE_PREFIX}{payload['inspection_id']}"
        pattern = json.loads((place / "pattern.json").read_text(encoding="utf-8"))
        self.assertEqual({c["commit"] for c in pattern["candidates"]}, {sha})
        self.assertEqual(len(pattern["candidates"]), 2)


class FailureExitOneTest(ScanBranchTestCase):
    def assert_failed_cleanly(self, result):
        code, payload, stdout, stderr = result
        self.assertEqual(code, 1)
        self.assertIsInstance(payload["errors"], list)
        self.assertTrue(payload["errors"])
        self.assertNotIn("inspection_id", payload)
        self.assertEqual(self.workspaces(), [])
        self.assertNotIn(BODY_MARKER, stdout)
        self.assertNotIn(BODY_MARKER, stderr)

    def test_project_root_that_is_not_a_git_repository(self):
        self.assert_failed_cleanly(self.scan_branch())

    def test_non_utf8_history_is_not_inspected_and_leaves_no_workspace(self):
        # 判断待ち（TASK-007 の報告）: UTF-8 として解釈できない履歴は、検査を実行できなかったことになる。
        # 対処の方針は本体が決める。現状の挙動として固定する。理由に本文を含めない
        self.init_repo()
        self.commit("m", {"latin1.txt": b"caf\xe9 " + BODY_MARKER.encode() + b"\n"})

        result = self.scan_branch()

        self.assert_failed_cleanly(result)
        self.assertIn("UTF-8", result[1]["errors"][0])

    def test_workspace_of_another_inspection_is_kept(self):
        self.init_repo()
        self.commit("m", {"a.txt": "one\n"})
        ok = self.scan_branch()[1]
        # 別の検査の失敗（git でない場所）が、成功した検査の置き場を消さない
        elsewhere = self.base_path / "not-a-repo"
        elsewhere.mkdir()
        code, _, _, _ = self.run_scan("--project-root", str(elsewhere), "--branch")
        self.assertEqual(code, 1)
        self.assertEqual(self.workspaces(), [f"{PLACE_PREFIX}{ok['inspection_id']}"])


class ArgumentErrorExitTwoTest(ScanBranchTestCase):
    def test_branch_together_with_another_scope_is_exit_two(self):
        for other in (["--diff"], ["--dirs", "docs"], ["--files", "a.txt"]):
            with self.subTest(other=other):
                code, _, stdout, _ = self.run_scan("--project-root", str(self.root), "--branch", *other)
                self.assertEqual(code, 2)
                self.assertEqual(stdout.strip(), "")
                self.assertEqual(self.workspaces(), [])

    def test_branch_takes_no_value(self):
        code, _, stdout, _ = self.run_scan("--project-root", str(self.root), "--branch", "main")
        self.assertEqual(code, 2)
        self.assertEqual(stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
