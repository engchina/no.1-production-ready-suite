# 参照実装（新規・変更コンポーネント）

**そのまま出荷しないでください。** これは Claude Design で作成した参照実装（React + インラインスタイル）です。
`packages/ui` の既存の作法（`.tsx` + Tailwind v4）に合わせて**書き直してください**。
写すべきは「構造・寸法・トークン名・状態の扱い・ARIA」であって、インラインスタイルの書き方ではありません。

対応する仕様は `README.md` の §4（柱 C）を参照。

> **アイコンの読み替え（packages/ui の決定）:** 下の JSX は Lucide 名の文字列と `<Icon name="…" />` で書かれていますが、`packages/ui` ではアイコン props を **`LucideIcon`（`lucide-react` のコンポーネントそのもの）** で受けます。`icon="Upload"` → `icon={Upload}`、`<Icon name="Upload" size={16} />` → `<Upload size={16} aria-hidden />` と読み替えてください。文字列から引く方式は全アイコンをバンドルに含めるため採用しません（RAG 実測で JS +38%）。サイズは 14 / 16 / 20 / 24 の4値のみで、Sidebar の `size={18}` は `20` と読み替えます。

---

## Tabs.jsx — **新規**

```jsx
import React from "react";
import { Icon } from "../core/Icon.jsx";

/**
 * ビュー切替。**同じ対象の別の見方**に切り替えるときだけ使います。
 * データを絞り込むだけなら ToggleChip、別の画面に移るなら Sidebar です。
 *
 * 下線スタイル固定（管理コンソールの標準）。PageHeader の `tabs` に渡すと
 * ヘッダー下端に吸い付き、単体でもカード内で使えます。
 *
 * items: [{ id, label, icon?, count?, disabled? }]
 * キーボード: ← → で移動、Home / End で端へ（WAI-ARIA Tabs パターン）。
 */
export function Tabs({ items = [], value, onChange, ariaLabel = "ビュー切替", style }) {
  const refs = React.useRef({});
  const enabled = items.filter((item) => !item.disabled);

  const move = (delta) => {
    if (enabled.length === 0) return;
    const current = enabled.findIndex((item) => item.id === value);
    const next = enabled[(current + delta + enabled.length) % enabled.length];
    if (!next) return;
    onChange && onChange(next.id);
    const node = refs.current[next.id];
    if (node) node.focus();
  };

  const jump = (index) => {
    const target = enabled[index];
    if (!target) return;
    onChange && onChange(target.id);
    const node = refs.current[target.id];
    if (node) node.focus();
  };

  const onKeyDown = (event) => {
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
    else if (event.key === "Home") { event.preventDefault(); jump(0); }
    else if (event.key === "End") { event.preventDefault(); jump(enabled.length - 1); }
  };

  return (
    <div role="tablist" aria-label={ariaLabel} className="pr-tablist" onKeyDown={onKeyDown} style={style}>
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            ref={(node) => { refs.current[item.id] = node; }}
            type="button"
            role="tab"
            id={`pr-tab-${item.id}`}
            className="pr-tab"
            aria-selected={selected}
            aria-controls={`pr-tabpanel-${item.id}`}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onChange && onChange(item.id)}
          >
            {item.icon ? <Icon name={item.icon} size={16} /> : null}
            <span>{item.label}</span>
            {item.count == null ? null : <span className="pr-tab-count">{item.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** 対応する中身。`id` は Tabs の item.id と一致させます。 */
export function TabPanel({ id, value, children, style }) {
  if (id !== value) return null;
  return (
    <div role="tabpanel" id={`pr-tabpanel-${id}`} aria-labelledby={`pr-tab-${id}`} tabIndex={0} style={{ minWidth: 0, ...style }}>
      {children}
    </div>
  );
}
```

### Tabs の props

```ts
import * as React from "react";
import type { LucideIcon } from "lucide-react";

export interface TabsProps {
  items: { id: string; label: string; icon?: LucideIcon; count?: number; disabled?: boolean }[];
  value: string;
  onChange?: (id: string) => void;
  ariaLabel?: string;
  style?: React.CSSProperties;
}

/** @dsComponent */
export declare function Tabs(props: TabsProps): JSX.Element;

export interface TabPanelProps {
  id: string; value: string; children?: React.ReactNode; style?: React.CSSProperties;
}

/** @dsComponent */
export declare function TabPanel(props: TabPanelProps): JSX.Element;
```

---

## PageBody.jsx — **新規**

