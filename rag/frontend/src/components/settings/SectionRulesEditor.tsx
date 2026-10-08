import {
  Button,
  FormActionBar,
  FormSkeleton,
  FormStatus,
  RowActionMenu,
  SearchableSelectField,
  SelectField,
  Switch,
  TextField,
  TimedLoadingState,
  toast,
  type EntityAction,
  type SelectFieldOption,
} from "@production-ready/ui";
import { ArrowDown, ArrowUp, Eye, Plus, RotateCcw, Save, Trash2, Undo2 } from "lucide-react";
import { useState } from "react";

import { SECTION_RULES_OPTIONS } from "@/components/documents/DocumentProcessingConfigPanel.values";
import { ApiErrorState } from "@/components/StateViews";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type DocumentSectionsData,
  type SectionRule,
  type SectionRulesMode,
  type SectionRulesSettingsData,
} from "@/lib/api";
import { sectionPageLabel } from "@/lib/document-sections";
import { t } from "@/lib/i18n";
import {
  useDocuments,
  usePreviewSectionRules,
  useResetSectionRulesSettings,
  useSaveSectionRulesSettings,
  useSectionRulesSettings,
} from "@/lib/queries";

const LEVEL_OPTIONS: SelectFieldOption<string>[] = [1, 2, 3, 4, 5, 6].map((level) => ({
  value: String(level),
  label: t("sectionRules.level", { level }),
}));

type Draft = { mode: SectionRulesMode; rules: SectionRule[] };

function draftFrom(data: SectionRulesSettingsData): Draft {
  return { mode: data.mode, rules: data.rules };
}

/** 正規表現として読めるか（backend の検証と同じく、保存の前に欄の下で知らせる）。 */
function patternError(pattern: string): string | undefined {
  if (!pattern.trim()) return t("sectionRules.error.patternRequired");
  try {
    new RegExp(pattern);
    return undefined;
  } catch {
    return t("sectionRules.error.patternInvalid");
  }
}

