"use client";

import {
  AlertCircle,
  CheckCircle2,
  Cpu,
  Database,
  Eye,
  EyeOff,
  Plus,
  Save,
  TestTube2,
  Trash2,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  SETTINGS_DETAIL_GRID_CLASS,
  SettingsSupplementalPanels,
  formatSettingsEnvValue,
  formatSettingsJson,
} from "@/components/settings/SettingsPreviewPanels";
import {
  ApiError,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiModelSettings,
  type EnterpriseAiVlmInputMode,
  type GenerativeAiModelSettings,
  type ModelSettingsCheckStatus,
  type ModelSettingsData,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type ModelSettingsTestResult,
  type ModelSettingsTestTargetType,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useModelSettings, useTestModelSettings, useUpdateModelSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

type CheckKey = keyof ModelSettingsData["checks"];
type NoticeTone = "success" | "info" | "error";
type ModelTestKey = `enterprise:${number}` | "embedding" | "rerank";

const CHECK_LABEL_KEYS: Record<CheckKey, I18nKey> = {
  enterprise_ai: "settings.model.status.enterprise",
  generative_ai: "settings.model.status.generative",
  embedding_dim: "settings.model.status.embedding",
};

const CHECK_MESSAGE_KEYS: Record<CheckKey, Record<ModelSettingsCheckStatus, I18nKey>> = {
  enterprise_ai: {
    ok: "settings.model.status.enterprise.ok",
    missing: "settings.model.status.enterprise.missing",
    invalid: "settings.model.status.enterprise.invalid",
  },
  generative_ai: {
    ok: "settings.model.status.generative.ok",
    missing: "settings.model.status.generative.missing",
    invalid: "settings.model.status.generative.invalid",
  },
  embedding_dim: {
    ok: "settings.model.status.embedding.ok",
    missing: "settings.model.status.embedding.missing",
    invalid: "settings.model.status.embedding.invalid",
  },
};

const STATUS_LABEL_KEYS: Record<ModelSettingsCheckStatus, I18nKey> = {
  ok: "settings.model.status.ok",
  missing: "settings.model.status.missing",
  invalid: "settings.model.status.invalid",
};

const CHECK_KEYS: CheckKey[] = ["enterprise_ai", "generative_ai", "embedding_dim"];
const DEFAULT_MODEL_SETTINGS_FILE = "model-settings.json";
const VLM_INPUT_MODE_OPTIONS = [
  {
    value: "auto",
    label: t("settings.model.enterprise.vlmInputMode.auto"),
    description: t("settings.model.enterprise.vlmInputMode.auto.description"),
  },
  {
    value: "files_api",
    label: t("settings.model.enterprise.vlmInputMode.filesApi"),
    description: t("settings.model.enterprise.vlmInputMode.filesApi.description"),
  },
  {
    value: "inline_image",
    label: t("settings.model.enterprise.vlmInputMode.inlineImage"),
    description: t("settings.model.enterprise.vlmInputMode.inlineImage.description"),
  },
] as const satisfies readonly SelectFieldOption<EnterpriseAiVlmInputMode>[];

/** モデル設定画面。既存 Settings のランタイム値を編集する。 */
export function ModelSettingsClient() {
  const query = useModelSettings();
  const updateMutation = useUpdateModelSettings();
  const testMutation = useTestModelSettings();
  const confirm = useConfirm();

  const [draft, setDraft] = useState<ModelSettingsPayload | null>(null);
  const [baseline, setBaseline] = useState<ModelSettingsPayload | null>(null);
  const [baselineData, setBaselineData] = useState<ModelSettingsData | null>(null);
  const [checkData, setCheckData] = useState<ModelSettingsData | null>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; message: string } | null>(null);
  const [errorText, setErrorText] = useState("");
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [testingKey, setTestingKey] = useState<ModelTestKey | null>(null);
  const [testResults, setTestResults] = useState<Partial<Record<ModelTestKey, ModelSettingsTestResult>>>({});

  useEffect(() => {
    if (!query.data || draft) return;
    const loaded = cloneSettings(query.data.settings);
    setDraft(loaded);
    setBaseline(cloneSettings(query.data.settings));
    setBaselineData(query.data);
    setCheckData(query.data);
  }, [draft, query.data]);

  const validationMessages = useMemo(
    () => (draft ? validateDraft(draft) : []),
    [draft]
  );
  const isDirty = useMemo(
    () => Boolean(draft && baseline && serializeSettings(draft) !== serializeSettings(baseline)),
    [baseline, draft]
  );

  const activeChecks = checkData?.checks ?? baselineData?.checks;
  const canSubmit = Boolean(draft);
  const modelSettingsFile =
    checkData?.model_settings_file ??
    baselineData?.model_settings_file ??
    query.data?.model_settings_file ??
    DEFAULT_MODEL_SETTINGS_FILE;
  const envPreview = buildModelEnvFile(modelSettingsFile);
  const jsonPreview = draft ? buildModelSettingsJsonPreview(draft) : "";

  const updateEnterprise = <K extends keyof EnterpriseAiModelSettings>(
    key: K,
    value: EnterpriseAiModelSettings[K]
  ) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: { ...current.enterprise_ai, [key]: value },
          }
        : current
    );
    setCheckData(baselineData);
    setTestResults({});
    setNotice(null);
    setErrorText("");
  };

  const updateGenerative = <K extends keyof GenerativeAiModelSettings>(
    key: K,
    value: GenerativeAiModelSettings[K]
  ) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            generative_ai: { ...current.generative_ai, [key]: value },
          }
        : current
    );
    setCheckData(baselineData);
    setTestResults((current) => ({ ...current, embedding: undefined, rerank: undefined }));
    setNotice(null);
    setErrorText("");
  };

  const updateApiKeyClear = (clear: boolean) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            enterprise_ai: {
              ...current.enterprise_ai,
              clear_api_key: clear,
              api_key: clear ? "" : current.enterprise_ai.api_key,
            },
          }
        : current
    );
    setCheckData(baselineData);
    setTestResults({});
    setNotice(null);
    setErrorText("");
  };

  const updateEnterpriseModel = (
    index: number,
    patch: Partial<EnterpriseAiConfiguredModel>
  ) => {
    setDraft((current) => {
      if (!current) return current;
      const models = current.enterprise_ai.models.map((model, modelIndex) =>
        modelIndex === index ? { ...model, ...patch } : model
      );
      const previousModelId = current.enterprise_ai.models[index]?.model_id ?? "";
      const nextModelId =
        typeof patch.model_id === "string" ? patch.model_id : previousModelId;
      let defaultModelId = current.enterprise_ai.default_model_id;
      if (previousModelId && previousModelId === defaultModelId) {
        defaultModelId = nextModelId;
      } else if (!defaultModelId && nextModelId.trim()) {
        defaultModelId = nextModelId;
      }
      return {
        ...current,
        enterprise_ai: {
          ...current.enterprise_ai,
          models,
          default_model_id: defaultModelId,
        },
      };
    });
    setCheckData(baselineData);
    setTestResults((current) => ({ ...current, [`enterprise:${index}`]: undefined }));
    setNotice(null);
    setErrorText("");
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
                { model_id: "", display_name: "", vision_enabled: false },
              ],
            },
          }
        : current
    );
    setCheckData(baselineData);
    setTestResults({});
    setNotice(null);
    setErrorText("");
  };

  const removeEnterpriseModel = async (index: number) => {
    const target = draft?.enterprise_ai.models[index]?.model_id?.trim();
    const ok = await confirm({
      title: t("settings.model.enterprise.removeConfirm.title"),
      description: target
        ? t("settings.model.enterprise.removeConfirm.description", { model: target })
        : t("settings.model.enterprise.removeConfirm.descriptionUnnamed"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!ok) return;
    setDraft((current) => {
      if (!current) return current;
      const removedModelId = current.enterprise_ai.models[index]?.model_id ?? "";
      const models = current.enterprise_ai.models.filter((_, modelIndex) => modelIndex !== index);
      const defaultModelId =
        removedModelId === current.enterprise_ai.default_model_id
          ? models.find((model) => model.model_id.trim())?.model_id ?? ""
          : current.enterprise_ai.default_model_id;
      return {
        ...current,
        enterprise_ai: {
          ...current.enterprise_ai,
          models,
          default_model_id: defaultModelId,
        },
      };
    });
    setCheckData(baselineData);
    setTestResults({});
    setNotice(null);
    setErrorText("");
  };

  const handleTestModel = async (
    key: ModelTestKey,
    target: Omit<ModelSettingsTestRequest, "settings">
  ) => {
    if (!draft) return;
    setErrorText("");
    setNotice(null);
    setTestingKey(key);
    try {
      const result = await testMutation.mutateAsync({ ...target, settings: draft });
      setTestResults((current) => ({ ...current, [key]: result }));
    } catch (error) {
      const message = error instanceof ApiError ? error.message : t("settings.model.test.apiFailed");
      setTestResults((current) => ({
        ...current,
        [key]: buildClientSideTestFailure(target, message),
      }));
    } finally {
      setTestingKey(null);
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) return;
    setErrorText("");
    setNotice(null);
    try {
      const data = await updateMutation.mutateAsync(draft);
      const saved = cloneSettings(data.settings);
      setDraft(saved);
      setBaseline(cloneSettings(data.settings));
      setBaselineData(data);
      setCheckData(data);
      setNotice({ tone: "success", message: t("settings.model.saved") });
    } catch (error) {
      setErrorText(error instanceof ApiError ? error.message : t("settings.model.loadError"));
    }
  };

  if (query.isError) {
    return (
      <div>
        <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
        <div className="p-8">
          <ErrorState
            message={
              query.error instanceof ApiError ? query.error.message : t("settings.model.loadError")
            }
            onRetry={() => void query.refetch()}
          />
        </div>
      </div>
    );
  }

  if (query.isPending || !draft || !activeChecks) {
    return (
      <div>
        <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
        <div className="space-y-4 p-8" aria-label={t("settings.model.loading")}>
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-72 w-full rounded-lg" />
          <Skeleton className="h-44 w-full rounded-lg" />
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
      <form onSubmit={(event) => void handleSubmit(event)} className="space-y-6 p-8">
        <section className="grid gap-4 lg:grid-cols-3" aria-labelledby="model-status-title">
          <div className="lg:col-span-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2 id="model-status-title" className="text-base font-semibold text-foreground">
                  {t("settings.model.status.title")}
                </h2>
                <p className="mt-1 text-sm text-muted">{t("settings.model.status.subtitle")}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {isDirty ? (
                  <span className="rounded-full bg-warning-bg px-3 py-1 text-xs font-medium text-warning">
                    {t("settings.model.unsaved")}
                  </span>
                ) : null}
                <span className="rounded-full border border-border bg-card px-3 py-1 text-xs text-muted">
                  {t("settings.model.source")}: {t("settings.model.source.runtime")}
                </span>
              </div>
            </div>
          </div>
          {CHECK_KEYS.map((key) => (
            <CheckCard key={key} checkKey={key} status={activeChecks[key]} />
          ))}
        </section>

        {notice ? (
          <Banner severity={notice.tone === "error" ? "danger" : notice.tone}>
            {notice.message}
          </Banner>
        ) : null}
        {errorText ? <Banner severity="danger">{errorText}</Banner> : null}

        <div className={SETTINGS_DETAIL_GRID_CLASS}>
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Cpu size={16} className="text-primary" aria-hidden />
                  {t("settings.model.enterprise.title")}
                </CardTitle>
                <CardDescription>{t("settings.model.enterprise.description")}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-5 md:grid-cols-2">
                <TextField
                  id="enterprise-endpoint"
                  label={t("settings.model.enterprise.endpoint")}
                  badge={t("settings.model.requiredInOci")}
                  value={draft.enterprise_ai.endpoint}
                  placeholder={t("settings.model.placeholder.endpoint")}
                  helper={t("settings.model.enterprise.endpointHelp")}
                  onChange={(value) => updateEnterprise("endpoint", value)}
                  className="md:col-span-2"
                />
                <TextField
                  id="enterprise-project-ocid"
                  label={t("settings.model.enterprise.project")}
                  badge={t("settings.model.requiredInOci")}
                  value={draft.enterprise_ai.project_ocid}
                  placeholder={t("settings.model.placeholder.project")}
                  helper={t("settings.model.enterprise.projectHelp")}
                  onChange={(value) => updateEnterprise("project_ocid", value)}
                  className="md:col-span-2"
                />
                <SecretField
                  id="enterprise-api-key"
                  label={t("settings.model.enterprise.apiKey")}
                  value={draft.enterprise_ai.api_key}
                  visible={apiKeyVisible}
                  disabled={draft.enterprise_ai.clear_api_key}
                  hasSavedSecret={draft.enterprise_ai.has_api_key}
                  placeholder={t("settings.model.placeholder.apiKey")}
                  helper={t("settings.model.enterprise.apiKeyHelp")}
                  onToggleVisible={() => setApiKeyVisible((current) => !current)}
                  onChange={(value) => updateEnterprise("api_key", value)}
                  className="md:col-span-2"
                />
                {draft.enterprise_ai.has_api_key ? (
                  <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-background px-4 py-3 text-sm transition-colors hover:bg-info-bg/30 md:col-span-2">
                    <input
                      type="checkbox"
                      checked={draft.enterprise_ai.clear_api_key}
                      onChange={(event) => updateApiKeyClear(event.target.checked)}
                      className="mt-0.5 h-4 w-4 cursor-pointer accent-[var(--primary)]"
                    />
                    <span className="text-foreground">
                      {t("settings.model.enterprise.clearApiKey")}
                    </span>
                  </label>
                ) : null}
                <ModelCatalogEditor
                  models={draft.enterprise_ai.models}
                  defaultModelId={draft.enterprise_ai.default_model_id}
                  testingKey={testingKey}
                  testResults={testResults}
                  onDefaultChange={(modelId) => updateEnterprise("default_model_id", modelId)}
                  onModelChange={updateEnterpriseModel}
                  onAdd={addEnterpriseModel}
                  onRemove={removeEnterpriseModel}
                  onTest={(key, target) => void handleTestModel(key, target)}
                />
                <TextField
                  id="enterprise-api-path"
                  label={t("settings.model.enterprise.apiPath")}
                  value={draft.enterprise_ai.api_path}
                  placeholder={t("settings.model.placeholder.apiPath")}
                  onChange={(value) => updateEnterprise("api_path", value)}
                  className="md:col-span-2"
                />
                <SelectField
                  id="enterprise-vlm-input-mode"
                  label={t("settings.model.enterprise.vlmInputMode")}
                  value={draft.enterprise_ai.vlm_input_mode}
                  options={VLM_INPUT_MODE_OPTIONS}
                  helper={t("settings.model.enterprise.vlmInputModeHelp")}
                  onValueChange={(value) => updateEnterprise("vlm_input_mode", value)}
                  className="md:col-span-2"
                />
                <NumberField
                  id="enterprise-timeout"
                  label={t("settings.model.enterprise.timeout")}
                  min={0.1}
                  max={600}
                  step={0.1}
                  value={draft.enterprise_ai.timeout_seconds}
                  onChange={(value) => updateEnterprise("timeout_seconds", value)}
                />
                <NumberField
                  id="enterprise-retries"
                  label={t("settings.model.enterprise.retries")}
                  min={0}
                  max={5}
                  step={1}
                  value={draft.enterprise_ai.max_retries}
                  onChange={(value) => updateEnterprise("max_retries", value)}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Database size={16} className="text-primary" aria-hidden />
                  {t("settings.model.genai.title")}
                </CardTitle>
                <CardDescription>{t("settings.model.genai.description")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="grid gap-5 md:grid-cols-2">
                  <TestableTextField
                    id="genai-embedding-model"
                    label={t("settings.model.genai.embeddingModel")}
                    value={draft.generative_ai.embedding_model}
                    placeholder={t("settings.model.placeholder.embeddingModel")}
                    onChange={(value) => updateGenerative("embedding_model", value)}
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
                    className="md:col-span-2"
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
                  canSubmit={canSubmit}
                  saving={updateMutation.isPending}
                />
              </CardContent>
            </Card>
          </div>

          <SettingsSupplementalPanels
            env={{
              description: t("settings.model.env.description"),
              value: envPreview,
            }}
            json={{
              description: t("settings.model.json.description"),
              value: jsonPreview,
            }}
            operation={{
              description: t("settings.model.ops.description"),
              notes: [
                t("settings.model.ops.nonBlockingSave"),
                t("settings.model.ops.enterpriseOnly"),
                t("settings.model.ops.genaiOnly"),
                t("settings.model.ops.vectorDim"),
              ],
              warnings: validationMessages,
            }}
          />
        </div>
      </form>
    </div>
  );
}

function ModelFormActions({
  canSubmit,
  saving,
}: {
  canSubmit: boolean;
  saving: boolean;
}) {
  const saveLabel = saving ? t("settings.model.saving") : t("settings.model.save");

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
      <Button
        type="submit"
        size="lg"
        className="whitespace-nowrap"
        aria-label={`${t("nav.settingsModel")}: ${saveLabel}`}
        disabled={!canSubmit}
        loading={saving}
      >
        {!saving ? <Save size={15} aria-hidden /> : null}
        {saveLabel}
      </Button>
    </div>
  );
}

function CheckCard({ checkKey, status }: { checkKey: CheckKey; status: ModelSettingsCheckStatus }) {
  const Icon = status === "ok" ? CheckCircle2 : AlertCircle;
  return (
    <Card>
      <CardContent className="flex h-full flex-col gap-3 pt-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-foreground">{t(CHECK_LABEL_KEYS[checkKey])}</p>
            <p className="mt-1 text-xs leading-relaxed text-muted">
              {t(CHECK_MESSAGE_KEYS[checkKey][status])}
            </p>
          </div>
          <Icon
            size={18}
            className={cn(
              "shrink-0",
              status === "ok" && "text-success",
              status === "missing" && "text-warning",
              status === "invalid" && "text-danger"
            )}
            aria-hidden
          />
        </div>
        <span
          className={cn(
            "mt-auto w-fit rounded-full px-2.5 py-1 text-xs font-medium",
            status === "ok" && "bg-success-bg text-success",
            status === "missing" && "bg-warning-bg text-warning",
            status === "invalid" && "bg-danger-bg text-danger"
          )}
        >
          {t(STATUS_LABEL_KEYS[status])}
        </span>
      </CardContent>
    </Card>
  );
}

function ModelCatalogEditor({
  models,
  defaultModelId,
  testingKey,
  testResults,
  onDefaultChange,
  onModelChange,
  onAdd,
  onRemove,
  onTest,
}: {
  models: EnterpriseAiConfiguredModel[];
  defaultModelId: string;
  testingKey: ModelTestKey | null;
  testResults: Partial<Record<ModelTestKey, ModelSettingsTestResult>>;
  onDefaultChange: (modelId: string) => void;
  onModelChange: (index: number, patch: Partial<EnterpriseAiConfiguredModel>) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
  onTest: (
    key: ModelTestKey,
    target: Omit<ModelSettingsTestRequest, "settings">
  ) => void;
}) {
  return (
    <div className="space-y-3 md:col-span-2">
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
        >
          <Plus size={14} aria-hidden />
          {t("settings.model.enterprise.addModel")}
        </Button>
      </div>
      <div
        id="enterprise-model-catalog"
        className="overflow-hidden rounded-md border border-border bg-background"
      >
        <div className="hidden border-b border-border bg-card px-3 py-2 text-xs font-medium text-muted md:grid md:grid-cols-[64px_minmax(0,1.2fr)_minmax(0,1fr)_84px_96px_44px] md:gap-3">
          <span>{t("settings.model.enterprise.default")}</span>
          <span>{t("settings.model.enterprise.modelId")}</span>
          <span>{t("settings.model.enterprise.displayName")}</span>
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
              className="grid gap-3 border-b border-border p-3 last:border-b-0 md:grid-cols-[64px_minmax(0,1.2fr)_minmax(0,1fr)_84px_96px_44px] md:items-start"
            >
              <label className="flex min-h-10 items-center gap-2 text-sm text-foreground">
                <input
                  type="radio"
                  name="enterprise-default-model"
                  checked={Boolean(trimmedModelId) && defaultModelId === model.model_id}
                  disabled={!trimmedModelId}
                  aria-label={`${t("settings.model.enterprise.default")} ${modelNumber}`}
                  onChange={() => onDefaultChange(model.model_id)}
                  className="h-4 w-4 cursor-pointer accent-[var(--primary)] disabled:cursor-not-allowed"
                />
                <span className="md:sr-only">{t("settings.model.enterprise.default")}</span>
              </label>
              <CompactTextInput
                label={`${t("settings.model.enterprise.modelId")} ${modelNumber}`}
                value={model.model_id}
                placeholder={t("settings.model.placeholder.modelId")}
                onChange={(value) => onModelChange(index, { model_id: value })}
              />
              <CompactTextInput
                label={`${t("settings.model.enterprise.displayName")} ${modelNumber}`}
                value={model.display_name}
                placeholder={t("settings.model.placeholder.displayName")}
                onChange={(value) => onModelChange(index, { display_name: value })}
              />
              <div className="flex min-h-10 items-center justify-between gap-3 text-sm text-foreground md:justify-start">
                <span className="md:sr-only">{t("settings.model.enterprise.vision")}</span>
                <Switch
                  checked={model.vision_enabled}
                  aria-label={`${t("settings.model.enterprise.vision")} ${modelNumber}`}
                  onCheckedChange={(checked) =>
                    onModelChange(index, { vision_enabled: checked })
                  }
                />
              </div>
              <div className="flex min-h-10 items-center">
                <span className="mr-2 text-xs font-medium text-muted md:sr-only">
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
                className="h-10 w-full px-2 text-danger hover:bg-danger-bg md:w-10"
                aria-label={`${t("settings.model.enterprise.removeModel")} ${modelNumber}`}
                onClick={() => onRemove(index)}
              >
                <Trash2 size={15} aria-hidden />
              </Button>
              <ModelTestResultPanel
                result={testResults[testKey]}
                className="md:col-span-5 md:col-start-2"
              />
            </div>
          );
        })}
      </div>
    </div>
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
      <span className="block text-xs font-medium text-muted md:sr-only">{label}</span>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
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
      <FieldLabel htmlFor={id} label={label} badge={badge} />
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <input
          id={id}
          type="text"
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
        />
        <TestButton
          modelId={value.trim()}
          fallbackLabel={label}
          testing={testing}
          disabled={!value.trim()}
          onClick={onTest}
        />
      </div>
      {helper ? <p className="text-xs leading-relaxed text-muted">{helper}</p> : null}
      <ModelTestResultPanel result={testResult} />
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
  const label = testing ? t("settings.model.test.testing") : t("settings.model.test.action");
  return (
    <Button
      type="button"
      variant="secondary"
      size="md"
      className="w-full whitespace-nowrap md:w-auto"
      aria-label={t("settings.model.test.aria", { model: modelId || fallbackLabel })}
      disabled={disabled}
      loading={testing}
      onClick={onClick}
    >
      {!testing ? <TestTube2 size={15} aria-hidden /> : null}
      {label}
    </Button>
  );
}

