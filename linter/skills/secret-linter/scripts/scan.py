#!/usr/bin/env python3
"""secret-linter の検査を始める。作業領域を作り、範囲を解決して検査単位（units.json）を書き、
パターン検出（``patterns.py``）の結果（pattern.json）を書く。

範囲の指定は 1 つだけ（``--diff`` / ``--branch`` / ``--dirs パス…`` / ``--files パス…``）で、省略時は
``--diff`` とする。``--branch`` は、リモートに無い commit の追加行とコミットメッセージを検査単位にする
（本文は作業領域へ書く。``history.py``）。
対象が 0 件でもエラーにせず、検査単位が 0 件の ``units.json`` を書いて 0 を返す（``pattern.json`` は、
候補が 0 件のものを書く）。成功時の標準出力は、検査 ID・検査単位の件数・パターンの機密候補の件数
（``pattern_candidate_count``）である。候補の値は標準出力に出さず、作業領域の ``pattern.json`` にだけ置く。

1 を返すときは、作った作業領域を自分で消してから返す（検査 ID を返さないため、検査制御が
``cleanup.py`` に渡せない）。消去に失敗したときも握りつぶさず、``errors`` に両方の理由を載せる。
``errors`` に検査単位の本文を含めない。

本 script は DES-087 のモジュール表 W-1・T-1・P-1・W-2 の実装である。

## 終了コード

| code | 意味                                                                                           |
| ---- | ---------------------------------------------------------------------------------------------- |
| 0    | 検査単位とパターン検出の結果を書いた（0 件を含む。標準出力は ``inspection_id``・``unit_count``・``pattern_candidate_count``） |
| 1    | 区別のない失敗（標準出力の ``errors`` に理由。指定したファイル・ディレクトリが無い、git が失敗した、作業領域を作れない・書けない、検査単位の本文を読めない） |
| 2    | 引数を受理できない（``argparse``。複数の範囲の指定、語彙外の引数、絶対パスでない ``--project-root``） |

Usage:
    python3 scan.py --project-root ABS_PATH [--diff | --branch | --dirs PATH... | --files PATH...]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history  # noqa: E402
import patterns  # noqa: E402
import secret_common  # noqa: E402
import targets  # noqa: E402
import workspace  # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser()
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--diff", action="store_true")
    scope.add_argument("--branch", action="store_true")
    scope.add_argument("--dirs", nargs="+", metavar="PATH")
    scope.add_argument("--files", nargs="+", metavar="PATH")
    parser.add_argument(
        "--project-root", required=True, type=secret_common.absolute_path
    )
    return parser


def _resolve(args):
    """範囲の指定から ``(label, 絶対パス)`` の並びを得る。省略時は ``--diff``。"""
    if args.dirs is not None:
        return targets.resolve_dirs(args.project_root, args.dirs)
    if args.files is not None:
        return targets.resolve_files(args.project_root, args.files)
    return targets.resolve_diff(args.project_root)


def _units_and_skipped(args, inspection_id):
    """範囲の指定から、``unit_id`` を採番した検査単位と、スキップを得る。"""
    if args.branch:
        pending, skipped = history.resolve_branch(args.project_root, inspection_id)
        return targets.number_units(pending), skipped
    return targets.build_units(_resolve(args))


def _fail(inspection_id, message):
    """作った作業領域を消してから、失敗（終了コード 1）を返す。消去の失敗も理由に載せる。"""
    messages = [message]
    if inspection_id is not None:
        try:
            workspace.remove(inspection_id)
        except workspace.WorkspaceError as exc:
            messages.append(f"作業領域の消去にも失敗しました: {exc}")
    return secret_common.error(*messages)


def cmd_scan(args):
    try:
        inspection_id = workspace.create()
    except workspace.WorkspaceError as exc:
        return secret_common.error(str(exc))
    try:
        units, skipped = _units_and_skipped(args, inspection_id)
        workspace.write(
            inspection_id, "units.json", {"units": units, "skipped": skipped}
        )
        pattern = patterns.detect(units)
        workspace.write(inspection_id, "pattern.json", pattern)
    except (
        targets.TargetError,
        patterns.PatternError,
        workspace.WorkspaceError,
    ) as exc:
        return _fail(inspection_id, str(exc))
    except Exception:
        # 想定外の例外でも作業領域を残さない。例外そのものは握りつぶさず再送出する
        workspace.remove(inspection_id)
        raise
    secret_common.emit(
        {
            "inspection_id": inspection_id,
            "unit_count": len(units),
            "pattern_candidate_count": len(pattern["candidates"]),
        }
    )
    return 0


def run_cli(argv=None):
    return cmd_scan(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
