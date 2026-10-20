#!/usr/bin/env python3
"""secret-linter の scan.py の契約テスト。

DES-087 のモジュール表 W-1・T-1・W-2 と、テスト表 SC-01・02・03・16 のスクリプトの検証に対応する。

- 終了コード 0: units.json と pattern.json が作業領域に書かれ、検査 ID・unit_count・pattern_candidate_count が
  標準出力に出る。対象 0 件でもエラーにならない（pattern.json の中身は test_scan_pattern.py が検証する）
- 終了コード 1: 不在のファイル・不在のディレクトリ・git でないプロジェクトルート。標準出力に errors があり、
  検査 ID が無く、作業領域が残らない。想定外の例外でも作業領域が残らず、例外は再送出される
- 終了コード 2: 複数の範囲の指定・語彙外の引数・絶対パスでない --project-root（argparse）
- 範囲の指定が省略されたときは --diff として動く
- errors に検査単位の本文が含まれない

作業領域は OS の一時ディレクトリに作られる。テストは、一時ディレクトリの置き場（TMPDIR）をテストごとの
専用ディレクトリへ向け、他のテスト・他の検査と共有しない。成功した検査の作業領域は addCleanup で必ず消す。
入力は明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.test_scan -v
"""

import importlib.util
import io
import json
import os
import shutil
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

_spec = importlib.util.spec_from_file_location("secret_linter_scan", SCRIPT)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

PLACE_PREFIX = scan.workspace.PLACE_PREFIX

#: 本文の代わりに置く、明白なプレースホルダ
BODY_MARKER = "PLACEHOLDER-BODY-MARKER"


