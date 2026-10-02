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
  TextareaField,
  useConfirm,
} from "@engchina/production-ready-ui";
import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { ApiError, type AnswerPromptKey, type AnswerPromptView } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useAnswerPrompts, useSaveAnswerPrompt } from "@/lib/queries";

const PROMPT_MAX = 50000;

/**
 * 編集できるプロンプト（回答生成のプロンプト `vlm_answer` / 図・画像の読み取りプロンプト `image_retrieval`）を 1 つ編集する。
 * 全体で 1 つの設定（業務ビュー・文書レシピでは上書きしない）。保存した内容は次の回答・次の解析から使う。
 */
export function AnswerPromptCard({
  promptKey,
  showStages = false,
}: {
  promptKey: AnswerPromptKey;
  showStages?: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`settings.answerPrompts.${promptKey}.title`)}</CardTitle>
        <CardDescription>{t(`settings.answerPrompts.${promptKey}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <AnswerPromptPanel promptKey={promptKey} showStages={showStages} />
      </CardContent>
    </Card>
  );
}

/**
 * カードの枠を持たないプロンプトの編集欄。ほかの設定の節の中に置くときに使う
 * （文書解析の「解析後の処理」の Vision の中の読み取りプロンプト。#528）。
 */
export function AnswerPromptPanel({
  promptKey,
  showStages = false,
}: {
  promptKey: AnswerPromptKey;
  showStages?: boolean;
}) {
  const query = useAnswerPrompts();
  const prompt = query.data?.prompts.find((item) => item.key === promptKey);
  if (query.isPending) return <Skeleton className="h-40 w-full" />;
  if (query.isError || !prompt) {
    return (
      <ErrorState message={t("settings.answerPrompts.loadError")} onRetry={() => void query.refetch()} />
    );
  }
  return (
    <div className="space-y-4">
      <AnswerPromptEditor prompt={prompt} />
      {showStages ? <ReadonlyStages stages={query.data.stages} /> : null}
    </div>
  );
}

function AnswerPromptEditor({ prompt }: { prompt: AnswerPromptView }) {
  const save = useSaveAnswerPrompt();
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
  const inputId = `answer-prompt-${prompt.key}`;

  async function reset() {
    const confirmed = await confirm({
      title: t("settings.answerPrompts.resetTitle"),
      description: t("settings.answerPrompts.resetDescription"),
      confirmLabel: t("settings.answerPrompts.reset"),
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
      {/* 空のままでは保存できない（既定へ戻すのは「既定に戻す」）ので必須（#531）。 */}
      <TextareaField
        id={inputId}
        label={t(`settings.answerPrompts.${prompt.key}.field`)}
        required
        helper={t("settings.answerPrompts.placeholders", {
          names: prompt.required_placeholders.map((name) => `{{${name}}}`).join("、"),
        })}
        value={content}
        maxLength={PROMPT_MAX}
        rows={16}
        spellCheck={false}
        monospace
        disabled={save.isPending}
        onChange={(event) => setContent(event.target.value)}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="md"
          icon={Save}
          loading={save.isPending && save.variables?.content !== null}
          // 「既定に戻す」の処理中は押せないだけにする（スピナーは押した側だけ。#819）。
          disabled={!dirty || !content.trim() || (save.isPending && save.variables?.content === null)}
          onClick={() => save.mutate({ key: prompt.key, content })}
        >
          {t("settings.answerPrompts.save")}
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
          {t("settings.answerPrompts.reset")}
        </Button>
        {/* 既定値か変更済みか。ラベルは文字列だけを持つため、「既定に戻す」の隣に置く（#584）。 */}
        <StatusBadge
          variant={prompt.customized ? "info" : "neutral"}
          label={
            prompt.customized
              ? t("settings.answerPrompts.customized", {
                  value: prompt.updated_at ? formatDateTime(prompt.updated_at) : "—",
                })
              : t("settings.answerPrompts.default")
          }
        />
        {save.isSuccess && !dirty ? (
          <FormStatus
            tone="success"
            message={t(
              save.variables?.content === null
                ? "settings.answerPrompts.resetDone"
                : "settings.answerPrompts.saved"
            )}
          />
        ) : null}
        {save.isError ? (
          <FormStatus
            tone="danger"
            message={
              save.error instanceof ApiError ? save.error.message : t("settings.answerPrompts.saveError")
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
      <h3 className="text-sm font-semibold text-fg">{t("settings.answerPrompts.stages.title")}</h3>
      <p className="text-xs leading-relaxed text-fg-muted">{t("settings.answerPrompts.stages.description")}</p>
      {stages.map((stage) => (
        <Disclosure
          key={stage.id}
          summary={t(`settings.answerPrompts.stage.${stage.id}` as I18nKey)}
          surface="sunken"
          contentClassName="space-y-3"
        >
            {stage.prompts.map((part) => (
              <div key={part.id}>
                <p className="text-xs font-medium text-fg-muted">
                  {t(`settings.answerPrompts.part.${part.id}` as I18nKey)}
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
