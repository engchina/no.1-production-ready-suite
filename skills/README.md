# 組み込みのスキル（Agent Skills）

Production Ready RAG・NL2SQL の MCP を使って調べるスキル。組み込みの Agent が読み、Claude Code・Codex などの外部の Agent にも入れられる（形式は [Agent Skills](https://agentskills.io) の `<name>/SKILL.md`）。

| スキル | 使う MCP サーバー | 使う場面 |
|---|---|---|
| [`business-rag-research`](./business-rag-research/SKILL.md)（業務 RAG 調査） | `rag` | 社内の規程・手順書・台帳を検索し、根拠（文書名と節・頁）を示して答える |
| [`structured-data-query`](./structured-data-query/SKILL.md)（構造化データ照会） | `nl2sql` | 業務データの件数・金額・一覧を SQL で照会する |
| [`rag-and-structured-data`](./rag-and-structured-data/SKILL.md)（資料とデータの横断調査） | `rag`・`nl2sql` | 資料と業務データの両方が要る調査 |

このディレクトリは Claude Code の plugin `production-ready-skills`（[`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json)）でもあり、repo 直下の [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json)（marketplace `production-ready-suite`）が配る。

## 外部の Agent（Claude Code）で使う

用意するもの:

- RAG・NL2SQL の画面の左下のアカウントのメニュー →「API キー」で発行した自分のキー（使える製品に RAG と NL2SQL を選ぶ）。キーの扱い・応答は [platform/docs/mcp-api-keys.md](../platform/docs/mcp-api-keys.md)。
- 接続先: `https://<host>/rag/api/mcp` と `https://<host>/nl2sql/api/mcp`（開発の dev server は `http://localhost:8000/api/mcp` と `http://localhost:8010/api/mcp`）。

### 1. スキルを入れる

```bash
claude plugin marketplace add https://github.com/engchina/no.1-production-ready-suite.git --sparse .claude-plugin skills
claude plugin install production-ready-skills@production-ready-suite
claude plugin list   # production-ready-skills@production-ready-suite が enabled
```

- Claude Code の中では `/plugin marketplace add …` と `/plugin install production-ready-skills@production-ready-suite`。入れた後に始めたセッションから使える（今のセッションは `/reload-plugins`）。
- `--sparse .claude-plugin skills` で clone を 2 つのディレクトリに絞る（1 MB 未満）。付けないと monorepo 全体（約 72 MB）を clone する。
- 1 行の `claude plugin install production-ready-skills --marketplace engchina/no.1-production-ready-suite` でも入る（monorepo 全体を clone する。`owner/repo` の形は SSH（`git@github.com:…`）で clone したので、SSH の鍵が無い環境では上の https の URL を使う）。
- スキルの名前には plugin の名前が前に付く（`production-ready-skills:business-rag-research`）。質問に合うスキルは Claude Code が `description` から選ぶので、名前を書かなくてよい。
- 更新は `claude plugin marketplace update production-ready-suite` の後に `claude plugin update production-ready-skills@production-ready-suite`。

### 2. MCP を設定する

スキルは MCP サーバーの名前を **`rag` と `nl2sql`** として書いているので、この名前で登録する（Claude Code ではツールが `mcp__rag__rag_search` などになる）。

```bash
export PR_API_KEY='prak_...'   # 発行したキー（シェルの起動ファイルや secret の管理に置き、repo に書かない）
claude mcp add --scope user --transport http rag https://<host>/rag/api/mcp \
  --header 'Authorization: Bearer ${PR_API_KEY}'
claude mcp add --scope user --transport http nl2sql https://<host>/nl2sql/api/mcp \
  --header 'Authorization: Bearer ${PR_API_KEY}'
claude mcp list   # rag・nl2sql が ✔ Connected
```

- `--header` は **単引用符** で渡す。`${PR_API_KEY}` のまま設定（`~/.claude.json`）に保存され、Claude Code が起動のたびに環境変数から展開する（キーの値を設定ファイルに書かない）。二重引用符だと、シェルが展開したキーの値がそのまま保存される。環境変数が無いと `claude mcp list` が `Missing environment variables: PR_API_KEY` と出す。
- 設定の JSON（プロジェクトの `.mcp.json`・`claude --mcp-config <file>`）で書くときも同じ形にする:

```json
{
  "mcpServers": {
    "rag": {
      "type": "http",
      "url": "https://<host>/rag/api/mcp",
      "headers": { "Authorization": "Bearer ${PR_API_KEY}" }
    },
    "nl2sql": {
      "type": "http",
      "url": "https://<host>/nl2sql/api/mcp",
      "headers": { "Authorization": "Bearer ${PR_API_KEY}" }
    }
  }
}
```

### 3. 質問する

```bash
export MCP_TOOL_TIMEOUT=600000   # MCP のツールの待ち時間（ミリ秒）。既定の 60 秒では rag_search が切れる
claude
```

非対話（`claude -p`）では、MCP のツールとスキルの使用を許可して渡す:

```bash
claude -p "経費の精算の申請期限はいつまでですか？期限を過ぎた場合の扱いも含めて、社内の規程で確かめて出典を示してください。" \
  --allowedTools "Skill,mcp__rag,mcp__nl2sql"
```

- 検索・回答プロファイル（RAG）・業務プロファイル（NL2SQL）は、質問に名前を書かなくてよい。スキルが推薦のツール（`rag_recommend_search_answer_profile`・`nl2sql_recommend_profile`）で質問に合うものを選ぶ。候補が違う資料に割れたときは、候補の名前を挙げてどれで調べるかを聞き返すので、選んで答える。決まっているときは名前を書くと確実（例「検索・回答プロファイル『社内規程』の資料で調べてください。…」）。

### つまずく所

| 症状 | 原因と対処 |
|---|---|
| `rag_search` が 60 秒で `The operation timed out.` | Claude Code の MCP のツールの待ち時間の既定が 60 秒。`rag_search`（回答を作る）は 1〜3 分かかることがあるので、`MCP_TOOL_TIMEOUT=600000` を付けて起動する |
| スキルは選ばれるが MCP のツールが呼ばれない・見つからない | MCP サーバーの名前が `rag` / `nl2sql` でない（`production-ready-rag` など）。名前を合わせて登録し直す |
| `claude -p` でツールが使われずに終わる | 非対話では許可の確認ができない。`--allowedTools "Skill,mcp__rag,mcp__nl2sql"` を渡す |
| 401 `SECURITY_API_KEY_INVALID` / 403 `SECURITY_API_KEY_PRODUCT_FORBIDDEN` | キーの誤り・失効・期限切れ、または使える製品にその製品が無い（[応答の一覧](../platform/docs/mcp-api-keys.md#応答)） |
| HTTPS で証明書のエラー | 配備の既定の自作の CA を信頼させる: `NODE_EXTRA_CA_CERTS=<ダウンロードした ca.crt>` を付けて Claude Code を起動する（CA は `https://<host>/platform/ca.crt`） |
| プロファイルを聞き返される・違うプロファイルで答える | 推薦の候補が違う資料に割れた・当たりが無い。候補から選ぶか、質問にプロファイルの名前を書く（`rag_recommend_search_answer_profile` は利用者が使えるプロファイルの資料を 1 回検索して決める） |
| スキルが出ない | `claude plugin list` で enabled か確かめ、新しいセッションを始める（今のセッションは `/reload-plugins`） |

開発の `*_AUTH_MODE=local` では、キーを付けない要求もローカル利用者として通る（`claude mcp list` の Connected だけではキーを確かめられない）。キーが使われたかは画面の「API キー」の最終利用日時で確かめる。

## Codex で使う

```bash
codex mcp add rag --url https://<host>/rag/api/mcp --bearer-token-env-var PR_API_KEY
codex mcp add nl2sql --url https://<host>/nl2sql/api/mcp --bearer-token-env-var PR_API_KEY
codex mcp list
```

キーは環境変数 `PR_API_KEY` から読む（`~/.codex/config.toml` には変数の名前だけを書く）。スキルは `skills/<name>/` をフォルダごと Codex のスキルのディレクトリに写す（置き場所は Codex の文書に従う）。

## スキルを変えるとき

- 書き方と検証は [agent/docs/agent-control-plane-design.md](../agent/docs/agent-control-plane-design.md)（`skills-ref` の検証は `agent/backend/tests/test_skill_md_spec.py`）。
- 外部の Agent へ配った後に内容を変えたら、[`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json) の `version` を上げる（Claude Code は `version` ごとに plugin を cache するため、上げないと `claude plugin update` で新しい内容が入らない）。
