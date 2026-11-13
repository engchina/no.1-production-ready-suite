"use client";

import { Button, Card, CardContent, CardHeader, CardTitle, FormSkeleton, Switch, TimedLoadingState } from "@production-ready/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import { api, type KnowledgeBaseWikiData } from "@/lib/api";
import { t } from "@/lib/i18n";

export function KnowledgeBaseWiki({ id, editable }: { id: string; editable: boolean }) {
  const cache = useQueryClient();
  const key = ["knowledge-base-wiki", id];
  const query = useQuery({ queryKey: key, queryFn: () => api.getKnowledgeBaseWiki(id), refetchInterval: 5000 });
  const saved = (data: KnowledgeBaseWikiData) => cache.setQueryData(key, data);
  const update = useMutation({ mutationFn: (enabled: boolean) => api.setKnowledgeBaseWiki(id, enabled), onSuccess: saved });
  const retry = useMutation({ mutationFn: () => api.retryKnowledgeBaseWiki(id), onSuccess: saved });
  const data = query.data;
  const counts = data?.counts ?? {};
  const error = update.error ?? retry.error ?? query.error;
  return <Card data-testid="knowledge-base-wiki">
    <CardHeader><CardTitle>{t("wiki.title")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      {!data && query.isPending ? <TimedLoadingState label={t("wiki.loading")}><FormSkeleton fields={1} actions={false} /></TimedLoadingState> : null}
      {error ? <ApiErrorState error={error} fallback={t("wiki.error")} onRetry={() => { update.reset(); retry.reset(); void query.refetch(); }} /> : null}
      {data ? <>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-medium">{t("wiki.build")}</p>
            <p id={`wiki-build-help-${id}`} className="text-xs text-fg-muted">{t("wiki.buildHelp")}</p>
          </div>
          <Switch aria-label={t("wiki.build")} aria-describedby={`wiki-build-help-${id}`} checked={data.enabled} onCheckedChange={(enabled) => { retry.reset(); update.mutate(enabled); }} disabled={!editable || update.isPending} className="shrink-0" />
        </div>
        <div aria-live="polite" className="space-y-2 text-sm text-fg-muted">
          <p>{t(data.enabled ? "wiki.enabled" : "wiki.disabled")}</p>
          <p>{t("wiki.progress", { pending: counts.NOT_BUILT ?? 0, queued: counts.QUEUED ?? 0, running: counts.RUNNING ?? 0, ready: counts.READY ?? 0, stale: counts.STALE ?? 0, failed: counts.FAILED ?? 0 })}</p>
          {Object.values(counts).every((count) => count === 0) ? <p>{t("wiki.empty")}</p> : null}
        </div>
        {data.enabled && (counts.FAILED ?? 0) > 0 ? <Button variant="secondary" icon={RefreshCw} loading={retry.isPending} disabled={!editable} onClick={() => { update.reset(); retry.mutate(); }}>{t("wiki.retry")}</Button> : null}
      </> : null}
    </CardContent>
  </Card>;
}
