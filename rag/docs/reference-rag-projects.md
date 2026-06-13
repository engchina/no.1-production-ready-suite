# 参考 RAG プロジェクト一覧

> 本ドキュメントは **no.1-production-ready-rag が参照する外部 OSS / 研究プロジェクトのカタログ** です。
> 目的は「各プロジェクトの優れた点を抽出し、本プロジェクトに統合する」こと。
> 技術選定の正本は [AGENTS.md](../AGENTS.md)。本一覧はあくまで **着想・ベストプラクティスの調査源**であり、
> 採用する場合も本プロジェクトの確定スタック(OCI Enterprise AI / OCI Generative AI Cohere / Oracle 26ai / Next.js)に
> 合わせて再実装する。外部ベクトル DB・別 LLM プロバイダをそのまま導入しないこと(逸脱時は AGENTS.md §8 に従い要確認)。

最終更新: 2026-06-14

---

## 1. 優先 POC 候補:OSS RAG プロダクト / プラットフォーム

| プロジェクト | 種別 | 適合シナリオ | 評価メモ |
|---|---|---|---|
| **Dify** | 企業向け LLM アプリ / RAG / Agent プラットフォーム | 企業ナレッジベース、ワークフロー、Agent、可視化オーケストレーション | プロダクト化が最も進んだものの一つ。RAG パイプライン・workflow・Agent・モデル管理・可観測性が一通り揃う。Knowledge Pipeline でデータソース→抽出→処理→ナレッジ保管の企業 RAG 運用チェーンを強化中。 |
| **RAGFlow** | RAG 特化のプロダクト級エンジン | PDF・Office・複雑フォーマット文書、引用付き QA | 「専門 RAG エンジン」寄り。deep document understanding・複雑フォーマット解析・引用トレーサビリティを重視。文書品質要求が高い場面向け。 |
| **AnythingLLM** | ローカル / プライベート RAG プロダクト | 個人・チーム・私有ナレッジベース | 導入が速い。ローカル/クラウド LLM、文書取込、マルチユーザ、Agent、ベクトル DB、文書パイプライン対応。 |
| **Open WebUI** | セルフホスト AI ポータル + RAG | Ollama、ローカルモデル、OpenAI 互換 API ポータル | 純 RAG ではないが、企業/チームのセルフホスト AI 入口として強力。RAG inference engine 内蔵。統一チャット入口向け。 |
| **FastGPT** | ナレッジベース + RAG + 可視化ワークフロー | 中国語企業ナレッジ、カスタマーサポート、社内 QA | データ処理・RAG 検索・visual workflow・hybrid search・rerank・OpenAPI 内蔵。**SaaS 形態へのライセンス制限に注意**。 |
| **MaxKB** | RAG + Workflow + MCP プラットフォーム | サポート、社内ナレッジ、研究/教育 | 文書/Web/テキスト取込、チャンク分割、ベクトル化、ワークフロー、MCP ツール呼び出しに対応。 |
| **QAnything** | ナレッジベース QA システム | 中国語文書 QA、多フォーマットナレッジ | Qwen 生態系ベース。PDF・Word・PPT・Markdown・メール・画像・Web など多形式対応。 |
| **Kotaemon** | 文書チャット RAG UI | 軽量文書 QA、UI の二次開発 | "clean & customizable RAG UI" という明快な立ち位置。"chat with documents" の素早い構築向け。 |
| **PrivateGPT** | ローカル/プライベート RAG | オフライン、データを外に出さない文書 QA | documents fully offline / no data leaves machine を強調。セキュリティ・プライバシー優先。 |
| **DocsGPT** | プライベート AI / 文書 Agent プラットフォーム | 文書アシスタント、私有デプロイ | 「文書 AI プラットフォーム」寄り。ローカル/クラウド API/ローカル推論の各デプロイ対応。 |
| **R2R** | RAG バックエンド / API プラットフォーム | REST API・ハイブリッド検索・ナレッジグラフ・文書管理が必要な場合 | 開発者がバックエンド能力を組む用途に最適。multimodal ingestion・hybrid search・knowledge graphs・document management 提供。 |
| **DB-GPT** | DB / データ資産 Agent + RAG | DB 問い合わせ、BI、DWH、CSV/Excel QA | 「データ + RAG + Agent」シナリオ向け。DB・CSV・Excel・DWH・ナレッジベースを接続。 |

