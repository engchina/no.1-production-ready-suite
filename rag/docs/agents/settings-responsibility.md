# RAG の設定責務（3 層モデル）

> rag/AGENTS.md から移した詳細（#2276）。rag/AGENTS.md の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、AGENTS.md には規則の要約と案内だけを書く。

## RAG 設定責務

> **3 層モデル（確定）**: 文書 = 処理レシピを持つ / KB = 純スコープ（コレクション）/ Search Answer Profile → KB。
> KB は「レシピ」と「検索スコープ」を兼任しない。レシピは文書単位、検索・回答設定は Search Answer Profile。

### 文書（Document）= レシピ

文書は中身に加えて **1〜3 件の独立した処理レシピ（preprocess / parser / chunking）** を自身のプロパティとして持つ。各レシピは設定、ジョブ、成果物、エラー、工程状態を個別に保持する。

- ファイル準備（preprocess）。
- 文書解析（parser / OCR engine）と、図・画像の読み取り（Vision。解析エンジンに関係なく解析の後に backend の共通の段で読み取る。切り替えはレシピだけ）。global の既定エンジンは Docling（PDF と画像だけ）。それ以外の形式は取込前に止めて処理レシピで Unstructured などを選ぶよう案内し、自動では振り分けない（判定は `backend/app/rag/parser_source_guard.py` の 1 か所。Unstructured のサービスは統合 stack の既定で配備する。#1559）。XML ツリー JSON（tag / attr / children の木の JSON。parser-xml-tree-json）も Docling・Unstructured と同じ使用エンジンの 1 つで、選んだときだけ使う（形式で自動には振り分けない。対応しない形の JSON は取込で扱えない旨のエラーにする。サービスは既定で配備する。#2042）。
- 文書分割（chunking strategy、chunk size、overlap、parent-child）。
- 索引構築（vector index build、GraphRAG、navigation summary、field extraction）。
- 品質 gate（解析品質、chunk 品質、公開前チェック）。

レシピの既定値は **global（「取込・回答設定」配下の ファイル準備 / 文書解析 / 文書分割 など）** から解決し、各レシピが選んだ値で上書きする。**KB からは解決しない**。embedding / HNSW は単一固定。同じ文書のジョブは直列実行し、異なる文書のジョブは並行実行できる。

### ナレッジベース（KB）= コレクション（純スコープ）

KB は **どの文書を検索対象にするか（membership）だけ**を持つ純スコープ。**レシピ（preprocess / parser / chunking）も検索・回答設定も持たない**。

- 文書 membership（N:N。1 文書が複数 KB に所属可、1 KB が複数文書を束ねる）。
- 名称 / 説明 / スコープ。
- 例外として、項目抽出の項目の定義（何を取り出すか）は KB ごとに持てる（無ければ全体の既定、複数 KB に属する文書は和集合。docs/knowledge-base-management.md の 8.4.1a）。項目抽出を行うかどうかは文書レシピ / global が決める。

文書の KB 出し入れは `rag_document_knowledge_bases` の行 add/delete **のみ**で、chunk へ波及しない（再プラン / materialize / GC を起こさない）。KB UI から preprocess / parser / chunking・検索方法・安全チェック・品質評価を出さない。KB の legacy adapter/query config は読み取りのみ許容し、runtime では使わず、次回保存で再保存しない。

### Search Answer Profile

Search Answer Profile は **検索・回答に使う設定だけ**を持つ。

- 参照 KB scope。
- 検索方法（回答の検索と生成）、安全チェック。
- feedback 集計。

設定責務は次の通りとし、検索・回答プロファイルには Sidebar 全項目を複製しない。

| 設定群 | グローバル既定 | 文書レシピ上書き | Search Answer Profile 上書き |
|---|---|---|---|
| ファイル準備 / 文書解析 / 文書分割 / GraphRAG | 可 | 可 | 不可 |
| 検索インデックス | 可 | 不可 | 不可 |
| 検索方法 / 安全チェック | 可 | 不可 | 可 |
| 回答プロンプト | 可 | 不可 | 不可 |
| 品質評価 | 可 | 不可 | 不可 |

関係情報の構築（構築する / しない。文書と章・節の見出しのつながり）は文書レシピで選ぶ（ナレッジベースの関係情報グラフで見られる。回答の検索で使うか〔当たった根拠と同じ狭い章節・表の根拠と、質問に見出しの語が含まれる章節の根拠を候補に足す。既定 OFF〕は検索方法 / 検索・回答プロファイルの「関係情報で根拠を広げる」〔`graph_expansion_enabled`。#2055〕で選ぶ。LLM で意味の entity を抜き出す取込と claims / community summary の構築は持たない）。共有 Oracle 索引の設定は Search Answer Profile へ保存しない。

検索時の解決順は **request 明示 > Published Search Answer Profile > global defaults**。KB の legacy query override は使わない（保存時にも新規保存しない）。

### 複数レシピ融合（精度向上）

同じ文書の検索精度を上げたい場合はレシピを最大 3 件まで追加し、それぞれを独立して materialize する。**各レシピの直近成功した active chunk_set はすべて検索対象**とし、hybrid RRF + rerank で融合する。回答に渡す根拠の選択（`rag_engine` の `evidence_spans`）では、本文が同じ根拠に加えて、同じ文書で解析の要素の ID（`element_ids`。親子分割の親は子の `source_record_refs`）が小さい側の 8 割以上重なり、短い側の本文の 9 割以上がもう一方に含まれる根拠を 1 つにまとめる（source-span 重複除去。順位の高い位置に範囲の広いほうを残し、まとめた chunk_id は `source_aliases` に残す。要素の ID の無い分割は本文が同じものだけをまとめる。#1331）。主レシピ、候補、配信中、昇格の概念は持たず、配信モード（`single / fused / routed`）の設定も持たない。設定変更後の再処理中や再処理失敗時も、直前の active chunk_set を検索対象として維持する。KB membership を変えてもレシピ集合や chunk_set は変わらない。

レシピ追加・削除は親文書行をロックして **最少 1 件・最大 3 件**を保証する。活動中ジョブのあるレシピは編集・削除できない。成功時だけ新 chunk_set を active に原子切替し、失敗時は他レシピと旧 active 出力を変更しない。

GraphRAG、navigation summary、field extraction が planning のみで実 materialize 未完の場合は、UI / API diagnostics にその状態を表示する。
