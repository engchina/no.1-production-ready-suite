"""原本の形式で決まる解析エンジン(#2024)。

主の解析エンジン(既定 Docling)を切り替えなくても、形式の決まった原本はその形式の解析へ回す。
今は XML ツリー JSON(``rag_parser_core.xml_tree_json`` の対応するスキーマ)だけで、
``rag_parser_xml_tree_json_enabled``(全体の既定 ON。文書のレシピで上書きできる)が有効なときに
XML ツリー JSON の解析(parser-xml-tree-json)へ回す。
ほかの JSON は今までどおり(主の解析エンジンの判定に任せる)。

判定は原本の中身で行う(``source_profile.source_format`` か、解析に渡す bytes の判定)。backend は
解析そのものはせず、判定だけを行う(解析は parser のサービス)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from rag_parser_core.xml_tree_json import XML_TREE_JSON_BACKEND, XML_TREE_JSON_SOURCE_FORMAT

from app.config import Settings
from app.rag.source_profile import detect_source_format
from app.schemas.document import SourceProfile

XML_TREE_JSON_SERVICE_ID = "parser-xml-tree-json"
XML_TREE_JSON_LABEL = "XML ツリー JSON"


def xml_tree_json_enabled(settings: Settings) -> bool:
    return bool(getattr(settings, "rag_parser_xml_tree_json_enabled", True))


def source_format_parser_backend(
    settings: Settings,
    *,
    source_profile: SourceProfile | None,
    data: bytes | None = None,
    content_type: str = "",
) -> str | None:
    """形式で決まる解析エンジン(無ければ None。主の解析エンジンを使う)。

    ``data`` を渡すと、解析に渡す bytes で判定する(ファイル準備で変換した後の原本・以前に
    アップロードして ``source_format`` を持たない原本も判定できる)。渡さなければ
    ``source_profile.source_format`` だけで判定する(アップロード直後の判定)。
    """
    if not xml_tree_json_enabled(settings):
        return None
    if data is not None:
        extension = source_profile.extension if source_profile is not None else None
        effective_type = content_type or (
            source_profile.content_type if source_profile is not None else ""
        )
        source_format = detect_source_format(data, content_type=effective_type, extension=extension)
    else:
        source_format = source_profile.source_format if source_profile is not None else None
    return XML_TREE_JSON_BACKEND if source_format == XML_TREE_JSON_SOURCE_FORMAT else None


class _ChunkLike(Protocol):
    metadata: dict[str, str | int | float | bool | None]


def apply_xml_tree_source_urls(chunks: Sequence[_ChunkLike], settings: Settings) -> None:
    """XML ツリー JSON の chunk に原典の URL(``xml_tree_source_url``)を付ける(#2024)。

    ``rag_xml_tree_json_source_url_template`` の ``{document_id}`` を文書 ID に置き換える。ひな形が
    空、または https で始まらないときは付けない(引用カードの「原典を開く」は https だけを開く)。
    """
    template = str(getattr(settings, "rag_xml_tree_json_source_url_template", "") or "").strip()
    if not template.startswith("https://") or "{document_id}" not in template:
        return
    for chunk in chunks:
        document_id = chunk.metadata.get("xml_tree_document_id")
        if isinstance(document_id, str) and document_id.strip():
            chunk.metadata["xml_tree_source_url"] = template.replace(
                "{document_id}", document_id.strip()
            )
