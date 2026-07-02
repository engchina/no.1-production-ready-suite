# AIDB Memory Engineering

Oracle Developer Day 2026 の「AIDBで進化するRAG / ベクトルを超えるMemory Engineering」で示された RAG 手法を、本プロジェクトの確定スタックへ再マップした実装方針。

## 採用する runtime flow

本プロジェクトの RAG runtime は、単純な「近い chunk を prompt へ入れる」形ではなく、次の順序で動く。

1. 依頼を受ける
2. Business Context Pack を確定する
3. Memory Router / Plan Builder で Retrieval Plan を作る
4. AIDB Retrieval で memory type 別に候補を取得する
5. Resolver / Verifier で候補を検証する
6. Context Builder で根拠・補助・過去文脈を分けて LLM へ渡す
7. Agent Memory Loop の writeback / eval 方針を監査・診断に残す

## Business Context Pack

検索前に、誰のどの業務かを確定する。

- tenant: `X-Tenant-ID` を hash 化し、Oracle document/chunk/knowledge base の predicate に使う。
- user: `X-User-ID` を hash 化し、監査相関と Agent Memory scope に使う。
- role: `X-RAG-Role-ID` を hash 化し、Business Context と Agent Memory scope に使う。
- agent / thread: `X-RAG-Agent-ID`、`X-RAG-Thread-ID` を hash 化し、Agent Memory の検索・保存 scope に使う。
- ACL: `X-RAG-Allowed-Document-Ids`、`X-RAG-Allowed-Category-Names`、`X-RAG-Allowed-Knowledge-Base-Ids` を request scope として固定する。
- dataset: `knowledge_base_ids` / `filters.knowledge_base_id` を Oracle knowledge base membership に固定する。
- source ACL: `filters.source_acl` を chunk metadata `source_acl` に固定する。
- version: `filters.document_version` を chunk metadata `document_version` に固定する。

raw tenant/user id や query 本文は audit / trace へ保存しない。

## Retrieval Plan

Router の分岐結果は、自由検索ではなく Retrieval Plan として固定する。

- `plan_id`: trace と非機密 routing 情報から作る短い ID。
- `purpose`: grounded answer、structured query boundary、relationship summary など。
- `memory_sequence`: `evidence -> similar -> structure -> history`。
- `memory_backends`: Oracle 26ai Hybrid Vector Search、Oracle AI Vector Search + OCI Rerank、GraphRAG-lite、Oracle Agent Memory Search policy。
- `query_shape`: hybrid/vector/keyword/graph/structured candidate。
- `scope_keys`: tenant、ACL、dataset、source_acl、version、filter key。
- `evidence_rules`: citation、scope、version、source ACL、contradiction の検証。
- `termination_criteria`: 検証済み根拠がない場合は LLM を呼ばない。
- `gap_handling`: 不足時に Agent の自由検索へ逃がさず、no-results / warning / evaluation へ渡す。

## Memory Type

Retrieval は同じ候補集合を無差別に扱わず、役割を分ける。

| memory type | 役割 | backend |
|---|---|---|
| evidence | 回答の主張を支える必須根拠 | Oracle 26ai Hybrid Vector Search + Oracle Text |
| similar | 理解・説明を補助する類似情報 | Oracle 26ai AI Vector Search + OCI Cohere Rerank |
| structure | 関係・集計・構造条件 | GraphRAG-lite relationship boundary |
| history | user / role / agent / thread の継続文脈 | Oracle 26ai `rag_agent_memories` |

## Agent Memory Loop

Agent Memory は外部ストアを使わず、Oracle 26ai 内の `rag_agent_memories` に保存する。

- scope: `tenant_id_hash`、`user_id_hash`、`role_id_hash`、`agent_id_hash`、`thread_id_hash`。raw ID は保存しない。
- vector: `memory_text` を OCI Generative AI Cohere Embed v4 で `SEARCH_DOCUMENT` embedding 化し、`VECTOR(1536, FLOAT32)` に保存する。
- search: 検索 request に user / agent / thread scope がある場合だけ、query embedding で Agent Memory Search を行う。取得結果は `retrieval_mode=agent_memory`、`context_role=history` として扱う。
- writeback: 回答が guardrail を通過し、引用付き context がある場合だけ、query 原文ではなく「回答要約 + 根拠 ID」の短い memory を保存する。
- eval: `evaluate_agent_memory()` で helpful / not helpful を `usefulness_score` の移動平均として更新できる。

