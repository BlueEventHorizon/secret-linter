#!/usr/bin/env python3
"""secret-linter の機密判定の規則（judgment.py。DES-087 J-1）の単体テスト。

検証すること（テスト表 SC-07 のスクリプトの検証を含む）:
- 判定の順序（両方のリストに一致したら fail、予約値は pass、メールアドレスは許可リストの完全一致の項目だけで pass・
  ドメイン項目と ``*.`` 項目では confirm、メールの項目はドメインの候補を通さない）
- 種別と区分の対応（14 種のパターンの kind すべてと AI の 4 種別。語彙外は失敗）
- アプリのパッケージ名（package_name）は fail。予約ドメインを逆にした形（com.example 等で始まるもの）だけ pass
- 文書用の予約値の一覧（ドメインの各形・サブドメイン・末尾・localhost・IP の範囲の内側と外側）
- リストの一致方法（接尾辞・語の境界・部分文字列・大文字小文字・同梱分の 2 種類）
- 全体の区分、決定論

入力は、予約値（example.com 等）・同梱の許可リストにある実在ドメイン・明白なプレースホルダをそのまま書き、
予約値ではない値として扱うテストは vocab.no_reserved() の中で予約値の語を使う。予約値の形で表せない値
（プライベート IP・会社名のヒント・予約値に近い別名）だけ、実行時に組み立てる（vocab.py）。

実行:
  python3 -m unittest tests.forge.secret-linter.test_judgment -v
"""

import sys
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import judgment  # noqa: E402
import lists  # noqa: E402
import patterns  # noqa: E402
import vocab  # noqa: E402

FAIL, CONFIRM, PASS = judgment.FAIL, judgment.CONFIRM, judgment.PASS

#: 予約値の語。予約値ではない値として扱うテストは vocab.no_reserved() の中で使う
DOCS = "docs.example.com"
CORP = "corp.example.com"
OTHER = "docs.example.org"
EMAIL = "someone@docs.example.com"
#: 同梱の許可リストにある実在ドメイン
PYTHON_ROOT = "python.org"
GITHUB_ROOT = "github.com"


def make_lists(secret_names=(), project=(), full=(), host_only=(), package_prefixes=()):
    return lists.Lists(
        bundled_allowlist=lists.BundledAllowlist(list(full), list(host_only), list(package_prefixes)),
        project_allowlist=list(project),
        secret_names=list(secret_names),
    )


def cand(kind, value, cid="P0001"):
    return {"candidate_id": cid, "source": "pattern", "unit_id": "U0001", "line": 1, "kind": kind, "value": value}


def classify(kind, value, **list_args):
    return judgment.classify(cand(kind, value), make_lists(**list_args))


