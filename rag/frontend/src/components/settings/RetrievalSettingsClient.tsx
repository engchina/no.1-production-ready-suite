"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, Save, Search } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  ApiError,
  type RetrievalModeName,
  type RetrievalSettingsData,
  type RetrievalStrategyName,
  type RetrievalStrategyStatusData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useRetrievalSettings, useUpdateRetrievalSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

const MODE_ORDER: RetrievalModeName[] = [
  "hybrid_rrf",
  "vector",
  "keyword",
  "graph_augmented",
  "reasoning_tree_search",
];

/** 画面ローカルの編集フォーム状態(検索モード + 合成トグル)。 */
interface RetrievalForm {
  mode: RetrievalModeName;
  query_expansion: boolean;
  query_expansion_llm: boolean;
  gap_stop: boolean;
  corrective_retrieval: boolean;
  business_fit_weighting: boolean;
}

function formFromSettings(settings: RetrievalSettingsData): RetrievalForm {
  return {
    mode: settings.mode,
    query_expansion: settings.query_expansion,
    query_expansion_llm: settings.query_expansion_llm,
    gap_stop: settings.gap_stop,
    corrective_retrieval: settings.corrective_retrieval,
    business_fit_weighting: settings.business_fit_weighting,
  };
}

function isDirty(form: RetrievalForm, settings: RetrievalSettingsData): boolean {
  // legacy 読み替え中は保存で新形式へ移行するため、同値でも保存可能にする。
  if (settings.legacy_strategy) return true;
  const base = formFromSettings(settings);
  return (Object.keys(base) as (keyof RetrievalForm)[]).some((key) => form[key] !== base[key]);
}

