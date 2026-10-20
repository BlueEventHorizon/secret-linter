#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lists.py（リスト管理: 読む）と同梱の許可リストのユニットテスト。

REQ-032 FNC-3004 / DES-087 L-1 に対応する。検証項目:
受理する書式（コメント・空行・引用符・BOM・CRLF）/ 形式不正の行番号と理由 /
形式不正のエラー出力にリストの項目の値が含まれないこと /
ファイル不在は空のリスト / 同梱の許可リストが読めること。

fixture はすべて本ファイル内の文字列定数で持ち、一時ディレクトリへ書き出す。
入力値は明白なプレースホルダ（`item-one` / `example.com` 等）だけを使う。
実際のホームの `~/.project-secret.yaml` は読まない・書かない
（ホームは常に一時ディレクトリを明示して渡す）。

実行:
  python3 -m unittest tests.forge.secret-linter.test_lists -v
"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts" / "lists.py"
)

_spec = importlib.util.spec_from_file_location("secret_linter_lists", _SCRIPT_PATH)
lists = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lists)

ListFormatError = lists.ListFormatError

#: 形式不正の行に載せる、エラー出力に出てはならない目印の値
LEAK_MARKER = "LEAK-MARKER-PLACEHOLDER"

NAME = "test-list.yaml"


def parse(text):
    return lists.parse_list(text, NAME)


def error_of(text):
    """text を解析して ListFormatError を返す（出なければテスト失敗）。"""
    try:
        parse(text)
    except ListFormatError as exc:
        return exc
    raise AssertionError("ListFormatError が発生しなかった")


class TestAcceptedFormats(unittest.TestCase):
    """受理する書式。"""

    def test_plain_items(self):
        self.assertEqual(parse("- item-one\n- item-two\n"), ["item-one", "item-two"])

    def test_empty_text_is_empty_list(self):
        self.assertEqual(parse(""), [])

    def test_comments_and_blank_lines_only(self):
        self.assertEqual(parse("# comment\n\n   \n# another\n"), [])

    def test_comment_and_blank_lines_between_items(self):
        # インデントされたコメント行も、strip 後に `#` で始まれば無視される
        text = "# head\n- item-one\n\n  # indented comment\n- item-two\n"
        self.assertEqual(parse(text), ["item-one", "item-two"])

    def test_hash_without_preceding_space_is_part_of_value(self):
        self.assertEqual(parse("- item#one\n"), ["item#one"])

    def test_double_quoted(self):
        self.assertEqual(parse('- "item-one"\n'), ["item-one"])

    def test_single_quoted(self):
        self.assertEqual(parse("- 'item-one'\n"), ["item-one"])

    def test_quoted_keeps_hash_and_colon_inside(self):
        self.assertEqual(
            parse('- "a # b"\n- "key: value"\n'), ["a # b", "key: value"]
        )

    def test_quoted_does_not_interpret_escapes(self):
        # 引用符の中は、エスケープを解釈せずそのまま値とする
        self.assertEqual(parse(r'- "a\nb"' + "\n"), [r"a\nb"])

    def test_quoted_wildcard_domain(self):
        self.assertEqual(parse('- "*.example.com"\n'), ["*.example.com"])

    def test_nonascii_item(self):
        self.assertEqual(parse("- サンプル商事\n"), ["サンプル商事"])

    def test_consistent_indent_is_accepted(self):
        self.assertEqual(parse("  - item-one\n  - item-two\n"), ["item-one", "item-two"])

    def test_no_trailing_newline(self):
        self.assertEqual(parse("- item-one"), ["item-one"])

    def test_crlf(self):
        self.assertEqual(parse("- item-one\r\n- item-two\r\n"), ["item-one", "item-two"])

    def test_bom(self):
        self.assertEqual(parse("﻿- item-one\n"), ["item-one"])

    def test_bom_and_crlf_and_comments(self):
        text = "﻿# comment\r\n\r\n- item-one\r\n- \"item-two\"\r\n"
        self.assertEqual(parse(text), ["item-one", "item-two"])

    def test_duplicates_are_kept_by_parser(self):
        # 解析は項目を読んで返すだけで、重複の除去はしない
        self.assertEqual(parse("- a\n- a\n"), ["a", "a"])


