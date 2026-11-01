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

この位置は**抽出本文の文字位置**で、原 JSON のバイト位置ではない。原本のノードへの
直接ジャンプを保証しない。完全な原本は別操作で取得できる。
