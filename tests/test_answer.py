#!/usr/bin/env python3
"""secret-linter の answer.py（回答の記録の CLI）と、再判定の引き継ぎ（judgment.rejudge）の契約テスト。

DES-087 のモジュール表 L-2・L-3・J-1・W-2 と、テスト表 SC-08・SC-09 のスクリプトの検証に対応する。
リストへの追記そのものは test_lists_write.py、判定の規則は test_judgment.py が検証する。

- 各回答の動作: secret は機密の名前リストへ、public-keep は許可リストへ追記し、public-once は何も追記しない
- 追記の前後で既存の項目・コメントが失われない。ファイルが無ければ作成する（SC-09）。同じ値は重複して追記しない
- 回答を反映した再判定: secret → 不合格、public-keep・public-once → 通過。judgment の次の連番が書かれる
- public-once は、同じ検査の中の後続の answer.py の呼び出しでも有効であり続ける（リストには何も残らない）
- メールアドレスへの public-keep は許可リストへ追記される（完全一致の項目）。選択肢外の回答・実在しない候補 ID・形式不正・重複・要確認でない候補の拒否
  （終了コード 1。リストが変わらない。全ての回答を検証してから書く）
- リストの書式で表せない値の拒否。形式不正な既存のリストの拒否（行番号だけ。ファイルが変わらない）
- 標準出力が judge.py と同じ形であること。値が errors に出ないこと
- 終了コード 0・1・2

作業領域は OS の一時ディレクトリに作られる。テストは、TMPDIR・HOME をテストごとの専用ディレクトリへ向け、
実際のホームを読まない・書かない。入力は明白なプレースホルダだけを使う。機密らしい値は連結して組み立てる。

実行:
  python3 -m unittest tests.forge.secret-linter.test_answer -v
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
ANSWER = _SCRIPTS_DIR / "answer.py"
JUDGE = _SCRIPTS_DIR / "judge.py"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lists  # noqa: E402
import vocab  # noqa: E402
import workspace  # noqa: E402

#: 予約値の語。run_script は予約値の定数を空にした状態で script を走らせるので、予約値ではない値として扱われる
HOST_A = "docs.example.com"
HOST_B = "wiki.example.com"
EMAIL = "someone@" + HOST_A
#: 同梱の許可リストにある実在ドメイン（予約値の定数が空でも pass になる）
PASS_HOST = "docs.python.org"
FAIL_VALUE = "PLACEHOLDER-" + "FAIL-" + "VALUE"
BOTH_QUOTES = "bad\"name" + "'x" + ".io"

#: judge.py と answer.py が返す標準出力のキー
SUMMARY_KEYS = ["class", "confirm", "fail_count", "pass_count", "skipped", "suppressed"]


def candidate(cid, kind, value, line=1, unit_id="U0001"):
    return {"candidate_id": cid, "source": "pattern", "unit_id": unit_id, "line": line, "kind": kind, "value": value}


class AnswerTestCase(unittest.TestCase):
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
        self.names_file = self.home / ".project-secret.yaml"
        self.allow_file = self.root / ".forge" / "secret_allowlist.yaml"

    def env(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.tmpdir)
        env["HOME"] = str(self.home)
        env.pop("TEMP", None)
        env.pop("TMP", None)
        return env

    def make_workspace(self, candidates, judged=True):
        """作業領域を作り、``judge.py --rules-only`` で最初の判定（0001）まで書く。検査 ID を返す。

        AI の候補を含む判定は test_judge_ai.py が検証する（``--rules-only`` の判定の後の answer.py は、
        AI の候補を含めない）。"""
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            inspection_id = workspace.create()
            workspace.write(inspection_id, "units.json", {
                "units": [{"unit_id": "U0001", "kind": "file", "label": "docs/a.md", "text_path": "/dev/null"}],
                "skipped": [{"label": "big.bin", "reason": "too large"}],
            })
            workspace.write(inspection_id, "pattern.json", {
                "candidates": candidates,
                "suppressed": {"count": 0, "locations": []},
                "filtered": {"count": 0, "reasons": {}},
            })
        if judged:
            code, payload, _ = self.run_script(JUDGE, inspection_id, "--project-root", str(self.root), "--rules-only")
            self.assertEqual(code, 0, payload)
        return inspection_id

    def workspace_dir(self, inspection_id):
        return self.tmpdir / f"{workspace.PLACE_PREFIX}{inspection_id}"

    def judgments(self, inspection_id):
        directory = self.workspace_dir(inspection_id) / "judgment"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.iterdir())]

    def run_script(self, script, *args):
        completed = subprocess.run(
            vocab.argv(script, *args, no_reserved=True), capture_output=True, text=True, env=self.env(),
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else None
        return completed.returncode, payload, completed

    def answer(self, inspection_id, *pairs):
        args = [inspection_id, "--project-root", str(self.root)]
        for pair in pairs:
            args += ["--answer", pair]
        return self.run_script(ANSWER, *args)

    def snapshot(self):
        """リスト 2 つの現状（無ければ None）。"""
        return tuple(
            p.read_bytes() if p.exists() else None for p in (self.names_file, self.allow_file)
        )


class RecordTest(AnswerTestCase):
    def test_secret_appends_to_secret_names_and_judges_fail(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])

        code, payload, completed = self.answer(iid, "P0001=secret")

        self.assertEqual(code, 0)
        self.assertEqual((payload["class"], payload["confirm"], payload["fail_count"]), ("fail", [], 1))
        self.assertEqual(lists.load_secret_names(self.home), [HOST_A])
        self.assertFalse(self.allow_file.exists())
        self.assertNotIn(HOST_A, completed.stdout)  # 不合格の値は標準出力に出ない

    def test_public_keep_appends_to_project_allowlist_and_judges_pass(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])

        code, payload, _ = self.answer(iid, "P0001=public-keep")

        self.assertEqual(code, 0)
        self.assertEqual((payload["class"], payload["confirm"], payload["pass_count"]), ("pass", [], 1))
        self.assertEqual(lists.load_project_allowlist(self.root), [HOST_A])
        self.assertFalse(self.names_file.exists())

    def test_public_once_appends_nothing_and_judges_pass(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        before = self.snapshot()

        code, payload, _ = self.answer(iid, "P0001=public-once")

        self.assertEqual((code, payload["class"], payload["pass_count"]), (0, "pass", 1))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.names_file.exists())
        self.assertFalse(self.allow_file.exists())
        self.assertFalse((self.root / ".forge").exists())

    def test_public_once_email_is_accepted(self):
        iid = self.make_workspace([candidate("P0001", "email", EMAIL)])
        code, payload, _ = self.answer(iid, "P0001=public-once")
        self.assertEqual((code, payload["class"]), (0, "pass"))
        self.assertFalse(self.allow_file.exists())

    def test_secret_email_is_accepted_and_fails(self):
        iid = self.make_workspace([candidate("P0001", "email", EMAIL)])
        code, payload, _ = self.answer(iid, "P0001=secret")
        self.assertEqual((code, payload["class"]), (0, "fail"))
        self.assertEqual(lists.load_secret_names(self.home), [EMAIL])

    def test_public_keep_email_appends_the_address_to_project_allowlist_and_judges_pass(self):
        iid = self.make_workspace([candidate("P0001", "email", EMAIL)])

        code, payload, _ = self.answer(iid, "P0001=public-keep")

        self.assertEqual((code, payload["class"], payload["confirm"], payload["pass_count"]), (0, "pass", [], 1))
        self.assertEqual(lists.load_project_allowlist(self.root), [EMAIL])
        self.assertFalse(self.names_file.exists())
        self.assertEqual(self.judgments(iid)[-1]["candidates"][0]["reason"], "allowlist_project")

    def test_email_shaped_value_of_another_kind_can_be_kept(self):
        # メールアドレスの規則は値の性質で定まる（種別が person_name 等でも同じ）
        iid = self.make_workspace([candidate("P0001", "person_name", EMAIL)])
        code, payload, _ = self.answer(iid, "P0001=public-keep")
        self.assertEqual((code, payload["class"]), (0, "pass"))
        self.assertEqual(lists.load_project_allowlist(self.root), [EMAIL])

    def test_kept_email_passes_on_a_fresh_judge_but_not_the_domain(self):
        iid = self.make_workspace([candidate("P0001", "email", EMAIL)])
        self.answer(iid, "P0001=public-keep")

        again = self.make_workspace([
            candidate("P0001", "email", EMAIL), candidate("P0002", "domain", HOST_A, line=2),
        ])
        classes = [c["class"] for c in self.judgments(again)[-1]["candidates"]]
        self.assertEqual(classes, ["pass", "confirm"])

    def test_several_answers_in_one_call(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A, line=1),
            candidate("P0002", "domain", HOST_B, line=2),
            candidate("P0003", "email", EMAIL, line=3),
        ])

        code, payload, _ = self.answer(iid, "P0001=public-keep", "P0002=secret", "P0003=public-once")

        self.assertEqual((code, payload["class"]), (0, "fail"))
        self.assertEqual(lists.load_project_allowlist(self.root), [HOST_A])
        self.assertEqual(lists.load_secret_names(self.home), [HOST_B])
        final = self.judgments(iid)[-1]
        self.assertEqual([(c["candidate_id"], c["class"]) for c in final["candidates"]],
                         [("P0001", "pass"), ("P0002", "fail"), ("P0003", "pass")])

    def test_next_judgment_number_is_written_each_call(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A), candidate("P0002", "domain", HOST_B, line=2),
        ])
        self.assertEqual(sorted(p.name for p in (self.workspace_dir(iid) / "judgment").iterdir()), ["0001.json"])
        first = json.loads((self.workspace_dir(iid) / "judgment" / "0001.json").read_text(encoding="utf-8"))

        self.answer(iid, "P0001=public-keep")
        self.answer(iid, "P0002=public-keep")

        directory = self.workspace_dir(iid) / "judgment"
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["0001.json", "0002.json", "0003.json"])
        # 0001（回答を反映する前の判定）は書き換えられない
        self.assertEqual(json.loads((directory / "0001.json").read_text(encoding="utf-8")), first)
        self.assertEqual(self.judgments(iid)[-1]["class"], "pass")


class ExistingListTest(AnswerTestCase):
    def test_existing_items_and_comments_are_kept(self):
        original = "# 機密の名前\n\n- existing-name\n"
        self.names_file.write_text(original, encoding="utf-8")
        allow_original = "# 許可\n- keep-this.example.com\n"
        self.allow_file.parent.mkdir()
        self.allow_file.write_text(allow_original, encoding="utf-8")
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A), candidate("P0002", "domain", HOST_B, line=2),
        ])

        code, _, _ = self.answer(iid, "P0001=secret", "P0002=public-keep")

        self.assertEqual(code, 0)
        self.assertEqual(self.names_file.read_text(encoding="utf-8"), original + f'- "{HOST_A}"\n')
        self.assertEqual(self.allow_file.read_text(encoding="utf-8"), allow_original + f'- "{HOST_B}"\n')

    def test_missing_files_and_directory_are_created(self):
        # SC-09: ~/.project-secret.yaml が無い間は空として扱い、最初の「機密である」で作成する
        self.assertFalse(self.names_file.exists())
        self.assertFalse((self.root / ".forge").exists())
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A), candidate("P0002", "domain", HOST_B, line=2),
        ])
        self.assertFalse(self.names_file.exists())  # 判定だけでは作成しない

        self.answer(iid, "P0001=secret", "P0002=public-keep")

        self.assertTrue(self.names_file.exists())
        self.assertTrue(self.allow_file.exists())
        self.assertEqual(lists.load_secret_names(self.home), [HOST_A])
        self.assertEqual(lists.load_project_allowlist(self.root), [HOST_B])

    def test_same_value_is_appended_once(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A, line=1), candidate("P0002", "domain", HOST_A, line=5),
        ])
        code, payload, _ = self.answer(iid, "P0001=secret", "P0002=secret")
        self.assertEqual((code, payload["class"]), (0, "fail"))
        self.assertEqual(lists.load_secret_names(self.home), [HOST_A])

    def test_appended_value_is_matched_by_the_judgment_rule_on_a_fresh_judge(self):
        # 追記した項目が、その値に対して判定の規則で一致する（別の検査でも、secret → 不合格、public-keep → 通過）
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A), candidate("P0002", "domain", HOST_B, line=2)])
        self.answer(iid, "P0001=secret", "P0002=public-keep")

        again = self.make_workspace([candidate("P0001", "domain", HOST_A), candidate("P0002", "domain", HOST_B, line=2)])
        classes = [c["class"] for c in self.judgments(again)[-1]["candidates"]]
        self.assertEqual(classes, ["fail", "pass"])


class PublicOnceCarryOverTest(AnswerTestCase):
    def test_public_once_stays_valid_in_a_later_call(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A, line=1), candidate("P0002", "domain", HOST_B, line=2),
        ])

        code, payload, _ = self.answer(iid, "P0001=public-once")
        self.assertEqual((code, payload["class"]), (0, "confirm"))
        self.assertEqual([c["candidate_id"] for c in payload["confirm"]], ["P0002"])

        code, payload, _ = self.answer(iid, "P0002=public-keep")
        self.assertEqual((code, payload["class"], payload["confirm"]), (0, "pass", []))
        final = self.judgments(iid)[-1]
        self.assertEqual(
            [(c["candidate_id"], c["class"], c["reason"]) for c in final["candidates"]],
            [("P0001", "pass", "answered_public_once"), ("P0002", "pass", "allowlist_project")],
        )
        # 今回だけの値は、どのリストにも残らない
        self.assertEqual(lists.load_project_allowlist(self.root), [HOST_B])
        self.assertEqual(lists.load_secret_names(self.home), [])

    def test_public_once_is_not_carried_into_a_new_inspection(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        self.answer(iid, "P0001=public-once")

        again = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        self.assertEqual(self.judgments(again)[-1]["class"], "confirm")

    def test_later_secret_answer_overrides_a_carried_public_once(self):
        # 同じ値の別の候補が後で「機密である」と回答されたら、引き継いだ今回だけより不合格が優先される
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A, line=1), candidate("P0002", "domain", HOST_A, line=5),
        ])
        self.answer(iid, "P0001=public-once")

        code, payload, _ = self.answer(iid, "P0002=secret")

        self.assertEqual((code, payload["class"], payload["fail_count"]), (0, "fail", 2))
        self.assertEqual([c["class"] for c in self.judgments(iid)[-1]["candidates"]], ["fail", "fail"])

    def test_carried_candidates_do_not_hide_remaining_confirm(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A, line=1),
            candidate("P0002", "domain", HOST_B, line=2),
            candidate("P0003", "email", EMAIL, line=3),
        ])
        self.answer(iid, "P0001=public-once")
        code, payload, _ = self.answer(iid, "P0002=public-once")
        self.assertEqual((code, payload["class"]), (0, "confirm"))
        self.assertEqual([c["candidate_id"] for c in payload["confirm"]], ["P0003"])
        self.assertEqual(payload["pass_count"], 2)


class OutputShapeTest(AnswerTestCase):
    def test_same_shape_as_judge(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A, line=1),
            candidate("P0002", "domain", HOST_B, line=7),
            candidate("P0003", "jwt", FAIL_VALUE, line=3),
        ])
        _, judged, _ = self.run_script(JUDGE, iid, "--project-root", str(self.root), "--rules-only")

        code, payload, completed = self.answer(iid, "P0001=public-once")

        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload), SUMMARY_KEYS)
        self.assertEqual(sorted(judged), SUMMARY_KEYS)
        self.assertEqual(payload["class"], "fail")
        # 残った要確認の候補は、judge.py が返したものと同じ形・同じ内容
        self.assertEqual(payload["confirm"], [c for c in judged["confirm"] if c["candidate_id"] == "P0002"])
        self.assertEqual(payload["confirm"][0], {
            "candidate_id": "P0002", "unit_id": "U0001", "label": "docs/a.md", "line": 7,
            "kind": "domain", "value": HOST_B,
        })
        self.assertEqual(payload["skipped"], [{"label": "big.bin", "reason": "too large"}])
        self.assertEqual(payload["suppressed"], {"count": 0, "locations": []})
        self.assertEqual((payload["pass_count"], payload["fail_count"]), (1, 1))
        self.assertNotIn(FAIL_VALUE, completed.stdout)  # fail の値は出さない
        self.assertNotIn(HOST_A, completed.stdout)  # pass の値は出さない
        self.assertIn(HOST_B, completed.stdout)  # 要確認の値は質問のために返す


class RejectionTest(AnswerTestCase):
    def assert_rejected(self, iid, *pairs, before=None):
        before = before if before is not None else self.snapshot()
        code, payload, completed = self.answer(iid, *pairs)
        self.assertEqual(code, 1, completed.stdout)
        self.assertEqual(list(payload), ["errors"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(self.judgments(iid)), 1)  # judgment の次の連番は書かれない
        for value in (HOST_A, HOST_B, EMAIL, FAIL_VALUE, BOTH_QUOTES):
            self.assertNotIn(value, completed.stdout + completed.stderr)
        return payload

    def test_answer_outside_the_choices(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        self.assert_rejected(iid, "P0001=maybe")
        self.assert_rejected(iid, "P0001=")
        self.assert_rejected(iid, "P0001=SECRET")

    def test_malformed_answer_argument(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        self.assert_rejected(iid, "P0001")
        self.assert_rejected(iid, "=secret")
        self.assert_rejected(iid, "secret")

    def test_nonexistent_candidate_id(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        self.assert_rejected(iid, "P9999=secret")

    def test_duplicate_candidate_id(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        self.assert_rejected(iid, "P0001=secret", "P0001=public-keep")

    def test_candidate_that_is_not_confirm(self):
        iid = self.make_workspace([
            candidate("P0001", "jwt", FAIL_VALUE), candidate("P0002", "domain", PASS_HOST, line=2),
        ])
        self.assert_rejected(iid, "P0001=public-keep")
        self.assert_rejected(iid, "P0002=secret")

    def test_one_invalid_answer_writes_nothing_for_the_valid_ones(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A), candidate("P0002", "email", EMAIL, line=2),
        ])
        self.assert_rejected(iid, "P0001=secret", "P0002=maybe")
        self.assert_rejected(iid, "P0001=secret", "P9999=secret")
        self.assertFalse(self.names_file.exists())

    def test_value_the_list_format_cannot_express(self):
        iid = self.make_workspace([
            candidate("P0001", "domain", BOTH_QUOTES), candidate("P0002", "domain", "line1\nline2.example.com", line=2),
        ])
        for pair in ("P0001=secret", "P0001=public-keep", "P0002=secret", "P0002=public-keep"):
            with self.subTest(answer=pair):
                self.assert_rejected(iid, pair)
        # 表せない値でも、今回だけは何も書かないので受理される
        code, _, _ = self.answer(iid, "P0001=public-once")
        self.assertEqual(code, 0)

    def test_value_that_the_rule_would_not_match_is_not_appended(self):
        # 許可リストの項目は、`*.` で始まるとドメイン指定になり、その値自身には一致しない。書かずに拒否する
        iid = self.make_workspace([candidate("P0001", "domain", "*." + HOST_A)])
        self.assert_rejected(iid, "P0001=public-keep")

    def test_invalid_existing_secret_list_is_rejected_with_line_number_only(self):
        leaked = "SENTINEL-" + "NAME-CONTENT"
        self.names_file.write_text(f"# comment\n- ok-name\n  - {leaked}\n", encoding="utf-8")
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)], judged=False)
        # 判定の前に形式不正なリストがあるため、judge.py が 1 を返す。判定ファイルを手で用意する
        workspace_dir = self.workspace_dir(iid)
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            workspace.write_next(iid, "judgment", {
                "candidates": [{"candidate_id": "P0001", "class": "confirm", "reason": "confirm_kind"}],
                "class": "confirm",
            })
        before = self.snapshot()

        code, payload, completed = self.answer(iid, "P0001=public-keep")

        self.assertEqual(code, 1)
        self.assertIn("3 行目", payload["errors"][0])
        self.assertNotIn(leaked, completed.stdout + completed.stderr)
        self.assertNotIn(HOST_A, completed.stdout + completed.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted(p.name for p in (workspace_dir / "judgment").iterdir()), ["0001.json"])

    def test_invalid_existing_project_list_is_rejected_and_unchanged(self):
        leaked = "SENTINEL-" + "LINE-CONTENT"
        self.allow_file.parent.mkdir()
        self.allow_file.write_text(f"- ok.example.com\nkey: {leaked}\n", encoding="utf-8")
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)], judged=False)
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            workspace.write_next(iid, "judgment", {
                "candidates": [{"candidate_id": "P0001", "class": "confirm", "reason": "confirm_kind"}],
                "class": "confirm",
            })
        before = self.snapshot()

        code, payload, completed = self.answer(iid, "P0001=secret")  # 機密の名前リストも書かれてはならない

        self.assertEqual(code, 1)
        self.assertIn("2 行目", payload["errors"][0])
        self.assertNotIn(leaked, completed.stdout + completed.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.names_file.exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root は書き込み権限の拒否を再現できない")
    def test_unwritable_list_returns_1_without_the_value(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)])
        os.chmod(self.home, 0o500)
        self.addCleanup(os.chmod, self.home, 0o700)

        code, payload, completed = self.answer(iid, "P0001=secret")

        self.assertEqual(code, 1)
        self.assertEqual(list(payload), ["errors"])
        self.assertNotIn(HOST_A, completed.stdout + completed.stderr)
        self.assertEqual(len(self.judgments(iid)), 1)

    def test_missing_workspace_returns_1(self):
        code, payload, _ = self.answer("0" * 32, "P0001=secret")
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)
        self.assertFalse(self.names_file.exists())

    def test_malformed_inspection_id_returns_1(self):
        code, payload, _ = self.answer("../etc", "P0001=secret")
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)

    def test_missing_judgment_returns_1_and_writes_nothing(self):
        iid = self.make_workspace([candidate("P0001", "domain", HOST_A)], judged=False)
        code, payload, _ = self.answer(iid, "P0001=secret")
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)
        self.assertFalse(self.names_file.exists())

    def test_missing_pattern_json_returns_1(self):
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            iid = workspace.create()
        code, payload, _ = self.answer(iid, "P0001=secret")
        self.assertEqual(code, 1)
        self.assertIn("pattern.json", payload["errors"][0])


class IsolationTest(AnswerTestCase):
    def test_lists_are_written_under_the_test_home_and_project_only(self):
        # answer.py は、HOME の ~/.project-secret.yaml と --project-root 配下だけへ書く
        iid = self.make_workspace([
            candidate("P0001", "domain", HOST_A), candidate("P0002", "domain", HOST_B, line=2),
        ])
        self.answer(iid, "P0001=secret", "P0002=public-keep")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), [".project-secret.yaml"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [".forge"])
        self.assertEqual(sorted(p.name for p in (self.root / ".forge").iterdir()), ["secret_allowlist.yaml"])


class ArgumentTest(AnswerTestCase):
    def run_raw(self, *args):
        return subprocess.run(
            [sys.executable, str(ANSWER), *args], capture_output=True, text=True, env=self.env(),
        )

    def test_relative_project_root_is_exit_2(self):
        completed = self.run_raw("0" * 32, "--project-root", "relative/path", "--answer", "P0001=secret")
        self.assertEqual(completed.returncode, 2)

    def test_missing_project_root_is_exit_2(self):
        self.assertEqual(self.run_raw("0" * 32, "--answer", "P0001=secret").returncode, 2)

    def test_missing_answer_is_exit_2(self):
        self.assertEqual(self.run_raw("0" * 32, "--project-root", str(self.root)).returncode, 2)

    def test_missing_inspection_id_is_exit_2(self):
        self.assertEqual(
            self.run_raw("--project-root", str(self.root), "--answer", "P0001=secret").returncode, 2
        )

    def test_unknown_option_is_exit_2(self):
        completed = self.run_raw(
            "0" * 32, "--project-root", str(self.root), "--answer", "P0001=secret", "--bogus",
        )
        self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
