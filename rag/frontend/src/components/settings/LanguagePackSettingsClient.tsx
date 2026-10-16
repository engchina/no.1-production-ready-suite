"use client";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormGrid,
  FormSkeleton,
  FormStatus,
  PageBody,
  SelectField,
  type SelectFieldOption,
  StatusBadge,
  Tabs,
  TextareaField,
  TextField,
  TimedLoadingState,
} from "@production-ready/ui";
import { FlaskConical, Languages, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import {
  ApiError,
  type LanguagePackData,
  type LanguagePackField,
  type LanguagePackPreviewResult,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import {
  type LanguagePackTexts,
  fieldId,
  isChanged,
  languagePackErrors,
  changesIngestFields,
  overridesFromTexts,
  textKey,
  textsFromSections,
} from "@/lib/language-pack";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useLanguagePack, usePreviewLanguagePack, useUpdateLanguagePack } from "@/lib/queries";
import { focusFirstInvalidField } from "@/lib/required-fields";
import { toast } from "@/lib/toast";

/** 語の数が多い項目は欄を高くする。 */
const LONG_FIELD_ITEMS = 8;

/** 言語資源（文書と質問を読むときの言語の書き方。#2181）の全体の設定。 */
export function LanguagePackSettingsClient() {
  const query = useLanguagePack();
  const save = useUpdateLanguagePack();
  const [texts, setTexts] = useState<LanguagePackTexts | null>(null);
  const [base, setBase] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [section, setSection] = useState<string | null>(null);
  const serverTexts = query.data ? textsFromSections(query.data.fields, query.data.effective) : null;
  const serverKey = serverTexts ? JSON.stringify(serverTexts) : null;
  if (serverTexts && serverKey !== base) {
    setBase(serverKey);
    if (texts === null || JSON.stringify(texts) === base) setTexts(serverTexts);
  }
  const dirty = Boolean(texts && serverKey && JSON.stringify(texts) !== serverKey);
  // 検索・回答だけの項目（全文検索の質問の語など）は、文書を分割し直さなくても反映する。
  const reingest = Boolean(dirty && query.data && texts && serverTexts && changesIngestFields(query.data.fields, texts, serverTexts));
  useLeaveGuard(dirty, save.isPending);

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.language.loading")}
          operationKey="settings-language-load"
          placement="page"
          testId="settings-language-loading"
        >
          <FormSkeleton fields={4} />
        </TimedLoadingState>
      </PageBody>
    );
  }
  if (query.isError) {
    return (
      <PageBody wide>
        <ApiErrorState
          error={query.error}
          fallback={t("settings.language.loadError")}
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }
  const data = query.data;
  if (!data || !texts) return null;
  const sections = [...new Set(data.fields.map((field) => field.section))];
  const current = section && sections.includes(section) ? section : sections[0];
  const fields = data.fields.filter((field) => field.section === current);
  const saveError = save.error instanceof ApiError ? save.error.message : t("settings.language.saveError");

  function update(key: string, value: string) {
    save.reset();
    setErrors((now) => Object.fromEntries(Object.entries(now).filter(([name]) => name !== key)));
    setTexts((now) => (now ? { ...now, [key]: value } : now));
  }

  function submit() {
    if (!data || !texts || save.isPending) return;
    const found = languagePackErrors(data.fields, texts);
    setErrors(found);
    const firstInvalid = data.fields.find((field) => found[textKey(field)]);
    if (firstInvalid) {
      // 誤りのある項目の節を開いてから、その欄へ移す。
      setSection(firstInvalid.section);
      requestAnimationFrame(() =>
        focusFirstInvalidField(data.fields.map((field) => [fieldId(field), found[textKey(field)]] as const)),
      );
      return;
    }
    save.mutate(overridesFromTexts(data.fields, texts, data.builtin), {
      onSuccess: (saved) => {
        const next = textsFromSections(saved.fields, saved.effective);
        setBase(JSON.stringify(next));
        setTexts(next);
        toast.success(t("settings.language.saved"));
      },
    });
  }

  return (
    <PageBody wide>
      {data.outdated_documents ? (
        <Banner
          severity="info"
          title={t("settings.language.outdated.title", { count: data.outdated_documents })}
        >
          {t("settings.language.outdated.description")}
        </Banner>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Languages size={16} className="text-accent-fg" aria-hidden />
            {t(`settings.language.section.${current}` as I18nKey)}
          </CardTitle>
          <CardDescription>{t(`settings.language.section.${current}.help` as I18nKey)}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Tabs
            idPrefix="language"
            ariaLabel={t("nav.settingsLanguagePack")}
            value={current}
            onChange={(value) => setSection(value)}
            items={sections.map((name) => ({
              id: name,
              label: t(`settings.language.section.${name}` as I18nKey),
            }))}
          />
          <div id={`language-panel-${current}`} role="tabpanel" aria-labelledby={`language-tab-${current}`}>
            <FormGrid>
              {fields.map((field) => (
                <LanguageField
                  key={textKey(field)}
                  field={field}
                  data={data}
                  texts={texts}
                  error={errors[textKey(field)]}
                  disabled={save.isPending}
                  onChange={(value) => update(textKey(field), value)}
                />
              ))}
            </FormGrid>
          </div>
          <FormActionBar
            ariaLabel={t("settings.language.actions.label")}
            primaryActions={[
              {
                id: "save",
                label: t("settings.language.actions.save"),
                icon: Save,
                loading: save.isPending,
                disabled: !dirty,
                onClick: submit,
              },
            ]}
            secondaryActions={[
              {
                id: "reset",
                label: t("settings.language.actions.reset"),
                icon: RotateCcw,
                disabled: !dirty || save.isPending,
                onClick: () => {
                  save.reset();
                  setErrors({});
                  setTexts(textsFromSections(data.fields, data.effective));
                },
              },
            ]}
            status={
              save.isError ? (
                <FormStatus tone="danger" message={saveError} />
              ) : reingest ? (
                <FormStatus tone="warning" message={t("settings.language.reingest")} />
              ) : null
            }
          />
        </CardContent>
      </Card>
      <PreviewCard data={data} texts={texts} />
    </PageBody>
  );
}

function LanguageField({
  field,
  data,
  texts,
  error,
  disabled,
  onChange,
}: {
  field: LanguagePackField;
  data: LanguagePackData;
  texts: LanguagePackTexts;
  error: string | undefined;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const key = textKey(field);
  const label = t(`settings.language.field.${field.key}` as I18nKey);
  const helper = t(`settings.language.field.${field.key}.help` as I18nKey);
  const changed = isChanged(field, texts, data.builtin);
  const builtin = data.builtin[field.section]?.[field.key];
  const resetRow = changed ? (
    <div className="flex flex-wrap items-center gap-2">
      <StatusBadge variant="info" icon={false} label={t("settings.language.changed")} />
      <Button
        size="sm"
        variant="ghost"
        icon={RotateCcw}
        disabled={disabled}
        onClick={() => onChange(Array.isArray(builtin) ? builtin.join("\n") : (builtin ?? ""))}
      >
        {t("settings.language.resetField")}
      </Button>
    </div>
  ) : null;
  if (field.kind === "choice") {
    const options: SelectFieldOption<string>[] = field.choices.map((value) => ({
      value,
      label: t(`settings.language.choice.${value}` as I18nKey),
    }));
    return (
      <div className="space-y-2">
        <SelectField
          id={fieldId(field)}
          label={label}
          helper={helper}
          value={texts[key] ?? ""}
          options={options}
          width="lg"
          disabled={disabled}
          error={error}
          onValueChange={onChange}
        />
        {resetRow}
      </div>
    );
  }
  if (field.kind === "text") {
    return (
      <div className="space-y-2">
        <TextField
          id={fieldId(field)}
          label={label}
          helper={helper}
          error={error}
          value={texts[key] ?? ""}
          width="md"
          disabled={disabled}
          onValueChange={onChange}
        />
        {resetRow}
      </div>
    );
  }
  const lines = (texts[key] ?? "").split("\n").length;
  return (
    <div className="space-y-2">
      <TextareaField
        id={fieldId(field)}
        label={label}
        helper={helper}
        error={error}
        value={texts[key] ?? ""}
        rows={lines > LONG_FIELD_ITEMS ? 6 : 3}
        width="md"
        disabled={disabled}
        onValueChange={onChange}
      />
      {resetRow}
    </div>
  );
}

function PreviewCard({ data, texts }: { data: LanguagePackData; texts: LanguagePackTexts }) {
  const preview = usePreviewLanguagePack();
  const [text, setText] = useState("");
  const invalid = Object.keys(languagePackErrors(data.fields, texts)).length > 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical size={16} className="text-accent-fg" aria-hidden />
          {t("settings.language.preview.title")}
        </CardTitle>
        <CardDescription>{t("settings.language.preview.description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <TextareaField
          id="language-preview-text"
          label={t("settings.language.preview.text")}
          value={text}
          rows={4}
          width="full"
          onValueChange={setText}
        />
        <div>
          <Button
            icon={FlaskConical}
            loading={preview.isPending}
            disabled={!text.trim() || invalid}
            onClick={() =>
              preview.mutate({ text, overrides: overridesFromTexts(data.fields, texts, data.builtin) })
            }
          >
            {t("settings.language.preview.run")}
          </Button>
        </div>
        {preview.isError ? <FormStatus tone="danger" message={t("settings.language.preview.error")} /> : null}
        {preview.data ? (
          <div className="grid gap-4 md:grid-cols-2" data-testid="language-preview-result">
            <PreviewResult title={t("settings.language.preview.saved")} result={preview.data.saved} />
            <PreviewResult title={t("settings.language.preview.draft")} result={preview.data.draft} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function PreviewResult({ title, result }: { title: string; result: LanguagePackPreviewResult }) {
  const references = result.references.map(
    (ref) => `${ref.label} → ${ref.kind}:${ref.key}${ref.document_title ? `（${ref.document_title}）` : ""}`,
  );
  const rows: [string, string[]][] = [
    [t("settings.language.preview.references"), references],
    [t("settings.language.preview.headingKeys"), result.heading_keys],
    [t("settings.language.preview.titleKeys"), result.title_keys],
  ];
  return (
    <section className="min-w-0 space-y-2 rounded-lg border border-border p-3" aria-label={title}>
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      <dl className="space-y-2 text-sm">
        {rows.map(([label, items]) => (
          <div key={label}>
            <dt className="text-xs text-fg-muted">{label}</dt>
            <dd className="break-words text-fg">
              {items.length ? items.join(" / ") : t("settings.language.preview.none")}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
