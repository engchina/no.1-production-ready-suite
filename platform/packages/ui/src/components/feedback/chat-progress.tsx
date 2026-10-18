import { Clock3, ListChecks, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { operationTimestampMs } from "../../lib/operation-timing";
import { cn } from "../../lib/utils";
import { RunTimeline, runTimelineStepIndent, type RunTimelineStep } from "../data/run-detail";
import { hasRunStepDetails, type RunStepDetails } from "../data/run-step-details";
import { Disclosure } from "../ui/disclosure";
import { Spinner } from "../ui/spinner";
import { formatChatProgressDuration } from "./chat-progress-format";
import { reduceChatProgressSteps, type ChatProgressStepsState } from "./chat-progress-steps";
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
  /**
   * 状態で変わらない名詞の名前（例:「SQL の生成」）。名前を状態で変える製品の段階で、`label` と違うときだけ入る
   * （`labelChatProgressSteps` が段階の定義の未実行の名前から付ける）。失敗のカードの止まった段に使う（#2342）。
   */
  name?: string;
  status: ChatProgressStepStatus;
  /** ISO 8601。 */
  startedAt?: string;
  /** ISO 8601。 */
  finishedAt?: string;
  /** 任意の短い補足（対象の表の名前・使ったツール名・件数など）。SQL 全文・ORA コードなど技術的な詳細は入れない。 */
  detail?: string;
  /**
   * 所要時間（ミリ秒）。時刻を持たない記録（工程の秒だけを残す記録）で使う。渡すと `startedAt` / `finishedAt`
   * より優先する。
   */
  durationMs?: number | null;
  /** 短い計測値（例:「LLM 1 回」）。一覧では名前の後ろの括弧に `detail` と並べて出す（#2265。実行の詳細と同じ）。 */
  meta?: string;
  /** 入れ子の深さ（0 が最上位。1 以上は字下げする。親の工程の中の工程。最大 3 段まで字下げ）。 */
  level?: number;
  /**
   * 段を開いて見る詳細（見出しと値・行・SQL や JSON の箱。#2294）。中身があれば一覧の行に開閉のボタンが付き、
   * 既定で閉じる。実行の詳細の `RunTimelineStep.details` と同じ形。
   */
  details?: RunStepDetails;
}

/** 文言。製品の i18n で上書きできる。 */
export interface ChatProgressLabels {
  /**
   * 実行中の見出し（全体の状態。例:「処理中（3 ステップ完了）」）。`current` は一覧を閉じているときだけ渡す今の段階の
   * 名前（開いているときは一覧の最後の行に出るので重ねない）。
   */
  running: (completed: number, current: string | null) => string;
  /** 完了後の 1 行（例:「処理の経過（4 ステップ・12 秒）」）。 */
  summary: (count: number, duration: string) => string;
  /** 段階の一覧の読み上げの名前。 */
  steps: string;
  /** 処理全体の経過時間の見出し（実行中の見出しの右に出す。#1176）。 */
  elapsed: string;
  /** 今の段階の遅延の案内（今の段階の経過時間で判断し、一覧の最後の行の下に出す。#1176）。 */
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
  /** 段の詳細の長さを切ったときの印（#2294）。 */
  detailsTruncated: string;
  /** 失敗の後に実行しなかった段の状態（完了後の一覧で「待機中」の代わりに出す。#2342）。 */
  notRun: string;
}

export { formatChatProgressDuration };

