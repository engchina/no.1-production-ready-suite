"use client";

import { FormSkeleton, PageBody, TimedLoadingState } from "@production-ready/ui";

import { useAnswerPrompts } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { AnswerPromptCard } from "./AnswerPromptEditor";

/**
 * 回答プロンプトの画面。回答を作る指示のテンプレートと、回答の各工程（読み取り専用）を出す。
 * 以前の system prompt の版（作成・有効化）は、回答エンジンを 1 つにしたときに削除した（#595）。
 */
export function AnswerPromptSettingsClient() {
  // 初回の読み込みはページの先頭で経過時間と形の Skeleton を出す（カードの中の Skeleton だけにしない）。
  // 取得の結果はカードの編集欄が同じ query から読む。
  const query = useAnswerPrompts();
  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-prompts-load"
          placement="page"
          testId="settings-prompts-loading"
        >
          <FormSkeleton />
        </TimedLoadingState>
      </PageBody>
    );
  }
  return (
    <PageBody wide>
      <AnswerPromptCard promptKey="vlm_answer" showStages />
    </PageBody>
  );
}
