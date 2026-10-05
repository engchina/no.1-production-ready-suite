import { CheckCircle2, Circle, Clock3, ListChecks, MinusCircle, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { operationTimestampMs } from "../../lib/operation-timing";
import { cn } from "../../lib/utils";
import { Disclosure } from "../ui/disclosure";
import { Spinner } from "../ui/spinner";
import { useOperationTiming } from "./processing-state";

const DEFAULT_SLOW_AFTER_MS = 10_000;

/** 段階の状態（AG-UI の STEP_STARTED / STEP_FINISHED / RUN_ERROR に倣う。3 製品共通）。 */
export type ChatProgressStepStatus = "pending" | "running" | "done" | "failed" | "skipped";

/**
 * チャットの 1 回の回答の処理の段階（3 製品共通の契約。#1145）。
 *
 * backend は製品の既存の配信（polling / SSE / WebSocket）で、この形の一覧を画面へ渡す。
 */
export interface ChatProgressStep {
  /** 段階の識別子（例: "classify" / "schema" / "generate_sql" / "execute" / "summarize" / "tool:<name>"）。 */
  id: string;
  /** 利用者向けの日本語（例:「SQL を生成しています」）。翻訳済みの文を渡す。 */
  label: string;
  status: ChatProgressStepStatus;
  /** ISO 8601。 */
  startedAt?: string;
  /** ISO 8601。 */
  finishedAt?: string;
  /** 任意の短い補足（対象の表の名前・使ったツール名・件数など）。SQL 全文・ORA コードなど技術的な詳細は入れない。 */
  detail?: string;
}

/** 文言。製品の i18n で上書きできる。 */
export interface ChatProgressLabels {
  /** 実行中に、完了した段階を畳んだ見出し（例:「3 ステップ完了」）。 */
  completedSteps: (count: number) => string;
  /** 完了後の 1 行（例:「処理の経過（4 ステップ・12 秒）」）。 */
  summary: (count: number, duration: string) => string;
  /** 段階の一覧の読み上げの名前。 */
  steps: string;
  /** 処理全体の経過時間の見出し（今の段階の行の右に出す。#1176）。 */
  elapsed: string;
  /** 今の段階の遅延の案内（今の段階の経過時間で判断する。#1176）。 */
  slow: string;
  /**
   * 終端でないのに実行中・待機中の段階が無い（段階の外で処理が続いている）ときの今の行（#1176）。
   * 製品は段階を写し漏らさないようにし、これは最後の備えにする。
   */
  working: string;
  /** 更新が途絶え、状態を取り直している間の案内（#1160。遅延の案内より優先する）。 */
  reconnecting: string;
  /** 段階の状態（アイコンに添える。色だけに頼らない）。 */
  status: Record<ChatProgressStepStatus, string>;
  /** 所要時間の表記。 */
  formatDuration: (ms: number) => string;
}

/** 所要時間の既定の表記（1 秒未満も 0.1 秒単位で出す。1 分以上は「1 分 5 秒」）。 */
export function formatChatProgressDuration(ms: number): string {
  const value = Math.max(0, ms);
  if (value < 10_000) return `${(Math.floor(value / 100) / 10).toFixed(1)} 秒`;
  const totalSeconds = Math.floor(value / 1000);
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} 分 ${totalSeconds % 60} 秒`;
  return `${Math.floor(totalMinutes / 60)} 時間 ${totalMinutes % 60} 分`;
}

export const DEFAULT_CHAT_PROGRESS_LABELS: ChatProgressLabels = {
  completedSteps: (count) => `${count} ステップ完了`,
  summary: (count, duration) => `処理の経過（${count} ステップ・${duration}）`,
  steps: "処理の段階",
  elapsed: "経過時間",
  slow: "通常より時間がかかっています。",
  working: "処理を続けています",
  reconnecting: "接続を確認しています。",
  status: {
    pending: "待機中",
    running: "処理中",
    done: "完了",
    failed: "失敗",
    skipped: "スキップ",
  },
  formatDuration: formatChatProgressDuration,
};

export interface ChatProgressProps {
  steps: ChatProgressStep[];
  /**
   * 処理中か。省略時は段階から決める（実行中の段階がある、または失敗が無く待機中の段階が残る）。
   * 回答の本文の受信中など、段階の外で処理が続くときは明示する。
   */
  active?: boolean;
  /** 全体の所要時間（完了後の 1 行）。省略時は段階の最初の開始から最後の終了まで。 */
  elapsedMs?: number | null;
  /**
   * 処理全体の開始（ISO 8601 か epoch ms）。実行中の経過時間をここから数える（#1176）。
   * 省略時は段階の最初の開始。どちらも無いときは、この表示が処理中になった時刻から数える。
   */
  startedAt?: string | number | null;
  /**
   * 今の段階がこの時間を超えたら、今の段階の行に遅延の案内を付ける。既定 10 秒（ProcessingIndicator と同じ）。
   * 全体の経過時間ではなく今の段階で判断する（どの段階で時間がかかっているかを示す。LLM を使う処理は全体が
   * 10 秒を超えるのが普通で、全体で判断すると毎回出る。#1176）。
   */
  slowAfterMs?: number;
  /** 完了後の 1 行を最初から開くか。既定は失敗した段階があるときだけ開く。 */
  defaultOpen?: boolean;
  /**
   * 更新が途絶え、状態を取り直している（#1160）。今の段階の行に「接続を確認しています」を出す。
   * 製品は `useChatProgressTracker` の結果（`progressProps`）をそのまま渡す。
   */
  reconnecting?: boolean;
  labels?: Partial<ChatProgressLabels>;
  className?: string;
  testId?: string;
}

type ProgressState = "running" | "done" | "failed";

/**
 * 段階から処理中かを決める（実行中の段階がある、または失敗が無く待機中の段階が残る）。3 製品共通の終端の判定。
 */
export function isChatProgressActive(steps: readonly ChatProgressStep[]): boolean {
  if (steps.some((step) => step.status === "running")) return true;
  if (steps.some((step) => step.status === "failed")) return false;
  return steps.some((step) => step.status === "pending");
}

function stepDurationMs(step: ChatProgressStep): number | null {
  const start = operationTimestampMs(step.startedAt);
  const end = operationTimestampMs(step.finishedAt);
  if (start === null || end === null) return null;
  return Math.max(0, end - start);
}

/** 段階の最初の開始（処理全体の開始）。 */
function firstStartedAtMs(steps: readonly ChatProgressStep[]): number | null {
  const starts = steps.map((step) => operationTimestampMs(step.startedAt)).filter((v): v is number => v !== null);
  return starts.length > 0 ? Math.min(...starts) : null;
}

function totalDurationMs(steps: ChatProgressStep[]): number | null {
  const starts = steps.map((step) => operationTimestampMs(step.startedAt)).filter((v): v is number => v !== null);
  const ends = steps.map((step) => operationTimestampMs(step.finishedAt)).filter((v): v is number => v !== null);
  if (starts.length === 0 || ends.length === 0) return null;
  return Math.max(0, Math.max(...ends) - Math.min(...starts));
}

const STATUS_ICON: Record<Exclude<ChatProgressStepStatus, "running">, { icon: LucideIcon; className: string }> = {
  done: { icon: CheckCircle2, className: "text-success-fg" },
  failed: { icon: XCircle, className: "text-danger-fg" },
  skipped: { icon: MinusCircle, className: "text-fg-muted" },
  pending: { icon: Circle, className: "text-fg-subtle" },
};

function StepStatusIcon({ status }: { status: ChatProgressStepStatus }) {
  if (status === "running") return <Spinner size={14} className="text-accent-fg" />;
  const { icon: Icon, className } = STATUS_ICON[status];
  return <Icon size={14} className={cn("shrink-0", className)} aria-hidden="true" />;
}

/** 畳んだ見出し（アイコンは文字と同じ色。失敗は danger の色とアイコンで示す）。 */
function SummaryText({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <span className="inline-flex min-w-0 items-start gap-1.5">
      <span className="flex h-5 shrink-0 items-center">
        <Icon size={14} className="shrink-0" aria-hidden="true" />
      </span>
      <span className="min-w-0 break-words leading-5">{children}</span>
    </span>
  );
}

/** 段階の一覧（開いたときの中身）。 */
function StepList({
  steps,
  labels,
  testId,
}: {
  steps: ChatProgressStep[];
  labels: ChatProgressLabels;
  testId?: string;
}) {
  return (
    <ol className="grid min-w-0 gap-1.5" aria-label={labels.steps}>
      {steps.map((step) => {
        const duration = stepDurationMs(step);
        // 完了は所要時間だけを見せ（アイコンで完了と分かる）、状態の語は読み上げに渡す。失敗・スキップは文字でも出す。
        const visibleStatus = step.status === "failed" || step.status === "skipped";
        return (
          <li
            key={step.id}
            className="flex min-w-0 items-start gap-2 text-xs leading-5"
            data-status={step.status}
            data-testid={testId ? `${testId}-step-${step.id}` : undefined}
          >
            <span className="flex h-5 shrink-0 items-center">
              <StepStatusIcon status={step.status} />
            </span>
            <span className="min-w-0 flex-1 break-words">
              <span className={step.status === "failed" ? "text-danger-fg" : "text-fg"}>{step.label}</span>
              {step.detail ? <span className="text-fg-muted">{`（${step.detail}）`}</span> : null}
              {visibleStatus ? null : <span className="sr-only">{` ${labels.status[step.status]}`}</span>}
            </span>
            {visibleStatus || duration !== null ? (
              <span className="shrink-0 whitespace-nowrap text-fg-muted tabular-nums">
                {visibleStatus ? labels.status[step.status] : null}
                {visibleStatus && duration !== null ? " · " : null}
                {duration !== null ? labels.formatDuration(duration) : null}
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * 実行中の 1 行（スピナー・今の段階・処理全体の経過時間・遅延の案内）。
 *
 * 経過時間は処理全体（最初の段階の開始）から数え、段階が変わっても 0 に戻さない（#1176）。段階ごとの所要時間は
 * 完了した段階の行に出す。遅延の案内は今の段階の経過時間で判断する。
 */
function CurrentStep({
  step,
  startedAt,
  slowAfterMs,
  reconnecting,
  labels,
  testId,
}: {
  /** 今の段階。終端でないのに実行中・待機中の段階が無いときは null（「処理を続けています」を出す）。 */
  step: ChatProgressStep | null;
  startedAt: number | null;
  slowAfterMs: number;
  reconnecting: boolean;
  labels: ChatProgressLabels;
  testId?: string;
}) {
  // 処理全体の経過時間。key を固定し、段階が変わっても数え直さない（開始の時刻が無いときは表示の開始から数える）。
  const total = useOperationTiming({
    active: true,
    operationKey: "chat-progress-total",
    startedAt: startedAt ?? undefined,
  });
  // 今の段階の経過時間（遅延の案内の判断だけに使う。段階が変わったら数え直す）。
  const current = useOperationTiming({
    active: true,
    operationKey: step ? `step:${step.id}` : "step:working",
    startedAt: step?.startedAt,
    slowAfterMs,
  });
  const label = step ? step.label : labels.working;
  return (
    <div
      className="grid min-w-0 gap-1"
      data-testid={testId ? `${testId}-current` : undefined}
      data-step-id={step?.id ?? ""}
      data-slow={current.slow ? "true" : "false"}
      data-reconnecting={reconnecting ? "true" : "false"}
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="flex min-w-0 items-center gap-2 text-sm font-medium text-fg">
          <Spinner size={16} className="text-accent-fg" />
          <span className="min-w-0 break-words">
            {label}
            {step?.detail ? (
              <span className="font-normal text-fg-muted">{`（${step.detail}）`}</span>
            ) : null}
          </span>
        </span>
        <span
          className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-fg-muted"
          role="timer"
          aria-live="off"
          aria-label={`${labels.elapsed} ${total.elapsedClock}`}
          data-testid={testId ? `${testId}-timer` : undefined}
        >
          <Clock3 size={14} aria-hidden="true" />
          <span>{labels.elapsed}</span>
          <span className="min-w-[3.25rem] text-right tabular-nums text-fg">{total.elapsedClock}</span>
        </span>
      </div>
      {/*
        遅延の案内の行は最初から高さを予約する（#902 と同じ。後から行を足してスピナーの行を動かさない）。
        案内は今の段階の行に付け、どの段階で時間がかかっているかを示す。
      */}
      <p
        className="pl-6 text-xs leading-5 text-fg-muted"
        data-testid={
          testId && reconnecting ? `${testId}-reconnecting` : testId && current.slow ? `${testId}-slow` : undefined
        }
      >
        {/* 取り直している間は、遅延の案内の代わりに「接続を確認しています」を出す（#1160）。 */}
        {reconnecting ? (
          labels.reconnecting
        ) : current.slow ? (
          labels.slow
        ) : (
          <span
            className="invisible before:content-[attr(data-placeholder)]"
            aria-hidden="true"
            data-placeholder={labels.slow}
          />
        )}
      </p>
    </div>
  );
}

/**
 * チャットの回答の処理の段階（#1145）。アシスタントの吹き出しの中、回答の上に置く控えめな表示。
 *
 * - 実行中: 今の段階の 1 行（スピナー・段階の名前・処理全体の経過時間・今の段階の遅延の案内）。経過時間は
 *   段階が変わっても 0 に戻さない（#1176）。完了した段階は「✓ N ステップ完了」に畳み、開くと段階ごとの
 *   完了 / 失敗 / スキップと所要時間を出す。終端でないのに実行中・待機中の段階が無いときも、今の行
 *   （「処理を続けています」）を出し、「N ステップ完了」だけにしない。
 * - 完了後: 「処理の経過（N ステップ・M 秒）」の 1 行に畳む（既定は閉じる）。失敗した段階があれば開いて出す。
 * - 段階の切り替わりは polite で読み上げる（経過時間の毎秒の更新は読み上げない）。状態はアイコンと文字で示す。
 * - 動くスピナーは今の段階の 1 つだけ（同じ処理のスピナーは 1 つ。messaging.md §3.7）。
 *
 * SQL 生成の画面のような詳細な工程の表示（NL2SQL の WorkflowProgressStrip）より情報を絞る。
 */
export function ChatProgress({
  steps,
  active: activeProp,
  elapsedMs,
  startedAt,
  slowAfterMs = DEFAULT_SLOW_AFTER_MS,
  defaultOpen,
  reconnecting = false,
  labels: labelOverrides,
  className,
  testId,
}: ChatProgressProps) {
  const labels = { ...DEFAULT_CHAT_PROGRESS_LABELS, ...labelOverrides };
  const active = activeProp ?? isChatProgressActive(steps);
  const failed = steps.some((step) => step.status === "failed");
  const state: ProgressState = active ? "running" : failed ? "failed" : "done";
  // 実行中の段階が無い（段階の間・開始前）ときは、次に進む段階を今の段階として出す。
  const current = active
    ? steps.find((step) => step.status === "running") ?? steps.find((step) => step.status === "pending")
    : undefined;
  const finished = steps.filter((step) => step.status === "done" || step.status === "failed" || step.status === "skipped");
  // 完了後の 1 行の開閉。失敗があれば最初から開く（利用者が閉じたらその状態を保つ）。
  const [summaryOpen, setSummaryOpen] = useState<boolean | null>(null);
  const summaryIsOpen = summaryOpen ?? defaultOpen ?? failed;
  const total = elapsedMs ?? totalDurationMs(steps);
  const overallStartedAt = operationTimestampMs(startedAt) ?? firstStartedAtMs(steps);
  // 段階の切り替わりと、取り直しの開始を読み上げる。
  const currentLabel = active ? (current?.label ?? labels.working) : "";
  const announcement = currentLabel && reconnecting ? `${currentLabel} ${labels.reconnecting}` : currentLabel;

  if (steps.length === 0) return null;

  return (
    <div
      className={cn("grid min-w-0 gap-1", className)}
      aria-busy={active}
      data-chat-progress-state={state}
      data-testid={testId}
    >
      {/* 段階の切り替わりだけを読み上げる（経過時間は role="timer" + aria-live="off"）。 */}
      <span className="sr-only" role="status">
        {announcement}
      </span>
      {active ? (
        <>
          {/* 終端になるまで今の行を必ず出す（段階の外で処理が続くときは「処理を続けています」。#1176）。 */}
          <CurrentStep
            step={current ?? null}
            startedAt={overallStartedAt}
            slowAfterMs={slowAfterMs}
            reconnecting={reconnecting}
            labels={labels}
            testId={testId}
          />
          {finished.length > 0 ? (
            <Disclosure
              variant="plain"
              size="sm"
              summary={<SummaryText icon={CheckCircle2}>{labels.completedSteps(finished.length)}</SummaryText>}
              summaryProps={{ "data-testid": testId ? `${testId}-completed` : undefined }}
              summaryClassName="text-fg-muted"
            >
              <StepList steps={finished} labels={labels} testId={testId} />
            </Disclosure>
          ) : null}
        </>
      ) : (
        <Disclosure
          variant="plain"
          size="sm"
          summary={
            <SummaryText icon={failed ? XCircle : ListChecks}>
              {labels.summary(finished.length, total !== null ? labels.formatDuration(total) : "-")}
            </SummaryText>
          }
          open={summaryIsOpen}
          onOpenChange={setSummaryOpen}
          summaryProps={{ "data-testid": testId ? `${testId}-summary` : undefined }}
          summaryClassName={failed ? "text-danger-fg" : "text-fg-muted"}
        >
          <StepList steps={steps} labels={labels} testId={testId} />
        </Disclosure>
      )}
    </div>
  );
}
