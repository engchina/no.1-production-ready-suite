"use client";

import { FormSkeleton, PageBody, TimedLoadingState } from "@production-ready/ui";

import { ErrorState } from "@/components/StateViews";
import { AnsweringSettingsCard } from "@/components/settings/AnsweringSettingsCard";
import { t } from "@/lib/i18n";
import { useAnswerPrompts } from "@/lib/queries";

import { AnswerPromptStages } from "./AnswerPromptStages";
import { isRetrievalPromptStage } from "./answer-prompt.logic";

/**
 * 検索方法の画面。回答の検索と生成の全体設定（検索・回答プロファイルでは上書きしない）と、検索に使うプロンプトを設定する。
 * 以前の検索モード・検索オプションは、回答エンジンを 1 つにしたときに削除した（#595）。
 */
export function RetrievalSettingsClient() {
  // 回答の設定のカードとプロンプトのカードは別々に読み、読めたものから順に出す（1 つの取得の失敗・再試行でほかの
  // カードを待たせない）。読み込み中の文言と経過時間は各カードの先頭に 1 か所ずつ出し、ページの先頭には重ねない
  // （1 つの領域に経過時間は 1 か所。#4649）。
  const prompts = useAnswerPrompts();
  return (
    <PageBody wide>
      <AnsweringSettingsCard />
      {prompts.isPending ? (
        <TimedLoadingState
          label={t("settings.answerPrompts.loading")}
          operationKey="settings-retrieval-prompts-load"
          testId="settings-retrieval-prompts-loading"
        >
          <FormSkeleton />
        </TimedLoadingState>
      ) : prompts.isError || !prompts.data ? (
        <ErrorState message={t("settings.answerPrompts.loadError")} onRetry={() => void prompts.refetch()} />
      ) : (
        <AnswerPromptStages
          stages={prompts.data.stages.filter(isRetrievalPromptStage)}
          titleKey="settings.retrieval.prompts.title"
          descriptionKey="settings.retrieval.prompts.description"
        />
      )}
    </PageBody>
  );
}
