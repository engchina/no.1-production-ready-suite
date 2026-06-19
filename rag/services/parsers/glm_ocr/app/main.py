"""GLM-OCR(GPU)parser マイクロサービス。

CUDA イメージ上で HuggingFace の GLM-OCR(既定 zai-org/GLM-OCR)を transformers で
ロードして実 OCR を行い、共通 contract の `StructuredExtraction` を返す。GPU/重い ML
依存は本 image に隔離され、他 parser / backend に影響しない。

実 OCR の呼び出しは `rag_parser_core.registry._run_glm_ocr`。専用 pip package が無いため
readiness の version 検出は transformers を代理に使う。
"""

from rag_parser_core.service import create_parse_app

app = create_parse_app(
    backend="glm_ocr",
    import_name="transformers",
    distribution_names=("transformers",),
    title="parser-glm-ocr",
)
