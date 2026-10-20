#!/usr/bin/env python3
"""secret-linter の履歴解析（history.py の ``--branch``）のユニットテスト。

DES-087 のモジュール表 T-1（``--branch``）と、テスト表 SC-01・03・16 のスクリプトの検証に対応する。

- 検査単位: 追加行（``added_lines``。label はパス）とコミットメッセージ（``commit_message``。label は固定の語）。
  どちらも ``commit`` の項目に commit の識別子を持つ。行番号、unit は commit の出力順で、各 commit の
  ``commit_message``、続けて ``added_lines`` が並ぶ
- skipped の要素も ``commit`` を持つ
- マージ commit: 追加行は検査単位にならず、メッセージだけが検査単位になる（個別のテスト）
- ファイルが削除された commit: ``skipped`` に ``deleted`` で記録する（個別のテスト）
- バイナリ: ``skipped`` に ``binary`` で記録する（個別のテスト）
- 引用符付きのパス（非 ASCII・空白・引用符）を元のパスへ戻す（個別のテスト）
- 範囲: リモートを持たないリポジトリは全履歴、push 済みの commit は対象外、0 件は検査単位 0 件
- 区切りに似た文字列・diff の見出しに似た内容で壊れない
- 判断待ち（TASK-007 の報告。本体の対処方針は未決のため、現状の挙動をそのまま固定する）:
  UTF-8 でない履歴は ``TargetError``、本文に単独の CR を含む追加行は行が分かれる

git のリポジトリは一時ディレクトリに作る。個人・システムの git 設定とホームを読まず、作業領域の置き場
（TMPDIR）もテストごとの専用ディレクトリへ向ける。入力は公式の例示値・明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.test_history -v
"""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)

_spec = importlib.util.spec_from_file_location(
    "secret_linter_history", _SCRIPTS_DIR / "history.py"
)
history = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(history)

TargetError = history.targets.TargetError

#: 本文の代わりに置く、明白なプレースホルダ
PLACEHOLDER = "EXAMPLE_PLACEHOLDER_VALUE"
MSG = history.LABEL_COMMIT_MESSAGE


class HistoryTestCase(unittest.TestCase):
    """テストごとに、専用の置き場（TMPDIR）・リポジトリ・外部の bare リポジトリを持つ。"""

    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        base_path = Path(base.name).resolve()
        self.tmpdir = base_path / "tmp"
        self.tmpdir.mkdir()
        self.root = base_path / "project"
        self.root.mkdir()
        self.remote = base_path / "remote.git"
        env = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CEILING_DIRECTORIES": str(base_path),
        }
        for patcher in (
            mock.patch.object(tempfile, "tempdir", str(self.tmpdir)),
            mock.patch.dict(os.environ, env),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- git の操作 -------------------------------------------------------

    def git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", *args], cwd=str(cwd or self.root), capture_output=True, check=True
        )
        return result.stdout.decode("utf-8", errors="replace").strip()

    def init_repo(self):
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")
        self.git("config", "commit.gpgSign", "false")

    def commit(self, message, files=None, remove=(), allow_empty_message=False):
        """``files``（パス -> str/bytes）を書いて commit し、commit の識別子を返す。"""
        for rel, content in (files or {}).items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                content if isinstance(content, bytes) else content.encode("utf-8")
            )
        for rel in remove:
            self.git("rm", "-q", "--", rel)
        self.git("add", "-A")
        args = ["commit", "-q", "--allow-empty", "-m", message]
        if allow_empty_message:
            args.append("--allow-empty-message")
        self.git(*args)
        return self.head()

    def head(self):
        return self.git("rev-parse", "HEAD")

    def attach_remote(self):
        """ローカルの bare リポジトリをリモート ``origin`` にする（push 先）。"""
        self.git(
            "init", "-q", "--bare", "-b", "main", str(self.remote), cwd=self.root.parent
        )
        self.git("remote", "add", "origin", str(self.remote))

    # --- 解決 -------------------------------------------------------------

    def resolve(self):
        """``history.resolve_branch`` を呼び、``(検査単位の配列, スキップの配列)`` を返す。"""
        inspection_id = history.workspace.create()
        return history.resolve_branch(self.root, inspection_id)

    @staticmethod
    def body(unit):
        return Path(unit["text_path"]).read_bytes().decode("utf-8")

    def by_key(self, units):
        """(commit, label) -> 検査単位。組は一意でなければならない。"""
        keyed = {(u["commit"], u["label"]): u for u in units}
        self.assertEqual(len(keyed), len(units), "(commit, label) が重複している")
        return keyed

    @staticmethod
    def keys(units):
        """検査単位の ``(commit, label)`` を出力順に並べる。"""
        return [(u["commit"], u["label"]) for u in units]