class OrderTest(unittest.TestCase):
    def test_secret_name_wins_over_allowlist(self):
        value = "docs." + CORP
        with vocab.no_reserved():
            cls, reason = classify("domain", value, secret_names=[CORP], project=[value])
        self.assertEqual((cls, reason), (FAIL, judgment.REASON_NAME_LIST))

    def test_secret_name_wins_over_reserved_value(self):
        self.assertEqual(classify("domain", "www.example.com", secret_names=["www.example.com"])[0], FAIL)

    def test_secret_name_wins_over_confirm_kind(self):
        self.assertEqual(classify("high_entropy", "PLACEHOLDERVALUE", secret_names=["placeholdervalue"])[0], FAIL)

    def test_fail_kind_is_fail_even_if_allowlisted_or_reserved(self):
        for kind in sorted(judgment.FAIL_KINDS):
            with self.subTest(kind=kind):
                cls, reason = classify(kind, "localhost", project=["localhost"])
                self.assertEqual((cls, reason), (FAIL, judgment.REASON_FAIL_KIND))

    def test_email_is_confirm_when_not_allowlisted(self):
        with vocab.no_reserved():
            cls, reason = classify("email", EMAIL)
        self.assertEqual((cls, reason), (CONFIRM, judgment.REASON_EMAIL))

    def test_email_is_pass_when_exact_item_is_in_project_allowlist(self):
        with vocab.no_reserved():
            cls, reason = classify("email", EMAIL, project=[EMAIL])
        self.assertEqual((cls, reason), (PASS, judgment.REASON_ALLOW_PROJECT))

    def test_email_is_confirm_with_domain_item_in_project_allowlist(self):
        # ドメインの項目は、そのドメインのメールアドレスを通さない（個人のアドレスを一括で許さない）
        with vocab.no_reserved():
            for item in (DOCS, "example.com"):
                with self.subTest(item=item):
                    self.assertEqual(classify("email", EMAIL, project=[item]), (CONFIRM, judgment.REASON_EMAIL))

    def test_email_is_confirm_with_wildcard_item_in_project_allowlist(self):
        with vocab.no_reserved():
            for item in ("*." + DOCS, "*.example.com"):
                with self.subTest(item=item):
                    self.assertEqual(classify("email", EMAIL, project=[item]), (CONFIRM, judgment.REASON_EMAIL))

    def test_email_item_does_not_pass_other_email(self):
        with vocab.no_reserved():
            cls, reason = classify("email", "other@docs.example.com", project=[EMAIL])
        self.assertEqual((cls, reason), (CONFIRM, judgment.REASON_EMAIL))

    def test_email_item_does_not_pass_domain_candidate(self):
        # 逆の方向は起こさない。メールの項目は、そのドメインの候補を通さない
        with vocab.no_reserved():
            for kind, value in (("domain", DOCS), ("domain", "https://" + DOCS + "/x"), ("internal_host", DOCS)):
                with self.subTest(kind=kind, value=value):
                    self.assertEqual(classify(kind, value, project=[EMAIL]), (CONFIRM, judgment.REASON_CONFIRM_KIND))

    def test_email_allowlist_match_is_case_sensitive(self):
        with vocab.no_reserved():
            for item in (EMAIL.upper(), "Someone@docs.example.com"):
                with self.subTest(item=item):
                    self.assertEqual(classify("email", EMAIL, project=[item]), (CONFIRM, judgment.REASON_EMAIL))

    def test_email_is_confirm_even_if_bundled_domain(self):
        with vocab.no_reserved():
            cls, _ = classify("email", EMAIL, full=[DOCS])
        self.assertEqual(cls, CONFIRM)

    def test_secret_name_wins_over_email_allowlist(self):
        with vocab.no_reserved():
            cls, reason = classify("email", EMAIL, secret_names=[EMAIL], project=[EMAIL])
        self.assertEqual((cls, reason), (FAIL, judgment.REASON_NAME_LIST))

    def test_email_shaped_value_of_another_kind_follows_the_email_rule(self):
        # メールアドレスの規則は値の性質で定まる。AI の候補（person_name 等）の値がメールアドレスの形でも、
        # 完全一致の項目があれば通過し、無ければ要確認にする
        for kind in ("person_name", "company_name", "resource_name", "other_ai"):
            with self.subTest(kind=kind), vocab.no_reserved():
                self.assertEqual(classify(kind, EMAIL, project=[EMAIL]), (PASS, judgment.REASON_ALLOW_PROJECT))
                self.assertEqual(classify(kind, EMAIL, project=[DOCS]), (CONFIRM, judgment.REASON_EMAIL))
                self.assertEqual(classify(kind, EMAIL), (CONFIRM, judgment.REASON_EMAIL))

    def test_email_shaped_reserved_value_of_another_kind_is_pass(self):
        self.assertEqual(classify("person_name", "someone@example.com"), (PASS, judgment.REASON_RESERVED))

    def test_is_email_candidate(self):
        self.assertTrue(judgment.is_email_candidate({"kind": "email", "value": "not-checked"}))
        self.assertTrue(judgment.is_email_candidate({"kind": "other_ai", "value": EMAIL}))
        for value in ("someone", "someone@localhost", "two@@" + DOCS, "a b@" + DOCS):
            with self.subTest(value=value):
                self.assertFalse(judgment.is_email_candidate({"kind": "person_name", "value": value}))

    def test_reserved_email_is_pass(self):
        for value in ("someone@example.com", "x@mail.example.org", "x@corp.test", "x@localhost"):
            with self.subTest(value=value):
                self.assertEqual(classify("email", value), (PASS, judgment.REASON_RESERVED))

    def test_reserved_value_is_pass(self):
        self.assertEqual(classify("domain", "www.example.com"), (PASS, judgment.REASON_RESERVED))

    def test_allowlisted_value_is_pass(self):
        with vocab.no_reserved():
            result = classify("domain", DOCS, project=["*.example.com"])
        self.assertEqual(result, (PASS, judgment.REASON_ALLOW_PROJECT))

    def test_unlisted_confirm_kind_is_confirm(self):
        with vocab.no_reserved():
            result = classify("domain", DOCS)
        self.assertEqual(result, (CONFIRM, judgment.REASON_CONFIRM_KIND))


