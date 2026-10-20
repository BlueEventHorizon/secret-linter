#!/usr/bin/env python3
"""secret-linter の機密判定の規則（J-1）。1 件の機密候補を fail・confirm・pass に分ける規則と、
全体の区分を決める規則を、1 か所に持つ。

``judge.py`` と、回答後に再判定する ``answer.py`` が、同じ規則を呼ぶ。各 script が同じ
ディレクトリから import する（直接実行はしない）ため、終了コードを持たない。失敗は
``JudgmentError`` で表す。メッセージに候補の値・本文を含めない。

## 判定の順序

候補ごとに、次の順で最初に当たったものを採る。

1. 機密の名前リストに一致する → ``fail``
2. ``package_name`` で、予約ドメインを逆にした形 → ``pass``（``reserved_value``）
   ``package_name`` で、同梱の許可リストの接頭辞に一致 → ``pass``（``allowlist_bundled_package``）、
   プロジェクトの許可リストの ``接頭辞.*`` の項目に一致 → ``pass``（``allowlist_project``）
3. ``kind`` が ``fail`` の種別 → ``fail``
4. 文書用の予約値 → ``pass``
5. メールアドレスで、プロジェクトの許可リストに完全一致の項目がある → ``pass``（``allowlist_project``）
6. メールアドレス（上に当たらないもの） → ``confirm``
7. 許可リストに一致する → ``pass``
8. ``kind`` が ``confirm`` の種別 → ``confirm``

2 は、``fail`` の種別が文書用の予約値と許可リストを通さない例外である。検出側は予約ドメインを逆にした形を除かず
（除くと、同じ値が ``domain`` として拾われる）、本モジュールが通過にする。``package_name`` が通るのは、
この 2 の規則だけである（上の 5・7 の許可リストの一致は、``fail`` の種別には効かない）。

メールアドレスは、同梱の許可リストの公開ドメインや、プロジェクトの許可リストのドメイン・``*.`` の項目では
通さない（個人のアドレスを一括で許さない）。逆に、メールアドレスの項目は、ドメインの候補を通さない
（完全一致でしか一致しないため）。

全体の区分は、1 件でも ``fail`` があれば ``fail``、なければ ``confirm`` が残る間は ``confirm``、
すべて ``pass``（候補が 0 件を含む）なら ``pass``。

## 種別と区分の対応

- ``fail``: known_token・private_key・connection_string・jwt・secret_assignment・password_prose・package_name
- ``confirm``: high_entropy・company_hint・obfuscated_host・domain・email・private_ip・internal_host、
  AI の 4 種別（person_name・company_name・resource_name・other_ai）

語彙に無い種別は ``JudgmentError``（黙って通さない）。

## 文書用の予約値（固定の一覧。利用者が指定するものではない。本モジュールの 1 か所だけに持つ）

インターネットの規格（RFC 2606・6761・5737・3849）が、説明や試験のために予約した値である。

- ドメイン: example.com・example.net・example.org とそのサブドメイン、末尾が .example・.test・
  .invalid・.localhost のもの、localhost（大文字小文字は区別しない）。メールアドレスは ``@`` の後ろで判定する
- IP アドレス: 192.0.2.0/24・198.51.100.0/24・203.0.113.0/24（IPv4）、2001:db8::/32（IPv6）
- パッケージ名: 予約ドメイン（example.com・example.net・example.org）を逆にした形、すなわち ``com.example``・
  ``net.example``・``org.example`` で始まるもの（ラベル単位。大文字小文字は区別しない）

## リストの一致方法

値は、URL・メールアドレス・ホストのいずれの形でも受け、スキーム・ユーザー情報・ポートを除いた
ホストと、ホストより後ろ（パス・クエリ）に分けて扱う。

- 機密の名前リスト（大文字小文字を区別しない）: 項目の形で一致方法が変わる。
  メールアドレス（``@`` を含む）・IP アドレス・日本語等の非 ASCII の名前・記号を含む名前は部分文字列、
  ドメイン名の形（ドットで区切った 2 ラベル以上の英数字とハイフン）はホストの接尾辞、
  英数字・空白・``_``・``-`` だけの名前は語の境界（前後が英数字でない）で一致させる。
- 許可リスト（大文字小文字を区別する）: プロジェクトの項目は、``*.`` で始まればそのドメインとサブドメイン、
  ``/`` を含み ``?`` を含まなければそのパスと配下（スキームは無視）、それ以外は完全一致。
  メールアドレスの候補は、この一致方法ではなく、プロジェクトの項目との完全一致だけで通す。
  同梱の「パスまで公開のドメイン」はホストの接尾辞で、「ホストだけ公開のドメイン」は、ドメイン自身と
  ``www.`` 付きのホストだけ（パスは無いか ``/`` だけ。サブドメイン・パス・クエリを伴えば一致しない）。
- パッケージの接頭辞（``package_name`` の候補にだけ効く）: 同梱の「公開の開発系パッケージの接頭辞」と、プロジェクトの
  ``接頭辞.*`` の項目は、ラベルの境界での前置き一致（``org.apache`` は ``org.apache`` とその配下に
  一致し、``org.apache`` に文字を続けただけの名前には一致しない）。大文字小文字を区別する。``接頭辞.*`` の項目は、``package_name``
  以外の種別には効かない（既存の項目の形の一致方法には含めない）。

判定は値だけから決まり、集合の反復順・乱数・時刻に依存しない（決定論）。

## 回答後の再判定（``rejudge``）と「公開してよい（今回だけ）」の引き継ぎ

回答後の再判定は、同じ判定の順序（``classify``）で候補を判定し直す。1 回の検査の中で ``answer.py`` が
複数回呼ばれる（要確認が残れば、SKILL が再び質問して呼び直す）ため、「公開してよい（今回だけ）」の回答は、
リストに残さずに、後続の呼び出しでも有効であり続ける必要がある。

- 今回だけの回答を受けた候補は、判定ファイルの ``reason`` を ``answered_public_once`` にして ``pass`` で記録する
- ``rejudge`` は、前の判定（最新の判定ファイル）で ``reason`` が ``answered_public_once`` の候補を引き継ぐ。
  判定の順序で ``confirm`` になる候補だけを、``pass``（``answered_public_once``）にする。``fail`` になる候補
  （後の回答で機密の名前リストに一致した等）は引き継がず、``fail`` のままにする
- この引き継ぎの記録は判定ファイルの ``reason`` だけであり、リストには何も残らない

## 判定ファイルの読み込み

``read_latest``（最大の連番の判定ファイル。現在の判定）と ``read_first``（``0001.json``。回答を反映する前の判定）を
公開する。``report.py`` と、回答を記録する ``answer.py`` が同じ読み方をする。

本モジュールは DES-087 のモジュール表 J-1（判定する）の規則の実装である。
"""