class RangeTest(HistoryTestCase):
    def test_repository_without_remote_targets_the_whole_history(self):
        self.init_repo()
        first = self.commit("first message", {"a.txt": "one\n"})
        second = self.commit("second message", {"b.txt": "two\n"})

        units, skipped = self.resolve()

        self.assertEqual(skipped, [])
        self.assertEqual(
            set(self.keys(units)),
            {(first, MSG), (first, "a.txt"), (second, MSG), (second, "b.txt")},
        )

    def test_pushed_commits_are_excluded_and_unpushed_ones_are_targets(self):
        self.init_repo()
        self.attach_remote()
        pushed = self.commit("pushed message", {"pushed.txt": "pushed\n"})
        self.git("push", "-q", "origin", "main")
        local = self.commit("local message", {"local.txt": "local\n"})

        units, skipped = self.resolve()

        self.assertEqual(set(self.keys(units)), {(local, MSG), (local, "local.txt")})
        self.assertNotIn(pushed, {u["commit"] for u in units})
        self.assertEqual(skipped, [])

    def test_no_commit_outside_the_remote_gives_zero_units(self):
        self.init_repo()
        self.attach_remote()
        self.commit("pushed message", {"pushed.txt": "pushed\n"})
        self.git("push", "-q", "origin", "main")

        self.assertEqual(self.resolve(), ([], []))

    def test_empty_repository_gives_zero_units(self):
        self.init_repo()
        self.assertEqual(self.resolve(), ([], []))

    def test_not_a_git_repository_is_target_error(self):
        # git リポジトリでない（GIT_CEILING_DIRECTORIES により、親の探索もしない）
        with self.assertRaises(TargetError):
            self.resolve()

    def test_value_added_in_an_intermediate_commit_and_removed_later_is_a_target(self):
        # 履歴の追加行を見るため、後で消した値も検査単位に残る（取りこぼし防止）
        self.init_repo()
        added = self.commit("add", {"temp.txt": f"{PLACEHOLDER}_REMOVED_LATER\n"})
        self.commit("remove", {"temp.txt": "clean\n"})

        units, _ = self.resolve()

        unit = self.by_key(units)[(added, "temp.txt")]
        self.assertEqual(self.body(unit), f"{PLACEHOLDER}_REMOVED_LATER\n")
        # 現在の作業ツリーの内容（clean）は、この commit の追加行ではない
        self.assertNotIn("clean", self.body(unit))


