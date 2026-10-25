import { useTraceDetailsLabels } from "@production-ready/ui";

/**
 * 書けなかった span がある記録の処理の経過（#3226 の案 A）。処理の経過の中に共通の案内の文
 * （「一部の処理だけを記録しています。」）を出す（成功と見なさない。状態は文で伝え、色だけにしない）。
 */
export function ProgressPartialNotice({ testId }: { testId?: string }) {
  const labels = useTraceDetailsLabels();
  return (
    <p className="text-xs leading-5 text-fg-muted" data-testid={testId}>
      {labels.partial}
    </p>
  );
}
