#!/usr/bin/env python3
"""secret-linter の AI 検出（``ai_units.py``・``ai_add.py``・``ai_finish.py``・``ai_check.py``）が共有する、
作業領域の読み書きの部品。

各 script が同じディレクトリから import する（直接実行はしない）ため、終了コードを持たない。失敗は
``workspace.WorkspaceError`` で表し、呼び出す script が ``secret_common.error`` で返す。メッセージに
候補の値・検査単位の本文を含めない。

## 作業領域のファイル（DES-087「作業領域の中身」）

- ``ai_events/NNNN.json``: ``ai_add.py`` の 1 回の呼び出しの結果。書くたびに新しい連番にする。
  - 受理: ``{"result": "accepted", "candidate": {...}}``。``candidate`` は機密候補 1 件で、
    ``candidate_id``（``A`` + 4 桁の連番。``A0001`` 形式。受理の順）・``source``（``ai``）・``unit_id``・
    ``line``（無ければ ``null``）・``kind``・``value``・``commit``（履歴の検査単位のときだけ）を持つ。
  - 拒否: ``{"result": "rejected", "reason": 理由の語彙, "message": 理由の文}``。値を含めない。
- ``ai_done.json``: 終了の印。``{"exit": "0"}`` だけを持つ。``ai_finish.py`` が成功したときだけ書く。

拒否が連続した回数は、``ai_events/`` の末尾に連なる拒否の数である。受理が 1 件あれば 0 に戻る。
"""

from pathlib import Path

import workspace

EVENTS_DIR = "ai_events"
DONE_FILE = "ai_done.json"
DONE_PAYLOAD = {"exit": "0"}

#: 拒否が連続してよい上限。これに達した後は、``ai_add.py`` も ``ai_finish.py`` も拒否する。
REJECT_LIMIT = 2

RESULT_ACCEPTED = "accepted"
RESULT_REJECTED = "rejected"

#: AI の候補の ``candidate_id`` の先頭（``A0001`` 形式）。パターンの候補は ``P``
AI_CANDIDATE_PREFIX = "A"


def candidate_id(number):
    """AI の候補の ``candidate_id``（接頭辞 + 4 桁の連番）。形の組み立ては、ここだけである。"""
    return f"{AI_CANDIDATE_PREFIX}{number:04d}"


def place(inspection_id):
    """実在する作業領域のパスを返す。無ければ ``WorkspaceError``。"""
    return workspace.existing_workspace(inspection_id)


def load_units(inspection_id):
    """``units.json`` の検査単位の配列を返す。作業領域か ``units.json`` が無ければ ``WorkspaceError``。"""
    data = workspace.read_json(place(inspection_id) / "units.json", "検査単位の一覧（units.json）")
    units = data.get("units") if isinstance(data, dict) else None
    if not isinstance(units, list):
        raise workspace.WorkspaceError("検査単位の一覧（units.json）の形が不正です")
    return units


def units_path(inspection_id):
    """検査単位の一覧ファイルの絶対パスを返す（本文は返さない）。読める形でなければ ``WorkspaceError``。"""
    load_units(inspection_id)
    return (place(inspection_id) / "units.json").resolve()


def read_events(inspection_id):
    """``ai_events/`` のイベントを、連番の昇順で返す。無ければ空。"""
    directory = place(inspection_id) / EVENTS_DIR
    if not directory.is_dir():
        return []
    try:
        numbered = workspace.sequence_files(directory)
    except OSError as exc:
        raise workspace.WorkspaceError("AI の候補の記録（ai_events）を列挙できません") from exc
    events = []
    for _, path in numbered:
        event = workspace.read_json(path, "AI の候補の記録（ai_events）")
        if not isinstance(event, dict) or event.get("result") not in (RESULT_ACCEPTED, RESULT_REJECTED):
            raise workspace.WorkspaceError("AI の候補の記録（ai_events）の形が不正です")
        events.append(event)
    return events


def consecutive_rejections(events):
    """イベントの末尾に連なる拒否の数を返す。受理が 1 件あれば、そこで数え終える。"""
    count = 0
    for event in reversed(events):
        if event["result"] != RESULT_REJECTED:
            break
        count += 1
    return count


def accepted_count(events):
    return sum(1 for event in events if event["result"] == RESULT_ACCEPTED)


def locked(events):
    """拒否が ``REJECT_LIMIT`` 回続いた後か。そうなら ``ai_add.py`` も ``ai_finish.py`` も拒否する。"""
    return consecutive_rejections(events) >= REJECT_LIMIT


def write_event(inspection_id, event):
    """イベントを ``ai_events/`` の次の連番として書く。公開したパスを返す。"""
    return workspace.write_next(inspection_id, EVENTS_DIR, event)


def write_done(inspection_id):
    """終了の印（``ai_done.json``）を書く。既にあれば ``WorkspaceError``（上書きしない）。"""
    return workspace.write(inspection_id, DONE_FILE, dict(DONE_PAYLOAD))


def is_done(inspection_id):
    """終了の印があり、内容が ``{"exit": "0"}`` ならば True。無い・壊れている・内容が違うときは False。

    作業領域が無いときは ``WorkspaceError``。
    """
    path: Path = place(inspection_id) / DONE_FILE
    try:
        return workspace.read_json(path, "終了の印") == DONE_PAYLOAD
    except workspace.WorkspaceError:
        return False
