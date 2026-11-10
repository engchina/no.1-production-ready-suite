"use client";

import { FormSkeleton, PageBody, TimedLoadingState } from "@production-ready/ui";

import { ErrorState } from "@/components/StateViews";
import { AnswerTermSettingsCard } from "@/components/settings/AnswerTermSettingsCard";
import { useAnswerPrompts } from "@/lib/queries";
import { t } from "@/lib/i18n";

import { isRetrievalPromptStage } from "./answer-prompt.logic";

import { AnswerPromptStages } from "./AnswerPromptStages";

/**
 * 回答プロンプトの画面。回答の各工程の指示を出す（編集できるのは生成の役割の文と登録済みQ&A。#4782）。
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
  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState message={t("settings.answerPrompts.loadError")} onRetry={() => void query.refetch()} />
      </PageBody>
    );
  }
  const stages = query.data.stages.filter((stage) => stage.phase !== "ingestion" && stage.phase !== "indexing" && !isRetrievalPromptStage(stage) && stage.id !== "evaluation");
  return (
    <PageBody wide>
      <AnswerTermSettingsCard />
      <AnswerPromptStages stages={stages} />
    </PageBody>
  );
}