```jsx
import React from "react";

/**
 * PageHeader の直後に置く本文コンテナ。readme が規定していた
 * 「2rem の左右ガター / セクション間 1.5rem」の唯一の実装です。
 * これが無かったため 3 アプリがそれぞれ手書きしていました。
 *
 * `--content-max-width` (1440px) で計測幅を止めます。<main> が無制限に伸びると
 * 10 列の表が 2400px に広がり、目が行を追えません（視線移動限界は約 1000〜1200px）。
 * 表を画面幅いっぱいに出したい画面だけ `wide` を使います。
 */
export function PageBody({ wide = false, children, style }) {
  return (
    <div
      style={{
        display: "grid",
        gap: "var(--gap-stack)",
        alignContent: "start",
        width: "100%",
        maxWidth: wide ? "none" : "var(--content-max-width)",
        marginInline: wide ? 0 : "auto",
        boxSizing: "border-box",
        padding: "var(--page-gutter-y) var(--page-gutter-x)",
        minWidth: 0,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/**
 * 見出し付きのひとまとまり。カードを複数含む領域や、カードに入れるほどでもない
 * 領域に使います。見出しは --text-section-title（16px / 600）で、
 * ページタイトル 20px とカード見出し 14px の間の段を埋めます。
 */
export function Section({ title, description, actions, children, style }) {
  return (
    <section style={{ display: "grid", gap: "var(--space-3)", minWidth: 0, ...style }}>
      {title || actions ? (
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-4)", minWidth: 0 }}>
          <div style={{ minWidth: 0 }}>
            {title ? <h2 style={{ margin: 0, font: "var(--text-section-title)", color: "var(--color-fg)" }}>{title}</h2> : null}
            {description ? <p style={{ margin: "var(--space-1) 0 0", font: "var(--text-body)", color: "var(--color-fg-muted)" }}>{description}</p> : null}
          </div>
          {actions ? <div style={{ display: "flex", flexShrink: 0, alignItems: "center", gap: "var(--gap-action)" }}>{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
```

### PageBody の props

```ts
import * as React from "react";

export interface PageBodyProps {
  /** 表を画面幅いっぱいに出す画面だけ true。既定は --content-max-width で止める。 */
  wide?: boolean;
  children?: React.ReactNode;
  style?: React.CSSProperties;
}

/** @dsComponent */
export declare function PageBody(props: PageBodyProps): JSX.Element;

export interface SectionProps {
  title?: string; description?: string; actions?: React.ReactNode;
  children?: React.ReactNode; style?: React.CSSProperties;
}

/** @dsComponent */
export declare function Section(props: SectionProps): JSX.Element;
```

---

## PageHeader.jsx — 変更

