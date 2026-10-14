/**
 * API キーの画面の既定の文言（RAG / NL2SQL 共通。#1805）。
 * 製品は `messages` で一部を上書きできる（同じ key）。
 */
export const API_KEYS_MESSAGES = {
  // 自分のキー（アカウントのメニュー）
  "apiKeys.mine.title": "API キー",
  "apiKeys.mine.subtitle":
    "Claude Code・Codex・Cursor などの MCP クライアントから RAG / NL2SQL を使うための、あなたのキーを発行します。",
  "apiKeys.endpoint.title": "接続先",
  "apiKeys.endpoint.description":
    "MCP クライアントに接続先の URL を登録し、Authorization ヘッダーに「Bearer <API キー>」を付けます。キーはあなたとして動き、あなたのロールの権限とデータの範囲で答えます。",
  "apiKeys.endpoint.copy": "URL をコピー",
  "apiKeys.endpoint.copied": "URL をコピーしました",
  "apiKeys.endpoint.copyFailed": "コピーできませんでした。URL を選択してコピーしてください。",
  "apiKeys.endpoint.example": "Claude Code の登録の例",
  "apiKeys.endpoint.exampleNote":
    "<API キー> は発行したキーに置き換えます。キーはファイルや共有の設定に書かず、環境変数などに置いてください。",
  "apiKeys.create.title": "API キーを発行",
  "apiKeys.create.description":
    "キーは端末・用途ごとに分けて発行します（1 人 {max} 件まで）。秘密は発行の直後に 1 回だけ表示します。",
  "apiKeys.create.name": "名前（用途）",
  "apiKeys.create.namePlaceholder": "例: 会社の PC の Claude Code",
  "apiKeys.create.nameRequired": "名前を入力してください。",
  "apiKeys.create.products": "使える製品",
  "apiKeys.create.productsRequired": "使える製品を 1 つ以上選んでください。",
  "apiKeys.create.expiry": "有効期限",
  "apiKeys.create.expiry.30": "30 日",
  "apiKeys.create.expiry.90": "90 日",
  "apiKeys.create.expiry.365": "365 日",
  "apiKeys.create.expiry.none": "無期限",
  "apiKeys.create.submit": "発行",
  "apiKeys.create.limit":
    "API キーは 1 人 {max} 件までです。使わないキーを失効させてから発行してください。",
  "apiKeys.created.title": "API キーを発行しました",
  "apiKeys.created.description":
    "このキーは今だけ表示します。コピーして MCP クライアントの設定（環境変数など）に保管してください。閉じると二度と表示できません。",
  "apiKeys.created.label": "API キー",
  "apiKeys.created.copy": "コピー",
  "apiKeys.created.copied": "API キーをコピーしました",
  "apiKeys.created.copyFailed": "コピーできませんでした。欄の文字を選択してコピーしてください。",
  "apiKeys.created.done": "保管しました",
  "apiKeys.list.mine": "あなたの API キー",
  "apiKeys.list.all": "全員の API キー",
  "apiKeys.list.label": "API キーの一覧",
  "apiKeys.list.loading": "API キーを読み込んでいます",
  "apiKeys.list.loadFailed": "API キーを読み込めませんでした。",
  "apiKeys.list.empty": "API キーはまだありません。",
  "apiKeys.list.emptyHint": "上の「API キーを発行」から、MCP クライアントごとにキーを発行します。",
  "apiKeys.list.emptyAll": "API キーはまだありません。",
  "apiKeys.list.emptyAllHint": "各利用者が、アカウントのメニューの「API キー」から自分のキーを発行します。",
  "apiKeys.list.noMatch": "条件に合う API キーはありません。",
  "apiKeys.list.search": "名前・利用者で絞り込み",
  "apiKeys.list.clearSearch": "検索語をクリア",
  "apiKeys.list.count": "{count} 件",
  "apiKeys.column.name": "名前",
  "apiKeys.column.products": "使える製品",
  "apiKeys.column.expires": "有効期限",
  "apiKeys.column.lastUsed": "最後に使った日時",
  "apiKeys.column.created": "発行日時",
  "apiKeys.column.owner": "利用者",
  "apiKeys.column.actions": "操作",
  "apiKeys.noExpiry": "無期限",
  "apiKeys.expired": "期限切れ",
  "apiKeys.neverUsed": "未使用",
  "apiKeys.deletedUser": "削除されたユーザー",
  "apiKeys.revoke": "失効",
  "apiKeys.rowActions": "{name} の操作",
  "apiKeys.revokeTitle": "「{name}」を失効させますか",
  "apiKeys.revokeDescription":
    "このキーを使っている MCP クライアントは、すぐに RAG / NL2SQL を呼べなくなります。元に戻せません。",
  "apiKeys.revokeOwnerDescription":
    "{owner} さんのキーです。このキーを使っている MCP クライアントは、すぐに RAG / NL2SQL を呼べなくなります。元に戻せません。",
  "apiKeys.revoked": "API キーを失効させました",
  "apiKeys.operationFailed": "操作できませんでした。",
  "apiKeys.retry": "再試行",
  "apiKeys.admin.title": "API キー",
  "apiKeys.admin.subtitle":
    "全員の API キーを確かめ、不要なキーや漏れたおそれのあるキーを失効させます。キーの発行は各利用者がアカウントのメニューから行います。",
  "apiKeys.product.rag": "RAG",
  "apiKeys.product.nl2sql": "NL2SQL",
} as const;

export type ApiKeysMessageKey = keyof typeof API_KEYS_MESSAGES;
export type ApiKeysMessages = Partial<Record<ApiKeysMessageKey, string>>;

/** 製品の上書きを合わせた文言の関数（`{name}` を params で置き換える）。 */
export function apiKeysTranslator(messages?: ApiKeysMessages) {
  return (key: ApiKeysMessageKey, params?: Record<string, string | number>): string => {
    let value: string = messages?.[key] ?? API_KEYS_MESSAGES[key];
    if (params) {
      for (const [name, replacement] of Object.entries(params)) {
        value = value.replace(new RegExp(`\\{${name}\\}`, "g"), String(replacement));
      }
    }
    return value;
  };
}
