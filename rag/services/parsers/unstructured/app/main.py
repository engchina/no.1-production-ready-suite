"""Unstructured parser マイクロサービス。

共有 contract(rag_parser_core)の app factory を使い、この image に導入された
unstructured の partition で parse して `StructuredExtraction` を返す。

モデルは配備で取得・検証し(rag_parser_core.unstructured_models)、解析の要求の時には取得しない。
spaCy のモデルの実行時の取得は明確なエラーに置き換え、HF のモデルは unit の HF_HUB_OFFLINE=1 で
キャッシュだけを読む。モデルが揃っていなければ /health は degraded を返す。
"""

from rag_parser_core.service import create_parse_app
from rag_parser_core.unstructured_models import forbid_runtime_downloads, models_ready

forbid_runtime_downloads()

app = create_parse_app(
    backend="unstructured",
    import_name="unstructured",
    distribution_names=("unstructured",),
    runtime_health=models_ready,
    title="parser-unstructured",
)
