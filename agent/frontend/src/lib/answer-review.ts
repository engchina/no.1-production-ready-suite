import type { Artifact } from "@/lib/api";

/**
 * 回答の確かめ（#1286）。Run の成果物の「支援タスクの状態」（kind=`support_task`。#1243）と
 * 「回答の検証」（kind=`answer_validation`。#1246 / #1277）を、業務の利用者が読める形にする。
 *
 * 成果物の内容（JSON）の項目名・内部の語（`budget_exceeded`・`no_rag_evidence`・条件の出所の値など）は
 * 画面に出さない（handoff §13）。ここは値を取り出して分類するだけで、文言は画面が i18n で決める。
 * 内容は backend の `support_task.build_support_task` と `answer_validation.validation_content` /
 * `combine_validations` が作る形。読めない項目は無いものとして扱う（古い版・壊れた内容で画面を止めない）。
 */

export const SUPPORT_TASK_KIND = "support_task";
export const ANSWER_VALIDATION_KIND = "answer_validation";
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
  question: string;
  options: string[];
}

export interface ReviewGuide {
  title: string;
  /** 業務ガイドの版。分からなければ null。 */
  revision: number | null;
}

/**
 * 資料で確かめた結果。
 * - verified: 資料で確かめた（回答はそのまま）
 * - withheld: 確かめられない点がある（段落を外した・本文を載せていない・不足を示した）
 * - unvalidated: 確かめられなかった（照合の失敗・資料を使わなかった回答）
 * - skipped: 照合の対象外（資料を使わない業務 Agent・空の回答）
 */
export type ValidationState = "verified" | "withheld" | "unvalidated" | "skipped";

/** 確かめられなかった理由（画面の文言を選ぶため。内部の reason の値そのものは出さない）。 */
export type ValidationCause = "noEvidence" | "emptyAnswer" | "clarification" | "failure" | null;

/** 根拠で確かめられなかった段落の判定（RAG の `rag_validate_answer` の claim の status）。 */
export type UnverifiedClaimKind = "contradicted" | "unsupported" | "citation_error" | "unassessed";

export type UnverifiedPoint =
  | { kind: "claim"; claim: UnverifiedClaimKind; quote: string; reason: string }
  | { kind: "finding"; message: string }
  | { kind: "stale"; count: number }
  | { kind: "missing"; count: number };

export interface ReviewValidation {
  state: ValidationState;
  cause: ValidationCause;
  /** 回答から外した段落の数。 */
  withheldClaims: number;
  /** 回答の本文をすべて載せていないか。 */
  withheldAll: boolean;
  points: UnverifiedPoint[];
}

export interface AnswerReview {
  conditions: ReviewCondition[];
  clarifications: ReviewClarification[];
  guide: ReviewGuide | null;
  gaps: string[];
  /** この回答で、資料を調べる回数の上限に達して調べなかった呼び出しがあったか。 */
  limitReached: boolean;
  validation: ReviewValidation | null;
  /** 回答の対応（出すものだけ。対応の無い古い Run・「答えた」は null）。 */
  outcome: ReviewOutcome | null;
}

const BLOCKING_CLAIMS = new Set<UnverifiedClaimKind>(["contradicted", "unsupported", "citation_error", "unassessed"]);
const MAX_LISTED = 10;

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
    return [{ key: `${text(item.condition_id)}:${index}`, question, options }];
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

function cause(reason: unknown): ValidationCause {
  if (reason === "no_rag_evidence") return "noEvidence";
  if (reason === "empty_answer") return "emptyAnswer";
  if (reason === "clarification_only") return "clarification";
  return reason ? "failure" : null;
}

function errorFindings(result: Record<string, unknown>): Record<string, unknown>[] {
  return records(result.findings).filter((item) => item.severity === "error");
}

/**
 * 回答から外す段落の判定（確かめられない段落）。主張ではない段落（見出し・出典の行・利用者への質問・
 * 資料に記載が無いことを述べる文。backend が `non_claim` を付ける。#1306）は除く。
 */