import ipaddress
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workspace  # noqa: E402

FAIL = "fail"
CONFIRM = "confirm"
PASS = "pass"

#: fail の種別
FAIL_KINDS = frozenset(
    (
        "known_token",
        "private_key",
        "connection_string",
        "jwt",
        "secret_assignment",
        "password_prose",
        "package_name",
    )
)

#: confirm の種別（パターン 7 種と AI の 4 種）
CONFIRM_KINDS = frozenset(
    (
        "high_entropy",
        "company_hint",
        "obfuscated_host",
        "domain",
        "email",
        "private_ip",
        "internal_host",
        "person_name",
        "company_name",
        "resource_name",
        "other_ai",
    )
)

#: 文書用の予約値（ドメイン）。この 1 か所だけに持つ
RESERVED_DOMAINS = ("example.com", "example.net", "example.org", "localhost")
RESERVED_TLDS = ("example", "test", "invalid", "localhost")

#: 文書用の予約値（IP アドレスの説明用の範囲）。この 1 か所だけに持つ
RESERVED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "2001:db8::/32",
    )
)

#: 文書用の予約値（予約ドメインを逆にしたパッケージ名の先頭）。この 1 か所だけに持つ
_RESERVED_PACKAGE_RE = re.compile(r"(?:com|net|org)\.example(?:\.|$)", re.IGNORECASE)

