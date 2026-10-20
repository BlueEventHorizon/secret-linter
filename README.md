# secret-linter

機密情報（認証情報・秘密鍵・接続文字列・個人情報・内部ホスト名等）が Git・GitHub など外部へ出る前に検査する、Claude Code の plugin。結果は、合格 / 不合格 / 検査を実行できなかった、のいずれかで返す。

commit された秘密・GitHub に書き込まれた秘密は、削除しても漏洩として扱う。そのため、止める時点は外へ出す前である。

## インストール

```bash
/plugin marketplace add BlueEventHorizon/secret-linter
/plugin install linter@secret-linter
/reload-plugins
```

## 使い方

```text
/linter:secret-linter [--diff|--branch|--files a.md ...|--dirs d1/ ...] [--rules-only]
```

| 範囲の指定      | 検査する範囲                                                              |
| --------------- | ------------------------------------------------------------------------- |
| `--diff`        | 未 commit の変更のファイル全体（省略時）                                  |
| `--branch`      | push で外へ出る履歴（リモートに無い commit の追加行とコミットメッセージ） |
| `--files パス…` | 指定ファイル全体                                                          |
| `--dirs パス…`  | 指定ディレクトリ配下のファイル全体                                        |

`--rules-only` は、AI による検出（`linter:secret-reader` Agent）を行わず、パターンによる検出だけを行う。速さを優先する指定で、外へ出す直前の検査などで付ける。

## 判定とリスト

検出した値を、不合格・要確認・通過に分ける。1 件でも不合格なら全体が不合格になる。要確認は利用者に確認し、回答に従って次のリストへ追記する。

| リスト           | 置き場                                                   |
| ---------------- | -------------------------------------------------------- |
| 許可リスト       | 同梱分と、プロジェクトの `.secret-linter/allowlist.yaml` |
| 機密の名前リスト | `~/.project-secret.yaml`                                 |

何を機密とするか・許容される書き方は、[sensitive_information_spec.md](linter/docs/sensitive_information_spec.md) が定める。

## 開発

標準ライブラリのみを使う（`linter/` 配下）。

```bash
python3 -m unittest discover -s tests -t . -p 'test_*.py'
```

要件・設計は [docs/specs/secret-linter/](docs/specs/secret-linter/) にある。`ai_tests/` は `secret-reader` Agent の手動評価である。
