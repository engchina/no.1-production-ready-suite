"use client";

import {
  Cpu,
  Database,
  Eye,
  EyeOff,
  ListChecks,
  Plus,
  Save,
  TestTube2,
  Trash2,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import {
  FormStatus,
  toast,
  Button,
  PageHeader,
  TextField,
  Banner,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
  Switch,
  PageBody,
  useConfirm,
} from "@engchina/production-ready-ui";

import { TimedLoadingState } from "@/components/ProcessingState";
import { ErrorState } from "@/components/StateViews";
import { InputActionField } from "@/components/ui/input-action-field";
import { RequiredIndicator } from "@/components/ui/required-field";
import { SavedSecretBadge } from "@/components/settings/SavedSecretBadge";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "@/components/settings/SettingsTestResultPanel";
import {
  ApiError,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiModelSettings,
  type GenerativeAiModelSettings,
  type ModelSettingsData,
  type ModelSettingsPayload,
  type ModelSettingsTestRequest,
  type ModelSettingsTestResult,
  type ModelSettingsTestTargetType,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useModelSettings, useTestModelSettings, useUpdateModelSettings } from "@/lib/queries";
import { useSettingsDraftGuard } from "@/lib/useSettingsDraftGuard";
import { cn } from "@/lib/utils";

type ModelTestKey = `enterprise:${number}` | "embedding" | "rerank";
type ModelSaveSection = "enterprise_connection" | "enterprise_models" | "generative_ai";

/** モデル設定画面。既存 Settings のランタイム値を編集する。 */
export function ModelSettingsClient() {
  const query = useModelSettings();
  const updateMutation = useUpdateModelSettings();
  const testMutation = useTestModelSettings();
  const confirm = useConfirm();

  const [draft, setDraft] = useState<ModelSettingsPayload | null>(null);
  const [baselineData, setBaselineData] = useState<ModelSettingsData | null>(null);
  const [checkData, setCheckData] = useState<ModelSettingsData | null>(null);
  const [saveErrors, setSaveErrors] = useState<
    Partial<Record<ModelSaveSection, string>>
  >({});
  const [activeSaveSection, setActiveSaveSection] = useState<ModelSaveSection | null>(null);
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [testingKey, setTestingKey] = useState<ModelTestKey | null>(null);
  const [testResults, setTestResults] = useState<Partial<Record<ModelTestKey, ModelSettingsTestResult>>>({});

  useEffect(() => {
    if (!query.data || draft) return;
    const loaded = cloneSettings(query.data.settings);
    setDraft(loaded);
    setBaselineData(query.data);
    setCheckData(query.data);
  }, [draft, query.data]);

  const canSubmit = Boolean(draft);
  const saveInProgress = activeSaveSection !== null;
  const operationBusy = saveInProgress || testingKey !== null;
  useSettingsDraftGuard(Boolean(draft && baselineData &&
    JSON.stringify(draft) !== JSON.stringify(cloneSettings(baselineData.settings))), operationBusy);
  const legacySecretDetected =
    checkData?.legacy_secret_detected ??
    baselineData?.legacy_secret_detected ??
    query.data?.legacy_secret_detected ??
    false;

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
    clearSaveError(key === "default_model_id" ? "enterprise_models" : "enterprise_connection");
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
    clearSaveError("generative_ai");
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
    clearSaveError("enterprise_connection");
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
                { model_id: "", display_name: "", vision_enabled: false },
              ],
            },
          }
        : current
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
    clearSaveError("enterprise_models");
  };

  function clearSaveError(section: ModelSaveSection) {
    setSaveErrors((current) => ({ ...current, [section]: undefined }));
  }

  const handleTestModel = async (
    key: ModelTestKey,
    target: Omit<ModelSettingsTestRequest, "settings">
  ) => {
    if (!draft || operationBusy) return;
    setTestResults((current) => ({ ...current, [key]: undefined }));
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

  const handleSubmit = async (
    event: FormEvent<HTMLFormElement>,
    section: ModelSaveSection
  ) => {
    event.preventDefault();
    if (!draft || !baselineData || operationBusy) return;
    clearSaveError(section);
    setActiveSaveSection(section);
    try {
      const payload = buildSectionSavePayload(baselineData.settings, draft, section);
      const data = await updateMutation.mutateAsync(payload);
      const saved = cloneSettings(data.settings);
      setDraft((current) =>
        current ? mergeSavedSectionIntoDraft(current, saved, section) : saved
      );
      setBaselineData(data);
      setCheckData(data);
      toast.success(t(MODEL_SAVE_SUCCESS_KEYS[section]));
    } catch (error) {
      setSaveErrors((current) => ({
        ...current,
        [section]: error instanceof ApiError ? error.message : t("settings.model.saveError"),
      }));
    } finally {
      setActiveSaveSection(null);
    }
  };

  if (query.isError) {
    return (
      <div>
        <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
        <PageBody>
          <ErrorState
            message={
              query.error instanceof ApiError ? query.error.message : t("settings.model.loadError")
            }
            onRetry={() => void query.refetch()}
          />
        </PageBody>
      </div>
    );
  }

  if (query.isPending || !draft) {
    return (
      <div>
        <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
        <PageBody>
          <TimedLoadingState
            label={t("settings.model.loading")}
            operationKey="settings-model-load"
            placement="page"
            testId="settings-model-loading"
          >
            <Skeleton className="h-28 w-full rounded-lg" />
            <Skeleton className="h-72 w-full rounded-lg" />
            <Skeleton className="h-44 w-full rounded-lg" />
          </TimedLoadingState>
        </PageBody>
      </div>
    );
  }

  return (
    <div>
      <PageHeader title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
      <PageBody>
        {legacySecretDetected ? (
          <Banner
            severity="warning"
            title={t("settings.model.legacySecret.title")}
          >
            {t("settings.model.legacySecret.description")}
          </Banner>
        ) : null}
        <fieldset disabled={operationBusy} aria-busy={operationBusy} className="min-w-0 space-y-6">
          <form
            onSubmit={(event) => void handleSubmit(event, "enterprise_connection")}
          >
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Cpu size={16} className="text-accent-fg" aria-hidden />
                  {t("settings.model.enterprise.title")}
                </CardTitle>
                <CardDescription>{t("settings.model.enterprise.description")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="grid gap-5 md:grid-cols-2">
                  <TextField
                    id="enterprise-endpoint"
                    label={t("settings.model.enterprise.endpoint")}
                    required requiredLabel={t("settings.model.requiredInOci")}
                    value={draft.enterprise_ai.endpoint}
                    placeholder={t("settings.model.placeholder.endpoint")}
                    helper={
                      <>
                        <span className="block">{t("settings.model.enterprise.endpointHelp")}</span>
                        <a
                          href="https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-compatible-api.htm"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex min-h-11 items-center rounded-sm text-accent-fg underline underline-offset-4 hover:text-accent-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2"
                        >
                          {t("settings.model.enterprise.endpointDocs")}
                        </a>
                      </>
                    }
                    onValueChange={(value) => updateEnterprise("endpoint", value)}
                    className="md:col-span-2"
                  />
                  <TextField
                    id="enterprise-project-ocid"
                    label={t("settings.model.enterprise.project")}
                    required requiredLabel={t("settings.model.requiredInOci")}
                    value={draft.enterprise_ai.project_ocid}
                    placeholder={t("settings.model.placeholder.project")}
                    helper={t("settings.model.enterprise.projectHelp")}
                    onValueChange={(value) => updateEnterprise("project_ocid", value)}
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
                    <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-surface-sunken px-4 py-3 text-sm transition-colors hover:bg-info-subtle md:col-span-2">
                      <input
                        type="checkbox"
                        checked={draft.enterprise_ai.clear_api_key}
                        onChange={(event) => updateApiKeyClear(event.target.checked)}
                        className="mt-0.5 h-4 w-4 cursor-pointer accent-[var(--color-accent-emphasis)]"
                      />
                      <span className="text-fg">
                        {t("settings.model.enterprise.clearApiKey")}
                      </span>
                    </label>
                  ) : null}
                </div>
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

          <form onSubmit={(event) => void handleSubmit(event, "enterprise_models")}>
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
                  defaultModelId={draft.enterprise_ai.default_model_id}
                  testingKey={testingKey}
                  testResults={testResults}
                  onDefaultChange={(modelId) =>
                    updateEnterprise("default_model_id", modelId)
                  }
                  onModelChange={updateEnterpriseModel}
                  onAdd={addEnterpriseModel}
                  onRemove={removeEnterpriseModel}
                  onTest={(key, target) => void handleTestModel(key, target)}
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
    </div>
  );
}

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
    <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
      <Button
        type="submit"
        size="lg"
        className="whitespace-nowrap"
        aria-label={`${sectionLabel}: ${saveLabel}`}
        disabled={!canSubmit || disabled}
        loading={saving} icon={Save}>
        {saveLabel}
      </Button>
      {errorText ? (
        <FormStatus tone="danger" message={errorText} className="min-w-0 flex-1" />
      ) : null}
    </div>
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
          disabled={models.length >= 20} icon={Plus}>
          {t("settings.model.enterprise.addModel")}
        </Button>
      </div>
      <div
        id="enterprise-model-catalog"
        className="overflow-hidden rounded-md border border-border bg-surface-sunken"
      >
        <div className="hidden border-b border-border bg-surface px-3 py-2 text-xs font-medium text-fg-muted md:grid md:grid-cols-[64px_minmax(0,1.2fr)_minmax(0,1fr)_84px_96px_44px] md:gap-3">
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
              <label className="flex min-h-10 items-center gap-2 text-sm text-fg">
                <input
                  type="radio"
                  name="enterprise-default-model"
                  checked={Boolean(trimmedModelId) && defaultModelId === model.model_id}
                  disabled={!trimmedModelId}
                  aria-label={`${t("settings.model.enterprise.default")} ${modelNumber}`}
                  onChange={() => onDefaultChange(model.model_id)}
                  className="h-4 w-4 cursor-pointer accent-[var(--color-accent-emphasis)] disabled:cursor-not-allowed"
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
              <div className="flex min-h-10 items-center justify-between gap-3 text-sm text-fg md:justify-start">
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
                <span className="mr-2 text-xs font-medium text-fg-muted md:sr-only">
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
                iconOnly tone="danger"
                aria-label={`${t("settings.model.enterprise.removeModel")} ${modelNumber}`}
                onClick={() => onRemove(index)} icon={Trash2}>
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
      <span className="block text-xs font-medium text-fg-muted md:sr-only">{label}</span>
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
            {badge ? <RequiredIndicator label={badge} /> : null}
          </>
        }
        value={value}
        placeholder={placeholder}
        helper={helper}
        onChange={onChange}
        action={{
          label: t("settings.model.test.action"),
          ariaLabel: t("settings.model.test.aria", { model: value.trim() || label }),
          icon: <TestTube2 size={16} aria-hidden />,
          loading: testing,
          disabled: !value.trim(),
          onClick: onTest,
        }}
      />
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
  const label = t("settings.model.test.action");
  return (
    <Button
      type="button"
      variant="secondary"
      size="md"
      className="w-full whitespace-nowrap md:w-auto"
      aria-label={t("settings.model.test.aria", { model: modelId || fallbackLabel })}
      disabled={disabled}
      loading={testing}
      onClick={onClick} icon={TestTube2}>
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
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {label}
        </label>
        {hasSavedSecret ? (
          <SavedSecretBadge label={t("settings.model.enterprise.apiKeySaved")} />
        ) : (
          <span className="rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs text-fg-muted">
            {t("settings.model.enterprise.apiKeyNotSet")}
          </span>
        )}
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
          className="h-[44px] w-full rounded-md border border-border-control bg-surface px-3 pr-12 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted disabled:cursor-not-allowed disabled:opacity-50 focus-visible:border-focus-ring"
        />
        <Button
          variant="ghost"
          size="sm"
          iconOnly
          touchTarget
          type="button"
          onClick={onToggleVisible}
          disabled={disabled}
          aria-label={
            visible
              ? t("settings.model.enterprise.apiKeyHide")
              : t("settings.model.enterprise.apiKeyShow")
          }
          className="absolute right-0 top-0 rounded-l-none"
        >
          {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
        </Button>
      </div>
      {helper ? <p id={hintId} className="text-xs leading-relaxed text-fg-muted">{helper}</p> : null}
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
          "tnum h-10 w-full rounded-md border border-border-control bg-surface px-3 text-sm text-fg outline-none transition-colors focus-visible:border-focus-ring",
          readOnly && "bg-surface-sunken text-fg-muted"
        )}
      />
      {helper ? <p className="text-xs leading-relaxed text-fg-muted">{helper}</p> : null}
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
      {badge ? <RequiredIndicator label={badge} /> : null}
    </div>
  );
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