class UnitShapeTest(HistoryTestCase):
    def test_label_and_commit_are_separate_fields(self):
        self.init_repo()
        sha = self.commit("message body", {"dir/a.txt": "one\n"})

        units, _ = self.resolve()

        kinds = {(u["commit"], u["label"]): u["kind"] for u in units}
        self.assertEqual(
            kinds, {(sha, MSG): "commit_message", (sha, "dir/a.txt"): "added_lines"}
        )
        self.assertEqual(MSG, "コミットメッセージ")
        for unit in units:
            self.assertRegex(unit["commit"], r"^[0-9a-f]+$")
            self.assertNotIn(unit["commit"], unit["label"])

    def test_commit_message_unit_holds_the_message_and_has_no_line_numbers(self):
        self.init_repo()
        sha = self.commit("subject line\n\nbody line\n", {"a.txt": "x\n"})

        units, _ = self.resolve()

        unit = self.by_key(units)[(sha, MSG)]
        self.assertEqual(unit["kind"], "commit_message")
        self.assertEqual(self.body(unit), "subject line\n\nbody line\n")
        self.assertNotIn("line_numbers", unit)
        self.assertNotIn("unit_id", unit)

    def test_added_lines_body_is_the_added_lines_joined_with_a_trailing_newline(self):
        self.init_repo()
        sha = self.commit("m", {"a.txt": "alpha\nbeta\ngamma\n"})

        units, _ = self.resolve()

        unit = self.by_key(units)[(sha, "a.txt")]
        self.assertEqual(unit["kind"], "added_lines")
        self.assertEqual(self.body(unit), "alpha\nbeta\ngamma\n")
        self.assertEqual(unit["line_numbers"], [1, 2, 3])

    def test_units_are_ordered_message_first_then_added_lines_per_commit(self):
        self.init_repo()
        first = self.commit("first", {"a.txt": "one\n"})
        second = self.commit("second", {"b.txt": "two\n", "c.txt": "three\n"})

        units, _ = self.resolve()

        self.assertEqual(
            self.keys(units),
            [
                (second, MSG),
                (second, "b.txt"),
                (second, "c.txt"),
                (first, MSG),
                (first, "a.txt"),
            ],
        )

    def test_empty_commit_message_is_not_a_unit(self):
        self.init_repo()
        sha = self.commit("", {"a.txt": "one\n"}, allow_empty_message=True)

        units, _ = self.resolve()

        self.assertEqual(self.keys(units), [(sha, "a.txt")])

    def test_empty_file_has_no_added_lines_and_is_not_skipped(self):
        self.init_repo()
        sha = self.commit("m", {"empty.txt": ""})

        units, skipped = self.resolve()

        self.assertEqual(self.keys(units), [(sha, MSG)])
        self.assertEqual(skipped, [])

    def test_texts_are_written_into_the_workspace(self):
        self.init_repo()
        self.commit("m", {"a.txt": "one\n"})
        inspection_id = history.workspace.create()
        units, _ = history.resolve_branch(self.root, inspection_id)
        place = history.workspace.workspace_path(inspection_id)
        for unit in units:
            self.assertEqual(Path(unit["text_path"]).parent.parent, place)


class LineNumberTest(HistoryTestCase):
    def numbers_match_file(self, sha, rel, file_lines):
        units, _ = self.resolve()
        unit = self.by_key(units)[(sha, rel)]
        lines = self.body(unit).splitlines()
        self.assertEqual(len(lines), len(unit["line_numbers"]))
        for text, number in zip(lines, unit["line_numbers"]):
            self.assertEqual(file_lines[number - 1], text, f"行番号 {number}")
        return unit

    def test_new_file_numbers_start_at_one(self):
        self.init_repo()
        sha = self.commit("m", {"a.txt": "l1\nl2\nl3\n"})
        unit = self.numbers_match_file(sha, "a.txt", ["l1", "l2", "l3"])
        self.assertEqual(unit["line_numbers"], [1, 2, 3])

    def test_single_line_file_with_abbreviated_hunk_header(self):
        # ``@@ -0,0 +1 @@``（追加の行数の ``,1`` が省略される）
        self.init_repo()
        sha = self.commit("m", {"a.txt": "only\n"})
        unit = self.numbers_match_file(sha, "a.txt", ["only"])
        self.assertEqual(unit["line_numbers"], [1])

    def test_modification_and_insertion_in_the_middle_of_a_file(self):
        self.init_repo()
        original = [f"line{i}" for i in range(1, 11)]
        self.commit("base", {"a.txt": "\n".join(original) + "\n"})
        changed = list(original)
        changed[2] = "CHANGED3"  # ``@@ -3 +3 @@``（行数が省略された見出し）
        changed[5:5] = ["INSERT_A", "INSERT_B"]  # ``@@ -5,0 +6,2 @@`` 相当
        sha = self.commit("edit", {"a.txt": "\n".join(changed) + "\n"})

        unit = self.numbers_match_file(sha, "a.txt", changed)

        self.assertEqual(
            self.body(unit).splitlines(), ["CHANGED3", "INSERT_A", "INSERT_B"]
        )
        self.assertEqual(unit["line_numbers"], [3, 6, 7])

    def test_multiple_hunks_in_one_file(self):
        self.init_repo()
        original = [f"line{i}" for i in range(1, 21)]
        self.commit("base", {"a.txt": "\n".join(original) + "\n"})
        changed = list(original)
        changed[1] = "FIRST_CHANGE"
        changed[17] = "SECOND_CHANGE"
        sha = self.commit("edit", {"a.txt": "\n".join(changed) + "\n"})

        unit = self.numbers_match_file(sha, "a.txt", changed)

        self.assertEqual(unit["line_numbers"], [2, 18])

    def test_removed_lines_are_not_added_lines_and_shift_the_numbers(self):
        self.init_repo()
        self.commit("base", {"a.txt": "k1\nk2\nk3\nk4\n"})
        sha = self.commit("edit", {"a.txt": "k2\nk3\nADDED\nk4\n"})

        unit = self.numbers_match_file(sha, "a.txt", ["k2", "k3", "ADDED", "k4"])

        self.assertEqual(unit["line_numbers"], [3])

    def test_file_without_a_trailing_newline(self):
        self.init_repo()
        sha = self.commit("m", {"a.txt": "l1\nl2"})
        unit = self.numbers_match_file(sha, "a.txt", ["l1", "l2"])
        self.assertEqual(unit["line_numbers"], [1, 2])

    def test_crlf_file_keeps_the_line_count_and_numbers(self):
        self.init_repo()
        sha = self.commit("m", {"a.txt": b"l1\r\nl2\r\nl3\r\n"})
        unit = self.numbers_match_file(sha, "a.txt", ["l1", "l2", "l3"])
        self.assertEqual(unit["line_numbers"], [1, 2, 3])


