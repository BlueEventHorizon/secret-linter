#!/usr/bin/env python3
"""テスト入力の語彙（vocab.py）の単体テスト。

検証すること:
- 語彙の全ての語が、本番の設定で pass になる（理由は予約値）
- ``vocab.no_reserved()`` の中では、同じ全ての語が confirm になる（メールも）
- ``no_reserved()`` を出ると、予約値の定数が元に戻る
- 子プロセスで走らせる実行器（``argv(..., no_reserved=True)``）も、同じ状態で script を走らせる

実行:
  python3 -m unittest tests.test_vocab -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import vocab  # noqa: E402

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "linter"
    / "skills"
    / "secret-linter"
    / "scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

import judgment  # noqa: E402
import lists  # noqa: E402

LISTS = lists.Lists(
    secret_names=[],
    bundled_allowlist=lists.load_bundled_allowlist(),
    project_allowlist=[],
)

#: 語彙の種別と、その語を判定に掛けるときの候補の kind
GROUPS = (
    ("domain", vocab.VOCAB_DOMAINS),
    ("email", vocab.VOCAB_EMAILS),
    ("private_ip", vocab.VOCAB_IPS),
)


def classify(kind, value):
    candidate = {
        "candidate_id": "P0001",
        "source": "pattern",
        "unit_id": "U0001",
        "line": 1,
        "kind": kind,
        "value": value,
    }
    return judgment.classify(candidate, LISTS)


class VocabTest(unittest.TestCase):
    def test_vocabulary_is_not_empty(self):
        for kind, words in GROUPS:
            with self.subTest(kind=kind):
                self.assertGreaterEqual(len(words), 3)

    def test_every_word_passes_as_reserved_in_production_settings(self):
        for kind, words in GROUPS:
            for word in words:
                with self.subTest(kind=kind, word=word):
                    self.assertEqual(
                        classify(kind, word), (judgment.PASS, judgment.REASON_RESERVED)
                    )

    def test_every_word_is_confirm_inside_no_reserved(self):
        with vocab.no_reserved():
            for kind, words in GROUPS:
                for word in words:
                    with self.subTest(kind=kind, word=word):
                        self.assertEqual(classify(kind, word)[0], judgment.CONFIRM)

    def test_email_is_confirm_with_the_email_reason_inside_no_reserved(self):
        with vocab.no_reserved():
            for word in vocab.VOCAB_EMAILS:
                with self.subTest(word=word):
                    self.assertEqual(
                        classify("email", word),
                        (judgment.CONFIRM, judgment.REASON_EMAIL),
                    )

    def test_reserved_constants_are_restored_after_no_reserved(self):
        before = (
            judgment.RESERVED_DOMAINS,
            judgment.RESERVED_TLDS,
            judgment.RESERVED_NETWORKS,
        )
        with vocab.no_reserved():
            self.assertEqual(
                (
                    judgment.RESERVED_DOMAINS,
                    judgment.RESERVED_TLDS,
                    judgment.RESERVED_NETWORKS,
                ),
                ((), (), ()),
            )
        self.assertEqual(
            (
                judgment.RESERVED_DOMAINS,
                judgment.RESERVED_TLDS,
                judgment.RESERVED_NETWORKS,
            ),
            before,
        )
        self.assertTrue(all(before))

    def test_reserved_constants_are_restored_when_the_block_raises(self):
        before = judgment.RESERVED_DOMAINS
        with self.assertRaises(RuntimeError):
            with vocab.no_reserved():
                raise RuntimeError("boom")
        self.assertEqual(judgment.RESERVED_DOMAINS, before)


class RunnerTest(unittest.TestCase):
    """子プロセスの実行器は、予約値の定数を空にした状態で script を走らせる。"""

    def test_runner_empties_the_constants_and_passes_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "probe.py"
            script.write_text(
                "import json, sys\n"
                f"sys.path.insert(0, {str(_SCRIPTS_DIR)!r})\n"
                "import judgment\n"
                "print(json.dumps([bool(judgment.RESERVED_DOMAINS), bool(judgment.RESERVED_TLDS),"
                " bool(judgment.RESERVED_NETWORKS), sys.argv[1:]]))\n",
                encoding="utf-8",
            )
            for no_reserved, expected in (
                (True, [False, False, False]),
                (False, [True, True, True]),
            ):
                with self.subTest(no_reserved=no_reserved):
                    completed = subprocess.run(
                        vocab.argv(script, "a", "--b", no_reserved=no_reserved),
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(
                        json.loads(completed.stdout), [*expected, ["a", "--b"]]
                    )

    def test_runner_propagates_the_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "exit3.py"
            script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
            completed = subprocess.run(
                vocab.argv(script, no_reserved=True), capture_output=True, text=True
            )
            self.assertEqual(completed.returncode, 3)


if __name__ == "__main__":
    unittest.main()
