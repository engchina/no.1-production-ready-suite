import { FileText } from "lucide-react";

import type { RetrievedChunk } from "@/lib/api";
import { citationMetadataChips, type CitationMetadataChip } from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";

/** 引用チャンク1件の表示。retrieval 由来の score/metadata を併記。 */
export function CitationCard({ chunk, index }: { chunk: RetrievedChunk; index: number }) {
  const score = chunk.rerank_score ?? chunk.score;
  const chips = citationMetadataChips(chunk.metadata);
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
      {chips.length > 0 ? (
        <dl className="mt-3 flex flex-wrap gap-2">
          {chips.map((chip) => (
            <MetadataChip key={chip.id} chip={chip} />
          ))}
        </dl>
      ) : null}
      {chunk.category_name ? (
        <span className="mt-2 inline-block rounded-full bg-info-bg px-2 py-0.5 text-xs text-info">
          {chunk.category_name}
        </span>
      ) : null}
    </li>
  );
}

function MetadataChip({ chip }: { chip: CitationMetadataChip }) {
  return (
    <div className="min-w-0 max-w-full rounded-full border border-border bg-background px-2.5 py-1 text-xs text-muted sm:max-w-80">
      <dt className="sr-only">{chipLabel(chip)}</dt>
      <dd className="truncate">{chipValue(chip)}</dd>
    </div>
  );
}

function chipValue(chip: CitationMetadataChip): string {
  switch (chip.id) {
    case "page":
      return t("search.citation.page", { page: chip.value });
    case "content_kind":
      return t("search.citation.contentKindValue", {
        kind: contentKindLabel(chip.value),
      });
    case "section_title":
      return t("search.citation.sectionTitleValue", { title: chip.value });
    case "section_path":
      return t("search.citation.sectionPathValue", { path: chip.value });
    case "chunk_profile":
      return t("search.citation.profileValue", { profile: chip.value });
  }
}

function chipLabel(chip: CitationMetadataChip): string {
  switch (chip.id) {
    case "page":
      return t("search.citation.pageLabel");
    case "content_kind":
      return t("search.citation.contentKindLabel");
    case "section_title":
      return t("search.citation.sectionTitleLabel");
    case "section_path":
      return t("search.citation.sectionPathLabel");
    case "chunk_profile":
      return t("search.citation.profileLabel");
  }
}

function contentKindLabel(kind: string): string {
  switch (kind) {
    case "text":
      return t("search.filters.contentKind.text");
    case "list":
      return t("search.filters.contentKind.list");
    case "table":
      return t("search.filters.contentKind.table");
    case "figure":
      return t("search.filters.contentKind.figure");
    default:
      return kind;
  }
}
