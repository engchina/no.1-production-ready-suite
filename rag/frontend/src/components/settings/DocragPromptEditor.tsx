import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormStatus,
  Skeleton,
  StatusBadge,
  useConfirm,
} from "@engchina/production-ready-ui";
import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { ApiError, type DocragPromptKey, type DocragPromptView } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { useDocragPrompts, useSaveDocragPrompt } from "@/lib/queries";

const PROMPT_MAX = 50000;

/**
 * DocRAG のプロンプト（rag_poc の vlm_answer.txt / image_retrieval.txt）を 1 つ編集する。
 * 全体で 1 つの設定（業務ビュー・文書レシピでは上書きしない）。保存した内容は次の回答・次の解析から使う。
 */
export function DocragPromptCard({
  promptKey,
  showStages = false,
}: {
  promptKey: DocragPromptKey;
  showStages?: boolean;
}) {
  const query = useDocragPrompts();
  const prompt = query.data?.prompts.find((item) => item.key === promptKey);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`settings.docragPrompts.${promptKey}.title`)}</CardTitle>
        <CardDescription>{t(`settings.docragPrompts.${promptKey}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {query.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : query.isError || !prompt ? (
          <ErrorState message={t("settings.docragPrompts.loadError")} onRetry={() => void query.refetch()} />
        ) : (
          <>
            <DocragPromptEditor prompt={prompt} />
            {showStages ? <ReadonlyStages stages={query.data.stages} /> : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function DocragPromptEditor({ prompt }: { prompt: DocragPromptView }) {
  const save = useSaveDocragPrompt();
  const confirm = useConfirm();
  const [content, setContent] = useState(prompt.content);
  // 保存・復帰で保存値が変わったレンダーで、編集欄を保存値へ揃える。
  if (useValuesChanged([prompt.content])) setContent(prompt.content);
  const dirty = content !== prompt.content;
  useLeaveGuard(dirty);
  const inputId = `docrag-prompt-${prompt.key}`;
  const helperId = `${inputId}-helper`;

  async function reset() {
    const confirmed = await confirm({
      title: t("settings.docragPrompts.resetTitle"),
      description: t("settings.docragPrompts.resetDescription"),
      confirmLabel: t("settings.docragPrompts.reset"),
      tone: "danger",
    });
    if (confirmed) save.mutate({ key: prompt.key, content: null });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={inputId} className="text-sm font-medium text-fg">
          {t(`settings.docragPrompts.${prompt.key}.field`)}
        </label>
        <StatusBadge
          variant={prompt.customized ? "info" : "neutral"}
          label={
            prompt.customized
              ? t("settings.docragPrompts.customized", {
                  value: prompt.updated_at ? formatDateTime(prompt.updated_at) : "—",
                })
              : t("settings.docragPrompts.default")
          }
        />
      </div>
      <textarea
        id={inputId}
        value={content}
        maxLength={PROMPT_MAX}
        rows={16}
        spellCheck={false}
        disabled={save.isPending}
        aria-describedby={helperId}
        onChange={(event) => setContent(event.target.value)}
        className="w-full resize-y rounded-md border border-border-control bg-surface-sunken px-3 py-2 font-mono text-xs leading-relaxed text-fg outline-none focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
      />
      <p id={helperId} className="text-xs leading-relaxed text-fg-muted">
        {t("settings.docragPrompts.placeholders", {
          names: prompt.required_placeholders.map((name) => `{{${name}}}`).join("、"),
        })}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="md"
          icon={Save}
          loading={save.isPending && save.variables?.content !== null}
          disabled={!dirty || !content.trim()}
          onClick={() => save.mutate({ key: prompt.key, content })}
        >
          {t("settings.docragPrompts.save")}
        </Button>
        <Button
          type="button"
          size="md"
          variant="secondary"
          icon={RotateCcw}
          loading={save.isPending && save.variables?.content === null}
          disabled={!prompt.customized || save.isPending}
          onClick={() => void reset()}
        >
          {t("settings.docragPrompts.reset")}
        </Button>
        {save.isSuccess && !dirty ? (
          <FormStatus
            tone="success"
            message={t(
              save.variables?.content === null
                ? "settings.docragPrompts.resetDone"
                : "settings.docragPrompts.saved"
            )}
          />
        ) : null}
        {save.isError ? (
          <FormStatus
            tone="danger"
            message={
              save.error instanceof ApiError ? save.error.message : t("settings.docragPrompts.saveError")
            }
          />
        ) : null}
      </div>
    </div>
  );
}

function ReadonlyStages({ stages }: { stages: { id: string; prompts: { id: string; content: string }[] }[] }) {
  return (
    <section className="space-y-2 border-t border-border pt-4">
      <h3 className="text-sm font-semibold text-fg">{t("settings.docragPrompts.stages.title")}</h3>
      <p className="text-xs leading-relaxed text-fg-muted">{t("settings.docragPrompts.stages.description")}</p>
      {stages.map((stage) => (
        <details key={stage.id} className="rounded-md border border-border bg-surface-sunken p-3">
          <summary className="cursor-pointer text-sm font-medium text-fg">
            {t(`settings.docragPrompts.stage.${stage.id}` as I18nKey)}
          </summary>
          <div className="mt-2 space-y-3">
            {stage.prompts.map((part) => (
              <div key={part.id}>
                <p className="text-xs font-medium text-fg-muted">
                  {t(`settings.docragPrompts.part.${part.id}` as I18nKey)}
                </p>
                <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface p-2 font-mono text-xs leading-relaxed text-fg">
                  {part.content}
                </pre>
              </div>
            ))}
          </div>
        </details>
      ))}
    </section>
  );
}
