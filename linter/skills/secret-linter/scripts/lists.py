#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""secret-linter の 2 つのリスト（許可リスト・機密の名前リスト）を読み書きするモジュール。

REQ-032 FNC-3004 / DES-087 L-1「リスト管理（読む）」・L-3「リストへ追記する」の実装である。
他のスクリプトが import して使う部品であり、CLI は持たない（終了コードを返さない）。

## 読むリスト

| リスト           | 置き場                                                                  | 関数                                    |
| ---------------- | ----------------------------------------------------------------------- | --------------------------------------- |
| 許可リスト       | 同梱分（`assets/` の 3 ファイル）                                       | `load_bundled_allowlist()`              |
| 許可リスト       | プロジェクトの `.secret-linter/allowlist.yaml`                           | `load_project_allowlist(project_root)`  |
| 機密の名前リスト | 利用者のホームの `~/.project-secret.yaml`                               | `load_secret_names(home)`               |
| 全て             | 上の 3 つをまとめて読む                                                 | `load_all(project_root, home)`          |

任意のファイルを読む低水準の入口は `load_list(path, display_name)`、
文字列を解析する入口は `parse_list(text, display_name)` である。

## 書くリスト（追記）

| リスト           | 関数                                                         |
| ---------------- | ------------------------------------------------------------ |
| 許可リスト       | `append_project_allowlist(project_root, values)`             |
| 機密の名前リスト | `append_secret_names(values, home)`                          |
| 任意のファイル   | `append_items(path, display_name, values, create_mode)`      |

追記は、既存のファイルの全体（コメント・空行・既存の項目・並び・改行コード・BOM）を変えずに、
末尾へ項目を加える。同じディレクトリの一時ファイルへ全体を書いてから、1 回の操作（`os.replace`）で
置き換える。ファイルが無ければ作成する（`.secret-linter/` が無ければ、そのディレクトリも作る）。
追記の前に既存の内容を `parse_list` で検査し、形式不正なら何も書かずに `ListFormatError`（行番号と理由だけ）。
同じ値が既に項目にあるとき（同じ呼び出しの中の重複を含む）は、重複して追記しない。

追記する項目は、`"` で囲む（値に `"` を含むときは `'` で囲む）。引用符の中は、エスケープを
解釈しない。既存の項目のインデントの幅に合わせる（項目が無ければ 0）。次の値は、リストの書式で
表せないため、書かずに `ListWriteError` とする: 空の値、制御文字（改行・タブを含む）を含む値、
`"` と `'` の両方を含む値。追記した行が `parse_list` で元の値に戻ることを、書く前に確かめる。
`ListWriteError` のメッセージに値を含めない。

## 同梱の許可リストの表し方

DES-087 は、同梱の許可リストを「パスまで公開のドメイン」「ホストだけ公開のドメイン」
「公開の開発系パッケージの接頭辞」の 3 種類に区別して持つと定め、区別の表し方を実装に委ねている。
本モジュールは**種類ごとにファイルを分ける**方式を採る（項目の書式を増やさず、リストの形式を
文字列リスト 1 種類に保つため）。

| ファイル                                | 種類                                 | `BundledAllowlist` のフィールド |
| --------------------------------------- | ------------------------------------ | ------------------------------- |
| `assets/public_domains_full.yaml`       | パスまで公開のドメイン               | `full_domains`                  |
| `assets/public_domains_host_only.yaml`  | ホストだけ公開のドメイン             | `host_only_domains`             |
| `assets/public_package_prefixes.yaml`   | 公開の開発系パッケージの接頭辞       | `package_prefixes`              |

一致規則（接尾辞で通す・ホストだけ通す・ラベルの境界で前置き一致させる等）は機密判定の責務であり、
本モジュールは項目を読んで返すだけである。

## 形式（制約付き YAML サブセット）

文字列だけのブロックリストである。

