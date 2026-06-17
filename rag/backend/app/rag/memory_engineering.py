"""AIDB RAG の Memory Engineering runtime helpers。"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from app.rag.request_context import current_audit_request_context
from app.rag.retrieval_strategy import ResolvedRetrievalStrategy
from app.schemas.search import RetrievedChunk, SearchMode, SearchRequest, SearchStrategy

ContextRole = Literal["evidence", "support", "structure", "history"]
MemoryType = Literal["evidence", "similar", "structure", "history"]

VERSION_REJECT_STATUSES = {
    "archived",
    "expired",
    "inactive",
    "obsolete",
    "superseded",
}
CONTRADICTION_REJECT_STATUSES = {"conflict", "contradicted", "contradiction"}
ACCESS_DENIED_METADATA_KEYS = {
    "source_acl_denied",
    "acl_denied",
    "access_denied",
}


@dataclass(frozen=True)
class BusinessContextPack:
    """検索前に固定した業務境界を、非機密の形で表す。"""

    tenant_scoped: bool
    user_scoped: bool
    role_scoped: bool
    document_acl_scoped: bool
    category_acl_scoped: bool
    knowledge_base_scoped: bool
    dataset_count: int
    filter_keys: tuple[str, ...]
    source_acl_filter_present: bool
    version_filter_present: bool

    def diagnostics(self) -> dict[str, object]:
        return {
            "tenant_scoped": self.tenant_scoped,
            "user_scoped": self.user_scoped,
            "role_scoped": self.role_scoped,
            "document_acl_scoped": self.document_acl_scoped,
            "category_acl_scoped": self.category_acl_scoped,
            "knowledge_base_scoped": self.knowledge_base_scoped,
            "dataset_count": self.dataset_count,
            "filter_keys": list(self.filter_keys),
            "source_acl_filter_present": self.source_acl_filter_present,
            "version_filter_present": self.version_filter_present,
        }


@dataclass(frozen=True)
class RetrievalPlan:
    """Router 出力を、Agent が逸脱できない読み取り計画へ落としたもの。"""

    plan_id: str
    purpose: str
    memory_sequence: tuple[MemoryType, ...]
    memory_backends: dict[MemoryType, str]
    query_shape: str
    scope_keys: tuple[str, ...]
    evidence_rules: tuple[str, ...]
    termination_criteria: str
    gap_handling: str
    evidence_allowed: bool
    query_variant_count: int

    def diagnostics(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "purpose": self.purpose,
            "memory_sequence": list(self.memory_sequence),
            "memory_backends": dict(self.memory_backends),
            "query_shape": self.query_shape,
            "scope_keys": list(self.scope_keys),
            "evidence_rules": list(self.evidence_rules),
            "termination_criteria": self.termination_criteria,
            "gap_handling": self.gap_handling,
            "evidence_allowed": self.evidence_allowed,
            "query_variant_count": self.query_variant_count,
        }


@dataclass(frozen=True)
class ContextPack:
    """取得候補を Resolver / Verifier で根拠・補助・過去文脈に分けた結果。"""

    chunks: list[RetrievedChunk]
    evidence_count: int = 0
    support_count: int = 0
    structure_count: int = 0
    history_count: int = 0
    rejected_count: int = 0
    insufficient_count: int = 0
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)

    def diagnostics(self) -> dict[str, object]:
        return {
            "evidence_count": self.evidence_count,
            "support_count": self.support_count,
            "structure_count": self.structure_count,
            "history_count": self.history_count,
            "rejected_count": self.rejected_count,
            "insufficient_count": self.insufficient_count,
            "rejection_reasons": list(self.rejection_reasons),
        }


@dataclass(frozen=True)
class BuiltContext:
    """Context Builder が LLM に渡す構造化 context。"""

    context: str
    citations: list[RetrievedChunk]
    evidence_count: int
    support_count: int
    structure_count: int
    history_count: int

    def diagnostics(self) -> dict[str, object]:
        return {
            "evidence_count": self.evidence_count,
            "support_count": self.support_count,
            "structure_count": self.structure_count,
            "history_count": self.history_count,
            "citation_count": len(self.citations),
            "context_chars": len(self.context),
        }


def build_business_context_pack(request: SearchRequest) -> BusinessContextPack:
    """request context と filter から Business Context Pack を作る。"""
    context = current_audit_request_context()
    knowledge_base_scoped = (
        bool(request.knowledge_base_ids)
        or context.allowed_knowledge_base_ids is not None
        or bool(request.filters.get("knowledge_base_id"))
    )
    return BusinessContextPack(
        tenant_scoped=context.tenant_id_hash is not None,
        user_scoped=context.user_id_hash is not None,
        role_scoped=context.role_id_hash is not None,
        document_acl_scoped=context.allowed_document_ids is not None,
        category_acl_scoped=context.allowed_category_names is not None,
        knowledge_base_scoped=knowledge_base_scoped,
        dataset_count=len(request.knowledge_base_ids),
        filter_keys=tuple(sorted(request.filters)),
        source_acl_filter_present="source_acl" in request.filters,
        version_filter_present="document_version" in request.filters,
    )


def build_retrieval_plan(
    *,
    trace_id: str,
    request: SearchRequest,
    business_context: BusinessContextPack,
    resolved_strategy: ResolvedRetrievalStrategy,
    query_variant_count: int,
) -> RetrievalPlan:
    """PDF の Plan Builder 相当の読み取り計画を作る。"""
    memory_sequence: tuple[MemoryType, ...] = (
        "evidence",
        "similar",
        "structure",
        "history",
    )
    scope_keys = _scope_keys(business_context)
    query_shape = _query_shape(request.mode, resolved_strategy)
    purpose = _plan_purpose(resolved_strategy)
    plan_id = _plan_id(
        trace_id=trace_id,
        strategy=resolved_strategy.strategy.value,
        query_shape=query_shape,
        scope_keys=scope_keys,
        query_variant_count=query_variant_count,
    )
    return RetrievalPlan(
        plan_id=plan_id,
        purpose=purpose,
        memory_sequence=memory_sequence,
        memory_backends={
            "evidence": "Oracle 26ai Hybrid Vector Search + Oracle Text",
            "similar": "Oracle 26ai AI Vector Search + OCI Cohere Rerank",
            "structure": "Oracle Select AI / GraphRAG-lite SQL boundary",
            "history": "Oracle 26ai rag_agent_memories Agent Memory Search",
        },
        query_shape=query_shape,
        scope_keys=scope_keys,
        evidence_rules=(
            "citation_required",
            "scope_locked",
            "version_checked",
            "source_acl_checked",
            "contradiction_checked",
        ),
        termination_criteria="verified_evidence_or_no_results",
        gap_handling="insufficient_context_warning_without_free_search",
        evidence_allowed=True,
        query_variant_count=query_variant_count,
    )


def resolve_context_pack(
    chunks: list[RetrievedChunk],
    *,
    plan: RetrievalPlan,
) -> ContextPack:
    """取得候補をそのまま根拠にせず、引用・権限・版・矛盾を確認する。"""
    verified: list[RetrievedChunk] = []
    evidence_count = 0
    support_count = 0
    structure_count = 0
    history_count = 0
    rejection_reasons: list[str] = []

    for chunk in chunks:
        rejection_reason = _candidate_rejection_reason(chunk)
        if rejection_reason is not None:
            rejection_reasons.append(rejection_reason)
            continue
        role = _context_role(chunk)
        confidence = _context_confidence(chunk)
        necessity = "required" if role == "evidence" else "optional"
        metadata = {
            **chunk.metadata,
            "memory_plan_id": plan.plan_id,
            "context_role": role,
            "resolver_verified": True,
            "resolver_confidence": confidence,
            "resolver_necessity": necessity,
            "evidence_allowed": role == "evidence",
        }
        verified.append(chunk.model_copy(update={"metadata": metadata}))
        if role == "evidence":
            evidence_count += 1
        elif role == "support":
            support_count += 1
        elif role == "structure":
            structure_count += 1
        elif role == "history":
            history_count += 1

    insufficient_count = 1 if chunks and evidence_count == 0 else 0
    return ContextPack(
        chunks=verified,
        evidence_count=evidence_count,
        support_count=support_count,
        structure_count=structure_count,
        history_count=history_count,
        rejected_count=len(chunks) - len(verified),
        insufficient_count=insufficient_count,
        rejection_reasons=tuple(sorted(set(rejection_reasons))),
    )


def build_context_with_memory_roles(
    chunks: list[RetrievedChunk],
    max_chars: int,
) -> BuiltContext:
    """根拠・補助・構造・過去文脈を分けた LLM context を作る。"""
    parts: list[str] = []
    citations: list[RetrievedChunk] = []
    role_counts: dict[ContextRole, int] = {
        "evidence": 0,
        "support": 0,
        "structure": 0,
        "history": 0,
    }
    total = 0
    separator = "\n\n---\n\n"
    for chunk in chunks:
        role = _metadata_role(chunk.metadata)
        role_counts[role] += 1
        source = chunk.file_name or chunk.document_id
        label = _context_label(role, role_counts[role])
        confidence = str(chunk.metadata.get("resolver_confidence") or "mid")
        necessity = str(chunk.metadata.get("resolver_necessity") or "optional")
        body = f"[{label} | {confidence} | {necessity} | {source}#{chunk.chunk_id}]\n{chunk.text}"
        separator_len = len(separator) if parts else 0
        if total + separator_len + len(body) > max_chars:
            remaining = max_chars - total - separator_len
            if remaining > 0 and not parts:
                parts.append(body[:remaining])
                citations.append(chunk)
            break
        parts.append(body)
        citations.append(chunk)
        total += separator_len + len(body)
    return BuiltContext(
        context=separator.join(parts),
        citations=citations,
        evidence_count=sum(
            1 for chunk in citations if _metadata_role(chunk.metadata) == "evidence"
        ),
        support_count=sum(1 for chunk in citations if _metadata_role(chunk.metadata) == "support"),
        structure_count=sum(
            1 for chunk in citations if _metadata_role(chunk.metadata) == "structure"
        ),
        history_count=sum(1 for chunk in citations if _metadata_role(chunk.metadata) == "history"),
    )


def _scope_keys(business_context: BusinessContextPack) -> tuple[str, ...]:
    keys: list[str] = []
    if business_context.tenant_scoped:
        keys.append("tenant")
    if business_context.user_scoped:
        keys.append("user")
    if business_context.role_scoped:
        keys.append("role")
    if business_context.document_acl_scoped:
        keys.append("document_acl")
    if business_context.category_acl_scoped:
        keys.append("category_acl")
    if business_context.knowledge_base_scoped:
        keys.append("dataset")
    if business_context.source_acl_filter_present:
        keys.append("source_acl")
    if business_context.version_filter_present:
        keys.append("version")
    keys.extend(f"filter:{key}" for key in business_context.filter_keys)
    return tuple(keys)


def _query_shape(
    mode: SearchMode,
    resolved_strategy: ResolvedRetrievalStrategy,
) -> str:
    if resolved_strategy.strategy in (SearchStrategy.GRAPH_GLOBAL, SearchStrategy.GRAPH_LOCAL):
        return "graph_structure"
    if resolved_strategy.route_reason.endswith("select_ai_candidate"):
        return "structured_query_candidate"
    return f"{mode.value}_retrieval"


def _plan_purpose(resolved_strategy: ResolvedRetrievalStrategy) -> str:
    if resolved_strategy.strategy in (SearchStrategy.GRAPH_GLOBAL, SearchStrategy.GRAPH_LOCAL):
        return "relationship_or_summary_grounding"
    if resolved_strategy.route_reason.endswith("select_ai_candidate"):
        return "structured_business_query_boundary"
    return "grounded_answer_generation"


def _plan_id(
    *,
    trace_id: str,
    strategy: str,
    query_shape: str,
    scope_keys: tuple[str, ...],
    query_variant_count: int,
) -> str:
    payload = "|".join(
        [
            trace_id,
            strategy,
            query_shape,
            ",".join(scope_keys),
            str(query_variant_count),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _candidate_rejection_reason(chunk: RetrievedChunk) -> str | None:
    if not chunk.document_id or not chunk.chunk_id or not chunk.text.strip():
        return "missing_citation"
    for key in ACCESS_DENIED_METADATA_KEYS:
        if _metadata_bool(chunk.metadata.get(key)):
            return "access_denied"
    if chunk.metadata.get("evidence_allowed") is False:
        return "evidence_not_allowed"
    version_status = _metadata_lower(chunk.metadata, "version_status")
    if version_status in VERSION_REJECT_STATUSES:
        return "version_rejected"
    contradiction_status = _metadata_lower(chunk.metadata, "contradiction_status")
    if contradiction_status in CONTRADICTION_REJECT_STATUSES:
        return "contradiction_rejected"
    return None


def _context_role(chunk: RetrievedChunk) -> ContextRole:
    metadata_role = _metadata_lower(chunk.metadata, "context_role")
    if metadata_role in {"evidence", "support", "structure", "history"}:
        return metadata_role  # type: ignore[return-value]
    retrieval_mode = _metadata_lower(chunk.metadata, "retrieval_mode")
    if retrieval_mode in {"agent_memory", "memory", "history"}:
        return "history"
    if retrieval_mode in {"graph_global", "graph_local", "select_ai", "structure"}:
        return "structure"
    if _metadata_bool(chunk.metadata.get("support_only")):
        return "support"
    score = chunk.rerank_score if chunk.rerank_score is not None else chunk.score
    return "evidence" if score >= 0.75 else "support"


def _context_confidence(chunk: RetrievedChunk) -> str:
    score = chunk.rerank_score if chunk.rerank_score is not None else chunk.score
    if score >= 0.85:
        return "high"
    if score >= 0.6:
        return "mid"
    return "low"


def _metadata_role(metadata: Mapping[str, object]) -> ContextRole:
    role = str(metadata.get("context_role") or "").strip().casefold()
    if role in {"evidence", "support", "structure", "history"}:
        return role  # type: ignore[return-value]
    return "support"


def _context_label(role: ContextRole, index: int) -> str:
    prefix = {
        "evidence": "Evidence",
        "support": "Support",
        "structure": "Structure",
        "history": "History",
    }[role]
    return f"{prefix} {index}"


def _metadata_lower(metadata: Mapping[str, object], key: str) -> str:
    value = metadata.get(key)
    return value.strip().casefold() if isinstance(value, str) else ""


def _metadata_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y"}
    return False