class KindClassTest(unittest.TestCase):
    #: 計画の 14 種（patterns.KINDS）が、2 つの集合のどちらか 1 つに入ること
    def test_all_pattern_kinds_are_classified(self):
        for kind in patterns.KINDS:
            with self.subTest(kind=kind):
                self.assertIn(kind, judgment.FAIL_KINDS | judgment.CONFIRM_KINDS)
        self.assertFalse(judgment.FAIL_KINDS & judgment.CONFIRM_KINDS)

    def test_fail_kinds(self):
        expected = {
            "known_token", "private_key", "connection_string", "jwt", "secret_assignment", "password_prose",
            "package_name",
        }
        self.assertEqual(judgment.FAIL_KINDS, expected)
        for kind in expected:
            self.assertEqual(classify(kind, "PLACEHOLDER-VALUE")[0], FAIL)

    def test_package_name_is_fail(self):
        for value in (
            vocab.domain("com", "foo", "bar"), vocab.domain("com", "foo", "bar", "dev"),
            vocab.domain("com", "foo", "bar", "MainActivity"),
        ):
            with self.subTest(value=value):
                self.assertEqual(classify("package_name", value), (FAIL, judgment.REASON_FAIL_KIND))

    def test_package_name_in_a_reserved_reverse_domain_is_pass(self):
        for value in (
            "com.example.app", "com.example.app.dev", "com.example.app.MainActivity",
            "net.example.app", "org.example.demo.Main", "COM.Example.App",
        ):
            with self.subTest(value=value):
                self.assertEqual(classify("package_name", value), (PASS, judgment.REASON_RESERVED))

    def test_package_name_close_to_a_reserved_reverse_domain_is_fail(self):
        for value in (
            vocab.domain("com", "examples", "app"), vocab.domain("com", "exampleapp", "main"),
            vocab.domain("io", "example", "app"), vocab.domain("dev", "example", "app"),
            vocab.domain("com", "foo", "example", "app"),
        ):
            with self.subTest(value=value):
                self.assertEqual(classify("package_name", value), (FAIL, judgment.REASON_FAIL_KIND))

    def test_reserved_reverse_domain_does_not_pass_other_fail_kinds(self):
        self.assertEqual(classify("secret_assignment", "com.example.app")[0], FAIL)

    def test_secret_name_wins_over_a_reserved_reverse_domain(self):
        self.assertEqual(classify("package_name", "com.example.app", secret_names=["com.example.app"])[0], FAIL)

    def test_confirm_kinds_when_nothing_matches(self):
        expected = {
            "high_entropy", "company_hint", "obfuscated_host", "domain", "email", "private_ip", "internal_host",
            "person_name", "company_name", "resource_name", "other_ai",
        }
        self.assertEqual(judgment.CONFIRM_KINDS, expected)
        for kind in expected:
            with self.subTest(kind=kind):
                self.assertEqual(classify(kind, "PLACEHOLDER-VALUE")[0], CONFIRM)

    def test_unknown_kind_raises(self):
        with self.assertRaises(judgment.JudgmentError) as ctx:
            classify("not_a_kind", "PLACEHOLDER-VALUE")
        self.assertNotIn("PLACEHOLDER-VALUE", str(ctx.exception))

    def test_non_string_value_raises(self):
        with self.assertRaises(judgment.JudgmentError):
            judgment.classify({"candidate_id": "P0001", "kind": "domain", "value": None}, make_lists())