```jsx
import React from "react";
import { Button } from "../core/Button.jsx";
import { Icon } from "../core/Icon.jsx";

/* アクションの並び順。右寄せグループなので、右端（＝最も押しやすい位置）に primary が来ます。
   旧構成は primary が左端で danger が右端＝最も破壊的な操作が最も押しやすい位置でした。
   danger は本来オーバーフローメニューに入れるべきものです（DropdownMenu 実装後に移行）。 */
const ORDER = { danger: 0, utility: 1, secondary: 2, primary: 3 };
const VARIANT = { primary: "primary", secondary: "secondary", utility: "secondary", danger: "danger" };

/** Location trail for 3+ level flows. The last item is the current page. */
export function Breadcrumbs({ items = [], onNavigate }) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="パンくず" style={{ display: "flex", alignItems: "center", font: "var(--text-meta)", color: "var(--color-fg-muted)" }}>
      <ol style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-1)", margin: 0, padding: 0, listStyle: "none" }}>
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <React.Fragment key={`${item.label}-${index}`}>
              <li>
                {item.href && !last ? (
                  <a href={item.href} onClick={onNavigate ? (event) => { event.preventDefault(); onNavigate(item.href); } : undefined} style={{ color: "inherit", textDecoration: "none" }}>{item.label}</a>
                ) : (
                  <span aria-current={last ? "page" : undefined} style={last ? { fontWeight: 500, color: "var(--color-fg)" } : undefined}>{item.label}</span>
                )}
              </li>
              {last ? null : <li aria-hidden="true" style={{ display: "flex", color: "var(--color-fg-subtle)" }}><Icon name="ChevronRight" size={14} /></li>}
            </React.Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * Every screen opens with this header (px-8 py-5 on --color-surface over a hairline).
 * 長い表でもタイトルと主要操作に手が届くよう `position: sticky` で上端に貼り付きます。
 * `tabs` に <Tabs> を渡すとヘッダー下端に吸い付きます（ビュー切替の唯一の置き場所）。
 * actions: [{ id, kind: "primary"|"secondary"|"utility"|"danger", label, icon?, onClick?, loading?, disabled? }]
 * 並びは danger → utility → secondary → primary（右端が primary）。
 */
export function PageHeader({ title, subtitle, status, breadcrumbs, actions = [], tabs, wide = false, onNavigate }) {
  const ordered = actions.map((action, index) => ({ action, index })).sort((a, b) => ORDER[a.action.kind] - ORDER[b.action.kind] || a.index - b.index).map(({ action }) => action);
  /* ※ <header> は full-bleed（背景と罫線を画面幅いっぴいに伸ばす）、
     中身は PageBody と同じ計測コンテナに入れる。
     これがないと 1920px モニタでタイトルとカードの左端が 240px ずれます。
     wide は PageBody の wide と必ず揃えること。 */
  const measure = {
    width: "100%",
    maxWidth: wide ? "none" : "var(--content-max-width)",
    marginInline: wide ? 0 : "auto",
    boxSizing: "border-box",
    paddingInline: "var(--page-gutter-x)",
    minWidth: 0,
  };
  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: "var(--z-sticky)",
        display: "flex",
        flexDirection: "column",
        gap: tabs ? "var(--space-4)" : 0,
        padding: tabs ? "var(--page-header-py) 0 0" : "var(--page-header-py) 0",
        borderBottom: "1px solid var(--color-border)",
        background: "var(--color-surface)",
      }}
    >
      <div style={{ ...measure, display: "flex", flexWrap: "wrap", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-4)" }}>
        <div style={{ minWidth: 0, flex: "1 1 auto" }}>
          {breadcrumbs && breadcrumbs.length ? <div style={{ marginBottom: "var(--space-1-5)" }}><Breadcrumbs items={breadcrumbs} onNavigate={onNavigate} /></div> : null}
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--space-2-5)" }}>
            <h1 style={{ margin: 0, font: "var(--text-page-title)", color: "var(--color-fg)" }}>{title}</h1>
            {status || null}
          </div>
          {subtitle ? <p style={{ margin: "var(--space-1) 0 0", font: "var(--text-body)", color: "var(--color-fg-muted)" }}>{subtitle}</p> : null}
        </div>
        {ordered.length ? (
          <div role="group" aria-label="ページ操作" style={{ display: "flex", flexShrink: 0, alignItems: "center", gap: "var(--gap-action)" }}>
            {ordered.map((action) => (
              <Button
                key={action.id}
                variant={VARIANT[action.kind]}
                icon={action.icon}
                loading={action.loading}
                disabled={action.disabled}
                onClick={action.onClick}
                iconOnly={!action.label}
                aria-label={action.label ? undefined : action.ariaLabel}
              >
                {action.label ? <span>{action.label}</span> : null}
              </Button>
            ))}
          </div>
        ) : null}
      </div>
      {tabs ? <div style={measure}>{tabs}</div> : null}
    </header>
  );
}
```

### PageHeader の props

```ts
import * as React from "react";
import type { LucideIcon } from "lucide-react";

export interface PageHeaderProps {
  title: string; subtitle?: string; status?: React.ReactNode;
  breadcrumbs?: { label: string; href?: string }[];
  /** 並びは danger → utility → secondary → primary（右端が primary）。 */
  actions?: { id: string; kind: "primary" | "secondary" | "utility" | "danger"; label?: string; ariaLabel?: string; icon?: LucideIcon; onClick?: () => void; loading?: boolean; disabled?: boolean }[];
  /** <Tabs> を渡すとヘッダー下端に吸い付く。ビュー切替の唯一の置き場所。 */
  tabs?: React.ReactNode;
  /** PageBody の wide と必ず同じ値にする。ずらすと本文と左端が揃わない。 */
  wide?: boolean;
  onNavigate?: (href: string) => void;
}

/** @dsComponent */
export declare function PageHeader(props: PageHeaderProps): JSX.Element;

export interface BreadcrumbsProps {
  items: { label: string; href?: string }[]; onNavigate?: (href: string) => void;
}

/** @dsComponent */
export declare function Breadcrumbs(props: BreadcrumbsProps): JSX.Element;
```

---

## Button.jsx — 変更

