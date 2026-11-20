# AGENTS.md — No.1 Production Ready Suite（monorepo 共通ルール）

> monorepo 全体で、どの作業でも守る規則と、作業ごとに読む文書の目次（Claude Code と Codex が直接読む。`CLAUDE.md` は置かない）。作業対象のディレクトリの `AGENTS.md` もあわせて適用し、矛盾する場合は製品固有のルールを優先する（GitHub 運用と CI は本ファイルと `docs/agents/` が優先）。
> 作業ごとの規則は下の表の文書にある。**その作業を始める前に読み、本ファイルと同じ強さで守る。** 大きさの上限は `scripts/check-agents-md-size.sh`（本ファイル 20 KiB、製品の `AGENTS.md` との合計 32 KiB）。`@path` の import は使わない。

## 作業ごとに読む文書

| いつ読むか | 読む文書 |
|---|---|
| `platform/` `rag/` `nl2sql/` `agent/` の下で作業する前（repo の直下で起動したときも） | その下の `AGENTS.md` |
| repo の構成・package の場所・製品間の連携の方法を確かめるとき | [docs/agents/repo-structure.md](./docs/agents/repo-structure.md) |
| Issue・PR を書く前、CI の待ち・merge・片付け・開発の ADB の起動の手順を確かめるとき | [docs/agents/github-workflow.md](./docs/agents/github-workflow.md) |
| テストを書く前、CI の job・workflow を変える前、ローカルで検証するとき、Python の版を変える前 | [docs/agents/ci.md](./docs/agents/ci.md) |
| UI（画面・部品・lint・e2e）に触る前 | [docs/agents/ui.md](./docs/agents/ui.md) と [ARCHITECTURE.md](./platform/docs/design-system/ARCHITECTURE.md) |
| LLM に渡す文（プロンプト・`skills/`・MCP の説明・Runtime の指示）や LLM の出力の契約を書く・変える前 | [docs/agents/prompts.md](./docs/agents/prompts.md) |
| AI / DB のサービス・言語・framework・依存を選ぶ前、Oracle の製品名を書く前 | [docs/agents/tech-stack.md](./docs/agents/tech-stack.md) |
| backend を実装する前 | [platform/docs/backend-standard.md](./platform/docs/backend-standard.md) |
| 配備（Terraform・Compute・Nginx・systemd・release）に触る前、再配備するとき | [docs/agents/deploy.md](./docs/agents/deploy.md) |
| 3 製品で同じ機能（システム設定・ナビ・API キー・データの見え方・権限管理など）に触る前 | [docs/agents/shared-features.md](./docs/agents/shared-features.md) |
| `.env` の変数・DB の object の名前を決める前、`.env.example` を変える前 | [docs/agents/naming.md](./docs/agents/naming.md) |
| Excel・.docx / .pptx / .pdf を作る・編集する前 | [docs/agents/spreadsheets.md](./docs/agents/spreadsheets.md) |

## 言語

- 応答・Issue / PR・commit message・コードコメントは日本語（identifier・API path・file path・command・固有名詞は英語のまま）。ユーザーが会話で別の言語を指定したら、応答だけ合わせる。
- システムの主要言語は日本語。UI 文言・エラー・通知・LLM への指示と出力は日本語を前提とし、ユーザー向け文言は i18n 経由で管理する。
- **利用者が入力する自然言語は、3 製品で「質問」と呼ぶ（#1183）。** 「クエリ」は SQL・技術の概念（サブクエリ・クエリ文字列など）にだけ使う（識別子・API・i18n のキーは変えなくてよい）。3 製品で同じ機能の説明の文は同じ書き方にする（[docs/agents/shared-features.md](./docs/agents/shared-features.md)）。

## 書かないもの

- **業務の内容は書かない（repo は public。#2019）。** 利用者が扱う文書・データの業務の内容（顧客・案件・制度・文書・法令などの名前、ナレッジベース・プロファイル・業務 Agent などの利用者が付けた名前、質問や文書の実データの抜粋）を、Issue・PR・commit message・ブランチ名・コード・コメント・識別子・ログ・画面の文言・i18n・docs・テストの fixture のどこにも書かない。再現情報は技術の言葉と匿名の名前（「ナレッジベース A」）で書き、テストは合成の fixture を使い、機能・設定の名前も技術の言葉で付ける。利用者が会話で示した業務の文書・質問は、ローカルの確認（repo の外か `./tmp/`）だけに使う。
- **シークレットは `.env` / secret store 経由にする**（ハードコード・commit・API 応答への展開をしない）。

