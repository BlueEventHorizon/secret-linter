#!/usr/bin/env python3
"""検査の作業領域の中身を、すべて削除する。

検査 ID が決める置き場（OS の一時ディレクトリの下）をディレクトリごと削除する。置き場が無くても
成功する。置き場がシンボリックリンクのときは辿らず、何も削除せずに失敗する。削除できないときも
失敗する（エラーを握りつぶさない）。エラーの理由に、作業領域の中身（検査単位の本文・検出した値）を
含めない。

本 script は REQ-032 の表示要件（結果をファイルに残さない）の実装（DES-087 W-5）である。

## 終了コード

| code | 意味                                                                                 |
| ---- | ------------------------------------------------------------------------------------ |
| 0    | 削除した、または置き場が無かった（標準出力は ``{}``）                                |
| 1    | 区別のない失敗（標準出力の ``errors`` に理由。削除できない、検査 ID が不正、置き場がシンボリックリンク） |
| 2    | 引数を受理できない（``argparse``）                                                   |

Usage:
    python3 cleanup.py INSPECTION_ID
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_common  # noqa: E402
import workspace  # noqa: E402


def cmd_cleanup(args):
    try:
        workspace.remove(args.inspection_id)
    except workspace.WorkspaceError as exc:
        return secret_common.error(str(exc))
    secret_common.emit({})
    return 0


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("inspection_id")
    return parser


def run_cli(argv=None):
    return cmd_cleanup(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run_cli())