class TestFormatErrors(unittest.TestCase):
    """形式不正は行番号と理由を持つ。"""

    def assert_error_at(self, text, lineno):
        exc = error_of(text)
        self.assertEqual(exc.lineno, lineno)
        self.assertEqual(exc.display_name, NAME)
        self.assertTrue(exc.reason)
        self.assertIn(f"{lineno} 行目", str(exc))
        self.assertIn(NAME, str(exc))
        return exc

    def test_nested_list(self):
        exc = self.assert_error_at("- - item-one\n", 1)
        self.assertIn("入れ子", exc.reason)

    def test_nested_by_indent(self):
        exc = self.assert_error_at("- item-one\n  - item-two\n", 2)
        self.assertIn("インデント", exc.reason)

    def test_inconsistent_indent_reports_the_offending_line(self):
        self.assert_error_at("# c\n\n  - a\n- b\n", 4)

    def test_tab_indent(self):
        exc = self.assert_error_at("\t- item-one\n", 1)
        self.assertIn("タブ", exc.reason)

    def test_empty_dash_item(self):
        self.assert_error_at("- item-one\n-\n", 2)

    def test_dash_with_only_comment(self):
        self.assert_error_at("- # comment\n", 1)

    def test_empty_quoted_item(self):
        exc = self.assert_error_at('- ""\n', 1)
        self.assertIn("空の項目", exc.reason)

    def test_empty_single_quoted_item(self):
        self.assert_error_at("- ''\n", 1)

    def test_unclosed_quote(self):
        exc = self.assert_error_at('- "item-one\n', 1)
        self.assertIn("閉じていません", exc.reason)

    def test_text_after_closing_quote(self):
        self.assert_error_at('- "item-one" trailing\n', 1)

    def test_key_value_line(self):
        exc = self.assert_error_at("key: value\n", 1)
        self.assertIn("リスト項目", exc.reason)

    def test_key_value_as_item(self):
        exc = self.assert_error_at("- key: value\n", 1)
        self.assertIn("mapping", exc.reason)

    def test_key_with_colon_end_as_item(self):
        self.assert_error_at("- key:\n", 1)

    def test_multiline_continuation(self):
        self.assert_error_at("- item-one\n  continued line\n", 2)

    def test_document_separator(self):
        self.assert_error_at("---\n- item-one\n", 1)

    def test_anchor(self):
        exc = self.assert_error_at("- &anchor item-one\n", 1)
        self.assertIn("アンカー", exc.reason)

    def test_tag(self):
        exc = self.assert_error_at("- !!str item-one\n", 1)
        self.assertIn("タグ", exc.reason)

    def test_literal_block(self):
        exc = self.assert_error_at("- |\n", 1)
        self.assertIn("複数行", exc.reason)

    def test_folded_block(self):
        exc = self.assert_error_at("- >\n", 1)
        self.assertIn("複数行", exc.reason)

    def test_flow_sequence(self):
        exc = self.assert_error_at("- [a, b]\n", 1)
        self.assertIn("flow style", exc.reason)

    def test_flow_mapping(self):
        exc = self.assert_error_at("- {a: b}\n", 1)
        self.assertIn("flow style", exc.reason)

    def test_unquoted_wildcard_domain_item_is_accepted(self):
        # 許可リストの記法 `*.` で始まる項目は、引用符なしでも項目として受理する。
        self.assertEqual(parse("- *.example.com\n"), ["*.example.com"])

    def test_inline_comment_after_unquoted_item_is_error(self):
        exc = self.assert_error_at("- item-one\n- item-two  # note\n", 2)
        self.assertIn("行内コメント", exc.reason)

    def test_inline_comment_after_wildcard_item_is_error(self):
        exc = self.assert_error_at("- *.example.com  # comment\n", 1)
        self.assertIn("行内コメント", exc.reason)

    def test_inline_comment_after_quoted_item_is_error(self):
        self.assert_error_at('- "item-one"  # note\n', 1)

    def test_unquoted_item_starting_with_asterisk_other_than_wildcard_is_alias_error(self):
        # `*.` で始まらない `*` の項目は、YAML のエイリアスとして形式不正にする。
        exc = self.assert_error_at("- *alias\n", 1)
        self.assertIn("エイリアス", exc.reason)

    def test_line_number_counts_comments_blank_lines_and_bom_line(self):
        text = "﻿# comment\n\n- ok\n# c\nkey: value\n"
        self.assert_error_at(text, 5)

    def test_line_number_with_crlf(self):
        self.assert_error_at("- ok\r\n\r\nkey: value\r\n", 3)

    def test_first_error_is_reported(self):
        self.assert_error_at("- ok\nkey: v\n- &a b\n", 2)


