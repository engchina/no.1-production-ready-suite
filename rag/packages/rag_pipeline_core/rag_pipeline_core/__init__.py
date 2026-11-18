"""rag_pipeline_core: pipeline の段の決定論ロジック。

文書分割(chunking)・vector index・evaluation の設定の解決を、backend の中で実行する決定論
ロジックとして提供する(段のサービスへの委譲は持たない。#3218)。
``rag_parser_core`` と同じく軽量(依存は pydantic + rag_parser_core のみ)。
"""
