// 実行履歴（#1955）の一覧の取得条件と、行 → 共通の実行履歴の形への写し。画面の部品（`RunHistoryTable` など）は
// `@production-ready/ui` が持ち、ここは NL2SQL の行をどう写すかだけを書く（UX 契約 run-history.md「状態の写し方」）。
//
// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import {
  runPeriodSince,
  RUN_EXECUTOR_FILTERS,
  RUN_PERIODS,
  type RunExecutorFilter,
  type RunPeriod,
  type RunStatus,
  type RunStatusFilter,
  type RunTimelineStepStatus,
} from "@production-ready/ui";

import { t } from "../../lib/i18n";
import { chatStageLabel } from "./chatProgress";
import type { EngineTiming, HistoryItem } from "./types";

/** NL2SQL が写せる共通の状態（完了 = 安全性の判定を通過、ブロック = 実行を止めた）。失敗は記録しない。 */
export const HISTORY_RUN_STATUSES: readonly RunStatus[] = ["succeeded", "blocked"] as const;

/** 種類（チャットの往復 / SQL 生成）。`all` は「すべて」。 */
export type HistoryKindFilter = "all" | "chat" | "query";
export const HISTORY_KIND_FILTERS: readonly HistoryKindFilter[] = ["all", "chat", "query"] as const;

/** 一覧の絞り込み（作業状態に残す）。値の読み直しは `normalizeHistoryFilters` を通す。 */
export interface HistoryFilters {
  search: string;
  status: RunStatusFilter;
  kind: HistoryKindFilter;
  period: RunPeriod;
  executor: RunExecutorFilter;
}

export const DEFAULT_HISTORY_FILTERS: HistoryFilters = {
  search: "",
  status: "all",
  kind: "all",
  period: "all",
  executor: "all",
};

/** 保存していた値が壊れていても（古い版の `mine` など）、既定の「すべて」として読む。 */
export function normalizeHistoryFilters(value: unknown): HistoryFilters {
  const record = value && typeof value === "object" ? (value as Partial<Record<keyof HistoryFilters, unknown>>) : {};
  const pick = <T extends string>(raw: unknown, allowed: readonly T[], fallback: T): T =>
    typeof raw === "string" && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
  return {
    search: typeof record.search === "string" ? record.search : "",
    status: pick<RunStatusFilter>(record.status, ["all", ...HISTORY_RUN_STATUSES], "all"),
    kind: pick(record.kind, HISTORY_KIND_FILTERS, "all"),
    period: pick(record.period, RUN_PERIODS, "all"),
    executor: pick(record.executor, RUN_EXECUTOR_FILTERS, "all"),
  };
}

/** 検索語か絞り込みのどれかが既定と違う（「条件をクリア」・「N 件（全 M 件）」）。 */
export function hasActiveHistoryFilters(filters: HistoryFilters) {
  return (
    Boolean(filters.search.trim()) ||
    filters.status !== "all" ||
    filters.kind !== "all" ||
    filters.period !== "all" ||
    filters.executor !== "all"
  );
}

/** backend の「実行者」の値（`mine` / `others`。#1954）。 */
const EXECUTOR_PARAM: Record<RunExecutorFilter, string | null> = { all: null, self: "mine", others: "others" };

/**
 * `GET /api/nl2sql/history` の URL。1 ページは表示件数（既定 25 件）、新しい順（サーバーが全件で並べる）。
 * 期間は今の時刻から `since`（ISO 8601）に直して渡す（`runPeriodSince`）。
 */
export function historyRequestUrl(
  { offset, limit, filters }: { offset: number; limit: number; filters: HistoryFilters },
  now: Date = new Date()
) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const search = filters.search.trim();
  if (search) params.set("q", search);
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.kind !== "all") params.set("kind", filters.kind);
  const since = runPeriodSince(filters.period, now);
  if (since) params.set("since", since);
  const executor = EXECUTOR_PARAM[filters.executor];
  if (executor) params.set("executor", executor);
  return `/api/nl2sql/history?${params.toString()}`;
}

/** 行の状態（共通の 7 つのうち NL2SQL が写せる 2 つ）。 */
export function historyRunStatus(item: HistoryItem): RunStatus {
  return item.safety_is_safe ? "succeeded" : "blocked";
}

/** 行の種類。古い応答（`kind` の無い行）は会話の持ち主の有無で決める（backend と同じ）。 */
export function historyKind(item: HistoryItem): "chat" | "query" {
  if (item.kind === "chat" || item.kind === "query") return item.kind;
  return item.conversation_owner_display_name != null ? "chat" : "query";
}

export function historyKindLabel(kind: "chat" | "query") {
  return kind === "chat" ? t("history.kind.chat") : t("history.kind.query");
}

