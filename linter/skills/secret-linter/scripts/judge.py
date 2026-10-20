#!/usr/bin/env python3
"""secret-linter の機密判定を実行する。作業領域の機密候補（パターンの候補 pattern.json と、``--rules-only`` で
ないときは AI の候補 ai_events）を、2 つのリスト（同梱の許可リスト・プロジェクトの許可リスト・機密の名前リスト）と
照らして fail・confirm・pass に分け、判定結果（judgment/0001.json）を書き、要確認の一覧などを標準出力へ返す。

判定の規則（J-1）は ``judgment.py`` が 1 か所に持つ（回答後の再判定を行う ``answer.py`` も同じ規則を呼ぶ）。
標準出力の組み立ては ``judge_summary.py`` が 1 か所に持つ（``answer.py`` も同じ形を返す）。
本 script は、引数の解釈・入力の読み込み・規則の呼び出し・判定結果の書き込み・標準出力だけを担う。

``--project-root`` は、プロジェクトの許可リスト（``.forge/secret_allowlist.yaml``）を探すために使う。
利用者のホームの ``~/.project-secret.yaml`` が無いときは、空のリストとして扱う。

## ``--rules-only`` の扱い

- ``--rules-only`` のとき: AI 検出の成果物（``ai_events``・``ai_done.json``）を読まず、パターンの機密候補だけを
  判定する。成果物の有無に関わらず判定が返る
- ``--rules-only`` でないとき: AI 検出の終了の印（``ai_done.json`` が ``{"exit": "0"}``）が無ければ、何も判定せず 1 を
  返す（``ai_check.py`` と同じ趣旨の確認。SKILL が手順 3 で別に呼ぶため二重になるが、設計書の契約どおり本 script も
  確かめる）。印があれば、``ai_events`` の受理した AI の候補を、パターンの候補に連結して判定する

AI の候補は、パターンの候補と同じ規則（``judgment.py``）を通る。AI の出力は合否を決めず、機密の名前リストに
一致すれば fail になり、一致しなければ種別（AI の 4 種別は confirm）に従って confirm になる。判定結果は、どちらの
場合も同じ連番のファイル（``judgment/0001.json``）に書く。

## 標準出力（終了コード 0）

``class``（全体の区分: fail・confirm・pass）、``confirm``（要確認の候補の一覧。``candidate_id``・``unit_id``・
``label``・``line``・``kind``・``value``、履歴なら ``commit``）、``pass_count``・``fail_count``（件数のみ）、
``suppressed``（抑制マーカー付きの件数と位置）、``skipped``（スキップの一覧）。要確認の値は、利用者へ質問するために
返す。fail・pass の候補の値は出さない。

## 終了コード

| code | 意味                                                                                                          |
| ---- | ------------------------------------------------------------------------------------------------------------- |
| 0    | 判定が完了した（全体の区分が fail や confirm でも 0）                                                           |
| 1    | 完了しなかった（標準出力の ``errors`` に理由。作業領域が無い・pattern.json か units.json が読めない・``--rules-only`` でなく AI 検出の終了の印が無い・AI の候補の記録が読めない・リストの形式不正（行番号と理由だけ）） |
| 2    | 引数を受理できない（``argparse``。絶対パスでない ``--project-root`` を含む）                                   |

本 script は DES-087 のモジュール表 L-1・J-1・W-2 の実装である。

Usage:
    python3 judge.py INSPECTION_ID --project-root ABS_PATH [--rules-only]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_events  # noqa: E402
import inspection_input  # noqa: E402
import judge_summary  # noqa: E402
import judgment  # noqa: E402
import lists  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402


#: 入力の読み込みは ``report.py`` と共有する（``inspection_input.py``）
InputError = inspection_input.InputError


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    parser.add_argument("--project-root", required=True, type=secret_common.absolute_path)
    parser.add_argument("--rules-only", action="store_true")
    return parser


def cmd_judge(args):
    inspection_id = args.inspection_id
    try:
        if not args.rules_only and not ai_events.is_done(inspection_id):
            return secret_common.error("AI 検出の終了の印がありません（AI 検出が正常に終えていないため、判定できません）")
        candidates, suppressed, units, skipped = inspection_input.read_inputs(
            inspection_id, include_ai=not args.rules_only
        )
        loaded = lists.load_all(args.project_root)
        result = judgment.judge(candidates, loaded)
        workspace.write_next(inspection_id, "judgment", result)
    except (InputError, workspace.WorkspaceError, judgment.JudgmentError, lists.ListFormatError) as exc:
        return secret_common.error(str(exc))

    secret_common.emit(judge_summary.build_summary(result, candidates, units, suppressed, skipped))
    return 0


def run_cli(argv=None):
    return cmd_judge(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
