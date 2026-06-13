import { FileText } from "lucide-react";

import type { RetrievedChunk } from "@/lib/api";

/** 引用チャンク1件の表示。retrieval 由来の score/metadata を併記。 */
export function CitationCard({ chunk, index }: { chunk: RetrievedChunk; index: number }) {
  const score = chunk.rerank_score ?? chunk.score;
  return (
    <li className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
            {index + 1}
          </span>
          <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-foreground">
            <FileText size={14} className="shrink-0 text-muted" aria-hidden />
            <span className="truncate" title={chunk.file_name ?? chunk.document_id}>
              {chunk.file_name ?? chunk.document_id}
            </span>
          </span>
        </div>
        <span className="tnum shrink-0 rounded bg-background px-2 py-0.5 text-xs text-muted">
          {score.toFixed(3)}
        </span>
      </div>
      <p className="mt-2 line-clamp-4 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">
        {chunk.text}
      </p>
      {chunk.category_name ? (
        <span className="mt-2 inline-block rounded-full bg-info-bg px-2 py-0.5 text-xs text-info">
          {chunk.category_name}
        </span>
      ) : null}
    </li>
  );
}
