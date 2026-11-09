import type { Artifact, ClarificationKind, RunState } from "@/lib/api";

/**
 * 回答の確認事項（#1286・#2361）。Run の成果物の「支援タスクの状態」（kind=`support_task`）と回答の対応を、
 * 業務の利用者が読める形にする。
 *
 * 成果物の内容（JSON）の項目名・内部の語（`budget_exceeded`・条件の id など）は画面に出さない
 * （handoff §13）。ここは値を取り出して分類するだけで、文言は i18n に置く。内容は backend の
 * `support_task.build_support_task` が作る形。読めない項目は無いものとして扱う。
 */

export const SUPPORT_TASK_KIND = "support_task";
export const ANSWER_KIND = "answer";

/**
 * 回答の対応（#1305 / #1314）。成果物 `answer` の `outcome.value`（RAG の AnswerEnvelope の `outcome` と同じ語彙）の
 * うち、画面にバッジで出すもの。「答えた（answered）」は RAG の回答の詳細（#1252）と同じく出さない。
 */
export type ReviewOutcome =
  | "conditional"
  | "needs_clarification"
  | "needs_environment_data"
  | "needs_human"
  | "insufficient_evidence";

const REVIEW_OUTCOMES = new Set<string>([
  "conditional",
  "needs_clarification",
  "needs_environment_data",
  "needs_human",
  "insufficient_evidence",
]);

/** 条件の出所（`user_answer` = 利用者の答え、`rag_guide` = RAG が質問の文から読んだ）。 */
export type ConditionSource = "user" | "question" | null;

export interface ReviewCondition {
  key: string;
  /** 条件の名前（業務ガイドのラベル。無ければ条件の id）。 */
  label: string;
  value: string;
  source: ConditionSource;
  /** 上書きする前の値（最も新しいもの）。無ければ null。 */
  previousValue: string | null;
}

export interface ReviewClarification {
  key: string;
  /** 確認の種類（NL2SQL の SQL の生成前の確認・RAG の確認。種類の無い古い問いは RAG。#1441）。 */
  kind: ClarificationKind;
  question: string;
  options: string[];
}

export interface ReviewGuide {
  title: string;
  /** 業務ガイドの版。分からなければ null。 */
  revision: number | null;
}

export interface AnswerReview {
  conditions: ReviewCondition[];
  clarifications: ReviewClarification[];
  guide: ReviewGuide | null;
  gaps: string[];
  /** この回答で、資料を調べる回数の上限に達して調べなかった呼び出しがあったか。 */
  limitReached: boolean;
  /** 回答の対応（出すものだけ。対応の無い古い Run・「答えた」は null）。 */
  outcome: ReviewOutcome | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(record).filter((item): item is Record<string, unknown> => item !== null) : [];
}

function text(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return typeof value === "string" ? value.split(/\s+/).filter(Boolean).join(" ") : "";
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function conditionSource(value: unknown): ConditionSource {
  if (value === "user_answer") return "user";
  if (value === "rag_guide") return "question";
  return null;
}

/** 最も新しい成果物（承認の後の再開で上書きされる成果物は 1 つだが、念のため最後のものを使う）。 */
function latest(artifacts: Artifact[], kind: string): Record<string, unknown> | null {
  for (const artifact of [...artifacts].reverse()) {
    if (artifact.kind === kind) return record(artifact.content);
  }
  return null;
}

function conditions(state: Record<string, unknown>): ReviewCondition[] {
  const known = record(state.known_conditions);
  if (!known) return [];
  return Object.entries(known).flatMap(([id, raw]) => {
    const entry = record(raw);
    const value = text(entry?.value);
    if (!entry || !value) return [];
    const previous = records(entry.previous).map((item) => text(item.value)).find(Boolean) ?? null;
    return [
      {
        key: id,
        label: text(entry.label) || text(id),
        value,
        source: conditionSource(entry.source),
        previousValue: previous && previous !== value ? previous : null,
      },
    ];
  });
}

function clarifications(state: Record<string, unknown>): ReviewClarification[] {
  return records(state.pending_clarifications).flatMap((item, index) => {
    const question = text(item.question);
    if (!question) return [];
    const options = Array.isArray(item.options) ? item.options.map(text).filter(Boolean) : [];
    const kind: ClarificationKind = item.kind === "nl2sql" ? "nl2sql" : "rag";
    return [{ key: `${kind}:${text(item.condition_id)}:${index}`, kind, question, options }];
  });
}

function guide(state: Record<string, unknown>): ReviewGuide | null {
  const value = record(state.guide);
  if (!value || !text(value.guide_id)) return null;
  const revision = count(value.revision);
  return { title: text(value.title), revision: revision > 0 ? revision : null };
}

function limitReached(state: Record<string, unknown>): boolean {
  const budget = record(state.budget);
  return count(record(budget?.run)?.budget_exceeded) > 0;
}

/**
 * 成果物 `answer` の内容から、画面に出す回答の対応を取り出す。対応の無い古い Run・知らない値・「答えた」は null
 * （内部の値をそのまま画面に出さない）。
 */
export function answerOutcome(content: Record<string, unknown> | null): ReviewOutcome | null {
  const value = record(content?.outcome)?.value;
  return typeof value === "string" && REVIEW_OUTCOMES.has(value) ? (value as ReviewOutcome) : null;
}

const CLARIFICATION_KINDS: readonly ClarificationKind[] = ["nl2sql", "rag"];

/**
 * Run が利用者の答えを待っている確認の種類（#1441・#1445。支援タスクの状態の確かめ中の問いから）。
 * 取り消しの操作（「データベースの検索は不要」「資料の検索は不要」）を出す種類。
 */
export function pendingClarificationKinds(artifacts: Artifact[]): ClarificationKind[] {
  const state = latest(artifacts, SUPPORT_TASK_KIND);
  if (!state) return [];
  const kinds = new Set(clarifications(state).map((item) => item.kind));
  return CLARIFICATION_KINDS.filter((kind) => kinds.has(kind));
}

/** Run で利用者が取り消した確認の種類（Run の記録 `metadata.clarification_cancel`。#1441・#1445）。 */
export function cancelledClarificationKinds(run: Pick<RunState, "metadata">): ClarificationKind[] {
  const kinds = new Set(
    records(run.metadata?.clarification_cancel).map((item) => item.kind).filter((kind) => typeof kind === "string")
  );
  return CLARIFICATION_KINDS.filter((kind) => kinds.has(kind));
}

/** Run の成果物から、回答の確認事項の表示を作る。出すものが何も無ければ null。 */
export function answerReview(artifacts: Artifact[]): AnswerReview | null {
  const state = latest(artifacts, SUPPORT_TASK_KIND);
  const outcome = answerOutcome(latest(artifacts, ANSWER_KIND));
  const review: AnswerReview = {
    conditions: state ? conditions(state) : [],
    clarifications: state ? clarifications(state) : [],
    guide: state ? guide(state) : null,
    gaps: state && Array.isArray(state.gaps) ? state.gaps.map(text).filter(Boolean) : [],
    limitReached: state ? limitReached(state) : false,
    outcome,
  };
  const empty =
    review.conditions.length === 0 &&
    review.clarifications.length === 0 &&
    review.guide === null &&
    review.gaps.length === 0 &&
    !review.limitReached &&
    review.outcome === null;
  return empty ? null : review;
}
