#!/usr/bin/env python3
"""secret-linter のパターン検出（patterns.py）の単体テスト。

DES-087 のモジュール表 P-1（検出する）と、テスト表 SC-04（各種別の検出・除外・同じ入力で同じ出力）・
SC-15（抑制マーカー付きの件数と位置）のスクリプトの検証に対応する。

- 14 種の ``kind``（``patterns.KINDS``）のそれぞれで、対応する kind の候補が出る
- 抑制マーカー付きの行は候補にならず、件数と位置（値を持たない）が数えられる。マーカー自体は検出されない
- 同じ行・同じ値は最初に一致した規則だけを採り、採用した値の部分文字列も除く
- プレースホルダ・コード式・定数名・パスのキーの代入は候補にならず、理由別の除外件数に数えられる
- hex だけの文字列は high_entropy にならない。40 文字未満は high_entropy にならない
- 行番号（``added_lines`` は元の行番号）・``commit``・``candidate_id`` の形式
- 同じ入力は、何度でも（ハッシュのシードが変わっても）同じ結果になる（決定論）

機密候補の値は、機密らしい値をテストコードの中で連結して組み立てる（リポジトリの機密スキャナが
テストファイル自身を検出しないため）。値は、形式だけを満たす架空の値か、明白なプレースホルダである。
実在の値を一部改変したものは使わない。

実行:
  python3 -m unittest tests.test_patterns -v
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)
_PATTERNS_PATH = _SCRIPTS_DIR / "patterns.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vocab  # noqa: E402

_spec = importlib.util.spec_from_file_location("secret_linter_patterns", _PATTERNS_PATH)
patterns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patterns)

# 形式だけを満たす架空の値。連結して組み立てる（テストファイル自身が検出されないため）
AWS_KEY = "AKIA" + "Z7Q2WXYVBN4KLMPD"
GITHUB_TOKEN = "ghp_" + "9Xk2LmQ7vRt4WsZa8BnCd3EfGh5JiKl6MnOp"
SLACK_TOKEN = "xoxb-" + "2417365981-4Kq7Wm3Rt9Yv"
GOOGLE_KEY = "AIza" + "SyD3kL9mQ2vXt7Rw4Zc8Nb1Hf6Js0Pe5Ugh"
STRIPE_KEY = "sk_live_" + "4Kq7Wm3Rt9YvXz2Bn"
NPM_TOKEN = "npm_" + "K3m9Qv2Xt7Rw4Zc8Nb1Hf6Js0Pe5Ug2Ld4Ab"
KNOWN_TOKENS = {
    "aws_access_key_id": AWS_KEY,
    "github_token": GITHUB_TOKEN,
    "slack_token": SLACK_TOKEN,
    "google_api_key": GOOGLE_KEY,
    "stripe_live_key": STRIPE_KEY,
    "npm_token": NPM_TOKEN,
}
JWT = ".".join(
    [
        "eyJhbGci" + "OiJIUzI1NiJ9",
        "eyJzdWIi" + "OiJ1c2VyIn0",
        "Zq4Lm8Rt" + "2Xv6Kp9Wd3Bn",
    ]
)
PRIVATE_KEY_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
CONNECTION_PASSWORD = "R7k2" + "LmQ9vXt4Ws"
CONNECTION_STRING = (
    "postgres://svcuser:" + CONNECTION_PASSWORD + "@db.example.org:5432/app"
)
ASSIGNED_VALUE = "9f8Ld2Qm" + "4Xr7Tb1Yv6Zc3Nk5Hs0Jw8Pe"
PROSE_PASSWORD = "Mq7" + "!Lz4Rt9"
HIGH_ENTROPY = "9f8Ld2Qm4Xr7Tb1Yv6Zc3Nk5Hs0Jw8Pe" + "Aa4Bb5Cc6Dd"
COMPANY = vocab.legal_ja("ダミー商事")
OBFUSCATED_HOST = vocab.domain("files", "corp", "com", sep="[.]")
#: 予約ドメインを逆にした形ではないアプリのパッケージ名（断片から組み立てる）
PACKAGE_NAME = vocab.domain("com", "foo", "bar")
DOMAIN = "docs.example.org"
EMAIL = "taro@example.org"
PRIVATE_IP = vocab.ip(192, 168, 0, 10)
INTERNAL_HOST = vocab.domain("db01", "corp", "internal")

#: kind ごとの入力の 1 行と、その行から出る（その kind の）候補の値
KIND_CASES = {
    "known_token": ("credential is " + AWS_KEY + " here", AWS_KEY),
    "private_key": (PRIVATE_KEY_HEADER, PRIVATE_KEY_HEADER),
    "connection_string": (CONNECTION_STRING, CONNECTION_PASSWORD),
    "jwt": ("bearer " + JWT, JWT),
    "secret_assignment": ("api_key = " + ASSIGNED_VALUE, ASSIGNED_VALUE),
    "password_prose": ("パスワードは" + PROSE_PASSWORD + "です", PROSE_PASSWORD),
    "high_entropy": ("blob " + HIGH_ENTROPY + " end", HIGH_ENTROPY),
    "company_hint": ("発注先は" + COMPANY + "です", COMPANY),
    "obfuscated_host": ("host " + OBFUSCATED_HOST, OBFUSCATED_HOST),
    "package_name": ("applicationId " + PACKAGE_NAME, PACKAGE_NAME),
    "domain": ("see " + DOMAIN, DOMAIN),
    "email": ("contact " + EMAIL, EMAIL),
    "private_ip": ("server " + PRIVATE_IP, PRIVATE_IP),
    "internal_host": ("host " + INTERNAL_HOST, INTERNAL_HOST),
}

COMMIT = "0123456789abcdef" * 2 + "01234567"
OTHER_COMMIT = "fedcba9876543210" * 2 + "fedcba98"


class PatternTestCase(unittest.TestCase):
    """テストごとに専用の一時ディレクトリを持ち、本文のファイルと検査単位を作る。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self._count = 0

    def write(self, text):
        self._count += 1
        path = self.dir / f"body{self._count}.txt"
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return path

    def file_unit(self, text, unit_id="U0001", label="sample.txt"):
        return {
            "unit_id": unit_id,
            "kind": "file",
            "label": label,
            "text_path": str(self.write(text)),
        }

    def added_lines_unit(
        self, text, line_numbers, unit_id="U0001", commit=COMMIT, path="src/app.txt"
    ):
        return {
            "unit_id": unit_id,
            "kind": "added_lines",
            "label": path,
            "commit": commit,
            "text_path": str(self.write(text)),
            "line_numbers": line_numbers,
        }

    def message_unit(self, text, unit_id="U0001", commit=COMMIT):
        return {
            "unit_id": unit_id,
            "kind": "commit_message",
            "label": "コミットメッセージ",
            "commit": commit,
            "text_path": str(self.write(text)),
        }

    def detect_text(self, text):
        return patterns.detect([self.file_unit(text)])