#: reason（どの規則で決まったか。値を含めない）
REASON_NAME_LIST = "confidential_name_list"
REASON_FAIL_KIND = "fail_kind"
REASON_EMAIL = "email_confirm"
REASON_RESERVED = "reserved_value"
REASON_ALLOW_FULL = "allowlist_bundled_full"
REASON_ALLOW_HOST_ONLY = "allowlist_bundled_host_only"
REASON_ALLOW_PACKAGE = "allowlist_bundled_package"
REASON_ALLOW_PROJECT = "allowlist_project"
REASON_CONFIRM_KIND = "confirm_kind"
#: 「公開してよい（今回だけ）」と回答された（リストには残さない。``rejudge`` が後続の再判定へ引き継ぐ）
REASON_ANSWERED_ONCE = "answered_public_once"

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_PORT_RE = re.compile(r":\d+$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
_DOMAIN_NAME_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)
_WORD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]*$")


class JudgmentError(Exception):
    """判定を完了できなかったことを表す。メッセージに候補の値・本文を含めない。"""


# --- 値の分解 -----------------------------------------------------------------


def split_value(value):
    """値を ``(ホスト, ホストより後ろ)`` に分ける。

    スキーム・ユーザー情報（メールアドレスの ``@`` の前を含む）・ポートは捨てる。後ろは、パス・
    クエリ・フラグメントを ``/`` ``?`` ``#`` から始まる形で返す（無ければ空）。ホストの形に見えなければ、
    ホストは ``None``。
    """
    scheme = _SCHEME_RE.match(value)
    body = value[scheme.end() :] if scheme else value
    boundary = re.search(r"[/?#]", body)
    authority, rest = (
        (body, "")
        if boundary is None
        else (body[: boundary.start()], body[boundary.start() :])
    )
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    if _parse_ip(authority) is not None:
        host = authority  # 括弧なしの IPv6 の末尾をポートと取り違えない
    elif authority.startswith("["):
        end = authority.find("]")
        host = authority[1:end] if end > 0 else None
    else:
        host = _PORT_RE.sub("", authority)
    if not host:
        return None, rest
    if _parse_ip(host) is not None or _HOSTNAME_RE.match(host):
        return host, rest
    return None, rest


def _parse_ip(text):
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


# --- 文書用の予約値 -----------------------------------------------------------


def is_reserved_value(value):
    """値が文書用の予約値（ドメイン・IP アドレス）なら True。メールアドレスは ``@`` の後ろで判定する。"""
    host, _ = split_value(value)
    if host is None:
        return False
    ip = _parse_ip(host)
    if ip is not None:
        return any(ip in network for network in RESERVED_NETWORKS)
    name = host.lower().rstrip(".")
    if name in RESERVED_DOMAINS or name.endswith(
        tuple("." + d for d in RESERVED_DOMAINS)
    ):
        return True
    return "." in name and name.rsplit(".", 1)[-1] in RESERVED_TLDS


# --- 機密の名前リスト ---------------------------------------------------------


def _name_style(item):
    """機密の名前リストの項目の一致方法（``substring`` / ``suffix`` / ``word``）を返す。"""
    if "@" in item or _parse_ip(item) is not None:
        return "substring"
    if _DOMAIN_NAME_RE.match(item):
        labels = item.split(".")
        # 数字だけのラベルが並ぶものは IP アドレスの形（不完全な表記を含む）として、部分文字列にする
        return "substring" if all(label.isdigit() for label in labels) else "suffix"
    if _WORD_NAME_RE.match(item):
        return "word"
    return "substring"


def matches_secret_name(value, secret_names):
    """値が機密の名前リストのいずれかの項目に一致するか。大文字小文字は区別しない。"""
    lowered = value.lower()
    host, _ = split_value(value)
    host = host.lower().rstrip(".") if host is not None else None
    for item in secret_names:
        needle = item.lower()
        style = _name_style(item)
        if style == "substring":
            if needle in lowered:
                return True
        elif style == "suffix":
            if host is not None and (host == needle or host.endswith("." + needle)):
                return True
        elif re.search(
            r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])", lowered
        ):
            return True
    return False


# --- 許可リスト ---------------------------------------------------------------


