#!/usr/bin/env python3
"""secret-linter の回答の記録を実行する。要確認の候補への利用者の回答に従って、機密の名前リストと許可リストへ
追記し、回答を反映して判定し直し、新しい判定結果（``judgment/NNNN.json``）を書き、``judge.py`` と同じ形の
標準出力を返す。回答と再判定の対象には、パターンの候補に加えて、AI の候補を含めることがある（下記）。

判定の規則（J-1）は ``judgment.py``、標準出力の組み立ては ``judge_summary.py``、リストへの追記（L-3）は
``lists.py`` が 1 か所に持つ。本 script は、引数の解釈・回答の検証・追記の呼び出し・再判定・判定結果の書き込み・
標準出力だけを担う薄い CLI である。

## 引数

``INSPECTION_ID``（位置引数）、``--project-root``（絶対パス。必須。プロジェクトの許可リストを探すために使う。
``scan.py`` に渡したものと同じ値）、``--answer 候補ID=回答``（候補ごとに繰り返す。1 回以上）。

## 回答の動作

| 回答          | 意味                           | 動作                                                                        |
| ------------- | ------------------------------ | --------------------------------------------------------------------------- |
| ``secret``      | 機密である                     | 機密の名前リスト（``~/.project-secret.yaml``）へ、その候補の値を追記する     |
| ``public-keep`` | 公開してよい（残す）           | プロジェクトの許可リスト（``.forge/secret_allowlist.yaml``）へ、値を追記する |
| ``public-once`` | 公開してよい（今回だけ）       | どのリストにも追記しない。判定ファイルの ``reason`` に記録し、後続の呼び出しへ引き継ぐ |

- メールアドレス（``kind`` が ``email``、または値全体がメールアドレスの形のもの。AI の候補を含む）も、
  他の候補と同じ 3 つの回答を受ける。``public-keep`` は、そのアドレスを許可リストへ追記する
  （メールアドレスは、完全一致の項目だけで通る）
- 追記は ``lists.py`` が、既存の項目・コメント・空行・並びを変えずに、一時ファイルから置き換えて行う。
  ファイルが無ければ作成する。同じ値が既に項目にあるときは、重複して追記しない
- 追記した項目は、その値に対して判定の規則で一致する（追記の前に確かめる。一致しない値は書かずに 1）
- 「公開してよい（今回だけ）」の記録と引き継ぎは ``judgment.rejudge`` が担う。1 回の検査の中で本 script が
  複数回呼ばれても、今回だけの回答は有効であり続ける
- 書き込みは、全ての回答の検証が済んでから行う（検証で 1 を返すとき、リストは何も変わらない）。
  機密の名前リストを先に、許可リストを後に書く（途中で失敗しても、機密を通す方向に残らない）。
  書き込み後の判定結果の書き込みに失敗したときは、追記済みのリストは戻らない（再度の ``judge.py`` で判定し直せる）

## 回答の対象

最新の判定が ``confirm`` の候補だけである。パターンの候補（``pattern.json``）と、AI の候補（``ai_events`` の
受理したもの）の両方が対象で、AI の候補にも、上の回答の動作と検証（リストの書式で
表せない値の拒否、追記した値が規則で一致することの確認）が同じに適用される。実在しない
候補 ID・要確認でない候補・同じ候補 ID の重複・形式が ``候補ID=回答`` でない指定・選択肢に無い回答は、
リストへ何も書かずに 1 を返す。

## AI の候補を含めるかの引き継ぎ

本 script は ``--rules-only`` を受け取らない（引数を足さない）。AI の候補を含めるかは、直前の判定ファイル
（最新の ``judgment/NNNN.json``）が AI の候補（``candidate_id`` が ``A`` で始まる）を含んでいたかを引き継いで決める
（``inspection_input.read_inputs_following_judgment``。``report.py`` も同じ関数で決める）。最初の判定を作った
``judge.py`` が ``--rules-only`` でなければ AI の候補が判定に入り、回答のたびの再判定にも入り続ける。含める
場合は、``ai_events`` の受理した候補を、パターンの候補に連結して、回答と再判定の対象にする。回答していない
AI の候補は、再判定でも消えない。``--rules-only`` で判定した検査では、AI の候補を含めない。

## 標準出力（終了コード 0）

``judge.py`` と同じ形（``class``・``confirm``・``pass_count``・``fail_count``・``suppressed``・``skipped``）。
要確認の値は、利用者へ質問するために返す。fail・pass の値は出さない。``errors``・例外メッセージ・ログには、
候補の値を載せない（行番号と理由、または固定の理由だけを返す）。

## 終了コード

| code | 意味                                                                                                                                                                  |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | 回答を記録して、判定し直した（全体の区分が fail や confirm でも 0）                                                                                                   |
| 1    | 完了しなかった（標準出力の ``errors`` に理由）。回答が選択肢に無い・形式不正・候補 ID が実在しない・リストの書式で表せない値・既存のリストの形式不正（行番号と理由だけ）・リストへ書き込めない・作業領域や判定が読めない |
| 2    | 引数を受理できない（``argparse``。絶対パスでない ``--project-root``・``--answer`` が無い場合を含む）                                                                    |

本 script は DES-087 のモジュール表 L-2・L-3・J-1・W-2 の実装である。

Usage:
    python3 answer.py INSPECTION_ID --project-root ABS_PATH --answer CANDIDATE_ID=ANSWER [--answer ...]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspection_input  # noqa: E402
import judge_summary  # noqa: E402
import judgment  # noqa: E402
import lists  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402

ANSWER_SECRET = "secret"
ANSWER_PUBLIC_KEEP = "public-keep"
ANSWER_PUBLIC_ONCE = "public-once"
ANSWERS = (ANSWER_SECRET, ANSWER_PUBLIC_KEEP, ANSWER_PUBLIC_ONCE)

_EMPTY_BUNDLED = lists.BundledAllowlist([], [], [])


class AnswerError(Exception):
    """回答を受理できない・記録できない。メッセージに候補の値と、渡された回答の文字列を含めない。"""


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    parser.add_argument("--project-root", required=True, type=secret_common.absolute_path)
    parser.add_argument("--answer", action="append", required=True, metavar="CANDIDATE_ID=ANSWER")
    return parser


def _parse_answers(raw_answers):
    """``候補ID=回答`` の指定を ``(候補 ID, 回答)`` の一覧にする。形式不正・選択肢外・重複は ``AnswerError``。"""
    parsed = []
    seen = set()
    for number, raw in enumerate(raw_answers, start=1):
        candidate_id, separator, answer = raw.partition("=")
        if not separator or not candidate_id:
            raise AnswerError(f"{number} 番目の --answer の形式が「候補ID=回答」ではありません")
        if answer not in ANSWERS:
            raise AnswerError(
                f"{number} 番目の --answer の回答が選択肢にありません（{' / '.join(ANSWERS)} のいずれか）"
            )
        if candidate_id in seen:
            raise AnswerError(f"{number} 番目の --answer の候補 ID が重複しています")
        seen.add(candidate_id)
        parsed.append((candidate_id, answer))
    return parsed


def _plan_writes(answers, candidates, previous):
    """回答を検証し、書き込む値と今回だけの候補 ID を決める。リストへは何も書かない。

    ``(機密の名前リストへ追記する値, 許可リストへ追記する値, 今回だけの候補 ID)`` を返す。
    """
    by_id = {c.get("candidate_id"): c for c in candidates}
    latest = {r["candidate_id"]: r["class"] for r in previous["candidates"]}
    secret_values, allow_values, once_ids = [], [], []
    for candidate_id, answer in answers:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise AnswerError("実在しない候補 ID への回答があります")
        if latest.get(candidate_id) != judgment.CONFIRM:
            raise AnswerError(f"候補 {candidate_id} は要確認ではないため、回答できません")
        if answer == ANSWER_PUBLIC_ONCE:
            once_ids.append(candidate_id)
            continue
        value = candidate.get("value")
        if answer == ANSWER_SECRET:
            display_name, target = lists.SECRET_NAMES_DISPLAY_NAME, secret_values
        else:
            display_name, target = lists.PROJECT_ALLOWLIST_DISPLAY_NAME, allow_values
        try:
            lists.check_representable(value, display_name)
        except lists.ListWriteError as exc:
            raise AnswerError(f"候補 {candidate_id}: {exc}") from None
        if answer == ANSWER_SECRET:
            matched = judgment.matches_secret_name(value, [value])
        else:
            matched = judgment.allowlist_reason(value, _EMPTY_BUNDLED, [value]) is not None
        if not matched:
            raise AnswerError(f"候補 {candidate_id} の値は、追記しても判定の規則で一致しないため、追記できません")
        target.append(value)
    return secret_values, allow_values, once_ids


def _record(secret_values, allow_values, project_root):
    """機密の名前リストへ、次に許可リストへ追記する（空の指定は何もしない）。"""
    if secret_values:
        lists.append_secret_names(secret_values)
    if allow_values:
        lists.append_project_allowlist(project_root, allow_values)


def cmd_answer(args):
    inspection_id = args.inspection_id
    try:
        answers = _parse_answers(args.answer)
        candidates, suppressed, units, skipped, previous = inspection_input.read_inputs_following_judgment(
            inspection_id
        )
        lists.load_all(args.project_root)  # 既存のリストの形式を、何かを書く前に検査する
        secret_values, allow_values, once_ids = _plan_writes(answers, candidates, previous)
        _record(secret_values, allow_values, args.project_root)
        result = judgment.rejudge(candidates, lists.load_all(args.project_root), previous, once_ids)
        workspace.write_next(inspection_id, "judgment", result)
    except (
        AnswerError, inspection_input.InputError, workspace.WorkspaceError, judgment.JudgmentError,
        lists.ListFormatError, lists.ListWriteError,
    ) as exc:
        return secret_common.error(str(exc))

    secret_common.emit(judge_summary.build_summary(result, candidates, units, suppressed, skipped))
    return 0


def run_cli(argv=None):
    return cmd_answer(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