def kinds_of(result):
    return [c["kind"] for c in result["candidates"]]


def values_of(result):
    return [c["value"] for c in result["candidates"]]


class KindTest(PatternTestCase):
    """DES-087 の kind の語彙 14 種のそれぞれで、対応する kind の候補が出る（1 種 1 テスト以上）。"""

    def assert_kind(self, kind):
        line, expected = KIND_CASES[kind]
        result = self.detect_text(line + "\n")
        matching = [c for c in result["candidates"] if c["kind"] == kind]
        self.assertEqual(
            [c["value"] for c in matching], [expected], result["candidates"]
        )
        self.assertEqual(matching[0]["source"], "pattern")
        self.assertEqual(matching[0]["line"], 1)
        self.assertEqual(matching[0]["unit_id"], "U0001")
        return result

    def test_cases_cover_the_whole_kind_vocabulary(self):
        self.assertEqual(len(patterns.KINDS), 14)
        self.assertEqual(sorted(KIND_CASES), sorted(patterns.KINDS))

    def test_known_token(self):
        for name, value in KNOWN_TOKENS.items():
            with self.subTest(format=name):
                result = self.detect_text("credential is " + value + " here\n")
                self.assertEqual(kinds_of(result), ["known_token"])
                self.assertEqual(values_of(result), [value])
        self.assert_kind("known_token")

    def test_private_key(self):
        self.assert_kind("private_key")
        plain = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertEqual(kinds_of(self.detect_text(plain + "\n")), ["private_key"])

    def test_connection_string(self):
        result = self.assert_kind("connection_string")
        # 値は埋め込まれたパスワードだけ。接続文字列の中の user@host はメールアドレスにならない
        self.assertNotIn("email", kinds_of(result))

    def test_jwt(self):
        self.assert_kind("jwt")

    def test_secret_assignment(self):
        self.assert_kind("secret_assignment")
        for line in (
            'password: "' + ASSIGNED_VALUE + '"',
            "SECRET_KEY='" + ASSIGNED_VALUE + "'",
        ):
            with self.subTest(line=line[:12]):
                self.assertEqual(
                    values_of(self.detect_text(line + "\n")), [ASSIGNED_VALUE]
                )

    def test_password_prose(self):
        self.assert_kind("password_prose")
        english = self.detect_text("the password is " + PROSE_PASSWORD + "\n")
        self.assertEqual(kinds_of(english), ["password_prose"])
        self.assertEqual(values_of(english), [PROSE_PASSWORD])

    def test_password_prose_requires_a_digit_or_symbol_and_six_characters(self):
        for value in ("abcdefgh", "Ab1!x"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.detect_text("パスワードは" + value + "\n")["candidates"], []
                )

    def test_password_prose_ignores_a_value_in_a_code_span(self):
        # 説明文の中の、バッククォートで囲んだコード片（識別子など）は、散文中のパスワードではない
        for line in (
            "散文中のパスワード: ``password_prose``（説明）",
            "パスワードは `" + PROSE_PASSWORD + "` です",
            "the password is `" + PROSE_PASSWORD + "`",
        ):
            with self.subTest(line=line[:16]):
                self.assertEqual(self.detect_text(line + "\n")["candidates"], [])

    def test_password_prose_still_detects_a_value_in_quotes(self):
        # バッククォート以外の引用符で囲んだ値は、今までどおり検出する
        for line in (
            "パスワードは「" + PROSE_PASSWORD + "」です",
            'the password is "' + PROSE_PASSWORD + '"',
        ):
            with self.subTest(line=line[:12]):
                self.assertEqual(
                    values_of(self.detect_text(line + "\n")), [PROSE_PASSWORD]
                )

    def test_password_prose_drops_trailing_punctuation(self):
        result = self.detect_text("password is " + PROSE_PASSWORD + ".\n")
        self.assertEqual(values_of(result), [PROSE_PASSWORD])

    def test_high_entropy(self):
        self.assertGreaterEqual(
            patterns.shannon_entropy(HIGH_ENTROPY), patterns.ENTROPY_THRESHOLD
        )
        self.assert_kind("high_entropy")

    def test_company_hint(self):
        self.assert_kind("company_hint")
        company = vocab.legal_en("Foo", "Bar")
        english = self.detect_text("vendor is " + company + " today\n")
        self.assertEqual(values_of(english), [company])

    def test_obfuscated_host(self):
        self.assert_kind("obfuscated_host")
        spaced = self.detect_text(
            "host " + vocab.domain("files", "corp", "com", sep=" dot ") + "\n"
        )
        self.assertEqual(kinds_of(spaced), ["obfuscated_host"])

    def test_obfuscated_host_does_not_take_ordinary_prose(self):
        self.assertEqual(
            self.detect_text("the dot product of two vectors\n")["candidates"], []
        )

    def test_package_name(self):
        self.assert_kind("package_name")

    def test_package_name_takes_the_whole_identifier_including_a_class_name(self):
        flavor = vocab.domain("com", "foo", "bar", "dev")
        activity = vocab.domain("com", "foo", "bar", "MainActivity")
        for value in (
            PACKAGE_NAME,
            flavor,
            activity,
            vocab.domain("ORG", "Foo_x", "bar2"),
        ):
            with self.subTest(value=value):
                result = self.detect_text("id: " + value + "\n")
                self.assertEqual(kinds_of(result), ["package_name"])
                self.assertEqual(values_of(result), [value])

    def test_package_name_is_adopted_before_domain(self):
        # 末尾が .dev・.app でも、domain として重ねて報告しない（package_name が先に採る）
        for value in (
            vocab.domain("com", "foo", "bar", "dev"),
            vocab.domain("com", "foo", "bar", "app"),
        ):
            with self.subTest(value=value):
                result = self.detect_text("see " + value + "\n")
                self.assertEqual(kinds_of(result), ["package_name"])

    def test_reserved_reverse_domain_is_still_a_package_name_candidate(self):
        # 予約ドメインを逆にした形は、検出では除かず（domain に拾われないよう採ったうえで）判定が通過にする
        result = self.detect_text("id: com.example.app\norg.example.demo.Main\n")
        self.assertEqual(kinds_of(result), ["package_name", "package_name"])
        self.assertEqual(
            values_of(result), ["com.example.app", "org.example.demo.Main"]
        )

    def test_package_name_after_a_shell_default_operator_is_taken_whole(self):
        # ``${APP_ID:-com.example.app}`` の ``-`` は語の区切りであり、パッケージ名の一部でも、より長い名前の一部でもない
        result = self.detect_text('APP_ID="${APP_ID:-' + PACKAGE_NAME + '}"\n')
        self.assertEqual(kinds_of(result), ["package_name"])
        self.assertEqual(values_of(result), [PACKAGE_NAME])

    def test_package_name_needs_three_labels_and_a_root_word(self):
        for value in (
            vocab.domain("com", "foo"),
            vocab.domain("io", "open"),
            vocab.domain("foo", "bar", "baz"),
            vocab.domain("comx", "foo", "bar"),
            vocab.domain("com", "foo-x", "bar"),
            vocab.domain("com", "1foo", "bar"),
        ):
            with self.subTest(value=value):
                self.assertNotIn(
                    "package_name", kinds_of(self.detect_text("id: " + value + "\n"))
                )

    def test_package_name_root_words_kept(self):
        for root in ("com", "org", "net", "io", "jp", "co", "edu", "gov"):
            value = vocab.domain(root, "foo", "bar")
            with self.subTest(root=root):
                result = self.detect_text("id: " + value + "\n")
                self.assertEqual(kinds_of(result), ["package_name"])
                self.assertEqual(values_of(result), [value])

    def test_package_name_is_not_taken_for_file_names_and_words_starting_with_a_dropped_root_word(
        self,
    ):
        # 根の語から外した app・dev・me・ai・info・biz で始まる、よくあるファイル名・設定名・ホスト名
        for text in (
            "app.module.ts",
            "app.component.spec",
            vocab.domain("dev", "env", "local"),
            "ai.model.config.json",
            "me.profile.settings.yaml",
            vocab.domain("app", "staging", "internal"),
        ):
            with self.subTest(text=text):
                self.assertNotIn(
                    "package_name", kinds_of(self.detect_text("see " + text + "\n"))
                )

    def test_package_name_roots_are_exactly_the_decided_set(self):
        self.assertEqual(
            sorted(patterns._PACKAGE_ROOTS),
            sorted(("com", "org", "net", "io", "jp", "co", "edu", "gov")),
        )

    def test_package_name_is_not_taken_from_the_middle_of_a_longer_name(self):
        for text in (
            vocab.domain("x", "com", "foo", "bar"),
            "a@" + vocab.domain("com", "foo", "bar"),
            vocab.domain("com", "foo", "bar") + "-x",
        ):
            with self.subTest(text=text):
                self.assertNotIn(
                    "package_name", kinds_of(self.detect_text("id: " + text + "\n"))
                )

    def test_domain(self):
        self.assert_kind("domain")

    def test_email(self):
        self.assert_kind("email")

    def test_email_with_git_local_part_is_not_an_email(self):
        result = self.detect_text("git@github.com:org/repo.git\n")
        self.assertNotIn("email", kinds_of(result))

    def test_private_ip(self):
        self.assert_kind("private_ip")
        for ip in (
            vocab.ip(10, 1, 2, 3),
            vocab.ip(172, 16, 0, 1),
            vocab.ip(172, 31, 255, 254),
            vocab.ip(192, 168, 1, 1),
        ):
            with self.subTest(ip=ip):
                self.assertEqual(
                    kinds_of(self.detect_text("ip " + ip + "\n")), ["private_ip"]
                )

    def test_non_private_ip_is_not_a_candidate(self):
        for ip in ("8.8.8.8", "172.32.0.1", "11.0.0.1", "192.169.0.1", "10.0.0.256"):
            with self.subTest(ip=ip):
                self.assertEqual(self.detect_text("ip " + ip + "\n")["candidates"], [])

    def test_internal_host(self):
        self.assert_kind("internal_host")

    def test_detection_does_not_exclude_reserved_values(self):
        # 文書用の予約値・許可リストは、検出では除かない（機密判定が通過にする）
        result = self.detect_text(
            "see example.com\ndocs.python.org\nuser@example.com\n"
        )
        self.assertEqual(
            values_of(result), ["example.com", "docs.python.org", "user@example.com"]
        )
        self.assertEqual(kinds_of(result), ["domain", "domain", "email"])


