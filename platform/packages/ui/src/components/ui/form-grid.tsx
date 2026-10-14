import type { ComponentProps } from "react";

import { cn } from "../../lib/utils";

export type FormGridProps = ComponentProps<"div"> & {
  /**
   * 段組み（既定 2）。2 は置き場所の幅が 42rem 以上で 2 列、それより狭いと 1 列。1 は常に 1 列
   * （項目の少ないフォーム・長い値が主のフォーム）。
   */
  columns?: 1 | 2;
};

/**
 * フォームの本文（入力欄の並び）の型（#1891）。最大幅は読みやすい `--form-max-width`（60rem）で、左に寄せる。
 *
 * - カード・`Section` は 100% の幅のまま（README §4「wide 画面の 100% 充填」）。止めるのはフォームの欄の並びだけで、
 *   1920px の画面でも 1 つの欄が 1,500px を超えて伸びない（GOV.UK の 2/3 の列、Baymard の欄の幅の研究）。
 * - 欄は値の種類の `width`（`xs`〜`lg`）を渡すと、セルの中でもその段が上限になる。URL・OCID・文章・SQL・
 *   `TextareaField` は `full` のまま（このグリッドの幅まで）にし、セルを 2 列にまたがせる（`className="col-span-full"`）。
 * - 意味のペア（ユーザー / パスワード・リージョン / テナンシ）は同じ行に並べ、入力の順は崩さない。
 * - 確定の行（`FormActionBar`）はこのグリッドの外（直後）に置く。左端は欄の左端にそろう。
 */
export function FormGrid({ columns = 2, className, children, ...props }: FormGridProps) {
  return (
    <div className="@container w-full min-w-0 max-w-[var(--form-max-width)]" data-form-grid={columns}>
      {/* 1 列も `grid-cols-1`（minmax(0, 1fr)）で明示する。暗黙の列（auto）は中身の最小幅まで広がり、長い値の選択欄・
          折り返さない文字があると狭い置き場所（375px・分割の右）で横にあふれる。 */}
      <div
        {...props}
        className={cn("grid min-w-0 grid-cols-1 gap-x-6 gap-y-4", columns === 2 && "@2xl:grid-cols-2", className)}
      >
        {children}
      </div>
    </div>
  );
}
