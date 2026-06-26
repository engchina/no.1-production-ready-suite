# syntax=docker/dockerfile:1
# build context = リポジトリ root。
# GLM-OCR は公式 self-host(vLLM OpenAI 互換)で配信する。公式 vLLM イメージ
# (CUDA + torch + vLLM + transformers 同梱)をベースにし、**同一コンテナ内**で
# vLLM サーバと parser FastAPI を起動する。これにより従来分離していた
# `parser-glm-ocr` と `parser-glm-ocr-vllm` の 2 イメージを 1 つへ統合する。
FROM vllm/vllm-openai:v0.22.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface \
    # parser → 同一コンテナ内の vLLM(localhost)を指す。
    GLM_OCR_RUNTIME=vllm \
    GLM_OCR_VLLM_BASE_URL=http://127.0.0.1:8080/v1 \
    GLM_OCR_VLLM_MODEL=glm-ocr

# parser app の HTTP サーバ依存(FastAPI/gunicorn 等)だけを base の Python へ追加する。
# torch / transformers / vLLM は base イメージに同梱済みなので再インストールしない。
COPY packages/rag_parser_core /opt/rag_parser_core
RUN pip install --no-cache-dir "/opt/rag_parser_core[service]"

WORKDIR /app
COPY services/parsers/glm_ocr/app ./app
COPY services/parsers/glm_ocr/entrypoint.sh /usr/local/bin/glm-ocr-entrypoint.sh
RUN chmod +x /usr/local/bin/glm-ocr-entrypoint.sh

EXPOSE 8000
# parser :8000/health のみ確認する。vLLM の起動状況は parser の runtime_health が
# 別フィールドとして反映するため、ここでは parser プロセスの生存だけを見る。
# モデルロードが遅いため start-period を長めに取る。
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
ENTRYPOINT ["/usr/local/bin/glm-ocr-entrypoint.sh"]
