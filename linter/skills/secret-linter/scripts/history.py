#!/usr/bin/env python3
"""secret-linter の対象解決のうち ``--branch``（push で外へ出る履歴の追加行とコミットメッセージ）。

各 script が同じディレクトリから import する（直接実行はしない）ため、終了コードを持たない。失敗は
``targets.TargetError`` で表し、呼び出す script が ``secret_common.error`` で返す。理由に本文を含めない。

## 範囲

``HEAD --not --remotes``（リモートに無い commit。リモートが無ければ全履歴）。commit が 0 件
（リモートに無い commit が無い・まだ commit が無い）ときは、検査単位が 0 件で、エラーにしない。

## 検査単位

本文は作業領域のファイルへ書く（元のファイルが無いため）。``unit_id`` は ``targets.number_units`` が付ける。

| kind               | 単位                       | label                 | commit                        | line_numbers                               |
| ------------------ | -------------------------- | --------------------- | ----------------------------- | ------------------------------------------ |
| ``added_lines``    | (commit, パス) ごとに 1 件 | パス                  | その commit の識別子（git の ``%H``） | 本文の各行に対応する、元のファイルの行番号 |
| ``commit_message`` | commit ごとに 1 件         | ``コミットメッセージ`` | その commit の識別子                  | 持たない                                   |

commit の識別子は、検査単位の ``commit`` の項目として独立に持つ（``label`` に詰めて解釈し直さない）。
識別子の長さ・形式（SHA-1 の 40 桁、SHA-256 の 64 桁など）には依存しない。``patterns.py`` はこの項目を
そのまま読む。

追加行はマージ commit を除く commit の ``git log -p -U0`` から、コミットメッセージはマージ commit を含む
全 commit から得る（git の呼び出しを 2 回に分ける）。``added_lines`` の本文は追加行を改行で連ねたもの
（末尾も改行）である。

## 読めないものの記録

検査単位にせず、``label``・``commit``・``reason`` で記録する。ファイルが削除された commit は ``deleted``
（追加行が無い）、バイナリは ``binary``（``Binary files … differ``）。本文の大きさでは除外しない。履歴の出力を UTF-8 の文字列として解釈できないときは、読めたことにせず失敗にする
（検査を実行できなかったことになる）。

## 区切りとパス

出力の区切りは、実行ごとに乱数で生成した文字列で、git の ``--format`` に埋め込む。メッセージや diff の
内容と衝突しない。diff の行は ``+`` ``-`` 空白 ``\\`` のいずれかで始まるため、``diff --git`` や ``@@`` の
見出しとも混ざらない。git が引用符で囲んだパス（非 ASCII・空白・特殊文字）は、元のパスへ戻す。

本モジュールは DES-087 のモジュール表 T-1（範囲を解決する）の ``--branch`` の実装である。
"""

import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_targets  # noqa: E402
import targets  # noqa: E402
import workspace  # noqa: E402

KIND_ADDED_LINES = "added_lines"
KIND_COMMIT_MESSAGE = "commit_message"

#: ``commit_message`` の検査単位の ``label``（元のファイルが無いため、パスの代わりに置く固定の語）。
LABEL_COMMIT_MESSAGE = "コミットメッセージ"

#: git が出力した commit の識別子の検証。16 進数であることだけを確かめ、長さには依存しない
#: （SHA-1 は 40 桁、SHA-256 は 64 桁。長さを決め打ちすると、別の形式のリポジトリで失敗する）。
_COMMIT_ID = re.compile(r"[0-9a-f]+")
_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34}

#: ``git log`` の共通の引数。設定（色・署名・外部 diff・接頭辞）に結果を左右されないよう明示する。
_LOG_BASE = ["log", "--no-color", "--no-show-signature"]
_RANGE = ["HEAD", "--not", "--remotes", "--"]


def _git(project_root, args):
    """git を ``git_targets.run_git`` で実行し、標準出力を返す。失敗は ``TargetError``。"""
    try:
        code, stdout, stderr = git_targets.run_git(args, project_root)
    except UnicodeDecodeError as exc:
        raise targets.TargetError("履歴を文字列として読めません（UTF-8 でない内容を含みます）") from exc
    if code != 0:
        raise targets.TargetError(f"git {args[0]} の実行に失敗しました: {stderr.strip()}")
    return stdout


def _has_commits(project_root):
    """``HEAD`` が commit を指すか。git のリポジトリでなければ ``TargetError``。"""
    _git(project_root, ["rev-parse", "--git-dir"])
    code, _, _ = git_targets.run_git(
        ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], project_root
    )
    return code == 0