class MergeCommitTest(HistoryTestCase):
    def make_merge(self):
        """main と feature を ``--no-ff`` でマージする。``(feature の commit, マージ commit)`` を返す。"""
        self.init_repo()
        self.commit("base", {"base.txt": "base\n"})
        self.git("checkout", "-q", "-b", "feature")
        feature = self.commit("feature work", {"feature.txt": "FEATURE_LINE\n"})
        self.git("checkout", "-q", "main")
        self.commit("main work", {"main.txt": "MAIN_LINE\n"})
        self.git(
            "merge",
            "-q",
            "--no-ff",
            "-m",
            "Merge feature MERGE_MESSAGE_MARKER",
            "feature",
        )
        return feature, self.head()

    def test_merge_commit_message_is_a_unit(self):
        _, merge = self.make_merge()

        units, _ = self.resolve()

        unit = self.by_key(units)[(merge, MSG)]
        self.assertEqual(unit["kind"], "commit_message")
        self.assertIn("MERGE_MESSAGE_MARKER", self.body(unit))

    def test_merge_commit_adds_no_added_lines_unit(self):
        _, merge = self.make_merge()

        units, _ = self.resolve()

        self.assertEqual(
            [u for u in units if u["commit"] == merge and u["kind"] == "added_lines"],
            [],
        )

    def test_lines_of_the_merged_branch_belong_to_its_own_commit_only(self):
        feature, merge = self.make_merge()

        units, _ = self.resolve()

        keyed = self.by_key(units)
        self.assertEqual(self.body(keyed[(feature, "feature.txt")]), "FEATURE_LINE\n")
        self.assertNotIn((merge, "feature.txt"), keyed)

    def test_lines_added_by_the_merge_commit_itself_are_not_units(self):
        # 競合の解消等でマージ commit 自体が入れた変更は対象外（REQ-032 の対象外）
        self.init_repo()
        self.commit("base", {"base.txt": "base\n"})
        self.git("checkout", "-q", "-b", "feature")
        self.commit("feature work", {"feature.txt": "FEATURE_LINE\n"})
        self.git("checkout", "-q", "main")
        self.commit("main work", {"main.txt": "MAIN_LINE\n"})
        self.git("merge", "-q", "--no-ff", "--no-commit", "feature")
        (self.root / "by_merge.txt").write_text(
            "ADDED_BY_THE_MERGE_ITSELF\n", encoding="utf-8"
        )
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "merge with an extra file")
        merge = self.head()

        units, skipped = self.resolve()

        self.assertEqual([k for k in self.keys(units) if k[0] == merge], [(merge, MSG)])
        self.assertFalse(
            any("ADDED_BY_THE_MERGE_ITSELF" in self.body(u) for u in units)
        )
        self.assertEqual([s for s in skipped if s["commit"] == merge], [])

    def test_unit_count_of_a_merge_history(self):
        feature, merge = self.make_merge()

        units, skipped = self.resolve()

        # 4 つの commit のメッセージ（base・feature・main・merge）と、マージ commit を除く 3 つの追加行
        self.assertEqual(len(units), 4 + 3)
        self.assertEqual(skipped, [])


