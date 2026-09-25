import type { SVGProps } from "react";

import { cn } from "../../lib/utils";

export interface SpinnerProps extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  /** アイコン寸法(px)。周囲のアイコンと揃える。既定は 16px。 */
  size?: number;
}

/**
 * 読み込み中スピナー。
 *
 * lucide の `Loader2`(loader-circle)は 288 度の欠けた円弧だけを描くため、回転すると
 * インクの重心と外形シルエットが角度ごとに動き、「中心がずれて上下に揺れている」ように見える。
 * ここでは **常に閉じた円のトラック** を敷き、その上を 270 度のアークが回る構成にすることで、
 * シルエットを回転角によらず一定に保ち、純粋な回転として知覚されるようにする。
 */
export function Spinner({ size = 16, className, ...props }: SpinnerProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      aria-hidden="true"
      // flex item の既定 flex-shrink: 1 で長いラベルに押されて縮まないよう shrink-0。
      className={cn("shrink-0 animate-spin motion-reduce:animate-none", className)}
      {...props}
    >
      {/* シルエットを一定に保つトラック(全周) */}
      <circle cx="12" cy="12" r="9" opacity="0.25" />
      {/* 回転を知覚させるアーク(270 度)。旧 Loader2(288 度)に近い視覚的な重みを保つ */}
      <path d="M21 12a9 9 0 1 0-9 9" strokeLinecap="round" />
    </svg>
  );
}
