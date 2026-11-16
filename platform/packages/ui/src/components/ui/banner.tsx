import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

import { Button } from "./button";
import { toneDivide, toneIcon, toneRank, toneRole, toneSurface, type FeedbackTone } from "./feedback-tone";
import { MessageText } from "./message-text";

/**
 * Banner（ページ/セクション常設の状況提示）。
 * 「設定未完了」「縮退モード」等の状況に使う。一時的な成功は Toast を使う。
 * 同じ区画（カード・フォーム・1 つの回答）に案内が 2 つ以上あるときは、`Banner` を並べず
 * `CombinedBanner` で 1 つの枠にまとめる（UX 契約 messaging.md §3.4.3）。
 *
 * i18n はパッケージに持ち込まないため、閉じるボタンの aria ラベルは `dismissLabel` で注入する
 * （未指定時は日本語の既定値「閉じる」）。
 * 閉じるボタンは Toast と同じ共有 `Button`（ghost・iconOnly・44px）。色はトーンの文字色に合わせ、
 * ホバーの面はトーンの淡い地の上で見える `fg` の薄い重ねにする。
 *
 * `progress` は `action` の直下（同じ Banner の中）に出す、その操作で始めた処理の進行
 * （`ProcessingIndicator` など）。案内の中のボタンで始めた処理の経過時間を、押したボタンから離れた
 * ページの先頭・末尾に出さない（UX 契約 messaging.md §10.1「起点の直下」。#2023）。
 */
export function Banner({
  severity,
  title,
  children,
  action,
  progress,
  onDismiss,
  dismissLabel = "閉じる",
  className,
}: {
  severity: FeedbackTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  /** `action` の直下に出す、その操作で始めた処理の進行（`ProcessingIndicator` など）。 */
  progress?: ReactNode;
  /** 指定すると閉じる × を表示する。 */
  onDismiss?: () => void;
  /** 閉じるボタンの aria-label（i18n 文字列を注入。既定「閉じる」）。 */
  dismissLabel?: string;
  className?: string;
}) {
  return (
    <BannerFrame
      severity={severity}
      className={className}
      trailing={
        onDismiss ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            iconOnly
            touchTarget
            icon={X}
            onClick={onDismiss}
            aria-label={dismissLabel}
            className="-mr-2 -mt-2 shrink-0 text-current hover:enabled:bg-fg/5 active:enabled:bg-fg/10"
          />
        ) : null
      }
    >
      <BannerContent title={title} action={action} progress={progress}>
        {children}
      </BannerContent>
    </BannerFrame>
  );
}

/** `CombinedBanner` の 1 つの項目（1 つの事柄の見出し・原因と対処・操作）。 */
export interface CombinedBannerItem {
  /** 項目の key（同じ枠の中で一意）。 */
  id: string;
  /** 項目の重さ。枠の色・アイコン・role は、最も重い項目のものになる。 */
  severity: FeedbackTone;
  /** 何が起きているか（翻訳済み）。最も重い項目の見出しが枠の見出しになる。見出しの `StatusBadge` の文字を繰り返さない。 */
  title?: string;
  /** 原因と対処（翻訳済み）。同じ文をほかの項目に重ねない。 */
  children?: ReactNode;
  /** この項目の操作（導線・再試行など）。 */
  action?: ReactNode;
  /** `action` で始めた処理の進行（`Banner` の `progress` と同じ）。 */
  progress?: ReactNode;
  /** 項目の要素の `data-testid`。 */
  testId?: string;
  /** 項目の要素の DOM の `id`（押せなくした操作の `aria-describedby` の先など）。 */
  elementId?: string;
}

export interface CombinedBannerProps {
  /** 区画の案内。偽の値（条件に合わない項目）と、見出しも本文も無い項目は出さない。 */
  items: ReadonlyArray<CombinedBannerItem | null | false | undefined>;
  className?: string;
  testId?: string;
}

/** 出す項目を重い順（同じ重さは渡した順）に並べる。見出しも本文も無い項目は捨てる（messaging.md §9 P4）。 */
export function combinedBannerItems(items: CombinedBannerProps["items"]): CombinedBannerItem[] {
  return items
    .filter((item): item is CombinedBannerItem => Boolean(item && (item.title || item.children)))
    .map((item, index) => ({ item, index }))
    .sort((a, b) => toneRank[b.item.severity] - toneRank[a.item.severity] || a.index - b.index)
    .map(({ item }) => item);
}

/**
 * 1 つの区画（カード・フォーム・1 つの回答）の案内を 1 つの枠にまとめる Banner（UX 契約 messaging.md §3.4.3）。
 *
 * - 色・アイコン・role は最も重い項目のもの（danger > warning > info > success）。role（`alert` / `status`）は
 *   外側の枠の 1 か所だけに付け、項目には付けない（読み上げを重ねない）。
 * - 項目は重い順（同じ重さは渡した順）。最も重い項目の見出しが枠の先頭に来る。
 * - 項目が 1 つなら `Banner` と同じ形。2 つ以上なら、枠の中に項目を区切り線で並べる（`<ul>`）。各項目は
 *   自分の見出し・原因と対処・操作を 1 回だけ持つ。
 * - 出す項目が無ければ何も描かない。
 */
export function CombinedBanner({ items, className, testId }: CombinedBannerProps) {
  const visible = combinedBannerItems(items);
  const top = visible[0];
  if (!top) return null;
  const severity = top.severity;
  return (
    <BannerFrame severity={severity} className={className} testId={testId}>
      {visible.length === 1 ? (
        <div id={top.elementId} data-testid={top.testId}>
          <BannerContent title={top.title} action={top.action} progress={top.progress}>
            {top.children}
          </BannerContent>
        </div>
      ) : (
        <ul className={cn("divide-y", toneDivide[severity])}>
          {visible.map((item) => (
            <li key={item.id} id={item.elementId} data-testid={item.testId} className="min-w-0 py-2.5 first:pt-0 last:pb-0">
              <BannerContent title={item.title} action={item.action} progress={item.progress}>
                {item.children}
              </BannerContent>
            </li>
          ))}
        </ul>
      )}
    </BannerFrame>
  );
}

/** 枠（トーンの面・アイコン・role）。 */
function BannerFrame({
  severity,
  className,
  testId,
  trailing,
  children,
}: {
  severity: FeedbackTone;
  className?: string;
  testId?: string;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  const Icon = toneIcon[severity];
  return (
    <div
      role={toneRole(severity)}
      data-surface-tint={severity}
      data-testid={testId}
      className={cn(
        "pr-message-text flex items-start gap-2.5 rounded-lg border px-3.5 py-3 text-sm",
        toneSurface[severity],
        className
      )}
    >
      {/* 状態のアイコンは文字の 1 行目の中心にそろえる（行の高さの箱。#2371）。 */}
      <span className="pr-icon-line leading-relaxed">
        <Icon size={16} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">{children}</div>
      {trailing}
    </div>
  );
}

/** 見出し → 本文 → 操作 → 進行。 */
function BannerContent({
  title,
  children,
  action,
  progress,
}: {
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  progress?: ReactNode;
}) {
  return (
    <>
      {title ? (
        <p className="font-medium leading-relaxed">
          <MessageText text={title} />
        </p>
      ) : null}
      {children ? (
        <div className={cn("leading-relaxed text-fg/90", title && "mt-0.5")}>
          {typeof children === "string" ? <MessageText text={children} /> : children}
        </div>
      ) : null}
      {action ? <div className="mt-2 flex flex-wrap gap-2">{action}</div> : null}
      {progress ? <div className="mt-2 min-w-0">{progress}</div> : null}
    </>
  );
}