class RulePrecedenceTest(PatternTestCase):
    def test_all_kinds_are_found_when_given_together(self):
        text = "".join(line + "\n" for line, _ in KIND_CASES.values())
        result = self.detect_text(text)
        self.assertEqual(
            set(kinds_of(result)) & set(patterns.KINDS), set(patterns.KINDS)
        )
        self.assertEqual(result["suppressed"]["count"], 0)

    def test_same_line_same_value_is_taken_by_the_first_rule_only(self):
        # 「秘密らしいキーへの代入」でも一致する値だが、既知の形式が先に採る
        result = self.detect_text("token = " + AWS_KEY + "\n")
        self.assertEqual(kinds_of(result), ["known_token"])
        self.assertEqual(values_of(result), [AWS_KEY])

    def test_substring_of_the_adopted_value_is_excluded(self):
        # メールアドレスを採ると、その中のドメインは別の候補にならない（規則 7 の中の順序）
        result = self.detect_text("contact " + EMAIL + "\n")
        self.assertEqual(kinds_of(result), ["email"])

    def test_package_name_is_not_reported_again_as_domain(self):
        result = self.detect_text(
            "flavor " + vocab.domain("com", "foo", "bar", "dev") + "\n"
        )
        self.assertEqual(kinds_of(result), ["package_name"])
        self.assertNotIn("domain", kinds_of(result))

    def test_package_name_on_a_suppressed_line_is_counted_not_reported(self):
        result = self.detect_text(
            PACKAGE_NAME + "  # " + patterns.SUPPRESS_MARKER + "\n"
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["suppressed"]["count"], 1)
        self.assertEqual(result["suppressed"]["locations"][0]["kind"], "package_name")

    def test_same_value_on_different_lines_is_reported_each_time(self):
        result = self.detect_text(EMAIL + "\n" + EMAIL + "\n")
        self.assertEqual([c["line"] for c in result["candidates"]], [1, 2])


