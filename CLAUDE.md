# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

機密情報を Git・GitHub など外部へ出す前に検査する Claude Code plugin（marketplace 1 plugin 構成）。plugin 本体は `linter/`（plugin 名 `linter`、スキル `/linter:secret-linter`）。使い方・インストールは `README.md`、要件・設計は `docs/specs/secret-linter/`（REQ-032 / DES-087）、機密の定義と許容される書き方は `linter/docs/sensitive_information_spec.md` を参照。

## コマンド

```bash
# 全テスト（CI と同じ）
python3 -m unittest discover -s tests -t . -p 'test_*.py'

# 単一ファイル / 単一テスト
python3 -m unittest tests.test_judge
python3 -m unittest tests.test_judge.<クラス>.<メソッド>

# フォーマット検査（CI で実行。dprint 0.55.1 — markdown / ruff / yaml）
dprint check
```

CI は上記に加え `.claude-plugin/marketplace.json` と `linter/.claude-plugin/plugin.json` の JSON 妥当性を検証する。Python は標準ライブラリのみ（`linter/` 配下に依存を足さない）。

## アーキテクチャ

検査制御は SKILL、決定論的処理は script、形式を持たない混入の検出は Agent に分担する。

- `linter/skills/secret-linter/SKILL.md`: 検査制御（手順 1〜8）。**処理ロジック・分岐判定・JSON 組み立てを SKILL.md に書かない**。script を順に呼ぶだけ。最終行は `secret-linter: 合格 / 不合格 / 検査を実行できなかった（理由）` の 3 択（呼び出し元が機械的に読む）
- `linter/skills/secret-linter/scripts/`: パイプライン
  `scan.py`（範囲解決 `targets.py`/`git_targets.py`/`history.py` → `units.json`、パターン検出 `patterns.py` → `pattern.json`）→ `ai_units.py` / `ai_add.py` / `ai_check.py` / `ai_finish.py`（AI 検出の受け渡し）→ `judge.py`（規則は `judgment.py` に 1 か所、出力整形は `judge_summary.py`）→ `answer.py`（要確認への回答を `lists.py` でリストへ追記し再判定）→ `report.py`（結果本文）→ `cleanup.py`（作業領域削除）
- `linter/agents/secret-reader.md`: AI 検出 Agent。受け取るのは検査 ID だけ。候補は `ai_add.py` 経由でのみ報告し、JSON を自分で組み立てない。合否は決めない
- `workspace.py`: 検査 ID から決まる OS 一時ディレクトリ配下の作業領域（0700）。ファイルは書き込み後に上書きせず、変化するもの（AI 候補・判定）は連番 `NNNN.json` で追記する
- `secret_common.py`: JSON 出力・エラー形式・`--project-root`（絶対パス必須）の共通部品。script 間は同ディレクトリからの import（直接実行しない）
- `assets/*.yaml`: 同梱の許可リスト（公開ドメイン・公開パッケージ接頭辞）。プロジェクトの許可リストは `.secret-linter/allowlist.yaml`、機密の名前リストは `~/.project-secret.yaml`

### 守るべき不変条件

- script の終了コードは 0（完了。不合格も完了）/ 1（完了しなかった。標準出力 `errors`）/ 2（引数。argparse）
- 検出した値は利用者向け本文にだけ出す。呼び出し元へ返す最終行・ログ・ファイル・エラー理由に含めない。結果はファイルに残さない
- 判定規則（J-1）は `judgment.py` のみ。`judge.py` と `answer.py` は同じ規則を呼ぶ
- バックグラウンド Agent の完了待ちに `ScheduleWakeup` を使わない（SKILL.md 手順 2）

## テスト上の注意

secret-linter は**このリポジトリのテストファイル自身も検査する**ため、機密に見える値をソースへ直書きしない。入力の作り方の優先順は `tests/vocab.py` の docstring に従う: ①予約値（`example.com` 等）をそのまま書く → ②`vocab.no_reserved()`（同一プロセス）/ `vocab.argv(..., no_reserved=True)`（子プロセス。`tests/run_no_reserved.py` 経由）で予約値定数を空にする → ③予約値で表せない値だけ `domain` / `ip` / `legal_en` / `legal_ja` で断片から組み立てる。`tests/ai_support.py` は AI 検出テストの共通部品。`ai_tests/` は `secret-reader` の手動評価（自動テスト対象外）。

