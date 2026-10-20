#!/usr/bin/env python3
"""secret-linter のテストが共有する、機密の疑いに当たる入力の部品。

secret-linter はテストファイル自身も検査する。入力の作り方は次の 3 通りで、上にあるものほど優先する。

1. 予約値（``example.com`` 等、規格が実在しないと保証した値）は、ソースへそのまま書く。検査は予約値を通す
2. 「予約値ではない値」として扱いたいテストは、予約値の語をそのまま使い、そのテストの中だけ ``no_reserved()``
   で予約値の定数を空にする（同一プロセスのテスト）。``judge.py`` 等を子プロセスで走らせるテストは
   ``argv(..., no_reserved=True)`` で同じ状態の子プロセスを起動する
3. 予約値の形で表せない値（プライベート IP・会社名のヒント・予約値に近い別名など）だけ、``domain`` /
   ``ip`` / ``legal_en`` / ``legal_ja`` で断片から組み立てる（ソースに値をリテラルで書かない）

同梱の許可リストにある実在ドメイン（``python.org`` 等）は、ソースへそのまま書いてよい。

テストではない（``test_*.py`` に一致しない）。``test_*.py`` が ``sys.path`` に自分のディレクトリを足して
import する（ディレクトリ名にハイフンを含むため、パッケージとしては import できない）。
"""

import contextlib
import sys
from pathlib import Path
from unittest import mock

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[3]
    / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"
)
sys.path.insert(0, str(_SCRIPTS_DIR))

import judgment  # noqa: E402

#: 子プロセスで予約値の定数を空にして script を走らせる実行器
RUNNER = Path(__file__).resolve().with_name("run_no_reserved.py")

#: 語彙の一覧（予約値の語だけ）。``test_vocab.py`` が、全語について本番の設定で pass、``no_reserved()`` の中で
#: confirm になることを確かめる
VOCAB_DOMAINS = (
    "example.com", "docs.example.com", "example.net", "example.org", "a.b.example.org",
    "foo.example", "foo.test", "foo.invalid", "foo.localhost", "localhost",
)
VOCAB_EMAILS = (
    "someone@example.com", "x@mail.example.org", "x@example.net", "x@corp.test", "x@localhost",
)
VOCAB_IPS = (
    "192.0.2.1", "198.51.100.7", "203.0.113.200", "2001:db8::1",
)


@contextlib.contextmanager
def no_reserved():
    """このブロックの中だけ、文書用の予約値（ドメイン・末尾・IP の範囲）の定数を空にする。

    ``docs.example.com`` 等が、予約値ではない値として判定される。``judgment`` を同一プロセスで呼ぶテスト用。
    """
    with (
        mock.patch.object(judgment, "RESERVED_DOMAINS", ()),
        mock.patch.object(judgment, "RESERVED_TLDS", ()),
        mock.patch.object(judgment, "RESERVED_NETWORKS", ()),
    ):
        yield


def argv(script, *args, no_reserved=False):
    """script を子プロセスで走らせる argv を返す。``no_reserved`` が真なら、予約値の定数を空にした状態で走らせる。"""
    if no_reserved:
        return [sys.executable, str(RUNNER), str(script), *args]
    return [sys.executable, str(script), *args]


def domain(*labels, sep="."):
    """ラベル（または既にドットを含む断片）を ``sep`` で連結してドメインを組み立てる。言い換えの形は ``sep`` で作る。

    アプリのパッケージ名（逆ドメイン形式の識別子。予約ドメインを逆にした形ではないもの）も、ラベルを
    ドットでつなぐ同じ組み立てで作る。
    """
    return sep.join(labels)


def ip(*octets):
    """4 つの数から IPv4 アドレスを組み立てる。範囲外の数も受ける。"""
    return ".".join(map(str, octets))


def legal_en(*words):
    """英語の法人格を伴う会社名を組み立てる。"""
    return " ".join(words) + " " + "Inc" + "."


def legal_ja(name):
    """日本語の法人格を伴う会社名を組み立てる。"""
    return "株" + "式会社" + name
