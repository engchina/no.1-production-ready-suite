import { Info } from "lucide-react";

import { cn } from "../../lib/utils";
import { TEXT_ICON_SIZE } from "../ui/text-icon";

/*
 * 処理の経過の段の中身（#2294）。実行の詳細とチャットの「処理の経過」（`RunTimeline`）で、段を開いて見る詳細の
 * 1 つの形。製品は自分の記録（RAG の回答の記録の工程・NL2SQL のジョブ・Agent のツールの呼び出し）をこの形に
 * 写すだけで、描き方（見出しと値・行・コードの箱・省略の印）は部品が持つ。
 *
 * - `fields`: 見出しと値（エンジン・行数・選んだ条件など）。
 * - `lines`: 補足の行（工程の記録の文など）。
 * - `code`: 長い値（SQL・ツールの引数と結果の JSON）。箱の中で縦横にスクロールし、ページを伸ばさない。
 * - `truncated`: 送る側で長さを切った（内容の一部だけ）。印を文字で出す。
 *
 * 秘密の値（token・API キー・パスワード）は送る側で入れない（backend の `pr_backend_core.chat_progress` の
 * `ChatProgressStepDetails` が長さを切り、秘密らしい値を伏せる）。
 */

export interface RunStepDetailField {
  /** 見出し（翻訳済み）。 */
  label: string;
  value: string;
  /** 識別子・SQL の断片などを等幅で出す。 */
  mono?: boolean;
}

export interface RunStepDetailCode {
  /** 箱の見出し（翻訳済み。例「引数」「結果」「SQL」）。 */
  label?: string;
  /** 中身の種類（読み上げと等幅の表示。色分けはしない）。 */
  language?: "json" | "sql" | "text";
  text: string;
}

export interface RunStepDetails {
  fields?: readonly RunStepDetailField[];
  lines?: readonly string[];
  code?: readonly RunStepDetailCode[];
  /** 送る側で長さを切った（内容の一部だけ）。 */
  truncated?: boolean;
}

/** 中身のある詳細か（無ければ段に開閉のボタンを付けない）。 */
export function hasRunStepDetails(details: RunStepDetails | null | undefined): details is RunStepDetails {
  if (!details) return false;
  return Boolean(
    details.fields?.some((field) => field.label && field.value) ||
      details.lines?.some((line) => line) ||
      details.code?.some((block) => block.text)
  );
}

/** 2 つの詳細が同じか（処理中の段の一覧の差分の判定）。 */
export function sameRunStepDetails(a: RunStepDetails | undefined, b: RunStepDetails | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

/** 画面で整形するときの JSON の上限（文字数）。backend の上限と同じ。 */
export const RUN_STEP_DETAILS_CODE_MAX_CHARS = 4000;

/**
 * 値を整形した JSON にし、上限を超えたら先頭だけにする（Agent のツールの結果など、画面が持っている値を写すとき）。
 */
export function runStepDetailsJson(
  value: unknown,
  maxChars: number = RUN_STEP_DETAILS_CODE_MAX_CHARS
): { text: string; truncated: boolean } {
  let text: string;
  if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value, null, 2) ?? "";
    } catch {
      text = String(value);
    }
  }
  if (text.length <= maxChars) return { text, truncated: false };
  return { text: `${text.slice(0, maxChars)}…`, truncated: true };
}

export interface RunStepDetailsLabels {
  /** 長さを切ったときの印。 */
  truncated: string;
}

export const DEFAULT_RUN_STEP_DETAILS_LABELS: RunStepDetailsLabels = {
  truncated: "長い内容は先頭だけを表示しています。",
};

export interface RunStepDetailsViewProps {
  details: RunStepDetails;
  labels?: Partial<RunStepDetailsLabels>;
  className?: string;
  testId?: string;
}

/**
 * 段の詳細を描く（`RunTimeline` の行を開いた中身）。見出しと値 → 行 → コードの箱 → 省略の印の順。
 * コードの箱は高さを止めて中でスクロールし、キーボードでもスクロールできるよう focus を受ける。
 */
export function RunStepDetailsView({ details, labels: labelsInput, className, testId }: RunStepDetailsViewProps) {
  const labels = { ...DEFAULT_RUN_STEP_DETAILS_LABELS, ...labelsInput };
  const fields = (details.fields ?? []).filter((field) => field.label && field.value);
  const lines = (details.lines ?? []).filter(Boolean);
  const code = (details.code ?? []).filter((block) => block.text);
  return (
    <div className={cn("grid min-w-0 gap-2 text-xs leading-5", className)} data-run-step-details="" data-testid={testId}>
      {fields.length ? (
        <dl className="grid min-w-0 gap-x-3 gap-y-1 @md:grid-cols-[max-content_minmax(0,1fr)]">
          {fields.map((field, index) => (
            <div key={`${field.label}-${index}`} className="contents">
              <dt className="min-w-0 break-words text-fg-muted [overflow-wrap:anywhere]">{field.label}</dt>
              <dd
                className={cn(
                  "min-w-0 whitespace-pre-wrap break-words text-fg [overflow-wrap:anywhere]",
                  field.mono && "font-mono"
                )}
              >
                {field.value}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {lines.length ? (
        <ul className="grid min-w-0 list-disc gap-0.5 pl-4 text-fg">
          {lines.map((line, index) => (
            <li key={`${index}-${line}`} className="break-words [overflow-wrap:anywhere]">
              {line}
            </li>
          ))}
        </ul>
      ) : null}
      {code.map((block, index) => (
        <div key={`${block.label ?? "code"}-${index}`} className="grid min-w-0 gap-1">
          {block.label ? <p className="text-fg-muted">{block.label}</p> : null}
          {/* 長い SQL・JSON は箱の中でスクロールする（ページの横あふれを作らない）。focus を受けてキーで動かせる。 */}
          <pre
            className="max-h-64 w-full min-w-0 max-w-full overflow-auto rounded-md border border-border bg-surface p-2 font-mono text-xs leading-5 text-fg"
            tabIndex={0}
            aria-label={block.label}
            data-language={block.language ?? "text"}
          >
            {block.text}
          </pre>
        </div>
      ))}
      {details.truncated ? (
        <p className="flex min-w-0 items-start gap-1.5 text-fg-muted" data-run-step-details-truncated="">
          <span className="pr-icon-line">
            <Info size={TEXT_ICON_SIZE.xs} aria-hidden="true" />
          </span>
          <span className="min-w-0">{labels.truncated}</span>
        </p>
      ) : null}
    </div>
  );
}
