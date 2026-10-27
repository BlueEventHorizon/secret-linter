# Changelog

このリポジトリの plugin の変更を記録する。書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に従う。

## [Unreleased]

## [0.1.1] - 2026-10-27

### linter

- **fix**: 文字列中の `\n`（バックスラッシュと n の 2 文字）を、`secret_assignment` の値に含めていたため、`dummy-placeholder\n` のような値が placeholder として除外されず、不合格になっていた。値を `\n` の手前で終えるようにした（#2）。
- **fix**: `secret-reader` が `ai_add.py` を 1 つの Bash コマンドに並べて実行すると、拒否の結果を読んでから立て直せず、拒否が 2 回続いて AI 検出が実行不能になっていた。Agent の定義に、`ai_add.py` を Bash の 1 回の実行につき 1 件だけ呼ぶことを明記した（#3）。
- **changed**: plugin のマニフェスト（`plugin.json`・`marketplace.json`）の作者・所有者の名義を `moons` に改め、`$schema` を除いた。

### marketplace

- **chore**: linter 0.1.1 のリリースに伴い marketplace バージョンをバンプ

## [0.1.0] - 2026-10-20

### linter

- **feat**: `secret-linter` SKILL（`/linter:secret-linter`）を、forge プラグインから独立した plugin として切り出した。機密情報が Git・GitHub など外部へ出る前に検査し、合格 / 不合格 / 検査を実行できなかった、を返す。
- **feat**: `secret-reader` Agent（`linter:secret-reader`）を同梱した。形式を持たない混入を、本文を読んで候補として報告する（合否は決めない）。
- **feat**: 規範文書 `sensitive_information_spec.md`（機密の定義・重大度カタログの SoT）を同梱した。
- **changed**: プロジェクトの許可リストの置き場を `.forge/secret_allowlist.yaml` から `.secret-linter/allowlist.yaml` に改めた。forge 版からの移行では、ファイルを移して名前を改める。
- **changed**: 作業領域の接頭辞を `forge-secret-linter-` から `secret-linter-` に改めた。
