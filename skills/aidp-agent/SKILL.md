---
name: aidp-agent
description: "Production Ready Agent の業務 Agent が、Oracle AI Data Platform に配備した Agent（接続 aidp）にテキストの依頼を 1 通送り、その応答で答える。依頼が、ツール send_message の説明にある相手の Agent の扱う内容に当たるときに、このスキルを使う。呼び出しは 1 回ごとに独立で、ファイル・画像は受け渡しできない。"
compatibility: "Production Ready Agent の業務 Agent（組み込みの実行環境）で使う。Oracle AI Data Platform を配備した構成で、接続 aidp に Agent の endpoint の URL を設定していること。ツール send_message は Agent の中の接続（A2A）のツールで、MCP サーバーでは提供しないので、外部の Agent（Claude Code・Codex など）では使えない。"
license: MIT
metadata:
  production-ready-display-name: "AI Data Platform の Agent"
  production-ready-mcp-tools: "aidp:send_message"
  production-ready-tags: "aidp a2a agent"
---
Oracle AI Data Platform に配備した Agent（接続 `aidp`）に、`send_message` でテキストの依頼を送り、その応答で利用者に答える。相手の Agent が何を扱うかは、`send_message` の説明にある。

## Step 1: 依頼を送るかを決める

- 利用者の依頼が、`send_message` の説明にある相手の Agent の扱う内容に当たるときに送る。
- 当たらないとき（あいさつ・相手の Agent が扱わない内容）は送らない。ほかのスキルの手順で進められなければ、相手の Agent が扱う内容を利用者に伝える。

## Step 2: 依頼を書いて送る

- `message` には、利用者の依頼を、対象・条件・期間を省かずに書く。この会話で既に決まった条件も書き入れる。
- 利用者が示していない条件・前提を足さない。

## Step 3: 結果ごとの対応

- `status` が `completed`: `text` の内容で答える。`text` に無い値・事実を足さない。
- `status` が `input_required` か `incomplete`: 結果に付く案内に従う。
- `content_omitted` がある: 応答に本文のほかの内容（ファイルなど）があり、受け取れていないことを利用者に伝える。
- 失敗が返った: 失敗の内容を利用者に伝え、結果を推測で補わない。
