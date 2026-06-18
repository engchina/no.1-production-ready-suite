"""MinerU(GPU)parser マイクロサービス。

CUDA イメージ上で MinerU の実 OCR/レイアウト解析を行い、共通 contract の
`StructuredExtraction` を返す。GPU 依存は本 image に隔離され、他 parser / backend に
影響しない。
"""

from rag_parser_core.service import create_parse_app

app = create_parse_app(
    backend="mineru",
    import_name="mineru",
    distribution_names=("mineru", "magic-pdf"),
    title="parser-mineru",
)
