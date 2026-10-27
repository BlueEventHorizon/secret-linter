#!/usr/bin/env python3
"""secret-linter のパターン検出（P-1）。検査単位の本文から、形式で見つかる機密候補を機械的に検出する。

各 script が同じディレクトリから import する（直接実行はしない）ため、終了コードを持たない。失敗は
``PatternError`` で表し、呼び出す script（``scan.py``）が ``secret_common.error`` で返す。理由に本文・
検出した値を含めない。AI は使わない。

``detect(units)`` は、``units.json`` の検査単位の配列を受け取り、``pattern.json`` の中身（下記）を返す。
書くのは呼び出し側（``workspace.write``）である。

## 検査単位の読み方

``text_path`` の本文を UTF-8 で読み、解釈できない文字は置換して続ける。行は ``\\n`` だけで分ける
（``str.splitlines`` は使わない。改ページなどで割れて、``line_numbers`` と合わなくなるため）。
``file`` と ``commit_message`` の ``line`` は、本文の 1 始まりの行番号である。``added_lines`` の ``line`` は、
検査単位の ``line_numbers`` の対応する元のファイルの行番号である（件数が本文の行数と合わなければ失敗）。
1 行が ``LINE_WINDOW_CHARS`` を超えるときは、``WINDOW_OVERLAP_CHARS`` だけ重ねた窓に分けて、行の終わりまで
走査する（行を切り捨てない）。窓の境目で欠けた断片は、後ろの窓が拾った全体の値に置き換える。抑制マーカーの有無は、
窓に分ける前の行全体で判定する。

## 検出の順序

行ごとに、次の順で検出する。同じ行・同じ値は、最初に一致した規則だけを採り、採用した値の部分文字列も除く。

1. 抑制マーカー（行末の ``secrets-scan: ignore``）付きの行は、候補にせず、件数と位置を数える。
   マーカー自体は走査から除く。
2. 既知の形式: ``known_token``・``private_key``・``connection_string``（値は埋め込まれたパスワード）・``jwt``
3. 秘密らしいキーへの代入: ``secret_assignment``（値だけ。文字列リテラルの改行を表す ``\\n``
   （バックスラッシュと n の 2 文字）は値に入れず、値は ``\\n`` の手前で終わる）
4. ``password_prose``（散文の中に書かれた認証用の文字列。値は 6 文字以上で、数字か記号を 1 つ以上含む ASCII 印字文字。
   バッククォートで囲んだコード片は、対象にしない）
5. 高エントロピー文字列: ``high_entropy``（40 文字以上、16 進数だけのものを除く）
6. 形式の手がかり: ``company_hint``（法人格の表記を伴う会社名）・``obfuscated_host``（言い換えたドメイン・IP）・
   ``package_name``（アプリのパッケージ名。``_PACKAGE_ROOTS`` のいずれかで始まる、3 つ以上のラベルの
   逆ドメイン形式の識別子。クラス名が続く形も全体で 1 つの値にする）。7 の ``domain`` より先に採るため、
   ``.dev`` で終わるパッケージ名が ``domain`` として重ねて報告されない
7. §2.2 の種別: ``email``・``private_ip``（RFC 1918）・``internal_host``・``domain``

文書用の予約値（localhost・example.com 等）と許可リストは、検出では除かない（機密判定が通過にする）。
予約ドメインを逆にしたパッケージ名（``com.example.app`` 等）も同じで、検出して採ったうえで機密判定が通過にする。
``email`` の局所部が ``git`` のもの（SSH の URL の ``git@host``）は、メールアドレスではないため除く。
``domain`` は、ファイル名やコードの識別子との衝突を避けるため、``_DOMAIN_TLDS`` の接尾辞に限る。

## 誤検出の除外

hex 文字列は単独で検出しない。規則 2〜5 の値が、プレースホルダ・コード式・定数名に見えるとき、および
パスに見えるキーへの代入は、候補にせず、理由別の件数に数える（``placeholder`` / ``code_expression`` /
``constant_name`` / ``path_like``）。規則 6・7 の値には、プレースホルダの除外を適用しない
（予約値を検出から除かないため）。

## pattern.json の中身

``candidates``: 機密候補の配列。``suppressed``: ``count`` と ``locations``（抑制マーカー付きで候補にならなかった
ものの ``unit_id``・``label``・``line``・``kind``。値は持たない）。``filtered``: ``count`` と ``reasons``
（理由別の件数）。候補の値そのもの（機密の疑いがある文字列）は ``candidates`` にだけ入る。作業領域は 0700 で、
検査の終わりに消える。

## 機密候補

``candidate_id``・``source``（``pattern``）・``unit_id``・``line``・``kind``・``value``、履歴のときは ``commit``。
``candidate_id`` は ``P`` + 4 桁以上の連番（``P0001`` 形式）で、検査単位の並び・行・規則の順に付ける。
``commit`` は、履歴の検査単位（``added_lines``・``commit_message``）の ``commit`` の項目をそのまま使う
（``label`` を解釈し直さない。識別子の長さ・形式には依存しない）。履歴の検査単位が ``commit`` を持たなければ
失敗にする。``file`` の検査単位の候補は ``commit`` のキーを持たない。

同じ検査単位には、常に同じ候補を出す（決定論）。集合の反復順や乱数に依存しない。

本モジュールは DES-087 のモジュール表 P-1（検出する）の実装である。
"""

