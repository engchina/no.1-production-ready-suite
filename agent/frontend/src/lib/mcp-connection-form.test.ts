import { describe, expect, it } from "vitest";

import type { McpConnectionPreset, McpConnectionSettings } from "./api";
import {
  EMPTY_MCP_FORM,
  a2aAgentCardOf,
  mcpAuthModesFor,
  mcpConnectionPayload,
  mcpFormOf,
  withMcpPreset,
  withMcpProtocol,
  type McpConnectionFormState,
  type McpConnectionPayloadContext,
} from "./mcp-connection-form";

// 規則は backend（app/features/agent/router.py の接続の API。#5932）と同じ。ずれると、画面が送った組み合わせを
// backend が 400 で断る（または、標準の接続で変えられない項目を送って保存に失敗する）。
// 値はすべて合成（実在の host・接続ではない）。

const PRESET: McpConnectionPreset = {
  id: "aidp_agent",
  protocol: "a2a",
  auth_mode: "oci_signature",
  base_url_prefix: "https://gateway.aidp.region-a.example.test/agentendpoint/",
  base_url_example: "https://gateway.aidp.region-a.example.test/agentendpoint/<agentId>/a2a",
  timeout_seconds: 120,
};

function connection(overrides: Partial<McpConnectionSettings>): McpConnectionSettings {
  return {
    server_id: "agent-a",
    label: "合成の Agent A",
    protocol: "a2a",
    base_url: "https://agent-a.example.test/a2a",
    agent_card_url: null,
    auth_mode: "none",
    timeout_seconds: 30,
    source: "runtime",
    removable: true,
    configured: true,
    api_key_configured: false,
    oauth_configured: false,
    session_configured: false,
    service_token_configured: false,
    oci_signature_configured: false,
    trust_tool_annotations: false,
    ...overrides,
  };
}

function editing(
  saved: McpConnectionSettings,
  overrides: Partial<McpConnectionPayloadContext> = {}
): McpConnectionPayloadContext {
  return { editingId: saved.server_id, builtin: false, urlLocked: false, baseline: mcpFormOf(saved), ...overrides };
}

const NEW: McpConnectionPayloadContext = { editingId: null, builtin: false, urlLocked: false, baseline: EMPTY_MCP_FORM };

describe("mcpFormOf", () => {
  it("プロトコルの無い応答（MCP の接続）は MCP として開く", () => {
    const form = mcpFormOf(connection({ protocol: undefined, agent_card_url: undefined }));
    expect(form.protocol).toBe("mcp");
    expect(form.agentCardUrl).toBe("");
  });

  it("A2A の接続は endpoint・Agent card の URL・認証・信頼を写す", () => {
    const form = mcpFormOf(
      connection({
        agent_card_url: "https://agent-a.example.test/card.json",
        auth_mode: "oci_signature",
        trust_tool_annotations: true,
      })
    );
    expect(form).toMatchObject({
      protocol: "a2a",
      baseUrl: "https://agent-a.example.test/a2a",
      agentCardUrl: "https://agent-a.example.test/card.json",
      authMode: "oci_signature",
      trustToolAnnotations: true,
      presetId: "",
    });
  });
});

describe("mcpAuthModesFor", () => {
  it("MCP は OCI 署名を出さず、A2A はサービストークンを出さない", () => {
    expect(mcpAuthModesFor("mcp")).toEqual(["none", "api_key", "oauth_client_credentials", "service_token"]);
    expect(mcpAuthModesFor("a2a")).toEqual(["none", "api_key", "oauth_client_credentials", "oci_signature"]);
  });
});