function blockingClaims(result: Record<string, unknown>): Record<string, unknown>[] {
  return records(result.claims).filter(
    (claim) => BLOCKING_CLAIMS.has(claim.status as UnverifiedClaimKind) && !claim.non_claim,
  );
}

/** 確かめられない点が無く、回答をそのまま載せたか（backend の `publish_answer` と同じ判定）。 */
function publishedAsIs(result: Record<string, unknown>): boolean {
  if (result.valid === true) return true;
  if (errorFindings(result).length > 0) return false;
  if (result.status === "no_claims") return true;
  if (result.status !== "completed") return false;
  if (records(result.stale_evidence).length > 0 || records(result.missing_evidence).length > 0) return false;
  const claims = records(result.claims);
  if (blockingClaims(result).length > 0) return false;
  return claims.some((claim) => claim.status === "supported" || Boolean(claim.non_claim));
}

function unverifiedPoints(result: Record<string, unknown>): UnverifiedPoint[] {
  const points: UnverifiedPoint[] = [];
  for (const claim of blockingClaims(result)) {
    const status = claim.status as UnverifiedClaimKind;
    points.push({ kind: "claim", claim: status, quote: text(claim.answer_quote), reason: text(claim.reason) });
  }
  for (const finding of errorFindings(result)) {
    const message = text(finding.message);
    if (message) points.push({ kind: "finding", message });
  }
  const stale = records(result.stale_evidence).length;
  if (stale) points.push({ kind: "stale", count: stale });
  const missing = records(result.missing_evidence).length;
  if (missing) points.push({ kind: "missing", count: missing });
  return points.slice(0, MAX_LISTED);
}

/** 「回答の検証」の成果物を、資料で確かめた結果にする（backend の `publish_answer` と同じ判定）。 */
export function validationReview(content: Record<string, unknown>): ReviewValidation | null {
  const status = content.status;
  const reasonCause = cause(content.reason);
  if (status === "skipped") {
    return { state: "skipped", cause: reasonCause, withheldClaims: 0, withheldAll: false, points: [] };
  }
  if (status === "unvalidated") {
    return { state: "unvalidated", cause: reasonCause ?? "failure", withheldClaims: 0, withheldAll: false, points: [] };
  }
  if (status !== "completed") return null;
  const result = record(content.result) ?? {};
  if (publishedAsIs(result)) {
    return { state: "verified", cause: null, withheldClaims: 0, withheldAll: false, points: [] };
  }
  const withheld = record(content.withheld);
  return {
    state: "withheld",
    cause: null,
    withheldClaims: count(withheld?.claims),
    withheldAll: withheld?.all === true,
    points: unverifiedPoints(result),
  };
}

/**
 * 成果物 `answer` の内容から、画面に出す回答の対応を取り出す。対応の無い古い Run・知らない値・「答えた」は null
 * （内部の値をそのまま画面に出さない）。
 */
export function answerOutcome(content: Record<string, unknown> | null): ReviewOutcome | null {
  const value = record(content?.outcome)?.value;
  return typeof value === "string" && REVIEW_OUTCOMES.has(value) ? (value as ReviewOutcome) : null;
}

/** Run の成果物から、回答の確かめを作る。出すものが何も無ければ null。 */
export function answerReview(artifacts: Artifact[]): AnswerReview | null {
  const state = latest(artifacts, SUPPORT_TASK_KIND);
  const validationContent = latest(artifacts, ANSWER_VALIDATION_KIND);
  const outcome = answerOutcome(latest(artifacts, ANSWER_KIND));
  const review: AnswerReview = {
    conditions: state ? conditions(state) : [],
    clarifications: state ? clarifications(state) : [],
    guide: state ? guide(state) : null,
    gaps: state && Array.isArray(state.gaps) ? state.gaps.map(text).filter(Boolean) : [],
    limitReached: state ? limitReached(state) : false,
    validation: validationContent ? validationReview(validationContent) : null,
    outcome,
  };
  const empty =
    review.conditions.length === 0 &&
    review.clarifications.length === 0 &&
    review.guide === null &&
    review.gaps.length === 0 &&
    !review.limitReached &&
    review.validation === null &&
    review.outcome === null;
  return empty ? null : review;
}
