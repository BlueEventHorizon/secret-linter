#!/usr/bin/env python3
"""secret-linter の script が共通で使う、作業領域の入力ファイル（pattern.json・units.json・AI の候補）の読み込み。

``judge.py``・``answer.py``・``report.py`` が同じ読み方をする。各 script が同じディレクトリから import する
（直接実行はしない）ため、終了コードを持たない。失敗は ``InputError`` で表す。メッセージに値・本文を含めない。

## 候補の読み方

機密候補の配列は、パターンの候補（``pattern.json``。``candidate_id`` は ``P0001`` 形式）に、AI の候補
（``ai_events/NNNN.json`` の受理したイベント。``A0001`` 形式。拒否イベントは候補にならない）を連結して作る。
AI の候補を含めるかは、呼び出し側が決める（``read_inputs`` の ``include_ai``）。

- ``judge.py``: ``--rules-only`` でなければ含める
- ``answer.py``・``report.py``: 最新の判定が AI の候補を含んでいたかを引き継ぐ（``read_inputs_following_judgment``）。
  これらは ``--rules-only`` を受け取らないため、最初の判定を作った ``judge.py`` の選択を判定ファイルから読む
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_events  # noqa: E402
import judgment  # noqa: E402
import workspace  # noqa: E402


class InputError(Exception):
    """入力（作業領域のファイル）の形が不正。メッセージに値を含めない。読めない・無いときは ``workspace.WorkspaceError``。"""


def read_json(inspection_id, name):
    data = workspace.read_json(workspace.workspace_path(inspection_id) / name, f"作業領域の {name}")
    if not isinstance(data, dict):
        raise InputError(f"作業領域の {name} の形が不正です")
    return data


def _list_field(data, key, name):
    items = data.get(key)
    if not isinstance(items, list):
        raise InputError(f"作業領域の {name} の {key} が配列ではありません")
    return items


def read_ai_candidates(inspection_id):
    """``ai_events`` の受理したイベントの機密候補を、受理の順に返す。拒否イベントは候補にならない。

    ``ai_events/`` が無ければ空。作業領域が無い・記録の形が不正なときは ``workspace.WorkspaceError``。
    終了の印（``ai_done.json``）は確かめない（確かめるのは ``judge.py``）。
    """
    candidates = []
    for event in ai_events.read_events(inspection_id):
        if event["result"] != ai_events.RESULT_ACCEPTED:
            continue
        candidate = event.get("candidate")
        if not isinstance(candidate, dict):
            raise InputError("作業領域の AI の候補の記録（ai_events）の候補が不正です")
        candidates.append(candidate)
    return candidates


def read_inputs(inspection_id, include_ai=False):
    """``(候補の配列, 抑制, 検査単位の配列, スキップの配列)`` を返す。

    ``include_ai`` が真なら、候補の配列はパターンの候補に AI の候補（``read_ai_candidates``）を連結したもの。
    """
    pattern = read_json(inspection_id, "pattern.json")
    units = read_json(inspection_id, "units.json")
    candidates = _list_field(pattern, "candidates", "pattern.json")
    if not all(isinstance(c, dict) for c in candidates):
        raise InputError("作業領域の pattern.json の candidates の要素が不正です")
    suppressed = pattern.get("suppressed")
    if not isinstance(suppressed, dict):
        raise InputError("作業領域の pattern.json の suppressed が不正です")
    unit_list = _list_field(units, "units", "units.json")
    skipped = _list_field(units, "skipped", "units.json")
    if include_ai:
        candidates = candidates + read_ai_candidates(inspection_id)
    return candidates, suppressed, unit_list, skipped


def judgment_includes_ai(latest):
    """判定（``judgment.read_latest`` の戻り値）が、AI の候補を含んでいたか。

    ``candidate_id`` が ``A`` で始まる候補があれば真。AI の候補が 0 件のときは偽になるが、そのとき
    連結しても候補の配列は変わらないため、結果は同じである。
    """
    return any(r["candidate_id"].startswith(ai_events.AI_CANDIDATE_PREFIX) for r in latest["candidates"])


def read_inputs_following_judgment(inspection_id):
    """``(候補の配列, 抑制, 検査単位の配列, スキップの配列, 最新の判定)`` を返す。

    AI の候補を含めるかは、最新の判定が AI の候補を含んでいたか（``judgment_includes_ai``）で決める。
    ``answer.py`` と ``report.py`` が使う。パターンの入力が読めないときの理由を、判定ファイルの理由より先に返す。
    """
    inputs = read_inputs(inspection_id)
    latest = judgment.read_latest(inspection_id)
    if judgment_includes_ai(latest):
        inputs = read_inputs(inspection_id, include_ai=True)
    return (*inputs, latest)
