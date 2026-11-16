"""取込済みの文書の実体・別名索引だけを、保存済みの chunk から作り直す CLI（#5178）。

実体・別名索引（``rag_entities`` / ``rag_entity_aliases`` / ``rag_entity_chunks``）は取込の索引の
保存の工程で作る（``app.rag.ingestion`` の ``_save_entity_index_if_enabled``）。抽出の規則を直した
とき（#5167）や、全体の既定を変えたときに、取込済みの文書の索引だけを直すのに使う。

- 対象は検索に出している chunk_set（有効な ``INDEXED`` の版と、レシピの無い旧データの配信中の版）。
- chunk_set ごとに保存済みの chunk を読み、取込と同じ ``build_entity_index`` で作り直して、その
  chunk_set の実体の行を置き換える。分割・embedding・全文検索の索引は変えない（LLM も使わない）。
- 文書レシピの有効な設定（全体の既定 → 形式別の既定 → レシピの上書き）に従う。実体・別名索引を
  使わない chunk_set は、実体の行を消す（取込し直したときと同じ状態にする。残すと、レシピで使わ
  ないと決めた文書の古い行が検索の拡張に使われ続ける）。
- 同じ chunk と設定なら、何度実行しても同じ行になる（実体の ID は chunk_set と名前から決まる）。

    uv run python -m app.rag.entity_index_cli rebuild --all --dry-run   # 件数だけを数える
    uv run python -m app.rag.entity_index_cli rebuild --knowledge-base-id <KB の ID>
    uv run python -m app.rag.entity_index_cli rebuild --document-id <文書 ID>  # 複数指定できる
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Literal, Protocol

from pr_backend_core.logging import configure_cli_logging

from app.clients.entity_store import EntityRebuildTarget
from app.config import Settings
from app.rag.chunking import Chunk
from app.rag.entity_index import EntityIndex, EntityIndexOptions, build_entity_index
from app.rag.request_context import (
    current_audit_request_context,
    reset_audit_request_context,
    set_audit_request_context,
    with_system_access,
)

logger = logging.getLogger(__name__)

RebuildAction = Literal["rebuild", "clear"]


class EntityRebuildStore(Protocol):
    """作り直しに使う読み書き（``app.clients.entity_store.EntityStore``）。"""

    async def list_rebuild_targets(
        self, *, document_ids: Sequence[str] = (), knowledge_base_ids: Sequence[str] = ()
    ) -> list[EntityRebuildTarget]: ...

    async def chunk_set_source_chunks(self, chunk_set_id: str) -> list[Chunk]: ...

    async def extraction_title_texts(
        self, *, document_id: str, extraction_recipe_id: str | None
    ) -> list[str]: ...

    async def count_chunk_set_entities(self, chunk_set_id: str) -> int: ...

    async def replace_chunk_set_entity_index(
        self, document_id: str, chunk_set_id: str, index: EntityIndex
    ) -> None: ...

    async def delete_chunk_set_entity_index(self, chunk_set_id: str) -> None: ...


SettingsResolver = Callable[[EntityRebuildTarget], Settings]


@dataclass
class ChunkSetResult:
    document_id: str
    chunk_set_id: str
    action: RebuildAction
    chunks: int = 0
    entities_before: int = 0
    entities: int = 0
    aliases: int = 0
    links: int = 0


@dataclass
class RebuildSummary:
    dry_run: bool
    chunk_sets: int = 0
    rebuilt: int = 0
    cleared: int = 0
    entities_before: int = 0
    entities: int = 0
    aliases: int = 0
    links: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    details: list[ChunkSetResult] = field(default_factory=list)

    def add(self, result: ChunkSetResult) -> None:
        self.details.append(result)
        if result.action == "rebuild":
            self.rebuilt += 1
        else:
            self.cleared += 1
        self.entities_before += result.entities_before
        self.entities += result.entities
        self.aliases += result.aliases
        self.links += result.links


async def rebuild_entity_indexes(
    store: EntityRebuildStore,
    resolve_settings: SettingsResolver,
    *,
    dry_run: bool,
    document_ids: Sequence[str] = (),
    knowledge_base_ids: Sequence[str] = (),
) -> RebuildSummary:
    """対象の chunk_set の実体・別名索引を作り直す（``dry_run`` は書き込まずに件数だけを数える）。

    ``document_ids`` と ``knowledge_base_ids`` が両方とも空なら全件。1 つの chunk_set の失敗は
    ``failed`` に記録して、残りを続ける。
    """
    summary = RebuildSummary(dry_run=dry_run)
    targets = await store.list_rebuild_targets(
        document_ids=document_ids, knowledge_base_ids=knowledge_base_ids
    )
    summary.chunk_sets = len(targets)
    for target in targets:
        # 実体の行の tenant は、文書の tenant にする（取込の要求の文脈と同じ値）。
        token = set_audit_request_context(
            replace(current_audit_request_context(), tenant_id_hash=target.tenant_id_hash)
        )
        try:
            summary.add(await _rebuild_chunk_set(store, resolve_settings, target, dry_run=dry_run))
        except Exception as exc:  # noqa: BLE001 - 1 つの chunk_set の失敗で全体を止めない
            logger.warning(
                "entity_index_rebuild_failed",
                extra={
                    "document_id": target.document_id,
                    "chunk_set_id": target.chunk_set_id,
                    "error_type": type(exc).__name__,
                },
            )
            summary.failed.append(
                {
                    "document_id": target.document_id,
                    "chunk_set_id": target.chunk_set_id,
                    "error_type": type(exc).__name__,
                }
            )
        finally:
            reset_audit_request_context(token)
    return summary


async def _rebuild_chunk_set(
    store: EntityRebuildStore,
    resolve_settings: SettingsResolver,
    target: EntityRebuildTarget,
    *,
    dry_run: bool,
) -> ChunkSetResult:
    settings = resolve_settings(target)
    before = await store.count_chunk_set_entities(target.chunk_set_id)
    if not settings.rag_entity_index_enabled:
        if not dry_run:
            await store.delete_chunk_set_entity_index(target.chunk_set_id)
        return ChunkSetResult(
            document_id=target.document_id,
            chunk_set_id=target.chunk_set_id,
            action="clear",
            entities_before=before,
        )
    chunks = await store.chunk_set_source_chunks(target.chunk_set_id)
    title = "\n".join(
        await store.extraction_title_texts(
            document_id=target.document_id, extraction_recipe_id=target.extraction_recipe_id
        )
    )
    index = build_entity_index(
        document_id=target.document_id,
        chunks=chunks,
        chunk_set_id=target.chunk_set_id,
        document_title=title,
        options=EntityIndexOptions(
            name_columns=tuple(settings.rag_entity_name_columns),
            attribute_columns=tuple(settings.rag_entity_attribute_columns),
        ),
    )
    if not dry_run:
        await store.replace_chunk_set_entity_index(target.document_id, target.chunk_set_id, index)
    return ChunkSetResult(
        document_id=target.document_id,
        chunk_set_id=target.chunk_set_id,
        action="rebuild",
        chunks=len(chunks),
        entities_before=before,
        entities=len(index.entities),
        aliases=index.alias_count,
        links=len(index.links),
    )


def recipe_settings(target: EntityRebuildTarget) -> Settings:
    """chunk_set のレシピの有効な設定（取込と同じ: 全体の既定 → 形式別の既定 → レシピ）。"""
    from app.api.routes import documents as documents_route
    from app.rag.file_type_defaults import classify_file_type
    from app.schemas.document import DocumentProcessingConfig

    config = DocumentProcessingConfig.model_validate(target.processing_config or {})
    file_type = classify_file_type(target.file_name, target.content_type)
    return documents_route._merge_document_processing_config(config, file_type=file_type)[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "取込済みの文書の実体・別名索引だけを、保存済みの chunk から作り直します（#5178）。"
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    rebuild = subparsers.add_parser(
        "rebuild", help="検索に出している chunk_set の実体・別名索引を作り直します。"
    )
    rebuild.add_argument(
        "--document-id", action="append", default=[], help="対象の文書 ID（複数指定できます）。"
    )
    rebuild.add_argument(
        "--knowledge-base-id",
        action="append",
        default=[],
        help="対象のナレッジベース ID（所属する文書のすべて。複数指定できます）。",
    )
    rebuild.add_argument("--all", action="store_true", help="すべての文書を対象にします。")
    rebuild.add_argument(
        "--dry-run",
        action="store_true",
        help="書き込まず、対象と作り直した後の件数だけを数えます。",
    )
    rebuild.add_argument(
        "--details", action="store_true", help="chunk_set ごとの件数も出力します。"
    )
    return parser


async def _run(args: argparse.Namespace) -> RebuildSummary:
    from app.clients.entity_store import EntityStore
    from app.clients.oracle import OracleClient

    return await rebuild_entity_indexes(
        EntityStore(OracleClient()),
        recipe_settings,
        dry_run=args.dry_run,
        document_ids=args.document_id,
        knowledge_base_ids=args.knowledge_base_id,
    )


@with_system_access
def main(argv: Sequence[str] | None = None) -> int:
    configure_cli_logging("rag")
    args = build_parser().parse_args(argv)
    selected = bool(args.document_id or args.knowledge_base_id)
    if args.all == selected:
        print(
            "--all か、--document-id / --knowledge-base-id のどちらかを指定してください"
            "（両方は指定できません）。",
            file=sys.stderr,
        )
        return 2
    summary = asyncio.run(_run(args))
    payload = asdict(summary)
    if not args.details:
        payload.pop("details")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