class DeletedFileTest(HistoryTestCase):
    def test_commit_that_deletes_a_file_is_recorded_as_deleted(self):
        self.init_repo()
        added = self.commit("add", {"gone.txt": "one\ntwo\n"})
        deleted = self.commit("delete", remove=["gone.txt"])

        units, skipped = self.resolve()

        self.assertEqual(
            skipped, [{"label": "gone.txt", "commit": deleted, "reason": "deleted"}]
        )
        keys = self.keys(units)
        self.assertNotIn((deleted, "gone.txt"), keys)
        # 削除の commit のメッセージは検査単位のまま、追加した commit の追加行も残る
        self.assertIn((deleted, MSG), keys)
        self.assertIn((added, "gone.txt"), keys)

    def test_commit_that_only_deletes_has_only_its_message_as_a_unit(self):
        self.init_repo()
        self.commit("add", {"gone.txt": "one\n"})
        deleted = self.commit("delete", remove=["gone.txt"])

        units, _ = self.resolve()

        self.assertEqual(
            [k for k in self.keys(units) if k[0] == deleted], [(deleted, MSG)]
        )

    def test_deleting_one_file_does_not_hide_other_files_of_the_same_commit(self):
        self.init_repo()
        self.commit("add", {"gone.txt": "one\n"})
        sha = self.commit(
            "delete and add", {"new.txt": "NEW_LINE\n"}, remove=["gone.txt"]
        )

        units, skipped = self.resolve()

        self.assertEqual(self.body(self.by_key(units)[(sha, "new.txt")]), "NEW_LINE\n")
        self.assertEqual(
            skipped, [{"label": "gone.txt", "commit": sha, "reason": "deleted"}]
        )


class BinaryTest(HistoryTestCase):
    def test_binary_file_is_recorded_as_binary(self):
        self.init_repo()
        sha = self.commit("add binary", {"data.bin": b"abc\x00def\x00"})

        units, skipped = self.resolve()

        self.assertEqual(
            skipped, [{"label": "data.bin", "commit": sha, "reason": "binary"}]
        )
        self.assertNotIn((sha, "data.bin"), self.keys(units))

    def test_binary_file_does_not_hide_text_files_of_the_same_commit(self):
        self.init_repo()
        sha = self.commit("add", {"data.bin": b"\x00\x01\x02", "a.txt": "TEXT_LINE\n"})

        units, skipped = self.resolve()

        self.assertEqual(self.body(self.by_key(units)[(sha, "a.txt")]), "TEXT_LINE\n")
        self.assertEqual(
            skipped, [{"label": "data.bin", "commit": sha, "reason": "binary"}]
        )

    def test_text_that_looks_like_a_binary_marker_is_still_text(self):
        # 本文の ``Binary files … differ`` は、追加行（``+`` で始まる）としてのみ現れる
        self.init_repo()
        sha = self.commit("add", {"a.txt": "Binary files a/x and b/x differ\n"})

        units, skipped = self.resolve()

        self.assertEqual(skipped, [])
        self.assertEqual(
            self.body(self.by_key(units)[(sha, "a.txt")]),
            "Binary files a/x and b/x differ\n",
        )


