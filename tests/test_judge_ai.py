#!/usr/bin/env python3
"""secret-linter の judge.py・answer.py・report.py が、AI 検出の成果物（ai_events の受理した候補と終了の印）を
判定に含める動作の契約テスト。

DES-087 の「スクリプトの契約」の judge.py の行と「機密判定（J-1）」、テスト表 SC-06・07・08 のうち、AI の候補を
読む側のスクリプトの検証に対応する。AI の成果物を書く側（ai_add.py・ai_finish.py）は test_ai_*.py が検証し、
判定の規則そのものは test_judgment.py が検証する。

- judge.py: AI の候補が判定に入る。拒否イベントは候補にならない。--rules-only でなく終了の印が無いと 1。
  --rules-only のときは、成果物の有無・状態に関わらず判定が返り、AI の候補は入らない（動作の違い）。
  判定は、これまでと同じ連番のファイル（judgment/0001.json）に書く
- AI の候補は合否を決めない: 機密の名前リストに一致すれば fail、一致しなければ confirm、許可リストに一致すれば pass
- answer.py: 最新の判定が AI の候補を含むとき、回答と再判定に AI の候補を含める。回答した候補の区分が反映され、
  他の AI の候補が消えない。public-once の引き継ぎも AI の候補で成り立つ。--rules-only の判定の後は AI の候補を含めない
- report.py: AI の候補を含む判定で動き、AI の候補の値が本文に載り、区分・errors には載らない
- 値が標準出力の errors に出ない。終了コード 0・1・2

作業領域は OS の一時ディレクトリに作られる。テストは、TMPDIR・HOME をテストごとの専用ディレクトリへ向け、
実際のホームを読まない・書かない。入力は明白なプレースホルダだけを使い、連結して組み立てる。

実行:
  python3 -m unittest tests.test_judge_ai -v
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
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)
JUDGE = _SCRIPTS_DIR / "judge.py"
ANSWER = _SCRIPTS_DIR / "answer.py"
REPORT = _SCRIPTS_DIR / "report.py"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lists  # noqa: E402
import vocab  # noqa: E402
import workspace  # noqa: E402

#: 明白なプレースホルダ（連結して組み立てる）
PERSON = "Taro-" + "Placeholder-Person"
COMPANY = "Acme-" + "Placeholder-Company"
RESOURCE = "bucket-" + "placeholder-res"
#: 予約値の語。run_script は予約値の定数を空にした状態で script を走らせるので、予約値ではない値として扱われる
HOST_A = "docs.example.com"
FAIL_VALUE = "PLACEHOLDER-" + "FAIL-" + "VALUE"
BROKEN = "SENTINEL-" + "BROKEN-EVENT"

SUMMARY_KEYS = ["class", "confirm", "fail_count", "pass_count", "skipped", "suppressed"]


def pattern_candidate(cid, kind, value, line=1):
    return {
        "candidate_id": cid,
        "source": "pattern",
        "unit_id": "U0001",
        "line": line,
        "kind": kind,
        "value": value,
    }


def ai_candidate(cid, kind, value, line=1, **extra):
    return {
        "candidate_id": cid,
        "source": "ai",
        "unit_id": "U0001",
        "line": line,
        "kind": kind,
        "value": value,
        **extra,
    }


class AiJudgeTestCase(unittest.TestCase):
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
        self.allow_file = self.root / ".secret-linter" / "allowlist.yaml"
        patcher = mock.patch.object(tempfile, "tempdir", str(self.tmpdir))
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self):
        env = dict(os.environ)
        env["TMPDIR"] = str(self.tmpdir)
        env["HOME"] = str(self.home)
        env.pop("TEMP", None)
        env.pop("TMP", None)
        return env

    def make_workspace(
        self, pattern_candidates=(), ai_candidates=(), rejected=0, done=True
    ):
        """パターンの候補・AI の候補（受理イベント）・拒否イベント・終了の印を持つ作業領域を作り、検査 ID を返す。"""
        inspection_id = workspace.create()
        workspace.write(
            inspection_id,
            "units.json",
            {
                "units": [
                    {
                        "unit_id": "U0001",
                        "kind": "file",
                        "label": "docs/a.md",
                        "text_path": "/dev/null",
                    }
                ],
                "skipped": [],
            },
        )
        workspace.write(
            inspection_id,
            "pattern.json",
            {
                "candidates": list(pattern_candidates),
                "suppressed": {"count": 0, "locations": []},
                "filtered": {"count": 0, "reasons": {}},
            },
        )
        for candidate in ai_candidates:
            workspace.write_next(
                inspection_id,
                "ai_events",
                {"result": "accepted", "candidate": candidate},
            )
        for _ in range(rejected):
            workspace.write_next(
                inspection_id,
                "ai_events",
                {
                    "result": "rejected",
                    "reason": "value_not_in_text",
                    "message": "値が検査単位の本文にそのまま含まれていません",
                },
            )
        if done:
            workspace.write(inspection_id, "ai_done.json", {"exit": "0"})
        return inspection_id

    def workspace_dir(self, inspection_id):
        return self.tmpdir / f"{workspace.PLACE_PREFIX}{inspection_id}"

    def judgments(self, inspection_id):
        directory = self.workspace_dir(inspection_id) / "judgment"
        if not directory.is_dir():
            return []
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(directory.iterdir())
        ]

    def run_script(self, script, *args):
        completed = subprocess.run(
            vocab.argv(script, *args, no_reserved=True),
            capture_output=True,
            text=True,
            env=self.env(),
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else None
        return completed.returncode, payload, completed

    def judge(self, inspection_id, *extra):
        return self.run_script(
            JUDGE, inspection_id, "--project-root", str(self.root), *extra
        )

    def answer(self, inspection_id, *pairs):
        args = [inspection_id, "--project-root", str(self.root)]
        for pair in pairs:
            args += ["--answer", pair]
        return self.run_script(ANSWER, *args)

    def report(self, inspection_id):
        return self.run_script(REPORT, inspection_id)

    def write_names(self, *items):
        self.names_file.write_text(
            "".join(f"- {item}\n" for item in items), encoding="utf-8"
        )

    def write_allowlist(self, *items):
        self.allow_file.parent.mkdir(parents=True, exist_ok=True)
        self.allow_file.write_text(
            "".join(f"- {item}\n" for item in items), encoding="utf-8"
        )

    @staticmethod
    def classes(judged):
        return [(c["candidate_id"], c["class"]) for c in judged["candidates"]]


class JudgeIncludesAiCandidatesTest(AiJudgeTestCase):
    def test_ai_candidates_are_in_the_judgment_with_pattern_candidates(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)],
            [
                ai_candidate("A0001", "person_name", PERSON, line=2),
                ai_candidate("A0002", "company_name", COMPANY, line=3),
            ],
        )

        code, payload, _ = self.judge(inspection_id)

        self.assertEqual(code, 0)
        self.assertEqual(payload["class"], "confirm")
        written = self.judgments(inspection_id)
        self.assertEqual(len(written), 1)  # これまでと同じ連番のファイル（0001）
        self.assertEqual(
            self.classes(written[0]),
            [("P0001", "confirm"), ("A0001", "confirm"), ("A0002", "confirm")],
        )
        for entry in written[0]["candidates"]:
            self.assertEqual(sorted(entry), ["candidate_id", "class", "reason"])
            self.assertNotIn(PERSON, entry["reason"])

    def test_confirm_list_carries_ai_candidates_with_position_kind_and_value(self):
        inspection_id = self.make_workspace(
            [],
            [ai_candidate("A0001", "person_name", PERSON, line=7, commit="abc123")],
        )

        _, payload, completed = self.judge(inspection_id)

        self.assertEqual(sorted(payload), SUMMARY_KEYS)
        self.assertEqual(
            payload["confirm"],
            [
                {
                    "candidate_id": "A0001",
                    "unit_id": "U0001",
                    "label": "docs/a.md",
                    "line": 7,
                    "kind": "person_name",
                    "value": PERSON,
                    "commit": "abc123",
                }
            ],
        )
        self.assertIn(
            PERSON, completed.stdout
        )  # 要確認の値は、利用者へ質問するために返す

    def test_rejected_events_are_not_candidates(self):
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "person_name", PERSON)], rejected=1
        )

        _, payload, _ = self.judge(inspection_id)

        self.assertEqual([c["candidate_id"] for c in payload["confirm"]], ["A0001"])
        self.assertEqual(
            self.classes(self.judgments(inspection_id)[0]), [("A0001", "confirm")]
        )

    def test_done_marker_with_no_ai_candidates_gives_the_pattern_only_judgment(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)]
        )
        _, payload, _ = self.judge(inspection_id)
        self.assertEqual(
            (payload["class"], [c["candidate_id"] for c in payload["confirm"]]),
            ("confirm", ["P0001"]),
        )

    def test_deterministic_and_next_number_file(self):
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "person_name", PERSON)]
        )
        _, first, _ = self.judge(inspection_id)
        _, second, _ = self.judge(inspection_id)
        self.assertEqual(first, second)
        directory = self.workspace_dir(inspection_id) / "judgment"
        self.assertEqual(
            sorted(p.name for p in directory.iterdir()), ["0001.json", "0002.json"]
        )
        self.assertEqual(
            (directory / "0001.json").read_text(encoding="utf-8"),
            (directory / "0002.json").read_text(encoding="utf-8"),
        )


class AiCandidatesFollowTheSameRulesTest(AiJudgeTestCase):
    """AI の候補は合否を決めない。パターンの候補と同じ規則（J-1）を通る。"""

    def test_candidate_matching_the_secret_name_list_fails(self):
        self.write_names(PERSON)
        inspection_id = self.make_workspace(
            [],
            [
                ai_candidate("A0001", "person_name", PERSON),
                ai_candidate("A0002", "company_name", COMPANY, line=2),
            ],
        )

        code, payload, completed = self.judge(inspection_id)

        self.assertEqual((code, payload["class"]), (0, "fail"))
        self.assertEqual(
            self.classes(self.judgments(inspection_id)[0]),
            [("A0001", "fail"), ("A0002", "confirm")],
        )
        self.assertEqual(
            self.judgments(inspection_id)[0]["candidates"][0]["reason"],
            "confidential_name_list",
        )
        self.assertEqual(
            (payload["fail_count"], [c["candidate_id"] for c in payload["confirm"]]),
            (1, ["A0002"]),
        )
        self.assertNotIn(PERSON, completed.stdout)  # fail の値は出さない

    def test_name_list_match_wins_over_the_project_allowlist(self):
        self.write_names(COMPANY)
        self.write_allowlist(COMPANY)
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "company_name", COMPANY)]
        )
        self.assertEqual(self.judge(inspection_id)[1]["class"], "fail")

    def test_candidate_not_in_any_list_is_confirm(self):
        self.write_names(PERSON)
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "resource_name", RESOURCE)]
        )
        _, payload, _ = self.judge(inspection_id)
        self.assertEqual(payload["class"], "confirm")
        self.assertEqual([c["kind"] for c in payload["confirm"]], ["resource_name"])

    def test_every_ai_kind_is_confirm_by_default(self):
        kinds = ("person_name", "company_name", "resource_name", "other_ai")
        candidates = [
            ai_candidate(f"A{n:04d}", kind, f"{PERSON}-{kind}", line=n)
            for n, kind in enumerate(kinds, start=1)
        ]
        _, payload, _ = self.judge(self.make_workspace([], candidates))
        self.assertEqual([c["kind"] for c in payload["confirm"]], list(kinds))

    def test_project_allowlist_passes_an_ai_candidate(self):
        self.write_allowlist(COMPANY)
        _, payload, _ = self.judge(
            self.make_workspace([], [ai_candidate("A0001", "company_name", COMPANY)])
        )
        self.assertEqual(
            (payload["class"], payload["confirm"], payload["pass_count"]),
            ("pass", [], 1),
        )

    def test_ai_output_does_not_decide_the_class(self):
        # AI の候補に区分らしい項目が付いていても、判定の規則だけが区分を決める
        candidate = ai_candidate(
            "A0001", "person_name", PERSON, **{"class": "pass", "reason": "pass"}
        )
        inspection_id = self.make_workspace([], [candidate])
        _, payload, _ = self.judge(inspection_id)
        self.assertEqual(payload["class"], "confirm")
        self.assertEqual(
            self.judgments(inspection_id)[0]["candidates"][0]["reason"], "confirm_kind"
        )

    def test_one_failing_ai_candidate_fails_the_whole_judgment(self):
        self.write_names(RESOURCE)
        candidates = [pattern_candidate("P0001", "domain", HOST_A)]
        _, payload, _ = self.judge(
            self.make_workspace(
                candidates, [ai_candidate("A0001", "resource_name", RESOURCE)]
            )
        )
        self.assertEqual(payload["class"], "fail")


class DoneMarkerAndRulesOnlyTest(AiJudgeTestCase):
    """終了の印が無いときの、--rules-only の有無による動作の違い。"""

    def test_without_done_marker_judge_returns_1_when_not_rules_only(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)],
            [ai_candidate("A0001", "person_name", PERSON)],
            done=False,
        )

        code, payload, completed = self.judge(inspection_id)

        self.assertEqual(code, 1)
        self.assertEqual(list(payload), ["errors"])
        self.assertEqual(self.judgments(inspection_id), [])  # 判定ファイルは書かれない
        for value in (PERSON, HOST_A):
            self.assertNotIn(value, completed.stdout + completed.stderr)

    def test_without_done_marker_rules_only_returns_the_pattern_judgment(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)],
            [ai_candidate("A0001", "person_name", PERSON)],
            done=False,
        )

        code, payload, _ = self.judge(inspection_id, "--rules-only")

        self.assertEqual(code, 0)
        self.assertEqual([c["candidate_id"] for c in payload["confirm"]], ["P0001"])
        self.assertEqual(
            self.classes(self.judgments(inspection_id)[0]), [("P0001", "confirm")]
        )

    def test_rules_only_ignores_ai_artifacts_even_when_the_done_marker_exists(self):
        self.write_names(PERSON)
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "person_name", PERSON)]
        )  # 読めば fail になる

        code, payload, _ = self.judge(inspection_id, "--rules-only")

        self.assertEqual(
            (code, payload["class"], payload["fail_count"]), (0, "pass", 0)
        )
        self.assertEqual(self.judgments(inspection_id)[0]["candidates"], [])

    def test_rules_only_does_not_read_broken_ai_artifacts(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)], done=False
        )
        events = self.workspace_dir(inspection_id) / "ai_events"
        events.mkdir()
        (events / "0001.json").write_text("{" + BROKEN, encoding="utf-8")
        (self.workspace_dir(inspection_id) / "ai_done.json").write_text(
            "{" + BROKEN, encoding="utf-8"
        )
        self.assertEqual(self.judge(inspection_id, "--rules-only")[0], 0)

    def test_done_marker_with_other_content_is_not_a_done_marker(self):
        inspection_id = self.make_workspace([], done=False)
        workspace.write(inspection_id, "ai_done.json", {"exit": "1"})
        code, payload, _ = self.judge(inspection_id)
        self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertEqual(self.judge(inspection_id, "--rules-only")[0], 0)

    def test_broken_done_marker_returns_1_when_not_rules_only(self):
        inspection_id = self.make_workspace([], done=False)
        (self.workspace_dir(inspection_id) / "ai_done.json").write_text(
            "{" + BROKEN, encoding="utf-8"
        )
        code, payload, completed = self.judge(inspection_id)
        self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertNotIn(BROKEN, completed.stdout + completed.stderr)

    def test_broken_ai_events_return_1_without_leaking_content(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)]
        )
        (self.workspace_dir(inspection_id) / "ai_events").mkdir()
        (self.workspace_dir(inspection_id) / "ai_events" / "0001.json").write_text(
            "{" + BROKEN, encoding="utf-8"
        )
        code, payload, completed = self.judge(inspection_id)
        self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertNotIn(BROKEN, completed.stdout + completed.stderr)
        self.assertEqual(self.judgments(inspection_id), [])

    def test_malformed_accepted_event_returns_1(self):
        inspection_id = self.make_workspace([])
        workspace.write_next(inspection_id, "ai_events", {"result": "accepted"})
        code, payload, _ = self.judge(inspection_id)
        self.assertEqual((code, list(payload)), (1, ["errors"]))

    def test_missing_workspace_returns_1_for_both_modes(self):
        for extra in ((), ("--rules-only",)):
            with self.subTest(extra=extra):
                code, payload, _ = self.judge("0" * 32, *extra)
                self.assertEqual((code, list(payload)), (1, ["errors"]))

    def test_unknown_kind_in_an_ai_candidate_returns_1_without_the_value(self):
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "not_a_kind", FAIL_VALUE)]
        )
        code, _, completed = self.judge(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(FAIL_VALUE, completed.stdout + completed.stderr)

    def test_argument_errors_are_exit_2(self):
        self.assertEqual(self.run_script(JUDGE, "0" * 32)[0], 2)
        self.assertEqual(
            self.run_script(JUDGE, "0" * 32, "--project-root", "relative/path")[0], 2
        )


class AnswerIncludesAiCandidatesTest(AiJudgeTestCase):
    def two_ai_workspace(self, with_pattern=True):
        pattern = [pattern_candidate("P0001", "domain", HOST_A)] if with_pattern else []
        inspection_id = self.make_workspace(
            pattern,
            [
                ai_candidate("A0001", "person_name", PERSON, line=2),
                ai_candidate("A0002", "company_name", COMPANY, line=3),
            ],
        )
        code, _, _ = self.judge(inspection_id)
        self.assertEqual(code, 0)
        return inspection_id

    def test_answer_secret_to_an_ai_candidate_is_reflected_and_the_others_remain(self):
        inspection_id = self.two_ai_workspace()

        code, payload, completed = self.answer(inspection_id, "A0001=secret")

        self.assertEqual(code, 0)
        self.assertEqual(payload["class"], "fail")
        self.assertEqual(lists.load_secret_names(self.home), [PERSON])
        final = self.judgments(inspection_id)[-1]
        self.assertEqual(
            self.classes(final),
            [("P0001", "confirm"), ("A0001", "fail"), ("A0002", "confirm")],
        )
        # 回答していない AI の候補は、判定からも要確認の一覧からも消えない
        self.assertEqual(
            [c["candidate_id"] for c in payload["confirm"]], ["P0001", "A0002"]
        )
        self.assertEqual((payload["fail_count"], payload["pass_count"]), (1, 0))
        self.assertNotIn(PERSON, completed.stdout)  # fail の値は出さない
        self.assertIn(COMPANY, completed.stdout)  # 要確認の値は質問のために返す

    def test_answer_public_keep_to_an_ai_candidate_appends_to_the_allowlist(self):
        inspection_id = self.two_ai_workspace()

        code, payload, _ = self.answer(inspection_id, "A0002=public-keep")

        self.assertEqual(code, 0)
        self.assertEqual(lists.load_project_allowlist(self.root), [COMPANY])
        self.assertFalse(self.names_file.exists())
        self.assertEqual(
            self.classes(self.judgments(inspection_id)[-1]),
            [("P0001", "confirm"), ("A0001", "confirm"), ("A0002", "pass")],
        )
        self.assertEqual(
            [c["candidate_id"] for c in payload["confirm"]], ["P0001", "A0001"]
        )

    def test_answer_public_once_to_an_ai_candidate_appends_nothing_and_is_carried_over(
        self,
    ):
        inspection_id = self.two_ai_workspace(with_pattern=False)

        code, payload, _ = self.answer(inspection_id, "A0001=public-once")
        self.assertEqual((code, payload["class"]), (0, "confirm"))
        self.assertEqual([c["candidate_id"] for c in payload["confirm"]], ["A0002"])
        self.assertFalse(self.names_file.exists())
        self.assertFalse(self.allow_file.exists())

        # 後続の呼び出しでも、今回だけの回答は有効であり続ける（リストには何も残らない）
        code, payload, _ = self.answer(inspection_id, "A0002=public-once")
        self.assertEqual(
            (code, payload["class"], payload["confirm"], payload["pass_count"]),
            (0, "pass", [], 2),
        )
        final = self.judgments(inspection_id)[-1]
        self.assertEqual(
            [(c["candidate_id"], c["class"], c["reason"]) for c in final["candidates"]],
            [
                ("A0001", "pass", "answered_public_once"),
                ("A0002", "pass", "answered_public_once"),
            ],
        )
        self.assertFalse(self.names_file.exists())
        self.assertFalse(self.allow_file.exists())

    def test_answers_to_pattern_and_ai_candidates_in_one_call(self):
        inspection_id = self.two_ai_workspace()

        code, payload, _ = self.answer(
            inspection_id, "P0001=public-keep", "A0001=secret", "A0002=public-once"
        )

        self.assertEqual((code, payload["class"]), (0, "fail"))
        self.assertEqual(lists.load_project_allowlist(self.root), [HOST_A])
        self.assertEqual(lists.load_secret_names(self.home), [PERSON])
        self.assertEqual(
            self.classes(self.judgments(inspection_id)[-1]),
            [("P0001", "pass"), ("A0001", "fail"), ("A0002", "pass")],
        )

    def test_ai_candidates_stay_in_every_following_judgment(self):
        inspection_id = self.two_ai_workspace()
        self.answer(inspection_id, "A0001=public-keep")
        code, payload, _ = self.answer(inspection_id, "P0001=public-keep")
        self.assertEqual(code, 0)
        self.assertEqual([c["candidate_id"] for c in payload["confirm"]], ["A0002"])
        self.assertEqual(
            [cid for cid, _ in self.classes(self.judgments(inspection_id)[-1])],
            ["P0001", "A0001", "A0002"],
        )
        self.assertEqual(
            sorted(
                p.name
                for p in (self.workspace_dir(inspection_id) / "judgment").iterdir()
            ),
            ["0001.json", "0002.json", "0003.json"],
        )

    def test_answer_to_an_ai_candidate_that_is_not_confirm_is_rejected(self):
        self.write_names(PERSON)
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "person_name", PERSON)]
        )
        self.assertEqual(
            self.judge(inspection_id)[1]["class"], "fail"
        )  # 機密の名前リストに一致して fail
        before = self.names_file.read_bytes()

        code, payload, completed = self.answer(inspection_id, "A0001=public-keep")

        self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertNotIn(PERSON, completed.stdout + completed.stderr)
        self.assertEqual(self.names_file.read_bytes(), before)
        self.assertFalse(self.allow_file.exists())
        self.assertEqual(len(self.judgments(inspection_id)), 1)

    def test_invalid_answers_to_ai_candidates_are_rejected_and_lists_are_unchanged(
        self,
    ):
        inspection_id = self.two_ai_workspace()
        for pair in ("A0001=maybe", "A0001", "A0009=secret"):
            with self.subTest(answer=pair):
                code, payload, completed = self.answer(inspection_id, pair)
                self.assertEqual((code, list(payload)), (1, ["errors"]))
                self.assertNotIn(PERSON, completed.stdout + completed.stderr)
        code, payload, _ = self.answer(
            inspection_id, "A0001=secret", "A0001=public-keep"
        )  # 重複
        self.assertEqual(code, 1)
        self.assertEqual(len(self.judgments(inspection_id)), 1)
        self.assertFalse(self.names_file.exists())
        self.assertFalse(self.allow_file.exists())

    def test_ai_candidate_with_a_value_the_list_cannot_express_is_rejected(self):
        value = "line1\nline2-" + "placeholder"
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "other_ai", value)]
        )
        self.judge(inspection_id)
        for pair in ("A0001=secret", "A0001=public-keep"):
            with self.subTest(answer=pair):
                code, payload, _ = self.answer(inspection_id, pair)
                self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertEqual(self.answer(inspection_id, "A0001=public-once")[0], 0)

    def test_email_shaped_ai_candidate_can_be_kept(self):
        # 既存の回答の動作は、AI の候補にも同じに適用される（メールアドレスの public-keep は許可リストへ追記する）
        email = "someone@" + HOST_A
        inspection_id = self.make_workspace([], [ai_candidate("A0001", "email", email)])
        self.judge(inspection_id)
        code, payload, _ = self.answer(inspection_id, "A0001=public-keep")
        self.assertEqual((code, payload["class"]), (0, "pass"))
        self.assertEqual(lists.load_project_allowlist(self.root), [email])

    def test_after_a_rules_only_judgment_ai_candidates_are_not_included(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)],
            [ai_candidate("A0001", "person_name", PERSON)],
        )
        self.judge(inspection_id, "--rules-only")

        code, payload, _ = self.answer(inspection_id, "P0001=public-keep")

        self.assertEqual((code, payload["class"]), (0, "pass"))
        self.assertEqual(
            self.classes(self.judgments(inspection_id)[-1]), [("P0001", "pass")]
        )
        # --rules-only の判定の検査では、AI の候補は回答の対象にもならない
        code, _, _ = self.answer(inspection_id, "A0001=secret")
        self.assertEqual(code, 1)

    def test_answer_on_ai_judgment_returns_1_when_ai_events_cannot_be_read(self):
        inspection_id = self.two_ai_workspace()
        (self.workspace_dir(inspection_id) / "ai_events" / "0001.json").write_text(
            "{" + BROKEN, encoding="utf-8"
        )
        code, payload, completed = self.answer(inspection_id, "P0001=public-keep")
        self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertNotIn(BROKEN, completed.stdout + completed.stderr)
        self.assertFalse(self.allow_file.exists())


class ReportIncludesAiCandidatesTest(AiJudgeTestCase):
    def test_report_after_answers_shows_ai_candidates_in_the_body_only(self):
        self.write_names(RESOURCE)
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)],
            [
                ai_candidate("A0001", "person_name", PERSON, line=2),
                ai_candidate("A0002", "company_name", COMPANY, line=3),
                ai_candidate("A0003", "resource_name", RESOURCE, line=4),
            ],
        )
        self.judge(inspection_id)
        self.answer(
            inspection_id, "P0001=public-keep", "A0001=public-once", "A0002=secret"
        )

        code, payload, completed = self.report(inspection_id)

        self.assertEqual(code, 0)
        self.assertEqual(sorted(payload), ["body", "class"])
        self.assertEqual(payload["class"], "不合格")
        body = payload["body"]
        for value in (PERSON, COMPANY, RESOURCE):
            self.assertIn(value, body)  # 値は、利用者へ示す本文に元のまま載る
            self.assertNotIn(value, payload["class"])
        self.assertIn("docs/a.md:2", body)
        self.assertIn("person_name", body)
        self.assertIn("要確認 → 通過", body)
        self.assertIn("要確認 → 不合格", body)
        self.assertEqual(completed.stderr, "")

    def test_report_after_a_pass_with_ai_candidates_groups_them_in_the_pass_section(
        self,
    ):
        self.write_allowlist(COMPANY)
        inspection_id = self.make_workspace(
            [],
            [
                ai_candidate("A0001", "company_name", COMPANY, line=1),
                ai_candidate("A0002", "company_name", COMPANY, line=9),
            ],
        )
        self.judge(inspection_id)

        code, payload, _ = self.report(inspection_id)

        self.assertEqual((code, payload["class"]), (0, "合格"))
        lines = [line for line in payload["body"].splitlines() if COMPANY in line]
        self.assertEqual(len(lines), 1)
        self.assertIn("2 件", lines[0])

    def test_report_with_a_remaining_ai_confirm_returns_1_without_the_value(self):
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "person_name", PERSON)]
        )
        self.judge(inspection_id)

        code, payload, completed = self.report(inspection_id)

        self.assertEqual((code, list(payload)), (1, ["errors"]))
        self.assertNotIn(PERSON, completed.stdout + completed.stderr)

    def test_report_after_a_rules_only_judgment_does_not_include_ai_candidates(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", HOST_A)],
            [ai_candidate("A0001", "person_name", PERSON)],
        )
        self.judge(inspection_id, "--rules-only")
        self.answer(inspection_id, "P0001=public-keep")

        code, payload, _ = self.report(inspection_id)

        self.assertEqual(code, 0)
        self.assertNotIn(PERSON, payload["body"])

    def test_report_does_not_fail_on_a_judgment_that_includes_ai_candidates(self):
        # 判定に AI の候補が入っていても「判定に含まれない機密候補があります」で壊れない
        self.write_names(PERSON)
        inspection_id = self.make_workspace(
            [], [ai_candidate("A0001", "person_name", PERSON)]
        )
        self.judge(inspection_id)
        code, payload, _ = self.report(inspection_id)
        self.assertEqual((code, payload["class"]), (0, "不合格"))
        self.assertIn(PERSON, payload["body"])


if __name__ == "__main__":
    unittest.main()
