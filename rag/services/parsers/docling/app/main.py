"""Docling parser マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、この image に導入された
docling で parse して `StructuredExtraction` を返す。docling のバージョンは本サービス
単独で upgrade でき、他 parser / backend に影響しない。
"""

from rag_parser_core.service import create_parse_app

app = create_parse_app(
    backend="docling",
    import_name="docling",
    distribution_names=("docling",),
    title="parser-docling",
)