## バージョン・文書

- バージョンは `.version-config.yaml` で管理（`linter` は `linter/.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json` と同期。`marketplace` は別ターゲット）。更新は `/forge:update-version`、`CHANGELOG.md` は keep-a-changelog 形式
- 検出・判定・リスト・出力の挙動を変えたら、同じ変更の中で `docs/specs/secret-linter/`（REQ-032 / DES-087）、`linter/docs/sensitive_information_spec.md`、`SKILL.md`、`README.md` の該当記述を直す
- コードコメント・ドキュメントは日本語

<!-- FORGE_ONBOARDING_START hash=66047b4b7b46 -->

> このブロックは forge の onboarding スキルが生成する。手で編集しない（次回実行で上書きされる）。
> `${CLAUDE_PLUGIN_ROOT}` は forge プラグインの配置先を指すプレースホルダであり、この文脈では実パスに解決されない。実体を読むには onboarding スキルを起動する。
> forge はこのブロックの範囲だけ CLAUDE.md を利用している。ブロックの外側はプロジェクトの所有物。

## forge SSOT の定義

- SSOT とは「真理が単一」であることではなく、ある関心事について「変更と不整合を収束させる先が一意」であること。正本は絶対的な真理ではなく、反証されれば更新される循環の現在値である。

## forge 文書規範

- 文書内に「改定履歴」「変更履歴」等の履歴セクションを置かない。履歴は CHANGELOG と git 履歴が持ち、設計判断の変更は ADR に記録する
- 「関連文書」等のリンク集セクションを置かない。必要でない参照は書かない。書かなければ本文が理解・検証できない参照だけを `[表示名](相対パス)` 形式で書く
- 要件（What）と設計（How）の境界は「ユーザーマニュアルに書く内容か」で判定する。ただし決定済みの「設計」「実装」は要件として記載されることもある。
- 設計中に要件定義書に無い必要が判明したら設計を止める。要件へ戻す場合も、戻せず設計書に書く場合も、ユーザーの承認を得る。後者は承認を ADR に記録し、設計書の当該節からリンクする
- 未確定の定量値を推測して書かない（TBD とする）。責務分割・境界・依存方向などの構造品質を数値目標に翻訳しない
- 実装・設計・命名を変えて文書と乖離が生じたら、同じ変更の中で文書も直す
- ADR に仕様・ルールの本文を写さない（真実源が 2 つになる）。ADR を根拠に提案を見送るときは、棄却理由の前提が今も成立するかを確かめ、根拠にした ADR ID を添える。ADR の節番号は変えない
- 新規の文書 ID（REQ- / DES- / ADR- 等）を手動で採番しない（採番スキルを使う。並行ブランチでの ID 衝突を防ぐ）
- 差分開発型と判定した文書には、一時マーカー（frontmatter の `feature_type: temporary-feature`）を付ける。判定は要件定義書・設計書それぞれで行い、両方に付くとは限らない（要件定義書が差分開発型なら設計書にも付く。要件定義書が差分でなくても、設計が既存の設計書を変えるなら設計書だけに付く）

## forge 実装規範 [MANDATORY]

