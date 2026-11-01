# 引用を起点とする本文の読み取り

RAG の公開 MCP が返す署名つき `content_url` は、原本のほかに部分閲覧も提供する。
Agent は RAG の内部 REST API・DB を利用せず、この公開契約を使用する。

- `GET <content_url>?view=reader`: 引用に紐づく同一解析版の要素と前後文。
- 続き: `cursor=<previous_cursor|next_cursor>&direction=before|after`。
- 章節: `scope=section|parent`。目次の項目: `scope=section&section_id=<outline.id>`。
- 章節・目次の明示的な選択は選択範囲の冒頭から読む。cursor のない paragraph は元の引用位置へ戻る。
- `view` 省略時は従来どおり原本。完全ダウンロードは `disposition=attachment`。
- Cookie・Referer を送らない。毎回、署名・期限・利用者・現在の権限と文書版を再確認する。
  404 は参照不可、409 は版の変化、410 は署名の期限切れ。新しい版へ黙って付け替えない。

JSON の `data` は共有 UI の `EvidenceReadingWindow` に対応する:

| 項目 | 意味 |
|---|---|
| `revision` | 引用が属する chunk set の版 |
| `anchor_status` | `exact`（原要素内の引用が一意）、`element`（要素のみ確定）、`unresolved`（保存された引用のみ） |
| `section_path` | 引用が属する章節の表示名 |
| `blocks` | 元の抽出配列の順序。`id`, `text`, `offset`, `highlights`（表示文字列内の半開区間）, `cited`, `kind`, `heading` |
| `previous_cursor`, `next_cursor` | 同一版・章節内の読み取り位置。なければ null |
| `outline` | 現在枝の祖先・兄弟・子の概要（最大25件）。`id`, `title`, `parent_id`, `has_children` |
| `scope` | 適用した読み取りモード |

1応答は本文8192文字、9要素まで。章節名も長さを制限する。画面は直近3窓まで保持する。
前後文は検索順位や隣接 chunk の文字列連結を使わない。MCP と共通の
`read_source_window` で段落・文・表の見出しを保つ。追加の参考文脈は回答生成時の根拠と区別する。
文書に章節がない場合は章節操作を無効にする。旧記録に要素 ID がない場合は引用だけ表示し、
全文検索で似た文字を別の位置へ結び直さない。

`reader` の位置は**抽出本文の Unicode codepoint**で、原 JSON のバイト位置や JS の UTF-16 index ではない。
完全な原本は別操作で取得できる。

## XML ツリー JSON の原本ノード

`GET <content_url>?view=source-map&element_id=<blocks.id>&source_index=0` は、同じ署名・権限・
chunk set の版を確認した上で、その解析成果が保存した原本対応を読む。Agent はこの公開の署名 URL を
使用し、RAG の内部 API を呼ばない。未展開では取得しない。

- RAG の引用は `GET /api/documents/{id}/source-map?chunk_id=...&element_id=...&expected_revision=<reader.revision>`。
- 文書の構造化要素は `GET /api/documents/{id}/recipes/{recipe_id}/source-map?element_id=...&expected_revision=<active_extraction_recipe_id>`。
- `expected_revision` は引用では chunk set、文書レシピでは解析成果の ID。署名 URL は claims の chunk set を正本とする。
- JSON の `data.status` は `exact` / `unrecorded` / `unresolved` / `version_mismatch`。
  版違い・旧記録・壊れた対応を全文検索で似た位置へ付け替えない。ファイル欠落は404、版の競合は409。
- `revision` は解析成果、`chunk_revision` は引用の chunk set。`variant` は `original` / `prepared`。
  保存した SHA256 と一致するファイルだけを使い、どちらを読んだかを表示する。
- `pointer` は RFC6901 JSON Pointer。`source_index` / `source_count` は同一要素が参照する文字列ノードの順序と総数。
  段落の複数ノードや結合セルの一対多の対応を保持する。引用本文が当該要素内で一意なら、対応をその引用範囲に絞る。
  一意でなければ `anchor_status=element` とし、引用の文字範囲が未確定であることを示す。
- `text` は原 JSON の文字列値の有界な断片（escape は原本のまま）。`offset` / `previous_offset` / `next_offset` と
  `highlights` は、BOM を除く原 JSON または表示文字列内の Unicode codepoint。UTF-8 bytes や UTF-16 index と混同しない。
  続きを読むときは同じ `source_index` と返却された `offset` を渡す。
- 一応答は本文8192文字、pointer4096文字まで。原本をブラウザへ全量転送・整形しない。
  サーバーの保存ファイル取得と JSON の構造走査は現時点では全体を読む（上限128 MiB）。ストリーム索引ではない。

`xml_tree_json_v2` は parser artifacts の `json_source_map` に raw bytes の SHA256、座標単位、
原ノード内と抽出要素内の半開範囲を保存する。本文の複写は保存せず、chunk metadata に大きな範囲配列を転記しない。
生成見出し・Markdown 記号・置換した空白は原文の一致として扱わない。要素 ID と解析成果を固定し、
hash・ノード・範囲内の実際の文字が一致した箇所だけを強調する。旧成果には自動で補完せず、対応する解析方式での再解析が必要。
