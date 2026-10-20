#!/usr/bin/env python3
"""AI 検出が正常に終えたことを示す印（``ai_done.json``）を、作業領域へ書く。

印の内容は ``{"exit": "0"}`` だけである。成功したときだけ書く。作業領域が無い、拒否が 2 回続いた後
（``ai_add.py`` が連続して拒否した後）、既に印がある、のいずれかのときは失敗し、印を書かない（上書きもしない）。
成功時の標準出力は ``{}`` である。

本 script は DES-087 のモジュール表 W-7（終了の印を書く）の実装である。

## 終了コード

| code | 意味                                                                                                 |
| ---- | ---------------------------------------------------------------------------------------------------- |
| 0    | 終了の印を書いた（標準出力は ``{}``）                                                                |
| 1    | 区別のない失敗（標準出力の ``errors`` に理由。作業領域が無い、拒否が 2 回続いた後、既に印がある。印は書かない） |
| 2    | 引数を受理できない（``argparse``）                                                                   |

Usage:
    python3 ai_finish.py INSPECTION_ID
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_events  # noqa: E402
import secret_common  # noqa: E402
import workspace  # noqa: E402


def cmd_finish(args):
    try:
        events = ai_events.read_events(args.inspection_id)
        if ai_events.locked(events):
            return secret_common.error(
                f"拒否が {ai_events.REJECT_LIMIT} 回続いたため、終了の印を書きません"
            )
        ai_events.write_done(args.inspection_id)
    except workspace.WorkspaceError as exc:
        return secret_common.error(str(exc))
    secret_common.emit({})
    return 0


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    return parser


def run_cli(argv=None):
    return cmd_finish(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
