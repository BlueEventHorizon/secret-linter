#!/usr/bin/env python3
"""secret-linter の対象解決（targets.py）のユニットテスト。

DES-087 のモジュール表 T-1（--diff / --dirs / --files）と、テスト表 SC-01・02・03・16 の
スクリプトの検証に対応する。

- --diff: ステージ済み・未ステージ・未追跡が対象、削除は対象外、変更が無ければ 0 件
- --dirs: 配下が対象、.gitignore されたものは対象外、不在のディレクトリは TargetError
- --files: プロジェクトルート外の絶対パスを受理、相対はプロジェクトルートから、不在・ディレクトリは TargetError
- 検査単位の分類（NUL・読めない。大きさでは除外しない）、スキップの理由に本文を含めない、unit_id の連番

git を使うテストは、一時ディレクトリに git init して状態を作る。入力は明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.test_targets -v
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)

_spec = importlib.util.spec_from_file_location(
    "secret_linter_targets", _SCRIPTS_DIR / "targets.py"
)
targets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(targets)

TargetError = targets.TargetError

#: 本文の代わりに置く、明白なプレースホルダ
PLACEHOLDER = "EXAMPLE_PLACEHOLDER_VALUE\n"


def _git(args, cwd):
    subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def init_repo(root: Path):
    """一時 git リポジトリを初期化する（個人のグローバル設定に依存しない）。"""
    _git(["init", "-b", "main"], root)
    _git(["config", "user.email", "test@example.com"], root)
    _git(["config", "user.name", "Test User"], root)
    _git(["config", "commit.gpgSign", "false"], root)


def commit_all(root: Path, message="initial commit"):
    _git(["add", "-A"], root)
    _git(["commit", "-m", message], root)


def write(root: Path, rel, content=PLACEHOLDER):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        init_repo(self.root)


class ResolveDiffTest(RepoTestCase):
    def test_staged_unstaged_and_untracked_are_targets(self):
        write(self.root, "staged.txt", "a\n")
        write(self.root, "unstaged.txt", "b\n")
        commit_all(self.root)
        write(self.root, "staged.txt", "changed\n")
        _git(["add", "staged.txt"], self.root)
        write(self.root, "unstaged.txt", "changed\n")
        write(self.root, "untracked.txt", "new\n")

        entries = targets.resolve_diff(self.root)

        self.assertEqual(
            [label for label, _ in entries],
            ["staged.txt", "unstaged.txt", "untracked.txt"],
        )
        for label, path in entries:
            self.assertEqual(path, self.root / label)

    def test_deleted_files_are_not_targets(self):
        write(self.root, "gone_staged.txt")
        write(self.root, "gone_unstaged.txt")
        write(self.root, "keep.txt")
        commit_all(self.root)
        _git(["rm", "gone_staged.txt"], self.root)
        (self.root / "gone_unstaged.txt").unlink()
        write(self.root, "new.txt")

        entries = targets.resolve_diff(self.root)

        self.assertEqual([label for label, _ in entries], ["new.txt"])

    def test_no_changes_gives_zero_targets(self):
        write(self.root, "base.txt")
        commit_all(self.root)
        self.assertEqual(targets.resolve_diff(self.root), [])

    def test_git_failure_becomes_target_error(self):
        # git リポジトリではないディレクトリ。親に git リポジトリを持たない場所を作れないため、
        # 存在しないディレクトリを指して git の実行を失敗させる
        missing = self.root / "no-such-dir"
        with self.assertRaises(TargetError):
            targets.resolve_diff(missing)


class NonUtf8PathTest(RepoTestCase):
    """UTF-8 として解釈できないパスは、トレースバックでなく TargetError になる。理由にパスを含めない。"""

    def test_diff_decode_error_becomes_target_error(self):
        error = UnicodeDecodeError("utf-8", b"\x83", 0, 1, "invalid start byte")
        with unittest.mock.patch.object(
            targets.git_targets, "get_diff_targets", side_effect=error
        ):
            with self.assertRaises(TargetError) as ctx:
                targets.resolve_diff(self.root)
        self.assertIn("UTF-8", str(ctx.exception))

    def test_dirs_decode_error_becomes_target_error(self):
        (self.root / "docs").mkdir()
        error = UnicodeDecodeError("utf-8", b"\x83", 0, 1, "invalid start byte")
        with unittest.mock.patch.object(
            targets.git_targets, "get_dir_targets", side_effect=error
        ):
            with self.assertRaises(TargetError) as ctx:
                targets.resolve_dirs(self.root, ["docs"])
        self.assertIn("UTF-8", str(ctx.exception))

    def test_files_path_that_cannot_be_encoded_becomes_target_error(self):
        given = "bad-\udc83-name.txt"
        with self.assertRaises(TargetError) as ctx:
            targets.resolve_files(self.root, [given])
        self.assertIn("UTF-8", str(ctx.exception))
        self.assertNotIn("bad-", str(ctx.exception))


class ResolveDirsTest(RepoTestCase):
    def test_files_under_dir_are_targets_and_others_are_not(self):
        write(self.root, "docs/a.md")
        write(self.root, "docs/sub/b.md")
        write(self.root, "src/c.py")
        write(self.root, "outside.txt")
        commit_all(self.root)

        entries = targets.resolve_dirs(self.root, ["docs"])

        self.assertEqual(
            [label for label, _ in entries], ["docs/a.md", "docs/sub/b.md"]
        )
        for label, path in entries:
            self.assertEqual(path, self.root / label)

    def test_trailing_slash_is_accepted(self):
        write(self.root, "docs/a.md")
        commit_all(self.root)
        entries = targets.resolve_dirs(self.root, ["docs/"])
        self.assertEqual([label for label, _ in entries], ["docs/a.md"])

    def test_gitignored_files_are_not_targets(self):
        write(self.root, ".gitignore", "docs/ignored.md\n")
        write(self.root, "docs/a.md")
        commit_all(self.root)
        write(self.root, "docs/ignored.md")

        entries = targets.resolve_dirs(self.root, ["docs"])

        self.assertEqual([label for label, _ in entries], ["docs/a.md"])

    def test_untracked_files_are_targets(self):
        write(self.root, "docs/a.md")
        commit_all(self.root)
        write(self.root, "docs/new.md")
        entries = targets.resolve_dirs(self.root, ["docs"])
        self.assertEqual([label for label, _ in entries], ["docs/a.md", "docs/new.md"])

    def test_missing_directory_is_target_error(self):
        write(self.root, "docs/a.md")
        commit_all(self.root)
        with self.assertRaises(TargetError):
            targets.resolve_dirs(self.root, ["docs", "no-such-dir"])

    def test_file_given_as_directory_is_target_error(self):
        write(self.root, "a.txt")
        commit_all(self.root)
        with self.assertRaises(TargetError):
            targets.resolve_dirs(self.root, ["a.txt"])

    def test_empty_directory_gives_zero_targets(self):
        (self.root / "empty").mkdir()
        self.assertEqual(targets.resolve_dirs(self.root, ["empty"]), [])


class ResolveFilesTest(RepoTestCase):
    def test_file_outside_project_root_is_accepted_with_absolute_path(self):
        with tempfile.TemporaryDirectory() as outside:
            external = write(Path(outside), "body.txt")
            entries = targets.resolve_files(self.root, [str(external)])
            units, skipped = targets.build_units(entries)

        self.assertEqual(skipped, [])
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["label"], str(external))
        self.assertTrue(Path(units[0]["text_path"]).is_absolute())
        self.assertEqual(Path(units[0]["text_path"]), external)

    def test_relative_path_is_resolved_from_project_root(self):
        write(self.root, "sub/a.txt")
        entries = targets.resolve_files(self.root, ["sub/a.txt"])
        self.assertEqual(entries, [("sub/a.txt", self.root / "sub" / "a.txt")])
        units, _ = targets.build_units(entries)
        self.assertEqual(units[0]["label"], "sub/a.txt")
        self.assertEqual(units[0]["text_path"], str(self.root / "sub" / "a.txt"))

    def test_relative_path_does_not_depend_on_current_directory(self):
        write(self.root, "a.txt")
        previous = os.getcwd()
        with tempfile.TemporaryDirectory() as elsewhere:
            os.chdir(elsewhere)
            try:
                entries = targets.resolve_files(self.root, ["a.txt"])
            finally:
                os.chdir(previous)
        self.assertEqual(entries[0][1], self.root / "a.txt")

    def test_missing_file_is_target_error(self):
        with self.assertRaises(TargetError):
            targets.resolve_files(self.root, ["no-such-file.txt"])

    def test_one_missing_among_existing_is_target_error(self):
        write(self.root, "a.txt")
        with self.assertRaises(TargetError) as ctx:
            targets.resolve_files(self.root, ["a.txt", "no-such-file.txt"])
        self.assertIn("no-such-file.txt", str(ctx.exception))

    def test_directory_is_target_error(self):
        (self.root / "adir").mkdir()
        with self.assertRaises(TargetError):
            targets.resolve_files(self.root, ["adir"])

    def test_duplicate_specification_gives_one_entry(self):
        write(self.root, "a.txt")
        entries = targets.resolve_files(self.root, ["a.txt", "a.txt"])
        self.assertEqual(len(entries), 1)

    def test_files_not_tracked_by_git_are_accepted(self):
        # --files は git の状態に依らず、指定されたファイルの全体を対象にする
        write(self.root, ".gitignore", "ignored.txt\n")
        write(self.root, "ignored.txt")
        entries = targets.resolve_files(self.root, ["ignored.txt"])
        self.assertEqual([label for label, _ in entries], ["ignored.txt"])


class BuildUnitsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def entry(self, name, data):
        path = self.dir / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return (name, path)

    def test_unit_ids_are_sequential_from_u0001(self):
        entries = [self.entry(f"f{i}.txt", PLACEHOLDER) for i in range(3)]
        units, skipped = targets.build_units(entries)
        self.assertEqual([u["unit_id"] for u in units], ["U0001", "U0002", "U0003"])
        self.assertEqual(skipped, [])

    def test_unit_ids_skip_no_numbers_when_something_is_skipped(self):
        entries = [
            self.entry("a.txt", PLACEHOLDER),
            self.entry("bin.dat", b"abc\x00def"),
            self.entry("b.txt", PLACEHOLDER),
        ]
        units, skipped = targets.build_units(entries)
        self.assertEqual([u["unit_id"] for u in units], ["U0001", "U0002"])
        self.assertEqual([s["label"] for s in skipped], ["bin.dat"])

    def test_unit_shape(self):
        entry = self.entry("a.txt", PLACEHOLDER)
        units, _ = targets.build_units([entry])
        self.assertEqual(
            units,
            [
                {
                    "unit_id": "U0001",
                    "kind": "file",
                    "label": "a.txt",
                    "text_path": str(entry[1]),
                }
            ],
        )

    def test_large_file_is_a_unit_not_skipped(self):
        """大きさでは除外しない（除外すると、巨大なファイルの中の機密が、検査されないまま合格になる）。"""
        entry = self.entry("big.txt", b"a" * (2 * 1024 * 1024))
        units, skipped = targets.build_units([entry])
        self.assertEqual([u["label"] for u in units], ["big.txt"])
        self.assertEqual(skipped, [])

    def test_nul_byte_is_binary(self):
        entry = self.entry("bin.dat", b"abc\x00def")
        units, skipped = targets.build_units([entry])
        self.assertEqual(units, [])
        self.assertEqual(skipped, [{"label": "bin.dat", "reason": "binary"}])

    @unittest.skipIf(
        os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        "権限を外せない環境",
    )
    def test_unreadable_file_is_recorded(self):
        entry = self.entry("locked.txt", PLACEHOLDER)
        entry[1].chmod(0o000)
        self.addCleanup(entry[1].chmod, 0o600)
        units, skipped = targets.build_units([entry])
        self.assertEqual(units, [])
        self.assertEqual(skipped, [{"label": "locked.txt", "reason": "unreadable"}])

    def test_unreadable_when_the_file_vanished(self):
        path = self.dir / "vanished.txt"
        units, skipped = targets.build_units([("vanished.txt", path)])
        self.assertEqual(units, [])
        self.assertEqual(skipped, [{"label": "vanished.txt", "reason": "unreadable"}])

    def test_skip_reason_does_not_contain_the_body(self):
        body = "PLACEHOLDER-BODY-MARKER"
        entries = [
            self.entry("bin.dat", body.encode() + b"\x00"),
            ("vanished.txt", self.dir / "vanished.txt"),
        ]
        _, skipped = targets.build_units(entries)
        self.assertEqual(len(skipped), 2)
        self.assertNotIn(body, repr(skipped))

    def test_reasons_are_from_a_closed_vocabulary(self):
        self.assertEqual(
            {targets.SKIP_BINARY, targets.SKIP_UNREADABLE, targets.SKIP_DELETED},
            {"binary", "unreadable", "deleted"},
        )
        self.assertFalse(hasattr(targets, "SKIP_TOO_LARGE"))
        self.assertFalse(hasattr(targets, "MAX_FILE_BYTES"))

    def test_no_entries_gives_empty_results(self):
        self.assertEqual(targets.build_units([]), ([], []))

    def test_text_is_not_copied(self):
        entry = self.entry("a.txt", PLACEHOLDER)
        units, _ = targets.build_units([entry])
        self.assertEqual(units[0]["text_path"], str(entry[1]))


if __name__ == "__main__":
    unittest.main()