## 2. 重点トラッキング:GraphRAG / マルチモーダル / Agentic RAG

| プロジェクト | 種別 | 着目理由 |
|---|---|---|
| **Microsoft GraphRAG** | GraphRAG フレームワーク | LLM で非構造化テキストからナレッジグラフ・community summaries を構築し私有データ QA を強化。GraphRAG 方向の代表格。 |
| **LightRAG** | 軽量 GraphRAG | graph + vector の二層検索、増分更新対応。研究・二次開発向け。 |
| **RAG-Anything** (HKUDS) | マルチモーダル RAG | LightRAG ベース。text/image/table/equation/chart など多モーダル文書対応。論文・財務帳票・複雑文書向け。**重点トラッキング**。 |
| **PageIndex** (VectifyAI) | Vectorless / 推論ベース RAG | no vector DB・no chunking・file-level tree・reasoning-based retrieval。「次世代インデックス範式」として要注目。 |
| **Understand-Anything** (Egonex-AI) | コード/文書ナレッジグラフ | コードベース・文書をインタラクティブな knowledge graph 化。コード理解・アーキ QA・agent memory 向け。 |
| **Graphiti** (getzep) | リアルタイムナレッジグラフ / Agent Memory | AI Agent 向け temporal knowledge graph。事実の更新/失効、vector + 全文 + グラフ走査検索。 |
| **Cognee** (topoteretes) | Agent memory + KG + vector | vector embeddings・graph reasoning・ontology generation を統合。長期記憶・企業 Agent コンテキスト層向け。 |
| **OpenSPG / KAG** | 知識増強生成 | ナレッジグラフ増強・論理推論・専門領域 QA 寄り。高信頼・強構造化業務向け。 |
| **Neo4j GraphRAG** | Neo4j 公式 GraphRAG パッケージ | 既に Neo4j を使う企業向けの公式ルート。グラフ DB 駆動の RAG。 |
| **UR2** (Tsinghua-dhy) | RAG + 強化学習 研究 | ACL 2026 ORAL。検索と推論を RL で動的協調。プロダクトではないが研究価値高。 |
| **agentic-rag-for-dummies** | Agentic RAG 教材 / 雛形 | LangGraph・memory・human-in-the-loop・query clarification・self-correction・RAGAS・Langfuse を網羅。アーキ参考価値高。 |
| **ai-agents-for-beginners** (Microsoft) | Agent 教材 | Agent 入門。Agentic RAG の章を含む。チーム学習向け。 |

## 3. 自社 RAG 構築で避けて通れないフレームワーク / コンポーネント

| プロジェクト | 種別 | 価値 |
|---|---|---|
| **LlamaIndex** | RAG / document agent フレームワーク | データ接続・索引・document agent・解析・抽出・RAG に強い。複雑ナレッジベースのバックエンド向け。 |
| **LangChain / LangGraph** | Agent / LLM アプリフレームワーク | LangChain は LLM アプリ部品の合成、LangGraph は長期稼働・状態付き・復元可能・human-in-the-loop な Agent 向け。 |
| **Haystack** (deepset) | プロダクション級 LLM/RAG オーケストレーション | 工学的パイプライン寄り。retrieval/routing/memory/generation の制御要求が高いバックエンド向け。 |
| **txtai** | Embeddings / semantic search / workflow | 意味検索・LLM orchestration・workflow を網羅。text/文書/音声/画像/動画の embedding パイプライン対応。 |
| **llmware** | 企業 RAG パイプライン | 企業文書の解析・取込・ナレッジ構築・小型モデル・ローカルデプロイに注力。 |
| **Pathway LLM App** | リアルタイムデータ RAG | live data / always-up-to-date RAG 向け。データソースが継続変化する企業検索に。 |
| **Ragbits** (deepsense-ai) | RAG building blocks | 20+ フォーマット取込、Docling/Unstructured、カスタム parser、表/画像/構造化コンテンツ、Ray 並列処理。 |
| **Cognita** (truefoundry) | 実験・デプロイ可能な RAG フレームワーク | 異なる RAG 構成のデバッグと結果観察 UI 提供。プラットフォーム/ML 工学チーム向け。 |
| **Canopy** (Pinecone) | Pinecone RAG フレームワーク | Pinecone 採用済みチーム向け。今後の更新頻度は要評価。 |

