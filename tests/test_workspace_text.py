#!/usr/bin/env python3
"""secret-linter の作業領域の ``write_text``（workspace.py）のユニットテスト。

DES-087 のモジュール表 W-2（書く）の、本文（テキスト）を置く操作の検証に対応する。``write`` と同じ契約を持つ。

- 既存のファイルを置き換えない（上書きの試みは ``WorkspaceError`` で、内容が変わらない）
- 内容が確定してから 1 回の操作で公開する（公開の時点で本文が完全であり、一時ファイルが残らない）
- ディレクトリは 0700、ファイルは作成者だけが読める
- 不正な相対パス・不正な検査 ID・存在しない置き場の拒否
- 本文は UTF-8 で、改行を含めて改変しない

作業領域は OS の一時ディレクトリに作られるため、テストが作った置き場は tearDown で必ず削除する。
入力は明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.forge.secret-linter.test_workspace_text -v
"""

import importlib.util
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts" / "workspace.py"
)

_spec = importlib.util.spec_from_file_location("secret_linter_workspace_text", _SCRIPT_PATH)
workspace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(workspace)

WorkspaceError = workspace.WorkspaceError

#: 本文の代わりに置く、明白なプレースホルダ
TEXT = "EXAMPLE_PLACEHOLDER_VALUE\n"


class WriteTextTestCase(unittest.TestCase):
    def setUp(self):
        self._created = []

    def tearDown(self):
        for inspection_id in self._created:
            shutil.rmtree(workspace.workspace_path(inspection_id), ignore_errors=True)

    def create(self):
        inspection_id = workspace.create()
        self._created.append(inspection_id)
        return inspection_id

    def names(self, inspection_id, rel=""):
        return sorted(p.name for p in (workspace.workspace_path(inspection_id) / rel).iterdir())


class ContentTest(WriteTextTestCase):
    def test_writes_the_text_and_returns_the_published_path(self):
        inspection_id = self.create()

        target = workspace.write_text(inspection_id, "texts/0001.txt", TEXT)

        self.assertEqual(target, workspace.workspace_path(inspection_id) / "texts" / "0001.txt")
        self.assertEqual(target.read_bytes(), TEXT.encode("utf-8"))

    def test_text_is_utf8_and_not_transformed(self):
        inspection_id = self.create()
        text = "日本語の本文 \"quoted\" \\ backslash\n"

        target = workspace.write_text(inspection_id, "t.txt", text)

        self.assertEqual(target.read_bytes(), text.encode("utf-8"))

    def test_newlines_are_kept_as_they_are(self):
        # 本文の改行を変換しない（CRLF・単独の CR・末尾の改行なしも、そのまま）
        inspection_id = self.create()
        text = "crlf\r\nlone\rcr\nno trailing newline"

        target = workspace.write_text(inspection_id, "t.txt", text)

        self.assertEqual(target.read_bytes(), text.encode("utf-8"))

    def test_empty_text_is_written_as_an_empty_file(self):
        inspection_id = self.create()
        target = workspace.write_text(inspection_id, "t.txt", "")
        self.assertEqual(target.read_bytes(), b"")

    def test_different_inspections_do_not_interfere(self):
        first = self.create()
        second = self.create()
        workspace.write_text(first, "t.txt", "first\n")
        workspace.write_text(second, "t.txt", "second\n")
        self.assertEqual((workspace.workspace_path(first) / "t.txt").read_text(encoding="utf-8"), "first\n")
        self.assertEqual((workspace.workspace_path(second) / "t.txt").read_text(encoding="utf-8"), "second\n")


class NoOverwriteTest(WriteTextTestCase):
    def test_does_not_replace_an_existing_file(self):
        inspection_id = self.create()
        target = workspace.write_text(inspection_id, "t.txt", "original\n")

        with self.assertRaises(WorkspaceError):
            workspace.write_text(inspection_id, "t.txt", "replacement\n")

        self.assertEqual(target.read_text(encoding="utf-8"), "original\n")

    def test_does_not_replace_a_file_written_by_write(self):
        inspection_id = self.create()
        target = workspace.write(inspection_id, "units.json", {"owner": "write"})
        before = target.read_bytes()

        with self.assertRaises(WorkspaceError):
            workspace.write_text(inspection_id, "units.json", "text\n")

        self.assertEqual(target.read_bytes(), before)

    def test_write_does_not_replace_a_file_written_by_write_text(self):
        inspection_id = self.create()
        target = workspace.write_text(inspection_id, "t.json", "text\n")

        with self.assertRaises(WorkspaceError):
            workspace.write(inspection_id, "t.json", {"owner": "write"})

        self.assertEqual(target.read_text(encoding="utf-8"), "text\n")

    def test_error_message_does_not_contain_the_text(self):
        inspection_id = self.create()
        workspace.write_text(inspection_id, "t.txt", "first\n")

        with self.assertRaises(WorkspaceError) as ctx:
            workspace.write_text(inspection_id, "t.txt", TEXT)

        self.assertNotIn("EXAMPLE_PLACEHOLDER_VALUE", str(ctx.exception))

    def test_leaves_no_temporary_file_after_success_or_refusal(self):
        inspection_id = self.create()
        workspace.write_text(inspection_id, "texts/0001.txt", TEXT)
        with self.assertRaises(WorkspaceError):
            workspace.write_text(inspection_id, "texts/0001.txt", TEXT)
        self.assertEqual(self.names(inspection_id, "texts"), ["0001.txt"])


