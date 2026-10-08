import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Disclosure,
  FormActionBar,
  FormStatus,
  Skeleton,
  StatusBadge,
  TextareaField,
  useConfirm,
} from "@production-ready/ui";
import { History, RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { ApiError, type AnswerPromptKey, type AnswerPromptView } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useAnswerPrompts, useSaveAnswerPrompt } from "@/lib/queries";
import { focusFirstInvalidField } from "@/lib/required-fields";
import { toast } from "@/lib/toast";

import { answerPromptError } from "./answer-prompt.logic";

const PROMPT_MAX = 50000;

/**
 * 編集できるプロンプト（回答生成のプロンプト `vlm_answer` / 図・画像の読み取りプロンプト `image_retrieval`）を 1 つ編集する。
 * 全体で 1 つの設定（検索・回答プロファイル・文書レシピでは上書きしない）。保存した内容は次の回答・次の解析から使う。
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
  // 保存を押した後だけ欄のエラーを出す（直すと消える）。
  const [showError, setShowError] = useState(false);
  const fieldLabel = t(`settings.answerPrompts.${prompt.key}.field`);
  const error = answerPromptError(content, fieldLabel, prompt.required_placeholders);
  // 保存中は離脱を止める。
  useLeaveGuard(dirty, save.isPending);
  const inputId = `answer-prompt-${prompt.key}`;
  const resetting = save.isPending && save.variables?.content === null;

  function submit() {
    if (save.isPending) return;
    // 空・必須の placeholder の欠けは送る前に欄の下へ出す（backend の validate_prompt と同じ規則。#1010）。
    setShowError(true);
    if (focusFirstInvalidField([[inputId, error]])) return;
    save.mutate(
      { key: prompt.key, content },
      // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
      { onSuccess: () => toast.success(t("settings.answerPrompts.saved")) }
    );
  }

  function discard() {
    save.reset();
    setShowError(false);
    setContent(prompt.content);
  }

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
          setShowError(false);
          toast.success(t("settings.answerPrompts.resetDone"));
        },
      }
    );
  }

  return (
    <div className="space-y-2">
      {/* 空のままでは保存できない（既定へ戻すのは「既定に戻す」）ので必須（#531）。 */}
      <TextareaField
        id={inputId}
        label={fieldLabel}
        required
        helper={t("settings.answerPrompts.placeholders", {
          names: prompt.required_placeholders.map((name) => `{{${name}}}`).join("、"),
        })}
        error={showError ? (error ?? undefined) : undefined}
        value={content}
        maxLength={PROMPT_MAX}
        rows={16}
        spellCheck={false}
        monospace
        disabled={save.isPending}
        onChange={(event) => {
          save.reset();
          setContent(event.target.value);
        }}
      />
      <FormActionBar
        ariaLabel={t("settings.answerPrompts.actions.label")}
        primaryActions={[
          {
            id: "save",
            label: t("settings.answerPrompts.save"),
            icon: Save,
            loading: save.isPending && !resetting,
            // 「既定に戻す」の処理中は押せないだけにする（スピナーは押した側だけ。#819）。
            disabled: !dirty || resetting,
            onClick: submit,
          },
        ]}
        secondaryActions={[
          {
            id: "discard",
            label: t("settings.answerPrompts.discard"),
            icon: RotateCcw,
            disabled: !dirty || save.isPending,
            onClick: discard,
          },
          {
            id: "reset",
            label: t("settings.answerPrompts.reset"),
            icon: History,
            loading: resetting,
            disabled: !prompt.customized || (save.isPending && !resetting),
            onClick: () => void reset(),
          },
        ]}
        status={
          save.isError ? (
            <FormStatus
              tone="danger"
              message={
                save.error instanceof ApiError ? save.error.message : t("settings.answerPrompts.saveError")
              }
            />
          ) : dirty ? (
            <FormStatus tone="warning" message={t("settings.answerPrompts.unsaved")} />
          ) : (
            // 保存値が既定値か変更済みか（#584）。
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
          )
        }
      />
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
