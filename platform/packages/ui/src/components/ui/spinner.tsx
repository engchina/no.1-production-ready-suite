import type { SVGProps } from "react";

import { cn } from "../../lib/utils";

export interface SpinnerProps extends Omit<SVGProps<SVGSVGElement>, "width" | "height"> {
  /** アイコン寸法(px)。周囲のアイコンと揃える(14 / 16 / 20 / 24)。既定は 16px。 */
  size?: number;
}

/** 線の実寸(px)。大きさによらず 2px にそろえる(#395)。 */
export const SPINNER_STROKE_PX = 2;

const VIEWBOX = 24;
/** 線の外縁の半径(viewBox 単位)。線を太くしても外形(直径 20/24)を変えない。 */
const OUTER_RADIUS = 10;

const round = (value: number) => Math.round(value * 1000) / 1000;

/**
 * 大きさごとの線幅(viewBox 単位)と円の半径。
 *
 * 旧実装は viewBox 24 に `strokeWidth={2}` 固定で、16px では実寸 1.33px、14px では 1.17px まで細り、
 * 等倍の画面ではアンチエイリアスで線がかすれていた。線の実寸を 2px に固定する(小さいほど viewBox 上の比率を
 * 太くする光学補正。GitHub Primer・Fluent 2 の 16〜28px のスピナーも 2px)。外縁の半径は固定し、太くした分だけ
 * 円の半径を内側へ寄せる(大きさを変えても外形は同じ)。
 */
export function spinnerGeometry(size: number) {
  const strokeWidth = round((SPINNER_STROKE_PX * VIEWBOX) / size);
  return { strokeWidth, radius: round(OUTER_RADIUS - strokeWidth / 2) };
}

/**
 * 読み込み中スピナー。
 *
 * lucide の `Loader2`(loader-circle)は 288 度の欠けた円弧だけを描くため、回転すると
 * インクの重心と外形シルエットが角度ごとに動き、「中心がずれて上下に揺れている」ように見える。
 * ここでは **常に閉じた円のトラック** を敷き、その上を 270 度のアークが回る構成にすることで、
 * シルエットを回転角によらず一定に保ち、純粋な回転として知覚されるようにする。
 *
 * - トラックの色は `--color-spinner-track`(`currentColor` をライト 30% / ダーク 35% で透かす)。
 *   アークとトラックの境目の 3:1 を保つ上限の濃さ(README §4「Spinner」)。
 * - 回転は `transform` だけ(合成スレッドで回るので、メインスレッドが詰まっても止まらない)。等速(linear)。
 * - `prefers-reduced-motion` でも回転を止めない(#440。`base.css`)。その場で回るだけの小さな動きで処理中を伝える
 *   本質的な表示のため(OS 標準の処理中表示も止まらない)。止めると処理が固まったように見える。
 */
export function Spinner({ size = 16, className, ...props }: SpinnerProps) {
  const { strokeWidth, radius } = spinnerGeometry(size);
  const center = VIEWBOX / 2;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      aria-hidden="true"
      // flex item の既定 flex-shrink: 1 で長いラベルに押されて縮まないよう shrink-0。
      className={cn("shrink-0 animate-spin", className)}
      {...props}
    >
      {/* シルエットを一定に保つトラック(全周) */}
      <circle className="pr-spinner-track" cx={center} cy={center} r={radius} />
      {/* 回転を知覚させるアーク(270 度)。旧 Loader2(288 度)に近い視覚的な重みを保つ */}
      <path
        className="pr-spinner-arc"
        d={`M${round(center + radius)} ${center}a${radius} ${radius} 0 1 0-${radius} ${radius}`}
        strokeLinecap="round"
      />
    </svg>
  );
}
