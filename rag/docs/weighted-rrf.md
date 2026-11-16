# 検索結果の統合（RRF）

全体の「検索方法」→「検索文の作成」の後に、ランキングの寄与を指定する比重を置く。

- ベクトル検索 : 全文検索 = **7 : 3**（`RAG_RRF_METHOD_RATIO=[7,3]`）。

未保存の環境にも新しい既定値を使う。非負の有限値で指定し、組内の全ゼロを拒否する。候補の件数と rerank は独立で、結果の件数や回答内容の比率を約束する設定ではない。rerank に渡る候補は変わり得る。KB は対象範囲、文書レシピは取込の責務を維持し、比重を保存しない。

## 計算

### 主検索は 1 回（#5159）

主検索（回答の流れの「文書検索」）は 1 回の検索で、方式ごとに入力を分ける。

- **ベクトル検索の文は原質問だけ。** embedding は原質問の 1 回だけ作る。自然言語の質問に語を足すと、意味の中心が足した語へずれる（query drift）ため。
- **全文検索の文は「原質問＋追加語」**（`runtime_knowledge.expanded_question`。追加語の出どころは「用語・同義語」「業務ガイド」「確認で選んだ条件」）。追加語が無ければ原質問。足した語が効くのは全文検索の語なので、全文検索にだけ足す。原質問と追加語は改行で区切られ、検索文の正規化（`query_prompts._normalized_query_text`）は改行・タブを含む空白を 1 つの空白にする（消さない。原質問の末尾の語と最初の追加語をつなげない。#5160）。

原生方式は 1 回の `DBMS_HYBRID_VECTOR.SEARCH` に、`vector` として原質問の embedding、`text.contains` として「原質問＋追加語」から作った式を渡し、`search_fusion=UNION` / `search_scorer=WRRF` で検索・融合する。`vector.score_weight` と `text.score_weight` に設定比率（既定7と3）、両方の `rank_penalty` に既存定数（既定60）を渡す。各経路の候補上限を維持し、返却上限は両経路の和にする。個別SQL方式はベクトルの SQL（原質問の embedding）と CONTAINS の SQL（「原質問＋追加語」の式）を 1 本ずつ実行し、両方の原始順位をアプリで融合する。どちらの方式も主検索の DB の検索は 1 回分（以前の 2 本の検索文の半分）で、原質問と追加検索文の比重は使わない。

検索の実装へは、検索文（ベクトル検索の文）ごとの全文検索の文を `text_queries`（`build_adb_hybrid_answer_context` の引数。backend の `AnswerEngine._search`・`rag_engine` の `search_adb_hybrid_chunks` が受ける）で渡す。一覧の同一性と重みは検索文（原質問）で決め、全文検索に使った文は一覧の `text`（計算記録の `text_query`）に残す。

全文検索の語は上限（`RAG_TEXT_SEARCH_MAX_TOKENS`。既定 24 語）と式の文字数の上限（`RAG_TEXT_SEARCH_MAX_QUERY_CHARS`。既定 3,000 字）までなので、足した用語・同義語はドメインキーワードと同じ段で優先し、長い質問の後ろの別名が上限で落ちないようにする（#2163）。重みはドメインキーワードと同じく主の語と同じ 1 にする（#5157）。登録した語は全文検索の文のときだけ `retrieval_query_keywords`（原生方式では `bind_text_query_keywords`）で全文検索の語の組み立てに渡す。

`score(d) = native_WRRF(d) / (100 × (k + 1))`（原生方式）、`score(d) = Σ channel_weight / (k + rank(d))`（個別SQL方式・質問の embedding の失敗時は全文だけ）。

Oracle WRRF は理論上の最大値を100とするため、原始の正規化した順位寄与へ尺度を戻す。融合済み結果の順位に再度RRFを掛けない。空結果・失敗・未使用経路の予算を再配分しない。

### 原質問と追加検索文の比重（独立 engine の経路だけ）

1 回の検索に原質問とほかの検索文を渡す経路では、原質問と追加検索文の出所の予算で一覧の寄与を合算する。主検索は 1 回なので製品（backend）の検索には該当が無く、全体設定（画面・`rag/backend/.env`）には持たない。`rag_engine` を直接使う経路（SDK の `SearchRequest.queries` に複数の検索文を渡すとき、独立 engine の業務ガイドの手順の検索〔「質問＋手がかり」と「手がかり」〕）だけが、`rag_engine` の設定 `RAG_RRF_SOURCE_RATIO`（既定 `[7,3]`。独自の予算で Oracle 公式の既定ではない）を使う。