/** 実行した人の表示名（空ならログイン ID）。UUID は出さず、引けない利用者は削除済みの文言（#1696・#1954）。 */
export function historyExecutorName(item: HistoryItem) {
  if (!item.actor_user_uuid?.trim()) return t("history.actor.unrecorded");
  return (
    item.executor_display_name?.trim() ||
    item.actor_display_name?.trim() ||
    item.actor_login_user_id?.trim() ||
    t("history.actor.missing")
  );
}

/** 実行者（`executed_by_me` の無い古い応答は自分の行とみなす）。 */
export function historyExecutor(item: HistoryItem) {
  return { name: historyExecutorName(item), isSelf: item.executed_by_me !== false };
}

/**
 * 会話の持ち主（実行者と違うときだけ。#1954）。会話の無い実行（SQL 生成）は null。
 * 自分が実行して自分が持ち主の行と、表示名が実行者と同じ行は出さない。
 */
export function historyOwner(item: HistoryItem) {
  if (item.conversation_owner_display_name == null) return null;
  const executor = historyExecutor(item);
  const ownerIsSelf = item.owned_by_me === true;
  if (executor.isSelf && ownerIsSelf) return null;
  const name = item.conversation_owner_display_name || t("history.actor.missing");
  if (!executor.isSelf && !ownerIsSelf && name === executor.name) return null;
  return { name, isSelf: ownerIsSelf };
}

function engineTimingLabel(engine: string) {
  if (engine === "deterministic") return t("history.timing.engine.deterministic");
  if (engine === "select_ai_agent") return t("nl2sql.engine.select_ai_agent");
  if (engine === "select_ai") return t("nl2sql.engine.select_ai");
  if (engine === "enterprise_ai_direct") return t("nl2sql.engine.enterprise_ai_direct");
  return engine || t("history.timing.engine.unknown");
}

const ENGINE_STEP_STATUS: Record<EngineTiming["status"], RunTimelineStepStatus> = {
  success: "done",
  failed: "failed",
  skipped: "skipped",
};

/** 処理の経過の 1 段（画面が `RunTimelineStep` にし、中身の `children` を足す）。 */
export interface HistoryTimelineStep {
  id: string;
  label: string;
  status: RunTimelineStepStatus;
  durationMs: number;
  /**
   * 処理の始まりからの位置（ミリ秒）。記録は段ごとの所要時間だけなので、段は順に積み、生成の方式の試行は
   * SQL の生成の段の始まりから積む（時間の線で試行が親の段の中に重なり、後の段の位置をずらさない）。
   */
  offsetMs: number;
  level: 0 | 1;
  /** 生成の方式の試行の失敗の文（開いて読む）。 */
  error?: string;
}

/**
 * 処理の経過（`stage_timings` の記録の順）。段の名前はチャットの処理の経過と同じ i18n（`chatStageLabel`）。
 * ブロックした行は安全性の確認を失敗にする。SQL の生成の下に、生成の方式の試行（`engine_timings`）を字下げで並べる。
 * 未知の段は飛ばす（段の id を画面に出さない）。
 */
export function historyTimelineSteps(item: HistoryItem): HistoryTimelineStep[] {
  const steps: HistoryTimelineStep[] = [];
  const blocked = !item.safety_is_safe;
  const engines = item.engine_timings ?? [];
  let cursor = 0;
  for (const [index, timing] of (item.stage_timings ?? []).entries()) {
    const start = cursor;
    cursor += Math.max(0, timing.elapsed_ms);
    const failed = blocked && timing.stage === "safety_check";
    const label = chatStageLabel(timing.stage, failed);
    if (!label) continue;
    steps.push({
      id: `${timing.stage}-${index}`,
      label,
      status: failed ? "failed" : "done",
      durationMs: timing.elapsed_ms,
      offsetMs: start,
      level: 0,
    });
    if (timing.stage === "generate_sql") {
      let attemptStart = start;
      for (const [engineIndex, attempt] of engines.entries()) {
        steps.push({
          id: `engine-${engineIndex}`,
          label: t("history.timeline.engineAttempt", { engine: engineTimingLabel(attempt.engine) }),
          status: ENGINE_STEP_STATUS[attempt.status] ?? "done",
          durationMs: attempt.elapsed_ms,
          offsetMs: attemptStart,
          level: 1,
          error: attempt.error?.trim() || undefined,
        });
        attemptStart += Math.max(0, attempt.elapsed_ms);
      }
    }
  }
  return steps;
}

/** 詳細の URL（`?id=<run_id>`。新しいタブでも開ける）。 */
export function historyDetailPath(basePath: string, id: string) {
  return `${basePath}?${new URLSearchParams({ id }).toString()}`;
}
