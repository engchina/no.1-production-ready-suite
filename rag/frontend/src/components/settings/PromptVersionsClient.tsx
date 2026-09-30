"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Button,
  FieldError,
  FieldLabel,
  FormStatus,
  RowActionMenu,
  type EntityAction,
  Switch,
  TextField,
  TimedLoadingState,
  ListSkeleton,
  DEFAULT_PAGE_SIZE,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
  usePagination,
} from "@engchina/production-ready-ui";
import { useState } from "react";
import { CheckCircle2, FileText, Plus } from "lucide-react";

import { EmptyState } from "@/components/StateViews";
import { ListPagination } from "@/components/ListPagination";
import { ErrorState } from "@/components/StateViews";
import { DocragUnusedNote } from "@/components/settings/DocragUnusedNote";
import { DocragPromptCard } from "./DocragPromptEditor";
import { ApiError, type PromptVersionData } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { focusFirstInvalidField, requiredTextError } from "@/lib/required-fields";
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
  const [errors, setErrors] = useState<{ name?: string | null; systemPrompt?: string | null }>({});

  // 作成前の新しい版の入力があるときだけ離脱を確認する（作成成功で入力は空に戻る）。
  useLeaveGuard(Boolean(name.trim() || systemPrompt.trim() || note.trim()));

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-prompts-load"
          placement="page"
          testId="settings-prompts-loading"
        >
          <ListSkeleton rowClassName="h-16" />
        </TimedLoadingState>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
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
  const createError =
    create.error instanceof ApiError ? create.error.message : t("settings.prompts.actions.saveError");
  const activateError =
    activate.error instanceof ApiError
      ? activate.error.message
      : t("settings.prompts.actions.saveError");

  function submit() {
    // 作成のボタンは押せる状態のまま、未入力は押したときに欄の直下へ出す（#541）。文言は backend と同じ。
    const nextErrors = {
      name: requiredTextError(name, t("settings.prompts.form.nameRequired")),
      systemPrompt: requiredTextError(systemPrompt, t("settings.prompts.form.systemPromptRequired")),
    };
    setErrors(nextErrors);
    if (
      focusFirstInvalidField([
        ["prompt-version-name", nextErrors.name],
        ["prompt-version-system-prompt", nextErrors.systemPrompt],
      ])
    ) {
      return;
    }
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

  // 版 1 件の操作。行に 1 個の RowActionMenu にまとめる（buttons.md §5.1）。
  // 有効な版は badge で示し、有効化は理由付きで無効にする。
  function versionActions(version: PromptVersionData): EntityAction[] {
    return [
      {
        id: "activate",
        label: t("settings.prompts.actions.activate"),
        ariaLabel: version.active ? t("settings.prompts.actions.alreadyActive") : undefined,
        icon: CheckCircle2,
        disabled: version.active || activate.isPending,
        loading: activate.isPending && activate.variables === version.version_id,
        testId: `prompt-version-activate-${version.version_id}`,
        onSelect: () => onActivate(version.version_id),
      },
    ];
  }

  function onActivate(versionId: string) {
    activate.reset();
    setSuccessMessage(null);
    activate.mutate(versionId, {
      onSuccess: () => setSuccessMessage(t("settings.prompts.actions.activated")),
    });
  }

  return (
    <PageBody wide>
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
          <DocragUnusedNote>{t("settings.prompts.docragUnused")}</DocragUnusedNote>
          <TextField
            id="prompt-version-name"
            label={t("settings.prompts.form.name")}
            value={name}
            maxLength={NAME_MAX}
            onValueChange={(value) => {
              setName(value);
              setErrors((current) => ({ ...current, name: null }));
            }}
            placeholder={t("settings.prompts.form.namePlaceholder")}
            error={errors.name ?? undefined}
            required
          />
          <div className="space-y-1.5">
            <FieldLabel
              htmlFor="prompt-version-system-prompt"
              label={t("settings.prompts.form.systemPrompt")}
              required
              className="block"
            />
            <textarea
              id="prompt-version-system-prompt"
              aria-required="true"
              aria-invalid={errors.systemPrompt ? true : undefined}
              aria-describedby={errors.systemPrompt ? "prompt-version-system-prompt-error" : undefined}
              value={systemPrompt}
              maxLength={PROMPT_MAX}
              onChange={(event) => {
                setSystemPrompt(event.target.value);
                setErrors((current) => ({ ...current, systemPrompt: null }));
              }}
              placeholder={t("settings.prompts.form.systemPromptPlaceholder")}
              rows={6}
              className={cn(
                "w-full resize-y rounded-md border bg-surface p-3 text-sm leading-relaxed text-fg transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring",
                errors.systemPrompt ? "border-danger-fg" : "border-border-control",
              )}
            />
            <FieldError id="prompt-version-system-prompt-error" message={errors.systemPrompt} />
          </div>
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
            <EmptyState title={t("settings.prompts.list.empty")} />
          ) : (
            <VersionList versions={versions} actionsFor={versionActions} />
          )}
        </CardContent>
      </Card>
      <DocragPromptCard promptKey="vlm_answer" showStages />
    </PageBody>
  );
}

/**
 * 版の一覧（#265）。md 未満 5 行・md 以上 8 行の高さで中を縦スクロールにし、10 件/ページで送る。
 * 有効化で行が変わってもページは保ち、版を作成して件数が変わったときだけ 1 ページ目へ戻す。
 */
function VersionList({
  versions,
  actionsFor,
}: {
  versions: PromptVersionData[];
  actionsFor: (version: PromptVersionData) => EntityAction[];
}) {
  const { page, setPage, totalPages, pageItems, range } = usePagination(versions, DEFAULT_PAGE_SIZE, {
    resetKey: versions.length,
  });
  return (
    <div className="grid gap-2">
      <ul
        className={cn("space-y-2", INFORMATION_LIST_SCROLL_CLASS, INFORMATION_TABLE_FOCUS_CLASS)}
        aria-label={t("settings.prompts.list.scrollLabel")}
        tabIndex={0}
        data-testid="prompt-version-list"
      >
        {pageItems.map((version) => (
          <VersionRow key={version.version_id} version={version} actions={actionsFor(version)} />
        ))}
      </ul>
      <ListPagination
        page={page}
        totalPages={totalPages}
        range={range}
        onPageChange={setPage}
        testId="prompt-version-pagination"
      />
    </div>
  );
}

function VersionRow({
  version,
  actions,
}: {
  version: PromptVersionData;
  actions: EntityAction[];
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
      <div className="shrink-0 self-end sm:self-center">
        <RowActionMenu
          actions={actions}
          ariaLabel={t("common.objectActions.aria", { name: version.name })}
          testId={`prompt-version-row-actions-${version.version_id}`}
        />
      </div>
    </li>
  );
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ja-JP");
}
