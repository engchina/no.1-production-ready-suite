import {
  AnswerEvidence,
  EvidenceViews,
  useEvidenceSource,
  type EvidenceGroup,
  type EvidenceSourceState,
} from "@production-ready/ui";
import {
  ArrowUpDown,
  BookOpen,
  File,
  Image,
  Link2,
  ListChecks,
  ListTree,
  Network,
  Search,
  type LucideIcon,
} from "lucide-react";

import type { RetrievedChunk } from "@/lib/api";
import { citationAnswerUse, citationModelUsed } from "@/lib/chunk-metadata";
import {
  evidenceExcerpt,
  evidenceGroups,
  evidenceLocation,
  evidenceRoleKind,
  evidenceRoleLabel,
  type EvidenceGroup as RagEvidenceGroup,
  type EvidenceRoleKind,
} from "@/lib/evidence-groups";
import { t } from "@/lib/i18n";

import { CitationActions, type CitationCardOptions } from "./CitationCard";
import { citationEvidenceItem, EVIDENCE_LABELS } from "./citation-evidence";
import { CitationSourceSheet } from "./CitationSourceSheet";

const ROLE_ICONS: Record<EvidenceRoleKind, LucideIcon> = {
  retrieved: Search,
  neighbor: ArrowUpDown,
  samePage: File,
  sameSection: ListTree,
  operation: ListChecks,
  visual: Image,
  section: BookOpen,
  relation: Network,
  context: Link2,
};

/**
 * 回答の根拠（チャットと RAG 検索で共通。#2097）。「AI に渡した N 件（K のまとまり）／ 回答で引用 M 件 …」の
 * 折りたたみ（既定は閉じる）に、AI に渡した箇所の一覧を入れる。回答の本文の直後、回答の評価の前に置く。
 * 折りたたみ・見出し・見方の切り替え・カードは 3 製品で共通の `AnswerEvidence`（#2328）。
 */
export function AnswerCitations({
  citations,
  diagnostics,
  idPrefix,
  cardProps,
  className,
  listTestId,
}: {
  citations: RetrievedChunk[];
  /** 回答の診断（`diagnostics.answer`）。根拠の木からまとまりを作る。生成中は null。 */
  diagnostics: unknown;
  idPrefix: string;
  cardProps?: CitationCardOptions;
  className?: string;
  listTestId?: string;
}) {
  const source = useEvidenceSource<number>();
  if (!citations.length) return null;
  const groups = evidenceGroups(citations, diagnostics);
  const preview = cardProps?.preview ?? true;
  return (
    <>
      <AnswerEvidence
        items={citations.map(citationEvidenceItem)}
        groups={groupViews(groups, preview ? source.open : null)}
        idPrefix={idPrefix}
        labels={EVIDENCE_LABELS}
        className={className}
        listTestId={listTestId}
        renderActions={(_item, index) => (
          <CitationActions chunk={citations[index]} {...cardProps} onPreview={() => source.open(index)} />
        )}
      />
      {preview ? <Preview citations={citations} source={source} kind="answer" /> : null}
    </>
  );
}

/**
 * AI に渡した箇所の一覧（#2097）。「順に表示」は引用のカード、「まとまりごと」は同じ節ごとにまとめて AI に渡した
 * 単位で、各箇所の役割・位置・本文の抜粋を出す（3 製品で共通の `EvidenceViews`。#2328）。まとまりが分からない回答
 * （根拠の木の無い記録）は一覧だけ。
 */
export function CitationList({
  citations,
  groups,
  idPrefix,
  cardProps = {},
  listTestId,
}: {
  citations: RetrievedChunk[];
  /** {@link evidenceGroups} の結果。見出しの件数と同じものを渡す。 */
  groups: RagEvidenceGroup[] | null;
  /** 同じ画面に複数の一覧（チャットの回答ごと）があるので、タブの id を分ける。 */
  idPrefix: string;
  cardProps?: CitationCardOptions;
  listTestId?: string;
}) {
  const source = useEvidenceSource<number>();
  const preview = cardProps.preview ?? true;
  return (
    <>
      <EvidenceViews
        items={citations.map(citationEvidenceItem)}
        groups={groupViews(groups, preview ? source.open : null)}
        idPrefix={idPrefix}
        labels={EVIDENCE_LABELS}
        listTestId={listTestId}
        renderActions={(_item, index) => (
          <CitationActions chunk={citations[index]} {...cardProps} onPreview={() => source.open(index)} />
        )}
      />
      {preview ? <Preview citations={citations} source={source} kind="search" /> : null}
    </>
  );
}

/**
 * 引用箇所のプレビュー（右のサイドパネル。#2360）。カードの「引用箇所を表示」と、まとまりの箇所の抜粋から開く
 * （1 つの一覧に 1 つ）。一度開くまでは描かず、閉じた後は描いたままにして開いた操作へフォーカスを戻す。
 */
function Preview({
  citations,
  source,
  kind,
}: {
  citations: RetrievedChunk[];
  source: EvidenceSourceState<number>;
  kind: "answer" | "search";
}) {
  if (!source.mounted || source.shown == null) return null;
  return (
    <CitationSourceSheet
      chunk={citations[source.shown] ?? null}
      index={source.shown}
      open={source.selected !== null}
      onClose={source.close}
      kind={kind}
    />
  );
}

/** RAG のまとまり → 共通の「まとまりごと」の表示。 */
function groupViews(
  groups: RagEvidenceGroup[] | null,
  onOpen: ((index: number) => void) | null,
): EvidenceGroup[] | null {
  if (!groups) return null;
  return groups.map((group) => ({
    key: group.key,
    fileName: group.fileName,
    section: group.section,
    pages: group.pages ? t("search.citation.page", { page: group.pages }) : "",
    entries: group.items.map((item) => {
      const kind = evidenceRoleKind(item.role);
      return {
        key: item.chunk.chunk_id,
        index: item.index,
        roleLabel: evidenceRoleLabel(item.role),
        roleIcon: ROLE_ICONS[kind],
        roleKind: kind,
        location: evidenceLocation(item.chunk),
        excerpt: evidenceExcerpt(item.chunk.text),
        usedInAnswer: citationModelUsed(item.chunk),
        answerUse: citationAnswerUse(item.chunk),
        chunkId: item.chunk.chunk_id,
        onOpen: onOpen ? () => onOpen(item.index) : undefined,
      };
    }),
  }));
}