/** 検索方法(検索モード + 検索オプション)の設定画面。 */
export function RetrievalSettingsClient() {
  const query = useRetrievalSettings();
  const save = useUpdateRetrievalSettings();
  const [form, setForm] = useState<RetrievalForm | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setForm(formFromSettings(query.data));
    }
  }, [query.data, save.isPending]);

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="p-8">
        <ErrorState
          message={
            query.error instanceof ApiError ? query.error.message : t("settings.retrieval.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const dirty = isDirty(form, settings);
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.retrieval.saveError");
  const modes = orderedModes(settings.modes);

  function updateForm(patch: Partial<RetrievalForm>) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, ...patch } : current));
  }

  function resetForm() {
    save.reset();
    setSuccessMessage(null);
    if (settings) setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form) return;
    save.mutate(
      { ...form },
      {
        onSuccess: (data) => {
          setForm(formFromSettings(data));
          setSuccessMessage(t("settings.retrieval.actions.saved"));
        },
        onError: () => setSuccessMessage(null),
      }
    );
  }

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <Search size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.retrieval.overview.title")}</CardTitle>
              <CardDescription>{t("settings.retrieval.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {settings.legacy_strategy ? (
            <FormStatus tone="info" message={t("settings.retrieval.legacyNotice")} />
          ) : null}
          <div className="space-y-2">
            <div className="text-sm font-medium text-foreground">
              {t("settings.retrieval.mode")}
            </div>
            <div
              role="radiogroup"
              aria-label={t("settings.retrieval.mode")}
              className="grid grid-cols-1 gap-2 md:grid-cols-2 lg:grid-cols-4"
            >
              {modes.map((item) => {
                const selected = form.mode === item.name;
                return (
                  <button
                    key={item.name}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={save.isPending}
                    onClick={() => updateForm({ mode: item.name as RetrievalModeName })}
                    className={cn(
                      "min-h-[96px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-foreground hover:bg-background"
                    )}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{strategyLabel(item.name)}</span>
                      {selected ? (
                        <CheckCircle2 size={15} className="shrink-0 text-primary" aria-hidden />
                      ) : null}
                    </span>
                    <span className="mt-1 block text-xs leading-relaxed text-muted">
                      {strategyDescription(item.name)}
                    </span>
                    <span className="mt-2 flex flex-wrap gap-1">
                      {item.recommended_for.slice(0, 2).map((token) => (
                        <span
                          key={token}
                          className="inline-flex min-h-5 items-center rounded bg-muted/20 px-1.5 text-[11px] text-muted"
                        >
                          {purposeLabel(token)}
                        </span>
                      ))}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="space-y-2">
            <div>
              <div className="text-sm font-medium text-foreground">
                {t("settings.retrieval.toggles")}
              </div>
              <p className="text-xs text-muted">{t("settings.retrieval.toggles.description")}</p>
            </div>
            <div className="divide-y divide-border rounded-md border border-border">
              <ToggleRow
                label={t("settings.retrieval.queryExpansion")}
                description={t("settings.retrieval.toggle.queryExpansion.description")}
                checked={form.query_expansion}
                disabled={save.isPending}
                onChange={(checked) =>
                  updateForm(
                    checked
                      ? { query_expansion: true }
                      : { query_expansion: false, query_expansion_llm: false }
                  )
                }
              />
              <ToggleRow
                nested
                label={t("settings.retrieval.toggle.queryExpansionLlm")}
                description={t("settings.retrieval.toggle.queryExpansionLlm.description")}
                checked={form.query_expansion_llm}
                disabled={save.isPending || !form.query_expansion}
                onChange={(checked) => updateForm({ query_expansion_llm: checked })}
              />
              <ToggleRow
                label={t("settings.retrieval.gapStop")}
                description={t("settings.retrieval.toggle.gapStop.description")}
                checked={form.gap_stop}
                disabled={save.isPending}
                onChange={(checked) => updateForm({ gap_stop: checked })}
              />
              <ToggleRow
                label={t("settings.retrieval.businessFit")}
                description={t("settings.retrieval.toggle.businessFit.description")}
                checked={form.business_fit_weighting}
                disabled={save.isPending}
                onChange={(checked) => updateForm({ business_fit_weighting: checked })}
              />
              <ToggleRow
                label={t("settings.retrieval.corrective")}
                description={t("settings.retrieval.toggle.corrective.description")}
                checked={form.corrective_retrieval}
                disabled={save.isPending}
                onChange={(checked) => updateForm({ corrective_retrieval: checked })}
              />
            </div>
          </div>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {dirty ? (
                <FormStatus tone="warning" message={t("settings.retrieval.actions.unsaved")} />
              ) : null}
              {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
              {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={resetForm}
                disabled={!dirty || save.isPending}
                aria-label={t("settings.retrieval.actions.reset")}
              >
                <RotateCcw size={15} aria-hidden />
                {t("settings.retrieval.actions.reset")}
              </Button>
              <Button
                type="button"
                loading={save.isPending}
                disabled={!dirty}
                onClick={submit}
                aria-label={t("settings.retrieval.actions.save")}
              >
                <Save size={15} aria-hidden />
                {save.isPending
                  ? t("settings.retrieval.actions.saving")
                  : t("settings.retrieval.actions.save")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  disabled,
  onChange,
  nested = false,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
  nested?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 px-3 py-3",
        nested && "pl-8",
        disabled && "opacity-60"
      )}
    >
      <div className="min-w-0">
        <div className="text-sm font-medium text-foreground">{label}</div>
        <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>
      </div>
      <Switch
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onCheckedChange={onChange}
        className="mt-0.5 shrink-0"
      />
    </div>
  );
}

function orderedModes(modes: RetrievalStrategyStatusData[]): RetrievalStrategyStatusData[] {
  const byName = new Map(modes.map((item) => [item.name, item]));
  const ordered = MODE_ORDER.map((name) => byName.get(name)).filter(
    (item): item is RetrievalStrategyStatusData => Boolean(item)
  );
  return ordered.length ? ordered : modes;
}

function strategyLabel(name: RetrievalStrategyName) {
  // 欠損キー(env 手編集の未配線戦略など)では undefined を返すため生名へ縮退する。
  return t(`settings.retrieval.strategy.${name}` as I18nKey) || name;
}

/** 推奨用途トークンを i18n ラベルへ。未定義トークンは生のまま安全縮退する。 */
function purposeLabel(token: string) {
  return t(`settings.retrieval.useCase.${token}` as I18nKey) || token;
}

function strategyDescription(name: RetrievalStrategyName) {
  return t(`settings.retrieval.strategy.${name}.description` as I18nKey);
}