```jsx
import React from "react";
import { Spinner } from "./Spinner.jsx";
import { Icon } from "./Icon.jsx";

/**
 * RAG / NL2SQL / Agent 共通の唯一のボタン。4 バリアント × 3 サイズ（32 / 36 / 40px）。
 *
 * ── アイコンの規約 ────────────────────────────────────────────────────────
 * `icon` に Lucide 名を渡します（子要素に <Icon> を直接書かない）。
 * 先頭スロットは常に 16px。`trailingIcon` は方向・開閉・外部リンクのみ
 * （chevron / ExternalLink）。アイコンを 2 つ持たせないこと。
 *
 * ── ローディングの規約 ──────────────────────────────────────────────────
 * `loading` 中は **先頭アイコンがスピナーに置き換わります**。
 *   - ラベルは変えません（「実行中…」に差し替えない）
 *   - したがって幅が変わらず、レイアウトが跳ねません
 *   - `aria-busy="true"` と `disabled` が自動で付きます
 * ★ 非同期の操作を起こすボタンは必ず `icon` を持たせてください。
 *   アイコンが無いとスピナーの分だけ幅が広がります。
 * 1 秒を超えて領域全体が待ちになる処理は、ボタンではなく領域側で
 * LoadingState を出します。
 */
export function Button({
  variant = "primary",
  size = "md",
  icon,
  trailingIcon,
  iconOnly = false,
  touchTarget = false,
  tone = "default",
  loading = false,
  disabled = false,
  pressed,
  type = "button",
  className = "",
  children,
  ...rest
}) {
  const classes = [
    "pr-button",
    `pr-button--${variant}`,
    size !== "md" && `pr-button--${size}`,
    iconOnly && "pr-button--icon",
    touchTarget && "pr-button--touch",
    tone === "danger" && "pr-button--danger-tone",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  // 先頭スロット: loading 中はスピナーがアイコンを置き換える（幅不変）。
  // icon が無いまま loading にするとスピナーの分だけ幅が広がる（規約違反）。
  const leading = loading ? <Spinner size={16} /> : icon ? <Icon name={icon} size={16} /> : null;

  return (
    <button
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      aria-pressed={pressed}
      {...rest}
    >
      {leading}
      {children}
      {trailingIcon && !loading ? <Icon name={trailingIcon} size={16} /> : null}
    </button>
  );
}
```

### Button の props

```ts
import * as React from "react";
import type { LucideIcon } from "lucide-react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  /** 先頭アイコン（lucide-react のコンポーネント。例: icon={Upload}）。子要素にアイコンを直接書かない。 */
  icon?: LucideIcon;
  /** 方向・開閉・外部リンクのみ（ChevronRight / ChevronDown / ExternalLink）。 */
  trailingIcon?: LucideIcon;
  iconOnly?: boolean;
  touchTarget?: boolean;
  tone?: "default" | "danger";
  /** true で先頭アイコンがスピナーに置き換わる。ラベルは変えない。 */
  loading?: boolean;
  pressed?: boolean;
}

/** @dsComponent */
export declare function Button(props: ButtonProps): JSX.Element;
```

---

## StatusBadge.jsx — 変更

```jsx
import React from "react";
import { Icon } from "./Icon.jsx";

const VARIANTS = {
  neutral: ["var(--color-border-control)", "var(--color-surface)", "var(--color-fg-muted)"],
  info:    ["var(--color-info-border)",    "var(--color-info-subtle)",    "var(--color-info-fg)"],
  success: ["var(--color-success-border)", "var(--color-success-subtle)", "var(--color-success-fg)"],
  warning: ["var(--color-warning-border)", "var(--color-warning-subtle)", "var(--color-warning-fg)"],
  danger:  ["var(--color-danger-border)",  "var(--color-danger-subtle)",  "var(--color-danger-fg)"],
};
/** @deprecated 旧 pending は warning と完全同値だった。warning を使うこと。 */
VARIANTS.pending = VARIANTS.warning;

/* 状態は色だけで表しません。success #047857 と danger #b91c1c は輝度がほぼ同じ
   （L=0.139 / 0.110）で、1型・2型色覚では見分けられないため、形（アイコン）で
   冗長に符号化します。強制カラーモードでも意味が残ります。 */
const VARIANT_ICON = {
  neutral: "Minus",
  info: "Info",
  success: "CircleCheck",
  warning: "TriangleAlert",
  danger: "CircleAlert",
};
VARIANT_ICON.pending = "History";

/**
 * State pill. Token + 1px border version (promoted from NL2SQL) so it follows the dark theme.
 * Apps map their domain enum (FileStatus, RunStatus, …) to a variant and pass a translated label.
 */
export function StatusBadge({ variant = "neutral", label, icon = true, style }) {
  const [border, bg, fg] = VARIANTS[variant] || VARIANTS.neutral;
  const glyph = icon === true ? VARIANT_ICON[variant] || VARIANT_ICON.neutral : icon || null;
  return (
    <span
      data-status-variant={variant}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        whiteSpace: "nowrap",
        borderRadius: "var(--radius-pill)",
        border: `1px solid ${border}`,
        background: bg,
        color: fg,
        gap: "var(--space-1)",
        padding: "var(--space-0-5) var(--space-2-5)",
        font: "var(--font-weight-medium) var(--font-size-xs) / var(--line-height-xs) var(--font-sans)",
        ...style,
      }}
    >
      {glyph ? <Icon name={glyph} size={14} aria-hidden="true" /> : null}
      {label}
    </span>
  );
}
```

