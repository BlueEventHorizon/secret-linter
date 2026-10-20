#!/usr/bin/env python3
"""secret-linter の ai_finish.py・ai_check.py の契約テスト。

DES-087 のモジュール表 W-4（終了を確かめる）・W-7（終了の印を書く）と、テスト表 SC-06（2 回連続の拒否後は
終了の印が書かれず、ai_check.py が失敗すること）の検証に対応する。

- ai_finish: ai_done.json を {"exit": "0"} だけの内容で書く。標準出力は {}。受理が 0 件でも書く。
  作業領域が無い・拒否が 2 回続いた後・既に印がある、のとき 1 で、印は書かない（上書きしない）。
  受理が 1 件あれば連続拒否の数が 0 に戻り、書ける
- ai_check: 印が無い・壊れている・内容が違う・作業領域が無いとき 1、正常なとき 0

実行:
  python3 -m unittest tests.test_ai_finish_check -v
"""

import importlib.util
import json
import unittest
from pathlib import Path

_support_spec = importlib.util.spec_from_file_location(
    "secret_linter_ai_support", Path(__file__).resolve().parent / "ai_support.py"
)
support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(support)

VALUE_A = support.VALUE_A


class FinishTest(support.AiTestCase):
    def done_path(self, iid):
        return self.place(iid) / "ai_done.json"

    def test_writes_done_mark_with_exit_zero_only(self):
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        code, payload, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual((code, payload), (0, {}))
        self.assertEqual(
            json.loads(self.done_path(iid).read_text(encoding="utf-8")), {"exit": "0"}
        )

    def test_writes_done_mark_when_nothing_was_reported(self):
        iid = self.make_workspace()
        code, _, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual(code, 0)
        self.assertTrue(self.done_path(iid).is_file())

    def test_refused_after_two_consecutive_rejections_and_no_mark_is_written(self):
        # SC-06: 拒否が 2 回続いた後の ai_finish.py の拒否
        iid = self.make_workspace()
        self.reject_once(iid)
        self.reject_once(iid)
        code, payload, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertFalse(self.done_path(iid).exists())

    def test_refused_after_two_rejections_even_if_earlier_candidates_were_accepted(
        self,
    ):
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        self.reject_once(iid)
        self.reject_once(iid)
        code, _, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual(code, 1)
        self.assertFalse(self.done_path(iid).exists())

    def test_one_rejection_does_not_block_finish(self):
        iid = self.make_workspace()
        self.reject_once(iid)
        code, _, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual(code, 0)

    def test_acceptance_resets_the_consecutive_count_for_finish(self):
        # 拒否 1 回 → 受理 → 拒否 1 回 は、連続 1 回なので書ける
        iid = self.make_workspace()
        self.reject_once(iid)
        self.add(iid, VALUE_A)
        self.reject_once(iid)
        code, _, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual(code, 0)

    def test_does_not_overwrite_existing_mark(self):
        iid = self.make_workspace()
        self.assertEqual(self.run_script("ai_finish.py", iid)[0], 0)
        code, payload, _, _ = self.run_script("ai_finish.py", iid)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertEqual(
            json.loads(self.done_path(iid).read_text(encoding="utf-8")), {"exit": "0"}
        )

    def test_missing_workspace_exits_1(self):
        code, payload, _, _ = self.run_script("ai_finish.py", "a" * 32)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_invalid_inspection_id_exits_1(self):
        code, payload, _, _ = self.run_script("ai_finish.py", "not-an-id")
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_missing_argument_exits_2(self):
        code, _, stdout, _ = self.run_script("ai_finish.py")
        self.assertEqual(code, 2)
        self.assertEqual(stdout.strip(), "")


class CheckTest(support.AiTestCase):
    def test_succeeds_when_mark_is_present(self):
        iid = self.make_workspace()
        self.run_script("ai_finish.py", iid)
        code, payload, _, _ = self.run_script("ai_check.py", iid)
        self.assertEqual((code, payload), (0, {}))

    def test_fails_when_mark_is_absent(self):
        # 終了の印が無いとき、1 を返す
        iid = self.make_workspace()
        code, payload, _, _ = self.run_script("ai_check.py", iid)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_fails_after_two_consecutive_rejections(self):
        # SC-06: 2 回連続の拒否後は印が書かれず、ai_check.py が失敗する
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        self.reject_once(iid)
        self.reject_once(iid)
        self.run_script("ai_finish.py", iid)
        code, _, _, _ = self.run_script("ai_check.py", iid)
        self.assertEqual(code, 1)

    def test_fails_when_mark_is_broken_json(self):
        iid = self.make_workspace()
        (self.place(iid) / "ai_done.json").write_text("{not json", encoding="utf-8")
        code, payload, _, _ = self.run_script("ai_check.py", iid)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_fails_when_mark_content_differs(self):
        for content in ({"exit": "1"}, {"exit": "0", "extra": 1}, {}, [], "0"):
            with self.subTest(content=content):
                iid = self.make_workspace()
                (self.place(iid) / "ai_done.json").write_text(
                    json.dumps(content), encoding="utf-8"
                )
                code, _, _, _ = self.run_script("ai_check.py", iid)
                self.assertEqual(code, 1)

    def test_fails_when_workspace_is_missing(self):
        code, payload, _, _ = self.run_script("ai_check.py", "a" * 32)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_fails_for_invalid_inspection_id(self):
        code, _, _, _ = self.run_script("ai_check.py", "not-an-id")
        self.assertEqual(code, 1)

    def test_result_depends_only_on_the_mark(self):
        # 受理した候補があっても、印が無ければ失敗する（AI の応答や候補の有無は見ない）
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        self.assertEqual(self.run_script("ai_check.py", iid)[0], 1)

    def test_error_does_not_contain_candidate_value(self):
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        _, _, stdout, stderr = self.run_script("ai_check.py", iid)
        self.assertNotIn(VALUE_A, stdout)
        self.assertNotIn(VALUE_A, stderr)

    def test_missing_argument_exits_2(self):
        code, _, stdout, _ = self.run_script("ai_check.py")
        self.assertEqual(code, 2)
        self.assertEqual(stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