export const DEFAULT_CHAT_PROGRESS_LABELS: ChatProgressLabels = {
  running: (completed, current) =>
    `${completed > 0 ? `処理中（${completed} ステップ完了）` : "処理中"}${current ? `・${current}` : ""}`,
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
  detailsTruncated: "長い内容は先頭だけを表示しています。",
  notRun: "未実行",
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
  /**
   * 更新が途絶え、状態を取り直している（#1160）。今の段階の行に「接続を確認しています」を出す。
   * 製品は `useChatProgressTracker` の結果（`progressProps`）をそのまま渡す。
   */
  reconnecting?: boolean;
  /**
   * 段階の一覧の対象（送信・Run・ジョブの ID など。#1358）。処理中は一度出した段階を消さず、状態を戻さない
   * （`useChatProgressSteps`）。この値が変わったら（と終端で）渡された一覧で作り直す。
   * `useChatProgressTracker` の `progressProps` は追う対象の `key` を渡す。省略時は、この表示の間は同じ対象。
   */
  progressKey?: string | null;
  labels?: Partial<ChatProgressLabels>;
  /**
   * 開いた中身の末尾（タイムラインの後ろ）に出す内容（#2325。RAG の「処理の詳細」など、処理の経過に属する技術的な
   * 詳細）。実行中・完了後のどちらでも一覧と一緒に開閉し、閉じているときは出ない。段階が 0 件のときは何も出さない
   * （製品が単独で出す）。
   */
  footer?: ReactNode;
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

export interface UseChatProgressStepsOptions {
  /** 対象（送信・Run・ジョブの ID など）。変わったら作り直す。 */
  key?: string | null;
  /** 配信の最新の状態から作った段階の一覧。 */
  steps: readonly ChatProgressStep[];
  /** 処理中か。省略時は段階から決める（`isChatProgressActive`）。false（終端）なら渡された一覧で確定する。 */
  active?: boolean;
}

/**
 * 処理中は一度出した段階を消さず、状態を戻さない段階の一覧（3 製品共通。#1358）。規則は
 * `reduceChatProgressSteps`（純粋な関数）が持ち、この hook は描画のたびにそれを呼ぶだけ。`ChatProgress` が
 * 内部で呼ぶので、製品は `ChatProgress` に配信の最新の一覧を渡せばよい。
 */
export function useChatProgressSteps({ key = null, steps, active }: UseChatProgressStepsOptions): ChatProgressStep[] {
  // 描画に使った（画面に出した）状態。描画の中では書き換えず、確定（commit）の後に進める。
  const committed = useRef<ChatProgressStepsState | null>(null);
  const state = reduceChatProgressSteps(committed.current, {
    key,
    steps,
    active: active ?? isChatProgressActive(steps),
  });
  useLayoutEffect(() => {
    committed.current = state;
  });
  return state.steps;
}

function stepDurationMs(step: ChatProgressStep): number | null {
  if (typeof step.durationMs === "number" && Number.isFinite(step.durationMs)) return Math.max(0, step.durationMs);
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
  // 時刻を持たない記録（所要時間だけ）は、最上位の段階の所要時間の和。
  if (steps.every((step) => step.startedAt === undefined && step.finishedAt === undefined)) {
    const top = steps.filter((step) => !step.level).map(stepDurationMs).filter((v): v is number => v !== null);
    return top.length > 0 ? top.reduce((sum, value) => sum + value, 0) : null;
  }
  const starts = steps.map((step) => operationTimestampMs(step.startedAt)).filter((v): v is number => v !== null);
  const ends = steps.map((step) => operationTimestampMs(step.finishedAt)).filter((v): v is number => v !== null);
  if (starts.length === 0 || ends.length === 0) return null;
  return Math.max(0, Math.max(...ends) - Math.min(...starts));
}

function lastRunningStep(steps: readonly ChatProgressStep[]): ChatProgressStep | undefined {
  for (let index = steps.length - 1; index >= 0; index -= 1) {
    if (steps[index].status === "running") return steps[index];
  }
  return undefined;
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

/**
 * チャットの段階を処理の経過の行（`RunTimeline` の段）にする（#2265）。実行の詳細の写し（Agent など）も同じ関数を
 * 使い、補足（`detail`）と計測値（`meta`。LLM の回数など）を名前の後ろの括弧に並べ、入れ子の深さ（`level`）を落とさない。
 */
export function chatProgressTimelineStep(step: ChatProgressStep): RunTimelineStep {
  const detail = [step.detail, step.meta].filter(Boolean).join("・");
  const result: RunTimelineStep = { id: step.id, label: step.label, status: step.status };
  if (step.startedAt) result.startedAt = step.startedAt;
  if (step.finishedAt) result.finishedAt = step.finishedAt;
  if (typeof step.durationMs === "number" && Number.isFinite(step.durationMs)) result.durationMs = Math.max(0, step.durationMs);
  if (detail) result.detail = detail;
  if (step.level) result.level = step.level;
  if (hasRunStepDetails(step.details)) result.details = step.details;
  return result;
}

/**
 * 段階の一覧（開いたときの中身）。実行の詳細と同じ `RunTimeline` で描く（#2265。3 製品のチャットと実行の詳細で
 * 行の形をそろえる）。完了後は時間の線を出し、実行中は出さない（全体の長さが伸び続け、線が毎回伸び縮みする）。
 */
function StepList({
  steps,
  bars,
  labels,
  testId,
}: {
  steps: ChatProgressStep[];
  bars: boolean;
  labels: ChatProgressLabels;
  testId?: string;
}) {
  return (
    <RunTimeline
      steps={steps.map(chatProgressTimelineStep)}
      showSummary={false}
      showBars={bars}
      size="sm"
      formatDuration={labels.formatDuration}
      stepTestIdPrefix={testId}
      labels={{
        timeline: labels.steps,
        // 時刻の無い段は所要時間を空にする（チャットの控えめな表示で「—」を並べない）。
        notRecorded: "",
        // 終わった後の一覧の待機中の段は、実行しなかった段（失敗・停止の後）なので「未実行」と出す（#2342）。
        stepStatus: { ...labels.status, waiting: labels.status.running, pending: labels.notRun },
        stepDetailsTruncated: labels.detailsTruncated,
      }}
      testId={testId ? `${testId}-timeline` : undefined}
    />
  );
}

/** 処理全体の経過時間（実行中の見出しの右。段階が変わっても 0 に戻さない。#1176）。 */
function TotalTimer({
  startedAt,
  labels,
  testId,
}: {
  startedAt: number | null;
  labels: ChatProgressLabels;
  testId?: string;
}) {
  // key を固定し、段階が変わっても数え直さない（開始の時刻が無いときは表示の開始から数える）。
  const total = useOperationTiming({
    active: true,
    operationKey: "chat-progress-total",
    startedAt: startedAt ?? undefined,
  });
  return (
    <span
      className="ml-auto inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap pl-3 text-xs font-normal text-fg-muted"
      role="timer"
      aria-live="off"
      aria-label={`${labels.elapsed} ${total.elapsedClock}`}
      data-testid={testId ? `${testId}-timer` : undefined}
    >
      <Clock3 size={14} aria-hidden="true" />
      <span>{labels.elapsed}</span>
      <span className="min-w-[3.25rem] text-right tabular-nums text-fg">{total.elapsedClock}</span>
    </span>
  );
}

/**
 * 実行中の段階の行（一覧の最後の行。スピナー・段階の名前・今の段階の経過時間・遅延 / 再接続の案内）。
 *
 * 完了した段階の行（`RunTimeline` の段）と同じ列・字下げで描き、段階が終わると同じ位置で完了の行に変わる
 * （行が跳ばない）。今の段階の経過時間は秒の単位で数え、遅延の案内もこの経過時間で判断する（#1176）。
 */
function CurrentStepRow({
  step,
  slowAfterMs,
  reconnecting,
  labels,
  testId,
}: {
  /** 今の段階。終端でないのに実行中・待機中の段階が無いときは null（「処理を続けています」を出す）。 */
  step: ChatProgressStep | null;
  slowAfterMs: number;
  reconnecting: boolean;
  labels: ChatProgressLabels;
  testId?: string;
}) {
  const current = useOperationTiming({
    active: true,
    operationKey: step ? `step:${step.id}` : "step:working",
    startedAt: step?.startedAt,
    slowAfterMs,
  });
  const label = step ? step.label : labels.working;
  const detail = step ? [step.detail, step.meta].filter(Boolean).join("・") : "";
  return (
    <div
      className="grid min-w-0"
      data-testid={testId ? `${testId}-current` : undefined}
      data-status="running"
      data-step-id={step?.id ?? ""}
      data-slow={current.slow ? "true" : "false"}
      data-reconnecting={reconnecting ? "true" : "false"}
    >
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3">
        <div className={cn("flex min-w-0 items-start gap-2 text-xs leading-5", runTimelineStepIndent(step?.level))}>
          <span className="flex h-5 shrink-0 items-center">
            <Spinner size={14} className="text-accent-fg" />
          </span>
          <span className="min-w-0 break-words [overflow-wrap:anywhere]">
            <span className="font-medium text-fg">{label}</span>
            {detail ? <span className="text-fg-muted">{`（${detail}）`}</span> : null}
            <span className="sr-only">{` ${labels.status.running}`}</span>
          </span>
        </div>
        <span
          className="whitespace-nowrap text-right text-xs leading-5 text-fg-muted tabular-nums"
          data-testid={testId ? `${testId}-current-elapsed` : undefined}
        >
          {labels.formatDuration(Math.floor(current.elapsedMs / 1000) * 1000)}
        </span>
      </div>
      {/*
        遅延の案内の行は最初から高さを予約する（#902 と同じ。後から行を足して一覧を動かさない）。
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
 * - 実行中: 見出しの行は全体の状態だけ（「処理中（N ステップ完了）」と処理全体の経過時間。経過時間は段階が
 *   変わっても 0 に戻さない。#1176）。開いた中身は 1 本のタイムラインで、完了した段階を終わった順に並べ、その後ろの
 *   最後の行に実行中の段階（スピナー・今の段階の経過時間・遅延 / 再接続の案内）を出す（#2309。時間の順に上から
 *   下へ。段階が終わると同じ位置で完了の行に変わる）。閉じたときは見出しに今の段階の名前を添える。終端でないのに
 *   実行中・待機中の段階が無いときも、最後の行に「処理を続けています」を出す。
 * - 完了後: 「処理の経過（N ステップ・M 秒）」の 1 行に閉じる（#2118 / #2265。3 製品で同じ。会話の履歴から開いた
 *   往復も閉じる）。押すと開き、実行の詳細と同じ `RunTimeline`（字下げ・時間の線・右の所要時間）で出す。失敗は
 *   開いたまま。
 * - `footer` は開いた中身の末尾（タイムラインの後ろ）に出し、一覧と一緒に開閉する（#2325。処理の経過に属する
 *   技術的な詳細を、回答の本文との間に独立して置かない）。
 * - 入れ子の工程は `level` で字下げし、親と子が同時に実行中なら最後に始まった段階を今の段階にする。計測値
 *   （`meta`。LLM の回数など）は名前の後ろの括弧、右の列は失敗とスキップの文字・所要時間。
 * - 段階の切り替わりは polite で読み上げる（経過時間の毎秒の更新は読み上げない）。状態はアイコンと文字で示す。
 * - 動くスピナーは今の段階の 1 つだけ（同じ処理のスピナーは 1 つ。messaging.md §3.7）。
 * - 処理中は一度出した段階を消さず、状態を戻さない（古い snapshot・段階が実行中に戻る組み立てでも、
 *   完了した段階の一覧が揺れない。`useChatProgressSteps`。#1358）。終端・`progressKey` の変化で作り直す。
 *
 * SQL 生成の画面のような詳細な工程の表示（NL2SQL の WorkflowProgressStrip）より情報を絞る。
 */
export function ChatProgress({
  steps: incomingSteps,
  active: activeProp,
  elapsedMs,
  startedAt,
  slowAfterMs = DEFAULT_SLOW_AFTER_MS,
  reconnecting = false,
  progressKey = null,
  labels: labelOverrides,
  footer,
  className,
  testId,
}: ChatProgressProps) {
  const labels = { ...DEFAULT_CHAT_PROGRESS_LABELS, ...labelOverrides };
  // 終端の判定は配信の最新の一覧で行い、表示する一覧は処理中に段階を消さない・戻さない（#1358）。
  const active = activeProp ?? isChatProgressActive(incomingSteps);
  const steps = useChatProgressSteps({ key: progressKey, steps: incomingSteps, active });
  const failed = steps.some((step) => step.status === "failed");
  const state: ProgressState = active ? "running" : failed ? "failed" : "done";
  // 実行中の段階が無い（段階の間・開始前）ときは、次に進む段階を今の段階として出す。
  // 入れ子の工程（親と子が同時に実行中）は、最後に始まった実行中の段階（いちばん内側）を今の段階にする。
  const current = active
    ? lastRunningStep(steps) ?? steps.find((step) => step.status === "pending")
    : undefined;
  const finished = steps.filter((step) => step.status === "done" || step.status === "failed" || step.status === "skipped");
  // 一覧の開閉（3 製品で同じ。製品は選ばない。#2265）。実行中は開き（何をしているかを見せる。#2096）、完了したら
  // 閉じる（回答の本文を主にする。#2118）。完了した状態で出したとき（会話の履歴から開いた往復）も閉じる。失敗は
  // どこで止まったかを見せるため開いたまま。利用者が開閉したら同じ表示の間はその状態を保ち、実行中と終端が
  // 切り替わったら既定に戻す（次の実行で開き直す）。
  const [summaryOpen, setSummaryOpen] = useState<boolean | null>(null);
  const [wasActive, setWasActive] = useState(active);
  if (wasActive !== active) {
    setWasActive(active);
    setSummaryOpen(null);
  }
  const summaryIsOpen = summaryOpen ?? (active || failed);
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
        <Disclosure
          variant="plain"
          size="sm"
          summary={
            <SummaryText icon={ListChecks}>
              {labels.running(finished.length, summaryIsOpen ? null : currentLabel)}
            </SummaryText>
          }
          meta={<TotalTimer startedAt={overallStartedAt} labels={labels} testId={testId} />}
          open={summaryIsOpen}
          onOpenChange={setSummaryOpen}
          summaryProps={{ "data-testid": testId ? `${testId}-running` : undefined }}
          summaryClassName="w-full text-fg-muted"
        >
          {/* 1 本のタイムライン: 完了した段階の後ろに、実行中の段階を最後の行として出す（#2309）。 */}
          <div className="grid min-w-0 gap-1.5">
            {finished.length > 0 ? <StepList steps={finished} bars={false} labels={labels} testId={testId} /> : null}
            {/* 終端になるまで今の行を必ず出す（段階の外で処理が続くときは「処理を続けています」。#1176）。 */}
            <CurrentStepRow
              step={current ?? null}
              slowAfterMs={slowAfterMs}
              reconnecting={reconnecting}
              labels={labels}
              testId={testId}
            />
            {footer}
          </div>
        </Disclosure>
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
          {footer ? (
            <div className="grid min-w-0 gap-1.5">
              <StepList steps={steps} bars labels={labels} testId={testId} />
              {footer}
            </div>
          ) : (
            <StepList steps={steps} bars labels={labels} testId={testId} />
          )}
        </Disclosure>
      )}
    </div>
  );
}
