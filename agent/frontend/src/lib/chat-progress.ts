/**
 * チャットの回答の処理の段階（3 製品共通の `ChatProgress`。#1145 / #1147）の、Agent の段階の定義（#1359）。
 *
 * 段階は backend が Run の状態から記録したイベント（`RunState.progress_events`。3 製品共通の契約）で届き、
 * 共通の `useChatProgressStream` が段階の一覧にまとめる。ここは段階の種類（`kind`）→ i18n の名前だけを持つ。
 * 並びは backend の記録の順（最初に出た順）: 進め方の検討（`plan`）→ ツールの呼び出し（`tool:<名前>[#n]`）→
 * 承認待ち（`approval_wait[#n]`。承認を求めた回ごと）→ 回答の作成（`respond[#n]`）。回答の作成の後に別のツールを
 * 呼んだら、backend がその段階を `plan`（進め方の検討の完了）に変える。
 */

import { chatProgressLabelState, type ChatProgressStep, type ChatProgressStepDefinitions } from "@production-ready/ui";

import { t } from "./i18n";

/** 送った質問（Run の作成の応答の前。#907）の段階。送信が遅いとき、回答の作成ではなく送信で待っていると分かる。 */
export function chatSubmitProgressSteps(sentAtMs: number): ChatProgressStep[] {
  return [
    { id: "submit", label: t("chat.progress.submit.running"), status: "running", startedAt: new Date(sentAtMs).toISOString() },
  ];
}

function toolName(params: Readonly<Record<string, string | number | boolean>>): string {
  return typeof params.tool === "string" ? params.tool : "";
}

/**
 * 段階の定義（実行中「〜しています」・完了「〜しました」・失敗「〜できませんでした」・未実行は名詞。NL2SQL と同じ。
 * #1145）。i18n の key は静的に書く（辞書の検査が key を見つけられるように）。
 */
export const AGENT_CHAT_PROGRESS_STEPS: ChatProgressStepDefinitions = {
  plan: {
    label: (status) =>
      ({
        running: t("chat.progress.plan.running"),
        done: t("chat.progress.plan.done"),
        failed: t("chat.progress.plan.failed"),
        idle: t("chat.progress.plan.idle"),
      })[chatProgressLabelState(status)],
  },
  tool: {
    label: (status, params) => {
      const tool = toolName(params);
      return {
        running: t("chat.progress.tool.running", { tool }),
        done: t("chat.progress.tool.done", { tool }),
        failed: t("chat.progress.tool.failed", { tool }),
        idle: t("chat.progress.tool.idle", { tool }),
      }[chatProgressLabelState(status)];
    },
    // 却下したツールは実行しない（スキップ）。理由を補足に出す。
    detail: (status, params) => (status === "skipped" && params.rejected === true ? t("chat.progress.toolRejected") : undefined),
  },
  approval: {
    label: (status) =>
      ({
        running: t("chat.progress.approval.running"),
        done: t("chat.progress.approval.done"),
        failed: t("chat.progress.approval.failed"),
        idle: t("chat.progress.approval.idle"),
      })[chatProgressLabelState(status)],
    // 承認を待っている間は、承認を待つツールの名前を補足に出す。
    detail: (status, params) => (status === "running" && typeof params.tools === "string" && params.tools ? params.tools : undefined),
  },
  respond: {
    label: (status) =>
      ({
        running: t("chat.progress.respond.running"),
        done: t("chat.progress.respond.done"),
        failed: t("chat.progress.respond.failed"),
        idle: t("chat.progress.respond.idle"),
      })[chatProgressLabelState(status)],
  },
};