`History` は継続性の補助であり、回答主張の必須根拠ではない。rerank では Evidence / Support 候補を優先し、Agent Memory が一次根拠を押し出さないようにする。

## Resolver / Verifier

取得候補はそのまま根拠にしない。Context Builder へ渡す前に次を確認する。

- citation があること
- source ACL / request scope に反していないこと
- version が archived / expired / inactive / obsolete / superseded ではないこと
- contradiction metadata が conflict / contradicted ではないこと
- support-only は根拠ではなく補助扱いにすること

検証済み chunk には `context_role`、`resolver_verified`、`resolver_confidence`、`resolver_necessity`、`memory_plan_id` を付ける。除外件数と理由は diagnostics / audit に残す。

## Context Builder

LLM context は `Evidence`、`Support`、`Structure`、`History` の label を付けた構造で渡す。回答の根拠として扱えるのは `Evidence` のみで、`Support` と `History` は説明・比較・継続性の補助に使う。

`SearchDiagnostics` と `rag_search_audit` には、`memory_plan_id`、business context、retrieval plan、context pack、evidence/support/structure/history 件数、Agent Memory retrieval / writeback 件数、resolver rejected 件数、不足 context 件数を残す。

## Retrieval アダプター / Grounding アダプター

検索段階と検索後処理を、Parser / Chunking アダプターと同型の **手動選択できるアダプター**に束ねる。

- **Retrieval アダプター(`rag_retrieval_strategy` + 合成トグル)** — `app/rag/retrieval_adapter.py`。
  検索モードは hybrid_rrf(既定)/ vector / keyword / graph_augmented の 4 択で、既存の
  hybrid / AI Vector Search / Oracle Text / GraphRAG-lite 経路へ解決する。gap-stop /
  業務適合加重 / 補正再検索 / クエリ拡張は**任意のモードに合成できるトグル**
  (`RAG_RETRIEVAL_GAP_STOP_ENABLED` 等)。legacy 複合値(business_context_strict /
  corrective_multi_query)は読み取り互換でモード + 強制トグルへ分解し(
  `rag_pipeline_core.decompose_retrieval_strategy`)、保存は常に新形式のみ。per-request の
  `strategy` / `mode` を明示した場合はそちらを優先する。
  `GET/PATCH /api/settings/retrieval` と専用設定画面で切替。
- **Grounding アダプター(`rag_post_retrieval_pipeline`)** — `app/rag/grounding_adapter.py`。
  custom(既定・既存 `rag_context_*` フラグを尊重)/ lean / verified_context /
  context_enrich / compact / full_governed。dedupe / Resolver-Verifier / Context Builder は
  常時実行し、任意段(dependency promotion / MMR diversity / context expansion /
  compression)を preset で束ねる。`GET/PATCH /api/settings/grounding` と専用設定画面で切替。

### 追加した決定論的手法(他 RAG を上回るための差分)

- **gap-stop(PDF Memory Router Route D)**: gap-stop トグル
  (`RAG_RETRIEVAL_GAP_STOP_ENABLED`)で業務スコープ(tenant / dataset / ACL / version)が
  未確定なら検索を実行せず、`gap_stopped` 診断付きで insufficient_context を返す。
  Agent の自由検索へ逃がさない。
- **corrective / iterative retrieval(PDF Step5「過不足あれば追加検索」+ CRAG 3分岐)**:
  補正再検索トグル(`RAG_RETRIEVAL_CORRECTIVE_ENABLED`)で、検証済み根拠が 0 件のとき
  top_k 拡大・絞り込み filter 緩和で再検索→再 rerank→再 verify する。加えて evidence grade
  3分岐(高 ≥ `RAG_CRAG_HIGH_CONFIDENCE_THRESHOLD` → そのまま生成 / 中間 → クエリ精緻化 +
  再検索を `RAG_CRAG_MAX_HOPS` まで / 低 < 低閾値 → 棄権は
  `RAG_CRAG_LOW_EVIDENCE_ABSTAIN_ENABLED` の opt-in)。診断に `crag_hops` /
  `crag_evidence_grade` を残す。
- **business-fit 加重(PDF AIDB Proof)**: rerank 後に `final = semantic × business_fit`
  (version active / source_acl / 鮮度 を metadata から決定論的に算出)で並べ替える。

既定 preset(hybrid_rrf / custom)は現行挙動と一致させ、明示選択時のみ挙動を束ねる。
`SearchDiagnostics` には `retrieval_strategy_adapter` / `post_retrieval_pipeline` /
`gap_stopped` / `corrective_retried` / `business_fit_reordered_count` を残す。
