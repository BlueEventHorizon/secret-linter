#!/usr/bin/env python3
"""AI 検出（``linter:secret-reader``）が見つけた機密候補を、1 件ずつ受け付ける。

候補 1 件ごとに 1 回呼ばれる。検査 ID（位置引数）・``--unit-id``・``--line``（任意、整数）・``--kind`` を引数で、
値を標準入力（UTF-8。末尾の改行 1 つは値に含めない。空は拒否）で受け取る。AI は JSON を組み立てない。
構造（``ai_events/NNNN.json``。形式は ``ai_events.py``）は本 script が作る。

受理の条件は、次の 2 つだけである（引数と本文の対応関係。区分は判定が決める）。

- ``unit_id`` が ``units.json`` に実在すること。
- 値が、その検査単位の本文（``--line`` があればその行）に、そのまま含まれること。

``--line`` の意味は、パターン検出の候補の ``line`` と同じである。``file`` と ``commit_message`` は本文の
1 始まりの行番号、``added_lines`` は検査単位の ``line_numbers`` の対応する元のファイルの行番号。

受理したら、機密候補 1 件を ``ai_events`` に書く。``candidate_id`` は ``A`` + 4 桁の連番（``A0001`` 形式。受理の順）、
``source`` は ``ai``、履歴の検査単位なら ``commit`` を持つ。拒否したら、拒否の旨と理由（語彙。値を含めない）を
``ai_events`` に連番で書き、1 を返す。拒否が連続した回数は ``ai_events`` の末尾に連なる拒否の数で、受理が
1 件あれば 0 に戻る。2 回続けて拒否された後は、本 script も ``ai_finish.py`` も拒否し（1 を返す。何も書かない）、
``ai_done.json`` は書かれない。

値は、標準出力・``errors``・例外メッセージ・ログに出さない（作業領域のファイルにだけ置く）。
成功時の標準出力は、受理した件数（``accepted_count``。この呼び出しの分を含む累計）である。

本 script は DES-087 のモジュール表 W-3（AI の候補を受け付ける）の実装である。

## 終了コード

| code | 意味                                                                                                                                      |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | 候補を受理して書いた（標準出力は ``accepted_count``）                                                                                     |
| 1    | 区別のない失敗（標準出力の ``errors`` に理由。候補を受理できない（拒否を記録する）、拒否が 2 回続いた後（何も書かない）、作業領域が無い） |
| 2    | 引数を受理できない（``argparse``。語彙外の ``--kind``、整数でない ``--line``）                                                            |

Usage:
    python3 ai_add.py INSPECTION_ID --unit-id ID [--line N] --kind KIND < value
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_events  # noqa: E402
import patterns  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402

KINDS = ("person_name", "company_name", "resource_name", "other_ai")

#: 拒否の理由の語彙（閉じた集合）と、利用者・AI へ返す文。値を含めない。
REJECT_MESSAGES = {
    "empty_value": "値が空です",
    "invalid_value": "値を UTF-8 として読めません",
    "unit_not_found": "unit_id が検査単位に実在しません",
    "line_not_in_unit": "指定した行が検査単位にありません",
    "value_not_in_text": "値が検査単位の本文（指定した行）にそのまま含まれていません",
    "unit_unreadable": "検査単位の本文を読めません",
}


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--line", type=int)
    parser.add_argument("--kind", required=True, choices=KINDS)
    return parser


def _read_value():
    """標準入力から値を読む。末尾の改行 1 つは値に含めない。読めなければ ``None``。"""
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def _find_reject_reason(unit, line, value):
    """受理の条件を満たさなければ、拒否の理由の語彙を返す。満たせば ``None``。"""
    try:
        if line is None:
            text = patterns.read_text(unit["text_path"])
            return None if value in text else "value_not_in_text"
        # 行の対応（元の行番号）は、パターン検出と同じ読み方に揃える
        numbered_lines, _ = patterns.unit_lines(unit)
    except (patterns.PatternError, KeyError):
        return "unit_unreadable"
    for number, text in numbered_lines:
        if number == line:
            return None if value in text else "value_not_in_text"
    return "line_not_in_unit"


def _reject(inspection_id, reason):
    ai_events.write_event(
        inspection_id,
        {
            "result": ai_events.RESULT_REJECTED,
            "reason": reason,
            "message": REJECT_MESSAGES[reason],
        },
    )
    return secret_common.error(REJECT_MESSAGES[reason])


def cmd_add(args):
    try:
        units = ai_events.load_units(args.inspection_id)
        events = ai_events.read_events(args.inspection_id)
        if ai_events.locked(events):
            return secret_common.error(
                f"拒否が {ai_events.REJECT_LIMIT} 回続いたため、候補を受け付けません"
            )
        value = _read_value()
        if value is None:
            return _reject(args.inspection_id, "invalid_value")
        if value == "":
            return _reject(args.inspection_id, "empty_value")
        unit = next(
            (
                u
                for u in units
                if isinstance(u, dict) and u.get("unit_id") == args.unit_id
            ),
            None,
        )
        if unit is None:
            return _reject(args.inspection_id, "unit_not_found")
        reason = _find_reject_reason(unit, args.line, value)
        if reason is not None:
            return _reject(args.inspection_id, reason)
        count = ai_events.accepted_count(events) + 1
        candidate = {
            "candidate_id": ai_events.candidate_id(count),
            "source": "ai",
            "unit_id": args.unit_id,
            "line": args.line,
            "kind": args.kind,
            "value": value,
        }
        if unit.get("commit") is not None:
            candidate["commit"] = unit["commit"]
        ai_events.write_event(
            args.inspection_id,
            {"result": ai_events.RESULT_ACCEPTED, "candidate": candidate},
        )
    except workspace.WorkspaceError as exc:
        return secret_common.error(str(exc))
    secret_common.emit({"accepted_count": count})
    return 0


def run_cli(argv=None):
    return cmd_add(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
