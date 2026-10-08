import { Button, ClearActionButton, StatusBadge } from "@production-ready/ui";
import { FileText, Upload, X } from "lucide-react";

import { t, type I18nKey } from "@/lib/i18n";
import { formatByteSize, totalUploadBytes } from "@/lib/upload-requests";
import { uploadFileProblem, type UploadFileProblem } from "@/lib/upload-selection";

const PROBLEM_LABEL: Record<UploadFileProblem, I18nKey> = {
  unsupported: "upload.selection.problem.unsupported",
  tooLarge: "upload.selection.problem.tooLarge",
  empty: "upload.selection.problem.empty",
};

/**
 * 送る前に選んだファイルの一覧（#701）。選ぶだけでは送らず、ここで見直してから「アップロードを開始」する。
 * 送れないファイル（形式・サイズ・空）は理由を示し、送る件数に数えない。送信中は出さない（送信中の表示が
 * ファイルごとの進み具合を出す。失敗したら選択を残したまま戻る）。
 */
export function UploadSelectionList({
  files,
  maxUploadBytes,
  onRemove,
  onClear,
  onStart,
}: {
  files: readonly File[];
  maxUploadBytes: number;
  onRemove: (file: File) => void;
  onClear: () => void;
  onStart: (sendable: File[]) => void;
}) {
  if (files.length === 0) return null;
  const rows = files.map((file) => ({ file, problem: uploadFileProblem(file, maxUploadBytes) }));
  const sendable = rows.filter((row) => row.problem === null).map((row) => row.file);
  const blocked = rows.length - sendable.length;
  return (
    <section
      aria-labelledby="upload-selection-title"
      className="space-y-3 rounded-lg border border-border bg-surface p-4"
      data-testid="upload-selection"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 id="upload-selection-title" className="text-sm font-semibold text-fg">
            {t("upload.selection.title", { count: files.length })}
          </h2>
          <p className="tnum mt-0.5 text-xs text-fg-muted" role="status">
            {t("upload.selection.summary", {
              sendable: sendable.length,
              size: formatByteSize(totalUploadBytes(sendable)),
            })}
            {blocked > 0 ? ` ${t("upload.selection.blocked", { count: blocked })}` : ""}
          </p>
        </div>
        <ClearActionButton label={t("upload.selection.clear")} onClick={onClear} />
      </div>
      <ul className="bounded-scroll-area divide-y divide-border rounded-md border border-border bg-surface-sunken">
        {rows.map(({ file, problem }, index) => (
          // 同じ名前のファイル（別のフォルダ）も並ぶので、位置を key に含める。
          <li
            key={`${index}-${file.name}`}
            className="flex items-center justify-between gap-3 px-3 py-2"
          >
            <div className="flex min-w-0 items-start gap-2">
              <FileText size={16} className="mt-0.5 shrink-0 text-accent-fg" aria-hidden />
              <div className="min-w-0">
                <p className="truncate text-sm text-fg" title={file.name}>
                  {file.name}
                </p>
                {/* 理由は名前の下に置き、375px でも名前を読める幅を残す。 */}
                {problem ? (
                  <StatusBadge
                    variant="warning"
                    label={t(PROBLEM_LABEL[problem])}
                    className="mt-1"
                  />
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="tnum text-xs text-fg-muted">{formatByteSize(file.size)}</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                iconOnly
                icon={X}
                aria-label={t("upload.selection.remove", { name: file.name })}
                onClick={() => onRemove(file)}
              />
            </div>
          </li>
        ))}
      </ul>
      <div className="flex justify-end">
        <Button
          type="button"
          size="lg"
          icon={Upload}
          disabled={sendable.length === 0}
          onClick={() => onStart(sendable)}
          className="w-full sm:w-auto"
        >
          {t("upload.selection.start", { count: sendable.length })}
        </Button>
      </div>
    </section>
  );
}
