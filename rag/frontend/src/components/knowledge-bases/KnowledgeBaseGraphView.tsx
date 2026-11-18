"use client";

import { useState } from "react";
import { Share2 } from "lucide-react";

import { DataLoadFailure } from "@/components/DataLoadStates";
import { EmptyState } from "@/components/StateViews";
import { Disclosure, Skeleton, TimedLoadingState } from "@production-ready/ui";
import { KnowledgeBaseGraphCanvas } from "./KnowledgeBaseGraphCanvas";
import { useStructureGraphOverview } from "@/lib/queries";
import { t } from "@/lib/i18n";

/**
 * KB の関係情報(文書と章・節の見出しのつながり)を可視化する読み取り専用ビュー。
 * 展開時のみ取得し、共通画布で文書と章節を階層に配置して描画する。
 */
export function KnowledgeBaseGraphView({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const [open, setOpen] = useState(false);
  const query = useStructureGraphOverview(open ? knowledgeBaseId : null);
  const isEmpty = query.data && (query.data.status === "empty" || query.data.documents.length === 0);

  return (
    // 文書構造索引の構築のカードの最下部に置く。カードの中の開閉の領域なので sunken の card にし、見出しは開閉で変えない
    // 固定の名詞、補足は description（#397 / #5480）。開いたときだけ取得して描く（#5453）。
    <Disclosure
      surface="sunken"
      summary={t("knowledgeBases.graph.title")}
      description={t("knowledgeBases.graph.hint")}
      icon={Share2}
      open={open}
      onOpenChange={setOpen}
      data-testid="knowledge-base-graph-disclosure"
    >
      {open ? (
        <div id="knowledge-base-graph" className="space-y-2">
          {query.isPending ? (
            <TimedLoadingState
              label={t("knowledgeBases.graph.loading")}
              operationKey="knowledge-base-graph-load"
              testId="knowledge-base-graph-loading"
            >
              <Skeleton className="h-[25.71rem] w-full rounded-md" />
            </TimedLoadingState>
          ) : query.isError ? (
            // 読み取れなかったことを「関係情報がまだありません」と区別する（#4217）。
            <DataLoadFailure
              error={query.error}
              fallback={t("knowledgeBases.graph.error")}
              onRetry={() => void query.refetch()}
            />
          ) : isEmpty ? (
            <EmptyState
              title={t("knowledgeBases.graph.empty")}
              hint={t("knowledgeBases.graph.emptyHint")}
            />
          ) : (
            <>
              {query.data ? <KnowledgeBaseGraphCanvas overview={query.data} knowledgeBaseId={knowledgeBaseId} /> : null}
            </>
          )}
        </div>
      ) : null}
    </Disclosure>
  );
}