def _strip_scheme(value):
    scheme = _SCHEME_RE.match(value)
    return value[scheme.end() :] if scheme else value


def _matches_project_item(value, item):
    if item.startswith("*."):
        domain = item[2:]
        host, _ = split_value(value)
        return host is not None and (host == domain or host.endswith("." + domain))
    if "/" in item and "?" not in item:
        target = _strip_scheme(item).rstrip("/")
        body = _strip_scheme(value)
        return body == target or body.startswith(target + "/")
    return value == item


def _has_label_prefix(value, prefix):
    """値が ``prefix`` と等しいか、``prefix`` + ``.`` で始まるか（ラベルの境界での前置き一致。大文字小文字を区別する）。"""
    return value == prefix or value.startswith(prefix + ".")


def _project_package_prefix(item):
    """プロジェクトの項目が ``接頭辞.*`` の形なら接頭辞を返す。そうでなければ ``None``。

    ``*.`` で始まる項目（ドメイン）・``/`` を含む項目（パス）・接頭辞が空の項目は、この形ではない。
    """
    if item.startswith("*.") or "/" in item or not item.endswith(".*"):
        return None
    return item[:-2] or None


def package_allowlist_reason(value, bundled, project_items):
    """``package_name`` の値が、同梱・プロジェクトの許可リストの接頭辞に一致すれば、その理由を返す。なければ ``None``。

    同梱の ``package_prefixes`` と、プロジェクトの ``接頭辞.*`` の項目を、ラベルの境界での前置き一致で比べる
    （大文字小文字を区別する）。``package_name`` の候補にだけ使い、他の種別の判定（``allowlist_reason``）には効かせない。
    """
    for prefix in bundled.package_prefixes:
        if _has_label_prefix(value, prefix):
            return REASON_ALLOW_PACKAGE
    for item in project_items:
        prefix = _project_package_prefix(item)
        if prefix is not None and _has_label_prefix(value, prefix):
            return REASON_ALLOW_PROJECT
    return None


def allowlist_reason(value, bundled, project_items):
    """値が許可リストに一致すれば、その理由（``REASON_ALLOW_*``）を返す。一致しなければ ``None``。

    ``bundled`` は ``lists.BundledAllowlist``、``project_items`` はプロジェクトの許可リストの項目。
    大文字小文字を区別して比べる（許可は狭く一致させる）。
    """
    host, rest = split_value(value)
    if host is not None and _parse_ip(host) is None:
        for domain in bundled.full_domains:
            if host == domain or host.endswith("." + domain):
                return REASON_ALLOW_FULL
        if rest in ("", "/"):
            for domain in bundled.host_only_domains:
                if host == domain or host == "www." + domain:
                    return REASON_ALLOW_HOST_ONLY
    for item in project_items:
        if _matches_project_item(value, item):
            return REASON_ALLOW_PROJECT
    return None


# --- 判定 ---------------------------------------------------------------------


#: 値全体がメールアドレスの形か（局所部と、ドットを含むドメインを ``@`` でつないだ形）
_EMAIL_VALUE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def is_email_candidate(candidate):
    """候補がメールアドレスとして扱われるか。種別が ``email`` か、値全体がメールアドレスの形のとき真。

    メールアドレスの規則（プロジェクトの許可リストの完全一致の項目だけで通す）は、値の性質で定まる。
    AI の候補（``person_name`` 等）の値がメールアドレスの形でも、同じ規則を適用するため、種別だけで決めない。
    """
    value = candidate.get("value")
    if candidate.get("kind") == "email":
        return True
    return isinstance(value, str) and _EMAIL_VALUE.fullmatch(value) is not None


