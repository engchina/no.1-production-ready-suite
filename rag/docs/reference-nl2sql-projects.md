# 参考 NL2SQL プロジェクト一覧

> 本ドキュメントは **Production Ready NL2SQL が参照する外部 OSS / 研究プロジェクト / Oracle ネイティブ機能のカタログ** です。
> 目的は「各プロジェクトの優れた点を抽出し、本プロジェクトに統合する」こと。
> 技術選定の正本は [AGENTS.md](../AGENTS.md)。本一覧はあくまで **着想・ベストプラクティスの調査源**であり、
> 採用する場合も本プロジェクトの確定スタック(**Oracle Select AI / Select AI Agent** = `DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT` を NL2SQL の中核、
> LLM=OCI Enterprise AI(Select AI から `oci_endpoint_id` で参照)、embedding/rerank=OCI Generative AI Cohere、
> 知識/例示ストア=Oracle 26ai AI Vector Search、フロント=Vite + React Router)に合わせて再実装する。
> **外部ベクトル DB・別 LLM プロバイダ・別 NL2SQL SaaS をそのまま導入しないこと**(逸脱時は AGENTS.md §コーディング規約 に従い理由を添えて要確認)。

最終更新: 2026-06-20(改訂2: sql-assist 精読 + 2025–2026 本番ハードニング知見を反映)

---

## 0. 自社参照実装(最優先・正本に近い設計源)

本プロジェクトは engchina 配下の 2 つの既存実装の **Select AI / Select AI Agent 連携**を中核設計源とする。
両者とも Oracle Autonomous Database の `DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT` で NL→SQL を **DB 内**で実行している。

| プロジェクト | 種別 | 取り込む設計 |
|---|---|---|
| **engchina/No.1-SQL-Assist** | SQL 生成補助 + SQL 学習 (Gradio) | ① **Select AI Agent 実行 UI**(`DBMS_CLOUD_AI_AGENT.RUN_TEAM(team_name, prompt)` を呼び、応答 JSON の `reply`/`content` を抽出して表示)。② **会話履歴**(`USER_CLOUD_AI_CONVERSATION_PROMPTS` を `TEAM_NAME` で絞り表示)。③ **権限チェック**(`DBMS_CLOUD_AI_AGENT` の EXECUTE 権限を anonymous block で検証)。④ **Few-shot 学習**(例示 SQL を「絶対的に複製せよ」という強い system prompt で固定し、純 SQL のみ出力)。⑤ **SQL 構造分析**(SELECT/FROM/JOIN/WHERE/GROUP BY/HAVING/ORDER BY/CTE を Markdown へ分解→再構築可能にする)。⑥ **分類器ベースの自動 profile ルーティング**(質問を OCI Cohere `embed-v4.0` で埋め込み → 学習済 `LogisticRegression`(joblib)で domain を予測 → `domain→profile` マップ → `DBMS_CLOUD_AI.SET_PROFILE` で profile を自動切替。**追加 LLM なし・決定論**)。⑦ **SQL 学習タブ**(部門/社員/プロジェクトの日本語業務スキーマで SELECT をステップ学習)。⑧ **Select AI 呼び出しの明示パターン**:`SET_PROFILE(profile_name)` の後 `SELECT DBMS_CLOUD_AI.GENERATE(prompt => :q, profile_name => :name, action => :a) FROM DUAL`(`action` ∈ `showsql`/`runsql`/`narrate`/`explainsql`/`chat`)。⑨ **profile の二重永続**(DB の `CREATE_PROFILE` と JSON ミラー: `object_list` / schema text / context DDL をオフライン保持し few-shot 文脈に再利用)。⑩ **合成データ生成**(Select AI で表ごとにサンプル行を生成し、評価/デモ/few-shot bootstrap に利用)。 |
| **engchina/no.1-denpyo-toroku-kun**(お任せ!伝ぴょん) | 伝票登録 + Select AI 自然言語検索 (Flask/Gunicorn + Oracle JET) | ① **カテゴリ単位の Select AI 資産プロビジョニング**:`credential → profile → tool(SQL) → agent → task → team` を **決定論ハッシュ命名**(prefix + SHA1 先頭 12 桁、30 byte 上限)で冪等に drop+create。② **profile attributes**:`provider=oci` / `credential_name` / `model` / `region` / `oci_compartment_id` / `object_list`(許可テーブル群) / `enforce_object_list` / `annotations` / `comments` / `constraints` / `embedding_model` / `oci_endpoint_id` / `max_tokens` / `oci_apiformat`。③ **tool**=`{tool_type: SQL, tool_params:{profile_name}}`、**agent**=`{profile_name, role}`、**task**=`{instruction(日本語応答指定), tools:[tool], enable_human_tool:false}`、**team**=`{agents:[{name, task}], process:sequential}`。④ **会話**=`DBMS_CLOUD_AI.CREATE_CONVERSATION` で `conversation_id` を発行しマルチターン。⑤ **drift 検知**:`SELECT_AI_CONFIG_HASH` / `SELECT_AI_READY` / `SELECT_AI_SYNCED_AT` / `SELECT_AI_LAST_ERROR` をカテゴリ表に保持し、設定変更時のみ再構築。⑥ **region/model fallback**(xAI Grok 非対応 region は代替モデルへ)。⑦ **rate limit 制御**(slot 予約 + backoff)。⑧ **prompt のカスタマイズ**(`prompt_settings.json` で上書き、未設定は default fallback)。 |

