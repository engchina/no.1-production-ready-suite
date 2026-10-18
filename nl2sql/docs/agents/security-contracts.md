# NL2SQL の横断的な保守・セキュリティ契約

> nl2sql/AGENTS.md から移した詳細（#2276）。nl2sql/AGENTS.md の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、AGENTS.md には規則の要約と案内だけを書く。

## 横断的な保守・セキュリティ契約

3 製品共通の契約（更新 API とデータの所有範囲、認可と状態遷移のサーバー側強制、i18n の変更と E2E の locator、状態保持、未保存変更の離脱ガード）は platform の [UX 契約](../../../platform/docs/ux-contracts/README.md)（[cross-cutting.md](../../../platform/docs/ux-contracts/cross-cutting.md) / [workspace-state.md](../../../platform/docs/ux-contracts/workspace-state.md)）が正本。ここには NL2SQL 固有の規則だけを書く（旧 repo の Issue #168〜#185 の再発防止策）。

### 「AI 活用」の下の機能のデータの見え方（#1889・#1928・#1954）

- **system_admin は全員のデータ、それ以外は自分のデータだけ**（3 製品共通。判定は `pr_system_settings.auth.visibility`）。一覧は backend の条件で絞り、他人のデータの詳細・操作は 404。`nl2sql.feedback.manage` などの管理権限は「AI 活用」で全員を見る理由にしない。
- **system_admin は他の利用者のデータに、本人と同じすべての操作ができる**（見る・名前の変更・削除・チャットの続きの質問・SQL の実行・回答前の確認・停止・回答の評価。外部の MCP もキーの持ち主が system_admin なら同じ。#1928）。実行するのは操作した system_admin 自身で、ジョブの actor・権限・業務プロファイル・SQL の実行の接続（#904 の分け方は崩さない）・実行履歴・評価した人（`feedback_actor_user_uuid`）は操作した人。会話の持ち主（最初のターンの actor）は変えず、持ち主以外が送ったターンには送り手の表示名（`sender_display_name`）を添える。system_admin が他の利用者の会話を開いているときは、入力欄の上に「この会話の持ち主にも、回答と途中の結果が見えます」を出す（#1954）。
- **実行履歴の行は、実行した人と会話の持ち主の両方に見える**（system_admin は全員。#1954）。行に会話の持ち主（`conversation_owner_user_uuid`）を書くときに残し、一覧は「実行した人か会話の持ち主」の条件で絞る（SQL 生成・SQL 直接実行など会話の無い実行は実行した人だけ）。画面は「実行者」・実行した人が自分でない行の「〈名前〉が実行」・絞り込み（すべて / 自分が実行 / 他の人が実行。`executor`）・詳細の「会話の持ち主」。利用量・API キーの上限・監査は実行した人に数える。API ごとの割り当ては [docs/security-rbac-deepsec.md](../../docs/security-rbac-deepsec.md)「「AI 活用」の下の機能のデータの見え方」。

### role の権限昇格の防止

- `SYSTEM_ADMIN` 以外が role を更新するとき、追加される実効権限 `expand_permissions(new) - expand_permissions(current)` は actor 自身の実効権限の部分集合でなければならず、違反は `403` で拒否する。暗黙 permission と `grants_all_profile_access` 相当の profile 管理権限も展開後に評価し、自分・他人いずれの role 経由でも権限昇格を許可しない。未保持の既存権限を削除する操作は妨げない。
- role 作成は未割当のため現行どおり許可できるが、user への role 割当では既存の実効権限部分集合 check を必須とする。role/assignment 変更が次 request から再計算される前提で、変更直後の許可・拒否まで API 回帰テストに含める。

### 状態保持の実装範囲

- 保存の namespace・期限・対象画面・検証の spec は [docs/frontend-workspace-state-spec.md](../../docs/frontend-workspace-state-spec.md) に書く。SQL・生成 SQL・`ADMIN_EXECUTE` などの確認語は共通契約の「入力」「生成した結果」「確認語」として扱う。
