"use client";

import { PageBody, TimedLoadingState } from "@engchina/production-ready-ui";

import { AnswerRecordRetentionCard } from "@/components/settings/AnswerRecordRetentionCard";
import { AnsweringSettingsCard } from "@/components/settings/AnsweringSettingsCard";
import { QueryHistorySettingsCard } from "@/components/settings/QueryHistorySettingsCard";
import { t } from "@/lib/i18n";
import {
  useAnswerRecordSettings,
  useAnsweringSettings,
  useQueryHistorySettings,
} from "@/lib/queries";

/**
 * 検索方法の画面。回答の検索と生成の全体既定（業務ビューで上書きできる）と、回答の記録・質問履歴を設定する。
 * 以前の検索モード・検索オプションは、回答エンジンを 1 つにしたときに削除した（#595）。
 */
export function RetrievalSettingsClient() {
  // 3 つのカードの初回の読み込みの経過時間は、ページの先頭の 1 か所だけに出す。カードは読み込み中も
  // 形の Skeleton で寸法を保ち、読めたものから順に出す（1 つの取得の失敗・再試行でほかのカードを待たせない）。
  const answering = useAnsweringSettings();
  const answerRecords = useAnswerRecordSettings();
  const queryHistory = useQueryHistorySettings();
  const loading = answering.isPending || answerRecords.isPending || queryHistory.isPending;
  return (
    <PageBody wide>
      {loading ? (
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-retrieval-load"
          placement="page"
          testId="settings-retrieval-loading"
        />
      ) : null}
      <AnsweringSettingsCard />
      <AnswerRecordRetentionCard />
      <QueryHistorySettingsCard />
    </PageBody>
  );
}
