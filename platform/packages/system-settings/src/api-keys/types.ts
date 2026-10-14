/**
 * 外部の MCP クライアント向けの API キーの API 契約（backend は `pr_system_settings.auth.api_keys`。#1805）。
 * 外部に MCP を公開する 3 製品が使う（Agent は業務 Agent へのチャット。#1874）。
 * 通信（認証 cookie・CSRF・エラー形式）は製品の API client が持ち、この型を満たす `api` を画面へ渡す。
 */

/** キーを使える製品（外部に MCP を公開する製品）。 */
export const API_KEY_PRODUCTS = ["rag", "nl2sql", "agent"] as const;
export type ApiKeyProduct = (typeof API_KEY_PRODUCTS)[number];

/**
 * 製品 → キーで使える対象の ID（#1874。最小権限）。製品の key が無ければ、その製品では利用者が使える
 * すべての対象。今は Agent の業務 Agent（`agent`）だけを絞れる。
 */
export type ApiKeyResourceScopes = Partial<Record<ApiKeyProduct, string[]>>;

/** 範囲を選ぶ候補（Agent の画面で発行するときの業務 Agent）。 */
export interface ApiKeyScopeOption {
  id: string;
  name: string;
}

/** 期限の日数（null は無期限）。 */
export const API_KEY_EXPIRY_DAYS = [30, 90, 365] as const;
export type ApiKeyExpiryDays = (typeof API_KEY_EXPIRY_DAYS)[number];

/** 1 人が持てるキーの数（backend の `API_KEY_MAX_PER_USER` と同じ）。 */
export const API_KEY_MAX_PER_USER = 10;
/** 名前の上限（backend と同じ）。 */
export const API_KEY_NAME_MAX_LENGTH = 100;

export const API_KEYS_QUERY_KEY = ["platform", "api-keys", "mine"] as const;
export const API_KEYS_ADMIN_QUERY_KEY = ["platform", "api-keys", "all"] as const;

/** 画面に返すキー（秘密・hash を含まない）。 */
export interface ApiKey {
  id: string;
  name: string;
  /** 一覧で見分けるための先頭（`prak_<id>_` + 秘密の先頭 4 文字）。 */
  token_prefix: string;
  products: ApiKeyProduct[];
  owner_user_uuid: string;
  owner_display_name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  expired: boolean;
  /** 製品ごとの対象の範囲（#1874。無い製品はすべて）。 */
  resource_scopes?: ApiKeyResourceScopes;
}

export interface ApiKeysList {
  keys: ApiKey[];
}

export interface ApiKeyCreateRequest {
  name: string;
  products: ApiKeyProduct[];
  /** null は無期限。 */
  expires_in_days: ApiKeyExpiryDays | null;
  /** 製品ごとの対象の範囲（#1874。省くとすべて）。 */
  resource_scopes?: ApiKeyResourceScopes;
}

/** 発行の応答。`token` はこの 1 回だけ返る。 */
export interface ApiKeyCreated {
  key: ApiKey;
  token: string;
}

/** 自分のキー（`/api/account/api-keys*`。ログインだけで使える）。 */
export interface MyApiKeysApi {
  list: () => Promise<ApiKeysList>;
  create: (request: ApiKeyCreateRequest) => Promise<ApiKeyCreated>;
  revoke: (keyId: string) => Promise<unknown>;
}

/** 全員のキー（`/api/security/api-keys*`。管理のメニュー権限が要る）。 */
export interface ApiKeysAdminApi {
  list: () => Promise<ApiKeysList>;
  revoke: (keyId: string) => Promise<unknown>;
}

/** 画面に出す MCP の接続先（製品ごと）。 */
export interface McpEndpoint {
  product: ApiKeyProduct;
  url: string;
}