> **本プロジェクトへの再マップ**: 上記 ①〜⑧ は、本プロジェクトでは **Select AI プロビジョニングサービス**(KB/業務ドメイン単位で credential/profile/tool/agent/task/team を冪等管理、config_hash で drift 検知)と **NL2SQL パイプラインアダプター群**(下記 §6)へ再マップする。`model`/`oci_endpoint_id` は **OCI Enterprise AI / 専用エンドポイント**を指し(LLM=Enterprise AI 方針を維持)、`embedding_model` と知識ストアは **OCI GenAI Cohere + Oracle 26ai AI Vector Search** を使う。Gradio / Oracle JET の UI は採用せず、共有 platform の **Vite + React Router + shadcn/ui** へ作り直す。

---

## 1. 優先 POC 候補:OSS NL2SQL プロダクト / プラットフォーム

| プロジェクト | 種別 | 適合シナリオ | 評価メモ |
|---|---|---|---|
| **Vanna AI** | RAG ベース Text-to-SQL ライブラリ (MIT) | 自社アプリへ NL2SQL を埋め込む、学習で精度を上げる | 「**train→ask**」の RAG 学習が核。DDL・doc・過去の正解 SQL を embedding 化してベクトル検索で文脈注入し、LLM に SQL を書かせる。**本プロジェクトの Knowledge/Few-shot アダプターの直接の手本**(ベクトルストアは Oracle 26ai、embedding は OCI Cohere に再マップ)。自動可視化・Web UI 同梱。 |
| **Wren AI** (Canner) | GenBI プラットフォーム / セマンティックレイヤ | 全社データアクセス、ガバナンス、BI | **セマンティックモデル(MDL)中心**。メタデータのみのセキュリティ、用語/計算指標の定義、フィードバックループで継続学習。「単なる Text-to-SQL ではなく Generative BI」。**業務アシスタント層と用語集・指標定義の設計参考**。 |
| **DB-GPT / DB-GPT-Hub** (eosphoros-ai) | データ Agent + NL2SQL フレームワーク | マルチエージェント、ワークフロー、FT データ | マルチエージェント・ワークフロー言語・NL2SQL FT データセット(DB-GPT-Hub)を提供。Agentic アダプター(decompose/multi_hop)の設計参考。 |
| **Dataherald** | エンタープライズ NL→SQL エンジン / API | DB に自然言語 QA API を生やす | DB から「英語で質問できる API」を構築。コンテキスト/ゴールデン SQL ストア・admin・微調整の運用設計が参考。 |
| **NL2SQL Studio** (GoogleCloudPlatform) | 本番 NL2SQL パイプライン構築ツールキット | 開発者/データサイエンティスト向けの実験〜本番化 | プロンプト手法(Linear/RAG/Chain-of-Thought)を切替えて比較できる構成。**パイプラインアダプターを「切替可能な戦略」として持つ思想**の参考。 |
| **Squrve** | 軽量 NL2SQL フレームワーク | 複雑 DB 上での NL→SQL を素早く | 軽量さ重視。schema linking とパイプライン部品化の参考。 |
| **Defog SQLCoder** | NL2SQL 特化 OSS モデル | ローカル/自前ホストの SQL 生成モデル | 「SQL 生成に特化したモデル」という選択肢。本プロジェクトは Enterprise AI を使うが、**self-host モデルを oci_endpoint_id 越しに使う場合の比較材料**。 |
| **DB-GPT-Web / Chat2DB** | NL2SQL 付き DB クライアント | 開発者の日常 DB 操作 + AI 補助 | NL2SQL を IDE/クライアントに統合する UX(SQL 補完・説明・最適化)の参考。 |
| **PremSQL** | ローカル Text-to-SQL パイプライン (end-to-end) | オンプレ/データを外に出さない NL2SQL | データセット・モデル・executor・自己修正を一通り含む。**自己修正(execute→repair)ループの設計参考**。 |

