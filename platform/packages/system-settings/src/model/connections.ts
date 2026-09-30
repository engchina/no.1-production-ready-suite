/**
 * OCI Enterprise AI の接続（最大 2 件）と、登録モデルが使う接続（#533）。
 * 検証の規則は backend の `pr_system_settings.model.validate_enterprise_ai_connections` と同じ。
 */
import type { SelectFieldOption } from "@engchina/production-ready-ui";

import { t } from "./messages";
import {
  ENTERPRISE_AI_CONNECTION_IDS,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiConnectionId,
  type EnterpriseAiConnectionSettings,
  type EnterpriseAiModelSettings,
  type ModelSettingsPayload,
} from "./types";

export const PRIMARY_CONNECTION_ID: EnterpriseAiConnectionId = "primary";

/** 画面の入力欄の id（接続 1 は #533 より前からの id のまま）。 */
export function connectionFieldId(
  connectionId: EnterpriseAiConnectionId,
  field: "display-name" | "endpoint" | "project-ocid" | "api-key",
): string {
  if (connectionId === PRIMARY_CONNECTION_ID) {
    return field === "display-name"
      ? "enterprise-connection-name"
      : `enterprise-${field}`;
  }
  return `enterprise-${connectionId}-${field}`;
}

/** 登録モデルの行の「接続」の選択欄の id。 */
export function modelConnectionFieldId(index: number): string {
  return `enterprise-model-connection-${index}`;
}

/** 接続の番号（1 始まり）。 */
export function connectionNumber(connectionId: string): number {
  const index = ENTERPRISE_AI_CONNECTION_IDS.indexOf(
    connectionId as EnterpriseAiConnectionId,
  );
  return (index < 0 ? 0 : index) + 1;
}

/** 利用者に見せる接続の名前（表示名が空なら「接続 1」「接続 2」）。 */
export function connectionLabel(
  connection: Pick<EnterpriseAiConnectionSettings, "connection_id" | "display_name">,
): string {
  return (
    connection.display_name.trim() ||
    t("settings.model.connection.name", {
      number: connectionNumber(connection.connection_id),
    })
  );
}

/** モデルの接続（未指定は接続 1）。 */
export function modelConnectionId(model: EnterpriseAiConfiguredModel): string {
  return model.connection_id?.trim() || PRIMARY_CONNECTION_ID;
}

export function emptyConnection(
  connectionId: EnterpriseAiConnectionId,
): EnterpriseAiConnectionSettings {
  return {
    connection_id: connectionId,
    display_name: "",
    endpoint: "",
    project_ocid: "",
    api_key: "",
    has_api_key: false,
    clear_api_key: false,
  };
}

/** 次に追加できる接続（上限に達していれば null）。 */
export function nextConnectionId(
  connections: readonly EnterpriseAiConnectionSettings[],
): EnterpriseAiConnectionId | null {
  const used = new Set(connections.map((connection) => connection.connection_id));
  return ENTERPRISE_AI_CONNECTION_IDS.find((id) => !used.has(id)) ?? null;
}

/** 登録モデルの「接続」の選択肢。表示名を付けた接続は、番号を説明に出す。 */
export function connectionOptions(
  connections: readonly EnterpriseAiConnectionSettings[],
): SelectFieldOption[] {
  return connections.map((connection) => {
    const fallback = t("settings.model.connection.name", {
      number: connectionNumber(connection.connection_id),
    });
    const label = connectionLabel(connection);
    return label === fallback
      ? { value: connection.connection_id, label }
      : { value: connection.connection_id, label, description: fallback };
  });
}

/** その接続を使っている登録モデル（モデル ID を入力した行だけ）。 */
export function modelsUsingConnection(
  models: readonly EnterpriseAiConfiguredModel[],
  connectionId: string,
): EnterpriseAiConfiguredModel[] {
  return models.filter(
    (model) => model.model_id.trim() && modelConnectionId(model) === connectionId,
  );
}

/** 接続を消し、その接続を使っていたモデルを接続 1 に移す。 */
export function removeConnection(
  enterprise: EnterpriseAiModelSettings,
  connectionId: EnterpriseAiConnectionId,
): EnterpriseAiModelSettings {
  return {
    ...enterprise,
    connections: enterprise.connections.filter(
      (connection) => connection.connection_id !== connectionId,
    ),
    models: moveModelsToAvailableConnections(
      enterprise.models,
      enterprise.connections
        .filter((connection) => connection.connection_id !== connectionId)
        .map((connection) => connection.connection_id),
    ),
  };
}