class ReservedValueTest(unittest.TestCase):
    def test_reserved_domains(self):
        for value in (
            "example.com", "example.net", "example.org", "www.example.com", "a.b.example.org",
            "EXAMPLE.COM", "foo.example", "foo.test", "foo.invalid", "foo.localhost", "localhost",
            "https://api.example.com/v1?x=1", "user@example.net",
        ):
            with self.subTest(value=value):
                self.assertTrue(judgment.is_reserved_value(value))

    def test_non_reserved_domains(self):
        for value in (
            # 予約値ではない別名は、予約値の形では表せないため組み立てる
            vocab.domain("example", "co"), vocab.domain("example", "com", "other", "io"),
            vocab.domain("notexample", "com"), vocab.domain("myexample", "org"), vocab.domain("test", "io"),
            vocab.domain("localhost", "io"), "test", "invalid", "example", PYTHON_ROOT, "PLACEHOLDER-VALUE",
            vocab.legal_en("Foo"),
        ):
            with self.subTest(value=value):
                self.assertFalse(judgment.is_reserved_value(value))

    def test_reserved_ip_inside(self):
        for value in ("192.0.2.0", "192.0.2.255", "198.51.100.7", "203.0.113.200", "2001:db8::1", "2001:db8:ffff::1",
                      "http://203.0.113.5:8080/x", "http://[2001:db8::1]:80/x"):
            with self.subTest(value=value):
                self.assertTrue(judgment.is_reserved_value(value))

    def test_reserved_ip_outside(self):
        for value in ("192.0.3.1", "192.0.1.255", "198.51.101.1", "203.0.114.1", vocab.ip(10, 0, 0, 1),
                      vocab.ip(192, 168, 0, 1), "2001:db9::1", "2001:db7::1"):
            with self.subTest(value=value):
                self.assertFalse(judgment.is_reserved_value(value))

    def test_private_ip_in_reserved_range_passes_and_others_confirm(self):
        self.assertEqual(classify("private_ip", "192.0.2.10")[0], PASS)
        self.assertEqual(classify("private_ip", vocab.ip(10, 0, 0, 1))[0], CONFIRM)


class SecretNameMatchTest(unittest.TestCase):
    def match(self, value, *names):
        return judgment.matches_secret_name(value, list(names))

    def test_domain_item_matches_by_suffix(self):
        self.assertTrue(self.match("secret." + CORP, CORP))
        self.assertTrue(self.match(CORP, CORP))
        self.assertTrue(self.match(f"https://a.{CORP}/path?q=1", CORP))
        self.assertTrue(self.match("user@mail." + CORP, CORP))

    def test_domain_item_does_not_match_partial_labels(self):
        self.assertFalse(self.match("not" + CORP, CORP))
        self.assertFalse(self.match(f"{CORP}.{OTHER}", CORP))

    def test_word_item_matches_on_word_boundary(self):
        self.assertTrue(self.match("corp", "corp"))
        self.assertTrue(self.match("corp-prod", "corp"))
        self.assertTrue(self.match("db_corp_1", "corp"))
        self.assertFalse(self.match("corpprod", "corp"))
        self.assertFalse(self.match("mcorp", "corp"))
        self.assertFalse(self.match("corp2", "corp"))

    def test_non_ascii_item_matches_as_substring(self):
        self.assertTrue(self.match("これは山田商事の件", "山田商事"))
        self.assertFalse(self.match("これは山田の件", "山田商事"))

    def test_email_item_matches_as_substring(self):
        boss = "boss@mail." + CORP
        self.assertTrue(self.match("mailto:" + boss, boss))
        self.assertFalse(self.match("other@mail." + CORP, boss))

    def test_ip_item_matches_as_substring(self):
        item = vocab.ip(10, 1, 2, 3)
        self.assertTrue(self.match(item, item))
        self.assertTrue(self.match(f"http://{vocab.ip(10, 1, 2, 34)}/x", item))
        self.assertFalse(self.match(vocab.ip(10, 1, 2, 4), item))

    def test_case_insensitive(self):
        self.assertTrue(self.match("Secret." + CORP.upper(), CORP))
        self.assertTrue(self.match("CORP-prod", "Corp"))
        self.assertTrue(self.match("Boss@MAIL." + CORP.upper(), "boss@mail." + CORP))

    def test_empty_list_never_matches(self):
        self.assertFalse(self.match("anything"))