describe("withMcpProtocol", () => {
  it("使えなくなる認証方式は「なし」に戻す", () => {
    const mcp: McpConnectionFormState = { ...EMPTY_MCP_FORM, authMode: "service_token" };
    expect(withMcpProtocol(mcp, "a2a")).toMatchObject({ protocol: "a2a", authMode: "none" });
    const a2a: McpConnectionFormState = { ...EMPTY_MCP_FORM, protocol: "a2a", authMode: "oci_signature" };
    expect(withMcpProtocol(a2a, "mcp")).toMatchObject({ protocol: "mcp", authMode: "none" });
  });

  it("どちらでも使える認証方式と、入力済みの値は残す", () => {
    const form: McpConnectionFormState = {
      ...EMPTY_MCP_FORM,
      authMode: "api_key",
      apiKey: "synthetic",
      baseUrl: "https://agent-a.example.test/a2a",
    };
    expect(withMcpProtocol(form, "a2a")).toEqual({ ...form, protocol: "a2a" });
    expect(withMcpProtocol(form, "mcp")).toBe(form);
  });
});

describe("withMcpPreset", () => {
  it("既定値はプロトコル・認証・タイムアウトと URL の雛形を入れる", () => {
    const form = withMcpPreset({ ...EMPTY_MCP_FORM, serverId: "agent-a" }, PRESET);
    expect(form).toMatchObject({
      presetId: "aidp_agent",
      serverId: "agent-a",
      protocol: "a2a",
      authMode: "oci_signature",
      timeoutSeconds: "120",
      baseUrl: PRESET.base_url_prefix,
    });
  });

  it("入力済みの URL は雛形で上書きしない", () => {
    const typed = { ...EMPTY_MCP_FORM, baseUrl: "https://agent-a.example.test/a2a" };
    expect(withMcpPreset(typed, PRESET).baseUrl).toBe("https://agent-a.example.test/a2a");
  });

  it("「指定なし」に戻すと、既定値が入れた項目と手を付けていない雛形の URL を初期の値へ戻す", () => {
    const applied = withMcpPreset(EMPTY_MCP_FORM, PRESET);
    expect(withMcpPreset(applied, null, PRESET)).toEqual(EMPTY_MCP_FORM);
    const edited = { ...applied, baseUrl: `${PRESET.base_url_prefix}agent-a/a2a` };
    expect(withMcpPreset(edited, null, PRESET)).toMatchObject({
      presetId: "",
      protocol: "mcp",
      authMode: "none",
      timeoutSeconds: "10",
      baseUrl: `${PRESET.base_url_prefix}agent-a/a2a`,
    });
  });
});

