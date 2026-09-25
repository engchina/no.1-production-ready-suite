"""Marker parser マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、この image に導入された
marker(LLM 補正は無効)で PDF/画像を parse して `StructuredExtraction` を返す。
"""

from rag_parser_core.service import create_parse_app

app = create_parse_app(
    backend="marker",
    import_name="marker",
    distribution_names=("marker-pdf", "marker"),
    title="parser-marker",
)
