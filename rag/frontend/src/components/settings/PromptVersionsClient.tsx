"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Button,
  FormStatus,
  RequiredBadge,
  Skeleton,
  Switch,
  TextField,
} from "@engchina/production-ready-ui";
import { useState } from "react";
import { CheckCircle2, FileText, Plus } from "lucide-react";

import { ErrorState } from "@/components/StateViews";
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
      <PageBody>
        <Skeleton className="h-64 w-full rounded-lg" />
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody>
        <ErrorState
          message={
            query.error instanceof ApiError ? query.error.message : t("settings.prompts.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
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
    <PageBody>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <FileText size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.prompts.overview.title")}</CardTitle>
              <CardDescription>{t("settings.prompts.overview.description")}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <TextField
            id="prompt-version-name"
            label={t("settings.prompts.form.name")}
            value={name}
            maxLength={NAME_MAX}
            onValueChange={setName}
            placeholder={t("settings.prompts.form.namePlaceholder")}
            required
            requiredLabel={t("common.required")}
          />
          <Field label={t("settings.prompts.form.systemPrompt")} required>
            <textarea
              aria-required="true"
              value={systemPrompt}
              maxLength={PROMPT_MAX}
              onChange={(event) => setSystemPrompt(event.target.value)}
              placeholder={t("settings.prompts.form.systemPromptPlaceholder")}
              rows={6}
              className="w-full resize-y rounded-md border border-border-control bg-surface p-3 text-sm leading-relaxed text-fg outline-none transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring"
            />
          </Field>
          <TextField
            id="prompt-version-note"
            label={t("settings.prompts.form.note")}
            value={note}
            maxLength={NOTE_MAX}
            onValueChange={setNote}
            placeholder={t("settings.prompts.form.notePlaceholder")}
          />
          <div className="flex items-center justify-between gap-3 text-sm text-fg">
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
              aria-label={t("settings.prompts.actions.create")} icon={Plus}>
              {t("settings.prompts.actions.create")}
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
            <p className="py-6 text-center text-sm text-fg-muted">{t("settings.prompts.list.empty")}</p>
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
    </PageBody>
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
      <span className="flex items-center gap-2 text-sm font-medium text-fg">
        {label}
        {/* 入力側の aria-required が必須を伝えるので、バッジは読み上げから外す */}
        {required ? <RequiredBadge label={t("common.required")} aria-hidden /> : null}
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
        version.active ? "border-accent-emphasis bg-accent-subtle" : "border-border bg-surface"
      )}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-fg">{version.name}</span>
          {version.active ? (
            <span className="inline-flex items-center gap-1 rounded bg-accent-muted px-1.5 py-0.5 text-xs font-medium text-accent-fg">
              <CheckCircle2 size={14} aria-hidden />
              {t("settings.prompts.list.activeBadge")}
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 text-xs text-fg-muted">
          {t("settings.prompts.list.createdAt")}: {formatTimestamp(version.created_at)}
        </div>
        {version.note ? (
          <p className="mt-1 break-words text-xs leading-relaxed text-fg-muted">{version.note}</p>
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
