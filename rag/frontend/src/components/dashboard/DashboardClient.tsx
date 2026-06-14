"use client";

import { useDashboardSummary } from "@/lib/queries";
import { ApiError } from "@/lib/api";
import { ErrorState } from "@/components/StateViews";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { DashboardIngestionQuality } from "@/lib/api";
import { DashboardHeader } from "./DashboardHeader";
import { FeatureHub } from "./FeatureHub";
import { IngestionQuality } from "./IngestionQuality";
import { MetricCards } from "./MetricCards";
import { RecentActivity } from "./RecentActivity";
import { SystemInfo } from "./SystemInfo";
import { WorkflowSteps } from "./WorkflowSteps";

const EMPTY_INGESTION_QUALITY: DashboardIngestionQuality = {
  document_count: 0,
  structured_document_count: 0,
  element_count: 0,
  table_count: 0,
  list_count: 0,
  page_count: 0,
  chunk_profile_counts: {},
  content_kind_counts: {},
};

/** ダッシュボード本体。/api/dashboard/summary を購読する。 */
export function DashboardClient() {
  const query = useDashboardSummary();

  return (
    <div className="min-h-dvh">
      <DashboardHeader
        onRefresh={() => void query.refetch()}
        isRefreshing={query.isFetching}
        updatedAt={query.dataUpdatedAt ? new Date(query.dataUpdatedAt).toISOString() : null}
      />
      <div className="space-y-6 p-8">
        {query.isPending ? (
          <DashboardSkeleton />
        ) : query.isError ? (
          <ErrorState
            message={
              query.error instanceof ApiError
                ? query.error.message
                : "ダッシュボードの読み込みに失敗しました。"
            }
            onRetry={() => void query.refetch()}
          />
        ) : (
          <>
            <MetricCards stats={query.data.stats} />
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
              <div className="space-y-6 lg:col-span-2">
                <FeatureHub />
                <WorkflowSteps />
                <RecentActivity activities={query.data.recent_activities} />
              </div>
              <div className="space-y-6 lg:col-span-1">
                <SystemInfo info={query.data.system} />
                <IngestionQuality
                  quality={query.data.ingestion_quality ?? EMPTY_INGESTION_QUALITY}
                />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="p-5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-4 h-7 w-20" />
            <Skeleton className="mt-3 h-3 w-16" />
          </Card>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Skeleton className="h-48 w-full rounded-lg" />
          <Skeleton className="h-24 w-full rounded-lg" />
        </div>
        <div className="space-y-6">
          <Skeleton className="h-56 w-full rounded-lg" />
          <Skeleton className="h-72 w-full rounded-lg" />
        </div>
      </div>
    </>
  );
}
