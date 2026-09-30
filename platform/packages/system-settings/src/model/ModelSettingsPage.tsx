import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
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
  TabPanel,
  Tabs,
  TextField,
  FieldLabel,
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
import { useFocusAfterCommit } from "../users-roles/shared";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "../oci/SettingsTestResultPanel";
import {
  PRIMARY_CONNECTION_ID,
  SECONDARY_CONNECTION_ID,
  connectionFieldId,
  connectionLabel,
  connectionOptions,
  emptyConnection,
  firstConnectionError,
  hasConnection,
  modelConnectionFieldId,
  modelConnectionId,
  modelsUsingConnection,
  moveModelsToAvailableConnections,
  normalizeModelSettings,
  removeConnection,
  unsavedConnectionIds,
  validateConnections,
  validateModelConnections,
  type ConnectionErrors,
  type ConnectionField,
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
  ENTERPRISE_AI_CONNECTION_IDS,
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
 * - Enterprise AI の接続はプライマリ接続とセカンダリ接続（#533）。カードの中の Tabs で切り替えて入力し、
 *   登録モデルの行で使う接続を選ぶ（#542）。セカンダリ接続は「設定」したときだけあり、削除は、
 *   使っているモデルがあればプライマリ接続に移すか確認する。エラー・未保存の入力があるタブは Tabs が示す
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
  // 表示中の接続のタブ（#542）。保存で止めたときは、最初のエラーがあるタブへ切り替える。
  const [connectionTab, setConnectionTab] =
    useState<EnterpriseAiConnectionId>(PRIMARY_CONNECTION_ID);
  // 接続の入力のエラー（セカンダリ接続の必須の欄の未入力）は、保存の操作の後から出す。
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
  // タブの切り替え・欄の追加の後は、描画（commit）の後にフォーカスを移す（#542）。
  const scheduleFocus = useFocusAfterCommit(!operationBusy);
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

  const addSecondaryConnection = () => {
    if (!draft || hasConnection(draft.enterprise_ai.connections, SECONDARY_CONNECTION_ID)) {
      return;
    }
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: {
              ...current.enterprise_ai,
              connections: [
                ...current.enterprise_ai.connections,
                emptyConnection(SECONDARY_CONNECTION_ID),
              ],
            },
          }
        : current,
    );
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_connection");
    // 設定したセカンダリ接続の最初の欄へフォーカスする。
    scheduleFocus(() =>
      document
        .getElementById(connectionFieldId(SECONDARY_CONNECTION_ID, "endpoint"))
        ?.focus(),
    );
  };

  const removeSecondaryConnectionWithConfirm = async () => {
    if (!draft) return;
    const inUse = modelsUsingConnection(
      draft.enterprise_ai.models,
      SECONDARY_CONNECTION_ID,
    );
    const ok = await confirm({
      title: t("settings.model.connection.removeConfirm.title"),
      description: inUse.length
        ? t("settings.model.connection.removeConfirm.descriptionInUse", {
            models: inUse
              .map((model) => model.display_name.trim() || model.model_id.trim())
              .join("、"),
          })
        : t("settings.model.connection.removeConfirm.description"),
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
              SECONDARY_CONNECTION_ID,
            ),
          }
        : current,
    );
    setApiKeyVisible((current) => ({
      ...current,
      [SECONDARY_CONNECTION_ID]: false,
    }));
    setShowModelConnectionErrors(true);
    setCheckData(baselineData);
    setTestResults({});
    clearSaveError("enterprise_connection");
    clearSaveError("enterprise_models");
    // 削除したボタンは消えるので、同じタブに出る「セカンダリ接続を設定」へフォーカスを移す。
    scheduleFocus(() =>
      document.getElementById(SECONDARY_CONNECTION_ADD_ID)?.focus(),
    );
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
      const first = firstConnectionError(
        validateConnections(draft.enterprise_ai.connections),
      );
      if (first) {
        setShowConnectionErrors(true);
        const focusFirst = () =>
          document
            .getElementById(connectionFieldId(first.connectionId, first.field))
            ?.focus();
        if (first.connectionId === connectionTab) {
          focusFirst();
        } else {
          // 別のタブの欄なら、そのタブに切り替え、描画の後にフォーカスする（#542）。
          setConnectionTab(first.connectionId);
          scheduleFocus(focusFirst);
        }
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
  const unsavedConnections =
    draft && baselineData
      ? unsavedConnectionIds(
          draft.enterprise_ai.connections,
          normalizeModelSettings(baselineData.settings).enterprise_ai.connections,
        )
      : new Set<EnterpriseAiConnectionId>();
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
          // セカンダリ接続の API key（SecretField の required）でブラウザの既定の検証を出さない。
          // 未入力は保存の操作で欄の直下に出し、最初のエラーの欄へフォーカスする（messaging.md §3.2.1）。
          noValidate
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
              {/* プライマリ接続とセカンダリ接続は同じ設定（接続）の別の枠なので、共有の Tabs で切り替える
                  （#542。README §4「Tabs」）。エラーのあるタブは invalid、保存していない入力は「未保存」で示す。 */}
              <Tabs
                idPrefix={CONNECTION_TABS_ID_PREFIX}
                ariaLabel={t("settings.model.connection.tabs")}
                value={connectionTab}
                onChange={(value) =>
                  setConnectionTab(value as EnterpriseAiConnectionId)
                }
                items={ENTERPRISE_AI_CONNECTION_IDS.map((id) => ({
                  id,
                  label: connectionLabel(id),
                  badge: unsavedConnections.has(id)
                    ? t("settings.model.connection.unsaved")
                    : undefined,
                  badgeTestId: `enterprise-connection-tab-${id}-unsaved`,
                  invalid: Boolean(connectionErrors[id]),
                }))}
              />
              <TabPanel
                id={connectionTab}
                value={connectionTab}
                idPrefix={CONNECTION_TABS_ID_PREFIX}
              >
                <ConnectionTabContent
                  connectionId={connectionTab}
                  connection={draft.enterprise_ai.connections.find(
                    (connection) => connection.connection_id === connectionTab,
                  )}
                  errors={connectionErrors[connectionTab]}
                  apiKeyVisible={Boolean(apiKeyVisible[connectionTab])}
                  onApiKeyVisibleChange={(visible) =>
                    setApiKeyVisible((current) => ({
                      ...current,
                      [connectionTab]: visible,
                    }))
                  }
                  onChange={(patch) => updateConnection(connectionTab, patch)}
                  onAdd={addSecondaryConnection}
                  onRemove={() => void removeSecondaryConnectionWithConfirm()}
                />
              </TabPanel>
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

/** 接続のタブの id の接頭辞（同じ画面のほかの Tabs と分ける）。 */
const CONNECTION_TABS_ID_PREFIX = "enterprise-connection";
/** 「セカンダリ接続を設定」の id（削除の後にフォーカスを戻す）。 */
const SECONDARY_CONNECTION_ADD_ID = "enterprise-secondary-add";

/**
 * 接続のタブの中身（#542）。
 * - プライマリ接続: 入力欄。OCI で運用するときだけ必須なので、3 つの欄とも「OCI 運用時必須」（保存は止めない）
 * - セカンダリ接続（未設定）: 空の状態と「セカンダリ接続を設定」
 * - セカンダリ接続（設定済み）: 入力欄（3 つとも「必須」）と「セカンダリ接続を削除」。削除は説明の行の右端に置く
 *   （主操作の保存と隣に並べない。README「カード内の操作行」）。API key だけを消す指定は出さない
 *   （必須の欄を空にするため。消すときはセカンダリ接続ごと削除する）
 */
function ConnectionTabContent({
  connectionId,
  connection,
  errors,
  apiKeyVisible,
  onApiKeyVisibleChange,
  onChange,
  onAdd,
  onRemove,
}: {
  connectionId: EnterpriseAiConnectionId;
  connection?: EnterpriseAiConnectionSettings;
  errors?: Partial<Record<ConnectionField, string>>;
  apiKeyVisible: boolean;
  onApiKeyVisibleChange: (visible: boolean) => void;
  onChange: (
    patch: Partial<Omit<EnterpriseAiConnectionSettings, "connection_id">>,
  ) => void;
  onAdd: () => void;
  onRemove: () => void;
}) {
  const primary = connectionId === PRIMARY_CONNECTION_ID;
  if (!connection) {
    return (
      <div data-testid={`enterprise-connection-${connectionId}-empty`}>
        <EmptyState
          title={t("settings.model.connection.secondaryEmpty.title")}
          hint={t("settings.model.connection.secondaryEmpty.hint")}
          action={
            <Button
              id={SECONDARY_CONNECTION_ADD_ID}
              type="button"
              variant="secondary"
              icon={Plus}
              onClick={onAdd}
            >
              {t("settings.model.connection.add")}
            </Button>
          }
        />
      </div>
    );
  }
  // プライマリ接続は条件付きの必須（文言で区別。README §4「必須の表示」）。セカンダリ接続は既定の「必須」。
  const requiredLabel = primary ? t("settings.model.requiredInOci") : undefined;
  return (
    <div
      data-testid={`enterprise-connection-${connectionId}`}
      className="space-y-4"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <p className="min-w-0 text-xs leading-relaxed text-fg-muted">
          {primary
            ? t("settings.model.connection.primaryDescription")
            : t("settings.model.connection.secondaryDescription")}
        </p>
        {primary ? null : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            tone="danger"
            icon={Trash2}
            onClick={onRemove}
            className="w-full shrink-0 sm:ml-auto sm:w-auto"
          >
            {t("settings.model.connection.remove")}
          </Button>
        )}
      </div>
      {/* Endpoint URL は長いので 1 行を使う。Project OCID と API key は広い画面（2xl）で同じ行に置く。 */}
      <div className="grid gap-x-6 gap-y-5 md:grid-cols-2">
        <TextField
          id={connectionFieldId(connectionId, "endpoint")}
          label={t("settings.model.enterprise.endpoint")}
          required
          requiredLabel={requiredLabel}
          value={connection.endpoint}
          placeholder={t("settings.model.placeholder.endpoint")}
          error={errors?.endpoint}
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
          className="md:col-span-2"
        />
        <TextField
          id={connectionFieldId(connectionId, "project_ocid")}
          label={t("settings.model.enterprise.project")}
          required
          requiredLabel={requiredLabel}
          value={connection.project_ocid}
          placeholder={t("settings.model.placeholder.project")}
          helper={t("settings.model.enterprise.projectHelp")}
          error={errors?.project_ocid}
          onValueChange={(value) => onChange({ project_ocid: value })}
          className="md:col-span-2 2xl:col-span-1"
        />
        <SecretField
          id={connectionFieldId(connectionId, "api_key")}
          label={t("settings.model.enterprise.apiKey")}
          required
          requiredLabel={requiredLabel}
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
          error={errors?.api_key}
          clearOption={
            primary
              ? {
                  label: t("settings.model.enterprise.clearApiKey"),
                  checked: connection.clear_api_key,
                  onCheckedChange: (clear) => onChange({ clear_api_key: clear }),
                }
              : undefined
          }
          className="md:col-span-2 2xl:col-span-1"
        />
      </div>
    </div>
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
    <div
      role="group"
      aria-labelledby="enterprise-model-catalog-label"
      className="space-y-3"
    >
      <div className="flex min-h-8 flex-wrap items-center justify-between gap-2">
        {/* 登録モデルは複数の入力をまとめた一覧で、label で結べる 1 つの入力が無い。見出しの行に「追加」を並べるため
            fieldset の legend にもできないので、共有の FieldLabel / FieldLegend で表せず、条件付きの必須のタグを
            RequiredBadge で直接置く（#531）。group は aria-required を持てないので、タグは読み上げ対象に残す。 */}
        <span
          id="enterprise-model-catalog-label"
          className="text-sm font-medium text-fg"
        >
          {t("settings.model.enterprise.models")}
          <RequiredBadge
            label={t("settings.model.requiredInOci")}
            className="ml-2 align-middle"
          />
        </span>
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
                // 行の番号は接続の名前と紛れないよう「モデル N の接続」と読ませる。
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
        label={label}
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
  readOnly?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      {/* 読み取り専用の固定値。「固定」は必須と同じタグで出さず、補足（helper）で伝える（#531）。 */}
      <FieldLabel htmlFor={id} label={label} className="block" />
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
    // 削除した接続を使っていた保存済みのモデルは、削除の確認のとおりプライマリ接続に移して送る。
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
