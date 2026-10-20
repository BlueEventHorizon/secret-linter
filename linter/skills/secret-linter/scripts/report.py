#!/usr/bin/env python3
"""secret-linter の結果提示（R-1）。最新の判定と作業領域の入力から、区分（合格・不合格）と、利用者へ示す
結果の本文を作り、標準出力へ返す。

結果を、ファイルに残さない。標準出力へ返すだけで、作業領域の中にも外にも書かない（作業領域の消去は
``cleanup.py`` の担当）。

## 入力

- 最新の ``judgment/NNNN.json``（最大の連番が現在の判定）と、最初の ``judgment/0001.json``
  （回答を反映する前の判定）。読み方は ``judgment.py`` が持つ
- ``pattern.json`` の ``candidates``（位置・種別・値・commit）と ``suppressed``、``units.json`` の ``skipped``
  （判定ファイルには値・位置が無いため、``candidate_id`` で結合する）。最新の判定が AI の候補を含んでいたときは、
  ``ai_events`` の受理した AI の候補も、パターンの候補と同じ書式で本文に載せる（含めるかの決め方は
  ``answer.py`` と同じで、``inspection_input.read_inputs_following_judgment`` が持つ）

判定が確定していない（現在の判定に confirm の候補が残る）ときは、結果を返さず 1 を返す。

## 標準出力（終了コード 0）

JSON 1 つ。``class`` は区分（``合格`` か ``不合格``）で、値・位置・本文を含めない。呼び出し元へ返る
区分はこの語だけである。``body`` は利用者へ示す本文で、不合格・要確認だった候補の値を元の文字列のまま含む
（値を示す相手は利用者に限る。区分・``errors``・例外メッセージ・ログには値を載せない）。

## 本文の並び（空の節は並べない）

1. 不合格・要確認だった候補を、候補ごとに 1 行（位置・種別・値・判定）。要確認だった候補は、最初の判定で
   confirm だったもので、回答後の判定を ``要確認だった → 通過`` のように並べて示す。最初から不合格の候補も含む
2. 通過した候補を、種別と値が同じものを 1 行にまとめる（件数と最初の位置）
3. 抑制マーカー付きの件数と位置
4. 読めずにスキップしたものと理由
5. 区分が不合格のときだけ、誤検出を通す方法（抑制マーカー）の案内を、最後に 1 つ添える

候補が 0 件のときは、見出しだけの節を並べず、先頭に ``検出なし`` と示す。

## 位置の書式

``<label>:<line>``。``line`` が無ければ ``<label>`` だけ。履歴の検査単位（``commit`` を持つもの）は、
末尾に `` [commit <識別子>]`` を付ける。``label`` と ``commit`` は検査単位（``units.json``）から得る。

## 終了コード

| code | 意味                                                                                                        |
| ---- | ----------------------------------------------------------------------------------------------------------- |
| 0    | 結果を返した（区分が不合格でも 0）                                                                          |
| 1    | 完了しなかった（標準出力の ``errors`` に理由。作業領域が無い・判定が無い・判定が確定していない・入力が読めない） |
| 2    | 引数を受理できない（``argparse``）                                                                          |

本 script は DES-087 のモジュール表 R-1 の実装である。

Usage:
    python3 report.py INSPECTION_ID
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspection_input  # noqa: E402
import judgment  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402

CLASS_PASS = "合格"
CLASS_FAIL = "不合格"

_JUDGMENT_WORD = {judgment.FAIL: "不合格", judgment.PASS: "通過", judgment.CONFIRM: "要確認"}

#: 不合格の結果の末尾に添える、誤検出を通す方法。示さなければ、利用者も呼び出し元の AI も、止まったまま打つ手を知らない
FALSE_POSITIVE_GUIDANCE = (
    "誤検出であれば、該当の行の末尾に `secrets-scan: ignore` を含むコメントを付けると、候補から外れる"
    "（利用者の了承を得てから付ける。抑制した件数と位置は、この結果に表示される）。"
)


class ReportError(Exception):
    """結果を作れない。メッセージに値を含めない。"""


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    return parser


def _format_position(label, line, commit):
    text = str(label) if line is None else f"{label}:{line}"
    if commit is not None:
        text += f" [commit {commit}]"
    return text


def _unit_index(units):
    return {u.get("unit_id"): u for u in units if isinstance(u, dict)}


def _position_of(candidate, unit_index):
    unit = unit_index.get(candidate.get("unit_id"), {})
    commit = candidate.get("commit", unit.get("commit"))
    return _format_position(unit.get("label", "(不明な検査単位)"), candidate.get("line"), commit)


def _verdict(first_class, latest_class):
    """候補 1 件の判定の表示。要確認だった候補は、回答後の判定を並べる。"""
    if first_class == judgment.CONFIRM:
        return f"{_JUDGMENT_WORD[judgment.CONFIRM]} → {_JUDGMENT_WORD[latest_class]}"
    return _JUDGMENT_WORD[latest_class]


def _candidate_section(entries):
    lines = [f"## 不合格・要確認だった候補（{len(entries)} 件）"]
    for position, kind, value, verdict in entries:
        lines.append(f"- {position} / {kind} / `{value}` / {verdict}")
    return "\n".join(lines)


def _pass_section(groups):
    lines = [f"## 通過した候補（{sum(g['count'] for g in groups.values())} 件。種別と値が同じものは 1 行）"]
    for (kind, value), group in groups.items():
        lines.append(f"- {kind} / `{value}` / {group['count']} 件 / 最初の位置 {group['first']}")
    return "\n".join(lines)


def _suppressed_section(suppressed, unit_index):
    locations = suppressed.get("locations")
    count = suppressed.get("count")
    if not isinstance(locations, list):
        locations = []
    if not isinstance(count, int):
        count = len(locations)
    if count == 0 and not locations:
        return None
    lines = [f"## 抑制マーカー付きで候補にしなかったもの（{count} 件）"]
    for location in locations:
        if not isinstance(location, dict):
            continue
        unit = unit_index.get(location.get("unit_id"), {})
        label = location.get("label", unit.get("label", "(不明な検査単位)"))
        position = _format_position(label, location.get("line"), unit.get("commit"))
        lines.append(f"- {position} / {location.get('kind', '')}")
    return "\n".join(lines)


def _skipped_section(skipped):
    if not skipped:
        return None
    lines = [f"## 読めずにスキップしたもの（{len(skipped)} 件）"]
    for item in skipped:
        if not isinstance(item, dict):
            continue
        commit = item.get("commit")
        position = _format_position(item.get("label", "(不明)"), None, commit)
        lines.append(f"- {position} / 理由: {item.get('reason', '')}")
    return "\n".join(lines)


def build_report(candidates, suppressed, units, skipped, first, latest):
    """``(区分, 本文)`` を返す。現在の判定に confirm が残るときは ``ReportError``。"""
    first_classes = {r["candidate_id"]: r["class"] for r in first["candidates"]}
    latest_classes = {r["candidate_id"]: r["class"] for r in latest["candidates"]}
    unit_index = _unit_index(units)

    detail = []
    pass_groups = {}
    for candidate in candidates:
        cid = candidate.get("candidate_id")
        if cid not in latest_classes:
            raise ReportError("判定に含まれない機密候補があります")
        if not isinstance(candidate.get("kind"), str) or not isinstance(candidate.get("value"), str):
            raise ReportError("機密候補の種別または値が文字列ではありません")
        latest_class = latest_classes[cid]
        if latest_class == judgment.CONFIRM:
            raise ReportError("判定が確定していません（要確認の候補が残っています）")
        first_class = first_classes.get(cid, latest_class)
        position = _position_of(candidate, unit_index)
        if first_class == judgment.CONFIRM or latest_class == judgment.FAIL:
            detail.append((position, candidate["kind"], candidate["value"], _verdict(first_class, latest_class)))
        else:
            group = pass_groups.setdefault((candidate["kind"], candidate["value"]), {"count": 0, "first": position})
            group["count"] += 1
    if latest["class"] == judgment.CONFIRM:
        raise ReportError("判定が確定していません（要確認の候補が残っています）")

    sections = []
    if not candidates:
        sections.append("検出なし（機密の候補は見つかりませんでした）")
    if detail:
        sections.append(_candidate_section(detail))
    if pass_groups:
        sections.append(_pass_section(pass_groups))
    for section in (_suppressed_section(suppressed, unit_index), _skipped_section(skipped)):
        if section is not None:
            sections.append(section)

    overall = CLASS_FAIL if latest["class"] == judgment.FAIL else CLASS_PASS
    if overall == CLASS_FAIL:
        sections.append(FALSE_POSITIVE_GUIDANCE)
    return overall, "\n\n".join(sections)


def cmd_report(args):
    inspection_id = args.inspection_id
    try:
        candidates, suppressed, units, skipped, latest = inspection_input.read_inputs_following_judgment(
            inspection_id
        )
        first = judgment.read_first(inspection_id)
        overall, body = build_report(candidates, suppressed, units, skipped, first, latest)
    except (
        inspection_input.InputError, workspace.WorkspaceError, judgment.JudgmentError, ReportError,
    ) as exc:
        return secret_common.error(str(exc))
    secret_common.emit({"class": overall, "body": body})
    return 0


def run_cli(argv=None):
    return cmd_report(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
