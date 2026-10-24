// 実行履歴（#1955）の一覧の取得条件と、行 → 共通の実行履歴の形への写し。画面の部品（`RunHistoryTable` など）は
// `@production-ready/ui` が持ち、ここは NL2SQL の行をどう写すかだけを書く（UX 契約 run-history.md「状態の写し方」）。
//
// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import {
  runPeriodSince,
  chatProgressStepsFromEvents,
  chatProgressTimelineStep,
  chatProgressTraceIdOf,
  parseChatProgressEvents,
  RUN_EXECUTOR_FILTERS,
  RUN_PERIODS,
  type RunExecutorFilter,
  type RunPeriod,
  type RunStatus,
  type RunStatusFilter,
  type RunTimelineStep,
  type TraceDetailsLabels,
} from "@production-ready/ui";

import { t } from "../../lib/i18n";
import { APP_ROUTES } from "../../lib/routes";
import { CHAT_PROGRESS_STEP_DEFINITIONS, chatProgressIsLegacy } from "./chatProgress";
import type { HistoryItem } from "./types";

/** NL2SQL が写せる共通の状態（完了 = 安全性の判定を通過、ブロック = 実行を止めた）。失敗は記録しない。 */
export const HISTORY_RUN_STATUSES: readonly RunStatus[] = ["succeeded", "blocked"] as const;

/** 行の種類（チャットの往復 / SQL 生成 / MCP / 品質評価。backend の `HistoryItem.kind`。#1986）。 */
export type HistoryKind = "chat" | "query" | "mcp" | "evaluation";
/** 一つずつ選ぶ種類の並び（Agent の「画面 → … → MCP → 品質評価」と同じく品質評価を最後に）。 */
export const HISTORY_KINDS: readonly HistoryKind[] = ["chat", "query", "mcp", "evaluation"] as const;

/**
 * 種類の絞り込みの既定。Agent の品質評価の Run が MCP で呼んだ実行（評価のケースごとの行）は利用者の実行を
 * 埋もれさせるので既定では隠し、「すべて」か「品質評価」を選んだときに出す（Agent の実行履歴と同じ。#1986）。
 */
export const DEFAULT_HISTORY_KIND = "not_evaluation";

/** 種類の絞り込み。`all` は「すべて」、既定は `not_evaluation`（品質評価以外）。 */
export type HistoryKindFilter = "all" | typeof DEFAULT_HISTORY_KIND | HistoryKind;
export const HISTORY_KIND_FILTERS: readonly HistoryKindFilter[] = [DEFAULT_HISTORY_KIND, "all", ...HISTORY_KINDS] as const;

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
  kind: DEFAULT_HISTORY_KIND,
  period: "all",
  executor: "all",
};

/** 保存していた値が壊れていても（古い版の `mine` など）、既定（種類は「品質評価以外」、ほかは「すべて」）として読む。 */
export function normalizeHistoryFilters(value: unknown): HistoryFilters {
  const record = value && typeof value === "object" ? (value as Partial<Record<keyof HistoryFilters, unknown>>) : {};
  const pick = <T extends string>(raw: unknown, allowed: readonly T[], fallback: T): T =>
    typeof raw === "string" && (allowed as readonly string[]).includes(raw) ? (raw as T) : fallback;
  return {
    search: typeof record.search === "string" ? record.search : "",
    status: pick<RunStatusFilter>(record.status, ["all", ...HISTORY_RUN_STATUSES], "all"),
    kind: pick(record.kind, HISTORY_KIND_FILTERS, DEFAULT_HISTORY_KIND),
    period: pick(record.period, RUN_PERIODS, "all"),
    executor: pick(record.executor, RUN_EXECUTOR_FILTERS, "all"),
  };
}

/** 検索語か絞り込みのどれかが既定と違う（「条件をクリア」・「N 件（全 M 件）」）。 */
export function hasActiveHistoryFilters(filters: HistoryFilters) {
  return (
    Boolean(filters.search.trim()) ||
    filters.status !== "all" ||
    filters.kind !== DEFAULT_HISTORY_KIND ||
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

/** 行の種類。古い応答（`kind` の無い行）はきっかけと会話の持ち主の有無で決める（backend と同じ）。 */
export function historyKind(item: HistoryItem): HistoryKind {
  if (item.kind && (HISTORY_KINDS as readonly string[]).includes(item.kind)) return item.kind;
  if (item.run_source === "mcp" || item.run_source === "evaluation") return item.run_source;
  return item.conversation_owner_display_name != null ? "chat" : "query";
}

/** 種類の名前（絞り込みの選択肢）。 */
export function historyKindLabel(kind: HistoryKind) {
  return t(`history.kind.${kind}`);
}

/**
 * 行の種類の表示。MCP は呼び出し元を添える（外部の MCP クライアントは API キーの名前、Agent の Run は「Agent」。
 * Agent の実行履歴の「MCP（〈キーの名前〉）」と同じ形。#1986）。
 */
export function historyKindDisplay(item: HistoryItem) {
  const kind = historyKind(item);
  if (kind !== "mcp") return historyKindLabel(kind);
  const keyName = item.api_key_name?.trim();
  if (keyName) return t("history.kind.mcpCaller", { name: keyName });
  if (item.agent_run_id?.trim()) return t("history.kind.mcpCaller", { name: t("history.kind.agent") });
  return historyKindLabel(kind);
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

/** 実行の詳細の処理の経過（#1955・#3226）。 */
export interface HistoryTimeline {
  steps: RunTimelineStep[];
  traceId?: string;
  /** span の無い古い形式の記録（処理の経過の位置に古い形式の案内を出す）。 */
  legacy: boolean;
}

/**
 * 実行の詳細の処理の経過: 実行のときに backend が記録した span の木（チャット・SQL 生成と同じ記録・工程の定義）。
 * span の無い古い形式の記録（段だけのイベント・工程の秒だけの記録）は再構成せず、古い形式として扱う（#3226）。
 */
export function historyTimeline(item: HistoryItem, traceLabels?: TraceDetailsLabels): HistoryTimeline {
  const events = parseChatProgressEvents(item.progress_events);
  const steps = chatProgressStepsFromEvents(events, CHAT_PROGRESS_STEP_DEFINITIONS, undefined, { traceLabels }).map(
    chatProgressTimelineStep
  );
  const traceId = chatProgressTraceIdOf(events);
  const legacy = chatProgressIsLegacy(events);
  return { steps: legacy ? [] : steps, ...(traceId ? { traceId } : {}), legacy };
}

/** 詳細の URL（`?id=<run_id>`。新しいタブでも開ける）。 */
export function historyDetailPath(basePath: string, id: string) {
  return `${basePath}?${new URLSearchParams({ id }).toString()}`;
}

/**
 * 実行の詳細の「会話を開く」の URL（3 製品で同じ規則。#2343）。チャットの往復（`kind` が chat）で会話の ID があり、
 * 会話を見られる人（system_admin か会話の持ち主）がチャットを開けるときだけ。SQL 生成・MCP・品質評価の実行は
 * 開く元の会話が無いので null（操作を出さない）。開いた会話は backend が見え方を確かめる（見えなければ 404）。
 */
export function historyConversationHref(
  item: HistoryItem,
  { seesAllUsers, canOpenChat }: { seesAllUsers: boolean; canOpenChat: boolean }
): string | null {
  if (!canOpenChat || historyKind(item) !== "chat" || !item.session_id) return null;
  if (!seesAllUsers && item.owned_by_me !== true) return null;
  return `${APP_ROUTES.chat}?${new URLSearchParams({ profile_id: item.profile_id, conversation_id: item.session_id }).toString()}`;
}
