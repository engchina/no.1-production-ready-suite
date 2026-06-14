"use client";

import {
  AlertCircle,
  Clipboard,
  CheckCircle2,
  FileJson2,
  FileText,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type CopyState = "idle" | "success" | "error";

export const SETTINGS_DETAIL_GRID_CLASS =
  "grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]";

interface PreviewCardProps {
  title: string;
  description: string;
  value: string;
  ariaLabel?: string;
  copyLabel: string;
  icon: LucideIcon;
  previewHeightClassName: string;
}

export function EnvPreviewCard(props: Omit<PreviewCardProps, "title" | "icon" | "copyLabel">) {
  return (
    <SettingsPreviewCard
      {...props}
      title={t("settings.preview.env.title")}
      copyLabel={t("settings.preview.env.copy")}
      icon={FileText}
    />
  );
}

export function JsonPreviewCard(props: Omit<PreviewCardProps, "title" | "icon" | "copyLabel">) {
  return (
    <SettingsPreviewCard
      {...props}
      title={t("settings.preview.json.title")}
      copyLabel={t("settings.preview.json.copy")}
      icon={FileJson2}
    />
  );
}

export function SettingsSupplementalPanels({
  status,
  env,
  json,
  operation,
}: {
  status?: ReactNode;
  env: {
    description: string;
    value: string;
  };
  json?: {
    description: string;
    value: string;
  };
  operation: {
    description: string;
    notes: string[];
    warnings?: string[];
  };
}) {
  return (
    <aside className="space-y-5">
      <EnvPreviewCard
        description={env.description}
        value={env.value}
        previewHeightClassName="h-44"
      />
      {json ? (
        <JsonPreviewCard
          description={json.description}
          value={json.value}
          previewHeightClassName="h-56"
        />
      ) : null}
      <OperationMemoCard
        description={operation.description}
        notes={operation.notes}
        warnings={operation.warnings ?? []}
      />
      {status}
    </aside>
  );
}

function OperationMemoCard({
  description,
  notes,
  warnings,
}: {
  description: string;
  notes: string[];
  warnings: string[];
}) {
  const uniqueWarnings = [...new Set(warnings.filter(Boolean))];

  return (
    <Card>
      <CardHeader>
        <SettingsCardHeader
          icon={ShieldCheck}
          title={t("settings.preview.ops.title")}
          description={description}
        />
      </CardHeader>
      <CardContent className="space-y-3">
        <ul className="space-y-2 text-sm leading-relaxed text-muted">
          {notes.map((note) => (
            <li key={note} className="flex gap-2">
              <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-info" aria-hidden />
              <span className="leading-relaxed">{note}</span>
            </li>
          ))}
        </ul>
        {uniqueWarnings.length > 0 ? (
          <ul className="space-y-2 border-t border-border pt-3 text-sm leading-relaxed text-foreground">
            {uniqueWarnings.map((warning) => (
              <li key={warning} className="flex gap-2">
                <AlertCircle size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                <span className="leading-relaxed">{warning}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function formatSettingsEnvValue(value: string): string {
  const normalized = value.trim();
  if (!normalized) return "";
  if (/[\s#"']/u.test(normalized)) return JSON.stringify(normalized);
  return normalized;
}

export function formatSettingsJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function SettingsPreviewCard({
  title,
  description,
  value,
  ariaLabel,
  copyLabel,
  icon,
  previewHeightClassName,
}: PreviewCardProps) {
  const [copyState, setCopyState] = useState<CopyState>("idle");

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopyState("success");
    } catch {
      setCopyState("error");
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
          <SettingsCardHeader icon={icon} title={title} description={description} />
          <Button
            type="button"
            variant="secondary"
            size="lg"
            className="w-full shrink-0 whitespace-nowrap sm:w-auto"
            onClick={() => void handleCopy()}
          >
            <Clipboard size={14} aria-hidden />
            {copyState === "success"
              ? t("settings.preview.copy.copied")
              : copyLabel}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <textarea
          readOnly
          value={value}
          aria-label={ariaLabel ?? title}
          className={cn(
            "w-full resize-none rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed text-foreground outline-none focus-visible:border-primary",
            previewHeightClassName
          )}
        />
        {copyState === "error" ? (
          <FormStatus
            tone="danger"
            className="text-xs"
            message={t("settings.preview.copy.failed")}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

function SettingsCardHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
        <Icon size={18} aria-hidden />
      </span>
      <span className="min-w-0 space-y-1">
        <CardTitle>{title}</CardTitle>
        <CardDescription className="leading-relaxed">{description}</CardDescription>
      </span>
    </div>
  );
}