function move<T>(items: readonly T[], from: number, to: number): T[] {
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/**
 * 章節の抽出規則の全体の既定（#715）。方式（解析エンジンの見出し / プリセット / 独自の規則）を選び、
 * 独自の規則は名前・正規表現・階層・有効を一覧で編集する（並びが優先順位）。見本の文書で章節の
 * 分かれ方をプレビューできる（保存しない）。規則は章節ナビゲーションの章節だけに当て、取込の
 * やり直しは要らない。
 */
export function SectionRulesEditor() {
  const query = useSectionRulesSettings();
  const save = useSaveSectionRulesSettings();
  const reset = useResetSectionRulesSettings();
  const confirm = useConfirm();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");

  if (query.isPending) {
    return (
      <TimedLoadingState label={t("sectionRules.loading")} operationKey="section-rules">
        <FormSkeleton fields={3} />
      </TimedLoadingState>
    );
  }
  if (query.isError) {
    return (
      <ApiErrorState
        error={query.error}
        fallback={t("sectionRules.error.load")}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const data = query.data;
  const current = draft ?? draftFrom(data);
  const dirty = draft !== null;
  const presetRules = current.mode === "custom" ? [] : (data.presets[current.mode] ?? []);
  const update = (next: Partial<Draft>) => setDraft({ ...current, ...next });
  const updateRule = (index: number, patch: Partial<SectionRule>) =>
    update({ rules: current.rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)) });
  const errors = current.mode === "custom" ? current.rules.map((rule) => ({
    name: rule.name.trim() ? undefined : t("sectionRules.error.nameRequired"),
    pattern: patternError(rule.pattern),
  })) : [];
  const hasErrors = errors.some((item) => item.name || item.pattern);

  const submit = () => {
    setSubmitted(true);
    if (hasErrors) {
      setError(t("sectionRules.error.fix"));
      return;
    }
    setError("");
    save.mutate(current, {
      onSuccess: () => {
        setDraft(null);
        setSubmitted(false);
        toast.success(t("sectionRules.toast.saved"));
      },
      onError: (failure) =>
        setError(failure instanceof ApiError ? failure.message : t("sectionRules.error.save")),
    });
  };

  const resetToDefault = async () => {
    const confirmed = await confirm({
      title: t("sectionRules.reset.confirm.title"),
      description: t("sectionRules.reset.confirm.description"),
      confirmLabel: t("sectionRules.reset.action"),
      tone: "danger",
    });
    if (!confirmed) return;
    reset.mutate(undefined, {
      onSuccess: () => {
        setDraft(null);
        setSubmitted(false);
        toast.success(t("sectionRules.toast.reset"));
      },
      onError: (failure) =>
        setError(failure instanceof ApiError ? failure.message : t("sectionRules.error.save")),
    });
  };

  const rowActions = (index: number): EntityAction[] => [
    {
      id: "up",
      label: t("sectionRules.action.moveUp"),
      icon: ArrowUp,
      disabled: index === 0,
      onSelect: () => update({ rules: move(current.rules, index, index - 1) }),
    },
    {
      id: "down",
      label: t("sectionRules.action.moveDown"),
      icon: ArrowDown,
      disabled: index === current.rules.length - 1,
      onSelect: () => update({ rules: move(current.rules, index, index + 1) }),
    },
    {
      id: "delete",
      label: t("sectionRules.action.delete"),
      icon: Trash2,
      tone: "danger",
      onSelect: () => update({ rules: current.rules.filter((_, i) => i !== index) }),
    },
  ];

  return (
    <div className="space-y-4" data-testid="section-rules-editor">
      <p className="text-xs text-fg-muted">{t("sectionRules.description")}</p>
      <SelectField
        id="section-rules-mode"
        label={t("sectionRules.mode.label")}
        value={current.mode}
        options={SECTION_RULES_OPTIONS}
        width="md"
        onValueChange={(mode) => update({ mode })}
      />

      {current.mode === "parser" ? (
        <p className="text-sm text-fg-muted">{t("sectionRules.parserHint")}</p>
      ) : current.mode !== "custom" ? (
        <div className="space-y-2">
          <p className="text-sm text-fg-muted">{t("sectionRules.presetHint")}</p>
          <ul className="divide-y divide-border rounded-md border border-border bg-surface-sunken">
            {presetRules.map((rule) => (
              <li key={rule.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
                <span className="font-medium text-fg">{rule.name}</span>
                <span className="text-xs text-fg-muted">{t("sectionRules.level", { level: rule.level })}</span>
                <code className="break-all text-xs text-fg-muted">{rule.pattern}</code>
              </li>
            ))}
          </ul>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={Plus}
            onClick={() => update({ mode: "custom", rules: presetRules })}
          >
            {t("sectionRules.action.copyPreset")}
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm text-fg-muted">{t("sectionRules.customHint")}</p>
          {current.rules.length > 0 ? (
            <ol className="space-y-2" aria-label={t("sectionRules.list.label")}>
              {current.rules.map((rule, index) => {
                const rowErrors = submitted ? errors[index] : undefined;
                const name = rule.name.trim() || t("sectionRules.untitled");
                return (
                  // 並べ替えで行が動くので、位置と名前を key にする（同じ名前の規則も並べられる）。
                  <li
                    key={`${index}-${rule.name}`}
                    className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-start gap-2 rounded-md border border-border bg-surface p-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_7rem_auto_auto]"
                  >
                    <div className="col-span-3 sm:col-span-1">
                      <TextField
                        id={`section-rule-name-${index}`}
                        label={t("sectionRules.field.name")}
                        size="sm"
                        value={rule.name}
                        error={rowErrors?.name}
                        onValueChange={(value) => updateRule(index, { name: value })}
                      />
                    </div>
                    <div className="col-span-3 sm:col-span-1">
                      <TextField
                        id={`section-rule-pattern-${index}`}
                        label={t("sectionRules.field.pattern")}
                        size="sm"
                        value={rule.pattern}
                        placeholder="^第[0-9]+章"
                        error={rowErrors?.pattern}
                        onValueChange={(value) => updateRule(index, { pattern: value })}
                      />
                    </div>
                    <SelectField
                      id={`section-rule-level-${index}`}
                      label={t("sectionRules.field.level")}
                      size="sm"
                      value={String(rule.level)}
                      options={LEVEL_OPTIONS}
                      onValueChange={(value) => updateRule(index, { level: Number(value) })}
                    />
                    <label className="flex items-center gap-2 pt-6 text-xs text-fg-muted">
                      <Switch
                        checked={rule.enabled}
                        aria-label={t("sectionRules.field.enabledAria", { name })}
                        onCheckedChange={(enabled) => updateRule(index, { enabled })}
                      />
                      {t("sectionRules.field.enabled")}
                    </label>
                    <div className="pt-6">
                      <RowActionMenu
                        actions={rowActions(index)}
                        ariaLabel={t("sectionRules.actions.aria", { name })}
                        testId={`section-rule-actions-${index}`}
                      />
                    </div>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="text-sm text-fg-muted">{t("sectionRules.empty")}</p>
          )}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={Plus}
            onClick={() =>
              update({ rules: [...current.rules, { name: "", pattern: "", level: 1, enabled: true }] })
            }
          >
            {t("sectionRules.action.add")}
          </Button>
        </div>
      )}

      <FormActionBar
        ariaLabel={t("sectionRules.actions.form")}
        primaryActions={[
          {
            id: "save",
            label: t("sectionRules.action.save"),
            icon: Save,
            loading: save.isPending,
            disabled: !dirty || reset.isPending,
            onClick: submit,
          },
        ]}
        secondaryActions={[
          {
            id: "discard",
            label: t("sectionRules.action.discard"),
            icon: Undo2,
            disabled: !dirty,
            onClick: () => {
              setDraft(null);
              setSubmitted(false);
              setError("");
            },
          },
        ]}
        dangerActions={[
          {
            id: "reset",
            label: t("sectionRules.reset.action"),
            icon: RotateCcw,
            loading: reset.isPending,
            // 保存の処理中は押せないだけにする（スピナーは押した側だけ。#819）。
            disabled: save.isPending,
            onClick: () => void resetToDefault(),
          },
        ]}
        status={error ? <FormStatus tone="danger" message={error} /> : null}
      />

      <SectionRulesPreview mode={current.mode} rules={current.rules} disabled={hasErrors} />
    </div>
  );
}

/** 見本の文書に、編集中の規則を当てた章節を出す（保存しない）。 */
function SectionRulesPreview({
  mode,
  rules,
  disabled,
}: {
  mode: SectionRulesMode;
  rules: SectionRule[];
  disabled: boolean;
}) {
  const [documentQuery, setDocumentQuery] = useState("");
  const [documentId, setDocumentId] = useState("");
  const documents = useDocuments({ q: documentQuery || undefined, limit: 50 });
  const preview = usePreviewSectionRules();
  const [result, setResult] = useState<DocumentSectionsData | null>(null);
  const options = (documents.data?.items ?? []).map((item) => ({
    value: item.id,
    label: item.file_name,
  }));
  const selected = options.find((option) => option.value === documentId) ?? null;

  const run = () => {
    if (!documentId) return;
    preview.mutate(
      { documentId, mode, rules },
      { onSuccess: (data) => setResult(data), onError: () => setResult(null) }
    );
  };

  return (
    <section
      aria-labelledby="section-rules-preview-title"
      className="space-y-3 rounded-md border border-border bg-surface-sunken p-3"
    >
      <h4 id="section-rules-preview-title" className="text-sm font-semibold text-fg">
        {t("sectionRules.preview.title")}
      </h4>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="min-w-0 flex-1">
          <SearchableSelectField
            id="section-rules-preview-document"
            label={t("sectionRules.preview.document")}
            value={documentId}
            options={options}
            selectedOption={selected}
            onValueChange={(value) => {
              setDocumentId(value);
              setResult(null);
            }}
            onQueryChange={setDocumentQuery}
            remote={{
              total: documents.data?.total ?? options.length,
              hasMore: false,
              loadingMore: false,
              searching: documents.isFetching,
              onLoadMore: () => undefined,
            }}
            width="full"
          />
        </div>
        <Button
          type="button"
          variant="secondary"
          icon={Eye}
          loading={preview.isPending}
          disabled={!documentId || disabled}
          onClick={run}
        >
          {t("sectionRules.preview.run")}
        </Button>
      </div>
      {disabled ? (
        <p className="text-xs text-fg-muted">{t("sectionRules.preview.blocked")}</p>
      ) : null}
      {preview.isError ? (
        <FormStatus
          tone="danger"
          message={
            preview.error instanceof ApiError
              ? preview.error.message
              : t("sectionRules.preview.failed")
          }
        />
      ) : null}
      {result ? (
        result.sections.length > 0 ? (
          <ol className="space-y-0.5" aria-label={t("sectionRules.preview.result")} data-testid="section-rules-preview">
            {result.sections.map((section) => {
              const pages = sectionPageLabel(section);
              return (
                <li
                  key={section.id}
                  className="flex items-start justify-between gap-2 py-0.5 text-sm"
                  style={{ paddingLeft: `calc(var(--space-4) * ${section.level - 1})` }}
                >
                  <span className="min-w-0 break-words text-fg">{section.title}</span>
                  {pages ? (
                    <span className="tnum shrink-0 rounded-full bg-surface px-2 py-0.5 text-xs text-fg-muted">
                      {pages}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="text-sm text-fg-muted">{t("sectionRules.preview.empty")}</p>
        )
      ) : null}
    </section>
  );
}
