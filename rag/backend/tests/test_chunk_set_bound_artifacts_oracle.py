"""章節の対応と準備済みファイルは chunk_set の版に属する（#4166。実 Oracle）。"""

from uuid import uuid4

import pytest
from rag_parser_core.preprocess import SourceDerivation

from app.clients.chunk_set_retention_store import ChunkSetRetentionStore
from app.clients.oracle import OracleClient
from app.clients.section_map_store import SectionMapStore
from app.rag.chunking import Chunk
from app.rag.document_crop import chunk_set_prepared_artifact
from app.rag.ingestion import _extraction_with_source_derivation
from app.rag.section_chunk_map import MapChunk, SectionChunkMap, build_section_chunk_map
from app.rag.section_map_service import rebuild_document_section_map, section_outline
from app.schemas.document import FileStatus
from app.schemas.extraction import StructuredExtraction


async def _new_document(client: OracleClient, name: str) -> tuple[str, str]:
    document = await client.create_document(
        file_name=f"synthetic-{name}.txt",
        object_storage_path=f"synthetic-{name}-{uuid4().hex[:8]}.txt",
        content_type="text/plain",
    )
    recipe = await client.ensure_default_document_recipe(document.id)
    return document.id, str(recipe["recipe_id"])


async def _chunk_set(
    client: OracleClient,
    document_id: str,
    recipe_id: str,
    chunk_set_id: str,
    extraction_id: str | None,
    *,
    derivation: SourceDerivation | None = None,
) -> str:
    """索引済みの chunk_set を作り、chunk の id を返す（active にはしない）。"""
    if extraction_id is not None:
        extraction = StructuredExtraction(raw_text=chunk_set_id)
        if derivation is not None:
            extraction = _extraction_with_source_derivation(extraction, derivation)
        await client.upsert_document_extraction_artifact(
            document_id=document_id,
            extraction_recipe_id=extraction_id,
            source_sha256="a" * 64,
            extraction=extraction.to_document_payload(),
            status="materialized",
        )
    await client.upsert_chunk_set(
        chunk_set_id=chunk_set_id,
        document_id=document_id,
        recipe_id=recipe_id,
        extraction_recipe_id=extraction_id,
    )
    await client.save_chunk_preview(
        document_id,
        StructuredExtraction(raw_text=chunk_set_id),
        [Chunk(index=0, text=chunk_set_id, start_offset=0, end_offset=len(chunk_set_id))],
        chunk_set_id=chunk_set_id,
    )
    await client.mark_chunk_set_indexed(chunk_set_id=chunk_set_id, chunk_count=1, vector_count=0)
    [chunk] = await client.list_chunk_set_chunks(chunk_set_id)
    return chunk.chunk_id


async def _map_rows(client: OracleClient, document_id: str) -> dict[str, tuple[int, int]]:
    """chunk_set ごとの (章節の行数, chunk の対応の行数)。"""
    sections = await client._fetch_all(
        """
        SELECT chunk_set_id, COUNT(*) AS cnt FROM rag_chunk_set_sections
        WHERE document_id = :document_id GROUP BY chunk_set_id
        """,
        {"document_id": document_id},
    )
    links = await client._fetch_all(
        """
        SELECT chunk_set_id, COUNT(*) AS cnt FROM rag_section_chunks
        WHERE document_id = :document_id GROUP BY chunk_set_id
        """,
        {"document_id": document_id},
    )
    link_counts = {str(row["chunk_set_id"]): int(str(row["cnt"])) for row in links}
    return {
        str(row["chunk_set_id"]): (
            int(str(row["cnt"])),
            link_counts.get(str(row["chunk_set_id"]), 0),
        )
        for row in sections
    }


def _map_of(chunk_set_id: str, chunk_id: str) -> SectionChunkMap:
    return build_section_chunk_map(
        chunk_set_id,
        [],
        [
            MapChunk(
                chunk_id=chunk_id,
                chunk_index=0,
                page_start=None,
                page_end=None,
                element_ids=(),
                section_path=(),
                head=chunk_set_id,
                chars=len(chunk_set_id),
            )
        ],
        anchors={},
        root_title="合成の文書",
    )