class DeduplicationTest(PatternTestCase):
    def test_substring_of_adopted_value_is_excluded(self):
        """JWT 全体を採ったあと、その署名部分を再び候補にしない（旧 DeduplicationTest の移植）。"""
        result = self.detect_text("secret = " + JWT + "\n")
        self.assertEqual(kinds_of(result), ["jwt"])
        self.assertEqual(values_of(result), [JWT])


class SuppressionTest(PatternTestCase):
    @staticmethod
    def marked(line):
        return f"{line}  # {patterns.SUPPRESS_MARKER}"

    def test_suppressed_line_is_counted_with_position(self):
        result = self.detect_text(self.marked("aws = " + AWS_KEY) + "\n")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["suppressed"]["count"], 1)
        self.assertEqual(
            result["suppressed"]["locations"],
            [
                {
                    "unit_id": "U0001",
                    "label": "sample.txt",
                    "line": 1,
                    "kind": "known_token",
                }
            ],
        )

    def test_suppressed_location_has_no_value(self):
        result = self.detect_text(self.marked("aws = " + AWS_KEY) + "\n")
        for location in result["suppressed"]["locations"]:
            self.assertEqual(sorted(location), ["kind", "label", "line", "unit_id"])
        self.assertNotIn(AWS_KEY, json.dumps(result["suppressed"]))

    def test_marker_affects_only_its_own_line(self):
        text = (
            self.marked("a = " + AWS_KEY)
            + "\nb = "
            + GITHUB_TOKEN
            + "\n"
            + self.marked("c = " + EMAIL)
            + "\n"
        )
        result = self.detect_text(text)
        self.assertEqual(values_of(result), [GITHUB_TOKEN])
        self.assertEqual(
            [loc["line"] for loc in result["suppressed"]["locations"]], [1, 3]
        )
        self.assertEqual(
            [loc["kind"] for loc in result["suppressed"]["locations"]],
            ["known_token", "email"],
        )

    def test_every_value_on_a_suppressed_line_is_counted(self):
        result = self.detect_text(
            self.marked("a = " + AWS_KEY + " b = " + EMAIL) + "\n"
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["suppressed"]["count"], 2)

    def test_marker_itself_is_not_detected(self):
        """マーカー文字列自体は「秘密らしいキーへの代入」の形をしているため、走査から除く。"""
        result = self.detect_text(
            "行末に `" + patterns.SUPPRESS_MARKER + "` と書くと分類される\n"
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["suppressed"], {"count": 0, "locations": []})

    def test_marker_after_the_line_length_limit_still_suppresses(self):
        """マーカーが先頭の窓より後ろにあっても、その行は抑制される（マーカーは行全体で判定する）。"""
        padding = " " * patterns.LINE_WINDOW_CHARS
        result = self.detect_text("aws = " + AWS_KEY + padding + self.marked("") + "\n")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["suppressed"]["count"], 1)
        self.assertEqual(result["suppressed"]["locations"][0]["kind"], "known_token")

    def test_suppressed_position_in_added_lines_is_the_original_line_number(self):
        unit = self.added_lines_unit(
            "first\n" + self.marked("aws = " + AWS_KEY) + "\n", [30, 41]
        )
        result = patterns.detect([unit])
        self.assertEqual(result["suppressed"]["locations"][0]["line"], 41)
        self.assertEqual(result["suppressed"]["locations"][0]["label"], unit["label"])


