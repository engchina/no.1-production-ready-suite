import { CheckCircle2, Circle, CirclePause, CirclePlay, MinusCircle, XCircle, type LucideIcon } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { CopyButton } from "../ui/copy-button";
import type { TraceDetailsLabels } from "../../lib/trace-labels";
import { useTraceDetailsLabels } from "./trace-labels-provider";
import { traceTimeline } from "../../lib/trace-tree";
import { operationTimestampMs } from "../../lib/operation-timing";
import { cn } from "../../lib/utils";
import type { ChatProgressStepStatus } from "../feedback/chat-progress";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../ui/card";
import { Disclosure } from "../ui/disclosure";
import { DisclosureChevron } from "../ui/disclosure-chevron";
import { TEXT_ICON_SIZE } from "../ui/text-icon";
import { Skeleton } from "../ui/skeleton";
import { Spinner } from "../ui/spinner";
import { formatRunDateTime, formatRunDuration, formatRunTokens } from "./run-history";
import { hasRunStepDetails, RunStepDetailsView, type RunStepDetails } from "./run-step-details";
import { DEFAULT_RUN_STATUS_LABELS, RunStatusBadge, type RunStatus, type RunStatusLabels } from "./run-status";

/*
 * 実行履歴の詳細（#1955）。3 製品の詳細を同じ順の節にそろえる:
 *   概要（状態・質問・日時・所要時間・実行者・会話の持ち主・対象・モデル・利用量）
 *   → 処理の経過（段・ツールの呼び出し・時間の線。回答の上。#2126）→ 回答 → 根拠 / SQL / 検証（製品の中身）
 *   → フィードバック → 技術的な詳細（畳む）
 * 製品は節の中身（RAG の根拠・NL2SQL の SQL・Agent のツールの結果）だけを持つ。規則は UX 契約 run-history.md「詳細」。
 */

// ---------------------------------------------------------------------------
// 文言

export interface RunDetailLabels {
  summary: string;
  question: string;
  answer: string;
  timeline: string;
  /** 処理の経過の節の中の「処理の詳細」（`RunProcessDetails` の見出し。#2325）。 */
  processDetails: string;
  facts: {
    startedAt: string;
    finishedAt: string;
    duration: string;
    kind: string;
    executor: string;
    owner: string;
    model: string;
    tokens: string;
    cost: string;
    runId: string;
  };
  /** 実行者・持ち主が自分のときの表記。 */
  self: string;
  notRecorded: string;
  /** 処理の経過の要約（「5 ステップ・合計 12.3 秒」）。 */
  timelineSummary: (count: number, duration: string) => string;
  /** 段の状態（アイコンに添える読み上げ・失敗とスキップは画面にも出す）。 */
  stepStatus: Record<RunTimelineStepStatus, string>;
  /** 段の詳細（`RunTimelineStep.details`）の長さを切ったときの印。 */
  stepDetailsTruncated: string;
  status: RunStatusLabels;
}

export const DEFAULT_RUN_DETAIL_LABELS: RunDetailLabels = {
  summary: "概要",
  question: "質問",
  answer: "回答",
  timeline: "処理の経過",
  processDetails: "処理の詳細",
  facts: {
    startedAt: "開始日時",
    finishedAt: "終了日時",
    duration: "所要時間",
    kind: "種類",
    executor: "実行者",
    owner: "会話の持ち主",
    model: "モデル",
    tokens: "token",
    cost: "費用",
    runId: "実行 ID",
  },
  self: "自分",
  notRecorded: "—",
  timelineSummary: (count, duration) => `${count} ステップ・合計 ${duration}`,
  stepStatus: {
    pending: "未実行",
    running: "実行中",
    waiting: "応答待ち",
    done: "完了",
    failed: "失敗",
    skipped: "スキップ",
  },
  stepDetailsTruncated: "長い内容は先頭だけを表示しています。",
  status: DEFAULT_RUN_STATUS_LABELS,
};

export type RunDetailLabelsInput = Partial<Omit<RunDetailLabels, "facts" | "stepStatus" | "status">> & {
  facts?: Partial<RunDetailLabels["facts"]>;
  stepStatus?: Partial<RunDetailLabels["stepStatus"]>;
  status?: Partial<RunStatusLabels>;
};

export function resolveRunDetailLabels(labels?: RunDetailLabelsInput): RunDetailLabels {
  return {
    ...DEFAULT_RUN_DETAIL_LABELS,
    ...labels,
    facts: { ...DEFAULT_RUN_DETAIL_LABELS.facts, ...labels?.facts },
    stepStatus: { ...DEFAULT_RUN_DETAIL_LABELS.stepStatus, ...labels?.stepStatus },
    status: { ...DEFAULT_RUN_DETAIL_LABELS.status, ...labels?.status },
  };
}

// ---------------------------------------------------------------------------
// 事実の一覧（概要・技術的な詳細）

export interface RunFact {
  id: string;
  /** 見出し（翻訳済み）。 */
  label: string;
  value: ReactNode;
  /** 識別子（実行 ID・モデル名）を等幅で出す。 */
  mono?: boolean;
  /** 長い値（対象の名前・エラーの要約など）を 1 行の全幅に置く。 */
  wide?: boolean;
}

