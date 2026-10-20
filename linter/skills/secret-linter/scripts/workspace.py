#!/usr/bin/env python3
"""secret-linter の作業領域（作る・書く・消す）。

1 回の検査の受け渡しデータ（検査単位・機密候補・判定結果）の置き場を扱う。各 script が同じ
ディレクトリから import する（直接実行はしない）ため、終了コードを持たない。失敗は
``WorkspaceError`` で表し、呼び出す script が ``secret_common.error`` で返す。

- 置き場は OS の一時ディレクトリの下に、検査ごとに一意な名前で、モード 0700（作成者だけが読める）で作る。
  検査 ID から置き場が決まる。
- ファイルは、内容が確定してから 1 回の操作で公開する（同じディレクトリの一時ファイルへ書き、
  ``os.link`` で公開する）。``os.link`` は既存のファイルを置き換えないため、公開済みのファイルを
  上書きする経路は無い。
- 書いた後は上書き・修正しない。変わるもの（AI の候補・拒否・判定）は、書くたびに新しい連番の
  ファイル（``<dir>/NNNN.json``）にする。

本モジュールは DES-087 のモジュール表 W-1（作る）・W-2（書く）・W-5（消す）の実装である。
"""

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

PLACE_PREFIX = "secret-linter-"
_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_SEQ_PATTERN = re.compile(r"(\d{4,})\.json")
_MAX_CREATE_ATTEMPTS = 100


class WorkspaceError(Exception):
    """作業領域の操作が完了しなかったことを表す。メッセージに値（本文・候補の値）を含めない。"""


def _validate_id(inspection_id):
    """検査 ID が、``create`` が返す形（32 桁の 16 進数）でなければ ``WorkspaceError``。

    置き場のパスは検査 ID から組み立てるため、パス区切りや ``..`` を含む値を通さない。
    """
    if not isinstance(inspection_id, str) or not _ID_PATTERN.fullmatch(inspection_id):
        raise WorkspaceError(
            "検査 ID が不正です（32 桁の 16 進数でなければなりません）"
        )


def workspace_path(inspection_id):
    """検査 ID から置き場のパスを返す（実在は確かめない）。"""
    _validate_id(inspection_id)
    return Path(tempfile.gettempdir()) / f"{PLACE_PREFIX}{inspection_id}"


def create():
    """実行ごとに一意な置き場を、モード 0700 で作り、検査 ID を返す。

    置き場が既に在れば、別の検査 ID で作り直す（他の検査の置き場に触れない）。
    """
    for _ in range(_MAX_CREATE_ATTEMPTS):
        inspection_id = os.urandom(16).hex()
        path = workspace_path(inspection_id)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise WorkspaceError(f"作業領域を作れません: {exc}") from exc
        return inspection_id
    raise WorkspaceError("作業領域を作れません（一意な置き場を確保できませんでした）")


def existing_workspace(inspection_id):
    """実在する置き場のパスを返す。無い、またはディレクトリでなければ ``WorkspaceError``。"""
    path = workspace_path(inspection_id)
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError as exc:
        raise WorkspaceError("検査 ID の作業領域がありません") from exc
    except OSError as exc:
        raise WorkspaceError(f"作業領域を確かめられません: {exc}") from exc
    if not stat.S_ISDIR(mode):
        raise WorkspaceError(
            "作業領域がディレクトリではありません（シンボリックリンクを含む）"
        )
    return path


