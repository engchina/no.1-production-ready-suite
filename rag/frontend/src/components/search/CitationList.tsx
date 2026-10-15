import { CopyButton, Disclosure, TabPanel, Tabs } from "@production-ready/ui";
import {
  ArrowUpDown,
  BookOpen,
  File,
  FileText,
  Image,
  Link2,
  ListChecks,
  ListTree,
  LocateFixed,
  Network,
  Search,
  type LucideIcon,
} from "lucide-react";
import { useState, type ComponentProps } from "react";

import type { RetrievedChunk } from "@/lib/api";
import { citationAnswerUse, citationCountLabel, citationModelUsed } from "@/lib/chunk-metadata";
import {
  evidenceExcerpt,
  evidenceGroups,
  evidenceLocation,
  evidenceRoleKind,
  evidenceRoleLabel,
  type EvidenceGroup,
  type EvidenceGroupItem,
  type EvidenceRoleKind,
} from "@/lib/evidence-groups";
import { t } from "@/lib/i18n";

import { AnswerUseBadge } from "./AnswerUseBadge";
import { CitationCard } from "./CitationCard";
import { CitationPreviewDialog } from "./CitationPreviewDialog";

type CardProps = Omit<ComponentProps<typeof CitationCard>, "chunk" | "index">;

const LIST_CLASS = "bounded-scroll-area-lg space-y-2 pr-1";

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
 * 折りたたみ（既定は閉じる）に、AI に渡した箇所の一覧を入れる。回答の本文の直後、処理の経過の前に置く。
 */
export function AnswerCitations({
  citations,
  diagnostics,
  idPrefix,
  cardProps,
  className,
}: {
  citations: RetrievedChunk[];
  /** 回答の診断（`diagnostics.answer`）。根拠の木からまとまりを作る。生成中は null。 */
  diagnostics: unknown;
  idPrefix: string;
  cardProps?: CardProps;
  className?: string;
}) {
  if (!citations.length) return null;
  const groups = evidenceGroups(citations, diagnostics);
  return (
    <Disclosure
      variant="plain"
      summary={citationCountLabel(citations, groups?.length ?? null)}
      className={className}
      data-testid="answer-citations"
    >
      <CitationList citations={citations} groups={groups} idPrefix={idPrefix} cardProps={cardProps} />
    </Disclosure>
  );
}

/**
 * AI に渡した箇所の一覧（#2097）。「順に表示」は引用のカード、「まとまりごと」は同じ節ごとにまとめて AI に渡した
 * 単位で、各箇所の役割・位置・本文の抜粋を出す。2 つの見方は同じ箇所を同じ番号で数えるので、まとまりの件数の
 * 合計は見出しの「AI に渡した N 件」と一致する。まとまりが分からない回答（根拠の木の無い記録）は一覧だけ。
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
  groups: EvidenceGroup[] | null;
  /** 同じ画面に複数の一覧（チャットの回答ごと）があるので、タブの id を分ける。 */
  idPrefix: string;
  cardProps?: CardProps;
  listTestId?: string;
}) {
  const [view, setView] = useState<"list" | "groups">("list");
  const list = (
    // 一定の件数を超えたら一覧の中で縦にスクロールする（共有の bounded-scroll-area-lg。画面ごとに高さを書かない）。
    <ul className={LIST_CLASS} data-testid={listTestId}>
      {citations.map((chunk, index) => (
        <CitationCard key={`${chunk.chunk_id}-${index}`} chunk={chunk} index={index} {...cardProps} />
      ))}
    </ul>
  );
  if (!groups) return list;
  const prefix = `${idPrefix}-evidence`;
  return (
    <div className="space-y-3">
      <Tabs
        idPrefix={prefix}
        ariaLabel={t("evidence.view.label")}
        value={view}
        onChange={(value) => setView(value === "groups" ? "groups" : "list")}
        items={[
          { id: "list", label: t("evidence.view.list"), count: citations.length },
          { id: "groups", label: t("evidence.view.groups"), count: groups.length },
        ]}
      />
      <TabPanel id="list" value={view} idPrefix={prefix}>
        {list}
      </TabPanel>
      <TabPanel id="groups" value={view} idPrefix={prefix}>
        <EvidenceGroupList groups={groups} preview={cardProps.preview ?? true} />
      </TabPanel>
    </div>
  );
}

