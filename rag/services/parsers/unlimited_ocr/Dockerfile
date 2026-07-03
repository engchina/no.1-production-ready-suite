# syntax=docker/dockerfile:1
# build context = リポジトリ root。
# Unlimited-OCR は公式 SGLang wheel を使うため、SGLang の CUDA image をベースにし、
# 同一コンテナ内で SGLang server と parser FastAPI を起動する。
FROM lmsysorg/sglang:v0.5.14

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface \
    UNLIMITED_OCR_RUNTIME=sglang \
    UNLIMITED_OCR_SGLANG_BASE_URL=http://127.0.0.1:10000/v1 \
    UNLIMITED_OCR_SGLANG_MODEL=Unlimited-OCR

RUN python3 -m pip install --no-cache-dir --force-reinstall --no-deps \
    "https://huggingface.co/baidu/Unlimited-OCR/resolve/main/wheel/sglang-0.0.0.dev11416+g92e8bb79e-py3-none-any.whl" \
    && python3 -m pip install --no-cache-dir \
    "kernels==0.11.7" \
    "pymupdf==1.27.2.2" \
    && mkdir -p /root/.cache

COPY packages/rag_parser_core /opt/rag_parser_core
RUN pip install --no-cache-dir "/opt/rag_parser_core[service]"

# モデルをビルド時に取得してイメージへ焼き込む(prod・配布向け。実行時 DL を排除)。
# dev は `--build-arg BAKE_MODELS=0` で省き named volume のキャッシュを使う。
# 別モデルは `--build-arg UNLIMITED_OCR_MODEL_ID=...`、ミラーは HF_ENDPOINT build-arg、token は runtime env のみ(image 履歴へ残さない)。
ARG UNLIMITED_OCR_MODEL_ID=baidu/Unlimited-OCR
ENV UNLIMITED_OCR_MODEL_ID=${UNLIMITED_OCR_MODEL_ID}
ARG BAKE_MODELS=1
ARG HF_ENDPOINT=
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        HF_ENDPOINT="$HF_ENDPOINT" \
        python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${UNLIMITED_OCR_MODEL_ID}')"; \
    fi

WORKDIR /app
COPY services/parsers/unlimited_ocr/app ./app
COPY services/parsers/unlimited_ocr/entrypoint.sh /usr/local/bin/unlimited-ocr-entrypoint.sh
RUN chmod +x /usr/local/bin/unlimited-ocr-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"
ENTRYPOINT ["/usr/local/bin/unlimited-ocr-entrypoint.sh"]
