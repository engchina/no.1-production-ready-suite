# 根拠の読み方（business-rag-research）

根拠の抜粋だけでは足りないとき（切り詰められている・前後の条件を確かめる・章節を終わりまで読む・同じ文書の別の箇所を読む）に読む。ツールは MCP サーバー `rag` のもの。

- 根拠の `excerpt` が切り詰められている（`truncated`）か、前後の条件・例外を確かめる必要があるときは、`rag_read_source` に `document_id` と `chunk_id` を渡して本文を読む。
- 根拠の `section` は、その根拠が属する章節（文書の章節ナビゲーションの章節。`section_id`・頁の範囲・文字数・親の章節）。
- 質問が手順・一覧・条件のまとまり（「手順をすべて」「確認する項目」「条件の一覧」など）を求め、根拠の章節の文字数（`section.chars`）が抜粋より長いときは、抜粋だけで答えず、`rag_read_section` に `document_id` と根拠の `chunk_id`（または `section_id`）を渡して章節を終わりまで読む（`section_complete` が `false` なら `next_cursor` で続きを読む）。
  - 章節を読んでも手順・条件が足りなければ、`up` を 1 ずつ増やして親の章節を読む。
  - 上がる回数・読む量（`max_chars`）は、この実行の残りのツールの呼び出しに合わせる（結果の `section_reads` が付いたらその `next_step` に従う）。
  - 答えには読んだ章節の名前と頁を示す。
- 検索で当たらなかった前後の章・本文が指す同じ文書の別の箇所（「第 4 章を参照」など。結果の `references`）・長い文書の続きは、`rag_outline` で文書の章節の構成（`section_id`・階層・頁）を見て、`rag_read_section` で章節を読むか、`rag_read_document` で順に読む（`section_id`・`page`・`locator` で読み始め、続きは `next_cursor` を渡す）。
- 根拠の `locator.element_locator` は `rag_read_source` と `rag_read_document` の `locator` に渡せ、chunk が作り直されても同じ箇所を読める。
