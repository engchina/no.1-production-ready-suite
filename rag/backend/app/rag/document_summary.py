"""文書全体の要約（#4793）。取込の後に 1 文書 1 回の LLM で作り、文書の説明と要約の chunk にする。

流れ（取込の job が成功した後の後段・文書の詳細の「作り直す」）:

1. 文書の有効な chunk_set と、その元の抽出結果を読む。
2. 入力を作る（``build_summary_input``）。本文は文書の先頭から入力の予算まで。予算を超える
   文書は、文書全体の見出しの一覧を足し、本文を省いたことを入力に書く。
3. 入力の指紋（指示と入力の hash）が保存済みの要約と同じなら、LLM を呼ばずに保存済みの文を使う
   （索引だけの作り直し・同じ解析の別の処理レシピで、もう一度呼ばない）。
4. LLM を 1 回呼び（``document.summarize``）、構造化出力を Pydantic で検証する。
5. 要約の chunk を埋め込み、文書の有効な chunk_set に 1 つずつ入れる
   （``DocumentSummaryStore.save_ready``）。

失敗・時間切れは取込を失敗にしない（``refresh_document_summary`` は例外を出さず、失敗の理由を
``rag_document_summaries`` に残す）。要約の文は検索の手がかりと文書の説明で、原文の代わりではない。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pr_backend_core.bounded_calls import Attempt, BoundedCallError, ErrorKind
from pr_backend_core.observability.model_purpose import async_call_with_model_purpose
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from rag_parser_core.extraction import DOCUMENT_SUMMARY_CONTENT_KIND, is_derived_content
from rag_parser_core.oci_enterprise_ai import sdk_retries_disabled

from app.clients.ai_calls import make_ai_caller
from app.clients.document_summary_store import (
    DOCUMENT_SUMMARY_KEY,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    DocumentSummaryRow,
    DocumentSummaryStore,
    SummaryTarget,
)
from app.clients.oci_enterprise_ai import OciEnterpriseAiClient
from app.clients.oci_genai import OciGenAiClient
from app.rag.chunking import Chunk
from app.rag.chunking_small_to_big import ENGINE_SEARCH_TEXT_KEY, engine_search_text
from app.rag.ingestion_prompts import DOCUMENT_SUMMARY_PROMPT
from app.schemas.document import DocumentContentSummary
from app.schemas.extraction import StructuredExtraction

logger = logging.getLogger(__name__)

DOCUMENT_SUMMARY_PROMPT_KEY = "document_summary"
# 要約の文の長さの上限（構造化出力の schema で渡し、超えた応答は形の誤りとしてやり直す）。
SUMMARY_MAX_CHARS = 1600
# 話題の語の数と 1 語の長さの上限（超えた分はコードで落とす）。
TOPICS_MAX_ITEMS = 10
TOPIC_MAX_CHARS = 80
# 本文を省いた文書で、見出しの一覧に使う入力の予算の割合（分母）。
_OUTLINE_BUDGET_DIVISOR = 4
# 本文を行の境目で切るとき、予算の終わりからさかのぼって探す範囲の割合（分母）。
_LINE_BREAK_SEARCH_DIVISOR = 5
# 入力の作り方の版（入力の形を変えたら上げる。保存済みの要約の指紋と合わなくなり、作り直す）。
_INPUT_FORMAT_VERSION = "1"
# 「作成中」の印を、時間の上限からこの秒数を過ぎたら途切れたものとして扱う。
_GENERATING_GRACE_SECONDS = 60.0
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

FAILURE_TIMEOUT = "timeout"
FAILURE_MODEL = "model_failed"
FAILURE_INVALID_OUTPUT = "invalid_output"
FAILURE_NO_CONTENT = "no_content"
FAILURE_SOURCE_MISSING = "source_missing"
FAILURE_INDEX = "index_failed"
FAILURE_INTERRUPTED = "interrupted"


class DocumentSummaryOutput(BaseModel):
    """LLM の構造化出力（文書全体の要約）。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(
        max_length=SUMMARY_MAX_CHARS,
        description="文書の主題・主な内容・結論を、文書の本文と同じ言語でまとめた文。",
    )
    topics: list[str] = Field(
        default_factory=list,
        description="この文書を探す人が使う話題の語。文書の本文の語のまま挙げる。",
    )


DOCUMENT_SUMMARY_RESPONSE_SCHEMA = DocumentSummaryOutput.model_json_schema()