class LargeBodyTest(HistoryTestCase):
    def test_large_body_is_a_unit_not_skipped(self):
        """追加行の本文が大きくても、除外しない（除外すると、機密が検査されないまま合格になる）。"""
        self.init_repo()
        sha = self.commit(
            "m", {"big.txt": "x" * (2 * 1024 * 1024) + "\n", "small.txt": "ok\n"}
        )

        units, skipped = self.resolve()

        self.assertEqual(skipped, [])
        self.assertIn((sha, "big.txt"), self.keys(units))
        self.assertIn((sha, "small.txt"), self.keys(units))


class QuotedPathTest(HistoryTestCase):
    """git が引用符で囲んで出力するパス（非 ASCII・空白・引用符等）を、元のパスへ戻す。"""

    def assert_path_is_restored(self, rel):
        self.init_repo()
        sha = self.commit("m", {rel: f"{PLACEHOLDER}\n"})

        units, skipped = self.resolve()

        self.assertEqual(skipped, [])
        unit = self.by_key(units)[(sha, rel)]
        self.assertEqual(self.body(unit), f"{PLACEHOLDER}\n")

    def test_non_ascii_path(self):
        self.assert_path_is_restored("日本語/ファイル名.txt")

    def test_path_with_a_space(self):
        self.assert_path_is_restored("dir with space/file name.txt")

    def test_path_with_double_quotes(self):
        self.assert_path_is_restored('dir/a "quoted" name.txt')

    def test_path_with_a_backslash(self):
        self.assert_path_is_restored("dir/back\\slash.txt")

    def test_path_with_non_ascii_space_and_quote_together(self):
        self.assert_path_is_restored('資料 "写し"/メモ 1.txt')

    def test_path_with_a_tab(self):
        self.assert_path_is_restored("dir/tab\there.txt")

    def test_unquoted_path_that_contains_a_b_slash_sequence(self):
        # 引用符なしの見出し ``a/P b/P`` は、``P`` が `` b/`` を含んでも長さから元のパスへ戻す
        self.assert_path_is_restored("my b/file b/x.txt")

    def test_deleted_and_binary_files_with_quoted_paths(self):
        self.init_repo()
        self.commit("add", {"削除 する.txt": "one\n"})
        sha = self.commit(
            "delete and add binary",
            {"二進 data.bin": b"\x00\x01"},
            remove=["削除 する.txt"],
        )

        _, skipped = self.resolve()

        self.assertEqual(
            sorted(skipped, key=lambda s: s["label"]),
            sorted(
                [
                    {"label": "削除 する.txt", "commit": sha, "reason": "deleted"},
                    {"label": "二進 data.bin", "commit": sha, "reason": "binary"},
                ],
                key=lambda s: s["label"],
            ),
        )

    def test_quotepath_disabled_in_the_user_configuration_gives_the_same_label(self):
        self.init_repo()
        self.git("config", "core.quotepath", "false")
        rel = "日本語/ファイル.txt"
        sha = self.commit("m", {rel: f"{PLACEHOLDER}\n"})

        units, _ = self.resolve()

        self.assertIn((sha, rel), self.keys(units))


