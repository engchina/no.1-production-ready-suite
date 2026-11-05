import { TRACE_DETAILS_JA } from "../../lib/trace-labels";
import { useTraceDetailsLabels } from "../data/trace-labels-provider";

/** 書込時の欠落と、保存済み記録の読取省略を別々に知らせる。 */
export function TracePartialNotice({ partial = false, readTruncated = false, droppedSpans = 0, testId }: {
  partial?: boolean;
  readTruncated?: boolean;
  droppedSpans?: number;
  testId?: string;
}) {
  const labels = useTraceDetailsLabels();
  const limited = readTruncated === true;
  const dropped = (Number.isFinite(droppedSpans) && droppedSpans > 0) || (partial === true && !limited);
  if (!limited && !dropped) return null;
  return <div className="grid min-w-0 gap-1" data-testid={testId}>
    {limited ? <p className="text-xs text-fg-muted" data-testid={testId ? `${testId}-read-truncated` : undefined}>{labels.readTruncated ?? TRACE_DETAILS_JA["trace.details.readTruncated"]}</p> : null}
    {dropped ? <p className="text-xs text-fg-muted" data-testid={testId ? `${testId}-write-dropped` : undefined}>{labels.partial}</p> : null}
  </div>;
}