def read_json(path, what):
    """作業領域のファイル ``path`` を JSON として読んで返す。作業領域の JSON を読む処理は、これだけである。

    ``what`` は、失敗の理由に載せるファイルの呼び名。無い・読めない・JSON として解釈できないときは
    ``WorkspaceError``（内容を含めないため、例外の文面は載せない）。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(f"{what}がありません") from exc
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"{what}を読めません") from exc


def sequence_filename(number):
    """連番ファイルの名前（``NNNN.json``。4 桁）。連番のファイル名の組み立ては、ここだけである。"""
    return f"{number:04d}.json"


def _check_relative(rel_path):
    """置き場の中の相対パスが、置き場の外を指さないことを確かめる。"""
    parts = Path(rel_path).parts
    if (
        not parts
        or Path(rel_path).is_absolute()
        or any(p in ("", ".", "..") for p in parts)
    ):
        raise WorkspaceError("作業領域の中の相対パスが不正です")


def _publish_text(path, text):
    """``text`` を同じディレクトリの一時ファイルへ書き、既存のファイルを置き換えずに公開する。

    既に ``path`` がある場合は ``FileExistsError``（何も変更しない）。改行は変換せず、そのまま書く。
    """
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _publish_new(path, payload):
    """``payload`` を JSON として公開する（公開の仕方は ``_publish_text`` に従う）。"""
    _publish_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _make_dirs(root, directory):
    """``root`` の下の ``directory`` を、1 段ずつ 0700 で作る（既にあれば何もしない）。

    ``Path.mkdir(parents=True)`` は、中間のディレクトリへ ``mode`` を適用しない（umask 由来の
    既定のモードになる）ため、使わない。
    """
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            pass


def _write_at(inspection_id, rel_path, publish):
    """置き場の ``rel_path`` へ ``publish(パス)`` で 1 回の操作で公開する。既にあれば失敗する。"""
    root = existing_workspace(inspection_id)
    _check_relative(rel_path)
    target = root / rel_path
    try:
        if target.parent != root:
            _make_dirs(root, target.parent)
        publish(target)
    except FileExistsError as exc:
        raise WorkspaceError(
            f"作業領域のファイルは上書きできません: {rel_path}"
        ) from exc
    except OSError as exc:
        raise WorkspaceError(f"作業領域へ書けません: {exc}") from exc
    return target


def write(inspection_id, rel_path, payload):
    """置き場の ``rel_path`` へ ``payload`` を 1 回の操作で公開する。既にあれば失敗する（上書きしない）。

    値は生成・要約・改変せずに置く。サブディレクトリは、無ければ 0700 で作る。
    """
    return _write_at(
        inspection_id, rel_path, lambda target: _publish_new(target, payload)
    )


def write_text(inspection_id, rel_path, text):
    """置き場の ``rel_path`` へ本文（テキスト）を、``write`` と同じ契約で公開する。公開したパスを返す。

    元のファイルが無い検査単位（履歴の追加行・コミットメッセージ）の本文を置くために使う。
    本文は UTF-8 で、改変せずに書く。
    """
    return _write_at(
        inspection_id, rel_path, lambda target: _publish_text(target, text)
    )


def write_next(inspection_id, rel_dir, payload):
    """置き場の ``rel_dir`` の下に、次の連番のファイル（``NNNN.json``。1 始まり、4 桁）として公開する。

    連番は、既存のファイルの最大の番号の次である。同じ番号で先に公開された場合は、既存のファイルを
    置き換えず、次の番号で試す（並行して書いても互いを上書きしない）。公開したパスを返す。
    """
    root = existing_workspace(inspection_id)
    _check_relative(rel_dir)
    directory = root / rel_dir
    try:
        _make_dirs(root, directory)
        for _ in range(_MAX_CREATE_ATTEMPTS):
            number = _last_number(directory) + 1
            target = directory / sequence_filename(number)
            try:
                _publish_new(target, payload)
            except FileExistsError:
                continue
            return target
    except OSError as exc:
        raise WorkspaceError(f"作業領域へ書けません: {exc}") from exc
    raise WorkspaceError(f"連番のファイルを公開できません: {rel_dir}")


def sequence_files(directory):
    """``directory`` の連番ファイル（``NNNN.json``）を、``(番号, パス)`` の昇順の配列で返す。

    連番の規則（桁数・パターン）は本モジュールが 1 か所だけに持つ。連番のファイルを読む側（AI の候補の記録など）も、
    これを使う。``directory`` が無ければ ``FileNotFoundError``、列挙できなければ ``OSError``。
    """
    return sorted(
        (int(m.group(1)), p)
        for p in directory.iterdir()
        if (m := _SEQ_PATTERN.fullmatch(p.name))
    )


def _last_number(directory):
    """``directory`` の連番ファイルの最大の番号を返す（無ければ 0）。"""
    return max((number for number, _ in sequence_files(directory)), default=0)


def last_number(inspection_id, rel_dir):
    """置き場の ``rel_dir`` の連番ファイル（``NNNN.json``）の最大の番号を返す。ディレクトリが無ければ 0。

    ``write_next`` が次に書く番号の 1 つ前であり、最新のファイルを読む側（結果提示・回答の記録）が使う。
    置き場そのものが無ければ ``WorkspaceError``。
    """
    root = existing_workspace(inspection_id)
    _check_relative(rel_dir)
    try:
        return _last_number(root / rel_dir)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise WorkspaceError(f"作業領域を読めません: {exc}") from exc


def remove(inspection_id):
    """置き場の中身をすべて削除する。置き場が無くても成功する。

    置き場がシンボリックリンクなら、辿らず拒否する（何も削除しない）。置き場が自分の所有でない
    場合も拒否する。置き場の中のシンボリックリンクは、辿らずにリンクだけを削除する。
    """
    path = workspace_path(inspection_id)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkspaceError(f"作業領域を確かめられません: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise WorkspaceError("作業領域がシンボリックリンクです。削除しません")
    if not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError("作業領域がディレクトリではありません。削除しません")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise WorkspaceError("作業領域の所有者が異なります。削除しません")
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkspaceError(f"作業領域を削除できません: {exc}") from exc