class FilterTest(PatternTestCase):
    def assert_filtered(self, line, reason):
        result = self.detect_text(line + "\n")
        self.assertEqual(result["candidates"], [], line)
        self.assertEqual(result["filtered"]["reasons"][reason], 1, result["filtered"])
        self.assertEqual(result["filtered"]["count"], 1)
        self.assertEqual(result["suppressed"]["count"], 0)

    def test_filter_reasons_are_the_closed_vocabulary(self):
        result = self.detect_text("nothing here\n")
        self.assertEqual(
            sorted(result["filtered"]["reasons"]), sorted(patterns.FILTER_REASONS)
        )
        self.assertEqual(
            sorted(patterns.FILTER_REASONS),
            ["code_expression", "constant_name", "path_like", "placeholder"],
        )
        self.assertEqual(result["filtered"]["count"], 0)

    def test_placeholders_are_filtered(self):
        # 「秘密らしいキーへの代入」の値が、プレースホルダ（コード式として先に除かれる形を含む）に見える
        for value in (
            "<your-api-key>",
            "${API_KEY}",
            "$FIGMA_TOKEN",
            "xxxxxxxxxxxx",
            "REPLACE_ME_NOW",
            "*****************",
        ):
            with self.subTest(value=value):
                result = self.detect_text('api_key = "' + value + '"\n')
                self.assertEqual(result["candidates"], [])
                self.assertEqual(result["filtered"]["count"], 1)

    def test_placeholder_is_counted_under_placeholder(self):
        self.assert_filtered('api_key = "<your-api-key>"', "placeholder")

    def test_placeholder_applies_to_known_formats_too(self):
        # 公式の例示値は、既知の形式に一致しても、プレースホルダ（example を含む）として除かれる
        self.assert_filtered("aws = AKIAIOSFODNN7EXAMPLE", "placeholder")

    def test_environment_lookup_is_filtered(self):
        self.assert_filtered('api_key = os.environ["KEY"]', "code_expression")

    def test_code_expression_is_filtered(self):
        self.assert_filtered("token = tokens[i].decode(errors)", "code_expression")

    def test_constant_name_is_filtered(self):
        """定数名の列挙を秘密と誤認しない（生成物の列挙で実測された誤検出）。"""
        self.assert_filtered(
            "with tokens: PROTOCOL_HEADER, REVIEW_TYPE, PLUGIN_ROOT", "constant_name"
        )

    def test_single_word_uppercase_is_still_a_candidate(self):
        """区切りの無い大文字列は、定数名として除かない（実在の資格情報と衝突させない）。"""
        value = "ABCDEFGHIJ" + "KLMNOPQRST"
        result = self.detect_text("secret_token = " + value + "\n")
        self.assertEqual(values_of(result), [value])
        self.assertEqual(result["filtered"]["reasons"]["constant_name"], 0)

    def test_path_like_key_is_filtered(self):
        """``design_token_template.md: <sha256>`` 形式（チェックサム表）を秘密と誤認しない。"""
        digest = "f1c6a877274047043cd1cd7226de6813ad0d6c66f5dd894df35416f24b3c5b93"
        self.assert_filtered("  docs/design_token_template.md: " + digest, "path_like")

    def test_prose_with_japanese_is_not_a_candidate(self):
        result = self.detect_text(
            "Authority: Tool-provided（forge 内蔵）/ Project-defined\n"
        )
        self.assertEqual(result["candidates"], [])

    def test_notation_word_in_connection_string_is_filtered(self):
        """``scheme://user:password@host`` のような記法の説明を秘密と誤認しない。"""
        self.assert_filtered(
            "接続文字列は `postgres://user:password@host/db` 形式", "placeholder"
        )

    def test_value_with_japanese_is_a_code_expression_not_a_secret(self):
        self.assert_filtered("password = 日本語のパスワードです", "code_expression")