@pytest.mark.usefixtures("oracle_db")
async def test_rebuild_keeps_rows_of_chunk_sets_outside_the_target() -> None:
    """有効でない版の対応は作り直しで残り、読む側は有効な版だけを見る。"""
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "section-keep")
    suffix = uuid4().hex[:12]
    active_set, retired_set = f"active_{suffix}", f"retired_{suffix}"
    await _chunk_set(client, document_id, recipe_id, active_set, f"ex_a_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=active_set,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=f"ex_a_{suffix}",
    )
    # 切替の後に残した旧版に当たる chunk_set（#4168 の前提）。有効ではなく、対応だけ持つ。
    retired_chunk = await _chunk_set(client, document_id, recipe_id, retired_set, f"ex_r_{suffix}")
    store = SectionMapStore(client)
    await store.replace_chunk_set_section_maps(document_id, [_map_of(retired_set, retired_chunk)])

    maps = await rebuild_document_section_map(document_id, oracle=client)

    assert maps is not None and [item.chunk_set_id for item in maps] == [active_set]
    rows = await _map_rows(client, document_id)
    assert rows[retired_set] == (1, 1)
    assert rows[active_set][0] >= 1
    assert rows[active_set][1] == 1
    # 読む側は今までどおり有効な chunk_set だけを見る。
    outline = await section_outline(document_id, oracle=client)
    assert outline is not None and outline.chunk_set.chunk_set_id == active_set
    # 作り直しを繰り返しても行は増えない（渡した chunk_set は置き換える）。
    await rebuild_document_section_map(document_id, oracle=client)
    assert await _map_rows(client, document_id) == rows
    # 空の置き換えは何も消さない。
    await store.replace_chunk_set_section_maps(document_id, [])
    assert await _map_rows(client, document_id) == rows


@pytest.mark.usefixtures("oracle_db")
async def test_chunk_set_removal_removes_its_section_rows() -> None:
    """chunk_set の行を消す処理は、その章節の行も消す（作り直しは他の版の行を消さない）。"""
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "section-gc")
    suffix = uuid4().hex[:12]
    old_set, new_set = f"old_{suffix}", f"new_{suffix}"
    await _chunk_set(client, document_id, recipe_id, old_set, f"ex_o_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=old_set,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=f"ex_o_{suffix}",
    )
    await rebuild_document_section_map(document_id, oracle=client)
    assert set(await _map_rows(client, document_id)) == {old_set}

    await _chunk_set(client, document_id, recipe_id, new_set, f"ex_n_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=new_set,
        expected_base_chunk_set_id=old_set,
        extraction_recipe_id=f"ex_n_{suffix}",
    )
    # 切替は旧版の行を消さない。清掃が chunk_set と一緒に消す（#4168）。
    assert set(await _map_rows(client, document_id)) == {old_set}
    await rebuild_document_section_map(document_id, oracle=client)
    assert set(await _map_rows(client, document_id)) == {old_set, new_set}
    await ChunkSetRetentionStore(client).expire_recipe(
        recipe_id, retention_days=0, retained_versions=0, canonical_prefix="artifacts/canonical"
    )
    assert set(await _map_rows(client, document_id)) == {new_set}

    # レシピの削除でも残らない。
    second = await client.create_document_recipe(document_id)
    second_id = str(second["recipe_id"])
    second_set = f"second_{suffix}"
    await _chunk_set(client, document_id, second_id, second_set, f"ex_s_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=second_id,
        chunk_set_id=second_set,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=f"ex_s_{suffix}",
    )
    await rebuild_document_section_map(document_id, oracle=client)
    assert set(await _map_rows(client, document_id)) == {new_set, second_set}
    await client.delete_document_recipe(document_id, second_id)
    assert set(await _map_rows(client, document_id)) == {new_set}