---

## 2. 重点トラッキング:マルチエージェント / Agentic NL2SQL(研究フロンティア)

> BIRD / Spider 2.0 上位はほぼ **マルチエージェント + schema linking + self-correction + 候補選択**の組合せ。
> 本プロジェクトでは **外部フレームワークをそのまま導入せず**、Select AI Agent の team(agent/task/tool)と
> アプリ側 Enterprise AI オーケストレーションへ「決定論的に再マップ」する。

| プロジェクト / 論文 | 種別 | 着目理由(本プロジェクトへの取り込み) |
|---|---|---|
| **MAC-SQL** (Multi-Agent Collaborative) | Selector / Decomposer / Refiner | スキーマ選択→質問分解(CoT)→SQL 修正の 3 役割分担。**Agentic アダプター(decompose)と Self-Correction アダプターの原型**。Select AI Agent の sequential team で再現。 |
| **DIN-SQL** | 分解 in-context learning | 難易度分類→分解→自己修正のプロンプト連鎖。few-shot/分解設計の参考。 |
| **CHESS** (Contextual Harnessing) | Information Retriever + Schema Selector | 大規模スキーマから関連要素だけを抽出して効率化。**Schema Linking アダプター(auto_prune)の手本**。 |
| **CHASE-SQL** | 候補生成 + 選択 | 複数候補を生成し選別。候補選択(self-consistency)の参考。 |
| **XiYan-SQL** | マルチ生成器アンサンブル + M-Schema | ICL+SFT 併用、**M-Schema 表現**で DB 構造を LLM へ渡す、候補から選択。**スキーマ表現フォーマットの参考**。 |
| **MCS-SQL** | 複数プロンプト + 多肢選択 | 複数プロンプト/候補から多肢選択で最終 SQL を決める。 |
| **ReFoRCE** | Agentic(DB 圧縮→自己精緻化→合意→列探索) | Spider 2.0 で SOTA 級。**enterprise 規模(~800 列)スキーマへの対処**(圧縮・列探索)が参考。 |
| **DSR-SQL** | Dual-State Reasoning | adaptive context + progressive generation。文脈適応の参考。 |
| **AutoLink** | 自律的スキーマ探索/拡張 | 大規模スキーマでの schema linking スケーリング。 |
| **E-SQL / X-SQL** | 質問強化型スキーマリンク | 質問をスキーマ語彙で richening してリンク精度を上げる。**注釈/用語集を質問へ注入する設計の参考**。 |
| **SteinerSQL** | グラフ誘導の数理推論 | JOIN 経路をグラフ問題として最短化。複雑 JOIN の参考。 |
| **SQLFixAgent / CSC-SQL** | 一貫性強化マルチエージェント / self-correction | 実行誤りを修正する consistency + correction。**Self-Correction アダプター(verified)の手本**。 |
| **LitE-SQL** | 軽量・ベクトルスキーマリンク + 実行誘導自己修正 | 軽量実装で schema linking と self-correction を両立。**本プロジェクトの「外部依存なし・決定論」志向に最も近い**。 |
| **LinkAlign** (EMNLP 2025) | スケーラブルなスキーマリンク(大規模・複数 DB) | スキーマを**意味ユニットに分解した多段リトリーバル**で大規模スキーマでも関連テーブル/列を絞る。**Schema Linking アダプター(auto_prune)を Oracle 26ai ベクトル検索で実装する手本**。 |
| **EllieSQL** | コスト効率・複雑度認識ルーティング | 質問の複雑度を推定し、簡単なら安価な単段、難しければ高価な多段へ**ルーティング**してコストを抑える。**Router アダプター(complexity_aware)+ sql-assist の分類器ルーティングと統合**。 |
| **AmbiSQL** | 曖昧性検出と対話的解決(human-in-the-loop) | NL の曖昧さを taxonomy で検出し、**確認質問**で意図を確定してから生成。**Clarify ゲート(2段ゲート哲学と整合)の手本**。 |
| **GBV-SQL** | SQL2Text 逆翻訳による意味検証 | 生成 SQL を NL へ**逆翻訳**して元の質問と突合し、誤った JOIN/列を検出・修正。**Guardrail/Self-Correction の `semantic_verify`(Select AI `explainsql`/`narrate` で実装、追加 provider 不要)**。 |