- `- 項目` を 1 行に 1 つ書く
- コメント行（`#` で始まる行）と空行を許す。項目の後ろの行内コメント（空白に続く `#`）は形式不正である
- 項目は、引用符なし、または `"` か `'` で囲む（引用符の中は、エスケープを解釈せずそのまま値とする）
- 項目のインデントは、全項目で同じスペースの幅でなければならない
- UTF-8（先頭の BOM と CRLF は許す）

これ以外の書式（入れ子・複数行・アンカー・エイリアス・タグ・flow style・`key: value`
の行）は形式不正である。許可リストの記法である `*.` で始まる項目（`*.example.com`）は、
引用符なしでも受理する。`*.` で始まらない `*` の項目（`*alias`）は、エイリアスとして形式不正にする。

## エラーの扱い

- **ファイル不在は正常**であり、空のリストを返す（エラーにも警告にもしない）
- **形式不正は `ListFormatError`**。メッセージは表示名・行番号・理由だけで構成し、
  **行の内容を含めない**（機密の値をエラー経路へ流さない。PyYAML のエラーは該当行の内容を
  含むため使わず、自前で解析する）。理由は固定の文言であり、入力から組み立てない
- 読み取れない（UTF-8 でない・権限不備等）場合も `ListFormatError` とする。行番号は無く、
  例外種別だけを載せる

## 依存

Python 標準ライブラリのみ。
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import NamedTuple

# --- 定数 ---------------------------------------------------------------------

#: 本モジュールが置かれた scripts/ の 1 つ上（スキルのディレクトリ）の assets/
_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

#: 同梱の許可リスト（パスまで公開のドメイン）
BUNDLED_FULL_FILENAME = "public_domains_full.yaml"

#: 同梱の許可リスト（ホストだけ公開のドメイン）
BUNDLED_HOST_ONLY_FILENAME = "public_domains_host_only.yaml"

#: 同梱の許可リスト（公開の開発系パッケージの接頭辞）
BUNDLED_PACKAGE_PREFIXES_FILENAME = "public_package_prefixes.yaml"

#: project root からの、プロジェクト許可リストの相対位置
PROJECT_ALLOWLIST_RELATIVE_PATH = Path(".secret-linter") / "allowlist.yaml"

#: プロジェクト許可リストのエラー表示名（絶対パスは載せない）
PROJECT_ALLOWLIST_DISPLAY_NAME = ".secret-linter/allowlist.yaml"

#: 機密の名前リストのファイル名（ホーム直下）
SECRET_NAMES_FILENAME = ".project-secret.yaml"  # secrets-scan: ignore

#: 機密の名前リストのエラー表示名
SECRET_NAMES_DISPLAY_NAME = "~/.project-secret.yaml"  # secrets-scan: ignore

#: 引用符なしの項目の先頭に現れたら対象外構文とみなす文字
_UNSUPPORTED_ITEM_PREFIXES = (
    ("&", "アンカー"),
    ("*", "エイリアス"),
    ("!", "タグ"),
    ("|", "複数行文字列"),
    (">", "複数行文字列"),
    ("[", "flow style"),
    ("{", "flow style"),
)


# --- 例外 ---------------------------------------------------------------------


class ListFormatError(Exception):
    """リストのファイルを解析できなかった。

    メッセージには表示名・行番号・理由だけを含め、行の内容は含めない。
    ファイル不在は本例外ではない（不在は正常であり空のリストを返す）。

    Attributes:
        display_name: エラーに表示するファイル名（絶対パスではない）
        lineno: 形式不正の行番号（1 始まり）。行に紐づかない失敗（読み取り不能）は None
        reason: 理由（固定の文言）
    """

    def __init__(self, display_name: str, lineno: int | None, reason: str):
        self.display_name = display_name
        self.lineno = lineno
        self.reason = reason
        if lineno is None:
            message = f"{display_name} を解析できません: {reason}"
        else:
            message = f"{display_name} の {lineno} 行目を解析できません: {reason}"
        super().__init__(message)


class ListWriteError(Exception):
    """リストへ追記できなかった（値がリストの書式で表せない・書き込めない）。

    メッセージには表示名と固定の理由（例外種別を含む）だけを含め、追記しようとした値は含めない。
    既存のリストの形式不正は本例外ではなく `ListFormatError` である。
    """


