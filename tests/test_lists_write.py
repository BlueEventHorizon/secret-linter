#!/usr/bin/env python3
"""secret-linter の lists.py の書く側（追記。DES-087 L-2・L-3）の単体テスト。

検証すること（テスト表 SC-08・SC-09 のスクリプトの検証を含む）:
- 追記の前後で、既存の項目・コメント・空行・並び・改行コード・BOM が失われない
- ファイルが無いときの新規作成（ディレクトリを含む。機密の名前リストは作成者だけが読める権限）
- 同じ値の重複追記が無い（既存の項目・同じ呼び出しの中）
- リストの書式で表せない値の拒否（値を含まないメッセージ。ファイルが変わらない）
- 形式不正な既存のリストへの追記の拒否（行番号だけ。ファイルが変わらない）
- 追記した結果が parse_list で元の値として再読込できる
- 一時ファイルが残らない。書き込めないとき ListWriteError

ホームとプロジェクトのリストは、テストごとの一時ディレクトリへ置き換える。実際のホームの
~/.project-secret.yaml を読まない・書かない。入力は明白なプレースホルダだけを使う。

実行:
  python3 -m unittest tests.forge.secret-linter.test_lists_write -v
"""

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

import lists  # noqa: E402

VALUE_A = "docs.example.com"
VALUE_B = "wiki.example.com"
VALUE_C = "someone@docs.example.com"


class ListsWriteTestCase(unittest.TestCase):
    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        base_path = Path(base.name).resolve()
        self.home = base_path / "home"
        self.home.mkdir()
        self.root = base_path / "project"
        self.root.mkdir()

    def names_file(self):
        return self.home / ".project-secret.yaml"

    def allow_file(self):
        return self.root / ".forge" / "secret_allowlist.yaml"

    def leftovers(self, directory):
        return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


class AppendTest(ListsWriteTestCase):
    def test_appends_to_secret_names_and_reads_back(self):
        count = lists.append_secret_names([VALUE_A, VALUE_B], home=self.home)
        self.assertEqual(count, 2)
        self.assertEqual(lists.load_secret_names(self.home), [VALUE_A, VALUE_B])
        self.assertEqual(self.names_file().read_text(encoding="utf-8"), f'- "{VALUE_A}"\n- "{VALUE_B}"\n')

    def test_appends_to_project_allowlist_and_reads_back(self):
        self.assertEqual(lists.append_project_allowlist(self.root, [VALUE_A]), 1)
        self.assertEqual(lists.load_project_allowlist(self.root), [VALUE_A])

    def test_existing_content_is_kept_and_item_added_at_end(self):
        original = f"# 先頭のコメント\n\n- first-item\n# 途中のコメント\n- '{VALUE_B}'\n\n# 末尾のコメント\n"
        self.names_file().write_text(original, encoding="utf-8")

        lists.append_secret_names([VALUE_A], home=self.home)

        text = self.names_file().read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original))
        self.assertEqual(text, original + f'- "{VALUE_A}"\n')
        self.assertEqual(lists.load_secret_names(self.home), ["first-item", VALUE_B, VALUE_A])

    def test_existing_indent_is_followed(self):
        self.names_file().write_text("# c\n  - one\n  - two\n", encoding="utf-8")
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertEqual(
            self.names_file().read_text(encoding="utf-8"), f'# c\n  - one\n  - two\n  - "{VALUE_A}"\n'
        )
        self.assertEqual(lists.load_secret_names(self.home), ["one", "two", VALUE_A])

    def test_file_without_trailing_newline(self):
        self.names_file().write_text("- one", encoding="utf-8")
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertEqual(self.names_file().read_text(encoding="utf-8"), f'- one\n- "{VALUE_A}"\n')

    def test_crlf_and_bom_are_kept(self):
        original = "﻿# comment\r\n- one\r\n"
        self.names_file().write_bytes(original.encode("utf-8"))
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertEqual(
            self.names_file().read_bytes(), (original + f'- "{VALUE_A}"\r\n').encode("utf-8")
        )
        self.assertEqual(lists.load_secret_names(self.home), ["one", VALUE_A])

    def test_comment_only_file_gets_item_at_indent_zero(self):
        self.names_file().write_text("# only a comment\n", encoding="utf-8")
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertEqual(lists.load_secret_names(self.home), [VALUE_A])

    def test_value_with_double_quote_uses_single_quotes(self):
        value = 'say "hi" ' + VALUE_A
        lists.append_secret_names([value], home=self.home)
        self.assertEqual(self.names_file().read_text(encoding="utf-8"), f"- '{value}'\n")
        self.assertEqual(lists.load_secret_names(self.home), [value])

    def test_values_that_look_like_other_syntax_round_trip(self):
        values = ["# not a comment", "*.example.invalid", "key: value", "- nested", "[flow]", "a #b", "  padded  "]
        lists.append_secret_names(values, home=self.home)
        self.assertEqual(lists.load_secret_names(self.home), values)

    def test_non_ascii_value_round_trips(self):
        value = "山田 太郎"
        lists.append_secret_names([value], home=self.home)
        self.assertEqual(lists.load_secret_names(self.home), [value])


