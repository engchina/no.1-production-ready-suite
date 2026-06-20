# 前処理: URL→Markdown(`url_to_markdown`)

原本(URL を 1 行 1 件で並べたテキスト/`.url`/`.txt`)を受け取り、各ページの本文を取得して
boilerplate を除去した **クリーン Markdown** へ変換する前処理マイクロサービス。Firecrawl 的な
「Web ページ→LLM 取込用 Markdown」を **ローカル OSS(trafilatura)** だけで実現し、外部 SaaS は
一切呼ばない(確定スタック非抵触)。

| 項目 | 値 |
|---|---|
| profile | `url_to_markdown` |
| 主依存 | httpx + trafilatura(純ローカル) |
| 既定 URL | `http://preprocess-url-to-markdown:8000` |
| dev port | 8014 |
| profile 種別 | CPU(dev は uv プロセス) |

## セキュリティ(SSRF 対策)

- `http` / `https` スキームのみ許可。`file://` 等は拒否。
- 名前解決した IP が loopback / private / link-local / reserved / multicast の場合は取得しない。
- URL 件数(20)・取得サイズ(10 MiB/URL)に上限。

## 契約

- `POST /convert`(`rag_parser_core.ConvertResponse`)。出力は `text/markdown`、各ページを
  `## Source: <url>` 見出しで連結。取得 0 件・全件失敗・未対応 profile は **passthrough** へ縮退。
- `GET /health` → trafilatura 可用性で `ok` / `degraded`。

## 起動

```bash
# dev(ホストの uv プロセス)
uv run --directory services/preprocess/url_to_markdown uvicorn app.main:app --port 8014

# Docker(build context = リポジトリ root)
docker compose up preprocess-url-to-markdown
```
