/**
 * OCI Enterprise AI の接続（プライマリ接続・セカンダリ接続）と、登録モデルが使う接続（#533 / #542）。
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
export const SECONDARY_CONNECTION_ID: EnterpriseAiConnectionId = "secondary";

/** 接続の入力欄（検証とフォーカスの順）。 */
export type ConnectionField = "endpoint" | "project_ocid" | "api_key";
export const CONNECTION_FIELD_ORDER: readonly ConnectionField[] = [
  "endpoint",
  "project_ocid",
  "api_key",
];

const CONNECTION_FIELD_ID_SUFFIX: Record<ConnectionField, string> = {
  endpoint: "endpoint",
  project_ocid: "project-ocid",
  api_key: "api-key",
};

/** 画面の入力欄の id（プライマリ接続は #533 より前からの id のまま）。 */
export function connectionFieldId(
  connectionId: EnterpriseAiConnectionId,
  field: ConnectionField,
): string {
  const suffix = CONNECTION_FIELD_ID_SUFFIX[field];
  return connectionId === PRIMARY_CONNECTION_ID
    ? `enterprise-${suffix}`
    : `enterprise-${connectionId}-${suffix}`;
}

/** 登録モデルの行の「接続」の選択欄の id。 */
export function modelConnectionFieldId(index: number): string {
  return `enterprise-model-connection-${index}`;
}

/**
 * 利用者に見せる接続の名前（タブの名前と同じ「プライマリ接続」「セカンダリ接続」。#542）。
 * 知らない ID はプライマリ接続（backend の `connection_label` と同じ）。
 */
export function connectionLabel(connectionId: string): string {
  return connectionId === SECONDARY_CONNECTION_ID
    ? t("settings.model.connection.secondary")
    : t("settings.model.connection.primary");
}

/** モデルの接続（未指定はプライマリ接続）。 */
export function modelConnectionId(model: EnterpriseAiConfiguredModel): string {
  return model.connection_id?.trim() || PRIMARY_CONNECTION_ID;
}

export function emptyConnection(
  connectionId: EnterpriseAiConnectionId,
): EnterpriseAiConnectionSettings {
  return {
    connection_id: connectionId,
    endpoint: "",
    project_ocid: "",
    api_key: "",
    has_api_key: false,
    clear_api_key: false,
  };
}

/** その接続が画面にあるか（セカンダリ接続は「設定」したときだけある）。 */
export function hasConnection(
  connections: readonly EnterpriseAiConnectionSettings[],
  connectionId: EnterpriseAiConnectionId,
): boolean {
  return connections.some((connection) => connection.connection_id === connectionId);
}