### StatusBadge の props

```ts
import * as React from "react";
import type { LucideIcon } from "lucide-react";

export interface StatusBadgeProps {
  variant: "neutral" | "info" | "success" | "warning" | "danger" | /** @deprecated warning と同値 */ "pending";
  label: string;
  /** 既定 true（バリアント既定のアイコン）。LucideIcon で上書き、false で非表示。 */
  icon?: boolean | LucideIcon;
  style?: React.CSSProperties;
}

/** @dsComponent */
export declare function StatusBadge(props: StatusBadgeProps): JSX.Element;
```

---

## Pagination.jsx — 変更

```jsx
import React from "react";
import { Button } from "../core/Button.jsx";

/** Paging under a DataTable. Hidden when there is a single page. Default page size 10. */
export function Pagination({ page = 1, totalPages = 1, onPageChange, summary, pageIndicator, prevLabel = "前へ", nextLabel = "次へ" }) {
  if (totalPages <= 1) return null;
  return (
    <nav aria-label={pageIndicator || summary} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "var(--space-2)", font: "var(--text-meta)", color: "var(--color-fg-muted)" }}>
      <span className="tnum">{summary}</span>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "var(--gap-action)" }}>
        <Button variant="secondary" size="sm" icon="ChevronLeft" disabled={page <= 1} onClick={() => onPageChange && onPageChange(page - 1)}>
          <span>{prevLabel}</span>
        </Button>
        {pageIndicator ? (
          <span className="tnum" style={{ display: "inline-flex", alignItems: "center", minHeight: 32, padding: "0 var(--space-3)", borderRadius: "var(--button-radius)", border: "1px solid var(--color-border-control)", color: "var(--color-fg)" }}>{pageIndicator}</span>
        ) : null}
        <Button variant="secondary" size="sm" trailingIcon="ChevronRight" disabled={page >= totalPages} onClick={() => onPageChange && onPageChange(page + 1)}>
          <span>{nextLabel}</span>
        </Button>
      </div>
    </nav>
  );
}
```

---

## DataTable.jsx — 変更

```jsx
import React from "react";
import { Icon } from "../core/Icon.jsx";

/**
 * Every list and result set. text-xs body, bg-background header, px-3 py-2 cells
 * (dense: py-1.5), divide-border/70 rows. Replaces the hand-written <table>s in all three apps.
 * columns: [{ key, header, align?: "left"|"right", mono?, sortable?, render?(row) }]
 */
export function DataTable({ columns = [], rows = [], rowKey = "id", dense = false, sort, onSortChange, onRowClick, emptyText = "データがありません", loading = false, ariaLabel }) {
  const padY = dense ? "var(--space-1-5)" : "var(--space-2)";
  const cell = (column) => ({ padding: `${padY} var(--space-3)`, textAlign: column.align === "right" ? "right" : "left" });
  return (
    <div style={{ overflowX: "auto", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", background: "var(--color-surface)" }}>
      <table aria-label={ariaLabel} style={{ width: "100%", borderCollapse: "collapse", font: "var(--text-meta)", color: "var(--color-fg)" }}>
        <thead style={{ background: "var(--color-surface-sunken)", color: "var(--color-fg-muted)" }}>
          <tr>
            {columns.map((column) => {
              const active = sort && sort.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined}
                  style={{ ...cell(column), fontWeight: 600, whiteSpace: "nowrap" }}
                >
                  {column.sortable ? (
                    <button
                      type="button"
                      className="pr-sort-header"
                      data-align={column.align === "right" ? "right" : undefined}
                      onClick={() => onSortChange && onSortChange({ key: column.key, direction: active && sort.direction === "asc" ? "desc" : "asc" })}
                      style={{ color: active ? "var(--color-fg)" : "var(--color-fg-muted)" }}
                    >
                      {column.header}
                      <Icon name={active ? (sort.direction === "asc" ? "ArrowUp" : "ArrowDown") : "ArrowUpDown"} size={14} />
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {loading
            ? [0, 1, 2].map((index) => (
                <tr key={index} style={{ borderTop: "1px solid color-mix(in srgb, var(--color-border) 70%, transparent)" }}>
                  {columns.map((column) => (
                    <td key={column.key} style={cell(column)}>
                      <span className="pr-pulse" style={{ display: "block", height: "1rem", borderRadius: "var(--radius-sm)", background: "color-mix(in srgb, var(--color-fg-muted) 40%, transparent)" }} />
                    </td>
                  ))}
                </tr>
              ))
            : rows.map((row, index) => (
                <tr
                  key={row[rowKey] ?? index}
                  className={onRowClick ? "pr-row-hover" : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  style={{ borderTop: "1px solid color-mix(in srgb, var(--color-border) 70%, transparent)", cursor: onRowClick ? "pointer" : undefined }}
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={column.align === "right" ? "tnum" : undefined}
                      style={{ ...cell(column), overflowWrap: "normal", fontFamily: column.mono ? "var(--font-mono)" : undefined }}
                    >
                      {column.render ? column.render(row) : row[column.key]}
                    </td>
                  ))}
                </tr>
              ))}
          {!loading && rows.length === 0 ? (
            <tr>
              <td colSpan={Math.max(columns.length, 1)} style={{ padding: "var(--space-6) var(--space-3)", textAlign: "center", color: "var(--color-fg-muted)" }}>{emptyText}</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
```