## 4. 文書解析・取込・評価:RAG の成否を分ける基盤

| プロジェクト | 種別 | 重要性 |
|---|---|---|
| **Docling** (IBM) | 文書解析 | PDF 等多形式を解析。企業 RAG ingestion パイプラインへの組込みに適す。 |
| **MinerU** (opendatalab) | PDF/Office/画像 → Markdown/JSON | 高精度文書抽出。複雑 PDF・スキャン・Office を検索可能な構造に変換。 |
| **Marker** (datalab-to) | 文書 → Markdown/JSON/chunks | PDF・画像・PPTX・DOCX・XLSX・HTML・EPUB 対応。表・数式・コードも処理。 |
| **Unstructured** | 汎用文書前処理 | 企業 RAG の定番 ingestion 部品。PDF/HTML/Word/画像/テキストの非構造化処理。 |
| **Ragas** | RAG 評価 | OSS の LLM アプリ評価フレームワーク。指標・合成テストデータ・品質監視。RAG 評価の定番。 |
| **AutoRAG** (Marker-Inc-Korea) | RAG パイプライン自動最適化 | モジュール組合せを自動評価し、より良い RAG パイプラインを探索。 |
| **FlashRAG** (RUC-NLPIR) | RAG 研究再現ツールキット | RAG アルゴリズム再現・研究向け。多データセット・多アルゴリズム・多 reasoning 手法。 |

## 5. 個別ピックアップ(立ち位置の整理)

| プロジェクト | 注目度 | 立ち位置 |
|---|---|---|
| **agentic-rag-for-dummies** | 高 | Agentic RAG の学習/雛形。プロダクトではないがアーキ参考価値高。 |
| **microsoft/ai-agents-for-beginners** | 高 | Agent 教材。チーム研修向け。RAG プロダクトではない。 |
| **PageIndex** | 非常に高 | 新型 vectorless / 推論式インデックス。重点トラッキング対象。 |
| **Dify Knowledge Pipeline** | 非常に高 | Dify が「アプリ基盤」から「企業級 RAG データガバナンス/運用パイプライン」へ進化中であることを示す。 |
| **UR2** | 中〜高 | RAG + RL 研究方向。論文/実験価値高、プロダクト成熟度はまだ早期。 |
| **Understand-Anything** | 高 | コード/ナレッジのグラフ化。codebase RAG・architecture Q&A・agent memory 向け。 |
| **nashsu/llm_wiki** | 高 | ローカルデスクトップナレッジベース。文書を永続 wiki に蓄積(都度 retrieve-and-answer ではない)。 |
| **nvk/llm-wiki** | 中 | Agent 研究/ナレッジ編纂ツール。Claude/Codex/OpenCode 等の Agent プラグイン流向け。 |
| **engchina/No.1-RAG** | シナリオ型 | **Oracle/OCI 方向のサンプル**。コミュニティシグナルは弱いが、本プロジェクトの OCI シナリオに参考価値あり。 |
| **hkuds/rag-anything** | 非常に高 | マルチモーダル RAG の重点プロジェクト。長期トラッキング推奨。 |
| **oceanbase/powerrag** | シナリオ型 | RAGFlow ベースの OceanBase 強化版。DB/ハイブリッド検索シナリオ向け、まだ早期。 |

## 6. 用途別おすすめ短縮リスト

- **企業ナレッジベースのプロダクト化**: Dify / RAGFlow / AnythingLLM / Open WebUI / FastGPT / MaxKB / QAnything / R2R
- **高品質・複雑文書 RAG**: RAGFlow / Dify Knowledge Pipeline / RAG-Anything / Docling / MinerU / Marker / Unstructured
- **GraphRAG / ナレッジグラフ増強**: Microsoft GraphRAG / LightRAG / RAG-Anything / KAG / Graphiti / Cognee / Neo4j GraphRAG / Understand-Anything
- **Agentic RAG**: LangGraph / LlamaIndex / agentic-rag-for-dummies / LangChain / Graphiti / Cognee
- **評価・チューニング**: Ragas / AutoRAG / FlashRAG
- **次世代 RAG 方向の研究**: PageIndex / UR2 / RAG-Anything / Graphiti / LightRAG / Understand-Anything

