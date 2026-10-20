#!/usr/bin/env python3
"""``linter/agents/secret-reader.md`` の契約テスト。

定義が持つ script の呼び方が、``ai_units.py``・``ai_add.py``・``ai_finish.py`` の実際の引数と一致すること、
他の script が現れないこと、``--kind`` の語彙が ``ai_add.py`` の選択肢と一致すること、
候補の区分を決める指示が無いこと、最終応答に値を含めない指示があること、
プロジェクト文書（ID・パス）を参照していないことを確かめる。
Claude Code の実 Agent 起動は unittest の検証対象にしない（手動テスト ``ai_tests/``）。

実行:
  python3 -m unittest tests.test_secret_reader_definition -v
"""

import importlib.util
import re
import shlex
import unittest
from pathlib import Path

_support_spec = importlib.util.spec_from_file_location(
    "secret_linter_ai_support_for_definition",
    Path(__file__).resolve().parent / "ai_support.py",
)
support = importlib.util.module_from_spec(_support_spec)
_support_spec.loader.exec_module(support)

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "linter"
AGENT_PATH = PLUGIN_ROOT / "agents" / "secret-reader.md"
SCRIPTS_DIR = support.SCRIPTS_DIR

#: Bash で実行してよい script（定義が許す 3 本）
ALLOWED_SCRIPTS = {"ai_units", "ai_add", "ai_finish"}


class SecretReaderDefinitionTest(support.AiTestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = AGENT_PATH.read_text(encoding="utf-8")

    def _documented_commands(self):
        """定義にある ``python3`` の行を、script 名 → 行 の対応で返す。"""
        commands = {}
        for line in self.text.splitlines():
            if not line.startswith('python3 "${CLAUDE_PLUGIN_ROOT}/'):
                continue
            name = re.search(r"/([A-Za-z0-9_]+)\.py\"", line).group(1)
            self.assertNotIn(name, commands, f"{name} の呼び方が複数ある")
            commands[name] = line
        return commands

    def _argv(self, line, inspection_id, unit_id="U0001", kind="person_name", row="2"):
        """定義の呼び方のプレースホルダを実値に置き換え、script 名以降の引数の列にする。"""
        line = line.replace("<<'VALUE'", "")
        line = line.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN_ROOT))
        for placeholder, value in (
            ("{検査ID}", inspection_id),
            ("{unit_id}", unit_id),
            ("{kind}", kind),
            ("{行番号}", row),
        ):
            line = line.replace(placeholder, value)
        argv = shlex.split(line)
        self.assertEqual(argv[0], "python3")
        return argv[1:]

    def _run_documented(self, commands, name, inspection_id, stdin="", **kwargs):
        argv = self._argv(commands[name], inspection_id, **kwargs)
        script_path = Path(argv[0])
        self.assertEqual(
            script_path.parent,
            SCRIPTS_DIR,
            f"{name}: 呼ぶ script の置き場が実在の場所と違う",
        )
        self.assertTrue(script_path.is_file(), f"{name}: script が実在しない")
        code, payload, stdout, _ = self.run_script(
            script_path.name, *argv[1:], stdin=stdin
        )
        return code, payload

    def test_documents_exactly_the_three_allowed_scripts(self):
        self.assertEqual(set(self._documented_commands()), ALLOWED_SCRIPTS)

    def test_documented_calls_run_against_the_real_scripts(self):
        commands = self._documented_commands()
        iid = self.make_workspace()
        code, payload = self._run_documented(commands, "ai_units", iid)
        self.assertEqual(code, 0)
        self.assertTrue(Path(payload["units_path"]).is_file())
        # 値は標準入力で渡す。定義の呼び方（位置引数・--unit-id・--line・--kind）で受理される
        code, payload = self._run_documented(
            commands,
            "ai_add",
            iid,
            stdin=support.VALUE_A + "\n",
            unit_id="U0001",
            kind="person_name",
            row="2",
        )
        self.assertEqual((code, payload), (0, {"accepted_count": 1}))
        code, payload = self._run_documented(commands, "ai_finish", iid)
        self.assertEqual((code, payload), (0, {}))
        code, _, _, _ = self.run_script("ai_check.py", iid)
        self.assertEqual(code, 0)

    def test_value_is_passed_through_a_quoted_here_document(self):
        self.assertIn("<<'VALUE'", self.text)

    def test_kind_vocabulary_matches_ai_add(self):
        # ai_add.py を import せず（他テストの import 状態に触れない）、ソースの KINDS 定義から読む
        source = (SCRIPTS_DIR / "ai_add.py").read_text(encoding="utf-8")
        kinds_literal = re.search(r"^KINDS = \(([^)]*)\)", source, re.MULTILINE).group(
            1
        )
        script_kinds = set(re.findall(r'"([a-z_]+)"', kinds_literal))
        section = self.text.split("`--kind` は次のいずれかである:", 1)[1].split(
            "\n", 1
        )[0]
        documented = set(re.findall(r"`([a-z_]+)`（", section))
        self.assertEqual(documented, script_kinds)

    def test_has_no_instruction_to_decide_candidate_classes(self):
        # 区分（不合格・要確認・通過）は機密判定が決める。定義に、その語を置かない
        for word in ("不合格", "要確認", "通過"):
            self.assertNotIn(word, self.text)

    def test_final_response_excludes_values(self):
        self.assertRegex(self.text, r"最終応答は.*終えたこと")
        self.assertRegex(self.text, r"値・本文.*応答に書かない")

    def test_does_not_reference_project_documents(self):
        self.assertNotRegex(self.text, r"\b(DES|REQ|ADR|TASK)-\d+")
        self.assertNotIn("docs/specs", self.text)
        self.assertNotIn("docs/rules", self.text)

    def test_forbids_launching_other_agents_and_asking_the_user(self):
        self.assertIn("他の Agent または Skill を起動しない", self.text)
        self.assertIn("利用者に質問しない", self.text)
        self.assertIn("対象のファイルを作成、編集、削除しない", self.text)

    def test_readable_docs_referenced_by_the_definition_exist(self):
        for relative in re.findall(
            r"\$\{CLAUDE_PLUGIN_ROOT\}/(docs/[A-Za-z0-9_/]+\.md)", self.text
        ):
            self.assertTrue((PLUGIN_ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