def _git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def init_repo(root: Path):
    _git(["init", "-b", "main"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Test User"], root)
    _git(["config", "commit.gpgSign", "false"], root)


def commit_all(root: Path):
    _git(["add", "-A"], root)
    _git(["commit", "-m", "initial commit"], root)


def write(root: Path, rel, content=BODY_MARKER + "\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class ScanTestCase(unittest.TestCase):
    """テストごとに、専用の一時ディレクトリの置き場・プロジェクトルート・外部の置き場を持つ。"""

    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        base_path = Path(base.name).resolve()
        # 作業領域の置き場（TMPDIR）。他のテスト・他の検査と共有しない
        self.tmpdir = base_path / "tmp"
        self.tmpdir.mkdir()
        self.root = base_path / "project"
        self.root.mkdir()
        self.outside = base_path / "outside"
        self.outside.mkdir()
        self._base_path = base_path

    def workspaces(self):
        """専用の置き場に在る作業領域の一覧。"""
        return sorted(p.name for p in self.tmpdir.glob(f"{PLACE_PREFIX}*"))

    def git_project(self):
        init_repo(self.root)

    def env(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.tmpdir)
        env.pop("TEMP", None)
        env.pop("TMP", None)
        # 一時ディレクトリの親に git リポジトリがあっても、それを検査対象に取り込まない
        env["GIT_CEILING_DIRECTORIES"] = str(self._base_path)
        return env

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
        """scan.run_cli を同じプロセスで呼ぶ。置き場は専用ディレクトリへ向ける。"""
        buffer = io.StringIO()
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            with mock.patch.dict(
                os.environ, {"GIT_CEILING_DIRECTORIES": str(self._base_path)}
            ):
                with redirect_stdout(buffer):
                    code = scan.run_cli(list(argv))
        return code, json.loads(buffer.getvalue())

    def read_units(self, inspection_id):
        path = self.tmpdir / f"{PLACE_PREFIX}{inspection_id}" / "units.json"
        return json.loads(path.read_text(encoding="utf-8"))


class SuccessTest(ScanTestCase):
    def test_diff_writes_units_and_prints_id_and_count(self):
        self.git_project()
        write(self.root, "base.txt")
        commit_all(self.root)
        write(self.root, "new.txt")

        code, payload, _ = self.run_scan("--project-root", str(self.root), "--diff")

        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(payload), ["inspection_id", "pattern_candidate_count", "unit_count"]
        )
        self.assertEqual(payload["unit_count"], 1)
        self.assertRegex(payload["inspection_id"], r"^[0-9a-f]{32}$")
        data = self.read_units(payload["inspection_id"])
        self.assertEqual(data["skipped"], [])
        self.assertEqual(
            data["units"],
            [
                {
                    "unit_id": "U0001",
                    "kind": "file",
                    "label": "new.txt",
                    "text_path": str(self.root / "new.txt"),
                }
            ],
        )

    def test_scope_omitted_behaves_as_diff(self):
        self.git_project()
        write(self.root, "base.txt")
        commit_all(self.root)
        write(self.root, "changed.txt")

        omitted = self.run_scan("--project-root", str(self.root))
        explicit = self.run_scan("--project-root", str(self.root), "--diff")

        self.assertEqual(omitted[0], 0)
        self.assertEqual(
            self.read_units(omitted[1]["inspection_id"])["units"],
            self.read_units(explicit[1]["inspection_id"])["units"],
        )
        self.assertEqual(omitted[1]["unit_count"], 1)

    def test_dirs_resolves_files_under_the_directory(self):
        self.git_project()
        write(self.root, "docs/a.md")
        write(self.root, "docs/sub/b.md")
        write(self.root, "src/c.py")
        commit_all(self.root)

        code, payload, _ = self.run_scan(
            "--project-root", str(self.root), "--dirs", "docs"
        )

        self.assertEqual(code, 0)
        data = self.read_units(payload["inspection_id"])
        self.assertEqual(
            [u["label"] for u in data["units"]], ["docs/a.md", "docs/sub/b.md"]
        )
        self.assertEqual(payload["unit_count"], 2)

    def test_files_accepts_a_file_outside_the_project_root(self):
        # 非 git のプロジェクトでも、--files は git を使わず外部のファイルを受理する
        external = write(self.outside, "body.txt")

        code, payload, _ = self.run_scan(
            "--project-root", str(self.root), "--files", str(external)
        )

        self.assertEqual(code, 0)
        unit = self.read_units(payload["inspection_id"])["units"][0]
        self.assertEqual(unit["label"], str(external))
        self.assertEqual(unit["text_path"], str(external))

    def test_files_records_skipped_units_with_reason(self):
        write(self.root, "ok.txt")
        (self.root / "bin.dat").write_bytes(b"abc\x00def")

        code, payload, _ = self.run_scan(
            "--project-root", str(self.root), "--files", "ok.txt", "bin.dat"
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 1)
        data = self.read_units(payload["inspection_id"])
        self.assertEqual(data["skipped"], [{"label": "bin.dat", "reason": "binary"}])

    def test_zero_targets_is_success_with_empty_units(self):
        # SC-02: 対象 0 件はエラーにならない
        self.git_project()
        write(self.root, "base.txt")
        commit_all(self.root)

        code, payload, _ = self.run_scan("--project-root", str(self.root), "--diff")

        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 0)
        self.assertNotIn("errors", payload)
        self.assertEqual(
            self.read_units(payload["inspection_id"]), {"units": [], "skipped": []}
        )

    def test_zero_targets_for_dirs_is_success(self):
        self.git_project()
        (self.root / "empty").mkdir()
        code, payload, _ = self.run_scan(
            "--project-root", str(self.root), "--dirs", "empty"
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["unit_count"], 0)

    def test_workspace_is_private_and_isolated_per_scan(self):
        write(self.root, "a.txt")
        first = self.run_scan("--project-root", str(self.root), "--files", "a.txt")[1]
        second = self.run_scan("--project-root", str(self.root), "--files", "a.txt")[1]
        self.assertNotEqual(first["inspection_id"], second["inspection_id"])
        self.assertEqual(len(self.workspaces()), 2)
        if os.name != "nt":
            place = self.tmpdir / f"{PLACE_PREFIX}{first['inspection_id']}"
            self.assertEqual(os.stat(place).st_mode & 0o077, 0)

    def test_stdout_is_a_single_json_object_without_body(self):
        write(self.root, "a.txt")
        code, _, completed = self.run_scan(
            "--project-root", str(self.root), "--files", "a.txt"
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        self.assertNotIn(BODY_MARKER, completed.stdout)


class FailureExitOneTest(ScanTestCase):
    def assert_failed_cleanly(self, result):
        code, payload, completed = result
        self.assertEqual(code, 1)
        self.assertIsInstance(payload["errors"], list)
        self.assertTrue(payload["errors"])
        self.assertNotIn("inspection_id", payload)
        self.assertEqual(self.workspaces(), [])
        self.assertNotIn(BODY_MARKER, completed.stdout)

    def test_missing_file(self):
        self.assert_failed_cleanly(
            self.run_scan(
                "--project-root", str(self.root), "--files", "no-such-file.txt"
            )
        )

    def test_missing_file_among_existing_ones(self):
        write(self.root, "a.txt")
        self.assert_failed_cleanly(
            self.run_scan(
                "--project-root", str(self.root), "--files", "a.txt", "no-such-file.txt"
            )
        )

    def test_directory_given_to_files(self):
        (self.root / "adir").mkdir()
        self.assert_failed_cleanly(
            self.run_scan("--project-root", str(self.root), "--files", "adir")
        )

    def test_missing_directory(self):
        self.git_project()
        self.assert_failed_cleanly(
            self.run_scan("--project-root", str(self.root), "--dirs", "no-such-dir")
        )

    def test_project_root_that_is_not_a_git_repository_for_diff(self):
        write(self.root, "a.txt")
        self.assert_failed_cleanly(
            self.run_scan("--project-root", str(self.root), "--diff")
        )

    def test_project_root_that_is_not_a_git_repository_for_default_scope(self):
        self.assert_failed_cleanly(self.run_scan("--project-root", str(self.root)))

    def test_project_root_that_is_not_a_git_repository_for_dirs(self):
        (self.root / "docs").mkdir()
        write(self.root, "docs/a.md")
        self.assert_failed_cleanly(
            self.run_scan("--project-root", str(self.root), "--dirs", "docs")
        )

    def test_existing_workspaces_of_other_inspections_are_kept(self):
        write(self.root, "a.txt")
        ok = self.run_scan("--project-root", str(self.root), "--files", "a.txt")[1]
        self.run_scan("--project-root", str(self.root), "--files", "no-such-file.txt")
        self.assertEqual(self.workspaces(), [f"{PLACE_PREFIX}{ok['inspection_id']}"])

    def test_workspace_creation_failure_is_exit_1(self):
        write(self.root, "a.txt")
        with mock.patch.object(
            scan.workspace,
            "create",
            side_effect=scan.workspace.WorkspaceError("作れません"),
        ):
            code, payload = self.run_in_process(
                "--project-root", str(self.root), "--files", "a.txt"
            )
        self.assertEqual(code, 1)
        self.assertEqual(payload, {"errors": ["作れません"]})

    def test_cleanup_failure_is_reported_alongside_the_original_reason(self):
        with mock.patch.object(
            scan.workspace,
            "remove",
            side_effect=scan.workspace.WorkspaceError("消せません"),
        ):
            code, payload = self.run_in_process(
                "--project-root", str(self.root), "--files", "no-such-file.txt"
            )
        self.assertEqual(code, 1)
        self.assertEqual(len(payload["errors"]), 2)
        self.assertIn("見つかりません", payload["errors"][0])
        self.assertIn("消せません", payload["errors"][1])
        # 消去に失敗したので、作った置き場がテスト側に残る。後始末する
        for name in self.workspaces():
            shutil.rmtree(self.tmpdir / name, ignore_errors=True)

    def test_errors_do_not_contain_the_body_of_a_unit(self):
        write(self.root, "a.txt", BODY_MARKER + "\n")
        code, _, completed = self.run_scan(
            "--project-root", str(self.root), "--files", "a.txt", "no-such-file.txt"
        )
        self.assertEqual(code, 1)
        self.assertNotIn(BODY_MARKER, completed.stdout)
        self.assertNotIn(BODY_MARKER, completed.stderr)

    def assert_non_utf8_path_is_exit_1(self, *argv):
        code, payload = self.run_in_process(*argv)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertIn("UTF-8", payload["errors"][0])
        self.assertNotIn("inspection_id", payload)
        self.assertEqual(self.workspaces(), [])

    def test_diff_with_a_path_that_is_not_utf8_is_exit_1_with_errors(self):
        # git の出力が UTF-8 として解釈できない（Linux 上の Shift_JIS のファイル名等）状態を、
        # git_targets の例外で再現する。トレースバックでなく、errors を標準出力に返す
        error = UnicodeDecodeError("utf-8", b"\x83", 0, 1, "invalid start byte")
        with mock.patch.object(
            scan.targets.git_targets, "get_diff_targets", side_effect=error
        ):
            self.assert_non_utf8_path_is_exit_1(
                "--project-root", str(self.root), "--diff"
            )

    def test_dirs_with_a_path_that_is_not_utf8_is_exit_1_with_errors(self):
        (self.root / "docs").mkdir()
        error = UnicodeDecodeError("utf-8", b"\x83", 0, 1, "invalid start byte")
        with mock.patch.object(
            scan.targets.git_targets, "get_dir_targets", side_effect=error
        ):
            self.assert_non_utf8_path_is_exit_1(
                "--project-root", str(self.root), "--dirs", "docs"
            )

    def test_files_with_a_path_that_is_not_utf8_is_exit_1_with_errors(self):
        # argv の UTF-8 でないバイト列は、Python では surrogate escape の文字になる
        self.assert_non_utf8_path_is_exit_1(
            "--project-root", str(self.root), "--files", "bad-\udc83-name.txt"
        )


class UnexpectedExceptionTest(ScanTestCase):
    def test_workspace_is_removed_and_exception_is_reraised(self):
        write(self.root, "a.txt")
        buffer = io.StringIO()
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            with mock.patch.object(
                scan.targets, "build_units", side_effect=RuntimeError("想定外")
            ):
                with redirect_stdout(buffer):
                    with self.assertRaises(RuntimeError):
                        scan.run_cli(
                            ["--project-root", str(self.root), "--files", "a.txt"]
                        )
        self.assertEqual(self.workspaces(), [])
        self.assertEqual(buffer.getvalue(), "")

    def test_exception_while_writing_units_also_leaves_no_workspace(self):
        write(self.root, "a.txt")
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            with mock.patch.object(
                scan.workspace, "write", side_effect=ValueError("想定外")
            ):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError):
                        scan.run_cli(
                            ["--project-root", str(self.root), "--files", "a.txt"]
                        )
        self.assertEqual(self.workspaces(), [])


class ArgumentErrorExitTwoTest(ScanTestCase):
    def assert_exit_two(self, *args):
        code, _, completed = self.run_scan(*args)
        self.assertEqual(code, 2, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "")
        self.assertEqual(self.workspaces(), [])

    def test_multiple_scopes_diff_and_dirs(self):
        self.assert_exit_two(
            "--project-root", str(self.root), "--diff", "--dirs", "docs"
        )

    def test_multiple_scopes_dirs_and_files(self):
        self.assert_exit_two(
            "--project-root", str(self.root), "--dirs", "docs", "--files", "a.txt"
        )

    def test_multiple_scopes_diff_and_files(self):
        self.assert_exit_two(
            "--project-root", str(self.root), "--diff", "--files", "a.txt"
        )

    def test_out_of_vocabulary_argument(self):
        self.assert_exit_two("--project-root", str(self.root), "--unknown")

    def test_relative_project_root(self):
        self.assert_exit_two("--project-root", "relative/path", "--diff")

    def test_missing_project_root(self):
        self.assert_exit_two("--diff")

    def test_dirs_without_a_path(self):
        self.assert_exit_two("--project-root", str(self.root), "--dirs")

    def test_files_without_a_path(self):
        self.assert_exit_two("--project-root", str(self.root), "--files")

    def test_argparse_raises_system_exit_2_in_process(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                scan.run_cli(
                    ["--project-root", str(self.root), "--diff", "--files", "a.txt"]
                )
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
