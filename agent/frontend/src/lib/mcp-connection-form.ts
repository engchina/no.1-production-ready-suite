/**
 * 外部接続（MCP / A2A。#5932）の編集フォームの状態と、保存の API へ送る内容の組み立て。
 *
 * 画面（`pages/settings/McpConnectionsPage.tsx`）から分けた純粋な関数（DOM を使わない単体テストで確かめる）。
 * 規則は backend（`agent/backend/app/features/agent/router.py` の接続の API）と同じ:
 * - `mcp` の接続は OCI 署名を使えず、`a2a` の接続はサービストークンを使えない。
 * - 標準の接続（`source: "builtin"`）の名前・プロトコル・認証・信頼・Agent card の URL は送らない。
 * - URL は変えたときだけ送る（保存済みの URL の資格情報は伏せて返るため。#1056）。
 */
import type {
  McpAuthMode,
  McpConnectionPreset,
  McpConnectionProtocol,
  McpConnectionSettings,
  McpConnectionWritePayload,
} from "@/lib/api";

export interface McpConnectionFormState {
  /** 追加の画面で選んだ既定値の ID（空は「指定なし」。保存の API には送らない）。 */
  presetId: string;
  serverId: string;
  label: string;
  protocol: McpConnectionProtocol;
  baseUrl: string;
  /** A2A の Agent card の URL（空なら endpoint から決まる）。 */
  agentCardUrl: string;
  authMode: McpAuthMode;
  apiKey: string;
  serviceAudience: string;
  timeoutSeconds: string;
  sessionId: string;
  oauthTokenUrl: string;
  oauthClientId: string;
  oauthClientSecret: string;
  oauthScope: string;
  /**
   * MCP: ツールの注釈（readOnlyHint）を信頼する（#1832・#1875）。A2A: この接続の Agent を承認なしで呼ぶ（#5932）。
   * 外部の接続は既定でオフ。標準の接続は常にオン（変えられない）。
   */
  trustToolAnnotations: boolean;
}

export const EMPTY_MCP_FORM: McpConnectionFormState = {
  presetId: "",
  serverId: "",
  label: "",
  protocol: "mcp",
  baseUrl: "",
  agentCardUrl: "",
  authMode: "none",
  apiKey: "",
  serviceAudience: "",
  timeoutSeconds: "10",
  sessionId: "",
  oauthTokenUrl: "",
  oauthClientId: "",
  oauthClientSecret: "",
  oauthScope: "",
  // 外部の接続は既定でオフ（#1875。MCP の仕様: 信頼しないサーバーの注釈は untrusted）。管理者が接続を
  // 確かめてから、確認のダイアログを経てオンにする。
  trustToolAnnotations: false,
};

export function mcpFormOf(connection: McpConnectionSettings | undefined): McpConnectionFormState {
  if (!connection) return EMPTY_MCP_FORM;
  return {
    ...EMPTY_MCP_FORM,
    serverId: connection.server_id,
    label: connection.label ?? "",
    protocol: connection.protocol ?? "mcp",
    baseUrl: connection.base_url ?? "",
    agentCardUrl: connection.agent_card_url ?? "",
    authMode: connection.auth_mode,
    serviceAudience: connection.service_audience ?? "",
    timeoutSeconds: String(connection.timeout_seconds),
    // 値を保存していない既存の外部の接続もオフ（backend が false を返す。#1875）。
    trustToolAnnotations: connection.trust_tool_annotations === true,
  };
}

const MCP_AUTH_MODES: readonly McpAuthMode[] = ["none", "api_key", "oauth_client_credentials", "service_token"];
const A2A_AUTH_MODES: readonly McpAuthMode[] = ["none", "api_key", "oauth_client_credentials", "oci_signature"];

/** プロトコルで選べる認証方式（並びは選択欄の順）。 */
export function mcpAuthModesFor(protocol: McpConnectionProtocol): readonly McpAuthMode[] {
  return protocol === "a2a" ? A2A_AUTH_MODES : MCP_AUTH_MODES;
}

/** プロトコルを切り替える。今の認証方式が使えなくなったら「なし」に戻す。 */
export function withMcpProtocol(
  form: McpConnectionFormState,
  protocol: McpConnectionProtocol
): McpConnectionFormState {
  if (form.protocol === protocol) return form;
  return {
    ...form,
    protocol,
    authMode: mcpAuthModesFor(protocol).includes(form.authMode) ? form.authMode : "none",
  };
}

/**
 * 追加の画面の既定値を入れる（プロトコル・認証・タイムアウトと URL の雛形）。`null` は「指定なし」で、
 * 既定値が入れた項目を初期の値へ戻す（URL は雛形のままのときだけ消す。入力した URL は残す）。
 */