class DuplicateTest(ListsWriteTestCase):
    def test_existing_item_is_not_appended_again(self):
        original = f"- {VALUE_A}\n"
        self.names_file().write_text(original, encoding="utf-8")
        count = lists.append_secret_names([VALUE_A, VALUE_B], home=self.home)
        self.assertEqual(count, 1)
        self.assertEqual(lists.load_secret_names(self.home), [VALUE_A, VALUE_B])

    def test_all_duplicates_leave_the_file_untouched(self):
        original = f"# c\n- {VALUE_A}\n"
        self.names_file().write_text(original, encoding="utf-8")
        before = self.names_file().stat()
        self.assertEqual(lists.append_secret_names([VALUE_A], home=self.home), 0)
        self.assertEqual(self.names_file().read_text(encoding="utf-8"), original)
        self.assertEqual(self.names_file().stat().st_mtime_ns, before.st_mtime_ns)

    def test_duplicates_inside_one_call_are_appended_once(self):
        self.assertEqual(lists.append_secret_names([VALUE_A, VALUE_A], home=self.home), 1)
        self.assertEqual(lists.load_secret_names(self.home), [VALUE_A])

    def test_value_is_appended_once_after_two_calls(self):
        lists.append_secret_names([VALUE_A], home=self.home)
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertEqual(lists.load_secret_names(self.home), [VALUE_A])

    def test_no_values_does_not_create_the_file(self):
        self.assertEqual(lists.append_secret_names([], home=self.home), 0)
        self.assertFalse(self.names_file().exists())


class CreateTest(ListsWriteTestCase):
    def test_missing_secret_names_file_is_created_with_private_mode(self):
        # SC-09: ~/.project-secret.yaml が無い間は空として扱い、最初の追記で作成する
        self.assertFalse(self.names_file().exists())
        self.assertEqual(lists.load_secret_names(self.home), [])
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertTrue(self.names_file().exists())
        self.assertEqual(stat.S_IMODE(self.names_file().stat().st_mode), 0o600)

    def test_missing_project_allowlist_and_directory_are_created(self):
        self.assertFalse((self.root / ".forge").exists())
        lists.append_project_allowlist(self.root, [VALUE_A])
        self.assertEqual(lists.load_project_allowlist(self.root), [VALUE_A])
        self.assertEqual(stat.S_IMODE(self.allow_file().stat().st_mode), 0o644)

    def test_existing_file_mode_is_kept(self):
        self.names_file().write_text("- one\n", encoding="utf-8")
        os.chmod(self.names_file(), 0o640)
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertEqual(stat.S_IMODE(self.names_file().stat().st_mode), 0o640)

    def test_no_temporary_file_is_left(self):
        lists.append_secret_names([VALUE_A], home=self.home)
        lists.append_secret_names([VALUE_B], home=self.home)
        lists.append_project_allowlist(self.root, [VALUE_A])
        self.assertEqual(self.leftovers(self.home), [])
        self.assertEqual(self.leftovers(self.root / ".forge"), [])
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".project-secret.yaml"])

    def test_symlinked_file_is_updated_through_the_link(self):
        real = self.root / "real-list.yaml"
        real.write_text("- one\n", encoding="utf-8")
        os.symlink(real, self.names_file())
        lists.append_secret_names([VALUE_A], home=self.home)
        self.assertTrue(self.names_file().is_symlink())
        self.assertEqual(lists.parse_list(real.read_text(encoding="utf-8"), "x"), ["one", VALUE_A])


