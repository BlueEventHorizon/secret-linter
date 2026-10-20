#!/usr/bin/env python3
"""secret-linter の script を、文書用の予約値の定数を空にした状態で走らせる実行器（テスト用）。

使い方: ``python3 run_no_reserved.py <script> [引数...]``。``vocab.argv(..., no_reserved=True)`` が組み立てる。
``judge.py`` 等を子プロセスで走らせるテストが、``docs.example.com`` 等を予約値ではない値として扱うために使う。
同一プロセスのテストは ``vocab.no_reserved()`` を使う。

テストではない（``test_*.py`` に一致しない）。
"""

import runpy
import sys
from pathlib import Path

script = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(script.parent))
sys.path.append(str(Path(__file__).resolve().parents[3] / "plugins" / "forge" / "skills" / "secret-linter" / "scripts"))

import judgment  # noqa: E402

judgment.RESERVED_DOMAINS = ()
judgment.RESERVED_TLDS = ()
judgment.RESERVED_NETWORKS = ()

sys.argv = [str(script), *sys.argv[2:]]
runpy.run_path(str(script), run_name="__main__")