class DelimiterCollisionTest(HistoryTestCase):
    """メッセージや diff の内容が区切り・見出しに似た文字列を含んでも、壊れず取りこぼさない。"""

    HOSTILE_LINES = [
        "diff --git a/evil.txt b/evil.txt",
        "@@ -1,1 +1,1 @@",
        "+++ b/evil.txt",
        "--- a/evil.txt",
        "deleted file mode 100644",
        "Binary files a/evil.txt and b/evil.txt differ",
        "index 0000000..1111111 100644",
        "0123456789abcdef0123456789abcdef0123456789abcdef",
        "\\ No newline at end of file",
    ]

    def test_added_lines_that_look_like_diff_headers_are_kept_verbatim(self):
        self.init_repo()
        content = "\n".join(self.HOSTILE_LINES) + "\n"
        sha = self.commit("m", {"hostile.txt": content})

        units, skipped = self.resolve()

        self.assertEqual(skipped, [])
        unit = self.by_key(units)[(sha, "hostile.txt")]
        self.assertEqual(self.body(unit), content)
        self.assertEqual(
            unit["line_numbers"], list(range(1, len(self.HOSTILE_LINES) + 1))
        )
        self.assertEqual(self.keys(units), [(sha, MSG), (sha, "hostile.txt")])

    def test_message_that_looks_like_diff_headers_stays_one_message(self):
        self.init_repo()
        message = "subject\n\n" + "\n".join(self.HOSTILE_LINES)
        sha = self.commit(message, {"a.txt": "one\n"})

        units, skipped = self.resolve()

        self.assertEqual(skipped, [])
        self.assertEqual(self.body(self.by_key(units)[(sha, MSG)]), message + "\n")
        self.assertEqual(self.keys(units), [(sha, MSG), (sha, "a.txt")])

    def test_content_containing_a_commit_like_line_does_not_split_units(self):
        self.init_repo()
        fake = "a" * 40
        sha = self.commit(f"subject {fake}\n\n{fake}", {"a.txt": f"{fake}\n{fake}\n"})

        units, _ = self.resolve()

        self.assertEqual(self.keys(units), [(sha, MSG), (sha, "a.txt")])
        self.assertEqual(
            self.body(self.by_key(units)[(sha, "a.txt")]), f"{fake}\n{fake}\n"
        )

    def test_the_delimiter_is_generated_for_each_run(self):
        self.init_repo()
        self.commit("m", {"a.txt": "one\n"})
        seen = []
        real = history.secrets.token_hex

        def spy(n):
            value = real(n)
            seen.append(value)
            return value

        with mock.patch.object(history.secrets, "token_hex", spy):
            self.resolve()
            self.resolve()

        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        self.assertEqual(len(seen[0]), 32)

    def test_a_message_that_contains_the_delimiter_is_the_documented_limit(self):
        # 区切りは実行ごとの乱数で、内容と衝突しない前提。予測できる値に固定すれば衝突でき、
        # 履歴が壊れて検査を実行できなかったことになる（黙って取りこぼさない）
        self.init_repo()
        delimiter = "b" * 32
        self.commit(f"subject\n\n{delimiter}not-a-commit-id", {"a.txt": "one\n"})

        with mock.patch.object(history.secrets, "token_hex", lambda n: delimiter):
            with self.assertRaises(TargetError):
                self.resolve()


class GitConfigIndependenceTest(HistoryTestCase):
    def test_user_configuration_does_not_change_the_result(self):
        self.init_repo()
        for key, value in (
            ("diff.noprefix", "true"),
            ("diff.mnemonicPrefix", "true"),
            ("color.ui", "always"),
            ("log.showSignature", "true"),
            ("diff.renames", "copies"),
            ("diff.context", "5"),
            ("core.quotepath", "true"),
        ):
            self.git("config", key, value)
        self.commit("base", {"old name.txt": "one\ntwo\nthree\n"})
        self.git("mv", "old name.txt", "new name.txt")
        sha = self.commit(
            "rename and edit", {"new name.txt": "one\nTWO_EDITED\nthree\n"}
        )

        units, skipped = self.resolve()

        # リネームの検出は切っている（設定の ``diff.renames`` に依らない）。新しいパスの全行が追加行で、
        # 古いパスは削除として記録される
        self.assertEqual(
            skipped, [{"label": "old name.txt", "commit": sha, "reason": "deleted"}]
        )
        unit = self.by_key(units)[(sha, "new name.txt")]
        self.assertEqual(self.body(unit), "one\nTWO_EDITED\nthree\n")
        self.assertEqual(unit["line_numbers"], [1, 2, 3])

    def test_root_commit_is_inspected_even_when_log_show_root_is_false(self):
        # log.showRoot=false だと、git log -p は最初の commit（root commit）の diff を出さない。
        # 追加行が黙って欠落しないよう、--root で固定している
        self.init_repo()
        self.git("config", "log.showRoot", "false")
        root_sha = self.commit("root commit", {"first.txt": "root-line\n"})
        second_sha = self.commit("second commit", {"second.txt": "second-line\n"})

        units, skipped = self.resolve()

        keyed = self.by_key(units)
        self.assertEqual(self.body(keyed[(root_sha, "first.txt")]), "root-line\n")
        self.assertEqual(self.body(keyed[(second_sha, "second.txt")]), "second-line\n")
        self.assertEqual(skipped, [])

    def test_renamed_file_is_inspected_as_a_whole_at_the_new_path(self):
        # リネームの検出を切っているため、内容が同じでも、新しいパスの全行を検査する（取りこぼさない側）
        self.init_repo()
        self.commit("base", {"before.txt": "one\ntwo\n"})
        self.git("mv", "before.txt", "after.txt")
        sha = self.commit("rename only")

        units, skipped = self.resolve()

        self.assertEqual(
            self.body(self.by_key(units)[(sha, "after.txt")]), "one\ntwo\n"
        )
        self.assertEqual(
            skipped, [{"label": "before.txt", "commit": sha, "reason": "deleted"}]
        )