class ProjectAllowlistTest(unittest.TestCase):
    def reason(self, value, *items):
        return judgment.allowlist_reason(value, lists.BundledAllowlist([], [], []), list(items))

    def test_wildcard_matches_domain_and_subdomains(self):
        item = "*." + DOCS
        for value in (DOCS, "a." + DOCS, f"https://a.b.{DOCS}/x"):
            with self.subTest(value=value):
                self.assertEqual(self.reason(value, item), judgment.REASON_ALLOW_PROJECT)
        for value in ("x" + DOCS, f"{DOCS}.{OTHER}"):
            with self.subTest(value=value):
                self.assertIsNone(self.reason(value, item))

    def test_path_item_matches_path_and_below(self):
        item = DOCS + "/guide"
        for value in (DOCS + "/guide", f"https://{DOCS}/guide/a/b"):
            with self.subTest(value=value):
                self.assertEqual(self.reason(value, item), judgment.REASON_ALLOW_PROJECT)
        for value in (DOCS + "/guidebook", DOCS, DOCS + "/other"):
            with self.subTest(value=value):
                self.assertIsNone(self.reason(value, item))

    def test_item_with_query_is_exact_match_only(self):
        item = DOCS + "/guide?a=1"
        self.assertEqual(self.reason(DOCS + "/guide?a=1", item), judgment.REASON_ALLOW_PROJECT)
        self.assertIsNone(self.reason(DOCS + "/guide?a=1/x", item))

    def test_other_items_are_exact_match(self):
        self.assertEqual(self.reason(DOCS, DOCS), judgment.REASON_ALLOW_PROJECT)
        self.assertIsNone(self.reason("a." + DOCS, DOCS))
        self.assertIsNone(self.reason(DOCS + "/x", DOCS))

    def test_case_sensitive(self):
        self.assertIsNone(self.reason("DOCS.example.com", DOCS))
        self.assertIsNone(self.reason("A." + DOCS.upper(), "*." + DOCS))
        self.assertIsNone(self.reason("DOCS.example.com/Guide", DOCS + "/guide"))


class BundledAllowlistTest(unittest.TestCase):
    """同梱の 2 種類（パスまで公開 / ホストだけ公開）の一致方法。実際の同梱リストを使う。"""

    @classmethod
    def setUpClass(cls):
        cls.bundled = lists.load_bundled_allowlist()

    def reason(self, value):
        return judgment.allowlist_reason(value, self.bundled, [])

    def test_bundled_lists_are_not_empty_and_contain_the_documented_examples(self):
        self.assertIn(PYTHON_ROOT, self.bundled.full_domains)
        self.assertIn(GITHUB_ROOT, self.bundled.host_only_domains)

    def test_full_domain_matches_by_suffix(self):
        for value in (PYTHON_ROOT, "docs." + PYTHON_ROOT, f"https://docs.{PYTHON_ROOT}/3/library/x.html?q=1"):
            with self.subTest(value=value):
                self.assertEqual(self.reason(value), judgment.REASON_ALLOW_FULL)

    def test_full_domain_does_not_match_lookalikes(self):
        # 同梱の実在ドメインの近い別名は、予約値の形では表せないため組み立てる
        for value in (vocab.domain("notpython", "org"), "python.org.evil.example.org"):
            with self.subTest(value=value):
                self.assertIsNone(self.reason(value))

    def test_host_only_domain_passes_the_host_and_www(self):
        for value in (
            GITHUB_ROOT, "www." + GITHUB_ROOT, f"https://{GITHUB_ROOT}",
            f"https://www.{GITHUB_ROOT}/", f"http://{GITHUB_ROOT}",
        ):
            with self.subTest(value=value):
                self.assertEqual(self.reason(value), judgment.REASON_ALLOW_HOST_ONLY)

    def test_host_only_domain_does_not_pass_path_query_or_subdomain(self):
        for value in (
            f"https://{GITHUB_ROOT}/other-org/secret-repo",
            f"{GITHUB_ROOT}/other-org",
            f"https://{GITHUB_ROOT}?tab=x",
            f"https://{GITHUB_ROOT}/#frag",
            # 同梱の実在ドメインの近い別名は組み立てる
            vocab.domain("other-org", GITHUB_ROOT),
            f"https://www.{vocab.domain('other-org', GITHUB_ROOT)}",
            f"https://sub.www.{GITHUB_ROOT}",
        ):
            with self.subTest(value=value):
                self.assertIsNone(self.reason(value))

    def test_host_only_url_with_path_is_confirm(self):
        lists_ = make_lists(full=self.bundled.full_domains, host_only=self.bundled.host_only_domains)
        for value in (
            f"https://{GITHUB_ROOT}/other-org/secret-repo?x=1",
            vocab.domain("other-org", "slack", "com"),  # 同梱の実在ドメインの近い別名は組み立てる
            "figma.com/file/abc",
        ):
            with self.subTest(value=value):
                cls, reason = judgment.classify(cand("domain", value), lists_)
                self.assertEqual((cls, reason), (CONFIRM, judgment.REASON_CONFIRM_KIND))
        self.assertEqual(judgment.classify(cand("domain", GITHUB_ROOT), lists_)[0], PASS)

    def test_bundled_match_is_case_sensitive(self):
        self.assertIsNone(self.reason(vocab.domain("GitHub", "com")))
        self.assertIsNone(self.reason(vocab.domain("Docs", "Python", "org")))

    def test_bundled_list_does_not_match_ip_or_non_host_values(self):
        self.assertIsNone(self.reason(vocab.ip(10, 0, 0, 1)))
        self.assertIsNone(self.reason("PLACEHOLDER VALUE"))


