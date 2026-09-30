import { describe, expect, it } from "vitest";

import {
  connectionFieldId,
  connectionLabel,
  connectionOptions,
  emptyConnection,
  modelsUsingConnection,
  nextConnectionId,
  normalizeModelSettings,
  removeConnection,
  validateConnections,
  validateModelConnections,
} from "../src/model/connections";
import {
  MAX_ENTERPRISE_AI_CONNECTIONS,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiConnectionSettings,
  type EnterpriseAiModelSettings,
  type ModelSettingsPayload,
} from "../src/model/types";

const primary: EnterpriseAiConnectionSettings = {
  ...emptyConnection("primary"),
  endpoint: "https://primary.example",
  has_api_key: true,
};
const secondary: EnterpriseAiConnectionSettings = {
  ...emptyConnection("secondary"),
  display_name: "シカゴ",
  endpoint: "https://secondary.example",
};
const models: EnterpriseAiConfiguredModel[] = [
  { model_id: "llm-a", display_name: "標準", vision_enabled: false },
  { model_id: "vlm-b", display_name: "", vision_enabled: true, connection_id: "secondary" },
  { model_id: " ", display_name: "未入力", vision_enabled: false, connection_id: "secondary" },
];

describe("接続（#533）", () => {
  it("上限は 2 件で、次に追加できる接続を返す", () => {
    expect(MAX_ENTERPRISE_AI_CONNECTIONS).toBe(2);
    expect(nextConnectionId([primary])).toBe("secondary");
    expect(nextConnectionId([primary, secondary])).toBeNull();
  });

  it("表示名が空なら「接続 N」、表示名があれば番号を説明に出す", () => {
    expect(connectionLabel(primary)).toBe("接続 1");
    expect(connectionLabel(secondary)).toBe("シカゴ");
    expect(connectionOptions([primary, secondary])).toEqual([
      { value: "primary", label: "接続 1" },
      { value: "secondary", label: "シカゴ", description: "接続 2" },
    ]);
  });

  it("接続 1 の入力欄の id は #533 より前のまま", () => {
    expect(connectionFieldId("primary", "endpoint")).toBe("enterprise-endpoint");
    expect(connectionFieldId("primary", "api-key")).toBe("enterprise-api-key");
    expect(connectionFieldId("secondary", "endpoint")).toBe("enterprise-secondary-endpoint");
  });

  it("接続 2 を追加したら Endpoint URL は必須（接続 1 は OCI 運用時だけ必須のまま）", () => {
    expect(validateConnections([emptyConnection("primary")])).toEqual({});
    expect(validateConnections([primary, emptyConnection("secondary")])).toEqual({
      secondary:
        "接続 2 の Endpoint URL を入力してください。使わない場合は接続を削除してください。",
    });
  });

  it("ない接続・保存前の接続を指すモデルを欄のエラーにする（モデル ID の空の行は除く）", () => {
    expect(validateModelConnections(models, [primary, secondary], ["primary", "secondary"])).toEqual(
      {},
    );
    expect(validateModelConnections(models, [primary], ["primary", "secondary"])).toEqual({
      1: "「vlm-b」の接続がありません。登録されている接続から選び直してください。",
    });
    expect(validateModelConnections(models, [primary, secondary], ["primary"])[1]).toContain(
      "シカゴ はまだ保存されていません",
    );
  });

  it("接続を削除すると、使っていたモデルを接続 1 に移す", () => {
    const enterprise = { connections: [primary, secondary], models } as EnterpriseAiModelSettings;
    expect(modelsUsingConnection(models, "secondary").map((model) => model.model_id)).toEqual([
      "vlm-b",
    ]);
    const next = removeConnection(enterprise, "secondary");
    expect(next.connections.map((connection) => connection.connection_id)).toEqual(["primary"]);
    expect(next.models.map((model) => model.connection_id)).toEqual([
      "primary",
      "primary",
      "primary",
    ]);
  });

  it("接続の一覧がない応答（#533 より前の形）は接続 1 として読む", () => {
    const legacy = {
      enterprise_ai: {
        endpoint: "https://legacy.example",
        project_ocid: "ocid1.legacy",
        api_key: "",
        has_api_key: true,
        clear_api_key: false,
        models: [{ model_id: "m", display_name: "", vision_enabled: true }],
        default_text_model_id: "",
        default_vision_model_id: "m",
      },
      generative_ai: { embedding_model: "e", embedding_dim: 1536, rerank_model: "r" },
    } as unknown as ModelSettingsPayload;
    const normalized = normalizeModelSettings(legacy);
    expect(normalized.enterprise_ai.connections).toEqual([
      {
        ...emptyConnection("primary"),
        endpoint: "https://legacy.example",
        project_ocid: "ocid1.legacy",
        has_api_key: true,
      },
    ]);
    expect(normalized.enterprise_ai.models[0]?.connection_id).toBe("primary");
    expect("endpoint" in normalized.enterprise_ai).toBe(false);
  });
});