## 7. リスク・法務メモ

- **Flowise**: ローコード生態系は強いが、深刻な脆弱性が悪用された報道あり。公開デプロイ時は**必ず最新化し隔離**する。
- **Dify / Open WebUI / FastGPT** などソース利用可能プロジェクトは、**ライセンス/ブランド/商用制限**を企業商用前に法務確認する。
- 本プロジェクトへ取り込む際は、確定スタック(OCI Enterprise AI / OCI Generative AI Cohere Embed v4・Rerank v4 fast / Oracle 26ai AI Vector Search / Next.js)に**必ず再マッピング**する。外部ベクトル DB・別 LLM プロバイダの直接導入は AGENTS.md §3・§8 に反するため不可(逸脱時は理由を添えて要確認)。

## 8. リンク集

- Dify — https://github.com/langgenius/dify
- RAGFlow — https://github.com/infiniflow/ragflow
- AnythingLLM — https://github.com/Mintplex-Labs/anything-llm
- Open WebUI — https://github.com/open-webui/open-webui
- FastGPT — https://github.com/labring/FastGPT
- MaxKB — https://github.com/1panel-dev/MaxKB
- QAnything — https://github.com/netease-youdao/qanything
- Kotaemon — https://github.com/Cinnamon/kotaemon
- PrivateGPT — https://github.com/zylon-ai/private-gpt
- DocsGPT — https://github.com/arc53/docsgpt
- R2R — https://github.com/SciPhi-AI/R2R
- DB-GPT — https://github.com/eosphoros-ai/DB-GPT
- Microsoft GraphRAG — https://github.com/microsoft/graphrag
- LightRAG — https://github.com/HKUDS/LightRAG
- RAG-Anything — https://github.com/hkuds/rag-anything
- PageIndex — https://github.com/VectifyAI/PageIndex
- Understand-Anything — https://github.com/Egonex-AI/Understand-Anything
- Graphiti — https://github.com/getzep/graphiti
- Cognee — https://github.com/topoteretes/cognee
- OpenSPG / KAG — https://github.com/OpenSPG/kag
- Neo4j GraphRAG — https://github.com/neo4j/neo4j-graphrag-python
- UR2 — https://github.com/Tsinghua-dhy/UR2
- agentic-rag-for-dummies — https://github.com/GiovanniPasq/agentic-rag-for-dummies
- ai-agents-for-beginners — https://github.com/microsoft/ai-agents-for-beginners
- LlamaIndex — https://github.com/run-llama/llama_index
- LangChain — https://github.com/langchain-ai/langchain
- Haystack — https://github.com/deepset-ai/haystack
- txtai — https://github.com/neuml/txtai
- llmware — https://github.com/llmware-ai/llmware
- Pathway LLM App — https://github.com/pathwaycom/llm-app
- Ragbits — https://github.com/deepsense-ai/ragbits
- Cognita — https://github.com/truefoundry/cognita
- Canopy — https://github.com/pinecone-io/canopy
- Docling — https://github.com/docling-project/docling
- MinerU — https://github.com/opendatalab/MinerU
- Marker — https://github.com/datalab-to/marker
- Unstructured — https://github.com/Unstructured-IO/unstructured
- Ragas — https://www.ragas.io/
- AutoRAG — https://github.com/Marker-Inc-Korea/AutoRAG
- FlashRAG — https://github.com/RUC-NLPIR/FlashRAG
- nashsu/llm_wiki — https://github.com/nashsu/llm_wiki
- nvk/llm-wiki — https://github.com/nvk/llm-wiki
- engchina/No.1-RAG — https://github.com/engchina/No.1-RAG
- oceanbase/powerrag — https://github.com/oceanbase/powerrag
- Flowise — https://github.com/FlowiseAI/Flowise
- Dify Knowledge Pipeline 解説 — https://langgenius.co.jp/article/post/dify-knowledge-pipeline/