import math
import re
from pathlib import Path

#: 1 行を走査する窓の幅。これを超える行は、重なりのある窓に分けて、行の終わりまで走査する
LINE_WINDOW_CHARS = 4_000

#: 隣り合う窓の重なり。窓の境目をまたぐ値を、欠けずに拾うため、規則が一致しうる値の最大長（256 文字）より大きくする
WINDOW_OVERLAP_CHARS = 512

SUPPRESS_MARKER = "secrets-scan: ignore"

#: 高エントロピーの閾値（TBD-D1。既存の実装から引き継いだ暫定値で、他のプロジェクトでの妥当性は確かめていない）
ENTROPY_THRESHOLD = 4.5

#: 機密候補の kind の語彙（パターン側。閉じた集合）
KINDS = (
    "known_token",
    "private_key",
    "connection_string",
    "jwt",
    "secret_assignment",
    "password_prose",
    "high_entropy",
    "company_hint",
    "obfuscated_host",
    "package_name",
    "domain",
    "email",
    "private_ip",
    "internal_host",
)

#: 除外の理由。件数のキーをここ 1 箇所で定義する
FILTER_REASONS = ("placeholder", "code_expression", "path_like", "constant_name")

#: プレースホルダの除外を適用する kind（規則 2〜5）
_PLACEHOLDER_KINDS = frozenset(KINDS[:7])


class PatternError(Exception):
    """パターン検出を完了できなかったことを表す。メッセージに本文・検出した値を含めない。"""


# --- 誤検出の除外 -------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(
    r"""(?ix)
    ^(
        [<{(\[].*[>})\]]                 # <your-key> / ${VAR} / {{TOKEN}} / [REDACTED]
      | \$.*                             # 環境変数参照
      | .*\$\{.*\}.*                     # 埋め込み変数参照
      | .*(example|sample|dummy|placeholder|redacted|replace[_-]?me|your[_-]|todo|changeme|test)
      | (x{4,}|\*{4,}|\.{3,}|-{4,}|0{8,})
      # 記法の説明で使われる一般語そのもの。語そのものが値である場合に限る
      | (password|passwd|secret|token|apikey|api[_-]?key|credential|username|hostname)
      | .*(os\.environ|getenv|process\.env|secrets\.)
    )$
    """
)

#: 値にこれらが含まれる場合はソースコードの式であり、秘密の literal ではない
_CODE_EXPRESSION_RE = re.compile(r"[()\[\]{}]")

#: 秘密として成立しうる文字集合（ASCII 印字可能・空白なし）。日本語・全角記号を含む値は散文の一部
_SECRET_CHARSET_RE = re.compile(r"^[\x21-\x7e]+$")

