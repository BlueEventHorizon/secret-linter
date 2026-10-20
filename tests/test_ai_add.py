#!/usr/bin/env python3
"""secret-linter の ai_add.py の契約テスト。

DES-087 のモジュール表 W-3 と、テスト表 SC-06（本文と対応しない報告の拒否、2 回連続の拒否後に終了の印が
書かれないこと）の検証に対応する。

- 受理: file・added_lines・commit_message の各 kind で、--line 有り・無し。candidate_id（A0001 形式）・
  source・line・commit が書かれ、標準出力は受理した件数
- 拒否: unit_id が無い・値が本文に含まれない・--line の行に含まれない・--line の行が無い・空の値。
  拒否は ai_events に連番で書かれ、終了コード 1。kind が語彙外・--line が整数でないときは argparse の 2
- 連続拒否: 2 回続いた後の ai_add は 1 で何も書かない。受理が 1 件あれば連続拒否の数が 0 に戻る
- 値が標準出力に出ない。書いたファイルを上書きしない（連番）

実行:
  python3 -m unittest tests.test_ai_add -v
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
VALUE_B = support.VALUE_B


class AcceptTest(support.AiTestCase):
    def test_accepts_file_value_without_line(self):
        iid = self.make_workspace()
        code, payload, _, _ = self.add(iid, VALUE_A)
        self.assertEqual((code, payload), (0, {"accepted_count": 1}))
        self.assertEqual(
            self.events(iid),
            [
                {
                    "result": "accepted",
                    "candidate": {
                        "candidate_id": "A0001",
                        "source": "ai",
                        "unit_id": "U0001",
                        "line": None,
                        "kind": "person_name",
                        "value": VALUE_A,
                    },
                }
            ],
        )

    def test_accepts_file_value_with_line(self):
        iid = self.make_workspace()
        code, payload, _, _ = self.add(iid, VALUE_A, line=2)
        self.assertEqual((code, payload), (0, {"accepted_count": 1}))
        self.assertEqual(self.events(iid)[0]["candidate"]["line"], 2)

    def test_accepts_added_lines_with_original_line_number(self):
        # added_lines の line は、検査単位の line_numbers の対応する元のファイルの行番号
        iid = self.make_workspace()
        code, _, _, _ = self.add(
            iid, VALUE_B, unit_id="U0002", kind="company_name", line=25
        )
        self.assertEqual(code, 0)
        candidate = self.events(iid)[0]["candidate"]
        self.assertEqual((candidate["line"], candidate["commit"]), (25, "COMMIT-ID-1"))

    def test_accepts_added_lines_without_line(self):
        iid = self.make_workspace()
        code, _, _, _ = self.add(iid, VALUE_B, unit_id="U0002", kind="company_name")
        self.assertEqual(code, 0)
        self.assertEqual(self.events(iid)[0]["candidate"]["commit"], "COMMIT-ID-1")

    def test_accepts_commit_message_with_line(self):
        iid = self.make_workspace()
        code, _, _, _ = self.add(
            iid, VALUE_B, unit_id="U0003", kind="resource_name", line=3
        )
        self.assertEqual(code, 0)
        candidate = self.events(iid)[0]["candidate"]
        self.assertEqual(
            (candidate["line"], candidate["commit"], candidate["kind"]),
            (3, "COMMIT-ID-2", "resource_name"),
        )

    def test_accepts_commit_message_without_line(self):
        iid = self.make_workspace()
        code, _, _, _ = self.add(iid, VALUE_B, unit_id="U0003", kind="other_ai")
        self.assertEqual(code, 0)

    def test_file_candidate_has_no_commit_key(self):
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        self.assertNotIn("commit", self.events(iid)[0]["candidate"])

    def test_candidate_ids_are_sequential_and_files_are_not_overwritten(self):
        iid = self.make_workspace()
        self.add(iid, VALUE_A)
        code, payload, _, _ = self.add(iid, "first line")
        self.assertEqual((code, payload), (0, {"accepted_count": 2}))
        events_dir = self.place(iid) / "ai_events"
        self.assertEqual(
            sorted(p.name for p in events_dir.glob("*.json")),
            ["0001.json", "0002.json"],
        )
        ids = [e["candidate"]["candidate_id"] for e in self.events(iid)]
        self.assertEqual(ids, ["A0001", "A0002"])
        first = json.loads((events_dir / "0001.json").read_text(encoding="utf-8"))
        self.assertEqual(first["candidate"]["value"], VALUE_A)

    def test_trailing_newline_is_not_part_of_value(self):
        iid = self.make_workspace()
        code, _, _, _ = self.run_script(
            "ai_add.py",
            iid,
            "--unit-id",
            "U0001",
            "--kind",
            "person_name",
            stdin=VALUE_A + "\r\n",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.events(iid)[0]["candidate"]["value"], VALUE_A)

    def test_value_is_not_printed_to_stdout(self):
        iid = self.make_workspace()
        _, _, stdout, stderr = self.add(iid, VALUE_A)
        self.assertNotIn(VALUE_A, stdout)
        self.assertNotIn(VALUE_A, stderr)

    def test_stdout_is_single_json_object(self):
        iid = self.make_workspace()
        _, _, stdout, _ = self.add(iid, VALUE_A)
        self.assertEqual(len(stdout.strip().splitlines()), 1)


class RejectTest(support.AiTestCase):
    def assert_rejected(self, iid, result, reason, value):
        code, payload, stdout, stderr = result
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        # 値は、標準出力にも errors にも出ない
        self.assertNotIn(value, stdout)
        self.assertNotIn(value, stderr)
        event = self.events(iid)[-1]
        self.assertEqual((event["result"], event["reason"]), ("rejected", reason))
        self.assertNotIn(value, json.dumps(event, ensure_ascii=False))
        self.assertNotIn("candidate", event)

    def test_rejects_value_not_in_text(self):
        # SC-06: 本文に含まれない値の報告が、拒否される
        iid = self.make_workspace()
        value = "INVENTED-VALUE-PLACEHOLDER"
        self.assert_rejected(iid, self.add(iid, value), "value_not_in_text", value)

    def test_rejects_value_not_in_the_given_line(self):
        iid = self.make_workspace()
        # VALUE_A は 2 行目にある。1 行目を指定したら拒否
        self.assert_rejected(
            iid, self.add(iid, VALUE_A, line=1), "value_not_in_text", VALUE_A
        )

    def test_rejects_value_in_other_unit_text(self):
        iid = self.make_workspace()
        self.assert_rejected(
            iid, self.add(iid, VALUE_B, unit_id="U0001"), "value_not_in_text", VALUE_B
        )

    def test_rejects_unknown_unit_id(self):
        iid = self.make_workspace()
        self.assert_rejected(
            iid, self.add(iid, VALUE_A, unit_id="U9999"), "unit_not_found", VALUE_A
        )

    def test_rejects_line_that_does_not_exist(self):
        iid = self.make_workspace()
        self.assert_rejected(
            iid, self.add(iid, VALUE_A, line=99), "line_not_in_unit", VALUE_A
        )

    def test_rejects_added_lines_line_by_body_position(self):
        # added_lines では本文の 2 行目は元の行番号 25。2 は存在しない行番号として拒否される
        iid = self.make_workspace()
        self.assert_rejected(
            iid,
            self.add(iid, VALUE_B, unit_id="U0002", line=2),
            "line_not_in_unit",
            VALUE_B,
        )

    def test_rejects_empty_value(self):
        iid = self.make_workspace()
        result = self.run_script(
            "ai_add.py", iid, "--unit-id", "U0001", "--kind", "person_name", stdin=""
        )
        code, payload, _, _ = result
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertEqual(self.events(iid)[-1]["reason"], "empty_value")

    def test_rejects_newline_only_value(self):
        iid = self.make_workspace()
        code, _, _, _ = self.run_script(
            "ai_add.py", iid, "--unit-id", "U0001", "--kind", "person_name", stdin="\n"
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.events(iid)[-1]["reason"], "empty_value")

    def test_rejection_does_not_write_a_candidate(self):
        iid = self.make_workspace()
        self.reject_once(iid)
        self.assertEqual([e["result"] for e in self.events(iid)], ["rejected"])

    def test_kind_outside_vocabulary_exits_2_and_writes_nothing(self):
        iid = self.make_workspace()
        code, _, _, _ = self.run_script(
            "ai_add.py",
            iid,
            "--unit-id",
            "U0001",
            "--kind",
            "known_token",
            stdin=VALUE_A,
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.events(iid), [])

    def test_non_integer_line_exits_2(self):
        iid = self.make_workspace()
        code, _, _, _ = self.run_script(
            "ai_add.py",
            iid,
            "--unit-id",
            "U0001",
            "--kind",
            "person_name",
            "--line",
            "x",
            stdin=VALUE_A,
        )
        self.assertEqual(code, 2)

    def test_missing_workspace_exits_1(self):
        code, payload, _, _ = self.add("a" * 32, VALUE_A)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])

    def test_invalid_inspection_id_exits_1(self):
        code, payload, _, _ = self.add("not-an-id", VALUE_A)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])


class ConsecutiveRejectionTest(support.AiTestCase):
    def test_add_is_refused_after_two_consecutive_rejections_and_writes_nothing(self):
        # SC-06: 拒否が 2 回続いた後の ai_add.py の拒否
        iid = self.make_workspace()
        self.reject_once(iid)
        self.reject_once(iid)
        before = self.events(iid)
        self.assertEqual(len(before), 2)
        # 本文に含まれる正しい値でも拒否され、何も書かれない
        code, payload, stdout, _ = self.add(iid, VALUE_A)
        self.assertEqual(code, 1)
        self.assertTrue(payload["errors"])
        self.assertNotIn(VALUE_A, stdout)
        self.assertEqual(self.events(iid), before)
        self.assertFalse((self.place(iid) / "ai_done.json").exists())

    def test_one_rejection_then_retry_is_accepted(self):
        # 1 回だけ報告し直せる
        iid = self.make_workspace()
        self.reject_once(iid)
        code, payload, _, _ = self.add(iid, VALUE_A)
        self.assertEqual((code, payload), (0, {"accepted_count": 1}))

    def test_acceptance_resets_the_consecutive_count(self):
        # 受理が 1 件あれば、連続拒否の数が 0 に戻る
        iid = self.make_workspace()
        self.reject_once(iid)
        self.assertEqual(self.add(iid, VALUE_A)[0], 0)
        self.reject_once(iid)
        # 拒否 1 回 → 受理 → 拒否 1 回。連続は 1 なので、まだ受け付ける
        code, payload, _, _ = self.add(iid, "first line")
        self.assertEqual((code, payload), (0, {"accepted_count": 2}))
        self.assertEqual(
            [e["result"] for e in self.events(iid)],
            ["rejected", "accepted", "rejected", "accepted"],
        )

    def test_two_rejections_separated_by_acceptance_do_not_lock(self):
        iid = self.make_workspace()
        self.reject_once(iid)
        self.add(iid, VALUE_A)
        self.reject_once(iid)
        code, _, _, _ = self.add(iid, "third line")
        self.assertEqual(code, 0)

    def test_locks_again_after_reset_when_two_more_rejections_follow(self):
        iid = self.make_workspace()
        self.reject_once(iid)
        self.add(iid, VALUE_A)
        self.reject_once(iid)
        self.reject_once(iid)
        code, _, _, _ = self.add(iid, "third line")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.events(iid)), 4)


class CandidateIdTest(unittest.TestCase):
    """AI の候補の ``candidate_id`` の形（接頭辞と桁数）は ``ai_events`` が 1 か所だけに持つ。"""

    def test_candidate_id_is_a_prefix_and_four_digits(self):
        import sys

        sys.path.insert(
            0,
            str(
                Path(__file__).resolve().parents[1]
                / "linter"
                / "skills"
                / "secret-linter"
                / "scripts"
            ),
        )
        import ai_events

        self.assertEqual(ai_events.AI_CANDIDATE_PREFIX, "A")
        self.assertEqual(ai_events.candidate_id(1), "A0001")
        self.assertEqual(ai_events.candidate_id(12345), "A12345")


if __name__ == "__main__":
    unittest.main()