`score(d) = source_weight(original) × list_contribution(original, d) + Σ source_weight(additional) × list_contribution(additional, d)`

追加文は正規化・重複除去後の集合で追加予算30%を等分する。追加がなければ原質問の出所予算は1。比重が順位に効く（原質問の一覧とほかの検索文の一覧が両方ある）ときだけ、処理の経過に「原質問と追加検索文の比重」を出す。

個別SQL方式（設定値 `oracle_separate_sql`）は各一覧のvector/text原始順位をアプリで融合する。独立 engine の旧schema経路では原生方式へ暗黙に切り替えず、backend入口を使う。順位定数はbackendの `RAG_RRF_K`、旧schemaの `RRF_K` を維持する。

画像のベクトル索引が有効な engine の直接経路では、既存の画像対テキスト埋め込みの比重を使い、**ベクトル方式の予算の中**を二つの埋め込み一覧に配分する。画像一覧の増設でベクトル方式の総予算を増やさない。

交差参照・引用ラベル・近傍・親子章節などの独立した根拠補完はこの一覧に入れず、範囲・権限・上限を維持する。交差参照・実体・文書構造の検索拡張の chunk は融合にも rerank の候補にも入れず、rerank と起点の選択の後に最後の根拠へ合わせる（#5162。[rag-engine.md の「検索拡張の根拠の合わせ方」](./rag-engine.md#検索拡張の根拠の合わせ方50975162)）。質問の語で比重を切り替える処理は置かない。

## 従来との差分

backend の「検索文ごとの hybrid 融合 → 検索文間の二度目の RRF」を廃止。原質問 1 票・通常派生合計 1 票・vector 専用派生合計 1 票という別枠も廃止する。直接 engine 経路の旧 `RETRIEVAL_ORIGINAL_QUERY_WEIGHTING` は新しい二組の比重へ置き換える。全追加文が一つの予算を分け合うため、既定でも候補順位は従来と変わり得る。

#5159 で主検索を 1 回にした（原質問と「原質問＋追加語」の 2 本の検索文の検索をやめ、ベクトルは原質問・全文は「原質問＋追加語」の 1 回の検索にした）。全体設定の `RAG_RRF_SOURCE_RATIO`（画面の「原質問と追加検索文の比重」）は削除した。保存済みの `rag/backend/.env` に残った値は読まない。

## Trace と検証

「検索結果の統合（RRF）」に、設定比重・検索文の出所・全文検索の文（語を足したとき）・実際の一覧の最終重み／状態／件数と、片段ごとの順位・寄与・融合スコアを同じ計算結果から表示する。工程「検索文をまとめ、検索語を作る」は「ベクトル検索の文」（出どころ: 原質問）と「全文検索の文」（出どころ: 原質問 + 用語・同義語 など）を分けて示す。原生方式の主検索は一覧が 1 本なので、追加の統合の段は「原質問の検索内で統合済み」（skipped）になる。重複除去前後の件数を区別する。計算記録は既存の権限付き詳細に保存し、既存の表示上限と切詰め表示に従う。新しい公開ログに質問・本文やベクトルを記録しない。

合成順位の厳密計算、設定の保存、実 Oracle の KB／プロファイルと権限範囲を回帰テストする。検索の召回と回答の品質は別に比較する。既定の比重の変更だけを理由に精度向上とは扱わない。

参考: [Azure のベクトルの重み](https://learn.microsoft.com/en-us/azure/search/vector-search-how-to-query#vector-weighting)、[RRF](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking)。順位寄与への乗算を参考にするが、本実装は出所ごとの総予算と方式の正規化を持ち、Azure と同一の仕様とは位置付けない。

Oracle公式: [SEARCH](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/search.html)、[WRRFの計算と尺度](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/understand-hybrid-search.html)。比率は品質向上の保証ではない。

### 原生検索の範囲指定

許可された chunk 集合を検索前に確定し、全件が許可された chunk set は `RAG_NATIVE_SEARCH_SCOPES` の安定した数値キーで指定する。ページなどで限定された集合は、許可外の行を含まない chunk index の区間か個別 ID で指定する。検索後にも従来の権限・明示範囲を照合する。長い ID の全列挙による Oracle 内部バッファ超過を避けるための表現変更であり、検索範囲や比率は変更しない。

`20261101_003_native_hybrid_scope` は対応表・補助列・索引・同期 trigger を追加する。本文・embedding は書き換えず、外部モデルも呼ばない。原質問のベクトル／全文検索は1回の `DBMS_HYBRID_VECTOR.SEARCH` で実行し、許可集合の取得用 SQL はその前に実行する。