## ハードコードしない・規則を継ぎ足さない

- **特定の文書・業務・質問・データに合わせた語・規則・閾値・手順をコード（分岐・正規表現・プロンプトの例）に書かない。** 必要なら skill（`skills/<name>/SKILL.md`）か設定（`.env`・設定画面・検索・回答プロファイル・言語資源）に置き、コードは汎用の仕組みにする。**語の意味の判定（問いか、対象の名前か、例かなど）は、特定の文書に合わせていない一般の語でも、正規表現と語の一覧でコードに書かない**（モデルの構造化出力に置き、コードは写しや番号が原文にあることを確かめる。形式の解析は別）。
- **モデルの誤りごとに規則を継ぎ足さない。** モデルの誤りを見つけるたびに、専用のフィールド・判定・処理フローを追加して対処しない。複雑さを増やしても信頼性が上がるとは限らないため、まず原要求の欠落・改変、文脈、既存の指示とインターフェースを確認する。構造の変更は、原因に対する必要性・汎用性・副作用を確かめたうえで最小限にし、構造化しただけでモデルの意味理解の正しさを保証したと扱わない。

## 依存の向き

- **依存の向きは `platform/` → 各製品・`suite/` の一方向。** platform は製品・suite を、製品は suite を import しない。suite は platform の公開の package だけに依存する。製品同士はコードで依存せず、HTTP API（呼び先の `POST /api/mcp`）で連携する。RAG / NL2SQL は Agent を呼ばない（向きは外部のクライアント → Agent → RAG / NL2SQL）。連携の方法と境界のテストは [docs/agents/repo-structure.md](./docs/agents/repo-structure.md)。

## 開発の流れ

手順・判定の条件・コマンドは [docs/agents/github-workflow.md](./docs/agents/github-workflow.md)。

- **`main` へ直接 commit / push しない。** Issue を先に作り（label `product:rag` / `product:nl2sql` / `product:agent` / `platform`。複数ならすべて）、ブランチ `codex/<issue-number>-<short-topic>` で変更して PR を出す。docs-only・緊急の修正も同じ（例外は理由を添えてユーザーに確認）。
- **CI はエージェント自身が待ち、merge の可否は `scripts/pr-merge-check.sh <PR>` で判定する**（`gh pr checks --watch --required` は使わない）。検証と PR 本文の更新が済み、最新 commit の `CI OK` が成功したら、ユーザーの確認を求めず `gh pr merge <PR> --merge` で merge する（報告だけで終わらない）。
- CI の失敗・conflict は直して再検証してから merge する。ruleset を迂回した強制 merge はしない。**main が赤（label `ci-failure` の Issue）なら、ほかの作業より先に直す。**
- **merge 後は `main` へ fast-forward し、その作業の worktree とローカルのブランチを消してから完了を報告する**（`reset --hard` 等で未保存の変更を破棄しない）。他のセッションの未コミット変更・stash・worktree を確認なしに破棄・上書きしない。
- **製品の不具合を確かめたら、必ず Issue を作る（#2488）。** 作業中（実装・検証・評価・配備・調査）に再現か根拠で確かめた不具合は、依頼の範囲の内外やその場で直すかに関わらず、ユーザーの指示を待たずに Issue にする（推測だけでは作らない）。実環境の機密（IP・ホスト名・OCID・実データのログなど）も書かない。
- **修正の後は根因・変更箇所・方法・変えない範囲を利用者へ説明する**（結果だけで終えない）。
- **開発の ADB（`platform/.env` の `PLATFORM_ORACLE_ADB_OCID`）が停止していたら、利用者に聞かずに起動する**（停止は指示が無い限りしない。起動したことは報告に書く）。
- **一時ファイルは `/tmp` ではなく repo の直下の `./tmp/<作業の名前>/` に置き、commit しない**（作業用の worktree は `.claude/worktrees/`）。