# --- 公開 I/F -----------------------------------------------------------------


class BundledAllowlist(NamedTuple):
    """同梱の許可リスト。2 種類のドメインと、パッケージの接頭辞を区別して持つ。"""

    #: パスまで公開のドメイン
    full_domains: list
    #: ホストだけ公開のドメイン
    host_only_domains: list
    #: 公開の開発系パッケージの接頭辞
    package_prefixes: list


class Lists(NamedTuple):
    """secret-linter が読む全てのリスト。"""

    bundled_allowlist: BundledAllowlist
    project_allowlist: list
    secret_names: list


def parse_list(text: str, display_name: str) -> list:
    """リストの文字列を解析し、項目の文字列のリストを返す。

    形式不正なら `ListFormatError`（行番号と理由だけ。行の内容は含めない）。
    """
    if text.startswith("﻿"):
        text = text[1:]

    items: list = []
    item_indent = None  # 最初の項目で確定する

    for lineno, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.rstrip()  # CRLF の \r も落ちる
        stripped = line.strip()

        # コメント行・空行は無視する
        if not stripped or stripped.startswith("#"):
            continue

        leading = line[: len(line) - len(line.lstrip())]
        if "\t" in leading:
            raise ListFormatError(display_name, lineno, "インデントにタブは使えません")
        indent = len(leading)

        if not (stripped == "-" or stripped.startswith("- ")):
            raise ListFormatError(
                display_name,
                lineno,
                "リスト項目（`- 項目`）の行ではありません"
                "（mapping・文書区切り・複数行の続き等は対象外です）",
            )

        if item_indent is None:
            item_indent = indent
        elif indent != item_indent:
            raise ListFormatError(
                display_name,
                lineno,
                "インデントの幅が他の項目と一致しません（入れ子は対象外です）",
            )

        items.append(_parse_item(stripped, display_name, lineno))

    return items


def load_list(path, display_name: str) -> list:
    """ファイルを読んで項目のリストを返す。

    ファイルが存在しなければ空のリストを返す（エラー・警告なし）。
    形式不正・読み取り不能は `ListFormatError`。
    """
    text = _read_text(path, display_name)
    return [] if text is None else parse_list(_universal_newlines(text), display_name)


def load_bundled_allowlist() -> BundledAllowlist:
    """同梱の許可リスト（公開ドメイン・公開の開発系パッケージの接頭辞）を読む。"""
    return BundledAllowlist(
        full_domains=load_list(
            _ASSETS_DIR / BUNDLED_FULL_FILENAME, BUNDLED_FULL_FILENAME
        ),
        host_only_domains=load_list(
            _ASSETS_DIR / BUNDLED_HOST_ONLY_FILENAME, BUNDLED_HOST_ONLY_FILENAME
        ),
        package_prefixes=load_list(
            _ASSETS_DIR / BUNDLED_PACKAGE_PREFIXES_FILENAME,
            BUNDLED_PACKAGE_PREFIXES_FILENAME,
        ),
    )


def load_project_allowlist(project_root) -> list:
    """プロジェクトの `.secret-linter/allowlist.yaml` を読む。無ければ空のリスト。"""
    return load_list(
        project_allowlist_path(project_root), PROJECT_ALLOWLIST_DISPLAY_NAME
    )


def load_secret_names(home=None) -> list:
    """利用者のホームの `~/.project-secret.yaml` を読む。無ければ空のリスト。

    `home` を省略するとログインユーザーのホームを使う（テストでは一時ディレクトリを渡す）。
    """
    return load_list(secret_names_path(home), SECRET_NAMES_DISPLAY_NAME)


def project_allowlist_path(project_root) -> Path:
    """プロジェクトの許可リストのファイルのパス（実在は確かめない）。"""
    return Path(project_root) / PROJECT_ALLOWLIST_RELATIVE_PATH