> **packages/ui の追加 props（一覧用、platform #56）。** 上の参照実装に無い、業務一覧の手書き `<table>` を置き換えるための props です。すべて optional で、渡さなければ出力は変わりません。
>
> | prop | 役割 |
> |---|---|
> | `stickyHeader` | `thead` をスクロール領域の上端に固定する。罫線は th の内側（inset shadow）に持たせ、スクロールしても消えない |
> | `visibleRows` / `fillVisibleRows` | `number` か `{ base, md }`（md = 48rem 以上）。表頭 + 先頭 N 行の**実測高さ**でスクロール領域の `max-height`（fill では `height`）を決める。2 行セルで行高が変わっても N 行ちょうどが見える |
> | `scrollAriaLabel` / `scrollTestId` | スクロール領域を `role="region"` + `tabIndex=0` + フォーカスリングにする（WCAG 2.1.1 キーボードでスクロール） |
> | `selectedRowKey` | master-detail で表示中の行。`aria-current="true"` + `bg-accent-subtle` + 先頭セルの左バー（0.25rem、`--color-accent-fg`。選択を色の差だけで示さない = WCAG 1.4.1）+ `data-surface-tint="accent"`（行内の `fg-muted` / `accent-fg` を淡青面用に深くする = 4.5:1）。全行に `data-selected` |
> | `isRowSelected` | チェックボックスの複数選択。背景・左バー・`data-surface-tint` だけ付け、状態はチェックボックスが伝える |
> | `onRowClick` | マウス操作の補助。行内の button / a / input / label 等のクリックでは発火しない。キーボード用に行内の button も置く |
> | `rowProps` | 行の `className` / `aria-label` / `data-testid` |
> | `renderRowDetail` | 行の直後に全幅の補足行（分析結果など）。`visibleRows` の計測では直前の行に含める |
> | `columns[].rowHeader` | `<th scope="row">` で描画する |
> | `tableClassName` / `loadingRows` | `table-fixed` や `min-w-*`、スケルトン行数 |
>
> 並べ替えヘッダーは折り返さず（`white-space: nowrap`）、当たり判定の高さは `--button-height-sm`（タッチ端末では 44px）です。

---

## Sidebar.jsx — 変更

