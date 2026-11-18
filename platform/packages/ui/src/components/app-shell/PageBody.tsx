import type { ComponentProps, ReactNode } from "react";

import { cn } from "../../lib/utils";

/**
 * 本文の計測コンテナ（最大幅 --content-max-width = 1440px で中央寄せ、左右ガター 1rem / 1.5rem / 2rem）。
 * PageHeader の中身と PageBody で共有し、ワイドモニタでもタイトルと本文の左端を揃える。
 * `wide` は PageHeader と PageBody で必ず同じ値にする。
 */
export function measureClass(wide: boolean) {
  // 狭い画面ほどガターを詰める。lg（1024px）以上は 2rem で、ワイドモニタでの左端の揃えは変わらない。
  return cn("w-full min-w-0 px-4 sm:px-6 lg:px-8", wide ? "max-w-none" : "mx-auto max-w-[var(--content-max-width)]");
}

/**
 * md 以上で、通知（Toast）が出ている間だけページの末尾に空ける下の余白（#5388。#5354 の常時の 14rem を置き換えた）。
 * 通知は md 以上で画面の右下（下端から 8rem）に出る。`<Toaster/>` が通知の表示中だけ通知の領域の上端から画面の下端までの
 * 高さ + 1rem を `--toast-reserve-bottom` に書き（`useToastBottomReserve`）、ここでその高さを下の余白にする。スクロールしきったとき
 * 通知の範囲の下に内容（表の行メニュー・末尾のボタン）を置かない。通知を積めばその高さまで伸び、通知が無いときは変数が無く
 * 1.5rem（`py-6`）に戻る。ページは自動でスクロールしない。md 未満は通知が上端に出るので空けない（1.5rem のまま）。
 * 下端に固定された入力欄（`ChatLayout`。埋め込みを除く）を持つ本文は、通知が入力欄の上に出る（`--toast-clear-bottom`。#2103）
 * ので空けない。
 */
const PAGE_BODY_TOAST_RESERVE_CLASS =
  "md:pb-[max(var(--space-6),var(--toast-reserve-bottom,0px))] md:has-[[data-chat-layout]:not([data-chat-layout-embedded])]:pb-6";

/** `PageBody` の通常の下の余白（`py-6` = 1.5rem。rem の数）。 */
const PAGE_BODY_RESTING_BOTTOM_REM = 1.5;

/**
 * `PageBody` の通常の下の余白（px）。通知の表示中だけ足す余白（`--toast-reserve-bottom`）を含めない（#5388）。
 * 表・一覧の列の高さを画面に合わせて測るときに使う（通知の表示中に測り直しても低くならない）。製品の `pb-0` などで
 * 通常の余白より小さいときはその値。
 */
export function pageBodyRestingBottomInset(body: Element) {
  const rem = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const padding = Number.parseFloat(getComputedStyle(body).paddingBottom) || 0;
  return Math.min(padding, PAGE_BODY_RESTING_BOTTOM_REM * rem);
}

/**
 * PageHeader の直後に置く本文コンテナ（左右ガター 2rem / 上 1.5rem / セクション間 1.5rem。
 * 下は 1.5rem。md 以上は通知の表示中だけ通知の領域の高さまで広げる。`PAGE_BODY_TOAST_RESERVE_CLASS`）。
 * 手書きの `<div style={{ padding: "1.5rem 2rem" }}>` を置き換える。
 * 表を画面幅いっぱいに出す画面だけ `wide`（PageHeader にも同じ値を渡す）。
 */
export function PageBody({ wide = false, className, children, ...props }: ComponentProps<"div"> & { wide?: boolean }) {
  // grid item は既定で min-width: auto になり、横スクロールする表などが main の外へはみ出すため min-w-0 を付ける。
  return (
    <div
      {...props}
      data-page-body=""
      className={cn(measureClass(wide), "grid content-start gap-6 py-6 [&>*]:min-w-0", PAGE_BODY_TOAST_RESERVE_CLASS, className)}
    >
      {children}
    </div>
  );
}

/**
 * 見出し付きのひとまとまり。見出しは 16px / 600（ページタイトル 20px とカード見出し 14px の間の段）。
 * `titleId` を渡すと見出しにその id を付け、節の名前（`aria-labelledby`）を見出しにする（#4558）。
 */
export function Section({
  title,
  titleId,
  description,
  actions,
  className,
  children,
  ...props
}: Omit<ComponentProps<"section">, "title"> & {
  title?: string;
  /** 見出しの id。節の `aria-labelledby` の既定にもなる（節を見出しの名前の region にする）。 */
  titleId?: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <section aria-labelledby={title ? titleId : undefined} {...props} className={cn("grid min-w-0 gap-3", className)}>
      {title || actions ? (
        <div className="flex min-w-0 flex-col items-start justify-between gap-4 sm:flex-row">
          <div className="min-w-0 flex-1 [overflow-wrap:anywhere]">
            {title ? (
              <h2 id={titleId} className="text-base font-semibold text-fg">
                {title}
              </h2>
            ) : null}
            {description ? <p className="mt-1 text-sm text-fg-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex min-w-0 max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
