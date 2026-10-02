"use client";

import {
  Clipboard,
  FileJson2,
  FileText,
  type LucideIcon,
} from "lucide-react";
import { useId, useState, type ReactNode } from "react";
import {
  toast,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormStatus,
  ContentActionBar,
  TextareaField,
} from "@engchina/production-ready-ui";

import { copyTextToClipboard } from "@/lib/clipboard";
import { t } from "@/lib/i18n";

type CopyState = "idle" | "error";

export const SETTINGS_DETAIL_GRID_CLASS =
  "grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]";

interface PreviewCardProps {
  title: string;
  description: string;
  value: string;
  ariaLabel?: string;
  copyLabel: string;
  icon: LucideIcon;
  /** プレビューの行数（高さは rows で決める。textareaClassName の h-* は使わない。#613 / #800）。 */
  previewRows: number;
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
}: {
  status?: ReactNode;
  env?: {
    description: string;
    value: string;
  };
  json?: {
    description: string;
    value: string;
  };
}) {
  return (
    <aside className="space-y-5">
      {env ? (
        <EnvPreviewCard
          description={env.description}
          value={env.value}
          previewRows={7}
        />
      ) : null}
      {json ? (
        <JsonPreviewCard
          description={json.description}
          value={json.value}
          previewRows={9}
        />
      ) : null}
      {status}
    </aside>
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
  previewRows,
}: PreviewCardProps) {
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const previewId = useId();

  async function handleCopy() {
    try {
      await copyTextToClipboard(value);
      setCopyState("idle");
      toast.success(t("common.action.copied"));
    } catch {
      setCopyState("error");
    }
  }

  return (
    <Card>
      <CardHeader>
        <ContentActionBar
          ariaLabel={t("settings.preview.actions", { label: copyLabel })}
          leading={<SettingsCardHeader icon={icon} title={title} description={description} />}
          testId="settings-preview-actions"
        >
          <Button
            type="button"
            variant="secondary"
            size="sm"
            aria-label={copyLabel}
            onClick={() => void handleCopy()} icon={Clipboard}>
            <span>{t("settings.preview.copy")}</span>
          </Button>
        </ContentActionBar>
      </CardHeader>
      <CardContent className="space-y-3">
        <TextareaField
          id={previewId}
          label={ariaLabel ?? title}
          labelHidden
          readOnly
          value={value}
          monospace
          resize="none"
          rows={previewRows}
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
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
        <Icon size={20} aria-hidden />
      </span>
      <span className="min-w-0 space-y-1">
        <CardTitle>{title}</CardTitle>
        <CardDescription className="leading-relaxed">{description}</CardDescription>
      </span>
    </div>
  );
}
