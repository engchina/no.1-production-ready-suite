"use client";

import { useState } from "react";
import { Share2 } from "lucide-react";

import { DegradedBanner } from "@/components/DegradedBanner";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Disclosure, Skeleton, TimedLoadingState } from "@production-ready/ui";
import { KnowledgeBaseGraphCanvas } from "./KnowledgeBaseGraphCanvas";
import { useKnowledgeBaseGraph } from "@/lib/queries";
import { t } from "@/lib/i18n";

/**
 * KB の関係情報(文書と章・節の見出しのつながり)を可視化する読み取り専用ビュー。
 * 展開時のみ取得し、共通画布で文書と章節を階層に配置して描画する。
 */
export function KnowledgeBaseGraphView({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const [open, setOpen] = useState(false);
  const query = useKnowledgeBaseGraph(open ? knowledgeBaseId : null);
  // DB が止まっていると backend は空の図と warning_messages を返す。取得できなかったことを
  // 「関係情報がまだありません」と区別して出す（空の案内は、取得できて本当に空のときだけ）。
  const warnings = query.data?.warning_messages ?? [];
  const degraded = warnings.length > 0;
  const isEmpty = query.data && (query.data.status === "empty" || query.data.nodes.length === 0);

  return (
    // 共有の Disclosure（card）。見出しの高さは 40px・タッチ端末 44px（#1135）。関係情報は開いたときだけ取得して描く。
    <Disclosure
      summary={open ? t("knowledgeBases.graph.hide") : t("knowledgeBases.graph.show")}
      icon={Share2}
      open={open}
      onOpenChange={setOpen}
    >
      {open ? (
        <div id="knowledge-base-graph" className="space-y-2">
          <p className="text-xs text-fg-muted">{t("knowledgeBases.graph.hint")}</p>
          {query.isPending ? (
            <TimedLoadingState
              label={t("knowledgeBases.graph.loading")}
              operationKey="knowledge-base-graph-load"
              testId="knowledge-base-graph-loading"
            >
              <Skeleton className="h-[25.71rem] w-full rounded-md" />
            </TimedLoadingState>
          ) : query.isError ? (
            <ErrorState message={t("knowledgeBases.graph.error")} onRetry={() => void query.refetch()} />
          ) : degraded ? (
            <DegradedBanner
              messages={warnings}
              onRetry={() => void query.refetch()}
              isRetrying={query.isFetching}
            />
          ) : isEmpty ? (
            <EmptyState
              title={t("knowledgeBases.graph.empty")}
              hint={t("knowledgeBases.graph.emptyHint")}
            />
          ) : (
            <>
              {query.data ? <KnowledgeBaseGraphCanvas data={query.data} /> : null}
              {query.data?.truncated ? (
                <p className="text-xs text-fg-muted">{t("knowledgeBases.graph.truncated")}</p>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </Disclosure>
  );
}