> **本プロジェクトへの再マップ**: schema linking 系(CHESS/E-SQL/AutoLink)は **Schema Linking アダプター**、分解/multi-hop 系(MAC-SQL/DIN-SQL/DB-GPT)は **Agentic アダプター**、自己修正系(SQLFixAgent/CSC-SQL/LitE-SQL)は **Self-Correction アダプター**、候補選択(CHASE-SQL/MCS-SQL/XiYan)は **Generation アダプターの候補生成オプション**へ集約する。実行は Oracle Select AI Agent team(`process: sequential`)とアプリ側 Enterprise AI の合成で、**外部グラフ DB / 別 LLM provider は導入しない**。

---

## 3. Oracle ネイティブ機能(中核・確定スタックの本体)

| 機能 | 用途 | 本プロジェクトでの位置づけ |
|---|---|---|
| **Select AI** (`DBMS_CLOUD_AI`) | NL→SQL 生成・実行・説明、RAG、チャット | NL2SQL の**中核エンジン**。`profile`(provider/credential/model/object_list/enforce_object_list/annotations/comments/constraints)を作り、`SELECT AI <action> '<prompt>'` で **`showsql` / `runsql` / `narrate` / `explainsql` / `chat`** を実行。**Generation アダプターの本体**。 |
| **Select AI Agent** (`DBMS_CLOUD_AI_AGENT`) | エージェント・チームによる多段 NL2SQL | `tool(SQL)` / `agent` / `task` / `team` を組み、`RUN_TEAM(team_name, prompt)` で分解・複数ステップ実行。会話は `CREATE_CONVERSATION`。**Agentic アダプター(decompose/multi_hop)の本体**。 |
| **Select AI RAG**(vector store profile) | スキーマ/ドキュメントを文脈に NL2SQL | 例示・用語集・スキーマ doc を Oracle 26ai のベクトルストアに置き、profile から参照。**Knowledge/Few-shot アダプターと統合**(embedding=OCI Cohere)。 |
| **Oracle 26ai AI Vector Search** | 例示 NL-SQL ペア / 用語集の意味検索 | Vanna 風「過去の正解 SQL を検索して文脈注入」を **`VECTOR(1536, FLOAT32)`** で実装。外部ベクトル DB は使わない。 |
| **会話履歴ビュー** (`USER_CLOUD_AI_CONVERSATION_PROMPTS` 等) | マルチターン文脈・監査 | クエリ履歴/会話の永続・監査。**クエリ履歴画面と評価のソース**。 |
| **OCI GenAI(Cohere Embed v4 / Rerank v4)** | embedding / rerank | 例示検索・スキーマリンクの意味マッチ。**埋め込み/再ランクは Enterprise AI ではなくこちら**(RAG と同分担)。 |
| **OCI Object Storage** | スキーマ doc / 例示 CSV / artifact 保管 | 取込原本・学習データの保管。 |
| **APEX + Select AI**(参考) | ローコード NL2SQL 画面 | Oracle 公式の NL2SQL UI 例。UX 参考(本体は React で実装)。 |

> **provider 方針(重要)**: Select AI profile は `provider=oci` のまま、`oci_endpoint_id` で **OCI Enterprise AI / 専用エンドポイント**を指し、モデルの頭脳は Enterprise AI に寄せる(親スタックの「LLM=Enterprise AI」を維持)。embedding/rerank は OCI GenAI Cohere。**OCI Generative AI の汎用 chat 推論 API を NL2SQL の生成に直接は使わない**(Select AI 経由でのみ利用)。

---

## 4. 評価・ベンチマーク:NL2SQL の成否を測る基盤