/** 見出しと値の並び（`<dl>`）。置き場所の幅に合わせて段を増やす（375px は 1 段）。 */
export function RunFactList({ facts, className, testId }: { facts: readonly RunFact[]; className?: string; testId?: string }) {
  if (facts.length === 0) return null;
  return (
    <dl
      className={cn(
        "grid min-w-0 gap-x-6 gap-y-3 text-sm [grid-template-columns:repeat(auto-fill,minmax(min(100%,11rem),1fr))]",
        className
      )}
      data-testid={testId}
    >
      {facts.map((fact) => (
        <div key={fact.id} className={cn("min-w-0", fact.wide && "col-span-full")} data-run-fact={fact.id}>
          <dt className="text-xs text-fg-muted">{fact.label}</dt>
          <dd
            className={cn(
              "mt-0.5 break-words text-fg [overflow-wrap:anywhere]",
              fact.mono ? "font-mono text-xs leading-5" : "tnum"
            )}
          >
            {fact.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------------------
// 概要

export interface RunSummaryProps {
  /** 補助用途のバッジ表示。実行履歴の概要は既定で表示しない。 */
  showBadges?: boolean;
  status: RunStatus;
  /** 製品に固有の状態の文言（例: waiting →「承認待ち」）。 */
  statusLabel?: string;
  /** 状態の横に並べる製品のバッジ（RAG の回答の対応・検証、NL2SQL の安全性など）。`StatusBadge` を渡す。 */
  badges?: ReactNode;
  /** 質問（入力）。全文を出す（一覧は 2 行で切り詰めるため、ここで全文を読む）。 */
  question: ReactNode;
  startedAt: string;
  finishedAt?: string | null;
  durationMs?: number | null;
  kind?: string | null;
  /** 実行者（#1954）。 */
  executor?: { name: string; isSelf?: boolean } | null;
  /** 会話の持ち主（実行者と違うときだけ渡す。#1954）。 */
  owner?: { name: string; isSelf?: boolean } | null;
  /** 対象（業務 Agent・検索・回答プロファイル・業務プロファイル）。見出しは製品が渡す。 */
  target?: { label: string; value: ReactNode } | null;
  model?: string | null;
  usage?: { tokens?: number | null; cost?: string | null } | null;
  runId: string;
  /** 製品に固有の事実（NL2SQL の実行エンジン・行数など）。共通の事実の後、実行 ID の前に並べる。 */
  extraFacts?: readonly RunFact[];
  /** 対象への操作（`ObjectActionBar`。再実行・停止・フィードバックなど）。見出しの右に 1 か所。 */
  actions?: ReactNode;
  /** 失敗・ブロックの理由（danger / warning の `Banner`）。質問の上、見出しの直下に出す。 */
  alert?: ReactNode;
  labels?: RunDetailLabelsInput;
  formatDateTime?: (iso: string) => string;
  testId?: string;
}

/**
 * 詳細の先頭のカード「概要」（#1955）。状態 → 質問 → 事実の並び（開始日時・所要時間・種類・実行者・会話の持ち主・
 * 対象・モデル・token・費用・製品の事実・実行 ID）。記録の無い事実は並べない（「—」の並びにしない）。
 */
export function RunSummary({
  showBadges = false,
  status,
  statusLabel,
  badges,
  question,
  startedAt,
  finishedAt,
  durationMs,
  kind,
  executor,
  owner,
  target,
  model,
  usage,
  runId,
  extraFacts = [],
  actions,
  alert,
  labels: labelsInput,
  formatDateTime = (iso) => formatRunDateTime(iso, { seconds: true }),
  testId,
}: RunSummaryProps) {
  const labels = resolveRunDetailLabels(labelsInput);
  const person = (value: { name: string; isSelf?: boolean }) => (value.isSelf ? labels.self : value.name);
  const facts: RunFact[] = [
    { id: "startedAt", label: labels.facts.startedAt, value: <time dateTime={startedAt}>{formatDateTime(startedAt)}</time> },
  ];
  if (finishedAt) {
    facts.push({ id: "finishedAt", label: labels.facts.finishedAt, value: <time dateTime={finishedAt}>{formatDateTime(finishedAt)}</time> });
  }
  facts.push({ id: "duration", label: labels.facts.duration, value: formatRunDuration(durationMs, labels.notRecorded) });
  if (kind) facts.push({ id: "kind", label: labels.facts.kind, value: kind });
  if (executor) facts.push({ id: "executor", label: labels.facts.executor, value: person(executor) });
  if (owner) facts.push({ id: "owner", label: labels.facts.owner, value: person(owner) });
  if (target) facts.push({ id: "target", label: target.label, value: target.value });
  if (model) facts.push({ id: "model", label: labels.facts.model, value: model, mono: true });
  if (usage?.tokens != null) facts.push({ id: "tokens", label: labels.facts.tokens, value: formatRunTokens(usage.tokens) });
  if (usage?.cost) facts.push({ id: "cost", label: labels.facts.cost, value: usage.cost });
  facts.push(...extraFacts);
  facts.push({ id: "runId", label: labels.facts.runId, value: runId, mono: true });

  return (
    <Card className="min-w-0" data-run-summary="" data-testid={testId}>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2" data-run-summary-heading="">
          <CardTitle>{labels.summary}</CardTitle>
          {showBadges ? <><RunStatusBadge status={status} label={statusLabel} labels={labels.status} />{badges}</> : null}
        </div>
        {actions ? <div className="min-w-0">{actions}</div> : null}
      </CardHeader>
      <CardContent className="grid min-w-0 gap-4">
        {alert}
        <div className="min-w-0" data-run-question="">
          <p className="text-xs text-fg-muted">{labels.question}</p>
          <div className="mt-1 whitespace-pre-wrap break-words text-sm text-fg [overflow-wrap:anywhere]">{question}</div>
        </div>
        <RunFactList facts={facts} />
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 節

export interface RunDetailSectionProps {
  /** 節の見出し（翻訳済み）。 */
  title: string;
  description?: string;
  /** 見出しの右の件数・状態のバッジ（操作は置かない）。 */
  meta?: ReactNode;
  /** 内容への操作（コピー・CSV など。`ContentActionBar`）。開閉できる節では内容の先頭に置く。 */
  actions?: ReactNode;
  /** 開閉できる節（技術的な詳細・長い根拠の一覧）。既定は閉じる（`defaultOpen` で開く）。 */
  collapsible?: boolean;
  defaultOpen?: boolean;
  /**
   * 開閉を製品が持つ（受控。`collapsible` のときだけ）。開いたときだけ中身を読む節（Agent の監査ログ）に使う。
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  icon?: LucideIcon;
  children: ReactNode;
  testId?: string;
}

/** 詳細の 1 つの節（カード）。見出しの大きさと余白を 3 製品で同じにする。 */
export function RunDetailSection({
  title,
  description,
  meta,
  actions,
  collapsible = false,
  defaultOpen = false,
  open,
  onOpenChange,
  icon,
  children,
  testId,
}: RunDetailSectionProps) {
  if (collapsible) {
    return (
      <Disclosure
        summary={title}
        description={description}
        meta={meta}
        icon={icon}
        defaultOpen={defaultOpen}
        open={open}
        onOpenChange={onOpenChange}
        className="bg-surface shadow-[var(--shadow-card)]"
        contentClassName="grid min-w-0 gap-3 border-t border-border p-5"
        data-run-detail-section=""
        data-testid={testId}
      >
        {actions ? <div className="flex justify-end">{actions}</div> : null}
        {children}
      </Disclosure>
    );
  }
  return (
    <Card className="min-w-0" data-run-detail-section="" data-testid={testId}>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <CardTitle>{title}</CardTitle>
            {meta}
          </div>
          {description ? <CardDescription className="mt-1">{description}</CardDescription> : null}
        </div>
        {actions ? <div className="min-w-0">{actions}</div> : null}
      </CardHeader>
      <CardContent className="grid min-w-0 gap-3">{children}</CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 処理の詳細

export interface RunProcessDetailsProps {
  /** 実測の trace（「トレースの詳細」）。文言は製品の root の `TraceDetailsLabelsProvider` から取る。 */
  traceSummary?: unknown;
  /**
   * 処理の経過が trace の投影のとき（#3226）の trace id。「処理の詳細」の先頭に Trace ID とコピーのボタンを出す。
   * `traceSummary` は渡さない（処理の経過と同じ記録を 2 回出さない）。
   */
  traceId?: string;
  /** 見出し（翻訳済み）。既定「処理の詳細」。 */
  title?: ReactNode;
  /** 開いた中身の先頭の補足。 */
  description?: ReactNode;
  defaultOpen?: boolean;
  children?: ReactNode;
  /** 開閉の要素（`<details>`）の `data-testid`。 */
  testId?: string;
  /** 見出し（`<summary>`）の `data-testid`。 */
  summaryTestId?: string;
  className?: string;
}

/**
 * 処理の詳細（#2325）。処理の経過に属する技術的な詳細（回答の記録の詳細・識別子・生成した SQL・イベントの記録など）を、
 * 処理の経過のタイムラインの後ろに既定で閉じて置く控えめな開閉（`Disclosure` の plain・sm）。
 *
 * チャット・RAG 検索の処理の経過（`ChatProgress` の `footer`）と、実行の詳細の「処理の経過」の節（`RunTimeline` の後ろ）で
 * 同じ名前・同じ形・同じ位置にする。処理の経過の外（回答・根拠の後ろやページの末尾）に別の節として置かない
 * （回答の補足と読み違える。ユーザーの指摘）。生の JSON などさらに深い内容は、中で `<pre data-surface="code">` や
 * 入れ子の `Disclosure` に畳む。
 */
export function RunProcessDetails({
  traceSummary,
  traceId,
  title = DEFAULT_RUN_DETAIL_LABELS.processDetails,
  description,
  defaultOpen = false,
  children,
  testId,
  summaryTestId,
  className,
}: RunProcessDetailsProps) {
  const traceLabels = useTraceDetailsLabels();
  return (
    <Disclosure
      variant="plain"
      size="sm"
      summary={title}
      defaultOpen={defaultOpen}
      summaryProps={{ "data-testid": summaryTestId }}
      summaryClassName="text-fg-muted"
      contentClassName="grid min-w-0 gap-3"
      className={className}
      data-run-process-details=""
      data-testid={testId}
    >
      {description ? <p className="text-xs text-fg-muted">{description}</p> : null}
      {traceId ? <TraceIdRow traceId={traceId} labels={traceLabels} /> : null}
      <TraceDetails summary={traceSummary} />
      {children}
    </Disclosure>
  );
}

// ---------------------------------------------------------------------------
// 処理の経過

/** 段の状態（チャットの処理の経過 `ChatProgressStepStatus` と同じ値 + 応答待ち）。 */
export type RunTimelineStepStatus = ChatProgressStepStatus | "waiting";

export interface RunTimelineStep {
  id: string;
  /** 段の名前（翻訳済み。例「SQL を生成」「ツール: search_documents」）。 */
  label: string;
  status: RunTimelineStepStatus;
  /** ISO 8601。開始と終了があれば時間の線を実際の位置に描く。 */
  startedAt?: string | null;
  finishedAt?: string | null;
  /** 所要時間（ミリ秒）。無ければ開始と終了から求める。開始が無い段は前の段の後ろに続けて描く。 */
  durationMs?: number | null;
  /** 短い補足（対象の表・ツール名・件数・LLM の回数）。名前の後ろの括弧に出す。SQL 全文・エラーの全文は `children` に入れる。 */
  detail?: string;
  /** 段の右に並べるバッジ（token・試行の回数など）。 */
  meta?: ReactNode;
  /**
   * 開いて見る詳細（見出しと値・行・SQL や JSON の箱・省略の印。#2294）。中身があれば行に開閉のボタンが付く。
   * 製品は記録をこの形に写すだけにし、描き方は部品が持つ（チャットの処理の経過と実行の詳細で同じ形）。
   */
  details?: RunStepDetails;
  /** 開いて見る自由な中身（結果の表など。`details` の下に出す）。渡すと行に開閉のボタンが付く。 */
  children?: ReactNode;
  /**
   * 入れ子の深さ（0 = 段、1 以上 = 段の中のツールの呼び出し・工程）。字下げは 3 段で止める（375px で名前の幅を残す）。
   * 時刻の無い入れ子の段の線は、親（1 つ浅い直前の段）の始まりから積む。
   */
  level?: number;
}

const STEP_ICON: Record<Exclude<RunTimelineStepStatus, "running">, { icon: LucideIcon; className: string }> = {
  done: { icon: CheckCircle2, className: "text-success-fg" },
  failed: { icon: XCircle, className: "text-danger-fg" },
  skipped: { icon: MinusCircle, className: "text-fg-muted" },
  pending: { icon: Circle, className: "text-fg-subtle" },
  waiting: { icon: CirclePause, className: "text-warning-fg" },
};

const STEP_BAR: Record<RunTimelineStepStatus, string> = {
  done: "bg-accent-emphasis",
  running: "bg-info-emphasis",
  waiting: "bg-warning-emphasis",
  failed: "bg-danger-emphasis",
  skipped: "bg-fg-subtle",
  pending: "bg-fg-subtle",
};

/** 入れ子の字下げ（1 段 = 1.5rem。3 段で止める）。 */
const STEP_INDENT = ["", "pl-6", "pl-12", "pl-16"] as const;

function stepLevel(level: number | undefined): number {
  return typeof level === "number" && Number.isFinite(level) ? Math.max(0, Math.floor(level)) : 0;
}

function stepIndent(level: number | undefined): string {
  return STEP_INDENT[Math.min(STEP_INDENT.length - 1, stepLevel(level))];
}

/** 段の行の字下げ（`level`。3 段で止める）。`RunTimeline` の外で同じ形の行を描く部品（チャットの実行中の段）が使う。 */
export const runTimelineStepIndent = stepIndent;

/**
 * 回さない実行中の印（`spinRunning` が false の一覧の実行中の段。チャットの処理の経過の、今の段階を包む親の工程）。
 * `RunStatusBadge` の実行中と同じ形（`CirclePlay`）にする。
 */
const RUNNING_STATIC_ICON = { icon: CirclePlay, className: "text-accent-fg" } as const;

/** 段の状態のアイコン。大きさは行の文字の大きさ（`sm` 12px / `md` 14px。#2371）。 */
function StepIcon({
  status,
  size = TEXT_ICON_SIZE.sm,
  spin = true,
}: {
  status: RunTimelineStepStatus;
  size?: number;
  /** 実行中の段を `Spinner` で回すか（false は `RUNNING_STATIC_ICON`）。 */
  spin?: boolean;
}) {
  if (status === "running" && spin) return <Spinner size={size} className="text-accent-fg" />;
  const { icon: Icon, className } = status === "running" ? RUNNING_STATIC_ICON : STEP_ICON[status];
  return <Icon size={size} className={cn("shrink-0", className)} aria-hidden="true" />;
}

export interface RunTimelineBar {
  /** 線の始まり（全体に対する %）。 */
  offset: number;
  /** 線の長さ（%。0.5% 未満は 0.5% にして見えるようにする）。 */
  width: number;
}

/**
 * 段ごとの時間の線の位置（%）を求める。開始・終了の時刻がある段はその位置、無い段は所要時間を前の段の後ろに積む
 * （入れ子の段は親の段の始まりから積む。時刻を持たない記録でも、子の線が親の線の中に入る）。
 * 全体の長さが分からない（どの段にも所要時間が無い）ときは null（線を描かない）。
 */
export function runTimelineBars(steps: readonly RunTimelineStep[]): (RunTimelineBar | null)[] {
  const spans = steps.map((step) => {
    const start = operationTimestampMs(step.startedAt ?? undefined);
    const end = operationTimestampMs(step.finishedAt ?? undefined);
    const duration = step.durationMs ?? (start !== null && end !== null ? Math.max(0, end - start) : null);
    return { start, duration, level: stepLevel(step.level) };
  });
  const starts = spans.map((span) => span.start).filter((value): value is number => value !== null);
  const origin = starts.length ? Math.min(...starts) : null;
  // 深さごとの、直前の段の始まり（子の起点）と、同じ親の中で次の段を置く位置。
  const levelStart: (number | undefined)[] = [];
  const levelCursor: (number | undefined)[] = [];
  const placed = spans.map((span) => {
    const { level } = span;
    const parentStart = level > 0 ? (levelStart[level - 1] ?? 0) : 0;
    const offset =
      span.start !== null && origin !== null ? span.start - origin : (levelCursor[level] ?? parentStart);
    // 新しい段を置いたら、より深い段の起点を作り直す（次の子はこの段の始まりから積む）。
    levelStart.length = level;
    levelCursor.length = level + 1;
    levelStart[level] = offset;
    if (span.duration === null) return null;
    levelCursor[level] = Math.max(levelCursor[level] ?? 0, offset + span.duration);
    return { offset, duration: span.duration };
  });
  const total = Math.max(0, ...placed.map((item) => (item ? item.offset + item.duration : 0)));
  if (total <= 0) return placed.map(() => null);
  return placed.map((item) => {
    if (!item) return null;
    const offset = Math.min(100, (item.offset / total) * 100);
    const width = Math.max(0.5, Math.min(100 - offset, (item.duration / total) * 100));
    return { offset, width };
  });
}

function stepDurationMs(step: RunTimelineStep): number | null {
  if (step.durationMs != null) return step.durationMs;
  const start = operationTimestampMs(step.startedAt ?? undefined);
  const end = operationTimestampMs(step.finishedAt ?? undefined);
  return start !== null && end !== null ? Math.max(0, end - start) : null;
}

/**
 * 段の開閉の印（`DisclosureChevron`）の大きさ（px）。行の名前の文字の大きさにそろえる（#2338）: `sm` は
 * `--font-size-xs`（12px）、`md` は `--font-size-sm`（14px。`--icon-sm` と同じ）。指定しないと lucide の既定の
 * 24px になり、行の文字より大きく見える。印は行の 1 行目（`leading-5`）の縦の中央に置き、押せる範囲は
 * ボタンの `pr-touch-target` が保つ。
 */
export const RUN_TIMELINE_CHEVRON_SIZE = {
  sm: TEXT_ICON_SIZE.xs,
  md: TEXT_ICON_SIZE.sm,
} as const satisfies Record<"sm" | "md", number>;

/** 並列の段は経過の範囲で数え、呼び出し時間を足し合わせない。 */
export function runTimelineDuration(steps: readonly RunTimelineStep[]): number | null {
  const roots = steps.filter(step => !step.level);
  const intervals = roots.map(step => [operationTimestampMs(step.startedAt), operationTimestampMs(step.finishedAt)]);
  if (intervals.length && intervals.every(([start, end]) => start != null && end != null && end >= start)) {
    return Math.max(...intervals.map(([, end]) => end!)) - Math.min(...intervals.map(([start]) => start!));
  }
  const sum = roots.reduce((total, step) => total + (stepDurationMs(step) ?? 0), 0);
  return sum > 0 ? sum : null;
}

export interface RunTimelineProps {
  steps: readonly RunTimelineStep[];
  /** 処理全体の所要時間（ミリ秒）。要約の行に出す（無ければ段の合計の範囲）。 */
  totalDurationMs?: number | null;
  /** 中身のある段を最初から開く（失敗した段は既定で開く）。 */
  defaultExpanded?: boolean;
  /**
   * 要約の行（「N ステップ・合計 M 秒」）を出すか。既定 true。チャットの処理の経過（`ChatProgress`）は、開閉の見出し
   * （「処理の経過（N ステップ・M 秒）」）が同じ情報を持つので出さない。
   */
  showSummary?: boolean;
  /**
   * 時間の線を出すか。既定 true。実行中の一覧（全体の長さが伸び続け、線が毎回伸び縮みする）は出さない。
   */
  showBars?: boolean;
  /**
   * 実行中の段を `Spinner` で回すか。既定 true。false は回さない実行中の印（`CirclePlay`）で出す。チャットの処理の経過
   * （`ChatProgress`）は、今の段階（最も内側の実行中の段）の行を自分で描き、その行だけを回す。今の段階を包む実行中の
   * 親の工程は、この一覧の中で開始順の位置に回さずに出す（同じ処理のスピナーは 1 つ。messaging.md §3.7。#2840）。
   */
  spinRunning?: boolean;
  /** 文字の大きさ。`sm`（既定。全製品の操作画面・履歴で共通）/ `md`（大きな表示）。 */
  size?: "sm" | "md";
  /** 段の所要時間の表記。既定は `formatRunDuration`（「0.4 秒」「1 分 5 秒」）。 */
  formatDuration?: (ms: number) => string;
  /**
   * 行に `data-testid="<stepTestIdPrefix>-step-<id>"` を付ける（`ChatProgress` は自分の testId を渡す）。開閉の
   * ボタンは `<…>-step-<id>-toggle`、開いた詳細は `<…>-step-<id>-details`。
   */
  stepTestIdPrefix?: string;
  labels?: RunDetailLabelsInput;
  testId?: string;
}

/**
 * 処理の経過（#1955）。段とツールの呼び出しを時間の順に 1 行ずつ並べ、状態のアイコン・名前・所要時間（文字）と、
 * 全体の中の位置と長さの線（ウォーターフォール）を出す。線は飾りで、時間は必ず文字でも出す。
 * 実行の詳細の「処理の経過」と、チャットの回答の「処理の経過」（`ChatProgress` の一覧）の 1 つの部品（#2265）。
 *
 * - 行の中身（ツールの引数と結果・SQL・エラー）は、行の開閉のボタン（`aria-expanded` + `DisclosureChevron`）で開く。
 *   失敗した段は最初から開く。
 * - 入れ子の段は `level` で字下げする（3 段まで）。補足（LLM の回数など）は名前の後ろの括弧に出す。
 * - 狭い置き場所（36rem 未満）では線を隠し、名前と所要時間だけにする（container query）。
 * - 状態はアイコンの形と読み上げの文言で伝え、失敗・スキップ・応答待ちは画面にも文字で出す（色だけにしない）。
 */
export function RunTimeline({
  steps,
  totalDurationMs,
  defaultExpanded = false,
  showSummary = true,
  showBars = true,
  spinRunning = true,
  size = "sm",
  formatDuration = formatRunDuration,
  stepTestIdPrefix,
  labels: labelsInput,
  testId,
}: RunTimelineProps) {
  const traceLabels = useTraceDetailsLabels();
  const labels = resolveRunDetailLabels(labelsInput);
  const bars = runTimelineBars(steps);
  const idPrefix = useId();
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(
      steps
        .filter((step) => step.children != null || hasRunStepDetails(step.details))
        .map((step) => [step.id, defaultExpanded || step.status === "failed"])
    )
  );
  const total = totalDurationMs ?? runTimelineDuration(steps);
  const nameText = size === "sm" ? "text-xs" : "text-sm";

  return (
    <div
      className="@container grid min-w-0 gap-2"
      data-run-timeline=""
      data-run-timeline-bars={showBars ? "true" : "false"}
      data-testid={testId}
    >
      {showBars && steps.some(step => stepDurationMs(step) != null && (!step.startedAt || !step.finishedAt)) ? (
        <p className="text-xs text-fg-muted">{traceLabels.reconstructed}</p>
      ) : null}
      {showSummary ? (
        <p className="text-xs text-fg-muted tnum">
          {labels.timelineSummary(steps.length, formatRunDuration(total, labels.notRecorded))}
        </p>
      ) : null}
      <ol className={cn("grid min-w-0", size === "sm" ? "gap-1.5" : "gap-1")} aria-label={labels.timeline}>
        {steps.map((step, index) => {
          const duration = stepDurationMs(step);
          const bar = showBars ? bars[index] : null;
          const details = hasRunStepDetails(step.details) ? step.details : undefined;
          const hasDetails = step.children != null || details !== undefined;
          const stepTestId = stepTestIdPrefix ? `${stepTestIdPrefix}-step-${step.id}` : undefined;
          const open = hasDetails && Boolean(expanded[step.id]);
          const regionId = `${idPrefix}-step-${index}`;
          // 失敗・スキップ・応答待ち・未実行（失敗の後に実行しなかった段。#2342）は状態を文字でも出す。
          const visibleStatus =
            step.status === "failed" || step.status === "skipped" || step.status === "waiting" || step.status === "pending";
          const level = stepLevel(step.level);
          const name = (
            <span className={cn("min-w-0 break-words [overflow-wrap:anywhere]", !hasDetails && "flex-1")}>
              <span
                className={
                  step.status === "failed" ? "text-danger-fg" : step.status === "pending" ? "text-fg-muted" : "text-fg"
                }
                data-run-timeline-label=""
              >
                {step.label}
              </span>
              {step.detail ? <span className="text-fg-muted">{`（${step.detail}）`}</span> : null}
              {visibleStatus ? null : <span className="sr-only">{` ${labels.stepStatus[step.status]}`}</span>}
            </span>
          );
          return (
            <li
              key={step.id}
              className="min-w-0 rounded-md"
              data-status={step.status}
              data-level={level > 0 ? String(level) : undefined}
              data-run-timeline-step={step.id}
              data-testid={stepTestId}
            >
              {/* 線の列と時間の列の幅を全行で同じにし、線の始まりをそろえる（字下げは名前の列の中だけ）。 */}
              <div
                className={cn(
                  "grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3",
                  showBars && "@xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_7rem] @xl:items-center"
                )}
              >
                <div className={cn("flex min-w-0 items-start gap-2 leading-5", nameText, stepIndent(level))}>
                  <span className="pr-icon-line">
                    <StepIcon status={step.status} size={RUN_TIMELINE_CHEVRON_SIZE[size]} spin={spinRunning} />
                  </span>
                  {hasDetails ? (
                    <button
                      type="button"
                      className="pr-touch-target relative inline-flex min-w-0 max-w-full cursor-pointer items-start gap-1 rounded-sm text-left hover:underline hover:underline-offset-2"
                      aria-expanded={open}
                      aria-controls={regionId}
                      data-run-timeline-toggle=""
                      data-testid={stepTestId ? `${stepTestId}-toggle` : undefined}
                      onClick={() => setExpanded((current) => ({ ...current, [step.id]: !current[step.id] }))}
                    >
                      {name}
                      <span className="pr-icon-line text-fg-muted">
                        <DisclosureChevron expanded={open} size={RUN_TIMELINE_CHEVRON_SIZE[size]} />
                      </span>
                    </button>
                  ) : (
                    name
                  )}
                  {step.meta ? <span className="flex shrink-0 flex-wrap items-center gap-1">{step.meta}</span> : null}
                </div>
                {/* 時間の線（飾り）。広い置き場所だけ。 */}
                {showBars ? (
                  <div
                    className="relative hidden h-2 min-w-0 overflow-hidden rounded-full bg-surface-sunken @xl:block"
                    aria-hidden="true"
                    data-run-timeline-bar=""
                  >
                    {bar ? (
                      <span
                        className={cn(
                          "absolute inset-y-0 rounded-full forced-colors:bg-[CanvasText]",
                          STEP_BAR[step.status]
                        )}
                        style={{ left: `${bar.offset}%`, width: `${bar.width}%` }}
                      />
                    ) : null}
                  </div>
                ) : null}
                <span className="whitespace-nowrap text-right text-xs leading-5 text-fg-muted tnum">
                  {visibleStatus ? labels.stepStatus[step.status] : null}
                  {visibleStatus && duration !== null ? " · " : null}
                  {duration !== null ? formatDuration(duration) : visibleStatus ? null : labels.notRecorded}
                </span>
              </div>
              {hasDetails ? (
                <div
                  id={regionId}
                  hidden={!open}
                  className="mt-2 mb-1 ml-6 grid min-w-0 gap-2 rounded-md border border-border bg-surface-sunken p-3 text-sm"
                  data-run-timeline-details=""
                  data-testid={stepTestId ? `${stepTestId}-details` : undefined}
                >
                  {open ? (
                    <>
                      {details ? (
                        <RunStepDetailsView details={details} labels={{ truncated: labels.stepDetailsTruncated }} />
                      ) : null}
                      {step.children}
                    </>
                  ) : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 処理の経過の節

export interface RunTimelineSectionProps {
  traceSummary?: unknown;
  /** 処理の経過が trace の投影のときの trace id（「処理の詳細」に出す。#3226）。 */
  traceId?: string;
  /** 節の見出し（翻訳済み。「処理の経過」）。 */
  title: string;
  steps: readonly RunTimelineStep[];
  /** 処理全体の所要時間（ミリ秒）。開閉の見出しに出す（無ければ段の合計）。 */
  totalDurationMs?: number | null;
  /** 段が無いときの表示（開閉の見出しを出さずに置く）。 */
  empty?: ReactNode;
  /** 処理の詳細の中身。共通の開閉内に trace とまとめる。 */
  processDetails?: ReactNode;
  processDetailsProps?: Omit<RunProcessDetailsProps, "children" | "traceSummary" | "traceId">;
  /** 段の一覧を最初から開く。既定は閉じる（#2473）。 */
  defaultOpen?: boolean;
  labels?: RunDetailLabelsInput;
  /** 節の `data-testid`。開閉の見出しは `<testId>-summary`。 */
  testId?: string;
}

/**
 * 実行の詳細の「処理の経過」の節（#2473）。節の中を「N ステップ・合計 M 秒」の 1 行の開閉（`Disclosure` の plain。
 * 回答の根拠の「AI に渡した N 件 ／ 回答で引用 M 件」と同じ形）にし、既定で閉じる。開くと段の一覧（`RunTimeline`）と
 * その後ろの処理の詳細が出る。段の数はチャットの処理の経過（`ChatProgress`）と同じく入れ子の段も数える。
 * 3 製品の実行の詳細はこの部品を使い、節の中の並びを自分で組まない。
 */
export function RunTimelineSection({
  traceSummary,
  traceId,
  title,
  steps,
  totalDurationMs,
  empty,
  processDetails,
  processDetailsProps,
  defaultOpen = false,
  labels: labelsInput,
  testId,
}: RunTimelineSectionProps) {
  const labels = resolveRunDetailLabels(labelsInput);
  const total = totalDurationMs ?? runTimelineDuration(steps);
  const failed = steps.some((step) => step.status === "failed");
  return (
    <RunDetailSection title={title} testId={testId}>
      {steps.length ? (
        <Disclosure
          variant="plain"
          size="sm"
          summary={<span className="tnum">{labels.timelineSummary(steps.length, formatRunDuration(total, labels.notRecorded))}</span>}
          // 失敗した段がある実行は、色だけでなく失敗のアイコンでも伝える（チャットの処理の経過と同じ）。
          icon={failed ? XCircle : undefined}
          defaultOpen={defaultOpen}
          summaryClassName={failed ? "text-danger-fg [&>span:first-child]:text-danger-fg" : undefined}
          summaryProps={{ "data-testid": testId ? `${testId}-summary` : undefined, "data-run-progress-summary": "" }}
          contentClassName="grid min-w-0 gap-3 pt-2"
          data-run-timeline-section=""
        >
          <RunTimeline steps={steps} totalDurationMs={totalDurationMs} showSummary={false} labels={labelsInput} />
          {processDetails != null || traceSummary != null || traceId ? <RunProcessDetails {...processDetailsProps} traceSummary={traceSummary} traceId={traceId}>{processDetails}</RunProcessDetails> : null}
        </Disclosure>
      ) : (
        <>
          {empty}
          {processDetails != null || traceSummary != null || traceId ? <RunProcessDetails {...processDetailsProps} traceSummary={traceSummary} traceId={traceId}>{processDetails}</RunProcessDetails> : null}
        </>
      )}
    </RunDetailSection>
  );
}

// ---------------------------------------------------------------------------
// 並び

export interface RunDetailLayoutProps {
  /** 概要（`RunSummary`）。 */
  summary: ReactNode;
  /** 回答（最終の出力。`RunDetailSection` + 製品の回答の表示）。 */
  answer?: ReactNode;
  /** 処理の経過（`RunTimelineSection`。開閉の中にタイムラインと `RunProcessDetails`〔処理の詳細〕。#2325 / #2473）。 */
  timeline?: ReactNode;
  /** 根拠・SQL と結果・検証・成果物（製品の節。複数可）。 */
  evidence?: ReactNode;
  /** フィードバック。 */
  feedback?: ReactNode;
  /**
   * 処理の経過に属さない技術的な節（Agent の監査ログ。`RunDetailSection collapsible`。既定で閉じる）。処理の識別子・
   * 生成した SQL・イベントの記録などの技術的な詳細は、処理の経過の節の中の `RunProcessDetails` に置く（#2325）。
   */
  technical?: ReactNode;
  testId?: string;
}

/**
 * 詳細の節の順を固定する並び（#1955）。概要 → 処理の経過（タイムラインと処理の詳細。#2325）→ 回答 → 根拠 / SQL / 検証 →
 * フィードバック → 監査ログ（処理の経過は回答の上。チャットと同じ並び。#2126）。
 * タブで隠さず、1 枚のページを縦に読む（ページ内検索・印刷・読み上げで全部に届く。run-history.md「詳細」）。
 */
export function RunDetailLayout({ summary, answer, timeline, evidence, feedback, technical, testId }: RunDetailLayoutProps) {
  return (
    <div className="grid min-w-0 content-start gap-4 [&>*]:min-w-0" data-run-detail="" data-testid={testId}>
      {summary}
      {timeline}
      {answer}
      {evidence}
      {feedback}
      {technical}
    </div>
  );
}

/** 詳細の読み込み中の形（概要のカード + 2 つの節）。`TimedLoadingState` の子に置く。 */
export function RunDetailSkeleton({ testId }: { testId?: string }) {
  return (
    <div className="grid min-w-0 gap-4" aria-hidden="true" data-testid={testId}>
      <div className="grid gap-4 rounded-lg border border-border bg-surface p-5">
        <div className="flex items-center gap-2">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
        <Skeleton className="h-4 w-3/4" />
        <div className="grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(min(100%,11rem),1fr))]">
          {Array.from({ length: 6 }, (_, index) => (
            <div key={index} className="grid gap-1">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-4 w-28" />
            </div>
          ))}
        </div>
      </div>
      {Array.from({ length: 2 }, (_, index) => (
        <div key={index} className="grid gap-3 rounded-lg border border-border bg-surface p-5">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ))}
    </div>
  );
}

/** Trace ID とコピーのボタン。ID の文字列だけを折り返し、ボタンを同じ行に保つ（375px。#3219）。 */
function TraceIdRow({ traceId, labels }: { traceId: string; labels: TraceDetailsLabels }) {
  return <div className="flex min-w-0 items-center gap-2" data-trace-id-row="">
    <span className="min-w-0 break-all font-mono text-xs text-fg-muted">Trace ID: {traceId}</span>
    <CopyButton type="button" variant="ghost" size="sm" iconOnly className="shrink-0" text={traceId} label={labels.copy} copiedMessage={labels.copied} failedMessage={labels.copyFailed} />
  </div>;
}

function TraceSegmentContent({ summary, labels }: { summary: unknown; labels: TraceDetailsLabels }) {
  const trace = traceTimeline(summary, labels);
  const notSampled = summary && typeof summary === "object" && "sampled" in summary && summary.sampled === false;
  return trace ? <div className="grid min-w-0 gap-2">
    <p className="text-xs text-fg-muted">{trace.steps.length === 0 ? labels.empty : trace.partial ? labels.partial : labels.measured}</p>
    {notSampled ? <p className="text-xs text-fg-muted">{labels.notSampled}</p> : null}
    {trace.traceId ? <TraceIdRow traceId={trace.traceId} labels={labels} /> : null}
    <RunTimeline steps={trace.steps} />
  </div> : null;
}

function TraceSegmentDetails({ summary, labels, title, defaultOpen }: { summary: unknown; labels: TraceDetailsLabels; title: string; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return <Disclosure variant="plain" size="sm" summary={title} open={open} onOpenChange={setOpen}>
    {open ? <TraceSegmentContent summary={summary} labels={labels} /> : null}
  </Disclosure>;
}

/** 5 つの質問の入口で使う、安全な実測 trace の詳細。実行区間の追加でも開閉を保つ。 */
export function TraceDetails({ summary }: { summary: unknown }) {
  const labels = useTraceDetailsLabels();
  const [open, setOpen] = useState(false);
  if (summary == null) return null;
  const segments = Array.isArray(summary)
    ? summary.filter(segment => segment && typeof segment === "object" && !Array.isArray(segment)).slice(0, 1000)
    : [summary];
  return <Disclosure variant="plain" size="sm" summary={labels.title} open={open} onOpenChange={setOpen} summaryProps={{ "data-testid": "trace-details-toggle" }}>
    {open ? <div className="grid min-w-0 gap-3" data-testid="trace-details">
      {segments.length === 0 ? <p className="text-xs text-fg-muted">{labels.empty}</p> : null}
      {segments.length === 1 ? <TraceSegmentContent summary={segments[0]} labels={labels} /> : segments.map((segment, index) => <div key={segment && typeof segment === "object" && "root_span_id" in segment && typeof segment.root_span_id === "string" ? segment.root_span_id : index}>
        <TraceSegmentDetails summary={segment} labels={labels} title={labels.segment(index + 1, segments.length)} defaultOpen={index === segments.length - 1} />
      </div>)}
    </div> : null}
  </Disclosure>;
}