- 読まずに書き始めない。必読文書・検索でヒットした文書は、見出しや部分読みで済ませず本文を読む。調査は「該当なし」も記録し、未調査と区別できるようにする
- 推測で埋めない。仕様・設計に無い値を空想（類似からのコピー・「よくある値」）で実装しない。重要な仕様が未定義なら実装を止めて報告する
- 実装で同じ役割のものを並べない。作る前に既存資産を探し、使えるものは使う。合わないものは直すか置き換える（整合のための削除は必要な労力である）。兄弟機能で確立されたパターンを壊さない。スコープ境界や影響範囲の狭さを口実に、同種の実装を並列に新設しない
- 設計は既存の実装に合わせず、要件から組み立てる。既存の実装をどう修正・削除・統合・分割するかは、設計ではなく実装戦略書が決める
- 複数箇所から参照される共用部品を、1 箇所の都合のために書き換えない
- 差分 feature を使うかは、要件定義書・設計書が定める意味が変わるかで判定する。意味を変えるなら差分 feature を使い、実装期間中は旧仕様ファイルを書き換えず、新規ファイルへ切り出す。意味の変更を「バグ修正」と呼んで、この手続きを飛ばさない
- 不具合（意味の正しい仕様に、実装や文書の記述が食い違っている状態）は差分開発にせず、原本を直接直す。文書の記述を書き換えても、意味が変わらなければ不具合の修正である
- 修正の検証は変更を反映した実体で行う。変更前の状態で得た結果を根拠に「直った」と判断しない。改名・語彙改訂・大規模な書き換えの後は、改名した識別子だけでなくその別名・形式の呼び名も検索し、検索語と範囲を残してから完了とする
- 「理論上あり得る」だけを根拠にした防御実装・投機的対策を積み増さない。この抑止を適用してよいのは投機的な防御の類型だけであり、判定に迷う指摘は通常どおり修正対象として扱う

## forge 機密情報 [MANDATORY]

- 認証情報・秘密鍵・資格情報を含む接続文字列・実在する個人の情報・内部ネットワーク情報をリポジトリに含めない
- commit された秘密は削除しても漏洩として扱う。修正は該当する秘密の失効・再発行を伴う
- 例示に実在の値を一部改変したものを使わない（公式の例示値・明白なプレースホルダを使う）。混入かどうか判断に迷う値は混入として扱う
- 検出した秘密の実体を報告・所見・ログに書き写さない。種別・位置・先頭数文字までに留める

## forge プロジェクト文書

- プロジェクトルール文書の参照には `query-db-rules` SKILL を使う
- プロジェクトルール文書の更新後には `update-db-rules` SKILL を使う
- プロジェクト仕様の参照には `query-db-specs` SKILL を使う
- プロジェクト仕様の更新後には `update-db-specs` SKILL を使う

## forge 重要規約

- **実装・文書改編に着手する前に `/forge:query-forge-rules`・`/forge:query-db-rules` で関連する原則・ルールを特定して読む**（スキル経由の作業は各スキルの調査 Phase がこれを担う。会話直の作業でも省略しない）
- **ルールはルール文書管理**: コンテキスト肥大化防止のため、CLAUDE.md にルールを詰め込まないことを推奨する
- **一般の作業で CHANGELOG.md・version 関連ファイルを編集しない**。リリースコミットでまとめて更新（`/forge:update-version` を使う）
- **`.toc_work/` 等の消えるべき一時物は `.gitignore` に入れない**。残存が `git status` に untracked として出ることで異常を検知できる
- セマンティック検索とは、字句の一致ではなく意味に基づいて文書を選ぶ検索であり、実現手段（ベクトル類似度か LLM による読解か）は問わない
- query-db-xxx SKILL はセマンティック検索であり、ワード一致検索（grep）ではない。**取りこぼしを出さないこと（false negative 厳禁）を規約として検索する**ため、意味的に近い文書を広く拾って返す。最終的に何を読むかは、何を求めて検索したかを知っている検索の実行主体が判断する
- doc-advisor のフロントマターを持つ文書を編集したら、`/doc-advisor:write-frontmatter --paths <編集した文書>` で再生成する。`Edit` / `Write` で直接書き換えてはならない（`body_hash` の打刻・値域検証・マージ規則は script の責務であり、手書きでは信頼されないフロントマターになる）。**編集した本人がその場で書いておく**。信頼できるフロントマターが原本にあれば、後日 `update-db-rules` / `update-db-specs` が走るときに AI 抽出が省かれ、索引が高速に作られる（信頼できなければ AI 抽出へフォールバックする）
- **forge plugin の script は、対応する SKILL.md が明示的に「直接呼び出す」と指示しているもの以外は直接実行しない**

<!-- FORGE_ONBOARDING_END -->
