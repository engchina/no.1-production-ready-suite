import { AlertCircle, AlertTriangle, CheckCircle2, Info, type LucideIcon } from "lucide-react";

/**
 * メッセージ機構の 4 トーン（docs/frontend-messaging-spec.md §2）。
 * 色だけで意味を伝えないため、トーンごとにアイコンと role を必ず併置する。
 */
export type FeedbackTone = "success" | "info" | "warning" | "danger";

export const toneIcon: Record<FeedbackTone, LucideIcon> = {
  success: CheckCircle2,
  info: Info,
  warning: AlertTriangle,
  danger: AlertCircle,
};

/** 文字（アイコン・本文）色。 */
export const toneText: Record<FeedbackTone, string> = {
  success: "text-success",
  info: "text-info",
  warning: "text-warning",
  danger: "text-danger",
};

/** バナー等の面（枠 + 背景 + 文字）。 */
export const toneSurface: Record<FeedbackTone, string> = {
  success: "border-success/30 bg-success-bg/60 text-success",
  info: "border-info/30 bg-info-bg/60 text-info",
  warning: "border-warning/30 bg-warning-bg/60 text-warning",
  danger: "border-danger/30 bg-danger-bg/60 text-danger",
};

/** danger は即時読み上げ（alert）、その他は polite（status）。 */
export function toneRole(tone: FeedbackTone): "alert" | "status" {
  return tone === "danger" ? "alert" : "status";
}
