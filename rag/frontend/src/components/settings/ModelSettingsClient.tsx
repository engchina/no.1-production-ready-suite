"use client";

import {
  AlertCircle,
  ChevronDown,
  CheckCircle2,
  Cpu,
  Database,
  Eye,
  EyeOff,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/PageHeader";
import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  ApiError,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiModelSettings,
  type GenerativeAiModelSettings,
  type ModelSettingsCheckStatus,
  type ModelSettingsData,
  type ModelSettingsPayload,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useCheckModelSettings, useModelSettings, useUpdateModelSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

type CheckKey = keyof ModelSettingsData["checks"];
type NoticeTone = "success" | "info" | "error";

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

/** モデル設定画面。既存 Settings のランタイム値を編集する。 */
export function ModelSettingsClient() {
  const query = useModelSettings();
  const updateMutation = useUpdateModelSettings();
  const checkMutation = useCheckModelSettings();

  const [draft, setDraft] = useState<ModelSettingsPayload | null>(null);
  const [baseline, setBaseline] = useState<ModelSettingsPayload | null>(null);
  const [baselineData, setBaselineData] = useState<ModelSettingsData | null>(null);
  const [checkData, setCheckData] = useState<ModelSettingsData | null>(null);
  const [notice, setNotice] = useState<{ tone: NoticeTone; message: string } | null>(null);
  const [errorText, setErrorText] = useState("");
  const [apiKeyVisible, setApiKeyVisible] = useState(false);

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
  const canSubmit = Boolean(draft && validationMessages.length === 0);
  const hasCustomPayloadTemplate = Boolean(
    draft?.enterprise_ai.text_payload_template.trim() ||
      draft?.enterprise_ai.vision_payload_template.trim()
  );

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
    setNotice(null);
    setErrorText("");
  };

  const removeEnterpriseModel = (index: number) => {
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
    setNotice(null);
    setErrorText("");
  };

  const handleReset = () => {
    if (!baseline) return;
    setDraft(cloneSettings(baseline));
    setCheckData(baselineData);
    setNotice({ tone: "info", message: t("settings.model.resetDone") });
    setErrorText("");
  };

  const handleCheck = async () => {
    if (!draft || validationMessages.length > 0) return;
    setErrorText("");
    setNotice(null);
    try {
      const data = await checkMutation.mutateAsync(draft);
      setCheckData(data);
      setNotice({ tone: "success", message: t("settings.model.checked") });
    } catch (error) {
      setErrorText(error instanceof ApiError ? error.message : t("settings.model.loadError"));
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft || validationMessages.length > 0) return;
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

        {notice ? <Notice tone={notice.tone} message={notice.message} /> : null}
        {errorText ? <Notice tone="error" message={errorText} /> : null}

        {validationMessages.length > 0 ? (
          <div
            role="alert"
            className="rounded-lg border border-warning/30 bg-warning-bg/60 px-4 py-3 text-sm text-warning"
          >
            <div className="flex items-start gap-2">
              <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
              <ul className="space-y-1">
                {validationMessages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
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
                  onDefaultChange={(modelId) => updateEnterprise("default_model_id", modelId)}
                  onModelChange={updateEnterpriseModel}
                  onAdd={addEnterpriseModel}
                  onRemove={removeEnterpriseModel}
                />
                <TextField
                  id="enterprise-api-path"
                  label={t("settings.model.enterprise.apiPath")}
                  value={draft.enterprise_ai.api_path}
                  placeholder={t("settings.model.placeholder.apiPath")}
                  onChange={(value) => updateEnterprise("api_path", value)}
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
                <div className="md:col-span-2">
                  <details className="group rounded-md border border-dashed border-border bg-background/70">
                    <summary className="flex cursor-pointer list-none items-start justify-between gap-3 px-4 py-3 outline-none transition-colors hover:bg-info-bg/30 focus-visible:ring-2 focus-visible:ring-primary [&::-webkit-details-marker]:hidden">
                      <span className="flex min-w-0 items-start gap-3">
                        <SlidersHorizontal
                          size={16}
                          className="mt-0.5 shrink-0 text-primary"
                          aria-hidden
                        />
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-foreground">
                            {t("settings.model.enterprise.advancedPayloadTitle")}
                          </span>
                          <span className="mt-1 block text-xs leading-relaxed text-muted">
                            {t("settings.model.enterprise.advancedPayloadDescription")}
                          </span>
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 text-[11px] font-medium",
                            hasCustomPayloadTemplate
                              ? "bg-warning-bg text-warning"
                              : "bg-success-bg text-success"
                          )}
                        >
                          {hasCustomPayloadTemplate
                            ? t("settings.model.enterprise.advancedPayloadConfigured")
                            : t("settings.model.enterprise.advancedPayloadStandard")}
                        </span>
                        <ChevronDown
                          size={16}
                          className="text-muted transition-transform group-open:rotate-180"
                          aria-hidden
                        />
                      </span>
                    </summary>
                    <div className="border-t border-border px-4 pb-4 pt-4">
                      <p className="mb-4 rounded-md bg-info-bg/50 px-3 py-2 text-xs leading-relaxed text-info">
                        {t("settings.model.enterprise.advancedPayloadNotice")}
                      </p>
                      <div className="grid gap-5 md:grid-cols-2">
                        <TextAreaField
                          id="enterprise-text-payload-template"
                          label={t("settings.model.enterprise.textPayloadTemplate")}
                          value={draft.enterprise_ai.text_payload_template}
                          placeholder={t("settings.model.placeholder.textPayloadTemplate")}
                          helper={t("settings.model.enterprise.payloadTemplateHelp")}
                          rows={6}
                          onChange={(value) => updateEnterprise("text_payload_template", value)}
                          className="md:col-span-2"
                        />
                        <TextAreaField
                          id="enterprise-vision-payload-template"
                          label={t("settings.model.enterprise.visionPayloadTemplate")}
                          value={draft.enterprise_ai.vision_payload_template}
                          placeholder={t("settings.model.placeholder.visionPayloadTemplate")}
                          helper={t("settings.model.enterprise.payloadTemplateHelp")}
                          rows={6}
                          onChange={(value) => updateEnterprise("vision_payload_template", value)}
                          className="md:col-span-2"
                        />
                      </div>
                    </div>
                  </details>
                </div>
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
              <CardContent className="grid gap-5 md:grid-cols-2">
                <TextField
                  id="genai-embedding-model"
                  label={t("settings.model.genai.embeddingModel")}
                  value={draft.generative_ai.embedding_model}
                  placeholder={t("settings.model.placeholder.embeddingModel")}
                  onChange={(value) => updateGenerative("embedding_model", value)}
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
                <TextField
                  id="genai-rerank-model"
                  label={t("settings.model.genai.rerankModel")}
                  value={draft.generative_ai.rerank_model}
                  placeholder={t("settings.model.placeholder.rerankModel")}
                  onChange={(value) => updateGenerative("rerank_model", value)}
                  className="md:col-span-2"
                />
              </CardContent>
            </Card>
          </div>

          <aside className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-primary" aria-hidden />
                  {t("settings.model.ops.title")}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-3 text-sm text-muted">
                  <OperationNote>{t("settings.model.ops.enterpriseOnly")}</OperationNote>
                  <OperationNote>{t("settings.model.ops.genaiOnly")}</OperationNote>
                  <OperationNote>{t("settings.model.ops.vectorDim")}</OperationNote>
                </ul>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <SlidersHorizontal size={16} className="text-primary" aria-hidden />
                  {t("settings.model.status.title")}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <Button
                  type="submit"
                  className="w-full"
                  disabled={!canSubmit}
                  loading={updateMutation.isPending}
                >
                  <Save size={15} aria-hidden />
                  {updateMutation.isPending ? t("settings.model.saving") : t("settings.model.save")}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  className="w-full"
                  disabled={!canSubmit}
                  loading={checkMutation.isPending}
                  onClick={() => void handleCheck()}
                >
                  <RefreshCw size={15} aria-hidden />
                  {checkMutation.isPending ? t("settings.model.checking") : t("settings.model.check")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  className="w-full"
                  disabled={!isDirty || updateMutation.isPending || checkMutation.isPending}
                  onClick={handleReset}
                >
                  <RotateCcw size={15} aria-hidden />
                  {t("settings.model.reset")}
                </Button>
              </CardContent>
            </Card>
          </aside>
        </div>
      </form>
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
  onDefaultChange,
  onModelChange,
  onAdd,
  onRemove,
}: {
  models: EnterpriseAiConfiguredModel[];
  defaultModelId: string;
  onDefaultChange: (modelId: string) => void;
  onModelChange: (index: number, patch: Partial<EnterpriseAiConfiguredModel>) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
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
        <div className="hidden border-b border-border bg-card px-3 py-2 text-xs font-medium text-muted md:grid md:grid-cols-[78px_minmax(0,1.2fr)_minmax(0,1fr)_96px_44px] md:gap-3">
          <span>{t("settings.model.enterprise.default")}</span>
          <span>{t("settings.model.enterprise.modelId")}</span>
          <span>{t("settings.model.enterprise.displayName")}</span>
          <span>{t("settings.model.enterprise.vision")}</span>
          <span aria-hidden />
        </div>
        {models.map((model, index) => {
          const modelNumber = index + 1;
          const trimmedModelId = model.model_id.trim();
          return (
            <div
              key={index}
              className="grid gap-3 border-b border-border p-3 last:border-b-0 md:grid-cols-[78px_minmax(0,1.2fr)_minmax(0,1fr)_96px_44px] md:items-start"
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

function TextAreaField({
  id,
  label,
  value,
  placeholder,
  helper,
  badge,
  className,
  rows = 5,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  placeholder?: string;
  helper?: string;
  badge?: string;
  className?: string;
  rows?: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <FieldLabel htmlFor={id} label={label} badge={badge} />
      <textarea
        id={id}
        value={value}
        placeholder={placeholder}
        rows={rows}
        onChange={(event) => onChange(event.target.value)}
        className="w-full resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-xs leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
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

function Notice({ tone, message }: { tone: NoticeTone; message: string }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "info" ? ShieldCheck : AlertCircle;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2 rounded-lg border px-4 py-3 text-sm",
        tone === "success" && "border-success/30 bg-success-bg/50 text-success",
        tone === "info" && "border-info/30 bg-info-bg/50 text-info",
        tone === "error" && "border-danger/30 bg-danger-bg/50 text-danger"
      )}
    >
      <Icon size={16} className="mt-0.5 shrink-0" aria-hidden />
      <span>{message}</span>
    </div>
  );
}

function OperationNote({ children }: { children: string }) {
  return (
    <li className="flex gap-2">
      <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-success" aria-hidden />
      <span className="leading-relaxed">{children}</span>
    </li>
  );
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
  for (const template of [
    draft.enterprise_ai.text_payload_template,
    draft.enterprise_ai.vision_payload_template,
  ]) {
    if (template.trim() && !isJsonObject(template)) {
      messages.push(t("settings.model.validation.payloadTemplate"));
    }
  }
  return [...new Set(messages)];
}

function isHttpUrl(value: string) {
  return value.startsWith("http://") || value.startsWith("https://");
}

function isApiPath(value: string) {
  return value.startsWith("/") || isHttpUrl(value);
}

function isJsonObject(value: string) {
  try {
    const parsed = JSON.parse(value);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed);
  } catch {
    return false;
  }
}

function cloneSettings(settings: ModelSettingsPayload): ModelSettingsPayload {
  return {
    enterprise_ai: {
      ...settings.enterprise_ai,
      models: settings.enterprise_ai.models.map((model) => ({ ...model })),
    },
    generative_ai: { ...settings.generative_ai },
  };
}

function serializeSettings(settings: ModelSettingsPayload): string {
  return JSON.stringify(settings);
}
