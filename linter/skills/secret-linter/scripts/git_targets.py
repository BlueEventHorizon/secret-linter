#!/usr/bin/env python3
"""secret-linter が git の状態から検査対象ファイルを列挙し、git を実行する部品。

``targets.py``（``--diff`` / ``--dirs``）と ``history.py``（``--branch``）が、同じディレクトリから
import して使う（直接実行はしない）。標準ライブラリのみ使用する（``git`` は subprocess で呼ぶ）。

## 公開 I/F

| 関数                                  | 返り値                                                                        |
| ------------------------------------- | ----------------------------------------------------------------------------- |
| ``run_git(args, project_root)``       | ``(returncode, stdout, stderr)``。git を起動できなければ returncode 1         |
| ``get_diff_targets(project_root)``    | 未 commit 変更（staged + unstaged）+ 未追跡ファイルの昇順リスト（削除を除く） |
| ``get_dir_targets(project_root, dirs)`` | 指定ディレクトリ配下のファイルの昇順リスト（``.gitignore`` を尊重）           |

列挙に失敗したら ``RuntimeError`` を送出する。パスはすべてプロジェクトルート相対で返す。
"""

import subprocess
from pathlib import Path


def run_git(args, project_root: Path):
    """git コマンドを実行し (returncode, stdout, stderr) を返す。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return 1, "", f"git 実行に失敗しました: {exc}"
    return result.returncode, result.stdout, result.stderr


def _parse_porcelain_status_z(stdout: str) -> list[str]:
    """``git status --porcelain=v1 -z --untracked-files=all`` の出力をファイル一覧へ変換する。

    staged / unstaged / 未追跡ファイルを列挙する。完全削除（``D``）は
    検査時に読めないため対象から除外する。rename/copy はリネーム後の
    パスを採用する。NUL 区切り (``-z``) を使うことで、非 ASCII ファイル名の
    C-style クォート化（例: 日本語ファイル名が ``"\\346\\227..."`` と出力される事象）
    を回避し、rename の `` -> `` 文字列パースの曖昧性も解消する（rename/copy は
    新パスの後続 NUL フィールドに旧パスが別エントリとして続く）。
    """
    files: list[str] = []
    seen: set[str] = set()

    entries = stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry or len(entry) < 3:
            continue
        xy = entry[:2]
        path = entry[3:]

        # rename/copy (先頭が R または C) は新パスの直後に旧パスが別 NUL エントリで続く
        if xy[0] in ("R", "C"):
            i += 1

        # 完全削除（staged / unstaged いずれかで D）は検査対象外
        if "D" in xy:
            continue

        if path in seen:
            continue
        seen.add(path)
        files.append(path)

    return sorted(files)


def get_diff_targets(project_root: Path):
    """diff モード: 未 commit 変更（staged + unstaged）+ 未追跡ファイルを列挙する。"""
    code, stdout, stderr = run_git(
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"], project_root
    )
    if code != 0:
        raise RuntimeError(f"git status の実行に失敗しました: {stderr.strip()}")
    return _parse_porcelain_status_z(stdout)


def get_dir_targets(project_root: Path, dirs: list[str]):
    """dirs モード: 指定ディレクトリ配下のファイルを列挙する。

    ``git ls-files`` を使い、追跡済みファイルと未追跡ファイルの両方を対象にしつつ
    ``.gitignore`` を尊重する。``os.walk`` で自前に歩くと ``.git/`` や ``.gitignore`` 対象の
    除外条件を独自に持つことになり、git の解釈との乖離がそのまま検査範囲の誤りになる。
    """
    code, stdout, stderr = run_git(
        ["ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *dirs],
        project_root,
    )
    if code != 0:
        raise RuntimeError(f"git ls-files の実行に失敗しました: {stderr.strip()}")

    candidates = {entry for entry in stdout.split("\0") if entry}
    # 削除済みでワークツリーに存在しないファイルは読めないため除外する
    existing = [f for f in candidates if (project_root / f).is_file()]
    return sorted(existing)
