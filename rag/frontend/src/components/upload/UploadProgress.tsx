import { ProcessingIndicator } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import {
  formatByteSize,
  uploadProgressPercent,
  type UploadProgress,
} from "@/lib/upload-requests";

/**
 * 送信中の表示（#306）。件数と経過時間（`ProcessingIndicator`）に、送信済み / 合計のバイト数と割合を添える。
 * 送り終えた後は保存先への保存・文書の登録を待っていることを示す（100% のまま止まって見えないように）。
 */
export function UploadSendingState({
  fileCount,
  progress,
}: {
  fileCount: number;
  progress: UploadProgress | null;
}) {
  const percent = progress ? uploadProgressPercent(progress) : 0;
  const hasBytes = progress !== null && progress.totalBytes > 0;
  const summary = hasBytes
    ? t("upload.progress.sent", {
        sent: formatByteSize(progress.sentBytes),
        total: formatByteSize(progress.totalBytes),
        percent,
      })
    : "";
  return (
    <div className="grid min-w-0 gap-2" data-testid="upload-sending">
      <ProcessingIndicator
        active
        placement="action"
        label={t("upload.uploading", { count: fileCount })}
        testId="upload-processing"
        // 大きなファイルの送信は 10 秒を超えるのが普通。進み具合を出すときは遅延の案内を出さない。
        showSlowMessage={!hasBytes}
      />
      {hasBytes ? (
        <div className="grid min-w-0 gap-1.5" data-testid="upload-progress">
          <div
            className="h-2 overflow-hidden rounded-full bg-surface-hover"
            role="progressbar"
            aria-label={t("upload.progress.aria")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            aria-valuetext={summary}
          >
            {/* 割合だけは送信の進み具合で決まるため inline style にする（色・高さはトークン）。
                幅ではなく transform で伸ばし、layout を動かさない。 */}
            <div
              className="h-full w-full origin-left bg-accent-emphasis transition-transform duration-300 motion-reduce:transition-none"
              style={{ transform: `scaleX(${percent / 100})` }}
            />
          </div>
          <p className="tnum text-xs text-fg-muted" data-testid="upload-progress-bytes">
            {summary}
          </p>
          {percent >= 100 ? (
            <p className="text-xs text-fg-muted" data-testid="upload-progress-saving">
              {t("upload.progress.saving")}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