class HighEntropyTest(PatternTestCase):
    def test_hex_only_is_not_high_entropy(self):
        for digest in (
            "b791ef2d1ad781c38a19c6edc7495e95221d202e376bb6d684912801b23a6129",
            "b791ef2d1ad781c38a19c6edc7495e95221d202e",
            "B791EF2D1AD781C38A19C6EDC7495E95221D202E376BB6D684912801B23A6129",
        ):
            with self.subTest(length=len(digest)):
                result = self.detect_text("sha256 of the file is " + digest + "\n")
                self.assertEqual(result["candidates"], [])
                self.assertEqual(result["filtered"]["count"], 0)

    def test_long_path_is_not_high_entropy(self):
        result = self.detect_text(
            "docs/specs/forge/design/DES-055_review_request_template_design.md\n"
        )
        self.assertEqual(result["candidates"], [])

    def test_shorter_than_forty_characters_is_not_high_entropy(self):
        value = HIGH_ENTROPY[:39]
        self.assertGreaterEqual(
            patterns.shannon_entropy(value), patterns.ENTROPY_THRESHOLD
        )
        self.assertEqual(self.detect_text("blob " + value + "\n")["candidates"], [])

    def test_forty_characters_is_high_entropy(self):
        value = HIGH_ENTROPY[:40]
        self.assertGreaterEqual(
            patterns.shannon_entropy(value), patterns.ENTROPY_THRESHOLD
        )
        result = self.detect_text("blob " + value + "\n")
        self.assertEqual(values_of(result), [value])
        self.assertEqual(kinds_of(result), ["high_entropy"])

    def test_low_entropy_long_string_is_not_high_entropy(self):
        value = "ab" * 25
        self.assertLess(patterns.shannon_entropy(value), patterns.ENTROPY_THRESHOLD)
        self.assertEqual(self.detect_text("blob " + value + "\n")["candidates"], [])

    def test_shannon_entropy(self):
        self.assertEqual(patterns.shannon_entropy("aaaa"), 0.0)
        self.assertEqual(patterns.shannon_entropy(""), 0.0)
        self.assertGreater(patterns.shannon_entropy("ab"), 0.0)
        self.assertAlmostEqual(patterns.shannon_entropy("ab"), 1.0)


