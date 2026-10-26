"""文書の検索の中の DB の検索の工程の名前（処理の経過の段。#3436・#3443）。

埋め込み・並べ替えと同じく span だけを足し（``observe_call("stage", 名前)``）、工程の記録（回答の記録の文・
``execution_steps``）には書かない。名前は利用者向けの名詞（画面にそのまま出る）。rag_engine の検索の実装
（``adapters/oracle/store.py``）と、検索を差し替える実行環境（backend の検索）が同じ名前を使う。
"""

VECTOR_SEARCH_STAGE = "ベクトル検索"
TEXT_SEARCH_STAGE = "全文検索"
CHUNK_POOL_LOAD_STAGE = "検索対象のチャンクの読み込み"