class UndecidedBehaviorTest(HistoryTestCase):
    """判断待ち（TASK-007 の報告）。対処の方針は本体が決めるため、現状の挙動をそのまま固定する。

    ここで固定するのは、変更しない前提の仕様ではなく、観測した現状である。本体の対処を入れるときは、
    これらのテストを新しい挙動に合わせて書き換える。
    """

    def test_pending_history_that_is_not_utf8_cannot_be_inspected(self):
        # 判断待ち: ``git_targets.run_git`` が ``text=True`` のため、UTF-8 でない追加行（例: Latin-1）を
        # 含む履歴は UnicodeDecodeError となり、TargetError（検査を実行できなかった）になる
        self.init_repo()
        self.commit("m", {"latin1.txt": b"caf\xe9 PLACEHOLDER\n"})

        with self.assertRaises(TargetError) as ctx:
            self.resolve()

        self.assertIn("UTF-8", str(ctx.exception))
        self.assertNotIn("PLACEHOLDER", str(ctx.exception))

    def test_non_utf8_content_makes_the_whole_range_fail_not_only_its_own_file(self):
        # 判断待ち: 1 件の UTF-8 でない内容が、他のファイル・他の commit の検査も不能にする
        self.init_repo()
        self.commit("ok", {"ok.txt": "fine\n"})
        self.commit("latin1", {"latin1.txt": b"\xe9\xe8\n"})

        with self.assertRaises(TargetError):
            self.resolve()

    def test_lone_cr_in_an_added_line_drops_the_text_after_the_cr(self):
        # 判断待ち: ``text=True`` の改行変換で、本文の単独の CR が改行として扱われる。
        # CR より後ろの部分は ``+`` で始まらない別の行になり、追加行として拾われず検査単位から落ちる
        self.init_repo()
        sha = self.commit("m", {"a.txt": b"FIRST_PART\rSECOND_PART\nTHIRD_LINE\n"})

        units, _ = self.resolve()

        unit = self.by_key(units)[(sha, "a.txt")]
        self.assertEqual(self.body(unit), "FIRST_PART\nTHIRD_LINE\n")
        self.assertNotIn("SECOND_PART", self.body(unit))
        # 元のファイルの行は 2 行（THIRD_LINE は 2 行目）。落ちた部分は数えないため、番号は偶然一致する
        self.assertEqual(unit["line_numbers"], [1, 2])

    def test_lone_cr_followed_by_a_plus_shifts_the_following_line_numbers(self):
        # 判断待ち: CR の直後が ``+`` のとき、後ろの部分が別の追加行として数えられ、後続の行番号がずれる
        self.init_repo()
        sha = self.commit("m", {"a.txt": b"FIRST_PART\r+SECOND_PART\nTHIRD_LINE\n"})

        units, _ = self.resolve()

        unit = self.by_key(units)[(sha, "a.txt")]
        self.assertEqual(self.body(unit), "FIRST_PART\nSECOND_PART\nTHIRD_LINE\n")
        # 元のファイルでは THIRD_LINE は 2 行目。本体は 3 を返す（ずれ）
        self.assertEqual(unit["line_numbers"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