def _read_quoted(text, start):
    """``text[start]`` の ``"`` から始まる C 形式の引用符付き文字列を戻す。``(元の文字列, 終端の次の位置)``。"""
    raw = bytearray()
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == '"':
            return raw.decode("utf-8", errors="replace"), i + 1
        if ch != "\\":
            raw.extend(ch.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= len(text):
            break
        esc = text[i]
        if esc in "01234567":
            digits = esc
            while len(digits) < 3 and i + 1 < len(text) and text[i + 1] in "01234567":
                i += 1
                digits += text[i]
            raw.append(int(digits, 8) & 0xFF)
        elif esc in _ESCAPES:
            raw.append(_ESCAPES[esc])
        else:
            raw.extend(("\\" + esc).encode("utf-8"))
        i += 1
    raise targets.TargetError("git が出力したパスの引用符を解釈できません")


def _path_from_diff_header(rest):
    """``diff --git`` の見出しの残り（``a/P b/P``）からパス ``P`` を得る。

    リネームの検出を切っているため、``a/`` 側と ``b/`` 側のパスは常に同じである。
    """
    if rest.startswith('"'):
        first, _ = _read_quoted(rest, 0)
        return first[2:]
    body = len(rest) - 5  # "a/" + P + " " + "b/" + P
    if body < 0 or body % 2:
        raise targets.TargetError("git の diff の見出しを解釈できません")
    length = body // 2
    return rest[2 : 2 + length]


def _commit_id(value):
    value = value.strip()
    if not _COMMIT_ID.fullmatch(value):
        raise targets.TargetError("git が出力した commit の識別子を解釈できません")
    return value


def _parse_added_lines(stdout, delimiter):
    """``git log -p -U0`` の出力を、commit ごとの追加行とスキップへ分ける。

    戻り値は ``(added, skipped)``。どちらも ``commit -> 配列``。``added`` の要素は
    ``(パス, 行の配列, 行番号の配列)``、``skipped`` の要素は ``(パス, 理由)``。
    """
    added = {}
    skipped = {}
    commit = None
    current = None  # {"path", "binary", "deleted", "lines", "numbers", "in_hunk", "next"}

    def flush():
        if current is None:
            return
        if current["deleted"]:
            skipped[commit].append((current["path"], targets.SKIP_DELETED))
        elif current["binary"]:
            skipped[commit].append((current["path"], targets.SKIP_BINARY))
        elif current["lines"]:
            added[commit].append((current["path"], current["lines"], current["numbers"]))

    for line in stdout.split("\n"):
        if line.startswith(delimiter):
            flush()
            current = None
            commit = _commit_id(line[len(delimiter) :])
            added[commit] = []
            skipped[commit] = []
            continue
        if commit is None:
            continue
        if line.startswith("diff --git "):
            flush()
            current = {
                "path": _path_from_diff_header(line[len("diff --git ") :]),
                "binary": False,
                "deleted": False,
                "lines": [],
                "numbers": [],
                "in_hunk": False,
                "next": 0,
            }
            continue
        if current is None:
            continue
        if line.startswith("@@ "):
            match = _HUNK.match(line)
            if match is None:
                raise targets.TargetError("git の diff の hunk の見出しを解釈できません")
            current["in_hunk"] = True
            current["next"] = int(match.group(1))
        elif current["in_hunk"]:
            if line.startswith("+"):
                current["lines"].append(line[1:])
                current["numbers"].append(current["next"])
                current["next"] += 1
        elif line.startswith("deleted file mode "):
            current["deleted"] = True
        elif line.startswith("Binary files "):
            current["binary"] = True
    flush()
    return added, skipped


def _parse_messages(stdout, delimiter):
    """全 commit のコミットメッセージを、``(commit, 本文)`` の並び（git の出力順）で返す。"""
    messages = []
    for piece in stdout.split(delimiter)[1:]:
        head, _, body = piece.partition("\n")
        # ``%B`` の末尾の改行の後ろに、git の行の終端の改行が 1 つ付く。メッセージ自体の末尾は 1 つにそろえる
        body = body.rstrip("\n")
        messages.append((_commit_id(head), body + "\n" if body else ""))
    return messages


def resolve_branch(project_root, inspection_id):
    """``--branch``: リモートに無い commit の追加行とコミットメッセージを検査単位にする。

    戻り値は ``(unit_id を持たない検査単位の配列, スキップの配列)``。本文は作業領域（``inspection_id``）へ
    書く。検査単位は commit の出力順で、各 commit の ``commit_message``、続けて ``added_lines`` を並べる。
    """
    if not _has_commits(project_root):
        return [], []
    delimiter = secrets.token_hex(16)
    diff_out = _git(
        project_root,
        [
            *_LOG_BASE,
            "--no-merges",
            # 設定の log.showRoot=false でも、最初の commit（root commit）の追加行を落とさない
            "--root",
            "-p",
            "-U0",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            f"--format={delimiter}%H",
            *_RANGE,
        ],
    )
    message_out = _git(project_root, [*_LOG_BASE, f"--format={delimiter}%H%n%B", *_RANGE])
    added, skipped_by_commit = _parse_added_lines(diff_out, delimiter)
    messages = _parse_messages(message_out, delimiter)
    if not set(added) <= {commit for commit, _ in messages}:
        raise targets.TargetError("履歴が検査の途中で変わりました。やり直してください")

    pending = []
    skipped = []
    counter = 0

    def place(commit, label, kind, body, line_numbers=None):
        nonlocal counter
        counter += 1
        path = workspace.write_text(inspection_id, f"texts/{counter:04d}.txt", body)
        unit = {"kind": kind, "label": label, "commit": commit, "text_path": str(path)}
        if line_numbers is not None:
            unit["line_numbers"] = line_numbers
        pending.append(unit)

    for commit, message in messages:
        if message.strip():
            place(commit, LABEL_COMMIT_MESSAGE, KIND_COMMIT_MESSAGE, message)
        for path, lines, numbers in added.get(commit, []):
            place(commit, path, KIND_ADDED_LINES, "\n".join(lines) + "\n", numbers)
        for path, reason in skipped_by_commit.get(commit, []):
            skipped.append({"label": path, "commit": commit, "reason": reason})
    return pending, skipped