class SummaryUnavailable(Exception):
    """要約を作れなかった理由（``code`` を ``failure_code`` に残す）。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SummaryInput:
    """LLM に渡す入力と、その作り方の記録。"""

    text: str
    truncated: bool
    body_chars: int
    source_chars: int
    heading_count: int


@dataclass(frozen=True)
class SummarySource:
    """抽出結果から取り出した要約の材料（本文と、読み順の見出し）。"""

    body: str
    headings: tuple[tuple[int, str], ...]


class _TextModel(Protocol):
    async def generate(
        self,
        prompt: str,
        context: str,
        *,
        system_prompt: str,
        instruction: str = "",
        response_schema: Mapping[str, Any] | None = None,
        response_schema_name: str = "structured_answer",
    ) -> str: ...


class _Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def summary_source(extraction: StructuredExtraction) -> SummarySource:
    """抽出結果の本文（派生の要素を除く。図・画像の読み取りの文を含む）と見出しの一覧。"""
    parts = [
        element.text.strip()
        for element in extraction.elements
        if element.text.strip() and not is_derived_content(element.content_kind, element.metadata)
    ]
    body = "\n".join(parts).strip() or extraction.raw_text.strip()
    headings = tuple(
        (max(1, node.depth), node.title.strip())
        for node in extraction.navigation
        if node.title.strip()
    )
    return SummarySource(body=body, headings=headings)


def _outline_lines(headings: Sequence[tuple[int, str]], max_depth: int) -> list[str]:
    return [f"{'  ' * (depth - 1)}{title}" for depth, title in headings if depth <= max_depth]


def _fit_outline(headings: Sequence[tuple[int, str]], budget: int) -> tuple[str, int]:
    """予算に収まる見出しの一覧（深い階層から落とす。それでも超えるなら先頭から予算まで）。"""
    if not headings or budget <= 0:
        return "", 0
    for max_depth in sorted({depth for depth, _ in headings}, reverse=True):
        lines = _outline_lines(headings, max_depth)
        text = "\n".join(lines)
        if len(text) <= budget:
            return text, len(lines)
    kept: list[str] = []
    used = 0
    for line in _outline_lines(headings, min(depth for depth, _ in headings)):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept), len(kept)


def _head(text: str, budget: int) -> str:
    """先頭から予算までの本文（予算の終わりに近い行の境目で切る）。"""
    if len(text) <= budget:
        return text
    cut = text.rfind("\n", budget - budget // _LINE_BREAK_SEARCH_DIVISOR, budget)
    return text[: cut if cut > 0 else budget].rstrip()


def build_summary_input(source: SummarySource, *, max_chars: int) -> SummaryInput:
    """入力を作る。予算に収まる文書は本文の全体、超える文書は先頭の本文と見出しの一覧。"""
    body = source.body
    total = len(body)
    if total <= max_chars:
        return SummaryInput(
            text=f'<document_body truncated="false">\n{body}\n</document_body>',
            truncated=False,
            body_chars=total,
            source_chars=total,
            heading_count=0,
        )
    outline, heading_count = _fit_outline(source.headings, max_chars // _OUTLINE_BUDGET_DIVISOR)
    head = _head(body, max_chars - len(outline))
    text = (
        f'<document_body truncated="true" shown_chars="{len(head)}" total_chars="{total}">\n'
        f"{head}\n</document_body>"
    )
    if outline:
        text += f"\n<document_outline>\n{outline}\n</document_outline>"
    return SummaryInput(
        text=text,
        truncated=True,
        body_chars=len(head),
        source_chars=total,
        heading_count=heading_count,
    )


def summary_input_hash(prompt: str, text: str) -> str:
    """保存済みの要約を使い直せるかを決める指紋（指示と入力が同じなら同じ）。"""
    payload = "\0".join((_INPUT_FORMAT_VERSION, prompt, text))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_summary_output(raw: str) -> DocumentSummaryOutput:
    """LLM の応答を検証する（形が合わなければ ``ValidationError`` / ``JSONDecodeError``）。"""
    match = _JSON_OBJECT.search(str(raw or ""))
    parsed = DocumentSummaryOutput.model_validate(json.loads(match.group(0) if match else ""))
    topics: list[str] = []
    for topic in parsed.topics:
        value = " ".join(topic.split())[:TOPIC_MAX_CHARS]
        if value and value not in topics:
            topics.append(value)
    return DocumentSummaryOutput(summary=parsed.summary.strip(), topics=topics[:TOPICS_MAX_ITEMS])


def summary_chunk(
    summary: str,
    topics: Sequence[str],
    *,
    document_title: str,
    context_header_enabled: bool,
    truncated: bool,
) -> Chunk:
    """要約の chunk（索引の番号は保存のときに決める）。

    本文（表示・引用に出る文）は要約の文だけ。話題の語は検索用の文（全文検索の索引・埋め込み・
    rerank の入力）にだけ足す。
    """
    metadata: dict[str, str | int | float | bool | None] = {
        "content_kind": DOCUMENT_SUMMARY_CONTENT_KIND,
        DOCUMENT_SUMMARY_KEY: True,
        "chunk_strategy": DOCUMENT_SUMMARY_CONTENT_KIND,
        "summary_input_truncated": truncated,
        "context_header_enabled": context_header_enabled,
    }
    if topics:
        metadata[ENGINE_SEARCH_TEXT_KEY] = f"{summary}\n{' / '.join(topics)}"
    title = document_title.strip()
    if context_header_enabled and title:
        metadata["context_header"] = title
    return Chunk(text=summary, index=0, start_offset=0, end_offset=len(summary), metadata=metadata)


def _embedding_input(chunk: Chunk) -> str:
    """埋め込みの入力（全文検索の索引の文と同じ: 文脈ヘッダ・要約の文・話題の語）。"""
    if search_text := engine_search_text(chunk.metadata):
        return search_text
    header = str(chunk.metadata.get("context_header") or "").strip()
    return f"{header}\n{chunk.text}" if header else chunk.text


def _timeout_seconds(settings: object) -> float:
    return float(getattr(settings, "rag_document_summary_timeout_seconds", 120.0))


def content_summary_view(
    row: DocumentSummaryRow | None, settings: object, *, now: datetime | None = None
) -> DocumentContentSummary | None:
    """画面・API に出す文書の要約（行が無ければ None）。"""
    if row is None:
        return None
    current = now or datetime.now(UTC)
    limit = timedelta(seconds=_timeout_seconds(settings) + _GENERATING_GRACE_SECONDS)
    generating = row.generating_since is not None and current - row.generating_since < limit
    status = row.status
    failure_code = row.failure_code
    if status == STATUS_PENDING and not generating:
        # 作り始めの印だけが残った（process が途中で終わった）。作り直せる失敗として出す。
        status, failure_code = STATUS_FAILED, FAILURE_INTERRUPTED
    return DocumentContentSummary(
        status={STATUS_READY: "ready", STATUS_FAILED: "failed"}.get(status, "pending"),
        generating=generating,
        text=row.summary_text if status == STATUS_READY else None,
        topics=list(row.topics) if status == STATUS_READY else [],
        input_truncated=row.input_truncated if status == STATUS_READY else False,
        failure_code=failure_code,
        generated_at=row.generated_at if status == STATUS_READY else None,
    )


async def _load_extraction(oracle: Any, document_id: str, target: SummaryTarget) -> SummarySource:
    row = await oracle.get_document_extraction_artifact(
        document_id=document_id, extraction_recipe_id=target.extraction_recipe_id
    )
    payload = (row or {}).get("extraction_json")
    if isinstance(payload, str) and payload.strip():
        payload = json.loads(payload)
    if not isinstance(payload, Mapping) or not payload:
        raise SummaryUnavailable(FAILURE_SOURCE_MISSING)
    try:
        extraction = StructuredExtraction.model_validate(payload)
    except ValidationError as exc:
        raise SummaryUnavailable(FAILURE_SOURCE_MISSING) from exc
    return summary_source(extraction)


async def _summarize(
    vlm: _TextModel, settings: object, prompt: str, source: SummarySource, max_chars: int
) -> DocumentSummaryOutput:
    async def once(attempt: Attempt) -> DocumentSummaryOutput:
        # 入力が大きすぎる（context の長さの超過）ときは、予算を半分にして作り直す。
        text = build_summary_input(source, max_chars=max(1, max_chars >> attempt.adaptation)).text
        with sdk_retries_disabled():
            raw = await async_call_with_model_purpose(
                "document.summarize",
                vlm.generate,
                "",
                text,
                system_prompt=prompt,
                response_schema=DOCUMENT_SUMMARY_RESPONSE_SCHEMA,
                response_schema_name="document_summary",
            )
        return parse_summary_output(raw)

    caller = make_ai_caller(settings, "document_summary")
    try:
        return await caller.call(once, label="document_summary", adaptive=True)
    except BoundedCallError as exc:
        code = FAILURE_INVALID_OUTPUT if exc.kind == ErrorKind.INVALID_OUTPUT else FAILURE_MODEL
        raise SummaryUnavailable(code) from exc


async def generate_document_summary(
    document_id: str,
    *,
    oracle: Any,
    settings: Any,
    vlm: _TextModel,
    genai: _Embedder,
    store: DocumentSummaryStore | None = None,
    recipe_id: str | None = None,
    force: bool = False,
    load_prompt: Callable[[], Awaitable[str]] | None = None,
) -> DocumentSummaryRow | None:
    """文書の要約を作って保存する。無効・索引の無い文書は何もせず None。

    ``recipe_id`` は要約の元にする処理レシピ（取込の job のレシピ。無ければ最初のレシピ）。
    ``force`` は、入力が同じでも LLM を呼び直す（利用者の「作り直す」）。
    失敗は理由を保存してから ``SummaryUnavailable`` を出す。
    """
    if not getattr(settings, "rag_document_summary_enabled", True):
        return None
    summaries = store or DocumentSummaryStore(oracle)
    targets = await summaries.targets(document_id)
    if not targets:
        return None
    target = next((item for item in targets if item.recipe_id == recipe_id), targets[0])
    await summaries.mark_generating(document_id)
    try:
        async with asyncio.timeout(_timeout_seconds(settings)):
            detail = await oracle.get_document(document_id)
            if detail is None:
                raise SummaryUnavailable(FAILURE_SOURCE_MISSING)
            source = await _load_extraction(oracle, document_id, target)
            if not source.body:
                raise SummaryUnavailable(FAILURE_NO_CONTENT)
            max_chars = int(getattr(settings, "rag_document_summary_max_input_chars", 16000))
            summary_input = build_summary_input(source, max_chars=max_chars)
            prompt = await load_prompt() if load_prompt is not None else DOCUMENT_SUMMARY_PROMPT
            input_hash = summary_input_hash(prompt, summary_input.text)
            saved = await summaries.get(document_id)
            if (
                not force
                and saved is not None
                and saved.status == STATUS_READY
                and saved.input_hash == input_hash
                and saved.summary_text
            ):
                summary, topics = saved.summary_text, list(saved.topics)
            else:
                output = await _summarize(vlm, settings, prompt, source, max_chars)
                if not output.summary:
                    raise SummaryUnavailable(FAILURE_NO_CONTENT)
                summary, topics = output.summary, output.topics
            chunk = summary_chunk(
                summary,
                topics,
                document_title=str(detail.file_name or ""),
                context_header_enabled=bool(
                    getattr(settings, "rag_chunk_context_header_enabled", True)
                ),
                truncated=summary_input.truncated,
            )
            try:
                vectors = await genai.embed([_embedding_input(chunk)])
                await summaries.save_ready(
                    document_id,
                    chunk_set_ids=[item.chunk_set_id for item in targets],
                    summary_text=summary,
                    topics=topics,
                    input_hash=input_hash,
                    input_truncated=summary_input.truncated,
                    input_chars=summary_input.body_chars,
                    source_chars=summary_input.source_chars,
                    chunk=chunk,
                    embedding=vectors[0],
                )
            except SummaryUnavailable:
                raise
            except Exception as exc:
                raise SummaryUnavailable(FAILURE_INDEX) from exc
    except TimeoutError as exc:
        await summaries.save_failed(
            document_id, chunk_set_id=target.chunk_set_id, code=FAILURE_TIMEOUT
        )
        raise SummaryUnavailable(FAILURE_TIMEOUT) from exc
    except SummaryUnavailable as exc:
        await summaries.save_failed(document_id, chunk_set_id=target.chunk_set_id, code=exc.code)
        raise
    except Exception as exc:
        await summaries.save_failed(
            document_id, chunk_set_id=target.chunk_set_id, code=FAILURE_MODEL
        )
        raise SummaryUnavailable(FAILURE_MODEL) from exc
    return await summaries.get(document_id)


async def refresh_document_summary(
    document_id: str,
    *,
    oracle: Any,
    settings: Any,
    reason: str,
    recipe_id: str | None = None,
    force: bool = False,
) -> bool:
    """文書の要約を作る。失敗しても呼び出し元を止めず、ログに残して False を返す。"""

    async def load_prompt() -> str:
        saved = await oracle.answer_prompt_overrides()
        return str(saved.get(DOCUMENT_SUMMARY_PROMPT_KEY) or DOCUMENT_SUMMARY_PROMPT)

    try:
        await generate_document_summary(
            document_id,
            oracle=oracle,
            settings=settings,
            vlm=OciEnterpriseAiClient(settings=settings),
            genai=OciGenAiClient(settings=settings),
            recipe_id=recipe_id,
            force=force,
            load_prompt=load_prompt,
        )
    except SummaryUnavailable as exc:
        logger.warning(
            "document_summary_failed",
            extra={"document_id": document_id, "reason": reason, "failure_code": exc.code},
            exc_info=exc.__cause__ is not None and exc.code not in {FAILURE_NO_CONTENT},
        )
        return False
    except Exception:  # noqa: BLE001 - 要約は補助。取込・呼び出し元を止めない。
        logger.warning(
            "document_summary_failed",
            extra={"document_id": document_id, "reason": reason},
            exc_info=True,
        )
        return False
    return True
