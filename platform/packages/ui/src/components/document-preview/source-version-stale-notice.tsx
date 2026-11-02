import { Banner } from "../ui/banner";
import { SOURCE_VERSION_STALE_MESSAGE } from "./labels";

/**
 * 引用の版を表示できないことの案内（#4092）。再索引の後に起こる通常の状態で、障害ではなく取り直しても変わらないので、
 * 赤いエラーと再試行を出さない。判定は `isSourceVersionStale`。RAG と Agent の根拠のパネルで同じ表示にする。
 */
export function SourceVersionStaleNotice({ message = SOURCE_VERSION_STALE_MESSAGE }: { message?: string }) {
  return (
    <div className="shrink-0" data-testid="source-version-stale-notice">
      <Banner severity="info" title={message} />
    </div>
  );
}