@pytest.mark.usefixtures("oracle_db")
async def test_document_resets_do_not_delete_served_output() -> None:
    """検索に出している版がある文書の初期化は何も消さない。無ければ章節の対応まで消す。"""
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "reset-guard")
    suffix = uuid4().hex[:12]
    served_set = f"served_{suffix}"
    chunk_id = await _chunk_set(client, document_id, recipe_id, served_set, f"ex_{suffix}")
    await client.activate_recipe_chunk_set(
        recipe_id=recipe_id,
        chunk_set_id=served_set,
        expected_base_chunk_set_id=None,
        extraction_recipe_id=f"ex_{suffix}",
    )
    await rebuild_document_section_map(document_id, oracle=client)
    before = await _map_rows(client, document_id)

    for reset in (
        client.reset_document_ingestion_outputs,
        client.reset_document_chunk_outputs,
        client.reset_document_index_outputs,
    ):
        detail = await reset(document_id)
        assert detail.status == FileStatus.INDEXED
        assert await client.retrievable_chunk(document_id, chunk_id) is not None
        assert await _map_rows(client, document_id) == before

    # 検索に出している版が無い文書（候補だけ）は今までどおり初期化する。
    other_id, other_recipe = await _new_document(client, "reset-candidate")
    candidate = f"candidate_{suffix}"
    candidate_chunk = await _chunk_set(client, other_id, other_recipe, candidate, f"ex_c_{suffix}")
    store = SectionMapStore(client)
    await store.replace_chunk_set_section_maps(other_id, [_map_of(candidate, candidate_chunk)])
    assert set(await _map_rows(client, other_id)) == {candidate}
    reset_detail = await client.reset_document_ingestion_outputs(other_id)
    assert reset_detail.status == FileStatus.UPLOADED
    assert await _map_rows(client, other_id) == {}
    assert await client.list_chunk_set_chunks(candidate) == []
    assert (
        await client.get_document_extraction_artifact(
            document_id=other_id, extraction_recipe_id=f"ex_c_{suffix}"
        )
        is None
    )


@pytest.mark.usefixtures("oracle_db")
async def test_chunk_set_source_derivation_reads_the_file_of_the_version() -> None:
    """抽出の系譜の読み取りが実 Oracle で通り、版ごとのファイルを返す。"""
    client = OracleClient()
    document_id, recipe_id = await _new_document(client, "derivation")
    suffix = uuid4().hex[:12]
    derivation = SourceDerivation(
        derivation_id=f"dv_{suffix}",
        preprocess_profile="office_to_pdf",
        converted=True,
        converter_name="stub",
        source_content_type="application/octet-stream",
        source_sha256="b" * 64,
        derived_object_path=f"artifacts/canonical/{document_id}/trace-a/canonical.pdf",
        derived_content_type="application/pdf",
        derived_sha256="c" * 64,
        page_map={"1": 3},
    )
    with_lineage, without_lineage, no_extraction = (
        f"lineage_{suffix}",
        f"plain_{suffix}",
        f"none_{suffix}",
    )
    await _chunk_set(
        client, document_id, recipe_id, with_lineage, f"ex_l_{suffix}", derivation=derivation
    )
    await _chunk_set(client, document_id, recipe_id, without_lineage, f"ex_p_{suffix}")
    await _chunk_set(client, document_id, recipe_id, no_extraction, None)

    raw = await client.chunk_set_source_derivation(
        document_id=document_id, chunk_set_id=with_lineage
    )
    assert raw is not None
    assert raw["derived_object_path"] == derivation.derived_object_path
    assert raw["page_map"] == {"1": 3}
    for chunk_set_id in (without_lineage, no_extraction, f"missing_{suffix}"):
        assert (
            await client.chunk_set_source_derivation(
                document_id=document_id, chunk_set_id=chunk_set_id
            )
            is None
        )

    detail = await client.get_document(document_id)
    assert detail is not None
    artifact = await chunk_set_prepared_artifact(client, detail, document_id, with_lineage)
    assert artifact is not None
    assert artifact.object_storage_path == derivation.derived_object_path
    assert artifact.derivation_id == derivation.derivation_id
    assert await chunk_set_prepared_artifact(client, detail, document_id, without_lineage) is None