function ModelTestResultPanel({
  result,
  className,
}: {
  result?: ModelSettingsTestResult;
  className?: string;
}) {
  if (!result) return null;
  const isSuccess = result.status === "success";
  const Icon = isSuccess ? CheckCircle2 : AlertCircle;
  const detailEntries = Object.entries(result.details);

  return (
    <div
      role={isSuccess ? "status" : "alert"}
      className={cn(
        "rounded-md border px-3 py-2.5 text-sm",
        isSuccess ? "border-success/30 bg-success-bg" : "border-danger/30 bg-danger-bg",
        className
      )}
    >
      <div className="flex items-start gap-2.5">
        <Icon
          size={16}
          className={cn("mt-0.5 shrink-0", isSuccess ? "text-success" : "text-danger")}
          aria-hidden
        />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="font-medium text-foreground">{result.message}</p>
            <p className="mt-0.5 text-xs text-muted">
              {t("settings.model.test.elapsed")}: {result.elapsed_ms} ms
            </p>
          </div>
          {detailEntries.length > 0 ? (
            <dl className="grid gap-1 text-xs text-muted sm:grid-cols-2">
              {detailEntries.map(([key, value]) => (
                <div key={key} className="min-w-0">
                  <dt className="font-medium text-foreground">{key}</dt>
                  <dd className="break-words">{String(value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
          {!isSuccess && result.troubleshooting.length > 0 ? (
            <div className="space-y-1">
              <p className="text-xs font-semibold text-foreground">
                {t("settings.model.test.troubleshooting")}
              </p>
              <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-foreground/90">
                {result.troubleshooting.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {!isSuccess ? (
            <details className="text-xs text-foreground">
              <summary className="cursor-pointer font-semibold">
                {t("settings.model.test.rawError")}
              </summary>
              {result.error_type ? (
                <p className="mt-1 text-muted">
                  {t("settings.model.test.errorType")}: {result.error_type}
                </p>
              ) : null}
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-card p-2 text-[11px] leading-relaxed text-foreground">
                {result.raw_error || t("settings.model.test.noDetails")}
              </pre>
            </details>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  placeholder,
  helper,
  badge,
  className,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  helper?: string;
  badge?: string;
  className?: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <FieldLabel htmlFor={id} label={label} badge={badge} />
      <input
        id={id}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
      />
      {helper ? <p className="text-xs leading-relaxed text-muted">{helper}</p> : null}
    </div>
  );
}

function SecretField({
  id,
  label,
  value,
  visible,
  disabled,
  hasSavedSecret,
  placeholder,
  helper,
  className,
  onChange,
  onToggleVisible,
}: {
  id: string;
  label: string;
  value: string;
  visible: boolean;
  disabled: boolean;
  hasSavedSecret: boolean;
  placeholder?: string;
  helper?: string;
  className?: string;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const hintId = helper ? `${id}-hint` : undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <div className="flex min-h-5 flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        <span className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted">
          {hasSavedSecret
            ? t("settings.model.enterprise.apiKeySaved")
            : t("settings.model.enterprise.apiKeyNotSet")}
        </span>
      </div>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          aria-describedby={hintId}
          onChange={(event) => onChange(event.target.value)}
          className="h-10 w-full rounded-md border border-border bg-card px-3 pr-12 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:border-primary"
        />
        <button
          type="button"
          onClick={onToggleVisible}
          aria-label={
            visible
              ? t("settings.model.enterprise.apiKeyHide")
              : t("settings.model.enterprise.apiKeyShow")
          }
          className="absolute right-0 top-0 flex h-10 w-10 cursor-pointer items-center justify-center rounded-r-md text-muted transition-colors hover:bg-background hover:text-foreground"
        >
          {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
        </button>
      </div>
      {helper ? <p id={hintId} className="text-xs leading-relaxed text-muted">{helper}</p> : null}
    </div>
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
          "tnum h-10 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors focus-visible:border-primary",
          readOnly && "bg-background text-muted"
        )}
      />
      {helper ? <p className="text-xs leading-relaxed text-muted">{helper}</p> : null}
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
      <label htmlFor={htmlFor} className="text-sm font-medium text-foreground">
        {label}
      </label>
      {badge ? (
        <span className="rounded-full bg-info-bg px-2 py-0.5 text-[11px] font-medium text-info">
          {badge}
        </span>
      ) : null}
    </div>
  );
}

function buildModelEnvFile(modelSettingsFile: string): string {
  return [
    "# モデル設定",
    `MODEL_SETTINGS_FILE=${formatSettingsEnvValue(modelSettingsFile)}`,
  ].join("\n");
}

function buildModelSettingsJsonPreview(draft: ModelSettingsPayload): string {
  return formatSettingsJson({
    version: 1,
    enterprise_ai: {
      endpoint: draft.enterprise_ai.endpoint,
      project_ocid: draft.enterprise_ai.project_ocid,
      api_key: modelApiKeyPreview(draft.enterprise_ai),
      models: draft.enterprise_ai.models
        .filter((model) => model.model_id.trim())
        .map((model) => ({
          model_id: model.model_id,
          display_name: model.display_name,
          vision_enabled: model.vision_enabled,
        })),
      default_model_id: draft.enterprise_ai.default_model_id,
      api_path: draft.enterprise_ai.api_path,
      vlm_input_mode: draft.enterprise_ai.vlm_input_mode,
      text_payload_template: draft.enterprise_ai.text_payload_template,
      vision_payload_template: draft.enterprise_ai.vision_payload_template,
      text_response_path: draft.enterprise_ai.text_response_path,
      vision_response_path: draft.enterprise_ai.vision_response_path,
      timeout_seconds: draft.enterprise_ai.timeout_seconds,
      max_retries: draft.enterprise_ai.max_retries,
    },
    generative_ai: {
      embedding_model: draft.generative_ai.embedding_model,
      embedding_dim: draft.generative_ai.embedding_dim,
      rerank_model: draft.generative_ai.rerank_model,
    },
  });
}

function modelApiKeyPreview(settings: EnterpriseAiModelSettings): string {
  if (settings.clear_api_key) return "";
  if (settings.api_key.trim()) return t("settings.preview.secret.entered");
  return settings.has_api_key ? t("settings.preview.secret.saved") : "";
}

function validateDraft(draft: ModelSettingsPayload): string[] {
  const messages: string[] = [];
  if (draft.enterprise_ai.endpoint && !isHttpUrl(draft.enterprise_ai.endpoint)) {
    messages.push(t("settings.model.validation.endpoint"));
  }
  if (
    draft.enterprise_ai.project_ocid &&
    !draft.enterprise_ai.project_ocid.startsWith("ocid1.generativeaiproject.")
  ) {
    messages.push(t("settings.model.validation.project"));
  }
  if (
    !draft.enterprise_ai.api_key.trim() &&
    (!draft.enterprise_ai.has_api_key || draft.enterprise_ai.clear_api_key)
  ) {
    messages.push(t("settings.model.validation.apiKey"));
  }
  const modelIds = draft.enterprise_ai.models.map((model) => model.model_id.trim());
  const presentModelIds = modelIds.filter(Boolean);
  if (draft.enterprise_ai.models.length === 0 || presentModelIds.length !== modelIds.length) {
    messages.push(t("settings.model.validation.modelRequired"));
  }
  if (new Set(presentModelIds).size !== presentModelIds.length) {
    messages.push(t("settings.model.validation.modelDuplicate"));
  }
  if (
    !draft.enterprise_ai.default_model_id.trim() ||
    !presentModelIds.includes(draft.enterprise_ai.default_model_id.trim())
  ) {
    messages.push(t("settings.model.validation.defaultModel"));
  }
  if (!draft.enterprise_ai.models.some((model) => model.model_id.trim() && model.vision_enabled)) {
    messages.push(t("settings.model.validation.visionModel"));
  }
  if (!draft.enterprise_ai.api_path.trim()) {
    messages.push(t("settings.model.validation.pathRequired"));
  } else if (!isApiPath(draft.enterprise_ai.api_path)) {
    messages.push(t("settings.model.validation.path"));
  }
  if (
    draft.enterprise_ai.timeout_seconds <= 0 ||
    draft.enterprise_ai.timeout_seconds > 600 ||
    Number.isNaN(draft.enterprise_ai.timeout_seconds)
  ) {
    messages.push(t("settings.model.validation.timeout"));
  }
  if (
    draft.enterprise_ai.max_retries < 0 ||
    draft.enterprise_ai.max_retries > 5 ||
    !Number.isInteger(draft.enterprise_ai.max_retries)
  ) {
    messages.push(t("settings.model.validation.retries"));
  }
  if (draft.generative_ai.embedding_dim !== 1536) {
    messages.push(t("settings.model.validation.embeddingDim"));
  }
  return [...new Set(messages)];
}

function isHttpUrl(value: string) {
  return value.startsWith("http://") || value.startsWith("https://");
}

function isApiPath(value: string) {
  return value.startsWith("/") || isHttpUrl(value);
}

function buildClientSideTestFailure(
  target: Omit<ModelSettingsTestRequest, "settings">,
  rawError: string
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
  return {
    enterprise_ai: {
      ...settings.enterprise_ai,
      vlm_input_mode: settings.enterprise_ai.vlm_input_mode ?? "auto",
      models: settings.enterprise_ai.models.map((model) => ({ ...model })),
    },
    generative_ai: { ...settings.generative_ai },
  };
}

function serializeSettings(settings: ModelSettingsPayload): string {
  return JSON.stringify(settings);
}
