import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ErrorState,
  FormActionBar,
  FormStatus,
  PageBody,
  ProcessingIndicator,
  RequiredBadge,
  SecretField,
  SelectField,
  Skeleton,
  Switch,
  TextField,
  cn,
  toast,
  useConfirm,
} from "@engchina/production-ready-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cpu,
  Database,
  ListChecks,
  Plus,
  Save,
  TestTube2,
  Trash2,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import {
  useSettingsDraftGuard,
  type DraftGuardMessages,
} from "../guards/useSettingsDraftGuard";
import { InputActionField } from "../oci/InputActionField";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "../oci/SettingsTestResultPanel";
import {
  PRIMARY_CONNECTION_ID,
  connectionFieldId,
  connectionLabel,
  connectionOptions,
  emptyConnection,
  modelConnectionFieldId,
  modelConnectionId,
  modelsUsingConnection,
  moveModelsToAvailableConnections,
  nextConnectionId,
  normalizeModelSettings,
  removeConnection,
  validateConnections,
  validateModelConnections,
  type ConnectionErrors,
  type ModelConnectionErrors,
} from "./connections";
import {
  DEFAULT_MODEL_FIELD_IDS,
  DEFAULT_MODEL_FIELD_ORDER,
  followModelChange,
  textModelOptions,
  validateDefaultModels,
  visionModelOptions,
  type DefaultModelErrors,
  type DefaultModelField,
} from "./defaultModels";
import { t } from "./messages";
import {
  MAX_ENTERPRISE_AI_CONNECTIONS,
  MODEL_SETTINGS_QUERY_KEY,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiConnectionId,
  type EnterpriseAiConnectionSettings,
  type EnterpriseAiModelSettings,
  type GenerativeAiModelSettings,
  type ModelSettingsApi,
  type ModelSettingsData,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type ModelSettingsTestResult,
  type ModelSettingsTestTargetType,
} from "./types";

type ModelTestKey = `enterprise:${number}` | "embedding" | "rerank";
type ModelSaveSection =
  "enterprise_connection" | "enterprise_models" | "generative_ai";

export interface ModelSettingsPageProps {
  /** 製品の API 関数（GET / PATCH /api/settings/model、POST /api/settings/model/test）。 */
  api: ModelSettingsApi;
  /** API エラーから画面に出すメッセージを取り出す（製品の ApiError など）。undefined なら既定の文言。 */
  errorMessage?: (error: unknown) => string | undefined;
  /** 入力欄の placeholder（登録モデルの表示名の例など、製品ごとの値）。 */
  placeholders?: { displayName?: string };
  /** 離脱確認ダイアログの文言の上書き。 */
  draftGuardMessages?: Partial<DraftGuardMessages>;
}

/**
 * モデル設定（3製品共通。NL2SQL の画面を基準に移設。#103）。PageHeader は製品の route が描く。
 *
 * - 3節（Enterprise AI 接続 / 登録モデル / Generative AI）をそれぞれ保存する。
 *   画面にない項目（API path・VLM 入力方式・timeout など）は保存済みの値をそのまま送る
 * - Enterprise AI の接続は 2 件まで（#533）。接続ごとのカードで入力し、登録モデルの行で使う接続を選ぶ。
 *   接続 2 の削除は、使っているモデルがあれば接続 1 に移すか確認する
 * - 登録モデルの節の下で、既定の Vision モデル（必須）と既定のテキストモデル（任意）を選ぶ（#499）。
 *   選んだモデルの削除・Vision 対応のオフ・保存の操作で、保存前にフィールドのエラーを出す
 * - 保存中・テスト中は入力を止め、未保存のまま離れようとすると確認する
 */
