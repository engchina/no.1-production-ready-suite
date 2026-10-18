import { BookOpen, ExternalLink, FileText, History, LocateFixed, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { cn } from "../../lib/utils";
import { StatusBadge } from "../data/status-badge";
import { CopyButton } from "../ui/copy-button";
import { Disclosure } from "../ui/disclosure";
import { TabPanel, Tabs } from "../ui/tabs";

/**
 * 回答の根拠（AI に渡した箇所の一覧。RAG のチャット・RAG 検索・Agent のチャットで共通。#2328）。
 *
 * 製品は自分の根拠の形（RAG の `RetrievedChunk`・Agent の成果物 `answer_evidence`）を
 * {@link EvidenceListItem} に写して渡す。文言は 3 製品で同じ書き方にするため {@link evidenceLabels} の既定を使う。
 */

/** 回答での使われ方。confirmed は質問の条件への該当確認済みの記載、reference は該当が確認できていない参考の記載。 */
export type EvidenceAnswerUse = "confirmed" | "reference";

/** 根拠の場所・種類のチップ（頁・節・内容の種類など。翻訳済み）。 */
export interface EvidenceChip {
  id: string;
  /** 読み上げの名前（例:「ページ」）。画面には出さない。 */
  label: string;
  /** 表示する値（例:「p.3」）。幅で切れたときも title で全文を確かめられる。 */
  value: string;
}

/** チップの後ろに並べる製品の補足（RAG の処理レシピ・文書の分類など）。 */
export interface EvidenceTag {
  id: string;
  value: string;
  icon?: LucideIcon;
  /** info は分類の色の面（色だけで意味を持たせない）。既定は neutral。 */
  tone?: "neutral" | "info";
}

/** 根拠 1 件のデータの契約（製品に依らない）。 */
export interface EvidenceListItem {
  /** 一覧の中で一意の key（chunk の id など）。 */
  key: string;
  /** 文書名（無ければ製品が文書の id などを入れる）。 */
  fileName: string;
  /** 本文（抜粋）。カードでは 3 行まで出す。 */
  text: string;
  /**
   * 回答で引用した箇所か。回答を作らない検索の結果など、引用したかが分からないときは null
   * （見出しは件数だけにし、バッジを出さない）。
   */
  usedInAnswer: boolean | null;
  /** 回答での使われ方。引用していない・分からなければ null。 */
  answerUse: EvidenceAnswerUse | null;
  /** 新しい版に置き換えた文書（旧版）の根拠か。 */
  superseded?: boolean;
  /** 検索の経路のバッジ（翻訳済み。例:「Vector #1」「Rerank #2」）。 */
  routeBadges?: readonly string[];
  /** 取得のスコア。分からなければ null（「—」）。 */
  score: number | null;
  /** Rerank のスコア（0〜1）。Rerank していなければ null。 */
  rerankScore: number | null;
  chips?: readonly EvidenceChip[];
  tags?: readonly EvidenceTag[];
  /** 原典の URL（https のときだけ渡す）。 */
  sourceUrl?: string | null;
}

/** 文言（すべて翻訳済み）。既定は {@link evidenceLabels}。 */
export interface EvidenceLabels {
  /** 見出しの件数（例:「AI に渡した 12 件」）。 */
  count: (count: number) => string;
  /** まとまりの数が分かるときの件数（例:「AI に渡した 12 件（4 のまとまり）」）。 */
  countWithGroups: (count: number, groups: number) => string;
  /** 例:「回答で引用 3 件」。 */
  used: (count: number) => string;
  /** 例:「うち該当確認済み 2 件」。 */
  confirmed: (count: number) => string;
  /** 例:「参考 1 件」。 */
  reference: (count: number) => string;
  separator: string;
  /** 回答で引用した箇所のバッジ。 */
  usedBadge: string;
  /** 参考の記載として引用した箇所のバッジ。 */
  referenceBadge: string;
  superseded: string;
  /** 経路のバッジの「Evidence」（AI に渡した箇所の印）。 */
  evidenceBadge: string;
  scoreRetrieval: string;
  scoreRerank: string;
  scoreRerankMissing: string;
  scoreMeter: (label: string, value: string) => string;
  openSource: string;
  /** 段落の下の根拠の行（例:「根拠：文書 A.pdf p.3」）。`location` は空のことがある。 */
  citation: (fileName: string, location: string) => string;
  /** 押せる根拠の行の読み上げ（例:「根拠：文書 A.pdf p.3 を表示」）。 */
  citationOpen: (citation: string) => string;
  /** 見方の切り替えの名前。 */
  viewLabel: string;
  viewList: string;
  viewGroups: string;
  groupsHint: string;
  groupCount: (count: number) => string;
  groupAria: (fileName: string, count: number) => string;
  itemOpen: (index: number) => string;
  itemDetails: string;
  itemChunkId: string;
  copyId: string;
  copiedId: string;
  copyFailed: string;
}

/** 3 製品で同じ根拠の文言。 */
export function evidenceLabels(overrides: Partial<EvidenceLabels> = {}): EvidenceLabels {
  return {
    count: (count) => `AI に渡した ${count} 件`,
    countWithGroups: (count, groups) => `AI に渡した ${count} 件（${groups} のまとまり）`,
    used: (count) => `回答で引用 ${count} 件`,
    confirmed: (count) => `うち該当確認済み ${count} 件`,
    reference: (count) => `参考 ${count} 件`,
    separator: " ／ ",
    usedBadge: "回答で引用",
    referenceBadge: "参考",
    superseded: "旧版",
    evidenceBadge: "Evidence",
    scoreRetrieval: "取得スコア",
    scoreRerank: "Rerank スコア",
    scoreRerankMissing: "Rerank 未実行",
    scoreMeter: (label, value) => `${label}: ${value}`,
    openSource: "原典を開く",
    citation: (fileName, location) => `根拠：${fileName}${location ? ` ${location}` : ""}`,
    citationOpen: (citation) => `${citation} を表示`,
    viewLabel: "根拠の見方",
    viewList: "順に表示",
    viewGroups: "まとまりごと",
    groupsHint:
      "同じ文書の同じ節から AI に渡した箇所を 1 つのまとまりにしています。箇所を押すと原文の位置を表示します。",
    groupCount: (count) => `${count} 件`,
    groupAria: (fileName, count) => `${fileName} のまとまり（${count} 件）`,
    itemOpen: (index) => `${index} 番の箇所の原文の位置を表示`,
    itemDetails: "詳細",
    itemChunkId: "箇所の ID",
    copyId: "ID をコピー",
    copiedId: "ID をコピーしました",
    copyFailed: "コピーできませんでした。ID を選んでコピーしてください。",
    ...overrides,
  };
}

export const DEFAULT_EVIDENCE_LABELS: EvidenceLabels = evidenceLabels();

type CountItem = Pick<EvidenceListItem, "usedInAnswer" | "answerUse">;

/**
 * 見出しの件数と内訳（「AI に渡した 45 件（12 のまとまり）／ 回答で引用 13 件 ／ うち該当確認済み 10 件 ／ 参考 3 件」）。
 * まとまりは数が分かるときだけ、引用したかが分からない（検索だけ）なら件数だけ、0 件の項目は出さない。
 */
export function evidenceCountLabel(
  items: readonly CountItem[],
  groupCount: number | null = null,
  labels: EvidenceLabels = DEFAULT_EVIDENCE_LABELS,
): string {
  const parts = [groupCount ? labels.countWithGroups(items.length, groupCount) : labels.count(items.length)];
  if (items.some((item) => item.usedInAnswer !== null)) {
    const used = items.filter((item) => item.usedInAnswer === true);
    const confirmed = used.filter((item) => item.answerUse === "confirmed").length;
    const reference = used.filter((item) => item.answerUse === "reference").length;
    if (used.length) parts.push(labels.used(used.length));
    if (confirmed) parts.push(labels.confirmed(confirmed));
    if (reference) parts.push(labels.reference(reference));
  }
  return parts.join(labels.separator);
}

/**
 * 回答で引用した箇所のバッジ。該当確認済みの記載は「回答で引用」（success）、該当が確認できていない記載は「参考」、
 * 使われ方が分からない引用は「回答で引用」（info）。色だけで分けず、文言とアイコンで区別する。
 */
export function EvidenceAnswerUseBadge({
  use,
  labels = DEFAULT_EVIDENCE_LABELS,
}: {
  use: EvidenceAnswerUse | null;
  labels?: EvidenceLabels;
}) {
  if (use === "reference") {
    return <StatusBadge variant="neutral" icon={BookOpen} label={labels.referenceBadge} />;
  }
  return <StatusBadge variant={use === "confirmed" ? "success" : "info"} label={labels.usedBadge} />;
}

export interface EvidenceCardProps {
  item: EvidenceListItem;
  /** 番号（0 始まり）。 */
  index: number;
  labels?: EvidenceLabels;
  /**
   * カードの下の操作の行（引用箇所の表示・根拠の評価・図を開くなど）。無ければ区切り線の行を出さない。
   */
  actions?: ReactNode;
  /** カードの最後に置くもの（操作が開くダイアログなど。表示の流れには入らない）。 */
  children?: ReactNode;
}

/** 根拠 1 件のカード（文書名・引用の印・経路・抜粋・場所のチップ・スコア・操作）。 */
export function EvidenceCard({ item, index, labels = DEFAULT_EVIDENCE_LABELS, actions, children }: EvidenceCardProps) {
  const chips = item.chips ?? [];
  const tags = item.tags ?? [];
  const badges = item.routeBadges ?? [];
  const hasMetadata = chips.length > 0 || tags.length > 0 || Boolean(item.sourceUrl);
  return (
    <li className="rounded-lg border border-border bg-surface p-3" data-testid="evidence-card">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_176px] sm:items-start">
        <div data-testid="citation-main" className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
            <span
              data-surface-tint="accent"
              className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-xs font-semibold text-accent-fg"
            >
              {index + 1}
            </span>
            <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-fg">
              <FileText size={14} className="shrink-0 text-fg-muted" aria-hidden />
              <span className="truncate" title={item.fileName}>
                {item.fileName}
              </span>
            </span>
            {item.superseded ? <StatusBadge variant="warning" icon={History} label={labels.superseded} /> : null}
            {item.usedInAnswer ? <EvidenceAnswerUseBadge use={item.answerUse} labels={labels} /> : null}
            {badges.length ? (
              <span className="flex flex-wrap gap-1" data-testid="evidence-route-badges">
                {badges.map((badge) => (
                  <span
                    key={badge}
                    className="rounded-full border border-border bg-surface-sunken px-1.5 py-0.5 text-xs font-medium leading-none text-fg-muted"
                  >
                    {badge}
                  </span>
                ))}
              </span>
            ) : null}
          </div>
          <p
            data-testid="citation-text"
            className="mt-2.5 line-clamp-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-fg/90"
          >
            {item.text}
          </p>
          {hasMetadata ? (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {chips.length > 0 ? (
                <dl className="flex flex-wrap gap-1.5" data-testid="evidence-chips">
                  {chips.map((chip) => (
                    <div
                      key={chip.id}
                      className="min-w-0 max-w-full rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs text-fg-muted sm:max-w-80"
                    >
                      <dt className="sr-only">{chip.label}</dt>
                      <dd className="truncate" title={chip.value}>
                        {chip.value}
                      </dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              {tags.map((tag) => {
                const Icon = tag.icon;
                return tag.tone === "info" ? (
                  <span
                    key={tag.id}
                    data-surface-tint="info"
                    className="inline-flex rounded-full bg-info-subtle px-2 py-0.5 text-xs text-info-fg"
                  >
                    {tag.value}
                  </span>
                ) : (
                  <span
                    key={tag.id}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs text-fg-muted"
                  >
                    {Icon ? <Icon size={14} aria-hidden /> : null}
                    {tag.value}
                  </span>
                );
              })}
              {item.sourceUrl ? (
                <a
                  href={item.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-medium text-accent-fg hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                >
                  {labels.openSource}
                  <ExternalLink size={14} aria-hidden />
                </a>
              ) : null}
            </div>
          ) : null}
        </div>
        <EvidenceScores score={item.score} rerankScore={item.rerankScore} labels={labels} />
      </div>
      {/* 操作が 1 つも無いとき（閲覧だけ）は、区切り線の行を出さない。 */}
      <div className="mt-2.5 flex flex-col gap-2 border-t border-border pt-2.5 empty:hidden sm:flex-row sm:items-center sm:justify-between">
        {actions}
      </div>
      {children}
    </li>
  );
}

function EvidenceScores({
  score,
  rerankScore,
  labels,
}: {
  score: number | null;
  rerankScore: number | null;
  labels: EvidenceLabels;
}) {
  return (
    <div
      data-testid="citation-score-panel"
      className="w-full space-y-2 rounded-md bg-surface-sunken px-2.5 py-2 sm:w-[12.57rem] sm:shrink-0"
    >
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-fg-muted">{labels.scoreRetrieval}</span>
        <span className="tnum shrink-0 text-fg">{formatEvidenceScore(score)}</span>
      </div>
      <ScoreMeter label={labels.scoreRerank} value={rerankScore} labels={labels} />
    </div>
  );
}

function ScoreMeter({ label, value, labels }: { label: string; value: number | null; labels: EvidenceLabels }) {
  if (value == null || !Number.isFinite(value)) {
    return <p className="text-xs font-medium text-fg-muted">{labels.scoreRerankMissing}</p>;
  }
  const valueText = formatEvidenceScore(value);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-fg-muted">{label}</span>
        <span className="tnum shrink-0 text-fg">{valueText}</span>
      </div>
      <div
        data-surface-tint="neutral"
        role="meter"
        aria-label={labels.scoreMeter(label, valueText)}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Math.min(Math.max(value, 0), 1)}
        aria-valuetext={valueText}
        className="h-1.5 overflow-hidden rounded-full bg-surface-hover"
      >
        <div
          data-testid="citation-rerank-fill"
          className="h-full rounded-full bg-success-emphasis"
          style={{ width: `${evidenceScoreMeterPercent(value)}%` }}
        />
      </div>
    </div>
  );
}

/** スコアの表示（小数 3 桁。分からなければ「—」）。 */
export function formatEvidenceScore(value: number | null): string {
  return value != null && Number.isFinite(value) ? value.toFixed(3) : "—";
}

/** Rerank のメーターの幅（0〜100%）。 */
export function evidenceScoreMeterPercent(value: number | null): number {
  if (value == null || !Number.isFinite(value) || value <= 0) return 0;
  return Math.min(100, value * 100);
}

/** 一覧の入れ物（一定の件数を超えたら一覧の中で縦にスクロールする。画面ごとに高さを書かない）。 */
export const EVIDENCE_LIST_CLASS = "bounded-scroll-area-lg space-y-2 pr-1";

export interface EvidenceListProps {
  items: readonly EvidenceListItem[];
  labels?: EvidenceLabels;
  /** 根拠ごとの操作（{@link EvidenceCardProps.actions}）。 */
  renderActions?: (item: EvidenceListItem, index: number) => ReactNode;
  /** 根拠ごとの、カードの最後に置くもの（ダイアログなど）。 */
  renderExtra?: (item: EvidenceListItem, index: number) => ReactNode;
  testId?: string;
}

/** 根拠のカードの一覧（「順に表示」）。 */
export function EvidenceList({ items, labels = DEFAULT_EVIDENCE_LABELS, renderActions, renderExtra, testId }: EvidenceListProps) {
  return (
    <ul className={EVIDENCE_LIST_CLASS} data-testid={testId}>
      {items.map((item, index) => (
        <EvidenceCard
          key={`${item.key}-${index}`}
          item={item}
          index={index}
          labels={labels}
          actions={renderActions?.(item, index)}
        >
          {renderExtra?.(item, index)}
        </EvidenceCard>
      ))}
    </ul>
  );
}

/** まとまりの中の箇所（「まとまりごと」の 1 行）。 */
export interface EvidenceGroupEntry {
  key: string;
  /** 「順に表示」と同じ番号（0 始まり）。 */
  index: number;
  /** 根拠の役割の利用者の言葉（例:「検索で見つかった箇所」）と、区分のアイコン（色だけで区別しない）。 */
  roleLabel: string;
  roleIcon: LucideIcon;
  /** 役割の区分（テスト・計測の印）。 */
  roleKind: string;
  /** 読める位置（引用ラベル・頁・見出し）。空なら出さない。 */
  location: string;
  excerpt: string;
  usedInAnswer: boolean | null;
  answerUse: EvidenceAnswerUse | null;
  chunkId: string;
  /** 押すと原文の位置を開く。無ければ抜粋は文字だけ。 */
  onOpen?: () => void;
}

/** 根拠のまとまり（同じ文書の同じ節から AI に渡した単位）。 */
export interface EvidenceGroup {
  key: string;
  fileName: string;
  /** まとまりの章節。空なら出さない。 */
  section: string;
  /** まとまりのページの範囲の表示（例:「p.3-5」）。空なら出さない。 */
  pages: string;
  entries: readonly EvidenceGroupEntry[];
}

export interface EvidenceViewsProps extends Omit<EvidenceListProps, "testId"> {
  /** まとまり（分かるときだけ）。渡すと「順に表示 / まとまりごと」のタブを出す。 */
  groups?: readonly EvidenceGroup[] | null;
  /** 同じ画面に複数の一覧（チャットの回答ごと）があるので、タブの id を分ける。 */
  idPrefix: string;
  listTestId?: string;
}

/**
 * 根拠の一覧の見方（#2097・#2328）。「順に表示」はカード、「まとまりごと」は同じ節ごとにまとめて AI に渡した
 * 単位で、各箇所の役割・位置・抜粋を出す。2 つの見方は同じ箇所を同じ番号で数える。どちらも一定の高さを超えたら
 * 一覧の中で縦にスクロールする（チャット・実行の詳細で同じ。回答の評価を押し下げない）。まとまりが無ければ一覧だけ。
 */
export function EvidenceViews({
  items,
  groups = null,
  idPrefix,
  labels = DEFAULT_EVIDENCE_LABELS,
  renderActions,
  renderExtra,
  listTestId,
}: EvidenceViewsProps) {
  const [view, setView] = useState<"list" | "groups">("list");
  const list = (
    <EvidenceList
      items={items}
      labels={labels}
      renderActions={renderActions}
      renderExtra={renderExtra}
      testId={listTestId}
    />
  );
  if (!groups?.length) return list;
  const prefix = `${idPrefix}-evidence`;
  return (
    <div className="space-y-3">
      <Tabs
        idPrefix={prefix}
        ariaLabel={labels.viewLabel}
        value={view}
        onChange={(value) => setView(value === "groups" ? "groups" : "list")}
        items={[
          { id: "list", label: labels.viewList, count: items.length },
          { id: "groups", label: labels.viewGroups, count: groups.length },
        ]}
      />
      <TabPanel id="list" value={view} idPrefix={prefix}>
        {list}
      </TabPanel>
      <TabPanel id="groups" value={view} idPrefix={prefix}>
        <EvidenceGroupList groups={groups} labels={labels} />
      </TabPanel>
    </div>
  );
}

function EvidenceGroupList({ groups, labels }: { groups: readonly EvidenceGroup[]; labels: EvidenceLabels }) {
  return (
    <div className="space-y-2">
      <p className="text-xs leading-relaxed text-fg-muted">{labels.groupsHint}</p>
      <ol className={EVIDENCE_LIST_CLASS} data-testid="evidence-groups">
        {groups.map((group) => (
          <li
            key={group.key}
            className="rounded-lg border border-border bg-surface"
            data-testid="evidence-group"
            aria-label={labels.groupAria(group.fileName, group.entries.length)}
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-2">
              <FileText size={14} className="shrink-0 text-fg-muted" aria-hidden />
              <span className="min-w-0 break-words text-sm font-medium text-fg">{group.fileName}</span>
              {group.section ? (
                <span className="min-w-0 break-words text-xs text-fg-muted" data-testid="evidence-group-section">
                  {group.section}
                </span>
              ) : null}
              {group.pages ? <span className="tnum text-xs text-fg-muted">{group.pages}</span> : null}
              <span className="tnum ml-auto text-xs text-fg-muted" data-testid="evidence-group-count">
                {labels.groupCount(group.entries.length)}
              </span>
            </div>
            <ul className="divide-y divide-border">
              {group.entries.map((entry) => (
                <EvidenceGroupRow key={entry.key} entry={entry} labels={labels} />
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  );
}

function EvidenceGroupRow({ entry, labels }: { entry: EvidenceGroupEntry; labels: EvidenceLabels }) {
  const RoleIcon = entry.roleIcon;
  return (
    <li className="space-y-1 px-3 py-2" data-testid="evidence-item" data-role-kind={entry.roleKind}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span
          data-surface-tint="accent"
          className="tnum flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-xs font-semibold text-accent-fg"
        >
          {entry.index + 1}
        </span>
        <span className="inline-flex items-center gap-1 text-xs font-medium text-fg" data-testid="evidence-role">
          <RoleIcon size={14} className="shrink-0 text-fg-muted" aria-hidden />
          {entry.roleLabel}
        </span>
        {entry.location ? <span className="min-w-0 break-words text-xs text-fg-muted">{entry.location}</span> : null}
        {entry.usedInAnswer ? <EvidenceAnswerUseBadge use={entry.answerUse} labels={labels} /> : null}
      </div>
      {entry.onOpen ? (
        // 抜粋を押すと、カードの「引用箇所を表示」と同じ場所で原文の位置を開く。
        <button
          type="button"
          className="flex w-full items-start gap-1.5 rounded-md px-1 py-0.5 text-left text-sm leading-relaxed text-fg/90 hover:bg-surface-hover"
          onClick={entry.onOpen}
          data-testid="evidence-item-open"
        >
          <span className="line-clamp-3 min-w-0 flex-1 break-words">{entry.excerpt}</span>
          <LocateFixed size={14} className="mt-1 shrink-0 text-accent-fg" aria-hidden />
          <span className="sr-only">{labels.itemOpen(entry.index + 1)}</span>
        </button>
      ) : (
        <p className="line-clamp-3 break-words px-1 text-sm leading-relaxed text-fg/90">{entry.excerpt}</p>
      )}
      <Disclosure variant="plain" size="sm" summary={labels.itemDetails}>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-fg-muted">{labels.itemChunkId}</span>
          <code className="min-w-0 break-all font-mono text-fg" data-testid="evidence-item-chunk-id">
            {entry.chunkId}
          </code>
          <CopyButton
            text={entry.chunkId}
            variant="ghost"
            size="sm"
            label={labels.copyId}
            copiedMessage={labels.copiedId}
            failedMessage={labels.copyFailed}
          />
        </div>
      </Disclosure>
    </li>
  );
}

export interface AnswerEvidenceProps extends Omit<EvidenceViewsProps, "idPrefix"> {
  /** タブの id を分ける接頭辞（同じ画面に複数の回答があるときは回答ごとに分ける）。 */
  idPrefix?: string;
  /** 見出しのまとまりの数。省略すると `groups` の数。 */
  groupCount?: number | null;
  /** 開閉の受控（根拠の行から根拠を開くとき）。 */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  testId?: string;
}

/**
 * 回答の根拠の折りたたみ（既定は閉じる）。見出しは「AI に渡した N 件 ／ 回答で引用 M 件 …」で、回答の本文の下・
 * 回答の評価の前に置く（3 製品で同じ位置）。中身は {@link EvidenceViews}。根拠が無ければ何も出さない。
 */
export function AnswerEvidence({
  items,
  groups = null,
  idPrefix = "answer",
  labels = DEFAULT_EVIDENCE_LABELS,
  groupCount,
  renderActions,
  renderExtra,
  listTestId,
  open,
  onOpenChange,
  className,
  testId = "answer-citations",
}: AnswerEvidenceProps) {
  if (!items.length) return null;
  const count = groupCount === undefined ? (groups?.length ?? null) : groupCount;
  return (
    <Disclosure
      variant="plain"
      summary={evidenceCountLabel(items, count, labels)}
      className={cn(className)}
      open={open}
      onOpenChange={onOpenChange}
      data-testid={testId}
    >
      <EvidenceViews
        items={items}
        groups={groups}
        idPrefix={idPrefix}
        labels={labels}
        renderActions={renderActions}
        renderExtra={renderExtra}
        listTestId={listTestId}
      />
    </Disclosure>
  );
}
