#!/usr/bin/env python3
"""secret-linter の judge.py（機密判定の CLI）の契約テスト。

DES-087 のモジュール表 L-1・J-1・W-2 と、テスト表 SC-07・SC-09 のスクリプトの検証に対応する。
判定の規則そのものは test_judgment.py が検証する。

- 終了コード 0: judgment/0001.json が書かれ、全体の区分・要確認の一覧・抑制・スキップが標準出力に出る。
  全体の区分が fail や confirm でも 0。fail・pass の値は標準出力に出ない
- 終了コード 1: 作業領域が無い・pattern.json が無い・壊れている・リストの形式不正（行番号と理由だけ。値は出ない）
- 終了コード 2: --project-root が絶対パスでない・必須の引数が無い
- ~/.project-secret.yaml が無いとき空のリストとして扱われる（ホームは一時ディレクトリへ置き換える）
- --rules-only のとき AI 検出の成果物を読まず、パターンの候補だけを判定する。--rules-only でないときの AI の
  候補・終了の印の検証は test_judge_ai.py が担う（ここでは、終了の印を書いた作業領域で、パターンの候補の判定が
  これまでと同じであることを検証する）
- 同じ入力から同じ判定

作業領域は OS の一時ディレクトリに作られる。テストは、TMPDIR・HOME をテストごとの専用ディレクトリへ向け、
実際のホームを読まない・書かない。入力は明白なプレースホルダだけを使う。機密らしい値は連結して組み立てる。

実行:
  python3 -m unittest tests.test_judge -v
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
SCRIPT = _SCRIPTS_DIR / "judge.py"
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vocab  # noqa: E402
import workspace  # noqa: E402

#: 明白なプレースホルダ（連結して組み立てる）
FAIL_VALUE = "PLACEHOLDER-" + "FAIL-" + "VALUE"
#: 同梱の許可リストにある実在ドメイン（許可リストで pass になる）
PASS_HOST = "docs.python.org"
GITHUB_HOST = "github.com"
#: 予約値の語。run_judge は予約値の定数を空にした状態で judge.py を走らせるので、予約値ではない値として扱われる
CONFIRM_HOST = "docs.example.com"
LISTED_HOST = "host.corp.example.com"


def pattern_candidate(cid, kind, value, unit_id="U0001", line=1, commit=None):
    candidate = {
        "candidate_id": cid,
        "source": "pattern",
        "unit_id": unit_id,
        "line": line,
        "kind": kind,
        "value": value,
    }
    if commit is not None:
        candidate["commit"] = commit
    return candidate


class JudgeTestCase(unittest.TestCase):
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

    def make_workspace(
        self,
        candidates,
        suppressed=None,
        skipped=None,
        units=None,
        write_pattern=True,
        done=True,
    ):
        """``done`` が真なら AI 検出の終了の印を書く（``--rules-only`` なしの ``judge.py`` が判定できる状態）。"""
        with mock.patch.object(tempfile, "tempdir", str(self.tmpdir)):
            inspection_id = workspace.create()
            if done:
                workspace.write(inspection_id, "ai_done.json", {"exit": "0"})
            workspace.write(
                inspection_id,
                "units.json",
                {
                    "units": units
                    if units is not None
                    else [
                        {
                            "unit_id": "U0001",
                            "kind": "file",
                            "label": "docs/a.md",
                            "text_path": "/dev/null",
                        },
                    ],
                    "skipped": skipped or [],
                },
            )
            if write_pattern:
                workspace.write(
                    inspection_id,
                    "pattern.json",
                    {
                        "candidates": candidates,
                        "suppressed": suppressed or {"count": 0, "locations": []},
                        "filtered": {"count": 0, "reasons": {}},
                    },
                )
        return inspection_id

    def workspace_dir(self, inspection_id):
        return self.tmpdir / f"{workspace.PLACE_PREFIX}{inspection_id}"

    def run_judge(self, *args):
        completed = subprocess.run(
            vocab.argv(SCRIPT, *args, no_reserved=True),
            capture_output=True,
            text=True,
            env=self.env(),
        )
        payload = json.loads(completed.stdout) if completed.stdout.strip() else None
        return completed.returncode, payload, completed

    def judge(self, inspection_id, *extra):
        return self.run_judge(inspection_id, "--project-root", str(self.root), *extra)

    def write_list(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class SuccessTest(JudgeTestCase):
    def test_writes_judgment_0001_and_prints_summary(self):
        candidates = [
            pattern_candidate("P0001", "domain", PASS_HOST, line=1),
            pattern_candidate("P0002", "domain", CONFIRM_HOST, line=2),
            pattern_candidate("P0003", "jwt", FAIL_VALUE, line=3),
        ]
        inspection_id = self.make_workspace(candidates)

        code, payload, _ = self.judge(inspection_id)

        self.assertEqual(code, 0)
        self.assertEqual(payload["class"], "fail")
        self.assertEqual(payload["pass_count"], 1)
        self.assertEqual(payload["fail_count"], 1)
        written = json.loads(
            (self.workspace_dir(inspection_id) / "judgment" / "0001.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(written["class"], "fail")
        self.assertEqual(
            [(c["candidate_id"], c["class"]) for c in written["candidates"]],
            [("P0001", "pass"), ("P0002", "confirm"), ("P0003", "fail")],
        )
        for c in written["candidates"]:
            self.assertEqual(sorted(c), ["candidate_id", "class", "reason"])
            for value in (PASS_HOST, CONFIRM_HOST, FAIL_VALUE):
                self.assertNotIn(value, c["reason"])

    def test_confirm_list_has_position_kind_and_value_only_for_confirm(self):
        candidates = [
            pattern_candidate("P0001", "domain", PASS_HOST),
            pattern_candidate("P0002", "domain", CONFIRM_HOST, line=7, commit="abc123"),
            pattern_candidate("P0003", "jwt", FAIL_VALUE),
        ]
        inspection_id = self.make_workspace(candidates)

        _, payload, completed = self.judge(inspection_id)

        self.assertEqual(
            payload["confirm"],
            [
                {
                    "candidate_id": "P0002",
                    "unit_id": "U0001",
                    "label": "docs/a.md",
                    "line": 7,
                    "kind": "domain",
                    "value": CONFIRM_HOST,
                    "commit": "abc123",
                }
            ],
        )
        self.assertNotIn(FAIL_VALUE, completed.stdout)
        self.assertNotIn(PASS_HOST, completed.stdout)

    def test_overall_confirm_and_pass_exit_zero(self):
        code, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", CONFIRM_HOST)])
        )
        self.assertEqual((code, payload["class"]), (0, "confirm"))
        code, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", PASS_HOST)])
        )
        self.assertEqual((code, payload["class"], payload["confirm"]), (0, "pass", []))

    def test_no_candidates_is_pass(self):
        code, payload, _ = self.judge(self.make_workspace([]))
        self.assertEqual(
            (code, payload["class"], payload["pass_count"]), (0, "pass", 0)
        )

    def test_suppressed_and_skipped_are_passed_through(self):
        suppressed = {
            "count": 1,
            "locations": [
                {"unit_id": "U0001", "label": "docs/a.md", "line": 4, "kind": "email"}
            ],
        }
        skipped = [{"label": "big.bin", "reason": "too large"}]
        _, payload, _ = self.judge(
            self.make_workspace([], suppressed=suppressed, skipped=skipped)
        )
        self.assertEqual(payload["suppressed"], suppressed)
        self.assertEqual(payload["skipped"], skipped)

    def test_rules_only_flag_gives_same_judgment_for_pattern_only_workspace(self):
        # 終了の印があり AI の候補が 0 件なら、--rules-only の有無で同じ判定になる（AI の候補がある場合は test_judge_ai.py）
        candidates = [
            pattern_candidate("P0001", "domain", CONFIRM_HOST),
            pattern_candidate("P0002", "jwt", FAIL_VALUE),
        ]
        _, without, _ = self.judge(self.make_workspace(candidates))
        _, with_flag, _ = self.judge(self.make_workspace(candidates), "--rules-only")
        self.assertEqual(without, with_flag)

    def test_deterministic(self):
        candidates = [
            pattern_candidate("P0001", "domain", CONFIRM_HOST),
            pattern_candidate("P0002", "jwt", FAIL_VALUE),
        ]
        inspection_id = self.make_workspace(candidates)
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

    def test_rules_only_does_not_read_broken_ai_files(self):
        # --rules-only は AI 検出の成果物を読まない。壊れていても、無くても 0 を返す
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", PASS_HOST)], done=False
        )
        directory = self.workspace_dir(inspection_id) / "ai_events"
        directory.mkdir()
        (directory / "0001.json").write_text("{not json", encoding="utf-8")
        code, payload, _ = self.judge(inspection_id, "--rules-only")
        self.assertEqual(
            (code, payload["class"], payload["pass_count"]), (0, "pass", 1)
        )


class ListsTest(JudgeTestCase):
    def test_missing_home_secret_file_is_treated_as_empty(self):
        # SC-09: ~/.project-secret.yaml が無い間は空のリストとして扱う（エラーにせず、作成もしない）
        self.assertFalse((self.home / ".project-secret.yaml").exists())
        code, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", CONFIRM_HOST)])
        )
        self.assertEqual((code, payload["class"]), (0, "confirm"))
        self.assertFalse((self.home / ".project-secret.yaml").exists())

    def test_secret_name_list_makes_fail_even_if_project_allowlist_matches(self):
        self.write_list(self.home / ".project-secret.yaml", f"- {LISTED_HOST}\n")
        self.write_list(
            self.root / ".secret-linter" / "allowlist.yaml", f"- {LISTED_HOST}\n"
        )
        code, payload, completed = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", LISTED_HOST)])
        )
        self.assertEqual((code, payload["class"]), (0, "fail"))
        self.assertNotIn(LISTED_HOST, completed.stdout)

    def test_project_allowlist_passes(self):
        self.write_list(
            self.root / ".secret-linter" / "allowlist.yaml", f"- {CONFIRM_HOST}\n"
        )
        code, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", CONFIRM_HOST)])
        )
        self.assertEqual((code, payload["class"]), (0, "pass"))

    def test_email_passes_when_the_exact_address_is_in_allowlist(self):
        email = "someone@" + CONFIRM_HOST
        self.write_list(self.root / ".secret-linter" / "allowlist.yaml", f"- {email}\n")
        code, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "email", email)])
        )
        self.assertEqual((code, payload["class"]), (0, "pass"))

    def test_email_stays_confirm_when_only_the_domain_is_in_allowlist(self):
        email = "someone@" + CONFIRM_HOST
        self.write_list(
            self.root / ".secret-linter" / "allowlist.yaml",
            f'- {CONFIRM_HOST}\n- "*.{CONFIRM_HOST}"\n',
        )
        code, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "email", email)])
        )
        self.assertEqual((code, payload["class"]), (0, "confirm"))

    def test_host_only_bundled_domain_with_path_is_confirm(self):
        for value in (
            f"https://{GITHUB_HOST}/other-org/some-repo?tab=x",
            vocab.domain("other-org", GITHUB_HOST),
        ):
            with self.subTest(value=value):
                code, payload, _ = self.judge(
                    self.make_workspace([pattern_candidate("P0001", "domain", value)])
                )
                self.assertEqual((code, payload["class"]), (0, "confirm"))
        _, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", GITHUB_HOST)])
        )
        self.assertEqual(payload["class"], "pass")
        _, payload, _ = self.judge(
            self.make_workspace([pattern_candidate("P0001", "domain", PASS_HOST)])
        )
        self.assertEqual(payload["class"], "pass")


class FailureTest(JudgeTestCase):
    def test_invalid_project_list_returns_1_with_line_number_only(self):
        leaked = "SENTINEL-" + "LINE-CONTENT"
        self.write_list(
            self.root / ".secret-linter" / "allowlist.yaml",
            f"- ok.example.com\nkey: {leaked}\n",
        )
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "domain", CONFIRM_HOST)]
        )

        code, payload, completed = self.judge(inspection_id)

        self.assertEqual(code, 1)
        self.assertEqual(list(payload), ["errors"])
        self.assertIn("2 行目", payload["errors"][0])
        self.assertNotIn(leaked, completed.stdout + completed.stderr)
        self.assertFalse((self.workspace_dir(inspection_id) / "judgment").exists())

    def test_invalid_secret_name_list_returns_1_with_line_number_only(self):
        leaked = "SENTINEL-" + "NAME-CONTENT"
        self.write_list(
            self.home / ".project-secret.yaml", f"# comment\n- ok-name\n  - {leaked}\n"
        )
        code, payload, completed = self.judge(self.make_workspace([]))
        self.assertEqual(code, 1)
        self.assertIn("3 行目", payload["errors"][0])
        self.assertNotIn(leaked, completed.stdout + completed.stderr)

    def test_missing_workspace_returns_1(self):
        code, payload, _ = self.judge("0" * 32)
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)

    def test_malformed_inspection_id_returns_1(self):
        code, payload, _ = self.judge("../etc")
        self.assertEqual(code, 1)
        self.assertIn("errors", payload)

    def test_missing_pattern_json_returns_1(self):
        code, payload, _ = self.judge(self.make_workspace([], write_pattern=False))
        self.assertEqual(code, 1)
        self.assertIn("pattern.json", payload["errors"][0])

    def test_broken_pattern_json_returns_1_without_leaking_content(self):
        leaked = "SENTINEL-" + "BROKEN-CONTENT"
        inspection_id = self.make_workspace([], write_pattern=False)
        (self.workspace_dir(inspection_id) / "pattern.json").write_text(
            "{" + leaked, encoding="utf-8"
        )
        code, payload, completed = self.judge(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(leaked, completed.stdout)

    def test_unknown_kind_returns_1_without_the_value(self):
        inspection_id = self.make_workspace(
            [pattern_candidate("P0001", "not_a_kind", FAIL_VALUE)]
        )
        code, payload, completed = self.judge(inspection_id)
        self.assertEqual(code, 1)
        self.assertNotIn(FAIL_VALUE, completed.stdout)


class ArgumentTest(JudgeTestCase):
    def test_relative_project_root_is_exit_2(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "0" * 32, "--project-root", "relative/path"],
            capture_output=True,
            text=True,
            env=self.env(),
        )
        self.assertEqual(completed.returncode, 2)

    def test_missing_project_root_is_exit_2(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "0" * 32],
            capture_output=True,
            text=True,
            env=self.env(),
        )
        self.assertEqual(completed.returncode, 2)

    def test_unknown_option_is_exit_2(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "0" * 32,
                "--project-root",
                str(self.root),
                "--bogus",
            ],
            capture_output=True,
            text=True,
            env=self.env(),
        )
        self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