/** 登録モデルの「接続」の選択肢。タブの名前を出し、設定していない接続は出さない（#542）。 */
export function connectionOptions(
  connections: readonly EnterpriseAiConnectionSettings[],
): SelectFieldOption[] {
  return connections.map((connection) => ({
    value: connection.connection_id,
    label: connectionLabel(connection.connection_id),
  }));
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

/** 接続を消し、その接続を使っていたモデルをプライマリ接続に移す。 */
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

/** ない接続を指すモデルをプライマリ接続に移す。 */
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

/** 接続ごとの欄のエラー。key は接続の ID、その中の key は欄。 */
export type ConnectionErrors = Partial<
  Record<EnterpriseAiConnectionId, Partial<Record<ConnectionField, string>>>
>;

const CONNECTION_REQUIRED_MESSAGES = {
  endpoint: "settings.model.connection.error.endpointRequired",
  project_ocid: "settings.model.connection.error.projectRequired",
  api_key: "settings.model.connection.error.apiKeyRequired",
} as const satisfies Record<ConnectionField, Parameters<typeof t>[0]>;

/** 保存後に API key があるか（新しい key の入力、または保存済みの key を削除しない）。 */
export function connectionHasApiKey(connection: EnterpriseAiConnectionSettings): boolean {
  return Boolean(
    connection.api_key.trim() || (connection.has_api_key && !connection.clear_api_key),
  );
}

/**
 * 接続の入力のエラー（#542）。
 * - セカンダリ接続を設定したら、Endpoint URL・Project OCID・API key はすべて必須
 * - プライマリ接続は OCI で運用するときだけ必須（「OCI 運用時必須」の表示だけで、保存は止めない）
 */
export function validateConnections(
  connections: readonly EnterpriseAiConnectionSettings[],
): ConnectionErrors {
  const errors: ConnectionErrors = {};
  for (const connection of connections) {
    if (connection.connection_id === PRIMARY_CONNECTION_ID) continue;
    const present: Record<ConnectionField, boolean> = {
      endpoint: Boolean(connection.endpoint.trim()),
      project_ocid: Boolean(connection.project_ocid.trim()),
      api_key: connectionHasApiKey(connection),
    };
    const fieldErrors: Partial<Record<ConnectionField, string>> = {};
    for (const field of CONNECTION_FIELD_ORDER) {
      if (!present[field]) fieldErrors[field] = t(CONNECTION_REQUIRED_MESSAGES[field]);
    }
    if (Object.keys(fieldErrors).length) errors[connection.connection_id] = fieldErrors;
  }
  return errors;
}

/** 最初のエラーの欄（タブの並び → 欄の並び）。なければ null。 */
export function firstConnectionError(
  errors: ConnectionErrors,
): { connectionId: EnterpriseAiConnectionId; field: ConnectionField } | null {
  for (const connectionId of ENTERPRISE_AI_CONNECTION_IDS) {
    const field = CONNECTION_FIELD_ORDER.find((name) => errors[connectionId]?.[name]);
    if (field) return { connectionId, field };
  }
  return null;
}

/**
 * 保存済みの接続から変えた接続（タブに「未保存」を出す。#542）。
 * 追加しただけ・削除しただけの接続も含む。API key は入力（応答では空）・削除の指定があれば変更とみなす。
 */
export function unsavedConnectionIds(
  draft: readonly EnterpriseAiConnectionSettings[],
  saved: readonly EnterpriseAiConnectionSettings[],
): Set<EnterpriseAiConnectionId> {
  const changed = new Set<EnterpriseAiConnectionId>();
  for (const connectionId of ENTERPRISE_AI_CONNECTION_IDS) {
    const current = draft.find((item) => item.connection_id === connectionId);
    const baseline = saved.find((item) => item.connection_id === connectionId);
    if (!current && !baseline) continue;
    if (
      !current ||
      !baseline ||
      current.endpoint.trim() !== baseline.endpoint.trim() ||
      current.project_ocid.trim() !== baseline.project_ocid.trim() ||
      current.api_key.trim() !== baseline.api_key.trim() ||
      current.clear_api_key !== baseline.clear_api_key
    ) {
      changed.add(connectionId);
    }
  }
  return changed;
}

/** 登録モデルの行ごとの「接続」のエラー。key は行の位置。 */
export type ModelConnectionErrors = Partial<Record<number, string>>;

/**
 * 登録モデルが指す接続の検証。
 * - 画面にない接続（削除した・env から消えた）を指す → 選び直しを案内する
 * - 設定しただけでまだ保存していない接続を指す → 先に接続を保存するよう案内する
 *   （登録モデルの節の保存は、保存済みの接続で検証されるため）
 */
export function validateModelConnections(
  models: readonly EnterpriseAiConfiguredModel[],
  connections: readonly EnterpriseAiConnectionSettings[],
  savedConnectionIds: readonly string[],
): ModelConnectionErrors {
  const available = new Set<string>(
    connections.map((connection) => connection.connection_id),
  );
  const saved = new Set(savedConnectionIds);
  const errors: ModelConnectionErrors = {};
  models.forEach((model, index) => {
    const modelId = model.model_id.trim();
    if (!modelId) return;
    const connectionId = modelConnectionId(model);
    if (!available.has(connectionId)) {
      errors[index] = t("settings.model.connection.error.missing", {
        model: model.display_name.trim() || modelId,
      });
    } else if (!saved.has(connectionId)) {
      errors[index] = t("settings.model.connection.error.unsaved", {
        connection: connectionLabel(connectionId),
      });
    }
  });
  return errors;
}

/**
 * 画面の編集用に整える。接続の一覧がない応答（#533 より前の形）は、接続 1 組をプライマリ接続にする。
 * 接続は画面が扱う項目だけにする（#533 の表示名 `display_name` が残った応答も読める。#542）。
 * モデルの接続の未指定はプライマリ接続にする。
 */
export function normalizeModelSettings(
  settings: ModelSettingsPayload,
): ModelSettingsPayload {
  const enterprise = settings.enterprise_ai as EnterpriseAiModelSettings &
    Partial<Omit<EnterpriseAiConnectionSettings, "connection_id">>;
  const connections = Array.isArray(enterprise.connections)
    ? enterprise.connections.map((connection) => ({
        ...emptyConnection(connection.connection_id),
        endpoint: connection.endpoint ?? "",
        project_ocid: connection.project_ocid ?? "",
        api_key: connection.api_key ?? "",
        has_api_key: connection.has_api_key ?? false,
        clear_api_key: connection.clear_api_key ?? false,
      }))
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