const MODEL_SAVE_SUCCESS_KEYS = {
  enterprise_connection: "settings.model.enterprise.saved",
  enterprise_models: "settings.model.enterprise.modelsSaved",
  generative_ai: "settings.model.genai.saved",
} as const satisfies Record<ModelSaveSection, Parameters<typeof t>[0]>;

function buildSectionSavePayload(
  baseline: ModelSettingsPayload,
  draft: ModelSettingsPayload,
  section: ModelSaveSection
): ModelSettingsPayload {
  const payload = cloneSettings(baseline);
  if (section === "enterprise_connection") {
    payload.enterprise_ai.endpoint = draft.enterprise_ai.endpoint;
    payload.enterprise_ai.project_ocid = draft.enterprise_ai.project_ocid;
    payload.enterprise_ai.api_key = draft.enterprise_ai.api_key;
    payload.enterprise_ai.has_api_key = draft.enterprise_ai.has_api_key;
    payload.enterprise_ai.clear_api_key = draft.enterprise_ai.clear_api_key;
  } else if (section === "enterprise_models") {
    payload.enterprise_ai.models = draft.enterprise_ai.models.map((model) => ({ ...model }));
    payload.enterprise_ai.default_model_id = draft.enterprise_ai.default_model_id;
  } else {
    payload.generative_ai = { ...draft.generative_ai };
  }
  return payload;
}

function mergeSavedSectionIntoDraft(
  draft: ModelSettingsPayload,
  saved: ModelSettingsPayload,
  section: ModelSaveSection
): ModelSettingsPayload {
  if (section === "enterprise_connection") {
    return {
      enterprise_ai: {
        ...saved.enterprise_ai,
        models: draft.enterprise_ai.models.map((model) => ({ ...model })),
        default_model_id: draft.enterprise_ai.default_model_id,
      },
      generative_ai: { ...draft.generative_ai },
    };
  }
  if (section === "enterprise_models") {
    return {
      enterprise_ai: {
        ...saved.enterprise_ai,
        endpoint: draft.enterprise_ai.endpoint,
        project_ocid: draft.enterprise_ai.project_ocid,
        api_key: draft.enterprise_ai.api_key,
        has_api_key: draft.enterprise_ai.has_api_key,
        clear_api_key: draft.enterprise_ai.clear_api_key,
      },
      generative_ai: { ...draft.generative_ai },
    };
  }
  return {
    enterprise_ai: {
      ...draft.enterprise_ai,
      models: draft.enterprise_ai.models.map((model) => ({ ...model })),
    },
    generative_ai: { ...saved.generative_ai },
  };
}