function EvidenceGroupList({ groups, preview }: { groups: EvidenceGroup[]; preview: boolean }) {
  return (
    <div className="space-y-2">
      <p className="text-xs leading-relaxed text-fg-muted">{t("evidence.groups.hint")}</p>
      <ol className={LIST_CLASS} data-testid="evidence-groups">
        {groups.map((group) => (
          <li
            key={group.key}
            className="rounded-lg border border-border bg-surface"
            data-testid="evidence-group"
            aria-label={t("evidence.groups.aria", { file: group.fileName, count: group.items.length })}
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-2">
              <FileText size={14} className="shrink-0 text-fg-muted" aria-hidden />
              <span className="min-w-0 break-words text-sm font-medium text-fg">{group.fileName}</span>
              {group.section ? (
                <span className="min-w-0 break-words text-xs text-fg-muted" data-testid="evidence-group-section">
                  {group.section}
                </span>
              ) : null}
              {group.pages ? (
                <span className="tnum text-xs text-fg-muted">
                  {t("search.citation.page", { page: group.pages })}
                </span>
              ) : null}
              <span className="tnum ml-auto text-xs text-fg-muted" data-testid="evidence-group-count">
                {t("evidence.groups.count", { count: group.items.length })}
              </span>
            </div>
            <ul className="divide-y divide-border">
              {group.items.map((item) => (
                <EvidenceItem key={item.chunk.chunk_id} item={item} preview={preview} />
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  );
}

function EvidenceItem({ item, preview }: { item: EvidenceGroupItem; preview: boolean }) {
  const [open, setOpen] = useState(false);
  const { chunk, index } = item;
  const kind = evidenceRoleKind(item.role);
  const RoleIcon = ROLE_ICONS[kind];
  const location = evidenceLocation(chunk);
  const excerpt = evidenceExcerpt(chunk.text);
  return (
    <li className="space-y-1 px-3 py-2" data-testid="evidence-item" data-role-kind={kind}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          data-surface-tint="accent"
          className="tnum flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-xs font-semibold text-accent-fg"
        >
          {index + 1}
        </span>
        <span className="inline-flex items-center gap-1 text-xs font-medium text-fg" data-testid="evidence-role">
          <RoleIcon size={14} className="shrink-0 text-fg-muted" aria-hidden />
          {evidenceRoleLabel(item.role)}
        </span>
        {location ? (
          <span className="min-w-0 break-words text-xs text-fg-muted">{location}</span>
        ) : null}
        {citationModelUsed(chunk) ? <AnswerUseBadge use={citationAnswerUse(chunk)} /> : null}
      </div>
      {preview ? (
        // 本文の抜粋を押すと、引用のカードの「引用箇所を表示」と同じダイアログで原文の位置を開く。
        <button
          type="button"
          className="flex w-full items-start gap-1.5 rounded-md px-1 py-0.5 text-left text-sm leading-relaxed text-fg/90 hover:bg-surface-hover"
          onClick={() => setOpen(true)}
          data-testid="evidence-item-open"
        >
          <span className="line-clamp-3 min-w-0 flex-1 break-words">{excerpt}</span>
          <LocateFixed size={14} className="mt-1 shrink-0 text-accent-fg" aria-hidden />
          <span className="sr-only">{t("evidence.item.openLabel", { index: index + 1 })}</span>
        </button>
      ) : (
        <p className="line-clamp-3 break-words px-1 text-sm leading-relaxed text-fg/90">{excerpt}</p>
      )}
      <Disclosure variant="plain" size="sm" summary={t("evidence.item.details")}>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-fg-muted">{t("evidence.item.chunkId")}</span>
          <code className="min-w-0 break-all font-mono text-fg" data-testid="evidence-item-chunk-id">
            {chunk.chunk_id}
          </code>
          <CopyButton
            text={chunk.chunk_id}
            variant="ghost"
            size="sm"
            label={t("evidence.item.copyId")}
            copiedMessage={t("evidence.item.copiedId")}
            failedMessage={t("evidence.item.copyFailed")}
          />
        </div>
      </Disclosure>
      {preview ? (
        <CitationPreviewDialog chunk={chunk} open={open} onClose={() => setOpen(false)} />
      ) : null}
    </li>
  );
}
