#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""secret-linter の git_targets.py のユニットテスト。

secret-linter が自前で持つ、git の状態から対象ファイルを列挙する部品の振る舞いを検証する。
一時ディレクトリに git リポジトリを作り、staged / unstaged / 未追跡 / 削除 /
`.gitignore` 対象のファイルを作り分けて実挙動を確かめる。
入力はすべて明白なプレースホルダだけにする。

実行:
  python3 -m unittest tests.test_git_targets -v
"""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
    / "git_targets.py"
)

_spec = importlib.util.spec_from_file_location(
    "secret_linter_git_targets", _SCRIPT_PATH
)
git_targets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(git_targets)


def _git(args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(project_root: Path):
    """一時 git リポジトリを初期化する（個人のグローバル設定に依存しない）。"""
    _git(["init", "-b", "main"], project_root)
    _git(["config", "user.email", "test@example.com"], project_root)
    _git(["config", "user.name", "Test User"], project_root)
    _git(["config", "commit.gpgSign", "false"], project_root)


def _commit_all(project_root: Path, message: str):
    _git(["add", "-A"], project_root)
    _git(["commit", "-m", message], project_root)


def _write(project_root: Path, rel_path: str, content: str = "content\n"):
    path = project_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class RunGitTest(unittest.TestCase):
    """run_git: 成功と失敗で終了コードを返す。"""

    def test_success_returns_zero_and_stdout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)

            code, stdout, _stderr = git_targets.run_git(
                ["rev-parse", "--is-inside-work-tree"], project_root
            )

        self.assertEqual(code, 0)
        self.assertEqual(stdout.strip(), "true")

    def test_failure_returns_nonzero_and_stderr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)

            code, _stdout, stderr = git_targets.run_git(
                ["no-such-subcommand"], project_root
            )

        self.assertNotEqual(code, 0)
        self.assertTrue(stderr)

    def test_git_not_launchable_returns_one(self):
        with mock.patch.object(
            git_targets.subprocess, "run", side_effect=OSError("boom")
        ):
            code, stdout, stderr = git_targets.run_git(["status"], Path("."))

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("git 実行に失敗しました", stderr)


class GetDiffTargetsTest(unittest.TestCase):
    """get_diff_targets: 未 commit 変更 + 未追跡を列挙し、削除を除く。"""

    def test_staged_unstaged_untracked_are_all_listed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "base.txt", "base\n")
            _write(project_root, "unstaged.txt", "u\n")
            _commit_all(project_root, "initial commit")

            _write(project_root, "unstaged.txt", "changed\n")
            _write(project_root, "base.txt", "staged change\n")
            _git(["add", "base.txt"], project_root)
            _write(project_root, "untracked.txt", "new\n")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, ["base.txt", "unstaged.txt", "untracked.txt"])

    def test_deleted_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "staged_delete.txt", "x\n")
            _write(project_root, "unstaged_delete.txt", "x\n")
            _write(project_root, "keep.txt", "k\n")
            _commit_all(project_root, "initial commit")

            _git(["rm", "staged_delete.txt"], project_root)
            (project_root / "unstaged_delete.txt").unlink()
            _write(project_root, "new.txt", "new\n")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, ["new.txt"])

    def test_untracked_files_in_new_directory_are_listed_individually(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "base.txt", "base\n")
            _commit_all(project_root, "initial commit")

            _write(project_root, "newdir/a.txt", "a\n")
            _write(project_root, "newdir/sub/b.txt", "b\n")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, ["newdir/a.txt", "newdir/sub/b.txt"])

    def test_gitignored_untracked_file_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, ".gitignore", "ignored.txt\n")
            _commit_all(project_root, "initial commit")

            _write(project_root, "ignored.txt", "ignored\n")
            _write(project_root, "visible.txt", "v\n")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, ["visible.txt"])

    def test_renamed_file_is_listed_by_new_path_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "old_name.txt", "body of the renamed file\n")
            _commit_all(project_root, "initial commit")

            _git(["mv", "old_name.txt", "new_name.txt"], project_root)

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, ["new_name.txt"])

    def test_non_ascii_filename_is_not_escaped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "base.txt", "base\n")
            _commit_all(project_root, "initial commit")

            _write(project_root, "日本語.txt", "new\n")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, ["日本語.txt"])

    def test_no_changes_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "base.txt", "base\n")
            _commit_all(project_root, "initial commit")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, [])

    def test_paths_are_sorted_and_project_root_relative(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "z.txt")
            _write(project_root, "sub/dir/m.txt")
            _write(project_root, "a.txt")

            result = git_targets.get_diff_targets(project_root)

        self.assertEqual(result, sorted(result))
        self.assertEqual(result, ["a.txt", "sub/dir/m.txt", "z.txt"])
        for f in result:
            self.assertFalse(Path(f).is_absolute())

    def test_git_failure_raises_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # git リポジトリではないディレクトリ（親にも無い一時領域）
            with mock.patch.object(
                git_targets, "run_git", return_value=(128, "", "fatal: placeholder")
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    git_targets.get_diff_targets(Path(tmpdir))

        self.assertIn("git status の実行に失敗しました", str(ctx.exception))


class GetDirTargetsTest(unittest.TestCase):
    """get_dir_targets: 指定ディレクトリ配下を列挙し、`.gitignore` を尊重する。"""

    def test_lists_tracked_files_under_dirs_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "docs/a.md", "a\n")
            _write(project_root, "docs/sub/b.md", "b\n")
            _write(project_root, "src/c.py", "c\n")
            _write(project_root, "outside.txt", "x\n")
            _commit_all(project_root, "initial commit")

            result = git_targets.get_dir_targets(project_root, ["docs", "src"])

        self.assertEqual(result, ["docs/a.md", "docs/sub/b.md", "src/c.py"])

    def test_untracked_file_is_included(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "docs/tracked.md", "t\n")
            _commit_all(project_root, "initial commit")
            _write(project_root, "docs/untracked.md", "u\n")

            result = git_targets.get_dir_targets(project_root, ["docs"])

        self.assertEqual(result, ["docs/tracked.md", "docs/untracked.md"])

    def test_gitignored_file_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, ".gitignore", "docs/ignored.md\nbuild/\n")
            _write(project_root, "docs/a.md", "a\n")
            _commit_all(project_root, "initial commit")
            _write(project_root, "docs/ignored.md", "placeholder\n")
            _write(project_root, "build/out.txt", "placeholder\n")

            result_docs = git_targets.get_dir_targets(project_root, ["docs"])
            result_build = git_targets.get_dir_targets(project_root, ["build"])

        self.assertEqual(result_docs, ["docs/a.md"])
        self.assertEqual(result_build, [])

    def test_git_directory_is_not_listed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "a.txt", "a\n")
            _commit_all(project_root, "initial commit")

            result = git_targets.get_dir_targets(project_root, ["."])

        self.assertEqual(result, ["a.txt"])
        for f in result:
            self.assertFalse(f.startswith(".git/"))

    def test_file_deleted_from_worktree_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "docs/keep.md", "k\n")
            _write(project_root, "docs/gone.md", "g\n")
            _commit_all(project_root, "initial commit")

            (project_root / "docs" / "gone.md").unlink()

            result = git_targets.get_dir_targets(project_root, ["docs"])

        self.assertEqual(result, ["docs/keep.md"])

    def test_paths_are_sorted_and_project_root_relative(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            _init_repo(project_root)
            _write(project_root, "docs/z.md")
            _write(project_root, "docs/a.md")
            _write(project_root, "docs/sub/m.md")
            _commit_all(project_root, "initial commit")

            result = git_targets.get_dir_targets(project_root, ["docs"])

        self.assertEqual(result, sorted(result))
        for f in result:
            self.assertFalse(Path(f).is_absolute())

    def test_git_failure_raises_runtime_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch.object(
                git_targets, "run_git", return_value=(128, "", "fatal: placeholder")
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    git_targets.get_dir_targets(Path(tmpdir), ["docs"])

        self.assertIn("git ls-files の実行に失敗しました", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
