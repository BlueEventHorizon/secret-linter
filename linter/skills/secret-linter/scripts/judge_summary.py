#!/usr/bin/env python3
"""secret-linter の判定の標準出力（``judge.py`` と ``answer.py`` が返す、同じ形の JSON）を組み立てる。

各 script が同じディレクトリから import する（直接実行はしない）ため、終了コードを持たない。

## 標準出力の形（終了コード 0）

``class``（全体の区分: fail・confirm・pass）、``confirm``（要確認の候補の一覧。``candidate_id``・``unit_id``・
``label``・``line``・``kind``・``value``、履歴なら ``commit``）、``pass_count``・``fail_count``（件数のみ）、
``suppressed``（抑制マーカー付きの件数と位置）、``skipped``（スキップの一覧）。要確認の値は、利用者へ質問するために
返す。fail・pass の候補の値は出さない。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import judgment  # noqa: E402


def _confirm_entry(candidate, labels):
    entry = {
        "candidate_id": candidate["candidate_id"],
        "unit_id": candidate.get("unit_id"),
        "label": labels.get(candidate.get("unit_id")),
        "line": candidate.get("line"),
        "kind": candidate["kind"],
        "value": candidate["value"],
    }
    if "commit" in candidate:
        entry["commit"] = candidate["commit"]
    return entry


def build_summary(result, candidates, units, suppressed, skipped):
    """判定結果（``judgment.judge`` / ``judgment.rejudge`` の戻り値）から、標準出力の JSON を作る。"""
    labels = {u.get("unit_id"): u.get("label") for u in units if isinstance(u, dict)}
    by_id = {c["candidate_id"]: c for c in candidates}
    classes = [r["class"] for r in result["candidates"]]
    return {
        "class": result["class"],
        "confirm": [
            _confirm_entry(by_id[r["candidate_id"]], labels)
            for r in result["candidates"] if r["class"] == judgment.CONFIRM
        ],
        "pass_count": classes.count(judgment.PASS),
        "fail_count": classes.count(judgment.FAIL),
        "suppressed": suppressed,
        "skipped": skipped,
    }
