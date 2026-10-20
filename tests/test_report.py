#!/usr/bin/env python3
"""secret-linter の report.py（結果提示 R-1）の契約テスト。

DES-087 のモジュール表 R-1 と、テスト表 SC-13・SC-14・SC-15 のスクリプトの検証に対応する。

- 終了コード 0: 区分（合格・不合格）と本文を標準出力へ返す。区分に値が含まれない。本文に不合格・要確認だった
  候補の値が元の文字列のまま含まれる
- 本文の並び: 候補ごと → 通過のまとめ → 抑制 → スキップ。候補が 0 件のときは「検出なし」
- 最新の連番の判定ファイルが使われる（0001 と 0002 がある場合は 0002）。要確認だった候補は 0001 から分かる
- 終了コード 1: 判定が確定していない（confirm が残る）・判定ファイルが無い・作業領域が無い・入力が読めない。
  エラー出力に値が含まれない
- 終了コード 2: 引数を受理できない
- 結果を、作業領域の内にも外にもファイルとして残さない

作業領域は OS の一時ディレクトリに作られる。テストは、TMPDIR・HOME をテストごとの専用ディレクトリへ向ける。
入力は明白なプレースホルダだけを使い、機密らしい値は連結して組み立てる。

実行:
  python3 -m unittest tests.forge.secret-linter.test_report -v
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"
)
SCRIPT = _SCRIPTS_DIR / "report.py"
sys.path.insert(0, str(_SCRIPTS_DIR))

import judgment  # noqa: E402
import workspace  # noqa: E402

#: 明白なプレースホルダ（連結して組み立てる）
FAIL_VALUE = "PLACEHOLDER-" + "FAIL-" + "VALUE"
#: report.py は判定ファイルの区分をそのまま読む（値を判定し直さない）ので、値は予約値の語や同梱の実在ドメインでよい
PASS_VALUE = "www.example.com"
CONFIRM_VALUE = "docs.example.com"
OTHER_PASS_VALUE = "docs.python.org"


def candidate(cid, kind, value, unit_id="U0001", line=1, commit=None):
    item = {"candidate_id": cid, "source": "pattern", "unit_id": unit_id, "line": line, "kind": kind, "value": value}
    if commit is not None:
        item["commit"] = commit
    return item


def judged(entries):
    """``[(candidate_id, class), ...]`` から判定ファイルの中身を作る。全体は規則どおりに決める。"""
    results = [{"candidate_id": cid, "class": cls, "reason": "test"} for cid, cls in entries]
    return {"candidates": results, "class": judgment.overall_class(cls for _, cls in entries)}


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        base = tempfile.TemporaryDirectory()
        self.addCleanup(base.cleanup)
        base_path = Path(base.name).resolve()
        self.tmpdir = base_path / "tmp"
        self.tmpdir.mkdir()
        self.home = base_path / "home"
        self.home.mkdir()
        self.root = base_path / "project"
        self.root.mkdir()

    def env(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.tmpdir)
        env["HOME"] = str(self.home)
        env.pop("TEMP", None)
        env.pop("TMP", None)
        return env

    def make_workspace(self, candidates, judgments, suppressed=None, skipped=None, units=None):
        """作業領域を作り、入力を書く。``judgments`` は判定ファイルの中身の配列（0001 から順に書く）。"""
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            inspection_id = workspace.create()
            workspace.write(inspection_id, "units.json", {
                "units": units if units is not None else [
                    {"unit_id": "U0001", "kind": "file", "label": "docs/a.md", "text_path": "/dev/null"},
                ],
                "skipped": skipped or [],
            })
            workspace.write(inspection_id, "pattern.json", {
                "candidates": candidates,
                "suppressed": suppressed or {"count": 0, "locations": []},
                "filtered": {"count": 0, "reasons": {}},
            })
            for payload in judgments:
                workspace.write_next(inspection_id, "judgment", payload)
        return inspection_id

    def workspace_dir(self, inspection_id):
        return self.tmpdir / f"{workspace.PLACE_PREFIX}{inspection_id}"

    def run_report(self, *args):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=self.env(),
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else None
        return completed.returncode, payload, completed

    def report(self, inspection_id):
        return self.run_report(inspection_id)

    def snapshot(self, *roots):
        """各ディレクトリの全パス（ファイル・ディレクトリ）の集合を返す。"""
        result = set()
        for root in roots:
            result.update(str(p) for p in Path(root).rglob("*"))
        return result


class ClassTest(ReportTestCase):
    """SC-13・SC-14: 区分は合格・不合格の 2 値で、値を含まない。"""

    def test_fail_class_has_no_value(self):
        candidates = [candidate("P0001", "jwt", FAIL_VALUE), candidate("P0002", "domain", PASS_VALUE)]
        inspection_id = self.make_workspace(candidates, [judged([("P0001", "fail"), ("P0002", "pass")])])

        code, payload, _ = self.report(inspection_id)

        self.assertEqual(code, 0)
        self.assertEqual(payload["class"], "不合格")
        self.assertEqual(sorted(payload), ["body", "class"])
        for value in (FAIL_VALUE, PASS_VALUE, "docs/a.md"):
            self.assertNotIn(value, payload["class"])

    def test_pass_class(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "domain", PASS_VALUE)], [judged([("P0001", "pass")])],
        )
        code, payload, _ = self.report(inspection_id)
        self.assertEqual((code, payload["class"]), (0, "合格"))

    def test_class_is_one_of_two_words_for_every_outcome(self):
        cases = {
            "合格": [("P0001", "pass")],
            "不合格": [("P0001", "fail")],
        }
        for expected, entries in cases.items():
            with self.subTest(expected=expected):
                inspection_id = self.make_workspace([candidate("P0001", "domain", CONFIRM_VALUE)], [judged(entries)])
                self.assertEqual(self.report(inspection_id)[1]["class"], expected)


class BodyTest(ReportTestCase):
    """SC-14: 本文に不合格・要確認だった候補の値が元のまま含まれ、節が決めた順に並ぶ。"""

    def test_fail_value_is_shown_as_is_with_position_kind_and_verdict(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "jwt", FAIL_VALUE, line=12)], [judged([("P0001", "fail")])],
        )
        _, payload, _ = self.report(inspection_id)
        body = payload["body"]
        self.assertIn(FAIL_VALUE, body)
        self.assertIn("docs/a.md:12", body)
        self.assertIn("jwt", body)
        self.assertIn("不合格", body)

    def test_fail_report_ends_with_the_way_to_pass_a_false_positive(self):
        # 不合格の結果は、誤検出を通す方法（抑制マーカー）を示す。示さなければ、利用者も AI も、止まったまま打つ手を知らない
        inspection_id = self.make_workspace(
            [candidate("P0001", "jwt", FAIL_VALUE, line=12)], [judged([("P0001", "fail")])],
        )
        body = self.report(inspection_id)[1]["body"]
        self.assertTrue(body.rstrip().splitlines()[-1].find("secrets-scan: ignore") >= 0)

    def test_pass_report_does_not_show_the_way_to_pass(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "domain", PASS_VALUE)], [judged([("P0001", "pass")])],
        )
        self.assertNotIn("secrets-scan: ignore", self.report(inspection_id)[1]["body"])

    def test_value_with_special_characters_is_unchanged(self):
        value = 'p@ss"w\\ord<>&' + "日本語"
        inspection_id = self.make_workspace(
            [candidate("P0001", "secret_assignment", value)], [judged([("P0001", "fail")])],
        )
        self.assertIn(value, self.report(inspection_id)[1]["body"])

    def test_commit_is_shown_for_history_candidates(self):
        units = [{"unit_id": "U0001", "kind": "added_lines", "label": "src/x.py", "commit": "abc1234",
                  "text_path": "/dev/null"}]
        inspection_id = self.make_workspace(
            [candidate("P0001", "jwt", FAIL_VALUE, line=3, commit="abc1234")],
            [judged([("P0001", "fail")])], units=units,
        )
        body = self.report(inspection_id)[1]["body"]
        self.assertIn("src/x.py:3 [commit abc1234]", body)

    def test_candidate_without_line_shows_label_only(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "jwt", FAIL_VALUE, line=None)], [judged([("P0001", "fail")])],
        )
        body = self.report(inspection_id)[1]["body"]
        self.assertIn("docs/a.md /", body)
        self.assertNotIn("docs/a.md:", body)

    def test_confirm_resolved_by_answer_shows_before_and_after(self):
        candidates = [candidate("P0001", "domain", CONFIRM_VALUE, line=2), candidate("P0002", "domain", FAIL_VALUE, line=5)]
        first = judged([("P0001", "confirm"), ("P0002", "confirm")])
        second = judged([("P0001", "pass"), ("P0002", "fail")])
        inspection_id = self.make_workspace(candidates, [first, second])

        code, payload, _ = self.report(inspection_id)

        self.assertEqual((code, payload["class"]), (0, "不合格"))
        body = payload["body"]
        self.assertIn(CONFIRM_VALUE, body)
        self.assertIn(FAIL_VALUE, body)
        self.assertIn("要確認 → 通過", body)
        self.assertIn("要確認 → 不合格", body)

    def test_section_order(self):
        candidates = [
            candidate("P0001", "domain", PASS_VALUE, line=1),
            candidate("P0002", "jwt", FAIL_VALUE, line=2),
        ]
        suppressed = {"count": 1, "locations": [{"unit_id": "U0001", "label": "docs/a.md", "line": 9, "kind": "email"}]}
        skipped = [{"label": "big.bin", "reason": "too large"}]
        inspection_id = self.make_workspace(
            candidates, [judged([("P0001", "pass"), ("P0002", "fail")])], suppressed=suppressed, skipped=skipped,
        )

        body = self.report(inspection_id)[1]["body"]

        headings = [line for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(len(headings), 4)
        self.assertIn("不合格・要確認", headings[0])
        self.assertIn("通過", headings[1])
        self.assertIn("抑制マーカー", headings[2])
        self.assertIn("スキップ", headings[3])

    def test_empty_sections_are_not_listed(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "domain", PASS_VALUE)], [judged([("P0001", "pass")])],
        )
        body = self.report(inspection_id)[1]["body"]
        headings = [line for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(len(headings), 1)
        self.assertIn("通過", headings[0])

    def test_no_candidates_says_nothing_detected(self):
        inspection_id = self.make_workspace([], [judged([])])
        code, payload, _ = self.report(inspection_id)
        self.assertEqual((code, payload["class"]), (0, "合格"))
        self.assertIn("検出なし", payload["body"])
        self.assertNotIn("## ", payload["body"])


class PassSummaryTest(ReportTestCase):
    def test_same_kind_and_value_are_one_line_with_count_and_first_position(self):
        candidates = [
            candidate("P0001", "domain", PASS_VALUE, line=4),
            candidate("P0002", "domain", PASS_VALUE, line=8),
            candidate("P0003", "domain", PASS_VALUE, unit_id="U0002", line=1),
            candidate("P0004", "domain", OTHER_PASS_VALUE, line=6),
        ]
        units = [
            {"unit_id": "U0001", "kind": "file", "label": "docs/a.md", "text_path": "/dev/null"},
            {"unit_id": "U0002", "kind": "file", "label": "docs/b.md", "text_path": "/dev/null"},
        ]
        inspection_id = self.make_workspace(
            candidates, [judged([(c["candidate_id"], "pass") for c in candidates])], units=units,
        )

        body = self.report(inspection_id)[1]["body"]

        lines_with_value = [line for line in body.splitlines() if PASS_VALUE in line]
        self.assertEqual(len(lines_with_value), 1)
        self.assertIn("3 件", lines_with_value[0])
        self.assertIn("docs/a.md:4", lines_with_value[0])
        self.assertNotIn("docs/a.md:8", body)
        self.assertEqual(len([line for line in body.splitlines() if OTHER_PASS_VALUE in line]), 1)

    def test_same_value_with_different_kind_is_not_merged(self):
        candidates = [
            candidate("P0001", "domain", PASS_VALUE, line=1),
            candidate("P0002", "internal_host", PASS_VALUE, line=2),
        ]
        inspection_id = self.make_workspace(candidates, [judged([("P0001", "pass"), ("P0002", "pass")])])
        body = self.report(inspection_id)[1]["body"]
        self.assertEqual(len([line for line in body.splitlines() if PASS_VALUE in line]), 2)

    def test_many_pass_candidates_stay_small(self):
        candidates = [candidate(f"P{n:04d}", "domain", PASS_VALUE, line=n) for n in range(1, 201)]
        inspection_id = self.make_workspace(candidates, [judged([(c["candidate_id"], "pass") for c in candidates])])
        body = self.report(inspection_id)[1]["body"]
        self.assertEqual(len([line for line in body.splitlines() if PASS_VALUE in line]), 1)
        self.assertIn("200 件", body)


class SuppressedAndSkippedTest(ReportTestCase):
    """SC-15: 抑制マーカー付きの件数と位置、スキップと理由。"""

    def test_suppressed_count_and_locations(self):
        suppressed = {"count": 2, "locations": [
            {"unit_id": "U0001", "label": "docs/a.md", "line": 4, "kind": "email"},
            {"unit_id": "U0001", "label": "docs/a.md", "line": 9, "kind": "domain"},
        ]}
        inspection_id = self.make_workspace([], [judged([])], suppressed=suppressed)

        body = self.report(inspection_id)[1]["body"]

        self.assertIn("（2 件）", body)
        self.assertIn("docs/a.md:4 / email", body)
        self.assertIn("docs/a.md:9 / domain", body)

    def test_suppressed_in_history_unit_shows_commit(self):
        units = [{"unit_id": "U0001", "kind": "added_lines", "label": "src/x.py", "commit": "abc1234",
                  "text_path": "/dev/null"}]
        suppressed = {"count": 1, "locations": [{"unit_id": "U0001", "label": "src/x.py", "line": 2, "kind": "email"}]}
        inspection_id = self.make_workspace([], [judged([])], suppressed=suppressed, units=units)
        self.assertIn("src/x.py:2 [commit abc1234]", self.report(inspection_id)[1]["body"])

    def test_skipped_with_reason(self):
        skipped = [
            {"label": "big.bin", "reason": "too large"},
            {"label": "src/y.py", "commit": "def5678", "reason": "binary"},
        ]
        inspection_id = self.make_workspace([], [judged([])], skipped=skipped)

        body = self.report(inspection_id)[1]["body"]

        self.assertIn("big.bin / 理由: too large", body)
        self.assertIn("src/y.py [commit def5678] / 理由: binary", body)
        self.assertIn("（2 件）", body)

    def test_zero_suppressed_and_no_skipped_have_no_section(self):
        body = self.report(self.make_workspace([], [judged([])]))[1]["body"]
        self.assertNotIn("抑制", body)
        self.assertNotIn("スキップ", body)


class LatestJudgmentTest(ReportTestCase):
    def test_highest_numbered_judgment_is_used(self):
        candidates = [candidate("P0001", "domain", CONFIRM_VALUE)]
        inspection_id = self.make_workspace(
            candidates, [judged([("P0001", "confirm")]), judged([("P0001", "pass")])],
        )
        code, payload, _ = self.report(inspection_id)
        self.assertEqual((code, payload["class"]), (0, "合格"))

        # 0003 を足すと、0003 が現在の判定になる
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            workspace.write_next(inspection_id, "judgment", judged([("P0001", "fail")]))
        code, payload, _ = self.report(inspection_id)
        self.assertEqual((code, payload["class"]), (0, "不合格"))

    def test_judgment_readers_are_public_in_judgment_module(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "domain", CONFIRM_VALUE)],
            [judged([("P0001", "confirm")]), judged([("P0001", "pass")])],
        )
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            self.assertEqual(judgment.read_first(inspection_id)["class"], "confirm")
            self.assertEqual(judgment.read_latest(inspection_id)["class"], "pass")
            self.assertEqual(workspace.last_number(inspection_id, "judgment"), 2)
            self.assertEqual(workspace.last_number(inspection_id, "no_such_dir"), 0)


class IncompleteTest(ReportTestCase):
    """判定が確定していないとき、結果を返さず 1。"""

    def test_remaining_confirm_returns_1_without_value(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "domain", CONFIRM_VALUE)], [judged([("P0001", "confirm")])],
        )
        code, payload, completed = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertEqual(list(payload), ["errors"])
        self.assertNotIn(CONFIRM_VALUE, completed.stdout + completed.stderr)

    def test_remaining_confirm_with_fail_returns_1(self):
        candidates = [candidate("P0001", "domain", CONFIRM_VALUE), candidate("P0002", "jwt", FAIL_VALUE)]
        inspection_id = self.make_workspace(candidates, [judged([("P0001", "confirm"), ("P0002", "fail")])])
        code, payload, completed = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(FAIL_VALUE, completed.stdout + completed.stderr)

    def test_missing_judgment_returns_1(self):
        inspection_id = self.make_workspace([candidate("P0001", "domain", CONFIRM_VALUE)], [])
        code, payload, completed = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)
        self.assertNotIn(CONFIRM_VALUE, completed.stdout + completed.stderr)

    def test_missing_workspace_returns_1(self):
        code, payload, _ = self.report("0" * 32)
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)

    def test_malformed_inspection_id_returns_1(self):
        code, payload, _ = self.report("../etc")
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)

    def test_candidate_missing_from_judgment_returns_1(self):
        inspection_id = self.make_workspace(
            [candidate("P0001", "jwt", FAIL_VALUE)], [judged([])],
        )
        code, payload, completed = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(FAIL_VALUE, completed.stdout + completed.stderr)

    def test_broken_judgment_returns_1_without_leaking_content(self):
        leaked = "SENTINEL-" + "BROKEN-CONTENT"
        inspection_id = self.make_workspace([], [judged([])])
        (self.workspace_dir(inspection_id) / "judgment" / "0001.json").write_text("{" + leaked, encoding="utf-8")
        code, payload, completed = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(leaked, completed.stdout + completed.stderr)

    def test_broken_pattern_json_returns_1_without_leaking_content(self):
        leaked = "SENTINEL-" + "PATTERN-CONTENT"
        inspection_id = self.make_workspace([], [judged([])])
        (self.workspace_dir(inspection_id) / "pattern.json").write_text("{" + leaked, encoding="utf-8")
        code, payload, completed = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(leaked, completed.stdout + completed.stderr)

    def test_missing_pattern_json_returns_1(self):
        inspection_id = self.make_workspace([], [judged([])])
        (self.workspace_dir(inspection_id) / "pattern.json").unlink()
        code, payload, _ = self.report(inspection_id)
        self.assertEqual(code, 1)
        self.assertIn("pattern.json", payload["errors"][0])


class NoFilesLeftTest(ReportTestCase):
    """SC-14: 結果を、作業領域の外のファイルに残さない（作業領域の中にも書かない）。"""

    def test_no_file_is_created_outside_or_inside_workspace(self):
        candidates = [candidate("P0001", "jwt", FAIL_VALUE), candidate("P0002", "domain", PASS_VALUE)]
        inspection_id = self.make_workspace(candidates, [judged([("P0001", "fail"), ("P0002", "pass")])])
        before_all = self.snapshot(self.tmpdir, self.home, self.root)

        for _ in range(2):
            code, _, _ = self.report(inspection_id)
            self.assertEqual(code, 0)

        self.assertEqual(self.snapshot(self.tmpdir, self.home, self.root), before_all)

    def test_failure_leaves_no_file_either(self):
        inspection_id = self.make_workspace([candidate("P0001", "domain", CONFIRM_VALUE)], [judged([("P0001", "confirm")])])
        before_all = self.snapshot(self.tmpdir, self.home, self.root)
        self.assertEqual(self.report(inspection_id)[0], 1)
        self.assertEqual(self.snapshot(self.tmpdir, self.home, self.root), before_all)

    def test_cwd_is_not_written(self):
        inspection_id = self.make_workspace([candidate("P0001", "jwt", FAIL_VALUE)], [judged([("P0001", "fail")])])
        cwd = self.root
        before = self.snapshot(cwd)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), inspection_id], capture_output=True, text=True, env=self.env(), cwd=str(cwd),
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(self.snapshot(cwd), before)


class ErrorOutputTest(ReportTestCase):
    """検査を実行できなかった場合のエラー出力（標準出力の errors・標準エラー）に値が含まれない。"""

    def test_stderr_and_errors_have_no_value_on_every_failure_path(self):
        value_candidates = [candidate("P0001", "jwt", FAIL_VALUE), candidate("P0002", "domain", CONFIRM_VALUE)]
        paths = {
            "confirm_remains": self.make_workspace(value_candidates, [judged([("P0001", "fail"), ("P0002", "confirm")])]),
            "no_judgment": self.make_workspace(value_candidates, []),
            "not_in_judgment": self.make_workspace(value_candidates, [judged([("P0001", "fail")])]),
        }
        for name, inspection_id in paths.items():
            with self.subTest(path=name):
                code, payload, completed = self.report(inspection_id)
                self.assertEqual(code, 1)
                self.assertEqual(list(payload), ["errors"])
                for value in (FAIL_VALUE, CONFIRM_VALUE):
                    self.assertNotIn(value, completed.stdout)
                    self.assertNotIn(value, completed.stderr)


class ArgumentTest(ReportTestCase):
    def test_missing_inspection_id_is_exit_2(self):
        self.assertEqual(self.run_report()[0], 2)

    def test_unknown_option_is_exit_2(self):
        self.assertEqual(self.run_report("0" * 32, "--bogus")[0], 2)

    def test_extra_positional_is_exit_2(self):
        self.assertEqual(self.run_report("0" * 32, "extra")[0], 2)


if __name__ == "__main__":
    unittest.main()
