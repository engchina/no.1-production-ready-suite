# 検索・回答フロー高度戦略の実装計画(段階導入)

> 本ドキュメントは、処理フローの内部基盤(`rag_pipeline_core` + `services/pipeline/*`)の上に
> 追加する **実行配線が重い高度な検索・回答方式** の設計と段階導入計画をまとめる。いずれも確定スタック
> (OCI Enterprise AI / OCI Generative AI Cohere / Oracle 26ai)を不変とし、外部ベクトル DB・別
> LLM provider は導入しない。GPU / 版管理 schema DDL / 実 Oracle を要するものは **本リポジトリの
> CI(GPU・実 DB なし)では検証不能** のため、scaffold(safe-degrade)→ 実環境配線 → 検証の順で導入する。

最終更新: 2026-06-20

## 現在の状態(scaffold 済 / 未配線)

| 戦略 | 種別 | 現状 | 既定挙動 |
|---|---|---|---|
| `reasoning_tree_search` | 検索方式(PageIndex 型) | 戦略として**選択可能**(`rag_retrieval_strategy`)。`pending_execution=True` | `strategy_bias=None` のため **hybrid 検索へ安全縮退** |
| `colpali_visual_retrieval` | 検索方式(ColPali 型) | 同上 | 同上(hybrid 縮退) |
| `self_reflective`(Self-RAG) | 回答スタイル | 未着手 | — |
| Temporal GraphRAG 検索時フィルタ | 検索/関係情報 | フラグ `rag_graph_temporal_enabled` のみ存在 | 構築側 timestamp 付与のみ |
| RAPTOR 検索時昇格 | 検索/根拠確認 | 取込側で summary node を索引済 | 通常検索が summary node にヒット |

未配線時の安全縮退は `app/rag/retrieval_strategy.py::resolve_retrieval_strategy` が未対応 strategy を
`SearchStrategy.HYBRID` へ落とす既存挙動を利用する。高度な診断では `retrieval_strategy_adapter` と
`runtime_retrieval_strategy=hybrid` の差分で「縮退中」を判別できる。

---

## 1. reasoning_tree_search(PageIndex 型 vectorless 推論検索)

### 目的
cosine 類似度ではなく **LLM が章節 tree を navigation** して関連 section を選ぶ。専門文書(金融・
法律・技術マニュアル)で、検索経路が監査可能(どの section を展開/スキップ/命中したか)になる。

### 設計
- **tree 構築(取込時 or 検索時キャッシュ)**: 既存の `DocumentElement.section_path` /
  `parent_id` 階層から、文書ごとに `section tree`(node = {title, summary, page_range,
  child_ids})を構築。要約は OCI Enterprise AI(`hierarchical_parent_child` / RAPTOR と共用可)。
  Oracle 26ai に `rag_document_nav_tree`(または既存 `navigation` JSON、`app/rag/navigation.py`)を
  再利用して node を永続化。
- **検索時 navigation**: OCI Enterprise AI に「query + 現在 node の title/summary 群」を渡し、各 node
  で yes/no(展開/スキップ)を JSON で判断 → 命中 leaf の chunk を Oracle から取得。`SearchDiagnostics`
  に `tree_search_path`(踏破 node 列)を追加して監査可能にする。
- **融合**: 既存 hybrid_rrf と RRF 融合可能(tree hit を 1 経路として)。
- **opt-in / コスト**: query ごとに複数 LLM 呼び出し。`rag_retrieval_reasoning_tree_*`(深さ・幅
  上限)を設け、失敗/未設定時は hybrid へ縮退。

### 段階
1. (済)戦略登録 + hybrid 縮退。
2. `app/rag/reasoning_tree.py`: 既存 navigation tree から検索時 navigation(injectable LLM、決定論
   部分を unit test)。`_retrieve_with_strategy` に経路追加。
3. `SearchDiagnostics.tree_search_path` + 設定 + 専用テスト(LLM stub)。
4. 実 Oracle/OCI 結合検証(staging)。

---

## 2. colpali_visual_retrieval(VLM late-interaction 視覚検索)

### 目的
ページ画像から **OCR を介さず直接検索**。複雑レイアウト(表・図・多欄)・スキャン PDF の検索精度を
上げる。視覚特徴(multi-vector / late interaction)で query とページをマッチング。

### 設計(要 GPU + 版管理 schema DDL)
- **取込時**: 既存 `pdf_to_page_images` 前処理でページ画像化 → **GPU サービス** `services/parsers`
  または新 `services/pipeline/colpali`(ColQwen/ColPali を transformers でロード)で **multi-vector
  embedding** を生成。OCI Enterprise AI VLM 経路でも近似可能だが multi-vector が要点。
- **索引(schema 変更)**: Oracle 26ai に視覚 embedding 列/表(`rag_page_visual_vectors`、
  `VECTOR` 複数 or per-patch 行)を追加。**版管理された schema DDL artifact の変更が必要**
  (`requires_reprovision`、自動変更しない)。
- **検索時**: query を同モデルで embedding 化し、**late interaction(MaxSim)** スコアで page を
  ランク。text hybrid と RRF 融合(visual score + text score)。
- **opt-in / コスト**: GPU 常時必要。`rag_retrieval_colpali_*` で有効化、未配線/未設定は hybrid 縮退。

### 段階
1. (済)戦略登録 + hybrid 縮退。
2. `services/pipeline/colpali`(GPU、`--profile gpu`)= multi-vector embedding サービス + 契約。
3. schema DDL artifact に視覚ベクトル表を追加(版管理・reprovision 手順込み)。
4. `_retrieve_with_strategy` に late-interaction 経路 + RRF 融合。GPU host で結合検証。

### 注意
- `ExtractionMetadataValue`(scalar)/ 確定スタックは不変。multi-vector は別表で持つ。
- CI は GPU 非搭載のため、remap/契約/縮退を fixture で検証し、実 GPU は手動/staging 検証。

---

## 3. 残りの高度戦略(設計メモ)

- **self_reflective(Self-RAG)**: generation profile。OCI Enterprise AI が
  `{"answer":..., "confidence":0-1, "grounded_in_context":bool}` を出力。`confidence<閾値` or
  `grounded_in_context==false` で grounding と連携し **1 回だけ再検索**(CRAG と同じ corrective
  machinery を再利用)。generation_adapter に profile 追加 + pipeline で reflection パース。
- **Temporal GraphRAG 検索時フィルタ**: build 側 timestamp(`rag_graph_temporal_enabled`)に加え、
  検索時に query の時間文脈(「最新の」「2024 年時点」)を抽出し Oracle の `valid_from/valid_to`
  条件でフィルタ。`graph_augmented` 経路に時間条件を足す。
- **RAPTOR 検索時昇格**: 既に summary node を索引済。`grounding` の dependency promotion と同様に、
  leaf hit 時に対応する summary node を citation context へ昇格する経路を追加(opt-in)。

---

## 検証方針(共通)

- 決定論部分(tree navigation 判定の集約、late-interaction スコアの数値、reflection パース)は
  injectable + unit test で CI 緑にする。
- LLM/VLM/GPU/Oracle を要する実行は **staging(実 OCI/Oracle/GPU host)** で結合検証し、
  file-processing / retrieval staging gate(`docs/evaluation`)に指標(retrieval recall / table QA /
  page hit / tree path coverage)を合流させる。
- すべて opt-in・未配線時は hybrid/既存挙動へ安全縮退し、既定の挙動・レイテンシを変えない。
