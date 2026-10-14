import type { ReactNode } from "react";
import {
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListSkeleton,
  TimedLoadingState,
  type ProcessingActivityIcon,
} from "@production-ready/ui";

import { formatDateTime } from "@/lib/format";
import { QuestionText } from "./QuestionText";

/**
 * 実行履歴とフィードバック管理のフィードバック履歴で共有する、履歴の行の一覧（#1764）。
 *
 * どちらも SQL の履歴 1 件を 1 行にし、1 行目に質問（2 行で折り返す）、2 行目に補足（実行ユーザーなど）、
 * 最後の行に日時・実行エンジン・処理時間・評価のバッジを並べる。質問とバッジを同じ行に並べないので、
 * 横幅が狭い分割表示でも質問が切り詰められない。一覧は md 未満 5 行・md 以上 8 行の高さで中を縦スクロール
 * にし（INFORMATION_LIST_SCROLL_CLASS）、ページングは一覧の直下の共通の `CursorPagination` が持つ。
 */
export function HistoryRowList({
  label,
  header,
  children,
  surfaceTestId,
  scrollTestId,
  listTestId,
}: {
  /** 一覧（ul）の aria-label。 */
  label: string;
  /** 一覧の上の帯（並べ替えのボタンなど）。 */
  header?: ReactNode;
  children: ReactNode;
  surfaceTestId?: string;
  scrollTestId?: string;
  listTestId?: string;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-surface" data-testid={surfaceTestId}>
      {header}
      <div className={`overflow-x-hidden ${INFORMATION_LIST_SCROLL_CLASS}`} data-testid={scrollTestId}>
        <ul className="divide-y divide-border/70" aria-label={label} data-testid={listTestId}>
          {children}
        </ul>
      </div>
    </div>
  );
}

export function HistoryListRow({
  question,
  createdAt,
  engine,
  selected,
  onSelect,
  ariaLabel,
  ariaDescribedBy,
  meta,
  badges,
  footer,
  testId,
  questionTestId,
}: {
  question: string;
  /** 行の日時（ISO 8601）。実行履歴は実行日時、フィードバック履歴は評価の日時。 */
  createdAt: string;
  /** 実行エンジンの表示名。 */
  engine: string;
  selected: boolean;
  onSelect: () => void;
  ariaLabel?: string;
  ariaDescribedBy?: string;
  /** 質問の下の補足の行（実行ユーザーなど）。 */
  meta?: ReactNode;
  /** 日時と実行エンジンの後ろに並べるバッジ。 */
  badges: ReactNode;
  /** 最後の行（コメントなど）。 */
  footer?: ReactNode;
  /** 行のボタンの data-testid。 */
  testId?: string;
  questionTestId?: string;
}) {
  return (
    <li className="min-w-0">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-describedby={ariaDescribedBy}
        aria-current={selected ? "true" : undefined}
        // 淡青面（bg-accent-subtle）の上の副次テキストを 1 段深くする（DataTable の選択行と同じ。#1595・WCAG 1.4.3）。
        data-surface-tint={selected ? "accent" : undefined}
        data-testid={testId}
        className={`grid min-h-20 w-full min-w-0 gap-2 border-l-2 px-3 py-2.5 text-left transition-colors ${
          selected ? "border-l-accent-fg bg-accent-subtle" : "border-l-transparent hover:bg-surface-hover"
        }`}
        onClick={onSelect}
      >
        <QuestionText
          value={question}
          variant="select"
          maxLines={2}
          className="font-medium text-fg"
          testId={questionTestId}
        />
        {meta}
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-sans text-xs tabular-nums text-fg">{formatDateTime(createdAt)}</span>
          <span className="min-w-0 break-words text-xs text-fg-muted [overflow-wrap:anywhere]">{engine}</span>
          {badges}
        </span>
        {footer}
      </button>
    </li>
  );
}

/** 履歴の行の一覧の読み込み中（初回）。経過時間は 1 か所だけ出し、一覧の形の Skeleton で覆う。 */
export function HistoryRowListSkeleton({
  label,
  operationKey,
  testId,
  skeletonTestId,
  activityIcon,
  children,
}: {
  label: string;
  operationKey: string;
  testId: string;
  skeletonTestId: string;
  activityIcon?: ProcessingActivityIcon;
  /** Skeleton の前に置く要素（見出しの sr-only など）。 */
  children?: ReactNode;
}) {
  return (
    <TimedLoadingState
      label={label}
      operationKey={operationKey}
      placement="panel"
      className="content-start"
      testId={testId}
      framed={false}
      activityIcon={activityIcon}
    >
      {children}
      <ListSkeleton rows={INFORMATION_TABLE_VISIBLE_ROWS} rowClassName="h-20" testId={skeletonTestId} />
    </TimedLoadingState>
  );
}
