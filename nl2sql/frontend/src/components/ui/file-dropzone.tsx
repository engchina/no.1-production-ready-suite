import {
  useEffect,
  useId,
  useRef,
  useState,
  type DragEvent,
} from "react";
import {
  FileSpreadsheet,
  FileText,
  Loader2,
  Upload,
  X,
} from "lucide-react";

import { t } from "@/lib/i18n";
import { validateFileDropzoneSelection, type FileDropzoneRejectReason } from "@/lib/file-dropzone";
import { cn } from "@/lib/utils";
import { Button } from "./button";
import { FieldError } from "./field-error";
import { FieldLabel } from "./required-field";

export type FileDropzoneIcon = "file" | "spreadsheet" | "upload";

const fileDropzoneIcons: Record<FileDropzoneIcon, typeof FileText> = {
  file: FileText,
  spreadsheet: FileSpreadsheet,
  upload: Upload,
};

export interface FileDropzoneProps {
  label: string;
  ariaLabel?: string;
  accept: string;
  formatLabel: string;
  multiple?: boolean;
  selectedText?: string;
  selectedCount?: number;
  actionText?: string;
  activeText?: string;
  replaceText?: string;
  addText?: string;
  loadingText?: string;
  clearText?: string;
  clearAriaLabel?: string;
  hint?: string;
  errorText?: string;
  icon?: FileDropzoneIcon;
  required?: boolean;
  disabled?: boolean;
  loading?: boolean;
  clearDisabled?: boolean;
  className?: string;
  dataTestId?: string;
  onFiles: (files: File[]) => void | Promise<void>;
  onClear?: () => void;
  onReject?: (reason: FileDropzoneRejectReason) => void;
}

