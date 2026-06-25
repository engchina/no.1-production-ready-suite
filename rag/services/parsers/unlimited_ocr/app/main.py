"""Unlimited-OCR(GPU)parser マイクロサービス。

CUDA イメージ上で HuggingFace の baidu/Unlimited-OCR を transformers でロードして実 OCR
を行い、共通 contract の `StructuredExtraction` を返す。GPU/重い ML 依存は本 image に
隔離され、他 parser / backend に影響しない。
"""

from rag_parser_core.service import create_parse_app


app = create_parse_app(
    backend="unlimited_ocr",
    import_name="transformers",
    distribution_names=("transformers",),
    title="parser-unlimited-ocr",
)
