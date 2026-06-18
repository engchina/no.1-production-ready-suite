"""Dots.OCR(GPU)parser マイクロサービス。

CUDA イメージ上で Dots.OCR の実 OCR を行い、共通 contract の `StructuredExtraction`
を返す。GPU 依存は本 image に隔離され、他 parser / backend に影響しない。
"""

from rag_parser_core.service import create_parse_app

app = create_parse_app(
    backend="dots_ocr",
    import_name="dots_ocr",
    distribution_names=("dots-ocr", "dots_ocr"),
    title="parser-dots-ocr",
)
