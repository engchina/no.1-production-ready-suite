"use client";

import { useState } from "react";
import { CheckCircle2, FileText, Plus } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { ApiError, type PromptVersionData } from "@/lib/api";
import { t } from "@/lib/i18n";
import { usePromptVersions, useCreatePromptVersion, useActivatePromptVersion } from "@/lib/queries";
import { cn } from "@/lib/utils";

const NAME_MAX = 120;
const PROMPT_MAX = 20000;
const NOTE_MAX = 2000;

/** 回答プロンプト版(custom 回答スタイルが使用)の作成・有効化画面。 */
export function PromptVersionsClient() {
  const query = usePromptVersions();
  const create = useCreatePromptVersion();
  const activate = useActivatePromptVersion();
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [note, setNote] = useState("");
  const [activateOnCreate, setActivateOnCreate] = useState(true);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

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
            query.error instanceof ApiError ? query.error.message : t("settings.prompts.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const versions = query.data?.versions ?? [];
  const canSubmit = name.trim().length > 0 && systemPrompt.trim().length > 0;
  const createError =
    create.error instanceof ApiError ? create.error.message : t("settings.prompts.actions.saveError");
  const activateError =
    activate.error instanceof ApiError
      ? activate.error.message
      : t("settings.prompts.actions.saveError");

  function submit() {
    if (!canSubmit) return;
    create.reset();
    setSuccessMessage(null);
    create.mutate(
      {
        name: name.trim(),
        system_prompt: systemPrompt.trim(),
        note: note.trim() || undefined,
        activate: activateOnCreate,
      },
      {
        onSuccess: () => {
          setName("");
          setSystemPrompt("");
          setNote("");
          setActivateOnCreate(true);
          setSuccessMessage(t("settings.prompts.actions.created"));
        },
      }
    );
  }

  function onActivate(versionId: string) {
    activate.reset();
    setSuccessMessage(null);
    activate.mutate(versionId, {
      onSuccess: () => setSuccessMessage(t("settings.prompts.actions.activated")),
    });
  }

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <FileText size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.prompts.overview.title")}</CardTitle>
              <CardDescription>{t("settings.prompts.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label={t("settings.prompts.form.name")} required>
            <input
              type="text"
              value={name}
              maxLength={NAME_MAX}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("settings.prompts.form.namePlaceholder")}
              className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
            />
          </Field>
          <Field label={t("settings.prompts.form.systemPrompt")} required>
            <textarea
              value={systemPrompt}
              maxLength={PROMPT_MAX}
              onChange={(event) => setSystemPrompt(event.target.value)}
              placeholder={t("settings.prompts.form.systemPromptPlaceholder")}
              rows={6}
              className="w-full resize-y rounded-md border border-border bg-card p-3 text-sm leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
            />
          </Field>
          <Field label={t("settings.prompts.form.note")}>
            <input
              type="text"
              value={note}
              maxLength={NOTE_MAX}
              onChange={(event) => setNote(event.target.value)}
              placeholder={t("settings.prompts.form.notePlaceholder")}
              className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
            />
          </Field>
          <div className="flex items-center justify-between gap-3 text-sm text-foreground">
            <span>{t("settings.prompts.form.activate")}</span>
            <Switch
              checked={activateOnCreate}
              aria-label={t("settings.prompts.form.activate")}
              onCheckedChange={setActivateOnCreate}
            />
          </div>
          <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
            <div className="min-h-6">
              {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
              {create.isError ? <FormStatus tone="danger" message={createError} /> : null}
              {activate.isError ? <FormStatus tone="danger" message={activateError} /> : null}
            </div>
            <Button
              type="button"
              loading={create.isPending}
              disabled={!canSubmit}
              onClick={submit}
              aria-label={t("settings.prompts.actions.create")}
            >
              <Plus size={15} aria-hidden />
              {create.isPending
                ? t("settings.prompts.actions.creating")
                : t("settings.prompts.actions.create")}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings.prompts.list.title")}</CardTitle>
          <CardDescription>{t("settings.prompts.list.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          {versions.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted">{t("settings.prompts.list.empty")}</p>
          ) : (
            <ul className="space-y-2">
              {versions.map((version) => (
                <VersionRow
                  key={version.version_id}
                  version={version}
                  busy={activate.isPending}
                  onActivate={() => onActivate(version.version_id)}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="flex items-center gap-1 text-sm font-medium text-foreground">
        {label}
        {required ? <span className="text-danger">*</span> : null}
      </span>
      {children}
    </label>
  );
}

function VersionRow({
  version,
  busy,
  onActivate,
}: {
  version: PromptVersionData;
  busy: boolean;
  onActivate: () => void;
}) {
  return (
    <li
      className={cn(
        "flex flex-col gap-2 rounded-md border px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between",
        version.active ? "border-primary bg-primary/10" : "border-border bg-card"
      )}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-foreground">{version.name}</span>
          {version.active ? (
            <span className="inline-flex items-center gap-1 rounded bg-primary/15 px-1.5 py-0.5 text-[11px] font-medium text-primary">
              <CheckCircle2 size={12} aria-hidden />
              {t("settings.prompts.list.activeBadge")}
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 text-xs text-muted">
          {t("settings.prompts.list.createdAt")}: {formatTimestamp(version.created_at)}
        </div>
        {version.note ? (
          <p className="mt-1 break-words text-xs leading-relaxed text-muted">{version.note}</p>
        ) : null}
      </div>
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={version.active || busy}
        onClick={onActivate}
        className="shrink-0"
        aria-label={`${t("settings.prompts.actions.activate")} ${version.name}`}
      >
        {version.active
          ? t("settings.prompts.list.activeBadge")
          : t("settings.prompts.actions.activate")}
      </Button>
    </li>
  );
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ja-JP");
}