export function withMcpPreset(
  form: McpConnectionFormState,
  preset: McpConnectionPreset | null,
  previous: McpConnectionPreset | null = null
): McpConnectionFormState {
  const untouchedUrl = !form.baseUrl.trim() || form.baseUrl === previous?.base_url_prefix;
  if (!preset) {
    return {
      ...form,
      presetId: "",
      protocol: EMPTY_MCP_FORM.protocol,
      authMode: EMPTY_MCP_FORM.authMode,
      timeoutSeconds: EMPTY_MCP_FORM.timeoutSeconds,
      baseUrl: untouchedUrl ? "" : form.baseUrl,
    };
  }
  return {
    ...form,
    presetId: preset.id,
    protocol: preset.protocol,
    authMode: preset.auth_mode,
    timeoutSeconds: String(preset.timeout_seconds),
    baseUrl: untouchedUrl ? preset.base_url_prefix : form.baseUrl,
  };
}

export interface McpConnectionPayloadContext {
  /** 編集中の接続の ID（新規は null）。 */
  editingId: string | null;
  /** 標準の接続（`source: "builtin"`）か。 */
  builtin: boolean;
  /** 配備が URL を決めた標準の接続か（URL を送らない。#1325）。 */
  urlLocked: boolean;
  /** 開いた時点（最後の保存の後）のフォーム。URL を変えたかの判定に使う。 */
  baseline: McpConnectionFormState;
}

/** URL を変えたか（新規は常に送る）。保存済みの伏せた URL をそのまま送り返さない（#1056）。 */
export function mcpBaseUrlChanged(form: McpConnectionFormState, context: McpConnectionPayloadContext): boolean {
  return !context.editingId || form.baseUrl.trim() !== context.baseline.baseUrl.trim();
}

/** Agent card の URL を送るか（A2A の外部の接続で変えたとき。MCP に戻したら保存済みの値を消す）。 */
function agentCardUrlToSend(form: McpConnectionFormState, context: McpConnectionPayloadContext): string | undefined {
  if (context.builtin) return undefined;
  const next = form.protocol === "a2a" ? form.agentCardUrl.trim() : "";
  const saved = context.editingId ? context.baseline.agentCardUrl.trim() : "";
  return next === saved ? undefined : next;
}

/** 保存の API（POST / PATCH）へ送る内容。`server_id` は呼び出し側が新規のときに足す。 */
export function mcpConnectionPayload(
  form: McpConnectionFormState,
  context: McpConnectionPayloadContext
): McpConnectionWritePayload {
  const payload: McpConnectionWritePayload = { timeout_seconds: Number(form.timeoutSeconds) };
  // セッション ID は MCP（streamable HTTP の gateway）だけ。
  if (form.protocol === "mcp") {
    payload.session_id = form.sessionId || undefined;
  }
  if (!context.builtin) {
    // 標準の接続の名前・プロトコル・認証・信頼は配備が決める（送らない。#1325・#1832・#5932）。
    payload.label = form.label || null;
    payload.protocol = form.protocol;
    payload.auth_mode = form.authMode;
    payload.trust_tool_annotations = form.trustToolAnnotations;
  }
  if (!context.urlLocked && mcpBaseUrlChanged(form, context)) {
    payload.base_url = form.baseUrl.trim();
  }
  const agentCardUrl = agentCardUrlToSend(form, context);
  if (agentCardUrl !== undefined) {
    payload.agent_card_url = agentCardUrl;
  }
  if (form.authMode === "api_key" && form.apiKey) {
    payload.api_key = form.apiKey;
  }
  if (form.authMode === "service_token" && !context.builtin) {
    payload.service_audience = form.serviceAudience.trim();
  }
  if (form.authMode === "oauth_client_credentials") {
    payload.oauth_token_url = form.oauthTokenUrl || undefined;
    payload.oauth_client_id = form.oauthClientId || undefined;
    payload.oauth_client_secret = form.oauthClientSecret || undefined;
    payload.oauth_scope = form.oauthScope || undefined;
  }
  return payload;
}

/** 「ツールを取得」の応答の Agent card（A2A の接続だけ。#5932）。 */
export interface A2aAgentCardInfo {
  name: string;
  description: string;
  protocolVersion: string | null;
  version: string | null;
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** 応答の `metadata` から Agent card を読む。A2A の接続でない・形が違うときは null。 */
export function a2aAgentCardOf(metadata: Record<string, unknown> | null | undefined): A2aAgentCardInfo | null {
  if (!metadata || metadata.protocol !== "a2a") return null;
  const card = metadata.agent_card;
  if (!card || typeof card !== "object" || Array.isArray(card)) return null;
  const record = card as Record<string, unknown>;
  const name = textOrNull(record.name);
  if (!name) return null;
  return {
    name,
    description: textOrNull(record.description) ?? "",
    protocolVersion: textOrNull(record.protocol_version),
    version: textOrNull(record.version),
  };
}