describe("mcpConnectionPayload", () => {
  it("新規の MCP の接続は今までどおりの項目を送る（Agent card の URL は送らない）", () => {
    const form: McpConnectionFormState = {
      ...EMPTY_MCP_FORM,
      serverId: "mcp-a",
      label: "合成の MCP A",
      baseUrl: " https://mcp-a.example.test/mcp ",
      authMode: "api_key",
      apiKey: "synthetic",
      sessionId: "session-a",
      timeoutSeconds: "7",
    };
    expect(mcpConnectionPayload(form, NEW)).toEqual({
      timeout_seconds: 7,
      session_id: "session-a",
      label: "合成の MCP A",
      protocol: "mcp",
      auth_mode: "api_key",
      trust_tool_annotations: false,
      base_url: "https://mcp-a.example.test/mcp",
      api_key: "synthetic",
    });
  });

  it("新規の A2A の接続は endpoint・Agent card の URL を送り、セッション ID と既定値の ID は送らない", () => {
    const form: McpConnectionFormState = {
      ...withMcpPreset(EMPTY_MCP_FORM, PRESET),
      serverId: "agent-a",
      baseUrl: `${PRESET.base_url_prefix}agent-a/a2a`,
      agentCardUrl: " https://agent-a.example.test/card.json ",
      sessionId: "session-a",
    };
    const payload = mcpConnectionPayload(form, NEW);
    expect(payload).toEqual({
      timeout_seconds: 120,
      label: null,
      protocol: "a2a",
      auth_mode: "oci_signature",
      trust_tool_annotations: false,
      base_url: `${PRESET.base_url_prefix}agent-a/a2a`,
      agent_card_url: "https://agent-a.example.test/card.json",
    });
    expect(payload).not.toHaveProperty("session_id");
    expect(payload).not.toHaveProperty("presetId");
  });

  it("OCI 署名は接続ごとの資格情報を送らない", () => {
    const form: McpConnectionFormState = {
      ...EMPTY_MCP_FORM,
      protocol: "a2a",
      authMode: "oci_signature",
      apiKey: "synthetic",
      oauthClientSecret: "synthetic",
      serviceAudience: "rag",
    };
    const payload = mcpConnectionPayload(form, NEW);
    for (const key of ["api_key", "oauth_client_secret", "oauth_token_url", "service_audience"]) {
      expect(payload).not.toHaveProperty(key);
    }
  });

  it("標準の接続（AIDP の Agent）は endpoint の URL とタイムアウトだけを送る", () => {
    const saved = connection({
      server_id: "aidp",
      source: "builtin",
      removable: false,
      base_url: null,
      auth_mode: "oci_signature",
      trust_tool_annotations: true,
      timeout_seconds: 120,
    });
    const form = { ...mcpFormOf(saved), baseUrl: `${PRESET.base_url_prefix}agent-a/a2a`, timeoutSeconds: "180" };
    expect(mcpConnectionPayload(form, editing(saved, { builtin: true }))).toEqual({
      timeout_seconds: 180,
      base_url: `${PRESET.base_url_prefix}agent-a/a2a`,
    });
  });

  it("変えていない URL（伏せて返った保存済みの値）は送り返さない", () => {
    const saved = connection({ agent_card_url: "https://agent-a.example.test/card.json" });
    const payload = mcpConnectionPayload({ ...mcpFormOf(saved), timeoutSeconds: "45" }, editing(saved));
    expect(payload).not.toHaveProperty("base_url");
    expect(payload).not.toHaveProperty("agent_card_url");
    expect(payload.timeout_seconds).toBe(45);
  });

  it("Agent card の URL を消す・MCP へ戻すと、保存済みの値を空で上書きする", () => {
    const saved = connection({ agent_card_url: "https://agent-a.example.test/card.json" });
    const cleared = mcpConnectionPayload({ ...mcpFormOf(saved), agentCardUrl: "" }, editing(saved));
    expect(cleared.agent_card_url).toBe("");
    const switched = mcpConnectionPayload(withMcpProtocol(mcpFormOf(saved), "mcp"), editing(saved));
    expect(switched).toMatchObject({ protocol: "mcp", agent_card_url: "" });
  });

  it("配備が URL を決めた標準の接続は URL を送らない", () => {
    const saved = connection({
      server_id: "rag",
      protocol: "mcp",
      source: "builtin",
      auth_mode: "service_token",
      base_url_locked: true,
    });
    const form = { ...mcpFormOf(saved), baseUrl: "https://other.example.test/mcp" };
    const payload = mcpConnectionPayload(form, editing(saved, { builtin: true, urlLocked: true }));
    expect(payload).toEqual({ timeout_seconds: 30, session_id: undefined });
  });
});

describe("a2aAgentCardOf", () => {
  it("A2A の応答の Agent card を読む", () => {
    expect(
      a2aAgentCardOf({
        protocol: "a2a",
        agent_card: { name: " 合成の Agent A ", description: "合成の説明", protocol_version: "0.3.0", version: null },
      })
    ).toEqual({ name: "合成の Agent A", description: "合成の説明", protocolVersion: "0.3.0", version: null });
  });

  it("MCP の応答・名前の無い Agent card は null", () => {
    expect(a2aAgentCardOf({ server_id: "mcp-a", method: "tools/list" })).toBeNull();
    expect(a2aAgentCardOf({ protocol: "a2a", agent_card: { description: "合成の説明" } })).toBeNull();
    expect(a2aAgentCardOf({ protocol: "a2a", agent_card: ["x"] })).toBeNull();
    expect(a2aAgentCardOf(undefined)).toBeNull();
  });
});
