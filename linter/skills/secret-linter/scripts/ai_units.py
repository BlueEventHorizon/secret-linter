#!/usr/bin/env python3
"""AI 検出（``forge:secret-reader``）へ、検査単位の一覧ファイル（``units.json``）の絶対パスを返す。

検査 ID を受け取り、そのパスを標準出力の ``units_path`` で返す。本文は返さない（AI はパスのファイルを
直接読む）。作業領域が無い、または ``units.json`` を読めないときは失敗する。

本 script は DES-087 のモジュール表 W-6（検査単位のパスを返す）の実装である。

## 終了コード

| code | 意味                                                                                         |
| ---- | -------------------------------------------------------------------------------------------- |
| 0    | 一覧ファイルの絶対パスを返した（標準出力は ``units_path``）                                  |
| 1    | 区別のない失敗（標準出力の ``errors`` に理由。作業領域が無い、検査 ID が不正、``units.json`` を読めない） |
| 2    | 引数を受理できない（``argparse``）                                                           |

Usage:
    python3 ai_units.py INSPECTION_ID
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_events  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402


def cmd_units(args):
    try:
        path = ai_events.units_path(args.inspection_id)
    except workspace.WorkspaceError as exc:
        return secret_common.error(str(exc))
    secret_common.emit({"units_path": str(path)})
    return 0


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    return parser


def run_cli(argv=None):
    return cmd_units(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
