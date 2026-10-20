#!/usr/bin/env python3
"""AI 検出が正常に終えたかを、終了の印（``ai_done.json``）の有無だけで返す。

印があり、内容が ``{"exit": "0"}`` なら成功する。印が無い（書かれなかった・落ちた・起動できなかった）・
壊れている・内容が違う、作業領域が無い、のいずれも失敗する。AI の応答や自己申告は見ない。理由に値を含めない。

本 script は DES-087 のモジュール表 W-4（終了を確かめる）の実装である。

## 終了コード

| code | 意味                                                                                                  |
| ---- | ----------------------------------------------------------------------------------------------------- |
| 0    | 終了の印がある（標準出力は ``{}``）                                                                   |
| 1    | 区別のない失敗（標準出力の ``errors`` に理由。印が無い・壊れている・内容が違う、作業領域が無い、検査 ID が不正） |
| 2    | 引数を受理できない（``argparse``）                                                                    |

Usage:
    python3 ai_check.py INSPECTION_ID
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_events  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402


def cmd_check(args):
    try:
        done = ai_events.is_done(args.inspection_id)
    except workspace.WorkspaceError as exc:
        return secret_common.error(str(exc))
    if not done:
        return secret_common.error(
            "AI 検出の終了の印がありません（AI 検出は正常に終えていません）"
        )
    secret_common.emit({})
    return 0


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    return parser


def run_cli(argv=None):
    return cmd_check(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