| プロジェクト / ベンチ | 種別 | 重要性(本プロジェクトへの取り込み) |
|---|---|---|
| **BIRD** (bird-bench) | 大規模実世界ベンチ(12,751 ペア / 95 DB / 37 ドメイン / ノイズあり) | 2025 時点の主力。**execution accuracy + 効率(VES)**。本プロジェクトの **Evaluation アダプターの指標設計の手本**。 |
| **Spider / Spider 2.0** (Yale/HKUSTDial) | 構造汎化 → エンタープライズ規模(平均 ~800 列) | スキーマ汎化の定番。Spider 2.0 は大規模スキーマ・実務ワークフロー。**大規模スキーマ対応(schema linking/圧縮)の評価**。 |
| **Spider 2.0-Lite / Snow** | 軽量/Snowflake 版 | Agentic 手法(ReFoRCE 等)の比較に。 |
| **execution accuracy / exact-set-match** | 標準指標 | 実行結果一致 / SQL 構文一致。**決定論評価のみ**(LLM-as-judge は追加導入しない)。 |
| **Ragas / DeepEval**(参考) | LLM アプリ評価 | NL2SQL 特化ではないが、回答 narrate の faithfulness 監視の参考。 |
| **test-suite-sql-eval** (taoyds) | SQL 評価ハーネス | 複数 DB 状態での execution match。**CI gate 用ハーネスの参考**。 |

> **本プロジェクトへの再マップ**: 外部評価 SaaS / LLM-as-judge の追加呼び出しは導入せず、**execution_accuracy / exact_match / 実行成功率 / 平均レイテンシ / 推定コスト**などの決定論指標を **Evaluation アダプター(suite)** として束ねる(`request_only`(既定)/ `execution_focused` / `balanced` / `strict_ci` / `bird_like`)。BIRD/Spider はゴールデンセット作成の手本に留める。

---

## 5. キュレーション一覧・ハンドブック(継続調査源)

| リソース | 種別 | 用途 |
|---|---|---|
| **eosphoros-ai/Awesome-Text2SQL** | キュレーション | Text2SQL / Text2DSL / Text2API / Text2Vis の論文・実装・チュートリアル集。新手法の発見源。 |
| **DEEP-PolyU/Awesome-LLM-based-Text2SQL** | サーベイ + キュレーション (TKDE2025) | LLM ベース Text-to-SQL のサーベイ/ベンチ/OSS。手法分類の地図。 |
| **HKUSTDial/NL2SQL_Handbook** | 実務ハンドブック | 最新 Text-to-SQL 技術の継続更新ガイド。実装判断の参照。 |
| **GitHub topics: `nl2sql` / `text2sql`** | タグ | 新規プロジェクトの定点観測。 |

---

## 6. 本プロジェクトの NL2SQL パイプライン(アダプター設計のマッピング)

> RAG プロジェクトの「パイプライン各段階を手動選択アダプターで切替える」思想を NL2SQL へ踏襲する。
> 各アダプターは **外部依存なし・決定論**で挙動を束ね、設定 API + 専用設定画面で切替える。詳細仕様は [AGENTS.md](../AGENTS.md) を正本とする。