class RejectTest(ListsWriteTestCase):
    def test_unrepresentable_values_raise_without_the_value_and_write_nothing(self):
        both_quotes = "a\"b" + "'c"
        cases = {
            "empty": "",
            "newline": "line1\nline2",
            "carriage return": "a\rb",
            "tab": "a\tb",
            "both quotes": both_quotes,
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(lists.ListWriteError) as ctx:
                    lists.append_secret_names([VALUE_A, value], home=self.home)
                for fragment in (value, "line1", "line2"):
                    if fragment:
                        self.assertNotIn(fragment, str(ctx.exception))
                self.assertFalse(self.names_file().exists())

    def test_non_string_value_raises(self):
        with self.assertRaises(lists.ListWriteError):
            lists.append_secret_names([None], home=self.home)
        self.assertFalse(self.names_file().exists())

    def test_invalid_existing_list_raises_with_line_number_only_and_keeps_the_file(self):
        leaked = "SENTINEL-" + "LINE-CONTENT"
        original = f"- ok\nkey: {leaked}\n"
        self.names_file().write_text(original, encoding="utf-8")

        with self.assertRaises(lists.ListFormatError) as ctx:
            lists.append_secret_names([VALUE_A], home=self.home)

        self.assertIn("2 行目", str(ctx.exception))
        self.assertNotIn(leaked, str(ctx.exception))
        self.assertNotIn(VALUE_A, str(ctx.exception))
        self.assertEqual(self.names_file().read_text(encoding="utf-8"), original)
        self.assertEqual(self.leftovers(self.home), [])

    def test_invalid_existing_project_list_raises_and_keeps_the_file(self):
        original = "- ok\n  - nested\n"
        self.allow_file().parent.mkdir()
        self.allow_file().write_text(original, encoding="utf-8")
        with self.assertRaises(lists.ListFormatError):
            lists.append_project_allowlist(self.root, [VALUE_A])
        self.assertEqual(self.allow_file().read_text(encoding="utf-8"), original)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root は書き込み権限の拒否を再現できない")
    def test_unwritable_directory_raises_write_error_without_the_value(self):
        directory = self.root / "locked"
        directory.mkdir()
        os.chmod(directory, 0o500)
        self.addCleanup(os.chmod, directory, 0o700)
        with self.assertRaises(lists.ListWriteError) as ctx:
            lists.append_items(directory / "list.yaml", "list.yaml", [VALUE_A])
        self.assertNotIn(VALUE_A, str(ctx.exception))
        self.assertNotIn(str(self.root), str(ctx.exception))
        self.assertEqual(list(directory.iterdir()), [])

    def test_unreadable_location_raises_list_format_error_without_the_path(self):
        # 読み取れない（親がディレクトリでない等）は、既存の読み込みと同じく ListFormatError
        blocker = self.root / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        with self.assertRaises(lists.ListFormatError) as ctx:
            lists.append_items(blocker / "list.yaml", "list.yaml", [VALUE_A])
        self.assertNotIn(VALUE_A, str(ctx.exception))
        self.assertNotIn(str(self.root), str(ctx.exception))


class CheckRepresentableTest(ListsWriteTestCase):
    def test_representable_values_pass(self):
        for value in (VALUE_A, VALUE_C, "https://" + VALUE_A + "/path?x=1", "a'b", 'a"b'):
            with self.subTest(value=value):
                lists.check_representable(value, "x")

    def test_unrepresentable_values_raise(self):
        for value in ("", "a\nb", "a\"b'c", None):
            with self.subTest(value=value):
                with self.assertRaises(lists.ListWriteError):
                    lists.check_representable(value, "x")


if __name__ == "__main__":
    unittest.main()
