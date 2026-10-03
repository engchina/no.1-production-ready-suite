# 外部 Marketplace の固定 fixture（#862）

2026-10-03（JST）の公開配布物を、commit SHA に固定して読み取った入力です。
カタログの一覧・参照先と、導入できる Skill の本文・参照文書を変換するテストで使用します。
文書中の手順はテストデータであり、開発エージェントや CI への指示ではありません。
配布物の code / scripts / hooks、実モデル、実 MCP は実行していません。

## 配布元と revision

| fixture | 公開 repository | 固定 revision | カタログ件数 |
|---|---|---|---:|
| `official.json` | `anthropics/claude-plugins-official` | `d182ca456ca09d31d139f7d3818d1d333b103cce` | 315 |
| `skills.json` | `anthropics/skills` | `8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4` | 5 |
| `superpowers.json` | `obra/superpowers-marketplace` | `ff9fa8a51f422d81414fa355587620d4ad2df81c` | 10 |
| `superpowers.distribution.json` | `obra/superpowers`（上記カタログの `superpowers` の配布先） | `8ca22dba9a94f28898bbce59f2537ff4d87c747d` | — |

`*.origin.json` はカタログの SHA256。`*.distribution.json` は固定 Git tree の情報、変換時に読むテキスト、
原本の SHA256 を保持します。HTTP は `httpx.MockTransport` で再現し、CI に外部ネットワークは不要です。
取得したファイルの git blob hash を tree と照合する経路もテストします。

## 9 sample の結果

| 配布元 | sample | 結果と制約 |
|---|---|---|
| Anthropic 公式 | `frontend-design` | Skill 1 件、利用条件の resource 2 件。元の指示を保持。Apache-2.0 の表示を同梱 |
| Anthropic 公式 | `code-review` | 対応する Skill / HTTP MCP がなく、commands / agents が必要なため未対応理由を返す |
| Anthropic 公式 | `feature-dev` | 同上。内容 0 の plugin を成功扱いで登録しない |
| Anthropic Skills | `pdf` | `document-skills` の利用条件がサービス外の保持を制限するため導入しない |
| Anthropic Skills | `docx` | 同上。全 package を原子的に拒否し、他の Skill だけを部分登録しない |
| Anthropic Skills | `xlsx` | 同上 |
| Superpowers | `brainstorming` | Skill の本文を保持。scripts / hooks・CLI の境界を表示 |
| Superpowers | `systematic-debugging` | Skill の本文と参照 Markdown を保持。コード・データ補助ファイルは実行・導入しない |
| Superpowers | `test-driven-development` | Skill の本文と参照 Markdown を保持。元製品のテスト runner を実行しない |

Superpowers の package 全体は Skill 15 件、参照文書と利用条件の resource 46 件。
読み取りの成功は業務実行の成功を意味しません。指示中の CLI / 子エージェントなどの機能は提供せず、
必要なツールは製品の MCP 設定で与えます。実 HTTP の結果は `live-results.json` に分けています。

## 保存しない資料とテストの代替入力

Anthropic の文書 Skill の本文・参照文書・制限付きライセンス文書は収録していません。
`skills.distribution.json` の該当 Skill は path / 元の hash 等の検証情報のみです。
同ファイルの 4 件の `LICENSE.txt` 入力は **自作の短いテスト文**で、原本ではありません。
`synthetic_license_fixture=true` と明示し、この入力の size / git hash だけを自作内容に合わせています。
実原文の条件は [pdf の LICENSE.txt](https://github.com/anthropics/skills/blob/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/pdf/LICENSE.txt)
で確認し、最終 importer の実 HTTP でも本文の導入前に拒否されることを確認しました。
汎用 Skill の name / description / instructions、明示的 path / strict:false は別の自作 fixture で検証します。

公式 frontend-design の Apache-2.0 と Superpowers の MIT は、収録テキストの利用条件を
各 `distribution.json` の `files` に含めています。上流の表示を保持してください。