| 段階(パイプライン順) | アダプター(設定キー) | 主な選択肢 | 主な調査源 |
|---|---|---|---|
| スキーマ取込 | `nl2sql_schema_source` | full / curated / sampled(**M-Schema 風**: 列ごとに distinct 値 ≤3・≤50 字 + 説明 + 値/セル索引) | denpyo(object_list)、Wren AI(MDL)、M-Schema(XiYan) |
| スキーマリンク | `nl2sql_schema_linking` | enforce_all(既定) / curated / auto_prune(**ベクトル多段**) | CHESS, E-SQL, AutoLink, LitE-SQL, **LinkAlign** |
| 知識・例示 | `nl2sql_knowledge_profile` | off(既定) / glossary / few_shot / rag_trained | Vanna, sql-assist few-shot, Wren AI |
| ルーティング | `nl2sql_router_profile` | off(既定) / classifier(分類器で profile 自動選択) / complexity_aware(単段↔多段) | **sql-assist 分類器**, **EllieSQL** |
| 曖昧性解決 | `nl2sql_clarify_policy` | off(既定) / detect / interactive(確認質問 human-in-the-loop) | **AmbiSQL** |
| 生成バックエンド | `nl2sql_generation_backend` | select_ai_agent(既定) / select_ai / app_enterprise_ai | sql-assist, denpyo, NL2SQL Studio |
| 生成アクション | `nl2sql_generation_action` | showsql→runsql 2 段(既定) / runsql / narrate / explainsql / chat | Oracle Select AI |
| SQL ガードレール | `nl2sql_guardrail_policy` | read_only(既定・**DB ロール/セッションで強制**) / strict(allowlist+row limit+EXPLAIN+**semantic_verify**) / sandboxed | **Defensive NL2SQL**, GBV-SQL |
| 自己修正 | `nl2sql_correction_profile` | off(既定) / retry_on_error / verified(**逆翻訳突合**) | SQLFixAgent, CSC-SQL, PremSQL, GBV-SQL |
| エージェント計画 | `nl2sql_agentic_profile` | off(既定) / decompose / multi_hop | MAC-SQL, DIN-SQL, DB-GPT |
| キャッシュ | `nl2sql_cache_policy` | off(既定) / nl_sql / nl_result / sql_result(**Oracle 26ai 意味キャッシュ**) | Defensive NL2SQL, semantic caching |
| 結果整形 | `nl2sql_result_profile` | table(既定) / narrate / chart / bilingual_ja_en | Vanna 自動可視化, Wren AI |
| 評価 | `nl2sql_evaluation_suite` | request_only(既定) / execution_focused / balanced / strict_ci / bird_like | BIRD, Spider, test-suite-sql-eval |
| 業務アシスタント | `nl2sql_business_view` | 複数データソース/profile を「利用者視点」で束ねる | Wren AI セマンティック層, RAG 業務アシスタント |

> これらは研究・設計参考であり、実装時は必ず **Oracle Select AI / Select AI Agent + OCI Enterprise AI(`oci_endpoint_id` 経由)+ OCI GenAI Cohere + Oracle 26ai** に再マッピングする。
> 既定 preset は「現行(最小)挙動」と一致させ、外部ベクトル DB・別 LLM provider・別 NL2SQL SaaS は導入しない(逸脱時は AGENTS.md に従い要確認)。

---

## 7. 本番ハードニング / 最適化パターン(2025–2026)

> 「動く NL2SQL」と「**本番で信頼できる NL2SQL**」の差を埋める設計群。いずれも確定スタック内(Select AI / Enterprise AI / OCI Cohere / Oracle 26ai)で**決定論的に**実装する。

| パターン | 出典/着想 | 本プロジェクトでの実装方針 |
|---|---|---|
| **多層防御(prompt だけに頼らない)** | "Engineering Trust: A Defensive Architecture for NL2SQL" (2026) | 「`SELECT` のみ」を prompt で頼まない。**read-only は専用低権限 DB ロール/セッションで物理的に強制**し、生成 SQL は実行前に**ハード検証層**(構文解析で文種別判定・object allowlist・row limit・`EXPLAIN PLAN` で全件スキャン/危険結合検知)を必ず通す。→ Guardrail アダプター。 |
| **意味検証(SQL2Text 逆翻訳)** | GBV-SQL | 生成 SQL を Select AI `explainsql`/`narrate` で NL へ逆翻訳し、元の質問と突合して**論理的な取り違え**(誤 JOIN・誤列・PII 列アクセス)を検出→修正。追加 provider 不要。→ Guardrail/Self-Correction `semantic_verify`。 |
| **意味キャッシュ** | Defensive NL2SQL / semantic caching | `NL→SQL` / `NL→結果` / `SQL→結果` の 3 層キャッシュ。NL の類似は **Oracle 26ai ベクトル検索**(Cohere 埋め込み)で判定し、頻出・類似質問の再生成/再実行を回避(レイテンシ/コスト削減)。鮮度要件で TTL/失効を制御。→ Cache アダプター。 |
| **曖昧性検出・確認** | AmbiSQL | 集計粒度・期間・同名列など曖昧な NL を taxonomy で検出し、**確認質問**で意図確定してから生成(2段ゲート哲学と整合)。→ Clarify ゲート。 |
| **複雑度認識ルーティング** | EllieSQL + sql-assist 分類器 | 質問を Cohere 埋め込み + 軽量分類器(LogisticRegression、決定論)で **(a) どの profile か / (b) 単段(`select_ai`)か多段(`select_ai_agent`)か** を判定し、安価な経路を優先。→ Router アダプター。 |
| **M-Schema 風スキーマ表現** | XiYan M-Schema | profile/プロンプトへ渡すスキーマに、列ごとの **distinct 値例(≤3・各 ≤50 字)+ 列説明 + 制約**を含め、リンク精度を上げる。Select AI profile の `annotations`/`comments`/`constraints` と整合。→ Schema Source(sampled)。 |
| **値/セル値リンク** | 大規模 schema linking 研究 | NL 中のリテラルを実際の列値へ対応付ける。低カーディナリティ列の**値索引**を作り、`LIKE`/類似で候補値を注入(例: 「東京」→ `LOCATION='東京'`)。→ Schema Source/Knowledge。 |
| **スケーラブル schema linking** | LinkAlign / RASL | スキーマを意味ユニットに分解した**多段ベクトルリトリーバル**で、列数の多い(Spider 2.0 級)スキーマでも関連要素だけを profile へ渡す。→ Schema Linking(auto_prune)。 |
| **合成データ生成** | Select AI / sql-assist | Select AI で表ごとにサンプル行を生成し、評価ゴールデン・デモ・few-shot bootstrap に利用(本番データに触れずに検証)。 |
| **Oracle セマンティックエンリッチメント** | Oracle Blog "Enterprise-Ready NL2SQL with Semantic Enrichment" | 列コメント/注釈/シノニム/業務用語のメタデータで Select AI のスキーマ理解を補強。本プロジェクトの注釈・用語集管理画面で運用。 |

