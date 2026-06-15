"use client";

import { UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";

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
        accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.txt,application/pdf,image/*,text/plain"
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
