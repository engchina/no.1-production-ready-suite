import { useMutation } from "@tanstack/react-query";
import { Download, File, FileDown, FileSpreadsheet, FileText, Presentation, type LucideIcon } from "lucide-react";
import { ApiErrorBanner, Button, formatAttachmentSize } from "@production-ready/ui";

import { downloadBlob } from "@/components/evaluation/EvaluationSetEditor";
import { agentApi } from "@/lib/api";
import { t } from "@/lib/i18n";
import type { FileArtifactType, RunFileArtifact } from "@/lib/run-artifacts";

/**
 * 業務 Agent のツールが作ったファイル（#5837。成果物 `file`）。チャットの回答の下と、実行の詳細の「成果物」に出す。
 *
 * 成果物はファイル名・形式・大きさだけを持つ。中身は押したときにダウンロードの API から Cookie セッションで取り
 * （リンクの直打ちにしない）、ブラウザに保存させる。失敗（無い・権限なし）は、その行の直下に出す。
 */

const TYPE_ICON: Record<FileArtifactType, LucideIcon> = {
  xlsx: FileSpreadsheet,
  docx: FileText,
  pptx: Presentation,
  other: File,
};

/** ファイルの成果物の 1 件（種類のアイコン・ファイル名・形式と大きさ・ダウンロードのボタン）。 */
export function FileArtifactItem({ runId, file, testId }: { runId: string; file: RunFileArtifact; testId: string }) {
  const download = useMutation({
    mutationFn: () => agentApi.downloadRunArtifactFile(runId, file.id),
    onSuccess: (blob) => downloadBlob(blob, file.fileName),
  });
  const Icon = TYPE_ICON[file.type];
  const details = [t(`chat.files.type.${file.type}`), file.sizeBytes === null ? null : formatAttachmentSize(file.sizeBytes)]
    .filter((item): item is string => item !== null)
    .join("・");
  return (
    <div className="min-w-0 space-y-2" data-testid={testId}>
      {/* 名前は折り返し、ボタンは右に残す（名前の幅が足りない狭い幅では、ボタンが名前の下に回る）。 */}
      <div className="flex min-w-0 flex-wrap items-center gap-3 rounded-md border border-border p-3">
        <div className="flex min-w-0 flex-1 basis-48 items-center gap-2">
          <Icon size={20} aria-hidden="true" className="shrink-0 text-fg-muted" />
          <div className="min-w-0">
            <p className="break-all text-sm font-medium text-fg">{file.fileName}</p>
            <p className="mt-0.5 text-xs tabular-nums text-fg-muted">{details}</p>
          </div>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          icon={Download}
          loading={download.isPending}
          aria-label={t("chat.files.downloadNamed", { name: file.fileName })}
          onClick={() => download.mutate()}
          className="shrink-0"
          data-testid={`${testId}-download`}
        >
          {t("chat.files.download")}
        </Button>
      </div>
      {download.error ? (
        <ApiErrorBanner error={download.error} fallback={t("chat.files.downloadFailed")} testId={`${testId}-error`} />
      ) : null}
    </div>
  );
}

/** 回答の下の「作成したファイル」（ファイルの成果物ごとに 1 行）。ファイルが無ければ何も出さない。 */
export function RunFileArtifacts({ runId, files, testId }: { runId: string; files: RunFileArtifact[]; testId: string }) {
  if (files.length === 0) return null;
  const name = t("chat.files.title");
  return (
    <section className="min-w-0 space-y-2" aria-label={name} data-testid={testId}>
      <p className="flex min-w-0 items-center gap-1.5 text-xs text-fg-muted">
        <FileDown size={14} aria-hidden="true" className="shrink-0" />
        <span className="min-w-0">{name}</span>
      </p>
      {files.map((file) => (
        <FileArtifactItem key={file.id} runId={runId} file={file} testId={`${testId}-${file.id}`} />
      ))}
    </section>
  );
}