class PublishAfterCompletionTest(WriteTextTestCase):
    def test_the_text_is_complete_at_the_moment_it_becomes_visible(self):
        # 公開（``os.link``）の時点で、公開前の一時ファイルに本文が全て書かれており、公開先は未だ無い
        inspection_id = self.create()
        text = "x" * 100_000 + "\n"
        real_link = os.link
        observed = {}

        def spy(src, dst, *args, **kwargs):
            observed["dst_existed"] = Path(dst).exists()
            observed["src_bytes"] = Path(src).read_bytes()
            return real_link(src, dst, *args, **kwargs)

        with mock.patch.object(workspace.os, "link", spy):
            target = workspace.write_text(inspection_id, "t.txt", text)

        self.assertFalse(observed["dst_existed"])
        self.assertEqual(observed["src_bytes"], text.encode("utf-8"))
        self.assertEqual(target.read_bytes(), text.encode("utf-8"))

    def test_a_failure_before_publication_leaves_no_file_behind(self):
        inspection_id = self.create()

        with mock.patch.object(workspace.os, "link", side_effect=OSError("link failed")):
            with self.assertRaises(WorkspaceError):
                workspace.write_text(inspection_id, "texts/0001.txt", TEXT)

        self.assertEqual(self.names(inspection_id, "texts"), [])

    def test_a_failure_while_writing_leaves_no_file_behind(self):
        inspection_id = self.create()

        with mock.patch.object(workspace.os, "fdopen", side_effect=OSError("write failed")):
            with self.assertRaises(WorkspaceError):
                workspace.write_text(inspection_id, "t.txt", TEXT)

        self.assertEqual(self.names(inspection_id), [])


@unittest.skipIf(os.name == "nt", "POSIX のモードを前提とする")
class ModeTest(WriteTextTestCase):
    def test_subdirectories_are_created_with_mode_0700_under_a_permissive_umask(self):
        inspection_id = self.create()
        old = os.umask(0o022)
        try:
            target = workspace.write_text(inspection_id, "a/b/c/t.txt", TEXT)
        finally:
            os.umask(old)

        root = workspace.workspace_path(inspection_id)
        directory = target.parent
        while directory != root:
            self.assertEqual(stat.S_IMODE(os.lstat(directory).st_mode), 0o700, directory)
            directory = directory.parent

    def test_file_is_readable_only_by_the_creator(self):
        inspection_id = self.create()
        old = os.umask(0)
        try:
            target = workspace.write_text(inspection_id, "t.txt", TEXT)
        finally:
            os.umask(old)
        self.assertEqual(os.lstat(target).st_mode & 0o077, 0)


class RejectionTest(WriteTextTestCase):
    def test_rejects_relative_paths_that_leave_the_place(self):
        inspection_id = self.create()
        for rel in ("../outside.txt", "/abs.txt", "", ".", "a/../../b.txt", "a//..//..//b.txt"):
            with self.subTest(rel=rel):
                with self.assertRaises(WorkspaceError):
                    workspace.write_text(inspection_id, rel, TEXT)

    def test_nothing_is_written_outside_the_place_when_rejected(self):
        inspection_id = self.create()
        with tempfile.TemporaryDirectory() as outside:
            escape = os.path.relpath(Path(outside) / "escaped.txt", workspace.workspace_path(inspection_id))
            with self.assertRaises(WorkspaceError):
                workspace.write_text(inspection_id, escape, TEXT)
            self.assertEqual(os.listdir(outside), [])

    def test_rejects_invalid_inspection_ids(self):
        for value in ("", "../etc", "a" * 31, "A" * 32, "g" * 32, None, 123):
            with self.subTest(value=value):
                with self.assertRaises(WorkspaceError):
                    workspace.write_text(value, "t.txt", TEXT)

    def test_fails_when_the_place_does_not_exist(self):
        with self.assertRaises(WorkspaceError):
            workspace.write_text("c" * 32, "t.txt", TEXT)

    def test_fails_when_the_place_is_a_symlink(self):
        inspection_id = "d" * 32
        link = workspace.workspace_path(inspection_id)
        self.addCleanup(lambda: link.unlink() if link.is_symlink() else None)
        with tempfile.TemporaryDirectory() as outside:
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(WorkspaceError):
                workspace.write_text(inspection_id, "t.txt", TEXT)
            self.assertEqual(os.listdir(outside), [])


if __name__ == "__main__":
    unittest.main()
