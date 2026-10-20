#!/usr/bin/env python3
"""secret-linter の対象解決（--diff / --dirs / --files）と、検査単位の採番。

検査する範囲を、検査単位（``kind`` が ``file``）と、読めずにスキップしたものへ解決する。``--branch``
（履歴の追加行・コミットメッセージ）の解決は ``history.py`` が担い、検査単位の採番（``number_units``）、
スキップの理由の語彙、上限、``TargetError`` を本モジュールと共有する。各 script が
同じディレクトリから import する（直接実行はしない）ため、終了コードを持たない。失敗は
``TargetError`` で表し、呼び出す script が ``secret_common.error`` で返す。

- ``--diff`` は ``git_targets.get_diff_targets``、``--dirs`` は ``git_targets.get_dir_targets`` を使う
  （同じディレクトリの ``git_targets.py``。列挙を本モジュールに持たない）。git の実行の失敗（``RuntimeError``）は ``TargetError`` にする。
- UTF-8 として解釈できないパスは、``--diff`` / ``--dirs``（git の出力の ``UnicodeDecodeError``）も
  ``--files``（引数の ``UnicodeEncodeError``）も、読めたことにせず ``TargetError``（検査を実行できなかった）にする。
  ``units.json`` は UTF-8 の JSON であり、解釈できないパスを ``label`` にできないため。理由にパスを含めない。
- ``--files`` は指定されたファイルの全体で、プロジェクトルートの外の絶対パスも受け付ける。
  相対パスはプロジェクトルートからの相対として扱う。ファイルとして実在しないものが 1 つでもあれば失敗にする。
- NUL バイトを含む・読めないファイルは、検査単位にせず、理由（``binary`` / ``unreadable``）付きで
  記録する。理由に本文を含めない。大きさでは除外しない（除外すると、巨大なファイルの中の機密が、検査されない
  まま合格になる）。``deleted``（ファイルが削除された commit。追加行が無い）は、履歴の解決
  （``history.py``）だけが使う。

本モジュールは DES-087 のモジュール表 T-1（範囲を解決する）の、``--diff`` / ``--dirs`` / ``--files``
の実装と、``--branch`` を含む検査単位の採番（``unit_id`` を ``U0001`` 形式の連番にする）である。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_targets  # noqa: E402

#: 読めないものの理由の語彙（閉じた集合）。
SKIP_BINARY = "binary"
SKIP_UNREADABLE = "unreadable"
SKIP_DELETED = "deleted"


#: UTF-8 として解釈できないパスの理由（パスそのものは含めない）。units.json は UTF-8 の JSON のため、
#: 解釈できないパスは検査単位の label にできず、読めたことにせず失敗にする。
_NON_UTF8_PATH_MESSAGE = "UTF-8 として解釈できないパスを含むため、対象を解決できません"


class TargetError(Exception):
    """対象を解決できなかったことを表す。メッセージに検査単位の本文を含めない。"""


def _unit_id(number):
    return f"U{number:04d}"


def _classify(path):
    """ファイルを検査できるかを返す。検査できれば None、できなければ理由の語彙。"""
    try:
        raw = path.read_bytes()
    except OSError:
        return SKIP_UNREADABLE
    if b"\x00" in raw:
        return SKIP_BINARY
    return None


def number_units(pending):
    """``unit_id`` を持たない検査単位の並びへ、``U0001`` 形式の連番の ``unit_id`` を先頭に付ける。

    ``pending`` の各要素は ``kind`` / ``label`` / ``text_path``（と、あれば ``line_numbers``）を持つ辞書である。
    ``--branch`` でも同じ採番を使う。
    """
    return [{"unit_id": _unit_id(i), **item} for i, item in enumerate(pending, start=1)]


def build_units(entries):
    """``(label, 絶対パス)`` の並びを、検査単位の配列と、スキップの配列へ分ける。

    検査単位は ``kind`` が ``file`` で、``text_path`` は元のファイルの絶対パスである（本文を複製しない）。
    """
    pending = []
    skipped = []
    for label, path in entries:
        reason = _classify(path)
        if reason is not None:
            skipped.append({"label": label, "reason": reason})
            continue
        pending.append({"kind": "file", "label": label, "text_path": str(path)})
    return number_units(pending), skipped


def resolve_diff(project_root):
    """``--diff``: 未 commit の変更ファイルの全体（削除を除く）。``(label, 絶対パス)`` の並び。"""
    try:
        files = git_targets.get_diff_targets(project_root)
    except RuntimeError as exc:
        raise TargetError(str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise TargetError(_NON_UTF8_PATH_MESSAGE) from exc
    return [(f, project_root / f) for f in files]


def resolve_dirs(project_root, dirs):
    """``--dirs``: 指定ディレクトリ配下のファイル全体（``.gitignore`` を尊重）。

    ``get_dir_targets`` は不在のディレクトリを黙って空にするため、呼ぶ前に実在を確かめる。
    """
    missing = [d for d in dirs if not (project_root / d).is_dir()]
    if missing:
        raise TargetError(
            f"指定されたディレクトリが見つかりません: {', '.join(missing)}"
        )
    try:
        files = git_targets.get_dir_targets(project_root, [d.rstrip("/") or d for d in dirs])
    except RuntimeError as exc:
        raise TargetError(str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise TargetError(_NON_UTF8_PATH_MESSAGE) from exc
    return [(f, project_root / f) for f in files]


def resolve_files(project_root, files):
    """``--files``: 指定ファイルの全体。プロジェクトルートの外も受け付ける。

    ラベルは指定されたとおりのパス、``text_path`` は絶対パスにする。
    """
    entries = []
    missing = []
    seen = set()
    for given in files:
        if given in seen:
            continue
        seen.add(given)
        try:
            given.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise TargetError(_NON_UTF8_PATH_MESSAGE) from exc
        path = Path(given)
        if not path.is_absolute():
            path = project_root / path
        if not path.is_file():
            missing.append(given)
            continue
        entries.append((given, path))
    if missing:
        raise TargetError(f"指定されたファイルが見つかりません: {', '.join(missing)}")
    return entries
