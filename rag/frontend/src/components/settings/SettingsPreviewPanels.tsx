"use client";

import { Clipboard, type LucideIcon } from "lucide-react";
import { useState } from "react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormStatus,
} from "@engchina/production-ready-ui";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type CopyState = "idle" | "success" | "error";

interface PreviewCardProps {
  title: string;
  description: string;
  value: string;
  ariaLabel?: string;
  copyLabel: string;
  icon: LucideIcon;
  previewHeightClassName: string;
}

export function SettingsPreviewCard({
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
            onClick={() => void handleCopy()} icon={Clipboard}>
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
            "w-full resize-none rounded-md border border-border-control bg-surface-sunken p-3 font-mono text-xs leading-relaxed text-fg outline-none focus-visible:border-focus-ring",
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