/** ない接続を指すモデルを接続 1 に移す。 */
export function moveModelsToAvailableConnections(
  models: readonly EnterpriseAiConfiguredModel[],
  available: readonly string[],
): EnterpriseAiConfiguredModel[] {
  const ids = new Set(available);
  return models.map((model) => {
    const connectionId = modelConnectionId(model);
    return {
      ...model,
      connection_id: ids.has(connectionId) ? connectionId : PRIMARY_CONNECTION_ID,
    };
  });
}

export type ConnectionErrors = Partial<Record<EnterpriseAiConnectionId, string>>;

/** 接続の入力のエラー（接続 2 を追加したら Endpoint URL は必須）。key は接続の ID。 */
export function validateConnections(
  connections: readonly EnterpriseAiConnectionSettings[],
): ConnectionErrors {
  const errors: ConnectionErrors = {};
  connections.forEach((connection, index) => {
    if (index > 0 && !connection.endpoint.trim()) {
      errors[connection.connection_id] = t(
        "settings.model.connection.error.endpointRequired",
        { connection: connectionLabel(connection) },
      );
    }
  });
  return errors;
}

/** 登録モデルの行ごとの「接続」のエラー。key は行の位置。 */
export type ModelConnectionErrors = Partial<Record<number, string>>;

/**
 * 登録モデルが指す接続の検証。
 * - 画面にない接続（削除した・env から消えた）を指す → 選び直しを案内する
 * - 追加しただけでまだ保存していない接続を指す → 先に接続を保存するよう案内する
 *   （登録モデルの節の保存は、保存済みの接続で検証されるため）
 */
export function validateModelConnections(
  models: readonly EnterpriseAiConfiguredModel[],
  connections: readonly EnterpriseAiConnectionSettings[],
  savedConnectionIds: readonly string[],
): ModelConnectionErrors {
  const available = new Map(
    connections.map((connection) => [connection.connection_id as string, connection]),
  );
  const saved = new Set(savedConnectionIds);
  const errors: ModelConnectionErrors = {};
  models.forEach((model, index) => {
    const modelId = model.model_id.trim();
    if (!modelId) return;
    const connectionId = modelConnectionId(model);
    const connection = available.get(connectionId);
    if (!connection) {
      errors[index] = t("settings.model.connection.error.missing", {
        model: model.display_name.trim() || modelId,
      });
    } else if (!saved.has(connectionId)) {
      errors[index] = t("settings.model.connection.error.unsaved", {
        connection: connectionLabel(connection),
      });
    }
  });
  return errors;
}

/**
 * 画面の編集用に整える。接続の一覧がない応答（#533 より前の形）は、接続 1 組を接続 1 にする。
 * モデルの接続の未指定は接続 1 にする。
 */
export function normalizeModelSettings(
  settings: ModelSettingsPayload,
): ModelSettingsPayload {
  const enterprise = settings.enterprise_ai as EnterpriseAiModelSettings &
    Partial<Omit<EnterpriseAiConnectionSettings, "connection_id" | "display_name">>;
  const connections = Array.isArray(enterprise.connections)
    ? enterprise.connections.map((connection) => ({ ...connection }))
    : [
        {
          ...emptyConnection(PRIMARY_CONNECTION_ID),
          endpoint: enterprise.endpoint ?? "",
          project_ocid: enterprise.project_ocid ?? "",
          has_api_key: enterprise.has_api_key ?? false,
        },
      ];
  const {
    endpoint: _endpoint,
    project_ocid: _project,
    api_key: _apiKey,
    has_api_key: _hasApiKey,
    clear_api_key: _clearApiKey,
    ...rest
  } = enterprise;
  return {
    enterprise_ai: {
      ...rest,
      vlm_input_mode: enterprise.vlm_input_mode ?? "auto",
      connections,
      models: enterprise.models.map((model) => ({
        ...model,
        connection_id: modelConnectionId(model),
      })),
    },
    generative_ai: { ...settings.generative_ai },
  };
}