> **優先度(推奨導入順)**: ① Guardrail 多層防御(read-only 物理強制 + ハード検証)→ ② 2段ゲート(showsql→確認→runsql)→ ③ M-Schema 風 Schema Source → ④ Router(分類器/複雑度)→ ⑤ Cache(意味キャッシュ)→ ⑥ semantic_verify / Clarify。①② は**安全要件のため最初に**入れる。

---

## 参考リンク

- Oracle Select AI: <https://www.oracle.com/artificial-intelligence/generate-sql-queries-with-ai/>
- DBMS_CLOUD_AI パッケージ: <https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/dbms-cloud-ai-package.html>
- Select AI で NL からSQL生成: <https://docs.oracle.com/en-us/iaas/autonomous-database/doc/use-select-ai-generate-sql-natural-language-prompts.html>
- Vanna AI: <https://github.com/vanna-ai/vanna>
- Wren AI: <https://github.com/Canner/WrenAI>
- DB-GPT: <https://github.com/eosphoros-ai/DB-GPT>
- Dataherald: <https://github.com/Dataherald/dataherald>
- NL2SQL Studio (Google Cloud): <https://github.com/GoogleCloudPlatform/nl2sql-studio>
- MAC-SQL: <https://github.com/wbbeyourself/MAC-SQL>
- LinkAlign (EMNLP 2025): <https://github.com/Satissss/LinkAlign>
- EllieSQL(複雑度認識ルーティング): <https://arxiv.org/pdf/2503.22402>
- AmbiSQL(曖昧性検出・解決): <https://arxiv.org/html/2508.15276v2>
- GBV-SQL(SQL2Text 逆翻訳検証): <https://arxiv.org/pdf/2509.12612>
- Defensive Architecture for NL2SQL: <https://medium.com/learnwithnk/engineering-trust-a-defensive-architecture-for-nl2sql-systems-eb2db557446c>
- Oracle Blog: Enterprise-Ready NL2SQL with Semantic Enrichment: <https://blogs.oracle.com/cloud-infrastructure/enterprise-nl2sql-with-semantic-enrichments>
- eosphoros-ai/Awesome-Text2SQL: <https://github.com/eosphoros-ai/Awesome-Text2SQL>
- DEEP-PolyU/Awesome-LLM-based-Text2SQL: <https://github.com/DEEP-PolyU/Awesome-LLM-based-Text2SQL>
- HKUSTDial/NL2SQL_Handbook: <https://github.com/hkustdial/nl2sql_handbook>
- BIRD ベンチ: <https://bird-bench.github.io/>
- Spider 2.0: <https://spider2-sql.github.io/>
- engchina/No.1-SQL-Assist: <https://github.com/engchina/No.1-SQL-Assist>
- engchina/no.1-denpyo-toroku-kun: <https://github.com/engchina/no.1-denpyo-toroku-kun>
