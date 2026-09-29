import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Disclosure,
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
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`settings.docragPrompts.${promptKey}.title`)}</CardTitle>
        <CardDescription>{t(`settings.docragPrompts.${promptKey}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <DocragPromptPanel promptKey={promptKey} showStages={showStages} />
      </CardContent>
    </Card>
  );
}

/**
 * カードの枠を持たないプロンプトの編集欄。ほかの設定の節の中に置くときに使う
 * （文書解析の「解析後の処理」の Vision の中の読み取りプロンプト。#528）。
 */
export function DocragPromptPanel({
  promptKey,
  showStages = false,
}: {
  promptKey: DocragPromptKey;
  showStages?: boolean;
}) {
  const query = useDocragPrompts();
  const prompt = query.data?.prompts.find((item) => item.key === promptKey);
  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError || !prompt) {
    return (
      <ErrorState message={t("settings.docragPrompts.loadError")} onRetry={() => void query.refetch()} />
    );
  }
  return (
    <div className="space-y-4">
      <DocragPromptEditor prompt={prompt} />
      {showStages ? <ReadonlyStages stages={query.data.stages} /> : null}
    </div>
  );
}

function DocragPromptEditor({ prompt }: { prompt: DocragPromptView }) {
  const save = useSaveDocragPrompt();
  const confirm = useConfirm();
  const [content, setContent] = useState(prompt.content);
  // 編集欄が基にした保存値。保存値が変わったレンダーで、未編集なら編集欄を保存値へ揃える。
  // 編集中の内容は背景の再取得で上書きしない(UX 契約 workspace-state。#276)。
  const [baseContent, setBaseContent] = useState(prompt.content);
  if (prompt.content !== baseContent) {
    setBaseContent(prompt.content);
    if (content === baseContent) setContent(prompt.content);
  }
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
    if (!confirmed) return;
    save.mutate(
      { key: prompt.key, content: null },
      {
        // 既定に戻すときは編集中の内容も捨て、既定の内容を編集欄へ入れる。
        onSuccess: (data) => {
          const restored = data.prompts.find((item) => item.key === prompt.key);
          if (restored) setContent(restored.content);
        },
      }
    );
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
        className="w-full resize-y rounded-md border border-border-control bg-surface-sunken px-3 py-2 font-mono text-xs leading-relaxed text-fg focus-visible:border-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
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
        <Disclosure
          key={stage.id}
          summary={t(`settings.docragPrompts.stage.${stage.id}` as I18nKey)}
          surface="sunken"
          contentClassName="space-y-3"
        >
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
        </Disclosure>
      ))}
    </section>
  );
}