export function ModelSettingsPage({
  api,
  errorMessage,
  placeholders,
  draftGuardMessages,
}: ModelSettingsPageProps) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: MODEL_SETTINGS_QUERY_KEY,
    queryFn: ({ signal }) => api.getModelSettings({ signal }),
  });
  const updateMutation = useMutation({
    mutationFn: (payload: ModelSettingsPayload) =>
      api.updateModelSettings(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: MODEL_SETTINGS_QUERY_KEY,
      });
    },
  });
  const testMutation = useMutation({
    mutationFn: (request: ModelSettingsTestRequest) =>
      api.testModelSettings(request),
  });
  const confirm = useConfirm();
  const displayNamePlaceholder =
    placeholders?.displayName ?? t("settings.model.placeholder.displayName");

  const [draft, setDraft] = useState<ModelSettingsPayload | null>(null);
  const [baselineData, setBaselineData] = useState<ModelSettingsData | null>(
    null,
  );
  const [checkData, setCheckData] = useState<ModelSettingsData | null>(null);
  const [saveErrors, setSaveErrors] = useState<
    Partial<Record<ModelSaveSection, string>>
  >({});
  const [activeSaveSection, setActiveSaveSection] =
    useState<ModelSaveSection | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState<
    Partial<Record<EnterpriseAiConnectionId, boolean>>
  >({});
  // 接続の入力のエラー（接続 2 の Endpoint URL の未入力）は、保存の操作の後から出す。
  const [showConnectionErrors, setShowConnectionErrors] = useState(false);
  // 登録モデルの「接続」のエラーは、接続を選んだ・削除した・保存の操作の後から出す（#533）。
  const [showModelConnectionErrors, setShowModelConnectionErrors] =
    useState(false);
  // 既定のモデルのエラーは、関係する操作（選択・Vision 対応の切替・削除・保存）の後から出す。
  // モデル ID の入力中（キー入力ごと）には出さない（messaging.md §3.2）。
  const [showDefaultErrors, setShowDefaultErrors] = useState(false);
  const [testingKey, setTestingKey] = useState<ModelTestKey | null>(null);
  const [testResults, setTestResults] = useState<
    Partial<Record<ModelTestKey, ModelSettingsTestResult>>
  >({});

  useEffect(() => {
    if (!query.data || draft) return;
    const loaded = cloneSettings(query.data.settings);
    setDraft(loaded);
    // 保存済みの状態が不正（旧設定で Vision 対応のモデルがない等）なら、開いた時点で案内する。
    setShowDefaultErrors(
      Object.keys(validateDefaultModels(loaded.enterprise_ai)).length > 0,
    );
    setShowModelConnectionErrors(
      Object.keys(
        validateModelConnections(
          loaded.enterprise_ai.models,
          loaded.enterprise_ai.connections,
          savedConnectionIds(loaded),
        ),
      ).length > 0,
    );
    setBaselineData(query.data);
    setCheckData(query.data);
  }, [draft, query.data]);

  const canSubmit = Boolean(draft);
  const saveInProgress = activeSaveSection !== null;
  const operationBusy = saveInProgress || testingKey !== null;
  useSettingsDraftGuard(
    Boolean(
      draft &&
      baselineData &&
      JSON.stringify(draft) !==
        JSON.stringify(cloneSettings(baselineData.settings)),
    ),
    operationBusy,
    draftGuardMessages,
  );
  const legacySecretDetected =
    checkData?.legacy_secret_detected ??
    baselineData?.legacy_secret_detected ??
    query.data?.legacy_secret_detected ??
    false;

  const updateEnterprise = <K extends keyof EnterpriseAiModelSettings>(
    key: K,
    value: EnterpriseAiModelSettings[K],
  ) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: { ...current.enterprise_ai, [key]: value },
          }
        : current,
    );
    setCheckData(baselineData);
    setTestResults({});
    const isDefaultModel =
      key === "default_text_model_id" || key === "default_vision_model_id";
    if (isDefaultModel) setShowDefaultErrors(true);
    clearSaveError(isDefaultModel ? "enterprise_models" : "enterprise_connection");
  };

  const updateGenerative = <K extends keyof GenerativeAiModelSettings>(
    key: K,
    value: GenerativeAiModelSettings[K],
  ) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            generative_ai: { ...current.generative_ai, [key]: value },
          }
        : current,
    );
    setCheckData(baselineData);
    setTestResults((current) => ({
      ...current,
      embedding: undefined,
      rerank: undefined,
    }));
    clearSaveError("generative_ai");
  };

  const updateConnection = (
    connectionId: EnterpriseAiConnectionId,
    patch: Partial<Omit<EnterpriseAiConnectionSettings, "connection_id">>,
  ) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: {
              ...current.enterprise_ai,
              connections: current.enterprise_ai.connections.map((connection) =>
                connection.connection_id === connectionId
                  ? {
                      ...connection,
                      ...patch,
                      // 削除の指定と新しい key の入力は両立しない。
                      ...(patch.clear_api_key ? { api_key: "" } : {}),
                    }
                  : connection,
              ),
            },
          }
        : current,
    );
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_connection");
  };

  const addConnection = () => {
    const connectionId = draft ? nextConnectionId(draft.enterprise_ai.connections) : null;
    if (!connectionId) return;
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: {
              ...current.enterprise_ai,
              connections: [
                ...current.enterprise_ai.connections,
                emptyConnection(connectionId),
              ],
            },
          }
        : current,
    );
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_connection");
    // 追加した接続の最初の欄へフォーカスする（描画の後）。
    requestAnimationFrame(() =>
      document.getElementById(connectionFieldId(connectionId, "display-name"))?.focus(),
    );
  };

  const removeConnectionWithConfirm = async (
    connection: EnterpriseAiConnectionSettings,
  ) => {
    if (!draft) return;
    const label = connectionLabel(connection);
    const inUse = modelsUsingConnection(
      draft.enterprise_ai.models,
      connection.connection_id,
    );
    const ok = await confirm({
      title: t("settings.model.connection.removeConfirm.title", {
        connection: label,
      }),
      description: inUse.length
        ? t("settings.model.connection.removeConfirm.descriptionInUse", {
            connection: label,
            models: inUse
              .map((model) => model.display_name.trim() || model.model_id.trim())
              .join("、"),
          })
        : t("settings.model.connection.removeConfirm.description", {
            connection: label,
          }),
      confirmLabel: inUse.length
        ? t("settings.model.connection.removeConfirm.moveAndRemove")
        : t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: removeConnection(
              current.enterprise_ai,
              connection.connection_id,
            ),
          }
        : current,
    );
    setApiKeyVisible((current) => ({
      ...current,
      [connection.connection_id]: false,
    }));
    setShowModelConnectionErrors(true);
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_connection");
    clearSaveError("enterprise_models");
  };

  const updateEnterpriseModel = (
    index: number,
    patch: Partial<EnterpriseAiConfiguredModel>,
  ) => {
    setDraft((current) => {
      if (!current) return current;
      const previous = current.enterprise_ai.models[index];
      const models = current.enterprise_ai.models.map((model, modelIndex) =>
        modelIndex === index ? { ...model, ...patch } : model,
      );
      const next = models[index];
      return {
        ...current,
        enterprise_ai: {
          ...current.enterprise_ai,
          models,
          ...(next
            ? followModelChange(current.enterprise_ai, previous, next)
            : {}),
        },
      };
    });
    if (patch.vision_enabled !== undefined) setShowDefaultErrors(true);
    if (patch.connection_id !== undefined) setShowModelConnectionErrors(true);
    setCheckData(baselineData);
    setTestResults((current) => ({
      ...current,
      [`enterprise:${index}`]: undefined,
    }));
    clearSaveError("enterprise_models");
  };

  const addEnterpriseModel = () => {
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: {
              ...current.enterprise_ai,
              models: [
                ...current.enterprise_ai.models,
                {
                  model_id: "",
                  display_name: "",
                  vision_enabled: false,
                  connection_id: PRIMARY_CONNECTION_ID,
                },
              ],
            },
          }
        : current,
    );
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_models");
  };

  const removeEnterpriseModel = async (index: number) => {
    const target = draft?.enterprise_ai.models[index]?.model_id?.trim();
    const ok = await confirm({
      title: t("settings.model.enterprise.removeConfirm.title"),
      description: target
        ? t("settings.model.enterprise.removeConfirm.description", {
            model: target,
          })
        : t("settings.model.enterprise.removeConfirm.descriptionUnnamed"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    // 既定に選んでいたモデルを消しても既定は変えず、フィールドのエラーで選び直しを案内する。
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: {
              ...current.enterprise_ai,
              models: current.enterprise_ai.models.filter(
                (_, modelIndex) => modelIndex !== index,
              ),
            },
          }
        : current,
    );
    setShowDefaultErrors(true);
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_models");
  };

  function clearSaveError(section: ModelSaveSection) {
    setSaveErrors((current) => ({ ...current, [section]: undefined }));
  }

  const handleTestModel = async (
    key: ModelTestKey,
    target: Omit<ModelSettingsTestRequest, "settings">,
  ) => {
    if (!draft || operationBusy) return;
    setTestResults((current) => ({ ...current, [key]: undefined }));
    setTestingKey(key);
    try {
      const result = await testMutation.mutateAsync({
        ...target,
        settings: draft,
      });
      setTestResults((current) => ({ ...current, [key]: result }));
    } catch (error) {
      const message =
        errorMessage?.(error) ?? t("settings.model.test.apiFailed");
      setTestResults((current) => ({
        ...current,
        [key]: buildClientSideTestFailure(target, message),
      }));
    } finally {
      setTestingKey(null);
    }
  };

  const handleSubmit = async (
    event: FormEvent<HTMLFormElement>,
    section: ModelSaveSection,
  ) => {
    event.preventDefault();
    if (!draft || !baselineData || operationBusy) return;
    clearSaveError(section);
    // 送信を止めるときは、最初の不正な欄へフォーカスする（messaging.md §3.2）。
    if (section === "enterprise_connection") {
      const errors = validateConnections(draft.enterprise_ai.connections);
      const firstInvalid = draft.enterprise_ai.connections.find(
        (connection) => errors[connection.connection_id],
      );
      if (firstInvalid) {
        setShowConnectionErrors(true);
        document
          .getElementById(connectionFieldId(firstInvalid.connection_id, "endpoint"))
          ?.focus();
        return;
      }
    }
    if (section === "enterprise_models") {
      const connectionErrors = validateModelConnections(
        draft.enterprise_ai.models,
        draft.enterprise_ai.connections,
        savedConnectionIds(baselineData.settings),
      );
      const invalidRow = draft.enterprise_ai.models.findIndex(
        (_, index) => connectionErrors[index],
      );
      const errors = validateDefaultModels(draft.enterprise_ai);
      const firstInvalid = DEFAULT_MODEL_FIELD_ORDER.find(
        (field) => errors[field],
      );
      if (invalidRow >= 0 || firstInvalid) {
        setShowModelConnectionErrors(true);
        setShowDefaultErrors(true);
        document
          .getElementById(
            invalidRow >= 0
              ? modelConnectionFieldId(invalidRow)
              : DEFAULT_MODEL_FIELD_IDS[firstInvalid!],
          )
          ?.focus();
        return;
      }
    }
    setActiveSaveSection(section);
    try {
      const payload = buildSectionSavePayload(
        baselineData.settings,
        draft,
        section,
      );
      const data = await updateMutation.mutateAsync(payload);
      const saved = cloneSettings(data.settings);
      setDraft((current) =>
        current ? mergeSavedSectionIntoDraft(current, saved, section) : saved,
      );
      setBaselineData(data);
      setCheckData(data);
      toast.success(t(MODEL_SAVE_SUCCESS_KEYS[section]));
    } catch (error) {
      setSaveErrors((current) => ({
        ...current,
        [section]: errorMessage?.(error) ?? t("settings.model.saveError"),
      }));
    } finally {
      setActiveSaveSection(null);
    }
  };

  const defaultModelErrors = draft
    ? validateDefaultModels(draft.enterprise_ai)
    : {};
  const connectionErrors: ConnectionErrors =
    draft && showConnectionErrors
      ? validateConnections(draft.enterprise_ai.connections)
      : {};
  const modelConnectionErrors: ModelConnectionErrors =
    draft && baselineData && showModelConnectionErrors
      ? validateModelConnections(
          draft.enterprise_ai.models,
          draft.enterprise_ai.connections,
          savedConnectionIds(baselineData.settings),
        )
      : {};

  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState
          message={errorMessage?.(query.error) ?? t("settings.model.loadError")}
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  if (query.isPending || !draft) {
    return (
      <PageBody wide>
        <div
          role="status"
          aria-busy="true"
          className="space-y-4"
          data-testid="settings-model-loading"
        >
          <p className="text-sm text-fg-muted">{t("settings.model.loading")}</p>
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-72 w-full rounded-lg" />
          <Skeleton className="h-44 w-full rounded-lg" />
        </div>
      </PageBody>
    );
  }

  return (
    <PageBody wide>
      {legacySecretDetected ? (
        <Banner
          severity="warning"
          title={t("settings.model.legacySecret.title")}
        >
          {t("settings.model.legacySecret.description")}
        </Banner>
      ) : null}
      <fieldset
        disabled={operationBusy}
        aria-busy={operationBusy}
        className="min-w-0 space-y-6"
      >
        <form
          onSubmit={(event) =>
            void handleSubmit(event, "enterprise_connection")
          }
        >
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Cpu size={16} className="text-accent-fg" aria-hidden />
                {t("settings.model.enterprise.title")}
              </CardTitle>
              <CardDescription>
                {t("settings.model.enterprise.description")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {draft.enterprise_ai.connections.map((connection) => (
                <ConnectionPanel
                  key={connection.connection_id}
                  connection={connection}
                  error={connectionErrors[connection.connection_id]}
                  apiKeyVisible={Boolean(apiKeyVisible[connection.connection_id])}
                  onApiKeyVisibleChange={(visible) =>
                    setApiKeyVisible((current) => ({
                      ...current,
                      [connection.connection_id]: visible,
                    }))
                  }
                  onChange={(patch) =>
                    updateConnection(connection.connection_id, patch)
                  }
                  onRemove={
                    connection.connection_id === PRIMARY_CONNECTION_ID
                      ? undefined
                      : () => void removeConnectionWithConfirm(connection)
                  }
                />
              ))}
              {nextConnectionId(draft.enterprise_ai.connections) ? (
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={Plus}
                    onClick={addConnection}
                    className="w-full sm:w-auto"
                  >
                    {t("settings.model.connection.add")}
                  </Button>
                  <p className="text-xs leading-relaxed text-fg-muted">
                    {t("settings.model.connection.addHelp", {
                      max: MAX_ENTERPRISE_AI_CONNECTIONS,
                    })}
                  </p>
                </div>
              ) : null}
              <ModelFormActions
                sectionLabel={t("settings.model.enterprise.title")}
                canSubmit={canSubmit}
                saving={activeSaveSection === "enterprise_connection"}
                disabled={saveInProgress}
                errorText={saveErrors.enterprise_connection}
              />
            </CardContent>
          </Card>
        </form>

        <form
          onSubmit={(event) => void handleSubmit(event, "enterprise_models")}
        >
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ListChecks size={16} className="text-accent-fg" aria-hidden />
                {t("settings.model.enterprise.models")}
              </CardTitle>
              <CardDescription>
                {t("settings.model.enterprise.modelsDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <ModelCatalogEditor
                models={draft.enterprise_ai.models}
                connections={draft.enterprise_ai.connections}
                connectionErrors={modelConnectionErrors}
                testingKey={testingKey}
                testResults={testResults}
                onModelChange={updateEnterpriseModel}
                onAdd={addEnterpriseModel}
                onRemove={removeEnterpriseModel}
                onTest={(key, target) => void handleTestModel(key, target)}
                displayNamePlaceholder={displayNamePlaceholder}
              />
              <DefaultModelFields
                enterprise={draft.enterprise_ai}
                errors={showDefaultErrors ? defaultModelErrors : {}}
                onChange={(field, value) => updateEnterprise(field, value)}
              />
              <ModelFormActions
                sectionLabel={t("settings.model.enterprise.models")}
                canSubmit={canSubmit}
                saving={activeSaveSection === "enterprise_models"}
                disabled={saveInProgress}
                errorText={saveErrors.enterprise_models}
              />
            </CardContent>
          </Card>
        </form>

        <form onSubmit={(event) => void handleSubmit(event, "generative_ai")}>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database size={16} className="text-accent-fg" aria-hidden />
                {t("settings.model.genai.title")}
              </CardTitle>
              <CardDescription>
                {t("settings.model.genai.description")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* 3 項目とも短い値なので、広い画面（2xl）では 1 行に並べる。 */}
              <div className="grid gap-x-6 gap-y-5 md:grid-cols-2 2xl:grid-cols-3">
                <TestableTextField
                  id="genai-embedding-model"
                  label={t("settings.model.genai.embeddingModel")}
                  value={draft.generative_ai.embedding_model}
                  placeholder={t("settings.model.placeholder.embeddingModel")}
                  onChange={(value) =>
                    updateGenerative("embedding_model", value)
                  }
                  testResult={testResults.embedding}
                  testing={testingKey === "embedding"}
                  onTest={() =>
                    void handleTestModel("embedding", {
                      target_type: "embedding",
                      model_id: draft.generative_ai.embedding_model,
                      vision_enabled: false,
                    })
                  }
                />
                <NumberField
                  id="genai-embedding-dim"
                  label={t("settings.model.genai.embeddingDim")}
                  badge={t("settings.model.fixed")}
                  value={draft.generative_ai.embedding_dim}
                  min={1536}
                  max={1536}
                  step={1}
                  readOnly
                  helper={t("settings.model.genai.embeddingDimHelp")}
                  onChange={(value) => updateGenerative("embedding_dim", value)}
                />
                <TestableTextField
                  id="genai-rerank-model"
                  label={t("settings.model.genai.rerankModel")}
                  value={draft.generative_ai.rerank_model}
                  placeholder={t("settings.model.placeholder.rerankModel")}
                  onChange={(value) => updateGenerative("rerank_model", value)}
                  className="md:col-span-2 2xl:col-span-1"
                  testResult={testResults.rerank}
                  testing={testingKey === "rerank"}
                  onTest={() =>
                    void handleTestModel("rerank", {
                      target_type: "rerank",
                      model_id: draft.generative_ai.rerank_model,
                      vision_enabled: false,
                    })
                  }
                />
              </div>
              <ModelFormActions
                sectionLabel={t("settings.model.genai.title")}
                canSubmit={canSubmit}
                saving={activeSaveSection === "generative_ai"}
                disabled={saveInProgress}
                errorText={saveErrors.generative_ai}
              />
            </CardContent>
          </Card>
        </form>
      </fieldset>
    </PageBody>
  );
}

/**
 * 接続 1 件のカード（#533）。接続 1 は #533 より前の入力欄そのまま（id も同じ）。
 * 接続 2 は削除でき、Endpoint URL は必須。削除は同じカードの見出しの行の右端に置く
 * （主操作の保存と隣に並べない。README「カード内の操作行」）。
 */
function ConnectionPanel({
  connection,
  error,
  apiKeyVisible,
  onApiKeyVisibleChange,
  onChange,
  onRemove,
}: {
  connection: EnterpriseAiConnectionSettings;
  error?: string;
  apiKeyVisible: boolean;
  onApiKeyVisibleChange: (visible: boolean) => void;
  onChange: (
    patch: Partial<Omit<EnterpriseAiConnectionSettings, "connection_id">>,
  ) => void;
  onRemove?: () => void;
}) {
  const id = connection.connection_id;
  const primary = id === PRIMARY_CONNECTION_ID;
  const headingId = `enterprise-connection-${id}-title`;
  const defaultName = connectionLabel({ connection_id: id, display_name: "" });
  const title = primary ? t("settings.model.connection.primaryTitle") : defaultName;
  return (
    <section
      aria-labelledby={headingId}
      data-testid={`enterprise-connection-${id}`}
      className="space-y-4 rounded-md border border-border bg-surface-sunken p-4"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h3 id={headingId} className="text-sm font-semibold text-fg">
            {title}
          </h3>
          <p className="text-xs leading-relaxed text-fg-muted">
            {primary
              ? t("settings.model.connection.primaryDescription")
              : t("settings.model.connection.secondaryDescription")}
          </p>
        </div>
        {onRemove ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            tone="danger"
            icon={Trash2}
            aria-label={`${title}: ${t("settings.model.connection.remove")}`}
            onClick={onRemove}
            className="w-full sm:ml-auto sm:w-auto"
          >
            {t("settings.model.connection.remove")}
          </Button>
        ) : null}
      </div>
      {/* 表示名と Endpoint URL は広い画面（xl）で同じ行。Project OCID と API キーは 2xl で同じ行に置く。 */}
      <div className="grid gap-x-6 gap-y-5 md:grid-cols-2">
        <TextField
          id={connectionFieldId(id, "display-name")}
          label={t("settings.model.connection.displayName")}
          value={connection.display_name}
          placeholder={defaultName}
          helper={t("settings.model.connection.displayNameHelp", {
            name: defaultName,
          })}
          onValueChange={(value) => onChange({ display_name: value })}
          className="md:col-span-2 xl:col-span-1"
        />
        <TextField
          id={connectionFieldId(id, "endpoint")}
          label={t("settings.model.enterprise.endpoint")}
          required
          requiredLabel={
            primary ? t("settings.model.requiredInOci") : t("settings.model.required")
          }
          value={connection.endpoint}
          placeholder={t("settings.model.placeholder.endpoint")}
          error={error}
          helper={
            <>
              <span className="block">
                {t("settings.model.enterprise.endpointHelp")}
              </span>
              <a
                href="https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-compatible-api.htm"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 items-center rounded-sm text-accent-fg underline underline-offset-4 hover:text-accent-fg"
              >
                {t("settings.model.enterprise.endpointDocs")}
              </a>
            </>
          }
          onValueChange={(value) => onChange({ endpoint: value })}
          className="md:col-span-2 xl:col-span-1"
        />
        <TextField
          id={connectionFieldId(id, "project-ocid")}
          label={t("settings.model.enterprise.project")}
          required
          requiredLabel={t("settings.model.requiredInOci")}
          value={connection.project_ocid}
          placeholder={t("settings.model.placeholder.project")}
          helper={t("settings.model.enterprise.projectHelp")}
          onValueChange={(value) => onChange({ project_ocid: value })}
          className="md:col-span-2 2xl:col-span-1"
        />
        <SecretField
          id={connectionFieldId(id, "api-key")}
          label={t("settings.model.enterprise.apiKey")}
          value={connection.api_key}
          onValueChange={(value) => onChange({ api_key: value })}
          visible={apiKeyVisible}
          onVisibleChange={onApiKeyVisibleChange}
          hasSavedSecret={connection.has_api_key}
          savedLabel={t("settings.model.enterprise.apiKeySaved")}
          notSetLabel={t("settings.model.enterprise.apiKeyNotSet")}
          showLabel={t("settings.model.enterprise.apiKeyShow")}
          hideLabel={t("settings.model.enterprise.apiKeyHide")}
          placeholder={t("settings.model.placeholder.apiKey")}
          helper={t("settings.model.enterprise.apiKeyHelp")}
          clearOption={{
            label: t("settings.model.enterprise.clearApiKey"),
            checked: connection.clear_api_key,
            onCheckedChange: (clear) => onChange({ clear_api_key: clear }),
          }}
          className="md:col-span-2 2xl:col-span-1"
        />
      </div>
    </section>
  );
}

/** 節ごとの保存の操作行（UX 契約 buttons §5.2.1 の FormActionBar。保存は form の submit）。 */
function ModelFormActions({
  sectionLabel,
  canSubmit,
  saving,
  disabled,
  errorText,
}: {
  sectionLabel: string;
  canSubmit: boolean;
  saving: boolean;
  disabled: boolean;
  errorText?: string;
}) {
  const saveLabel = t("settings.model.save");

  return (
    <FormActionBar
      ariaLabel={t("settings.model.actions.label", { section: sectionLabel })}
      primaryActions={[
        {
          id: "save",
          type: "submit",
          label: saveLabel,
          ariaLabel: `${sectionLabel}: ${saveLabel}`,
          icon: Save,
          loading: saving,
          disabled: !canSubmit || disabled,
        },
      ]}
      status={
        errorText ? <FormStatus tone="danger" message={errorText} /> : null
      }
    />
  );
}

function ModelCatalogEditor({
  models,
  connections,
  connectionErrors,
  testingKey,
  testResults,
  onModelChange,
  onAdd,
  onRemove,
  onTest,
  displayNamePlaceholder,
}: {
  models: EnterpriseAiConfiguredModel[];
  connections: EnterpriseAiConnectionSettings[];
  connectionErrors: ModelConnectionErrors;
  testingKey: ModelTestKey | null;
  testResults: Partial<Record<ModelTestKey, ModelSettingsTestResult>>;
  onModelChange: (
    index: number,
    patch: Partial<EnterpriseAiConfiguredModel>,
  ) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
  onTest: (
    key: ModelTestKey,
    target: Omit<ModelSettingsTestRequest, "settings">,
  ) => void;
  displayNamePlaceholder: string;
}) {
  return (
    <div className="space-y-3">
      <div className="flex min-h-8 flex-wrap items-center justify-between gap-2">
        <FieldLabel
          htmlFor="enterprise-model-catalog"
          label={t("settings.model.enterprise.models")}
          badge={t("settings.model.requiredInOci")}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={onAdd}
          disabled={models.length >= 20}
          icon={Plus}
        >
          {t("settings.model.enterprise.addModel")}
        </Button>
      </div>
      <div
        id="enterprise-model-catalog"
        className="overflow-hidden rounded-md border border-border bg-surface-sunken"
      >
        <div
          className={cn(
            "hidden border-b border-border bg-surface px-3 py-2 text-xs font-medium text-fg-muted lg:grid lg:gap-3",
            CATALOG_COLUMNS,
          )}
        >
          <span>{t("settings.model.enterprise.modelId")}</span>
          <span>{t("settings.model.enterprise.displayName")}</span>
          <span>{t("settings.model.enterprise.connection")}</span>
          <span>{t("settings.model.enterprise.vision")}</span>
          <span>{t("settings.model.test.action")}</span>
          <span aria-hidden />
        </div>
        {models.map((model, index) => {
          const modelNumber = index + 1;
          const trimmedModelId = model.model_id.trim();
          const testKey: ModelTestKey = `enterprise:${index}`;
          const targetType: ModelSettingsTestTargetType = model.vision_enabled
            ? "enterprise_vision"
            : "enterprise_text";
          return (
            <div
              key={index}
              className={cn(
                "grid gap-3 border-b border-border p-3 last:border-b-0 lg:items-start",
                CATALOG_COLUMNS,
              )}
            >
              <CompactTextInput
                label={`${t("settings.model.enterprise.modelId")} ${modelNumber}`}
                value={model.model_id}
                placeholder={t("settings.model.placeholder.modelId")}
                onChange={(value) => onModelChange(index, { model_id: value })}
              />
              <CompactTextInput
                label={`${t("settings.model.enterprise.displayName")} ${modelNumber}`}
                value={model.display_name}
                placeholder={displayNamePlaceholder}
                onChange={(value) =>
                  onModelChange(index, { display_name: value })
                }
              />
              <SelectField
                id={modelConnectionFieldId(index)}
                // 「接続 1」は接続の名前と紛らわしいので、行の番号は「モデル N の接続」と読ませる。
                label={t("settings.model.enterprise.connectionOfModel", {
                  number: modelNumber,
                })}
                value={modelConnectionId(model)}
                options={connectionOptions(connections)}
                error={connectionErrors[index]}
                onValueChange={(value) =>
                  onModelChange(index, { connection_id: value })
                }
                // 広い画面では表頭が見出しになるので、欄のラベルは読み上げだけにする。狭い画面の
                // ラベルは同じ行の他の欄（CompactTextInput）と同じ小さい文字にそろえる。
                className="min-w-0 max-lg:[&>label]:text-xs max-lg:[&>label]:text-fg-muted lg:[&>label]:sr-only"
                buttonClassName="h-10"
              />
              <div className="flex min-h-10 items-center justify-between gap-3 text-sm text-fg lg:justify-start">
                <span className="lg:sr-only">
                  {t("settings.model.enterprise.vision")}
                </span>
                <Switch
                  checked={model.vision_enabled}
                  aria-label={`${t("settings.model.enterprise.vision")} ${modelNumber}`}
                  onCheckedChange={(checked) =>
                    onModelChange(index, { vision_enabled: checked })
                  }
                />
              </div>
              <div className="flex min-h-10 items-center">
                <span className="mr-2 text-xs font-medium text-fg-muted lg:sr-only">
                  {t("settings.model.test.action")}
                </span>
                <TestButton
                  modelId={trimmedModelId}
                  fallbackLabel={`${t("settings.model.enterprise.modelId")} ${modelNumber}`}
                  testing={testingKey === testKey}
                  disabled={!trimmedModelId}
                  onClick={() =>
                    onTest(testKey, {
                      target_type: targetType,
                      model_id: model.model_id,
                      vision_enabled: model.vision_enabled,
                    })
                  }
                />
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                iconOnly
                tone="danger"
                aria-label={`${t("settings.model.enterprise.removeModel")} ${modelNumber}`}
                onClick={() => onRemove(index)}
                icon={Trash2}
              ></Button>
              <ModelTestResultPanel
                result={testResults[testKey]}
                testing={testingKey === testKey}
                model={trimmedModelId || `${t("settings.model.enterprise.modelId")} ${modelNumber}`}
                className="lg:col-span-6"
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * 登録モデルの一覧の列（モデル ID / 表示名 / 接続 / 画像入力（Vision）に対応 / テスト / 削除）。
 * 6 列はサイドバーを引いた md の本文幅では窮屈なため、lg から表の形にする（#533）。
 */
const CATALOG_COLUMNS =
  "lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,0.9fr)_8rem_7rem_3.25rem]";

/**
 * 既定のモデル（#499）。登録モデルの一覧の下に置き、登録モデルの節と一緒に保存する。
 * - 既定の Vision モデル: 必須。選択肢は画像入力（Vision）に対応した登録モデルだけ
 * - 既定のテキストモデル: 任意。未選択は「既定の Vision モデルを使う」
 * 一覧から消えた・Vision 対応でなくなったモデルが選ばれたままなら、その ID を出したままエラーにする。
 */
function DefaultModelFields({
  enterprise,
  errors,
  onChange,
}: {
  enterprise: EnterpriseAiModelSettings;
  errors: DefaultModelErrors;
  onChange: (field: DefaultModelField, value: string) => void;
}) {
  const headingId = "enterprise-default-models-title";
  return (
    <section
      aria-labelledby={headingId}
      className="space-y-4 border-t border-border pt-5"
    >
      <div className="space-y-1">
        <h3 id={headingId} className="text-sm font-semibold text-fg">
          {t("settings.model.defaults.title")}
        </h3>
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("settings.model.defaults.description")}
        </p>
      </div>
      <div className="grid gap-x-6 gap-y-5 md:grid-cols-2">
        <SelectField
          id={DEFAULT_MODEL_FIELD_IDS.default_vision_model_id}
          label={t("settings.model.defaults.vision")}
          required
          requiredLabel={t("settings.model.required")}
          value={enterprise.default_vision_model_id}
          options={visionModelOptions(enterprise.models)}
          placeholder={t("settings.model.defaults.visionPlaceholder")}
          helper={t("settings.model.defaults.visionHelp")}
          error={errors.default_vision_model_id}
          onValueChange={(value) => onChange("default_vision_model_id", value)}
        />
        <SelectField
          id={DEFAULT_MODEL_FIELD_IDS.default_text_model_id}
          label={t("settings.model.defaults.text")}
          value={enterprise.default_text_model_id}
          options={textModelOptions(enterprise.models)}
          helper={t("settings.model.defaults.textHelp")}
          error={errors.default_text_model_id}
          onValueChange={(value) => onChange("default_text_model_id", value)}
        />
      </div>
    </section>
  );
}

function CompactTextInput({
  label,
  value,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  placeholder?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="space-y-1.5">
      <span className="block text-xs font-medium text-fg-muted lg:sr-only">
        {label}
      </span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-border-control bg-surface px-3 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring"
      />
    </label>
  );
}

function TestableTextField({
  id,
  label,
  value,
  placeholder,
  helper,
  badge,
  className,
  testResult,
  testing,
  onChange,
  onTest,
}: {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  helper?: string;
  badge?: string;
  className?: string;
  testResult?: ModelSettingsTestResult;
  testing: boolean;
  onChange: (value: string) => void;
  onTest: () => void;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <InputActionField
        id={id}
        label={
          <>
            {label}
            {badge ? (
              <RequiredBadge label={badge} className="ml-2 align-middle" />
            ) : null}
          </>
        }
        value={value}
        placeholder={placeholder}
        helper={helper}
        onChange={onChange}
        action={{
          label: t("settings.model.test.action"),
          ariaLabel: t("settings.model.test.aria", {
            model: value.trim() || label,
          }),
          icon: TestTube2,
          loading: testing,
          disabled: !value.trim(),
          onClick: onTest,
        }}
      />
      <ModelTestResultPanel
        result={testResult}
        testing={testing}
        model={value.trim() || label}
      />
    </div>
  );
}

function TestButton({
  modelId,
  fallbackLabel,
  testing,
  disabled,
  onClick,
}: {
  modelId: string;
  fallbackLabel: string;
  testing: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  const label = t("settings.model.test.action");
  return (
    <Button
      type="button"
      variant="secondary"
      size="md"
      className="w-full whitespace-nowrap lg:w-auto"
      aria-label={t("settings.model.test.aria", {
        model: modelId || fallbackLabel,
      })}
      disabled={disabled}
      loading={testing}
      onClick={onClick}
      icon={TestTube2}
    >
      {label}
    </Button>
  );
}

function ModelTestResultPanel({
  result,
  testing = false,
  model,
  className,
}: {
  result?: ModelSettingsTestResult;
  /** テスト中は結果の位置に経過時間を出す（スピナーはテストのボタンが担う。messaging.md §3.7）。 */
  testing?: boolean;
  model?: string;
  className?: string;
}) {
  if (testing) {
    return (
      <ProcessingIndicator
        active
        label={t("settings.model.test.running", { model: model ?? "" })}
        operationKey={model ?? null}
        placement="action"
        activityIcon="none"
        className={className}
        testId="settings-model-test-processing"
      />
    );
  }
  if (!result) return null;
  return (
    <SettingsTestResultPanel
      tone={result.status === "success" ? "success" : "danger"}
      message={result.message}
      elapsedMs={result.elapsed_ms}
      details={toSettingsTestResultDetails(result.details)}
      troubleshooting={result.troubleshooting}
      errorType={result.error_type}
      rawError={result.raw_error}
      className={className}
    />
  );
}

function NumberField({
  id,
  label,
  value,
  min,
  max,
  step,
  helper,
  badge,
  readOnly,
  onChange,
}: {
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  helper?: string;
  badge?: string;
  readOnly?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <FieldLabel htmlFor={id} label={label} badge={badge} />
      <input
        id={id}
        type="number"
        inputMode="decimal"
        value={value}
        min={min}
        max={max}
        step={step}
        readOnly={readOnly}
        onChange={(event) => onChange(Number(event.target.value))}
        className={cn(
          "tnum h-10 w-full rounded-md border border-border-control bg-surface px-3 text-sm text-fg outline-none transition-colors focus-visible:border-focus-ring",
          readOnly && "bg-surface-sunken text-fg-muted",
        )}
      />
      {helper ? (
        <p className="text-xs leading-relaxed text-fg-muted">{helper}</p>
      ) : null}
    </div>
  );
}

function FieldLabel({
  htmlFor,
  label,
  badge,
}: {
  htmlFor: string;
  label: string;
  badge?: string;
}) {
  return (
    <div className="flex min-h-5 items-center gap-2">
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg">
        {label}
      </label>
      {badge ? <RequiredBadge label={badge} /> : null}
    </div>
  );
}

function buildClientSideTestFailure(
  target: Omit<ModelSettingsTestRequest, "settings">,
  rawError: string,
): ModelSettingsTestResult {
  return {
    status: "failed",
    target_type: target.target_type,
    model_id: target.model_id,
    message: t("settings.model.test.failed"),
    troubleshooting: [t("settings.model.test.apiFailed")],
    raw_error: rawError,
    error_type: "ApiError",
    elapsed_ms: 0,
    checked_at: new Date().toISOString(),
    details: {},
  };
}

function cloneSettings(settings: ModelSettingsPayload): ModelSettingsPayload {
  return normalizeModelSettings(settings);
}

function savedConnectionIds(settings: ModelSettingsPayload): string[] {
  return normalizeModelSettings(settings).enterprise_ai.connections.map(
    (connection) => connection.connection_id,
  );
}

function cloneConnections(
  connections: readonly EnterpriseAiConnectionSettings[],
): EnterpriseAiConnectionSettings[] {
  return connections.map((connection) => ({ ...connection }));
}

const MODEL_SAVE_SUCCESS_KEYS = {
  enterprise_connection: "settings.model.enterprise.saved",
  enterprise_models: "settings.model.enterprise.modelsSaved",
  generative_ai: "settings.model.genai.saved",
} as const satisfies Record<ModelSaveSection, Parameters<typeof t>[0]>;

export function buildSectionSavePayload(
  baseline: ModelSettingsPayload,
  draft: ModelSettingsPayload,
  section: ModelSaveSection,
): ModelSettingsPayload {
  const payload = cloneSettings(baseline);
  if (section === "enterprise_connection") {
    const connections = cloneConnections(draft.enterprise_ai.connections);
    payload.enterprise_ai.connections = connections;
    // 削除した接続を使っていた保存済みのモデルは、削除の確認のとおり接続 1 に移して送る。
    payload.enterprise_ai.models = moveModelsToAvailableConnections(
      payload.enterprise_ai.models,
      connections.map((connection) => connection.connection_id),
    );
  } else if (section === "enterprise_models") {
    payload.enterprise_ai.models = draft.enterprise_ai.models.map((model) => ({
      ...model,
    }));
    payload.enterprise_ai.default_text_model_id =
      draft.enterprise_ai.default_text_model_id;
    payload.enterprise_ai.default_vision_model_id =
      draft.enterprise_ai.default_vision_model_id;
  } else {
    payload.generative_ai = { ...draft.generative_ai };
  }
  return payload;
}

function mergeSavedSectionIntoDraft(
  draft: ModelSettingsPayload,
  saved: ModelSettingsPayload,
  section: ModelSaveSection,
): ModelSettingsPayload {
  if (section === "enterprise_connection") {
    return {
      enterprise_ai: {
        ...saved.enterprise_ai,
        // 入力中の新しい key は保存で消える（応答は空）。削除の指定も戻す。
        connections: cloneConnections(saved.enterprise_ai.connections),
        models: draft.enterprise_ai.models.map((model) => ({ ...model })),
        default_text_model_id: draft.enterprise_ai.default_text_model_id,
        default_vision_model_id: draft.enterprise_ai.default_vision_model_id,
      },
      generative_ai: { ...draft.generative_ai },
    };
  }
  if (section === "enterprise_models") {
    return {
      enterprise_ai: {
        ...saved.enterprise_ai,
        connections: cloneConnections(draft.enterprise_ai.connections),
      },
      generative_ai: { ...draft.generative_ai },
    };
  }
  return {
    enterprise_ai: {
      ...draft.enterprise_ai,
      connections: cloneConnections(draft.enterprise_ai.connections),
      models: draft.enterprise_ai.models.map((model) => ({ ...model })),
    },
    generative_ai: { ...saved.generative_ai },
  };
}