class TestErrorOmitsValues(unittest.TestCase):
    """形式不正のエラー出力に、リストの項目の値が含まれない。"""

    #: 形式不正の行（値の目印を含む）。種類ごとに 1 つ
    BAD_LINES = {
        "nested": f"- - {LEAK_MARKER}\n",
        "key_value": f"- {LEAK_MARKER}: {LEAK_MARKER}\n",
        "key_value_line": f"{LEAK_MARKER}: {LEAK_MARKER}\n",
        "anchor": f"- &{LEAK_MARKER} {LEAK_MARKER}\n",
        "alias": f"- *{LEAK_MARKER}\n",
        "tag": f"- !{LEAK_MARKER} {LEAK_MARKER}\n",
        "flow": f"- [{LEAK_MARKER}]\n",
        "unclosed_quote": f'- "{LEAK_MARKER}\n',
        "trailing_after_quote": f'- "{LEAK_MARKER}" {LEAK_MARKER}\n',
        "continuation": f"- ok\n  {LEAK_MARKER}\n",
        "indent": f"- ok\n  - {LEAK_MARKER}\n",
        "tab": f"\t- {LEAK_MARKER}\n",
        "document_separator": f"--- {LEAK_MARKER}\n",
    }

    def test_message_and_fields_do_not_contain_the_value(self):
        for name, text in self.BAD_LINES.items():
            with self.subTest(name):
                exc = error_of(text)
                self.assertNotIn(LEAK_MARKER, str(exc))
                self.assertNotIn(LEAK_MARKER, exc.display_name)
                self.assertNotIn(LEAK_MARKER, exc.reason)
                self.assertNotIn(LEAK_MARKER, repr(exc))
                self.assertNotIn(LEAK_MARKER, repr(exc.args))

    def test_value_is_not_in_the_exception_chain(self):
        # `from None` 等で、内容を含む例外を連鎖させていない
        exc = error_of(f"- &{LEAK_MARKER} x\n")
        self.assertIsNone(exc.__cause__)
        self.assertIsNone(exc.__context__)

    def test_file_error_does_not_contain_the_value_or_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_text(f"- ok\n- {LEAK_MARKER}: x\n", encoding="utf-8")
            with self.assertRaises(ListFormatError) as ctx:
                lists.load_list(path, "list.yaml")
            message = str(ctx.exception)
            self.assertNotIn(LEAK_MARKER, message)
            self.assertNotIn(tmp, message)
            self.assertIn("2 行目", message)
            self.assertIn("list.yaml", message)

    def test_load_secret_names_error_shows_display_name_only(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".project-secret.yaml").write_text(
                f"{LEAK_MARKER}: x\n", encoding="utf-8"
            )
            with self.assertRaises(ListFormatError) as ctx:
                lists.load_secret_names(home)
            message = str(ctx.exception)
            self.assertNotIn(LEAK_MARKER, message)
            self.assertNotIn(home, message)
            self.assertIn("~/.project-secret.yaml", message)
            self.assertIn("1 行目", message)

    def test_load_project_allowlist_error_shows_display_name_only(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / ".forge" / "secret_allowlist.yaml"
            target.parent.mkdir()
            target.write_text(f"- ok\n- [{LEAK_MARKER}]\n", encoding="utf-8")
            with self.assertRaises(ListFormatError) as ctx:
                lists.load_project_allowlist(root)
            message = str(ctx.exception)
            self.assertNotIn(LEAK_MARKER, message)
            self.assertNotIn(root, message)
            self.assertIn(".forge/secret_allowlist.yaml", message)
            self.assertIn("2 行目", message)


class TestLoadList(unittest.TestCase):
    """ファイルを読む入口。"""

    def test_missing_file_is_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(lists.load_list(Path(tmp) / "none.yaml", "none.yaml"), [])

    def test_reads_utf8_with_bom_and_crlf_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_bytes(
                "﻿# コメント\r\n- item-one\r\n- 'item-two'\r\n".encode("utf-8")
            )
            self.assertEqual(lists.load_list(path, "list.yaml"), ["item-one", "item-two"])

    def test_accepts_str_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_text("- item-one\n", encoding="utf-8")
            self.assertEqual(lists.load_list(str(path), "list.yaml"), ["item-one"])

    def test_non_utf8_is_format_error_without_lineno(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.write_bytes(b"- \xff\xfe\xfa\n")
            with self.assertRaises(ListFormatError) as ctx:
                lists.load_list(path, "list.yaml")
            self.assertIsNone(ctx.exception.lineno)
            self.assertIn("list.yaml", str(ctx.exception))
            self.assertNotIn(tmp, str(ctx.exception))

    def test_directory_is_format_error_with_exception_type_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.yaml"
            path.mkdir()
            with self.assertRaises(ListFormatError) as ctx:
                lists.load_list(path, "list.yaml")
            self.assertIsNone(ctx.exception.lineno)
            self.assertIn("IsADirectoryError", str(ctx.exception))
            self.assertNotIn(tmp, str(ctx.exception))


class TestHomeAndProjectFiles(unittest.TestCase):
    """ホームのファイルとプロジェクトのファイル。ホームは一時ディレクトリに置き換える。"""

    def test_secret_names_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(lists.load_secret_names(home), [])
            # 読むだけで作らない
            self.assertFalse((Path(home) / ".project-secret.yaml").exists())

    def test_secret_names_read_from_given_home(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".project-secret.yaml").write_text(
                "# names\n- name-one\n- \"name two\"\n", encoding="utf-8"
            )
            self.assertEqual(lists.load_secret_names(home), ["name-one", "name two"])

    def test_secret_names_default_home_is_resolved_from_environment(self):
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".project-secret.yaml").write_text(
                "- name-one\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
                self.assertEqual(lists.load_secret_names(), ["name-one"])

    def test_explicit_home_does_not_touch_real_home(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(
                Path, "home", side_effect=AssertionError("実際のホームを参照した")
            ):
                self.assertEqual(lists.load_secret_names(home), [])

    def test_project_allowlist_missing_is_empty(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(lists.load_project_allowlist(root), [])
            # .forge/ も作らない
            self.assertFalse((Path(root) / ".forge").exists())

    def test_project_allowlist_missing_even_if_forge_dir_exists(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / ".forge").mkdir()
            self.assertEqual(lists.load_project_allowlist(root), [])

    def test_project_allowlist_read(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / ".forge" / "secret_allowlist.yaml"
            target.parent.mkdir()
            target.write_text(
                '- docs.example.com\n- "*.example.org"\n- /docs/path\n',
                encoding="utf-8",
            )
            self.assertEqual(
                lists.load_project_allowlist(root),
                ["docs.example.com", "*.example.org", "/docs/path"],
            )

    def test_load_all_with_no_project_or_home_files(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            result = lists.load_all(root, home)
            self.assertEqual(result.project_allowlist, [])
            self.assertEqual(result.secret_names, [])
            self.assertTrue(result.bundled_allowlist.full_domains)
            self.assertTrue(result.bundled_allowlist.host_only_domains)

    def test_load_all_reads_each_list_from_its_own_place(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            target = Path(root) / ".forge" / "secret_allowlist.yaml"
            target.parent.mkdir()
            target.write_text("- project-item\n", encoding="utf-8")
            (Path(home) / ".project-secret.yaml").write_text(
                "- secret-item\n", encoding="utf-8"
            )
            result = lists.load_all(root, home)
            self.assertEqual(result.project_allowlist, ["project-item"])
            self.assertEqual(result.secret_names, ["secret-item"])

    def test_load_all_propagates_format_error(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            (Path(home) / ".project-secret.yaml").write_text(
                f"{LEAK_MARKER}: x\n", encoding="utf-8"
            )
            with self.assertRaises(ListFormatError) as ctx:
                lists.load_all(root, home)
            self.assertNotIn(LEAK_MARKER, str(ctx.exception))


class TestBundledAllowlist(unittest.TestCase):
    """同梱の許可リスト（件数は固定しない）。"""

    @classmethod
    def setUpClass(cls):
        cls.bundled = lists.load_bundled_allowlist()

    def test_both_kinds_are_not_empty(self):
        self.assertTrue(self.bundled.full_domains)
        self.assertTrue(self.bundled.host_only_domains)
        self.assertTrue(self.bundled.package_prefixes)

    def test_two_kinds_come_from_different_files(self):
        self.assertNotEqual(lists.BUNDLED_FULL_FILENAME, lists.BUNDLED_HOST_ONLY_FILENAME)
        assets = _SCRIPT_PATH.parent.parent / "assets"
        self.assertEqual(
            self.bundled.full_domains,
            lists.load_list(assets / lists.BUNDLED_FULL_FILENAME, "x"),
        )
        self.assertEqual(
            self.bundled.host_only_domains,
            lists.load_list(assets / lists.BUNDLED_HOST_ONLY_FILENAME, "x"),
        )

    def test_two_kinds_are_distinguished(self):
        full = set(self.bundled.full_domains)
        host_only = set(self.bundled.host_only_domains)
        self.assertNotEqual(full, host_only)
        # 同じ項目が両方の種類にあると、一致規則が曖昧になる
        self.assertEqual(full & host_only, set())

    def test_no_duplicates_within_each_kind(self):
        for label, items in (
            ("full", self.bundled.full_domains),
            ("host_only", self.bundled.host_only_domains),
            ("package_prefixes", self.bundled.package_prefixes),
        ):
            with self.subTest(label):
                self.assertEqual(len(items), len(set(items)))

    def test_items_are_nonempty_strings_without_whitespace(self):
        for label, items in (
            ("full", self.bundled.full_domains),
            ("host_only", self.bundled.host_only_domains),
            ("package_prefixes", self.bundled.package_prefixes),
        ):
            for item in items:
                with self.subTest(label=label, item=item):
                    self.assertIsInstance(item, str)
                    self.assertTrue(item)
                    self.assertEqual(item, item.strip())
                    self.assertNotRegex(item, r"\s")

    def test_package_prefixes_come_from_their_own_file(self):
        assets = _SCRIPT_PATH.parent.parent / "assets"
        self.assertEqual(
            self.bundled.package_prefixes,
            lists.load_list(assets / lists.BUNDLED_PACKAGE_PREFIXES_FILENAME, "x"),
        )

    def test_package_prefixes_are_the_decided_set(self):
        expected = {
            "org.apache", "org.eclipse", "org.junit", "org.jetbrains", "org.springframework", "org.slf4j",
            "org.gradle", "org.mozilla", "org.w3c", "org.xml", "com.apple", "com.google", "com.android",
            "com.microsoft", "com.facebook", "com.squareup", "com.sun", "com.oracle", "com.fasterxml",
            "com.amazonaws", "com.jetbrains", "io.flutter", "io.netty", "io.reactivex", "io.grpc",
            "io.micrometer", "io.opentelemetry", "net.java", "net.sf",
        }
        self.assertEqual(set(self.bundled.package_prefixes), expected)
        self.assertEqual(len(self.bundled.package_prefixes), len(expected))

    def test_package_prefixes_exclude_namespaces_owned_by_individuals(self):
        for item in ("io.github", "com.github", "io.gitlab", "me", "dev", "app"):
            self.assertNotIn(item, self.bundled.package_prefixes)

    def test_load_all_returns_the_same_bundled_lists(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
            self.assertEqual(lists.load_all(root, home).bundled_allowlist, self.bundled)


if __name__ == "__main__":
    unittest.main()