class PositionTest(PatternTestCase):
    def test_file_line_is_one_based(self):
        result = self.detect_text(
            "line one\nline two\napi_key = " + ASSIGNED_VALUE + "\n"
        )
        self.assertEqual([c["line"] for c in result["candidates"]], [3])
        self.assertEqual(result["candidates"][0]["unit_id"], "U0001")

    def test_file_candidate_has_no_commit_key(self):
        result = self.detect_text(EMAIL + "\n")
        self.assertNotIn("commit", result["candidates"][0])

    def test_added_lines_line_is_the_original_line_number(self):
        unit = self.added_lines_unit(
            "x = 1\nemail " + EMAIL + "\n" + "ip " + PRIVATE_IP + "\n", [10, 11, 42]
        )
        result = patterns.detect([unit])
        self.assertEqual(
            [(c["kind"], c["line"]) for c in result["candidates"]],
            [("email", 11), ("private_ip", 42)],
        )

    def test_added_lines_commit_comes_from_the_commit_field(self):
        unit = self.added_lines_unit(EMAIL + "\n", [7], commit=OTHER_COMMIT)
        self.assertEqual(
            patterns.detect([unit])["candidates"][0]["commit"], OTHER_COMMIT
        )

    def test_commit_message_line_is_one_based_and_has_the_commit(self):
        result = patterns.detect([self.message_unit("subject\n\nbody " + EMAIL + "\n")])
        candidate = result["candidates"][0]
        self.assertEqual((candidate["line"], candidate["commit"]), (3, COMMIT))

    def test_sha256_commit_id_becomes_the_candidate_commit_as_it_is(self):
        commit = "ab" * 32
        self.assertEqual(len(commit), 64)
        result = patterns.detect([self.message_unit(EMAIL + "\n", commit=commit)])
        self.assertEqual(result["candidates"][0]["commit"], commit)

    def test_commit_does_not_depend_on_the_length_of_the_identifier(self):
        for length in (1, 7, 12, 39, 40, 41, 63, 64, 65, 100):
            commit = ("0123456789abcdef" * 7)[:length]
            with self.subTest(length=length):
                added = patterns.detect(
                    [self.added_lines_unit(EMAIL + "\n", [3], commit=commit)]
                )
                message = patterns.detect(
                    [self.message_unit(EMAIL + "\n", commit=commit)]
                )
                self.assertEqual(added["candidates"][0]["commit"], commit)
                self.assertEqual(message["candidates"][0]["commit"], commit)

    def test_label_is_not_read_for_the_commit(self):
        # label にどんな語があっても、commit は commit の項目から取る
        unit = self.added_lines_unit(
            EMAIL + "\n", [1], commit=OTHER_COMMIT, path=COMMIT + ":x.txt"
        )
        self.assertEqual(
            patterns.detect([unit])["candidates"][0]["commit"], OTHER_COMMIT
        )

    def test_crlf_does_not_enter_the_value(self):
        result = self.detect_text(EMAIL + "\r\n" + PRIVATE_IP + "\r\n")
        self.assertEqual(values_of(result), [EMAIL, PRIVATE_IP])
        self.assertEqual([c["line"] for c in result["candidates"]], [1, 2])

    def test_lines_are_split_on_newline_only(self):
        # 改ページ等で割らない（割ると added_lines の line_numbers と合わなくなる）
        result = self.detect_text("a\x0cb c\nemail " + EMAIL + "\n")
        self.assertEqual([c["line"] for c in result["candidates"]], [2])

    def test_last_line_without_newline_is_scanned(self):
        self.assertEqual(values_of(self.detect_text("x\n" + EMAIL)), [EMAIL])

    def test_invalid_utf8_is_replaced_and_scanning_continues(self):
        result = patterns.detect(
            [self.file_unit(b"\xff\xfe " + EMAIL.encode("ascii") + b"\n")]
        )
        self.assertEqual(values_of(result), [EMAIL])

    def test_long_line_is_scanned_to_the_end(self):
        """窓の幅を超えた先にある値も検出する（黙って見落とさない）。"""
        first = EMAIL + " " + " " * (patterns.LINE_WINDOW_CHARS - len(EMAIL) - 1)
        later = " " * (patterns.LINE_WINDOW_CHARS * 3) + PRIVATE_IP
        result = self.detect_text(first + "\n" + later + "\n")
        self.assertEqual(
            [(c["kind"], c["line"]) for c in result["candidates"]],
            [("email", 1), ("private_ip", 2)],
        )

    def test_value_across_a_window_boundary_is_detected_once_whole(self):
        """窓の境目をまたぐ値は、欠けた断片ではなく全体を 1 件だけ検出する（空白の無い行でも）。"""
        step = patterns.LINE_WINDOW_CHARS - patterns.WINDOW_OVERLAP_CHARS
        for offset in (-5, 0, 5):
            with self.subTest(offset=offset):
                head = "x" * (patterns.LINE_WINDOW_CHARS - len(EMAIL) // 2 + offset)
                result = self.detect_text(head + " " + EMAIL + "\n")
                self.assertEqual([c["value"] for c in result["candidates"]], [EMAIL])
        tail = "x" * (step * 2) + " " + EMAIL + "\n"
        self.assertEqual(values_of(self.detect_text(tail)), [EMAIL])

    def test_units_are_scanned_in_order_and_ids_follow(self):
        first = self.file_unit(EMAIL + "\n", unit_id="U0001", label="a.txt")
        second = self.file_unit(PRIVATE_IP + "\n", unit_id="U0002", label="b.txt")
        result = patterns.detect([first, second])
        self.assertEqual(
            [(c["candidate_id"], c["unit_id"]) for c in result["candidates"]],
            [("P0001", "U0001"), ("P0002", "U0002")],
        )


class CandidateIdTest(PatternTestCase):
    def test_ids_are_sequential_p_and_four_digits(self):
        text = "\n".join([EMAIL, PRIVATE_IP, INTERNAL_HOST]) + "\n"
        result = self.detect_text(text)
        self.assertEqual(
            [c["candidate_id"] for c in result["candidates"]],
            ["P0001", "P0002", "P0003"],
        )

    def test_candidate_shape(self):
        candidate = self.detect_text(EMAIL + "\n")["candidates"][0]
        self.assertEqual(
            sorted(candidate),
            ["candidate_id", "kind", "line", "source", "unit_id", "value"],
        )
        history = patterns.detect([self.message_unit(EMAIL + "\n")])["candidates"][0]
        self.assertEqual(
            sorted(history),
            ["candidate_id", "commit", "kind", "line", "source", "unit_id", "value"],
        )

    def test_ids_grow_beyond_four_digits(self):
        result = self.detect_text(("ip " + PRIVATE_IP + "\n") * 10_001)
        ids = [c["candidate_id"] for c in result["candidates"]]
        self.assertEqual(ids[0], "P0001")
        self.assertEqual(ids[9998], "P9999")
        self.assertEqual(ids[-1], "P10001")
        self.assertEqual(len(set(ids)), 10_001)

    def test_kinds_in_candidates_are_in_the_vocabulary(self):
        text = "".join(line + "\n" for line, _ in KIND_CASES.values())
        for candidate in self.detect_text(text)["candidates"]:
            self.assertIn(candidate["kind"], patterns.KINDS)


class ResultShapeTest(PatternTestCase):
    def test_empty_units_give_an_empty_result(self):
        self.assertEqual(
            patterns.detect([]),
            {
                "candidates": [],
                "suppressed": {"count": 0, "locations": []},
                "filtered": {
                    "count": 0,
                    "reasons": {reason: 0 for reason in patterns.FILTER_REASONS},
                },
            },
        )

    def test_top_level_keys(self):
        result = self.detect_text("nothing\n")
        self.assertEqual(sorted(result), ["candidates", "filtered", "suppressed"])
        self.assertEqual(sorted(result["suppressed"]), ["count", "locations"])
        self.assertEqual(sorted(result["filtered"]), ["count", "reasons"])

    def test_result_is_json_serializable(self):
        text = "".join(line + "\n" for line, _ in KIND_CASES.values())
        json.dumps(self.detect_text(text))


class DeterminismTest(PatternTestCase):
    def rich_unit(self):
        text = "".join(line + "\n" for line, _ in KIND_CASES.values())
        text += "token = tokens[i].decode(errors)\n"
        text += "aws = " + AWS_KEY + "  # " + patterns.SUPPRESS_MARKER + "\n"
        return self.file_unit(text)

    def test_same_input_twice_gives_the_same_result(self):
        unit = self.rich_unit()
        self.assertEqual(patterns.detect([unit]), patterns.detect([unit]))

    def test_result_does_not_depend_on_the_hash_seed(self):
        unit = self.rich_unit()
        code = (
            "import importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location('p', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "print(json.dumps(module.detect([json.loads(sys.argv[2])]), sort_keys=False))\n"
        )
        outputs = []
        for seed in ("1", "2", "3"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    code,
                    str(_PATTERNS_PATH),
                    json.dumps(unit),
                ],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            outputs.append(completed.stdout)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
        self.assertEqual(json.loads(outputs[0]), patterns.detect([unit]))


class PatternErrorTest(PatternTestCase):
    def test_missing_body_file_is_a_pattern_error(self):
        unit = self.file_unit("x\n")
        Path(unit["text_path"]).unlink()
        with self.assertRaises(patterns.PatternError):
            patterns.detect([unit])

    def test_unknown_unit_kind_is_a_pattern_error(self):
        unit = self.file_unit("x\n")
        unit["kind"] = "other"
        with self.assertRaises(patterns.PatternError):
            patterns.detect([unit])

    def test_history_unit_without_a_usable_commit_is_a_pattern_error_without_the_body(
        self,
    ):
        makers = {
            "added_lines": lambda: self.added_lines_unit("secret " + EMAIL + "\n", [1]),
            "commit_message": lambda: self.message_unit("secret " + EMAIL + "\n"),
        }
        for kind, make in makers.items():
            for case, change in (
                ("missing", lambda unit: unit.pop("commit")),
                ("empty", lambda unit: unit.update(commit="")),
                ("none", lambda unit: unit.update(commit=None)),
                ("not a string", lambda unit: unit.update(commit=12345)),
                ("list", lambda unit: unit.update(commit=["abc"])),
            ):
                with self.subTest(kind=kind, case=case):
                    unit = make()
                    change(unit)
                    with self.assertRaises(patterns.PatternError) as raised:
                        patterns.detect([unit])
                    self.assertIn("commit がありません", str(raised.exception))
                    self.assertNotIn(EMAIL, str(raised.exception))

    def test_line_numbers_that_do_not_match_the_body_are_a_pattern_error_without_the_body(
        self,
    ):
        unit = self.added_lines_unit(EMAIL + "\nsecond\n", [1])
        with self.assertRaises(patterns.PatternError) as raised:
            patterns.detect([unit])
        self.assertNotIn(EMAIL, str(raised.exception))

    def test_missing_line_numbers_are_a_pattern_error(self):
        unit = self.added_lines_unit(EMAIL + "\n", [1])
        del unit["line_numbers"]
        with self.assertRaises(patterns.PatternError):
            patterns.detect([unit])


if __name__ == "__main__":
    unittest.main()