def secret_names_path(home=None) -> Path:
    """機密の名前リストのファイルのパス（実在は確かめない）。`home` を省略するとログインユーザーのホーム。"""
    base = Path(home) if home is not None else Path.home()
    return base / SECRET_NAMES_FILENAME


def load_all(project_root, home=None) -> Lists:
    """2 つのリスト（許可リストは同梱分とプロジェクト分）をまとめて読む。

    いずれかが形式不正なら `ListFormatError`。
    """
    return Lists(
        bundled_allowlist=load_bundled_allowlist(),
        project_allowlist=load_project_allowlist(project_root),
        secret_names=load_secret_names(home),
    )


def append_items(path, display_name: str, values, create_mode: int = 0o600) -> int:
    """リストのファイル `path` の末尾へ、`values` の項目を追記する。追記した件数を返す。

    既存の内容を変えず、同じディレクトリの一時ファイルへ全体を書いてから `os.replace` で置き換える
    （詳細はモジュールの docstring）。ファイルが無ければ、`create_mode` のモードで作成する。
    既に項目にある値と、`values` の中で重複する値は、追記しない（全て重複なら何も書かず 0 を返す）。

    既存のリストの形式不正・読み取り不能は `ListFormatError`（何も書かない）。値がリストの書式で
    表せない・書き込めないときは `ListWriteError`（何も書かない）。
    """
    values = list(values)
    for value in values:
        check_representable(value, display_name)

    raw = _read_text(path, display_name)
    existing_text = "" if raw is None else raw
    existing = parse_list(_universal_newlines(existing_text), display_name)

    new_values = []
    for value in values:
        if value not in existing and value not in new_values:
            new_values.append(value)
    if not new_values:
        return 0

    eol = "\r\n" if "\r\n" in existing_text else "\n"
    indent = _item_indent(existing_text)
    addition = "".join(f"{indent}- {_quote(value)}{eol}" for value in new_values)
    if existing_text and not existing_text.endswith(("\n", "\r")):
        addition = eol + addition
    _replace_file(Path(path), existing_text + addition, display_name, create_mode)
    return len(new_values)


def append_project_allowlist(project_root, values) -> int:
    """プロジェクトの `.secret-linter/allowlist.yaml` へ追記する（無ければ、ディレクトリごと作成する）。"""
    return append_items(
        project_allowlist_path(project_root),
        PROJECT_ALLOWLIST_DISPLAY_NAME,
        values,
        create_mode=0o644,
    )


def append_secret_names(values, home=None) -> int:
    """利用者のホームの `~/.project-secret.yaml` へ追記する（無ければ、作成者だけが読める権限で作成する）。"""
    return append_items(
        secret_names_path(home), SECRET_NAMES_DISPLAY_NAME, values, create_mode=0o600
    )


# --- 内部 ---------------------------------------------------------------------


def _read_text(path, display_name: str):
    """ファイルを UTF-8 の文字列（改行コードは変換しない）として読む。不在なら None。

    読み取れない・UTF-8 でないときは `ListFormatError`（行番号は無い。例外種別だけを載せる）。
    """
    try:
        return Path(path).read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError:
        raise ListFormatError(
            display_name, None, "UTF-8 テキストとして読み取れません"
        ) from None
    except OSError as exc:
        # 不在以外の読取失敗（権限不備・ディレクトリである等）。メッセージには
        # 例外種別のみ載せ、絶対パスは含めない
        raise ListFormatError(
            display_name, None, f"読み取れません（{type(exc).__name__}）"
        ) from None


def _universal_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _item_indent(text: str) -> str:
    """最初の項目の行のインデント（空白）を返す。項目が無ければ空。"""
    for line in _universal_newlines(text).split("\n"):
        stripped = line.strip()
        if stripped == "-" or stripped.startswith("- "):
            return line[: len(line) - len(line.lstrip())]
    return ""


def _quote(value: str) -> str:
    if '"' not in value:
        return f'"{value}"'
    return f"'{value}'"