def classify(candidate, lists):
    """1 件の候補を ``(区分, reason)`` に分ける。``lists`` は ``lists.Lists``。"""
    kind = candidate.get("kind")
    value = candidate.get("value")
    if kind not in FAIL_KINDS and kind not in CONFIRM_KINDS:
        raise JudgmentError("機密候補の種別が語彙にありません")
    if not isinstance(value, str):
        raise JudgmentError("機密候補の値が文字列ではありません")

    if matches_secret_name(value, lists.secret_names):
        return FAIL, REASON_NAME_LIST
    if kind == "package_name" and _RESERVED_PACKAGE_RE.match(value):
        return PASS, REASON_RESERVED
    if kind == "package_name":
        package_reason = package_allowlist_reason(
            value, lists.bundled_allowlist, lists.project_allowlist
        )
        if package_reason is not None:
            return PASS, package_reason
    if kind in FAIL_KINDS:
        return FAIL, REASON_FAIL_KIND
    if is_reserved_value(value):
        return PASS, REASON_RESERVED
    if is_email_candidate(candidate):
        if value in lists.project_allowlist:
            return PASS, REASON_ALLOW_PROJECT
        return CONFIRM, REASON_EMAIL
    reason = allowlist_reason(value, lists.bundled_allowlist, lists.project_allowlist)
    if reason is not None:
        return PASS, reason
    return CONFIRM, REASON_CONFIRM_KIND


def overall_class(classes):
    """候補ごとの区分から全体の区分を決める。"""
    classes = list(classes)
    if FAIL in classes:
        return FAIL
    if CONFIRM in classes:
        return CONFIRM
    return PASS


# --- 判定ファイルの読み込み ---------------------------------------------------


def _read_judgment(inspection_id, number):
    path = (
        workspace.workspace_path(inspection_id)
        / "judgment"
        / workspace.sequence_filename(number)
    )
    data = workspace.read_json(path, "作業領域の判定ファイル")
    candidates = data.get("candidates") if isinstance(data, dict) else None
    valid = (
        isinstance(candidates, list)
        and data.get("class") in (FAIL, CONFIRM, PASS)
        and all(
            isinstance(c, dict)
            and isinstance(c.get("candidate_id"), str)
            and c.get("class") in (FAIL, CONFIRM, PASS)
            for c in candidates
        )
    )
    if not valid:
        raise JudgmentError("作業領域の判定ファイルの形が不正です")
    return data


def read_latest(inspection_id):
    """最新（最大の連番）の判定ファイル ``judgment/NNNN.json`` を読んで返す。現在の判定である。

    判定ファイルが 1 つも無い、形が不正なときは ``JudgmentError``。作業領域が無い、判定ファイルを読めないときは
    ``workspace.WorkspaceError``。メッセージに値を含めない。
    """
    number = workspace.last_number(inspection_id, "judgment")
    if number == 0:
        raise JudgmentError("作業領域に判定ファイルがありません")
    return _read_judgment(inspection_id, number)


def read_first(inspection_id):
    """最初の判定ファイル ``judgment/0001.json``（回答を反映する前の判定）を読んで返す。

    エラーは ``read_latest`` と同じ。
    """
    workspace.last_number(
        inspection_id, "judgment"
    )  # 作業領域が無ければ WorkspaceError
    return _read_judgment(inspection_id, 1)


def judge(candidates, lists):
    """候補の配列から判定結果（``judgment/NNNN.json`` の中身）を作る。"""
    return _judge(candidates, lists, frozenset())


def rejudge(candidates, lists, previous, once_ids=()):
    """回答を反映して判定し直す。判定結果（``judgment/NNNN.json`` の中身）を返す。

    ``previous`` は前の判定（``read_latest`` の戻り値）、``once_ids`` は今回「公開してよい（今回だけ）」と
    回答された候補 ID。前の判定で今回だけの回答を受けていた候補と ``once_ids`` の候補は、判定の順序で
    ``confirm`` になるときだけ ``pass``（``answered_public_once``）にする。
    """
    carried = {
        r["candidate_id"]
        for r in previous["candidates"]
        if r.get("reason") == REASON_ANSWERED_ONCE
    }
    carried.update(once_ids)
    return _judge(candidates, lists, carried)


def _judge(candidates, lists, once_ids):
    results = []
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str):
            raise JudgmentError("機密候補に candidate_id がありません")
        cls, reason = classify(candidate, lists)
        if cls == CONFIRM and candidate_id in once_ids:
            cls, reason = PASS, REASON_ANSWERED_ONCE
        results.append({"candidate_id": candidate_id, "class": cls, "reason": reason})
    return {"candidates": results, "class": overall_class(r["class"] for r in results)}
