<!--
記述規約の正本は AGENTS.md の「GitHub Issue / Pull Request の記述規約」。
- PR title は `<type>: <日本語の要約> (#<issue-number>)`。
  type は feat / fix / docs / test / refactor / chore など。
- merge は CI/checks が成功したことを確認してから行う。
- PR 作成後に追加修正や検証結果の変化があった場合は、コメントだけで済ませず本文を最終状態へ更新する。
不要な HTML コメントは削除して構いません。
-->

## 関連 Issue

Closes #<issue-number>

<!-- merge で完了する Issue は `Closes #N`、参照のみは `Refs #N`。複数ある場合はすべて列挙する。 -->

## 背景 / 原因

Issue の要点と、この変更が必要な理由を記載する。bug fix では根因を記載する。

## 変更内容

- 変更した責務・挙動を具体的に記載する
- schema / API / UI / data migration / compatibility への影響を記載する

<!-- commit の羅列ではなく、reviewer が挙動差分と責務境界を判断できる粒度で書く。
     変更していない重要範囲や backward compatibility も必要に応じて明記する。 -->

## 検証結果

- `<実行した command>` — pass / fail / skip と件数
- 手動確認または Playwright の対象 flow / viewport / 状態

<!--
- 実行した正確な command と結果を書く。失敗・skip・未実行を隠さず、今回の変更によるものか既存問題かを分ける。
- backend: `uv run pytest` / `uv run ruff check .` / `uv run mypy .`
  frontend: `npm run lint` / `npm run build` / `npm run test`
- UI/UX 変更では、対象 Playwright spec、desktop / 375px viewport、主要導線と
  空 / 読込 / エラー / ブロック状態の結果を記載する。
- OCI / Oracle / LLM を呼ぶ範囲は、CI 上の決定論スタブ確認と手動 / ステージングでの
  実サービス確認を区別して記載する。
- docs-only など test 対象外でも省略せず、`git diff --check` 等の実施結果と、
  コード test を実行しない理由を記載する。
-->

## 既知の制約・残課題

- 未対応範囲、既知の制約、follow-up Issue を記載する。なければ「なし」と記載する。