class PackagePrefixAllowlistTest(unittest.TestCase):
    """公開の開発系パッケージの接頭辞（同梱）と、プロジェクトの ``接頭辞.*`` の項目。実際の同梱リストを使う。"""

    @classmethod
    def setUpClass(cls):
        cls.bundled = lists.load_bundled_allowlist()

    def classify(self, kind, value, secret_names=(), project=()):
        lists_ = lists.Lists(
            bundled_allowlist=self.bundled, project_allowlist=list(project), secret_names=list(secret_names)
        )
        return judgment.classify(cand(kind, value), lists_)

    def test_bundled_prefix_passes_a_package_name(self):
        for value in (
            "org.apache.commons.lang3.StringUtils", "com.apple.Safari.plist",
            "com.google.android.material.Button", "org.apache", "org.apache.Foo",
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.classify("package_name", value), (PASS, judgment.REASON_ALLOW_PACKAGE)
                )

    def test_bundled_prefix_matches_only_at_a_label_boundary(self):
        for value in (
            vocab.domain("org", "apachefoo", "bar", "baz"), vocab.domain("com", "applesauce", "app", "Main"),
            vocab.domain("org", "apach", "foo", "bar"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.classify("package_name", value), (FAIL, judgment.REASON_FAIL_KIND))

    def test_bundled_prefix_is_case_sensitive(self):
        for value in (
            vocab.domain("Org", "Apache", "Foo", "Bar"), vocab.domain("org", "Apache", "Foo", "Bar"),
            vocab.domain("COM", "APPLE", "Safari", "plist"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.classify("package_name", value), (FAIL, judgment.REASON_FAIL_KIND))

    def test_bundled_prefix_does_not_cover_names_owned_by_individuals(self):
        for value in (
            vocab.domain("io", "github", "user", "project"), vocab.domain("com", "github", "user", "project"),
            vocab.domain("net", "http", "client", "Request"), vocab.domain("co", "jp", "company", "service"),
        ):
            with self.subTest(value=value):
                self.assertEqual(self.classify("package_name", value), (FAIL, judgment.REASON_FAIL_KIND))

    def test_bundled_prefix_does_not_pass_other_kinds(self):
        value = "org.apache.commons.lang3"
        self.assertEqual(self.classify("domain", value), (CONFIRM, judgment.REASON_CONFIRM_KIND))
        self.assertEqual(self.classify("secret_assignment", value), (FAIL, judgment.REASON_FAIL_KIND))

    def test_project_prefix_item_passes_a_package_name(self):
        item = vocab.domain("com", "acme") + ".*"
        for value in (
            vocab.domain("com", "acme"), vocab.domain("com", "acme", "app", "Main"),
            vocab.domain("com", "acme", "app"),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.classify("package_name", value, project=[item]), (PASS, judgment.REASON_ALLOW_PROJECT)
                )

    def test_project_prefix_item_matches_only_at_a_label_boundary(self):
        item = vocab.domain("com", "acme") + ".*"
        for value in (vocab.domain("com", "acmex", "app", "Main"), vocab.domain("org", "acme", "app", "Main")):
            with self.subTest(value=value):
                self.assertEqual(
                    self.classify("package_name", value, project=[item]), (FAIL, judgment.REASON_FAIL_KIND)
                )

    def test_project_prefix_item_does_not_pass_other_kinds(self):
        item = vocab.domain("com", "acme") + ".*"
        value = vocab.domain("com", "acme", "app")
        self.assertEqual(self.classify("domain", value, project=[item]), (CONFIRM, judgment.REASON_CONFIRM_KIND))
        self.assertEqual(self.classify("secret_assignment", value, project=[item]), (FAIL, judgment.REASON_FAIL_KIND))

    def test_project_prefix_item_is_not_an_item_of_the_general_allowlist_match(self):
        # 既存の項目の形（`*.`・パス・完全一致）の一致方法（allowlist_reason）には、接頭辞の項目を足さない
        item = vocab.domain("com", "acme") + ".*"
        self.assertIsNone(judgment.allowlist_reason(vocab.domain("com", "acme", "app"), self.bundled, [item]))

    def test_secret_name_wins_over_a_prefix_allowance(self):
        project = [vocab.domain("com", "acme") + ".*"]
        self.assertEqual(
            self.classify("package_name", "org.apache.commons.lang3.StringUtils", secret_names=["commons"]),
            (FAIL, judgment.REASON_NAME_LIST),
        )
        self.assertEqual(
            self.classify(
                "package_name", vocab.domain("com", "acme", "app", "Main"), secret_names=["acme"], project=project
            ),
            (FAIL, judgment.REASON_NAME_LIST),
        )

    def test_reserved_reverse_domain_still_passes_as_reserved(self):
        self.assertEqual(self.classify("package_name", "com.example.app"), (PASS, judgment.REASON_RESERVED))


class OverallClassTest(unittest.TestCase):
    def test_overall(self):
        self.assertEqual(judgment.overall_class([]), PASS)
        self.assertEqual(judgment.overall_class([PASS, PASS]), PASS)
        self.assertEqual(judgment.overall_class([PASS, CONFIRM]), CONFIRM)
        self.assertEqual(judgment.overall_class([CONFIRM, FAIL, PASS]), FAIL)
        self.assertEqual(judgment.overall_class([FAIL]), FAIL)

    def test_one_fail_makes_whole_fail(self):
        candidates = [
            cand("domain", "www.example.com", "P0001"),
            cand("domain", DOCS, "P0002"),
            cand("jwt", "PLACEHOLDER-VALUE", "P0003"),
        ]
        # 予約値ではない値（P0002）と並べるため、P0001 は予約値ではなくプロジェクトの許可リストで通す
        with vocab.no_reserved():
            result = judgment.judge(candidates, make_lists(project=["www.example.com"]))
        self.assertEqual([r["class"] for r in result["candidates"]], [PASS, CONFIRM, FAIL])
        self.assertEqual(result["class"], FAIL)

    def test_no_candidates_is_pass(self):
        self.assertEqual(judgment.judge([], make_lists()), {"candidates": [], "class": PASS})

    def test_reason_never_contains_the_value(self):
        value = "PLACEHOLDER-VALUE-ZZ"
        for kind in sorted(judgment.FAIL_KINDS | judgment.CONFIRM_KINDS):
            result = judgment.judge([cand(kind, value)], make_lists())
            self.assertNotIn(value, repr(result))

    def test_candidate_without_id_raises(self):
        with self.assertRaises(judgment.JudgmentError):
            judgment.judge([{"kind": "domain", "value": "x.test"}], make_lists())


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_judgment(self):
        private_ip = vocab.ip(10, 0, 0, 9)
        candidates = [
            cand("domain", "docs." + PYTHON_ROOT, "P0001"),
            cand("email", "x@example.com", "P0002"),
            cand("company_hint", "サンプル商事", "P0003"),
            cand("private_ip", private_ip, "P0004"),
        ]
        names = ["サンプル", CORP, "corp"]
        lists_ = make_lists(secret_names=names, full=[PYTHON_ROOT], host_only=[GITHUB_ROOT], project=[private_ip])
        first = judgment.judge(candidates, lists_)
        for _ in range(5):
            self.assertEqual(judgment.judge(candidates, lists_), first)


if __name__ == "__main__":
    unittest.main()
