"""製品の LLM 要求の計数器に、同じ版の原文・事前参照・近接候補を接続する。"""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import astuple
from itertools import islice
from typing import Any

from pr_backend_core.bounded_calls import Attempt, BoundedCaller
from pr_backend_core.generation_input import GENERATION_INPUT_LIMIT
from pr_backend_core.observability.model_purpose import async_call_with_model_purpose
from rag_parser_core.oci_enterprise_ai import (
    OciEnterpriseAiConfig,
    measure_llm_request,
    sdk_retries_disabled,
)

from app.rag.wiki_compiler import WikiTextModel, compile_pages
from app.rag.wiki_large_unit import compile_large_unit
from app.rag.wiki_models import WikiPage, WikiUnit, fingerprint
from app.rag.wiki_packet import SourceContext, WikiInputTooLarge, WikiRequest, prepare_request


class WikiRequestBuilder:
    def __init__(
        self,
        units: Sequence[WikiUnit],
        *,
        config: OciEnterpriseAiConfig,
        system_prompt: str,
        sources: Mapping[str, SourceContext],
        related: Mapping[str, Sequence[str]],
        max_source_chars: int,
        batch_size: int,
    ):
        self.config = config
        self.system_prompt = system_prompt
        self.sources = sources
        self.related = related
        self.max_source_chars = max_source_chars
        self.batch_size = batch_size
        self.token_limit = min(GENERATION_INPUT_LIMIT, config.text_max_input_tokens)
        self.scopes: dict[tuple[str, str], dict[str, WikiUnit]] = defaultdict(dict)
        self.indices: dict[str, int] = {}
        for unit in units:
            group = self.scopes[unit.document_id, unit.chunk_set_id]
            if unit.unit_id in self.indices:
                raise ValueError("原文単位が重複しています。")
            self.indices[unit.unit_id] = len(group)
            group[unit.unit_id] = unit
        self.order = {key: tuple(group) for key, group in self.scopes.items()}
        self.preparation_fingerprint = fingerprint(
            [
                "scoped-wiki-input-v1",
                [(uid, astuple(sources[uid])) for uid in sorted(sources)],
                [(uid, list(related[uid])) for uid in sorted(related)],
            ]
        )

    def __call__(self, targets: Sequence[WikiUnit], include_optional: bool) -> WikiRequest:
        first = targets[0]
        key = (first.document_id, first.chunk_set_id)
        scope = self.scopes[key]
        order = self.order[key]
        requested = {u.unit_id for u in targets}

        def candidates() -> Iterator[WikiUnit]:
            seen = set(requested)
            for unit in targets:
                for uid in self.related.get(unit.unit_id, ()):
                    if uid in scope and uid not in seen:
                        seen.add(uid)
                        yield scope[uid]
            left = min(self.indices[uid] for uid in requested)
            right = max(self.indices[uid] for uid in requested)
            for distance in range(1, self.batch_size + 1):
                for index in (left - distance, right + distance):
                    if 0 <= index < len(order) and order[index] not in seen:
                        seen.add(order[index])
                        yield scope[order[index]]

        extra = list(islice(candidates(), self.batch_size)) if include_optional else []
        return prepare_request(
            targets,
            extra,
            scope=scope,
            source=self.sources,
            measure=self.measure,
            max_input_tokens=self.token_limit,
            max_source_chars=self.max_source_chars,
        )

    def measure(self, text: str, schema: dict[str, Any]) -> int:
        return measure_llm_request(
            self.config,
            "",
            text,
            system_prompt=self.system_prompt,
            response_schema=schema,
            response_schema_name="wiki_contexts",
        ).estimated_tokens

    async def compile(
        self,
        units: Sequence[WikiUnit],
        *,
        model: WikiTextModel,
        saved: Sequence[WikiPage],
        authorize: Callable[[], Awaitable[None]],
        checkpoint: Callable[[Sequence[WikiPage]], Awaitable[None]],
        caller: BoundedCaller,
    ) -> list[WikiPage]:
        async def oversized(
            unit: WikiUnit, version: str, prior: int | None, max_chars: int
        ) -> WikiPage:
            source = self.sources.get(unit.unit_id)
            if source is None or not source.paragraphs:
                raise WikiInputTooLarge(
                    f"原文単位 {unit.unit_id} [0,{len(unit.text)}) の段落の位置を確認できません。"
                )
            scope = self.scopes[unit.document_id, unit.chunk_set_id]

            async def authorized_scope() -> Mapping[str, WikiUnit]:
                await authorize()
                return scope

            async def generate(req: WikiRequest) -> str:
                async def once(attempt: Attempt) -> str:
                    await authorize()
                    with sdk_retries_disabled():
                        return await async_call_with_model_purpose(
                            "document.extract",
                            model.generate,
                            "",
                            req.text,
                            system_prompt=self.system_prompt,
                            response_schema=req.schema.model_json_schema(),
                            response_schema_name="wiki_contexts",
                        )

                return await caller.call(once, label="wiki_build")

            related = [
                scope[uid]
                for uid in self.related.get(unit.unit_id, ())
                if uid in scope and uid != unit.unit_id
            ][: self.batch_size]
            page, _, overflow = await compile_large_unit(
                unit,
                source,
                source.paragraphs,
                limit=min(self.token_limit, prior - 1) if prior else self.token_limit,
                max_source_chars=max_chars,
                measure=self.measure,
                generate=generate,
                authorize=authorized_scope,
                input_fingerprint=version,
                related=related,
            )
            if overflow:
                raise ValueError("同じ原文の確認済み依存先を一件の案内に保持できません。")
            return page

        return await compile_pages(
            units,
            model=model,
            max_input_chars=self.max_source_chars,
            batch_size=self.batch_size,
            saved=saved,
            authorize=authorize,
            checkpoint=checkpoint,
            system_prompt=self.system_prompt,
            caller=caller,
            prepare=self,
            preparation_fingerprint=self.preparation_fingerprint,
            oversized_unit=oversized,
        )