def check_representable(value, display_name: str) -> None:
    """値がリストの書式で表せることを確かめる。表せなければ `ListWriteError`（値を含めない）。

    `append_items` が書く前に呼ぶ。書く前に全ての値を検証したい呼び出し元も、同じ検査を使う。
    """
    if not isinstance(value, str) or not value:
        raise ListWriteError(
            f"{display_name} へ追記できません: 空の値は項目にできません"
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ListWriteError(
            f"{display_name} へ追記できません: 制御文字（改行を含む）を含む値は項目にできません"
        )
    if '"' in value and "'" in value:
        raise ListWriteError(
            f"{display_name} へ追記できません: `\"` と `'` の両方を含む値は項目にできません"
        )
    try:
        round_trip = parse_list(f"- {_quote(value)}", display_name)
    except ListFormatError:
        round_trip = None
    if round_trip != [value]:
        raise ListWriteError(
            f"{display_name} へ追記できません: リストの書式で表せない値です"
        )


def _replace_file(path: Path, text: str, display_name: str, create_mode: int) -> None:
    """`text` を同じディレクトリの一時ファイルへ書き、`os.replace` で `path` を置き換える。

    既存のファイルのモードは引き継ぐ。`path` がシンボリックリンクなら、リンク先のファイルを置き換える
    （リンクを通常のファイルで置き換えない）。失敗したら一時ファイルを消して `ListWriteError`。
    """
    target = Path(os.path.realpath(path))
    tmp = None
    try:
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = create_mode
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
        tmp = None
    except OSError as exc:
        raise ListWriteError(
            f"{display_name} へ書き込めません（{type(exc).__name__}）"
        ) from None
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


def _parse_item(stripped: str, display_name: str, lineno: int) -> str:
    """`- 項目` の 1 行を文字列として解析する。"""
    if stripped == "-":
        raise ListFormatError(
            display_name, lineno, "値のないリスト項目（入れ子・mapping）は対象外です"
        )
    item_text = stripped[2:].strip()
    if not item_text or item_text.startswith("#"):
        raise ListFormatError(
            display_name, lineno, "値のないリスト項目（入れ子・mapping）は対象外です"
        )
    if item_text[0] in ('"', "'"):
        return _parse_quoted(item_text, display_name, lineno)

    text = item_text
    _reject_inline_comment(text, display_name, lineno)
    # 許可リストの記法 `*.` で始まる項目は、エイリアスではなく項目として受理する
    if not text.startswith("*."):
        for prefix, syntax_name in _UNSUPPORTED_ITEM_PREFIXES:
            if text.startswith(prefix):
                raise ListFormatError(
                    display_name,
                    lineno,
                    f"対象外の構文（{syntax_name}）は解析できません",
                )
    if text == "-" or text.startswith("- "):
        raise ListFormatError(display_name, lineno, "入れ子のリストは対象外です")
    if text.endswith(":") or ": " in text:
        raise ListFormatError(
            display_name, lineno, "mapping をリスト項目にする構文は対象外です"
        )
    return text


def _parse_quoted(item_text: str, display_name: str, lineno: int) -> str:
    """引用符で囲まれた項目を解析する。引用符の後に文字列（行内コメントを含む）は許さない。"""
    quote = item_text[0]
    closing = item_text.find(quote, 1)
    if closing < 0:
        raise ListFormatError(display_name, lineno, "引用符が閉じていません")
    if item_text[closing + 1 :].strip():
        raise ListFormatError(
            display_name, lineno, "引用符の後に解析できない文字列が続いています"
        )
    value = item_text[1:closing]
    if not value:
        raise ListFormatError(display_name, lineno, "空の項目は対象外です")
    return value


def _reject_inline_comment(text: str, display_name: str, lineno: int) -> None:
    """行内コメント（空白に続く `#`）を形式不正にする。

    コメントは行全体を占める行だけが許される。値の途中に空白なしで現れる `#` は値の一部である。
    """
    for i, ch in enumerate(text):
        if ch == "#" and i > 0 and text[i - 1] in (" ", "\t"):
            raise ListFormatError(
                display_name,
                lineno,
                "行内コメントは対象外です（コメントは行全体に書きます）",
            )
