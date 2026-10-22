import {
  Disclosure,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormActionBar,
  FormGrid,
  FormStatus,
  Skeleton,
  StatusBadge,
  TextareaField,
  useConfirm,
  TimedLoadingState,
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
export function AnswerPromptCard({ promptKey }: { promptKey: AnswerPromptKey }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t(`settings.answerPrompts.${promptKey}.title`)}</CardTitle>
        <CardDescription>{t(`settings.answerPrompts.${promptKey}.description`)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <AnswerPromptPanel promptKey={promptKey} />
      </CardContent>
    </Card>
  );
}

/**
 * カードの枠を持たないプロンプトの編集欄。ほかの設定の節の中に置くときに使う
 * （文書解析の「解析後の処理」の Vision の中の読み取りプロンプト。#528）。
 */
export function AnswerPromptPanel({ promptKey }: { promptKey: AnswerPromptKey }) {
  const query = useAnswerPrompts();
  const prompt = query.data?.prompts.find((item) => item.key === promptKey);
  if (query.isPending) {
    // ほかの読み込み中と同じく、先頭に文言と経過時間の行、その下に内容の形の Skeleton（#2510）。
    return (
      <TimedLoadingState
        label={t("settings.answerPrompts.loading")}
        operationKey={`answer-prompt-load:${promptKey}`}
        framed={false}
        testId="answer-prompt-loading"
      >
        <Skeleton className="h-40 w-full" />
      </TimedLoadingState>
    );
  }
  if (query.isError || !prompt) {
    return (
      <ErrorState message={t("settings.answerPrompts.loadError")} onRetry={() => void query.refetch()} />
    );
  }
  return (
    <div className="space-y-4">
      <AnswerPromptEditor prompt={prompt} />
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
      {/* プロンプトの編集欄はフォームの最大幅まで（1920px で 1 行が長くなりすぎない。#1891）。 */}
      <FormGrid columns={1}>
        <TextareaField
          id={inputId}
          width="full"
          label={fieldLabel}
          required
          helper={prompt.required_placeholders.length ? t("settings.answerPrompts.placeholders", {
            names: prompt.required_placeholders.map((name) => `{{${name}}}`).join("、"),
          }) : t("settings.answerPrompts.noPlaceholders")}
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
      </FormGrid>
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

/** 各機能の設定で共通のプロンプト編集操作を示す。 */
export function FeaturePromptDisclosure({ promptKey }: { promptKey: AnswerPromptKey }) {
  return (
    <Disclosure
      summary={t(`settings.answerPrompts.${promptKey}.title`)}
      description={t(`settings.answerPrompts.${promptKey}.description`)}
      surface="sunken"
    >
      <AnswerPromptPanel promptKey={promptKey} />
      {promptKey === "image_retrieval" ? <VisionPromptRules /> : null}
    </Disclosure>
  );
}


/** 画像の読み取りに必ず適用する共通規則は、テンプレートと同じ場所で確認できる。 */
function VisionPromptRules() {
  const query = useAnswerPrompts();
  const parts = query.data?.stages.find((stage) => stage.id === "picture_description")?.prompts.filter((part) => part.id !== "template") ?? [];
  if (!parts.length) return null;
  return (
    <Disclosure summary={t("settings.answerPrompts.fixedRules")} surface="sunken" contentClassName="space-y-3">
      {parts.map((part) => (
        <TextareaField key={part.id} id={`picture-prompt-rule-${part.id}`} label={t(`settings.answerPrompts.part.${part.id}` as I18nKey)} value={part.content} readOnly monospace rows={12} />
      ))}
    </Disclosure>
  );
}
