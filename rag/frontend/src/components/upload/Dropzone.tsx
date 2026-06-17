"use client";

import { UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

export const ACCEPTED_UPLOAD_TYPES = [
  ".pdf",
  ".gif",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".tif",
  ".tiff",
  ".txt",
  ".md",
  ".markdown",
  ".csv",
  ".tsv",
  ".json",
  ".jsonl",
  ".xml",
  ".ndjson",
  ".html",
  ".htm",
  ".xhtml",
  ".eml",
  ".msg",
  ".doc",
  ".docx",
  ".ppt",
  ".pptx",
  ".xls",
  ".xlsx",
  ".aac",
  ".flac",
  ".m4a",
  ".mp3",
  ".ogg",
  ".wav",
  "application/pdf",
  "image/gif",
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/webp",
  "image/tif",
  "image/tiff",
  "text/plain",
  "text/markdown",
  "text/csv",
  "text/tab-separated-values",
  "text/html",
  "application/xhtml+xml",
  "application/json",
  "application/jsonl",
  "application/jsonlines",
  "application/ndjson",
  "application/xml",
  "application/csv",
  "application/x-ndjson",
  "message/rfc822",
  "application/eml",
  "application/vnd.ms-outlook",
  "application/x-msg",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-powerpoint",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "audio/aac",
  "audio/flac",
  "audio/mp3",
  "audio/mpeg",
  "audio/mp4",
  "audio/ogg",
  "audio/wave",
  "audio/wav",
  "audio/x-flac",
  "audio/x-m4a",
  "audio/x-wav",
  "application/ogg",
].join(",");

/** ドラッグ＆ドロップ + クリック選択のファイル入力。 */
export function Dropzone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const files = Array.from(e.dataTransfer.files ?? []);
    if (files.length) onFiles(files);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-disabled={disabled}
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(e) => {
        if ((e.key === "Enter" || e.key === " ") && !disabled) {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={cn(
        "flex h-52 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed bg-card text-center transition-colors",
        dragOver ? "border-primary bg-info-bg/40" : "border-border hover:border-primary/60",
        disabled && "cursor-not-allowed opacity-60"
      )}
    >
      <UploadCloud size={28} className="text-primary" aria-hidden />
      <p className="text-sm font-medium text-foreground">{t("upload.dropzone")}</p>
      <p className="text-xs text-muted">{t("upload.dropzoneHint")}</p>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        accept={ACCEPTED_UPLOAD_TYPES}
        disabled={disabled}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length) onFiles(files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
