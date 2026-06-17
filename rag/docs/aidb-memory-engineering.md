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
- `memory_backends`: Oracle 26ai Hybrid Vector Search、Oracle AI Vector Search + OCI Rerank、Oracle Select AI / GraphRAG-lite、Oracle Agent Memory Search policy。
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
| structure | 関係・集計・構造条件 | Oracle Select AI / GraphRAG-lite SQL boundary |
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