```jsx
import React from "react";
import { Icon } from "../core/Icon.jsx";

const reveal = (collapsed) => ({ opacity: collapsed ? 0 : 1, transition: "opacity var(--duration-reveal) var(--ease-out)" });

/**
 * Collapsible dark navigation shared by all products. Only `product` (second line of the
 * wordmark), `sections` and the account in the footer differ between RAG / NL2SQL / Agent.
 * sections: [{ key, title, collapsed?, items: [{ href, label, sidebarLabel?, icon }] }]
 * account: { name, roles } — renders SidebarAccountFooter (PROPOSED shared footer).
 */
export function Sidebar({ product, sections = [], currentPath, collapsed = false, onToggleCollapsed, onToggleSection, onNavigate, onOpenCommandPalette, account, theme = "light", onToggleTheme, onLogout }) {
  const isActive = (href) => currentPath === href || (currentPath || "").startsWith(href + "/");
  return (
    <aside
      aria-label="サイドナビゲーション"
      data-surface="inverted"
      data-state={collapsed ? "collapsed" : "expanded"}
      style={{ display: "flex", flexDirection: "column", flexShrink: 0, height: "100%", width: collapsed ? "var(--sidebar-width-collapsed)" : "var(--sidebar-width)", overflow: "hidden", background: "var(--color-surface)", color: "var(--color-fg-muted)", fontFamily: "var(--font-sans)", transition: "width var(--duration-enter) var(--ease-out)" }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: collapsed ? "center" : "space-between", height: "var(--sidebar-header-height)", flexShrink: 0, padding: collapsed ? "0 var(--space-2)" : "0 var(--space-3)", borderBottom: "1px solid var(--color-border)" }}>
        {collapsed ? null : (
          <div title={`Production Ready ${product}`} style={{ minWidth: 0, flex: 1, padding: "0 var(--space-2)", color: "var(--color-fg)", ...reveal(collapsed) }}>
            <span style={{ display: "block", whiteSpace: "nowrap", fontSize: "var(--font-size-base)", lineHeight: "1.25rem", fontWeight: 700 }}>Production Ready</span>
            <span style={{ display: "block", whiteSpace: "nowrap", fontSize: "var(--font-size-xs)", lineHeight: "var(--line-height-xs)", fontWeight: 600, color: "var(--color-fg-muted)" }}>{product}</span>
          </div>
        )}
        <button type="button" className="pr-nav-row" onClick={onToggleCollapsed} aria-label={collapsed ? "サイドバーを展開" : "サイドバーを折りたたむ"} aria-expanded={!collapsed} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: "2.75rem", height: "2.75rem", flexShrink: 0, border: 0, borderRadius: "var(--radius-md)", background: "transparent", color: "var(--color-fg-muted)", cursor: "pointer" }}>
          <Icon name={collapsed ? "PanelLeftOpen" : "PanelLeftClose"} size={18} />
        </button>
      </div>

      <nav style={{ flex: 1, minHeight: 0, overflowX: "hidden", overflowY: "auto", padding: collapsed ? "var(--space-3) var(--space-2)" : "var(--space-3)" }}>
        {onOpenCommandPalette ? (
          <button type="button" className="pr-nav-row" onClick={onOpenCommandPalette} aria-label="コマンドパレットを開く" style={{ display: "flex", alignItems: "center", justifyContent: collapsed ? "center" : "flex-start", gap: "var(--space-2)", width: "100%", height: "var(--command-button-height)", marginBottom: "var(--space-3)", padding: collapsed ? 0 : "0 var(--space-3)", border: "1px solid var(--color-border)", borderRadius: "var(--radius-md)", background: "transparent", color: "var(--color-fg-muted)", font: "var(--text-body)", cursor: "pointer" }}>
            <Icon name="Search" size={16} />
            {collapsed ? null : (
              <>
                <span style={{ minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>コマンドパレットを開く</span>
                <kbd style={{ flexShrink: 0, borderRadius: "var(--radius-sm)", border: "1px solid var(--color-border-strong)", padding: "0.125rem 0.375rem", fontFamily: "var(--font-sans)", fontSize: 10, fontWeight: 500, color: "var(--color-fg-subtle)" }}>⌘K</kbd>
              </>
            )}
          </button>
        ) : null}

        {sections.map((section) => {
          const expanded = collapsed || !section.collapsed;
          const containsActive = section.items.some((item) => isActive(item.href));
          return (
            <div key={section.key} style={{ marginBottom: collapsed ? "var(--space-3)" : "var(--space-4)" }}>
              {collapsed ? null : (
                <button type="button" className="pr-nav-row" aria-expanded={expanded} onClick={() => onToggleSection && onToggleSection(section.key)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-2)", width: "100%", padding: "var(--space-1) var(--space-3)", border: 0, borderRadius: "var(--radius-md)", background: "transparent", color: "var(--color-fg-subtle)", font: "var(--font-weight-semibold) var(--font-size-xs) / var(--line-height-xs) var(--font-sans)", letterSpacing: "0.025em", cursor: "pointer" }}>
                  <span style={{ display: "flex", alignItems: "center", gap: "var(--space-1-5)", minWidth: 0 }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{section.title}</span>
                    {!expanded && containsActive ? <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "var(--radius-pill)", background: "var(--color-accent-emphasis)" }} /> : null}
                  </span>
                  <Icon name="ChevronDown" size={14} style={{ transform: expanded ? "none" : "rotate(-90deg)", transition: "transform var(--duration-enter) var(--ease-out)" }} />
                </button>
              )}
              {expanded ? (
                <ul style={{ margin: 0, padding: collapsed ? 0 : "var(--space-1) 0 0", listStyle: "none", display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
                  {section.items.map((item) => {
                    const active = isActive(item.href);
                    return (
                      <li key={item.href}>
                        <a
                          href={item.href}
                          className="pr-nav-row"
                          aria-current={active ? "page" : undefined}
                          aria-label={collapsed ? item.label : undefined}
                          title={item.label}
                          onClick={onNavigate ? (event) => { event.preventDefault(); onNavigate(item.href); } : undefined}
                          style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: collapsed ? "center" : "flex-start", gap: "var(--space-2-5)", height: "var(--nav-item-height)", padding: collapsed ? 0 : "0 var(--space-3)", overflow: "hidden", borderRadius: "var(--radius-md)", color: "inherit", textDecoration: "none", font: "var(--text-body)" }}
                        >
                          {active ? <span aria-hidden="true" style={{ position: "absolute", left: 0, top: "50%", width: "0.25rem", height: "1.25rem", transform: "translateY(-50%)", borderRadius: "0 9999px 9999px 0", background: "var(--color-fg)" }} /> : null}
                          <Icon name={item.icon} size={18} />
                          {collapsed ? null : <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.sidebarLabel || item.label}</span>}
                        </a>
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          );
        })}
      </nav>

      {account ? <SidebarAccountFooter account={account} collapsed={collapsed} theme={theme} onToggleTheme={onToggleTheme} onLogout={onLogout} /> : null}
    </aside>
  );
}

/** PROPOSED shared footer: user + roles, logout, theme toggle. Replaces three app-specific footers. */
export function SidebarAccountFooter({ account, collapsed = false, theme = "light", onToggleTheme, onLogout }) {
  const row = { display: "flex", alignItems: "center", gap: "var(--space-2-5)", height: "var(--nav-item-height)", border: 0, borderRadius: "var(--radius-md)", background: "transparent", color: "inherit", font: "var(--text-body)", cursor: "pointer" };
  return (
    <div style={{ borderTop: "1px solid var(--color-border)", padding: collapsed ? "var(--space-3) var(--space-2)" : "var(--space-3)" }}>
      {collapsed ? null : (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2-5)", minHeight: "var(--nav-item-height)", padding: "var(--space-2) var(--space-3)" }}>
          <Icon name="UserRound" size={18} />
          <div style={{ minWidth: 0 }}>
            <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", font: "var(--text-label)", color: "var(--color-fg)" }}>{account.name}</div>
            {account.roles ? <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", font: "var(--text-meta)", color: "var(--color-fg-subtle)" }}>{account.roles}</div> : null}
          </div>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: collapsed ? "column" : "row", gap: "var(--space-1)" }}>
        <button type="button" className="pr-nav-row" onClick={onLogout} aria-label="ログアウト" style={{ ...row, flex: 1, justifyContent: collapsed ? "center" : "flex-start", padding: collapsed ? 0 : "0 var(--space-3)" }}>
          <Icon name="LogOut" size={18} />
          {collapsed ? null : <span>ログアウト</span>}
        </button>
        <button type="button" className="pr-nav-row" onClick={onToggleTheme} aria-label={theme === "dark" ? "ライトテーマに切り替え" : "ダークテーマに切り替え"} style={{ ...row, justifyContent: "center", width: collapsed ? "100%" : "var(--nav-item-height)", padding: 0, border: "1px solid var(--color-border)" }}>
          <Icon name={theme === "dark" ? "Sun" : "Moon"} size={18} />
        </button>
      </div>
    </div>
  );
}
```

---

## AppShell.jsx — 変更

```jsx
import React from "react";

/** Full-height shell: Sidebar slot on the left, one scrolling main region. Required in every app. */
export function AppShell({ sidebar, children, style }) {
  return (
    <div style={{ display: "flex", width: "100%", height: "100vh", overflow: "hidden", position: "relative", background: "var(--color-canvas)", color: "var(--color-fg)", fontFamily: "var(--font-sans)", ...style }}>
      <a className="pr-skip-link" href="#pr-main">本文へスキップ</a>
      {sidebar}
      <main id="pr-main" tabIndex={-1} style={{ display: "flex", minWidth: 0, flex: 1, flexDirection: "column", overflowY: "auto" }}>{children}</main>
    </div>
  );
}
```

---
