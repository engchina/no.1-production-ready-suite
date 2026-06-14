import { cn } from "@/lib/utils";

import { toneIcon, toneRole, toneText, type FeedbackTone } from "./feedback-tone";

/**
 * FormStatus（フォーム/アクション結果）。docs/frontend-messaging-spec.md §3.3。
 * 保存・接続テスト等のボタン近傍に直近結果を 1 行で表示する。
 * `message` が空のときは何も描画しない。
 */
export function FormStatus({
  tone,
  message,
  className,
}: {
  tone: FeedbackTone;
  message?: string | null;
  className?: string;
}) {
  if (!message) return null;
  const Icon = toneIcon[tone];

  return (
    <p
      role={toneRole(tone)}
      className={cn("inline-flex items-center gap-1.5 text-sm font-medium", toneText[tone], className)}
    >
      <Icon size={15} className="shrink-0" aria-hidden />
      {message}
    </p>
  );
}