/** 全画面共通のコンパクトなファイル選択 / drag & drop 入力。 */
export function FileDropzone({
  label,
  ariaLabel = label,
  accept,
  formatLabel,
  multiple = false,
  selectedText = "",
  selectedCount = 0,
  actionText = t("common.fileDropzone.action"),
  activeText = t("common.fileDropzone.active"),
  replaceText = t("common.fileDropzone.replace"),
  addText = t("common.fileDropzone.add"),
  loadingText = t("common.fileDropzone.loading"),
  clearText = t("common.fileDropzone.clear"),
  clearAriaLabel,
  hint = "",
  errorText = "",
  icon = "upload",
  required = false,
  disabled = false,
  loading = false,
  clearDisabled,
  className,
  dataTestId,
  onFiles,
  onClear,
  onReject,
}: FileDropzoneProps) {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const Icon = fileDropzoneIcons[icon];
  const hasSelection = Boolean(selectedText) || selectedCount > 0;
  const interactionDisabled = disabled || loading;
  const clearIsDisabled = clearDisabled ?? !hasSelection;
  const dragDepthRef = useRef(0);
  const [isDragActive, setIsDragActive] = useState(false);
  const [validationError, setValidationError] = useState("");
  const visibleError = errorText || validationError;
  const describedBy = [hint ? hintId : "", visibleError ? errorId : ""]
    .filter(Boolean)
    .join(" ");

  useEffect(() => {
    if (interactionDisabled) {
      dragDepthRef.current = 0;
      setIsDragActive(false);
    }
  }, [interactionDisabled]);

  const acceptFiles = (files: FileList | File[]) => {
    const candidates = Array.from(files);
    if (candidates.length === 0) return;
    const result = validateFileDropzoneSelection(candidates, { accept, multiple });
    if (!result.accepted) {
      if (onReject) {
        onReject(result.reason);
      } else {
        setValidationError(
          result.reason === "multiple-files"
            ? t("common.fileDropzone.error.multiple", { formats: formatLabel })
            : t("common.fileDropzone.error.unsupported", { formats: formatLabel })
        );
      }
      return;
    }
    setValidationError("");
    void onFiles(result.files);
  };

  const handleDragEnter = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (interactionDisabled) return;
    dragDepthRef.current += 1;
    setIsDragActive(true);
  };

  const handleDragOver = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (!interactionDisabled) event.dataTransfer.dropEffect = "copy";
  };

  const handleDragLeave = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    if (interactionDisabled) return;
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDragActive(false);
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setIsDragActive(false);
    if (interactionDisabled) return;
    acceptFiles(event.dataTransfer.files);
  };

  const displayText = loading
    ? loadingText
    : isDragActive
      ? activeText
      : selectedText ||
        (selectedCount > 0
          ? t("common.fileDropzone.selectedCount", { count: selectedCount })
          : actionText);

  return (
    <div
      className={cn("grid min-w-0 gap-1 text-sm font-medium text-foreground", className)}
      data-testid={dataTestId}
    >
      <FieldLabel htmlFor={inputId} label={label} required={required} />
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-2">
        <label
          htmlFor={inputId}
          data-testid={dataTestId ? `${dataTestId}-dropzone` : undefined}
          data-drag-active={String(isDragActive)}
          aria-busy={loading}
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={cn(
            "group flex h-[44px] min-w-0 touch-manipulation items-center gap-2 rounded-md border border-dashed bg-background px-3 py-1 text-left",
            "transition-[border-color,background-color,box-shadow] duration-200 ease-out motion-reduce:transition-none",
            "focus-within:ring-2 focus-within:ring-ring/40",
            interactionDisabled
              ? "cursor-not-allowed border-border opacity-60"
              : isDragActive
                ? "cursor-copy border-primary bg-primary/10 ring-2 ring-ring/40"
                : visibleError
                  ? "cursor-pointer border-danger/60 bg-danger-bg/30 hover:border-danger"
                  : hasSelection
                    ? "cursor-pointer border-primary/40 bg-primary/5 hover:border-primary hover:bg-primary/10"
                    : "cursor-pointer border-border hover:border-primary/60 hover:bg-primary/5"
          )}
        >
          <span
            className={cn(
              "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md",
              hasSelection || isDragActive
                ? "bg-primary/10 text-primary"
                : "bg-muted/30 text-muted group-hover:bg-primary/10 group-hover:text-primary"
            )}
            aria-hidden="true"
          >
            {loading ? (
              <Loader2 size={16} className="animate-spin motion-reduce:animate-none" />
            ) : (
              <Icon size={16} />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span
              className="block truncate text-sm font-semibold text-foreground"
              title={displayText}
            >
              {displayText}
            </span>
          </span>
          <span
            className={cn(
              "inline-block max-w-24 shrink-0 truncate rounded-md border px-2 py-1 text-xs font-semibold sm:max-w-56",
              hasSelection
                ? "border-primary/30 bg-primary/10 text-primary"
                : "border-border bg-background text-muted"
            )}
            title={hasSelection ? (multiple ? addText : replaceText) : formatLabel}
          >
            {hasSelection ? (multiple ? addText : replaceText) : formatLabel}
          </span>
          <input
            id={inputId}
            data-testid={dataTestId ? `${dataTestId}-input` : undefined}
            className="sr-only"
            type="file"
            accept={accept}
            multiple={multiple}
            disabled={interactionDisabled}
            required={required}
            aria-label={ariaLabel}
            aria-required={required}
            aria-invalid={Boolean(visibleError)}
            aria-describedby={describedBy || undefined}
            onChange={(event) => {
              const input = event.currentTarget;
              if (!input.files?.length) return;
              acceptFiles(input.files);
              queueMicrotask(() => {
                input.value = "";
              });
            }}
          />
        </label>
        {onClear ? (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            className="h-[44px] whitespace-nowrap"
            disabled={clearIsDisabled || interactionDisabled}
            aria-label={clearAriaLabel ?? clearText}
            onClick={() => {
              setValidationError("");
              onClear();
            }}
          >
            <X size={15} aria-hidden="true" />
            <span>{clearText}</span>
          </Button>
        ) : null}
      </div>
      {hint ? (
        <p id={hintId} className="text-xs font-normal leading-5 text-muted">
          {hint}
        </p>
      ) : null}
      <FieldError id={errorId} message={visibleError} />
    </div>
  );
}