#: キーがファイル名・パスに見える場合の除外（チェックサム表のキー等）
_PATH_LIKE_KEY_RE = re.compile(
    r"\.(md|py|ya?ml|json|toml|txt|ts|tsx|js|jsx|sh|lock|cfg|ini)$", re.IGNORECASE
)

#: 値が定数名（アンダースコア区切りの複数語の大文字列）の場合の除外。区切りを要求して実在のトークンと衝突させない
_CONSTANT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

#: 同一文字の繰り返し・単調な埋め草
_MONOTONE_RE = re.compile(r"^(.)\1*$")

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def shannon_entropy(value):
    """文字列のシャノンエントロピー（bit/文字）を返す。"""
    if not value:
        return 0.0
    counts = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def _is_placeholder(value):
    return bool(_MONOTONE_RE.match(value) or _PLACEHOLDER_RE.match(value))


def _value_reason(value):
    """値がコード式・定数名・プレースホルダに見えるときの理由。見えなければ None。"""
    if _CODE_EXPRESSION_RE.search(value) or not _SECRET_CHARSET_RE.match(value):
        return "code_expression"
    if _CONSTANT_NAME_RE.match(value):
        return "constant_name"
    return None


# --- 規則 2: 既知の形式 --------------------------------------------------------

_PREFIXED_TOKEN_RULES = (
    ("known_token", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("known_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("known_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("known_token", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("known_token", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("known_token", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}\b")),
    ("known_token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
    ("known_token", re.compile(r"\bpypi-[A-Za-z0-9_-]{16,}\b")),
    ("private_key", re.compile(r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----")),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    ),
)

#: 資格情報を埋め込んだ URI（scheme://user:password@host）。値は埋め込まれたパスワード側
_CONNECTION_STRING_RE = re.compile(
    r"\b[a-z][a-z0-9+.\-]*://(?P<user>[^\s:/@\"']{1,64}):(?P<secret>[^\s:/@\"']{1,256})@"
)

# --- 規則 3: 秘密らしいキーへの代入 ---------------------------------------------

#: ``auth`` 単独を含めないのは ``Authority`` / ``author`` に一致するため。複合形だけを拾う
_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(?P<key>[A-Za-z0-9_.\-]*
        (?:api[_-]?key|secret|token|password|passwd|pwd|credential
          |private[_-]?key|auth[_-]?(?:token|key|secret|header)|authorization)
       [A-Za-z0-9_.\-]*)
    \s*[:=]\s*
    (?P<quote>["'])?(?P<value>(?:(?!\\n)[^\s"',;]){8,256})(?(quote)(?:["']|(?=\\n)))
    """
)

# --- 規則 4: 散文中のパスワード -------------------------------------------------

_PASSWORD_PROSE_RES = (
    re.compile(
        r"(?:パスワード|パスフレーズ|暗証番号)\s*(?:は|が|:|：|=|＝)\s*"
        r"(?P<open>[「『\"'`(（]?)(?P<value>[\x21-\x7e]{1,128})"
    ),
    re.compile(
        r"(?i)\b(?:password|passwd|passphrase|pwd)\s+(?:is|was)\s*[:=]?\s*"
        r"(?P<open>[\"'`(]?)(?P<value>[\x21-\x7e]{1,128})"
    ),
)
_PASSWORD_PROSE_MIN_LENGTH = 6
_TRAILING_PUNCTUATION = ".,;:!?)]}\"'`"

# --- 規則 5: 高エントロピー文字列 -----------------------------------------------

#: 候補文字に ``/`` を含めないのは、ファイルパスが軒並み一致して誤検出が支配的になるため
_HIGH_ENTROPY_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+=_-]{40,}")

# --- 規則 6: 形式の手がかり -----------------------------------------------------

_NAME = r"[\u30A0-\u30FF\u3400-\u9FFFA-Za-z0-9&＆]{1,20}"
_CORPORATE_JA = r"(?:株式会社|有限会社|合同会社|合資会社|合名会社)"
_COMPANY_HINT_RES = (
    re.compile(_CORPORATE_JA + "[ \u3000]?" + _NAME),
    re.compile(_NAME + r"(?:株式会社|有限会社|合同会社)"),
    re.compile(
        r"\b(?:[A-Z][A-Za-z0-9&'\-]*,?\s+){1,3}"
        r"(?:Co\.,?\s*Ltd\.?|Inc\.?|Ltd\.?|LLC|Corp\.?|Corporation|Limited|GmbH)(?![A-Za-z0-9])"
    ),
)

#: ドメインの接尾辞（小文字）。``py`` ``md`` ``sh`` ``js`` 等のファイルの拡張子と衝突するものは含めない
_DOMAIN_TLDS = (
    "com",
    "net",
    "org",
    "io",
    "dev",
    "app",
    "jp",
    "co",
    "us",
    "uk",
    "de",
    "fr",
    "cn",
    "kr",
    "tw",
    "hk",
    "sg",
    "au",
    "ca",
    "ai",
    "cloud",
    "info",
    "biz",
    "edu",
    "gov",
    "me",
    "tv",
    "xyz",
    "site",
    "online",
    "tech",
    "ly",
)

#: パッケージ名の根になる語（逆ドメインの先頭のラベル。小文字。大文字小文字を区別せずに判定する）。この 1 か所だけに持つ
_PACKAGE_ROOTS = (
    "com",
    "org",
    "net",
    "io",
    "jp",
    "co",
    "edu",
    "gov",
)
#: 各ラベルは英字で始まり、英数字と ``_`` だけ（``-`` は含まない）。前が英数字・``_``・``.``・``@``、または
#: 英数字・``_`` に続く ``-`` のとき、後ろが英数字・``_``・``-`` や ``.`` + 英数字のときは、より長い名前
#: （ファイル名・メールアドレス・ハイフン付きの語）の一部として取らない。``${X:-com.example.app}`` の ``-`` は区切りである
_PACKAGE_LABEL = r"[A-Za-z][A-Za-z0-9_]*"
_PACKAGE_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9_.@])(?<![A-Za-z0-9_]-)(?i:"
    + "|".join(_PACKAGE_ROOTS)
    + r")(?:\."
    + _PACKAGE_LABEL
    + r"){2,}"
    r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_])"
)

_HOST_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_OBFUSCATED_BRACKET = r"(?:\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}|\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\})"
_OBFUSCATED_PLAIN = r"[ \t]+dot[ \t]+"
_OBFUSCATED_HOST_RE = re.compile(
    r"(?<![A-Za-z0-9-])"
    + _HOST_LABEL
    + r"(?:(?:"
    + _OBFUSCATED_BRACKET
    + "|"
    + _OBFUSCATED_PLAIN
    + r"|\.)"
    + _HOST_LABEL
    + r")+"
)
_OBFUSCATED_BRACKET_RE = re.compile(_OBFUSCATED_BRACKET)
_OBFUSCATED_SEPARATOR_RE = re.compile(_OBFUSCATED_BRACKET + "|" + _OBFUSCATED_PLAIN)

# --- 規則 7: §2.2 の種別 --------------------------------------------------------

_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*"
    r"\.[A-Za-z]{2,}(?![A-Za-z0-9-])"
)
_IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
_INTERNAL_HOST_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?:"
    + _HOST_LABEL
    + r"\.)+(?:internal|local|corp|lan|intranet|localdomain)"
    r"(?![A-Za-z0-9-]|\.[A-Za-z0-9_])"
)
_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    + _HOST_LABEL
    + r"\.)+(?:"
    + "|".join(sorted(_DOMAIN_TLDS, key=lambda t: (-len(t), t)))
    + r")(?![A-Za-z0-9-]|\.[A-Za-z0-9_])"
)


def _octets(value):
    parts = [int(p) for p in value.split(".")]
    return parts if len(parts) == 4 and all(p <= 255 for p in parts) else None


def _is_private_ipv4(value):
    octets = _octets(value)
    if octets is None:
        return False
    first, second = octets[0], octets[1]
    return (
        first == 10
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
    )


def _is_obfuscated_host(matched):
    """言い換えのある ``matched`` が、ドメインまたは IP の言い換えと見なせるか。

    ``[.]`` 等の括弧の形は意図的な言い換えとして、末尾が英字 2 文字以上ならよい。`` dot `` の形は散文
    （``the dot product``）と衝突するため、末尾が ``_DOMAIN_TLDS`` に限る。どちらも、数字だけの 4 つの
    ラベル（IPv4 の言い換え）は受理する。
    """
    if not _OBFUSCATED_SEPARATOR_RE.search(matched):
        return False
    labels = [
        p for p in re.split(_OBFUSCATED_SEPARATOR_RE.pattern + r"|\.", matched) if p
    ]
    if len(labels) == 4 and all(p.isdigit() for p in labels):
        return _octets(".".join(labels)) is not None
    last = labels[-1]
    if _OBFUSCATED_BRACKET_RE.search(matched):
        return last.isalpha() and len(last) >= 2
    return last in _DOMAIN_TLDS


def _normalized_password_value(value):
    """散文中のパスワードの値を整える。パスワードに見えなければ None。"""
    value = value.rstrip(_TRAILING_PUNCTUATION)
    if len(value) < _PASSWORD_PROSE_MIN_LENGTH or all(c.isalpha() for c in value):
        return None
    return value


def _iter_matches(line):
    """1 行から ``(kind, value, 除外の理由)`` を、規則の順に列挙する。

    除外の理由が None 以外なら、形式は一致したが秘密ではないと機械的に判定したもの（件数だけ数える）。
    """
    # 規則 2: 既知の形式
    for kind, pattern in _PREFIXED_TOKEN_RULES:
        for match in pattern.finditer(line):
            yield kind, match.group(0), None
    connection_spans = []
    for match in _CONNECTION_STRING_RE.finditer(line):
        connection_spans.append(match.span())
        yield "connection_string", match.group("secret"), None

    # 規則 3: 秘密らしいキーへの代入
    for match in _ASSIGNMENT_RE.finditer(line):
        key = match.group("key")
        value = match.group("value")
        preceding = line[match.start("key") - 1] if match.start("key") > 0 else ""
        if _PATH_LIKE_KEY_RE.search(key) or preceding == "/":
            yield "secret_assignment", value, "path_like"
        else:
            yield "secret_assignment", value, _value_reason(value)

    # 規則 4: 散文中のパスワード
    for pattern in _PASSWORD_PROSE_RES:
        for match in pattern.finditer(line):
            if match.group("open") == "`" or match.group("value").startswith("`"):
                continue  # バッククォートで囲んだコード片は、文章に書かれたパスワードではない
            value = _normalized_password_value(match.group("value"))
            if value is not None:
                yield "password_prose", value, _value_reason(value)

    # 規則 5: 高エントロピー文字列
    for match in _HIGH_ENTROPY_CANDIDATE_RE.finditer(line):
        candidate = match.group(0)
        if _HEX_RE.match(candidate):
            continue
        if shannon_entropy(candidate) >= ENTROPY_THRESHOLD:
            yield "high_entropy", candidate, None

    # 規則 6: 形式の手がかり
    for pattern in _COMPANY_HINT_RES:
        for match in pattern.finditer(line):
            yield "company_hint", match.group(0), None
    for match in _OBFUSCATED_HOST_RE.finditer(line):
        if _is_obfuscated_host(match.group(0)):
            yield "obfuscated_host", match.group(0), None
    for match in _PACKAGE_NAME_RE.finditer(line):
        yield "package_name", match.group(0), None

    # 規則 7: §2.2 の種別
    for match in _EMAIL_RE.finditer(line):
        local = match.group(0).split("@", 1)[0]
        inside_connection = any(s <= match.start() < e for s, e in connection_spans)
        if local != "git" and not inside_connection:
            yield "email", match.group(0), None
    for match in _IPV4_RE.finditer(line):
        if _is_private_ipv4(match.group(0)):
            yield "private_ip", match.group(0), None
    for match in _INTERNAL_HOST_RE.finditer(line):
        yield "internal_host", match.group(0), None
    for match in _DOMAIN_RE.finditer(line):
        yield "domain", match.group(0), None


# --- 検査単位の走査 -------------------------------------------------------------


def read_text(path):
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise PatternError(f"検査単位の本文を読めません: {exc}") from exc
    return raw.decode("utf-8", errors="replace")


def _split_lines(text):
    """``\\n`` だけで行に分ける（末尾の改行による空の最終行は持たない）。行末の ``\\r`` は除く。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _windows(line):
    """行を、``LINE_WINDOW_CHARS`` 幅・``WINDOW_OVERLAP_CHARS`` 重ねの窓に分ける。短い行は 1 つだけ。"""
    step = LINE_WINDOW_CHARS - WINDOW_OVERLAP_CHARS
    start = 0
    while True:
        yield line[start : start + LINE_WINDOW_CHARS]
        if start + LINE_WINDOW_CHARS >= len(line):
            return
        start += step


def _iter_line_matches(line):
    """1 行の ``(kind, value, 除外の理由)`` を列挙する。長い行は窓ごとに走査し、重なりでの重複を除く。"""
    if len(line) <= LINE_WINDOW_CHARS:
        yield from _iter_matches(line)
        return
    found = []  # (窓の番号, kind, value, reason)
    for index, window in enumerate(_windows(line)):
        for kind, value, reason in _iter_matches(window):
            if any(k == kind and v == value for _, k, v, _ in found):
                continue
            found.append((index, kind, value, reason))
    for index, kind, value, reason in found:
        # 窓の境目で切れた断片は、後ろの窓が拾った、それを含む値に置き換わる
        if any(
            later > index and value != other and value in other
            for later, _, other, _ in found
        ):
            continue
        yield kind, value, reason


def unit_lines(unit):
    """検査単位の ``(元の行番号, 行)`` の並びと、``commit``（無ければ None）を返す。"""
    kind = unit.get("kind")
    lines = _split_lines(read_text(unit["text_path"]))
    if kind == "file":
        return list(enumerate(lines, start=1)), None
    if kind not in ("added_lines", "commit_message"):
        raise PatternError("検査単位の kind が不正です")
    commit = unit.get("commit")
    if not isinstance(commit, str) or not commit:
        raise PatternError("履歴の検査単位に commit がありません")
    if kind == "commit_message":
        return list(enumerate(lines, start=1)), commit
    numbers = unit.get("line_numbers")
    if not isinstance(numbers, list) or len(numbers) != len(lines):
        raise PatternError("検査単位の line_numbers が本文の行数と合いません")
    return list(zip(numbers, lines)), commit


def detect(units):
    """検査単位の配列から ``pattern.json`` の中身を作って返す。"""
    candidates = []
    suppressed = []
    filtered = {reason: 0 for reason in FILTER_REASONS}
    for unit in units:
        numbered_lines, commit = unit_lines(unit)
        for line_number, line in numbered_lines:
            # マーカーは、窓に分ける前の行全体で判定する。先頭の窓より後ろのマーカーでも抑制が効く
            is_suppressed = SUPPRESS_MARKER in line
            if is_suppressed:
                # マーカー自体は「秘密らしいキーへの代入」の形をしているため、走査から除く
                line = line.replace(SUPPRESS_MARKER, "")
            seen = []
            for kind, value, reason in _iter_line_matches(line):
                if any(value in earlier for earlier in seen):
                    continue
                seen.append(value)
                if (
                    reason is None
                    and kind in _PLACEHOLDER_KINDS
                    and _is_placeholder(value)
                ):
                    reason = "placeholder"
                if reason is not None:
                    filtered[reason] += 1
                    continue
                if is_suppressed:
                    suppressed.append(
                        {
                            "unit_id": unit["unit_id"],
                            "label": unit["label"],
                            "line": line_number,
                            "kind": kind,
                        }
                    )
                    continue
                candidate = {
                    "candidate_id": f"P{len(candidates) + 1:04d}",
                    "source": "pattern",
                    "unit_id": unit["unit_id"],
                    "line": line_number,
                    "kind": kind,
                    "value": value,
                }
                if commit is not None:
                    candidate["commit"] = commit
                candidates.append(candidate)
    return {
        "candidates": candidates,
        "suppressed": {"count": len(suppressed), "locations": suppressed},
        "filtered": {"count": sum(filtered.values()), "reasons": filtered},
    }
