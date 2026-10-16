"use client";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  StatusBadge,
  TableSkeleton,
  TimedLoadingState,
  useConfirm,
} from "@production-ready/ui";
import { History, RotateCcw } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError, type LanguagePackHistoryVersion } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useLanguagePackHistory, useRestoreLanguagePack } from "@/lib/queries";
import { toast } from "@/lib/toast";

/** 言語資源の保存の履歴と、前の版への戻し（#2181 段 6）。編集中の変更があるあいだは戻さない。 */
export function LanguagePackHistoryCard({ dirty }: { dirty: boolean }) {
  const history = useLanguagePackHistory();
  const restore = useRestoreLanguagePack();
  const confirm = useConfirm();
  const versions = history.data?.versions ?? [];
  const current = versions[0];

  const restoreVersion = async (version: LanguagePackHistoryVersion) => {
    const reingest = Boolean(current && version.ingest_hash !== current.ingest_hash);
    const ok = await confirm({
      title: t("settings.language.history.restoreConfirm.title"),
      description: t(
        reingest
          ? "settings.language.history.restoreConfirm.reingest"
          : "settings.language.history.restoreConfirm.description",
        { savedAt: formatDateTime(version.saved_at) },
      ),
      confirmLabel: t("settings.language.history.restore"),
      tone: "warning",
    });
    if (!ok) return;
    restore.mutate(version.id, {
      onSuccess: () => toast.success(t("settings.language.history.restored")),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("settings.language.history.restoreError")),
    });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History size={16} className="text-accent-fg" aria-hidden />
          {t("settings.language.history.title")}
        </CardTitle>
        <CardDescription>{t("settings.language.history.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {history.isPending ? (
          <TimedLoadingState
            label={t("settings.language.history.loading")}
            operationKey="settings-language-history"
            testId="settings-language-history-loading"
          >
            <TableSkeleton columns={4} />
          </TimedLoadingState>
        ) : history.isError ? (
          <ApiErrorState
            error={history.error}
            fallback={t("settings.language.history.loadError")}
            onRetry={() => void history.refetch()}
          />
        ) : versions.length === 0 ? (
          <p className="text-sm text-fg-muted">{t("settings.language.history.empty")}</p>
        ) : (
          <DataTable<LanguagePackHistoryVersion>
            ariaLabel={t("settings.language.history.title")}
            rows={versions}
            getRowKey={(version) => version.id}
            rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
            stickyHeader
            visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
            scrollAriaLabel={t("settings.language.history.title")}
            tableClassName="w-full text-sm"
            columns={[
              {
                key: "saved_at",
                header: t("settings.language.history.column.savedAt"),
                rowHeader: true,
                className: "font-medium text-fg md:whitespace-nowrap",
                render: (version) => formatDateTime(version.saved_at),
              },
              {
                key: "saved_by",
                header: t("settings.language.history.column.savedBy"),
                // 狭い画面では操作の列を近づけるため出さない（保存した人は詳細の情報）。
                className: "hidden text-fg md:table-cell",
                headerClassName: "hidden md:table-cell",
                render: (version) => version.saved_by ?? "—",
              },
              {
                key: "changes",
                header: t("settings.language.history.column.changes"),
                className: "text-fg",
                render: (version) => (
                  <span className="flex flex-wrap items-center gap-2">
                    {t("settings.language.history.changedFields", { count: version.changed_fields })}
                    {version.id === current?.id ? (
                      <StatusBadge variant="success" label={t("settings.language.history.current")} />
                    ) : null}
                    {version.restored_from ? (
                      <StatusBadge variant="info" label={t("settings.language.history.restoredBadge")} />
                    ) : null}
                  </span>
                ),
              },
              {
                key: "actions",
                header: t("settings.language.history.column.actions"),
                className: "md:whitespace-nowrap",
                render: (version) =>
                  version.id === current?.id ? null : (
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={RotateCcw}
                      loading={restore.isPending && restore.variables === version.id}
                      disabled={dirty || restore.isPending}
                      title={dirty ? t("settings.language.history.dirty") : undefined}
                      onClick={() => void restoreVersion(version)}
                    >
                      {t("settings.language.history.restore")}
                    </Button>
                  ),
              },
            ]}
          />
        )}
      </CardContent>
    </Card>
  );
}
