#!/usr/bin/env python3
"""secret-linter の script が共通で使う、出力の部品。

JSON の出力・失敗の返し方・``--project-root`` の引数の型を持つ。各 script が同じディレクトリから import する
（直接実行はしない）。
終了コードは、成功が 0、完了しなかったことが 1（標準出力の ``errors`` に理由）、引数を受理できない
ことが 2（``argparse``）である。
"""

import argparse
import json
import sys
from pathlib import Path


def absolute_path(value):
    """絶対パスだけを受け付ける引数の型（``--project-root``）。"""
    if not Path(value).is_absolute():
        raise argparse.ArgumentTypeError("絶対パスでなければなりません")
    return Path(value)


def emit(payload):
    """成功の結果を、JSON object 1 つとして標準出力へ出す。"""
    json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def error(*messages):
    """失敗を、終了コード 1 と標準出力の ``errors`` で返す。戻り値を終了コードとして返すこと。

    理由に、検査単位の本文や検出した値を含めないこと。
    """
    emit({"errors": list(messages)})
    return 1
