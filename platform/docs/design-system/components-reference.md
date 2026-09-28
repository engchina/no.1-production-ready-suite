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
/** 操作のグループ。境界に区切り線を置く（buttons.md §5）。 */
const GROUP = { danger: "danger", utility: "utility", secondary: "work", primary: "work" };

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
 * グループ（danger / utility / secondary + primary）の境界に区切り線を置く（buttons.md §5）。
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
            {ordered.map((action, index) => (
              <React.Fragment key={action.id}>
              {index > 0 && GROUP[ordered[index - 1].kind] !== GROUP[action.kind] ? (
                <span aria-hidden className="pr-page-header__separator" />
              ) : null}
              <Button
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
              </React.Fragment>
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
 *   - `aria-busy="true"` と `aria-disabled="true"` が自動で付きます。ネイティブの `disabled` は付けません
 *     （フォーカス中のボタンに disabled を付けるとフォーカスが body へ外れるため。#355）。
 *     クリック・Enter / Space・form の submit（暗黙の送信を含む）は止め、見た目は disabled と同じにします
 *   - `disabled` prop はネイティブの `disabled` のまま（loading と重なったら disabled を優先）
 * ★ 非同期の操作を起こすボタンは必ず `icon` を持たせてください（adherence の lint が検出します）。
 *   アイコンが無いとスピナーの分だけ幅が広がります。
 * ★ `variant="danger"` と `tone="danger"` は同時に指定できません（型で禁止。赤地に赤文字になる）。
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
  onClick,
  ...rest
}) {
  // disabled が優先。loading だけのときはフォーカスを保つため aria-disabled にしてクリックを止める。
  const busy = loading && !disabled;
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
      aria-busy={loading || undefined}
      aria-pressed={pressed}
      {...rest}
      disabled={disabled || undefined}
      aria-disabled={busy || undefined}
      onClick={busy ? (event) => { event.preventDefault(); event.stopPropagation(); } : onClick}
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

/** variant="danger"（赤塗り）と tone="danger"（赤文字）の同時指定は禁止（赤地に赤文字になる）。 */
export type ButtonVariantToneProps =
  | { variant: "danger"; tone?: "default" }
  | { variant?: "primary" | "secondary" | "ghost"; tone?: "default" | "danger" };

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & ButtonVariantToneProps & {
  size?: "sm" | "md" | "lg";
  /** 先頭アイコン（lucide-react のコンポーネント。例: icon={Upload}）。子要素にアイコンを直接書かない。 */
  icon?: LucideIcon;
  /** 方向・開閉・外部リンクのみ（ChevronRight / ChevronDown / ExternalLink）。 */
  trailingIcon?: LucideIcon;
  /** アイコンだけのボタン。aria-label 必須。既定で aria-label と同じ文言の Tooltip を出す（#372）。 */
  iconOnly?: boolean;
  /** ホバーとキーボードのフォーカスで出す説明。iconOnly の既定は aria-label、false で出さない。出すときは title を無視する。 */
  tooltip?: string | false;
  tooltipPlacement?: "top" | "bottom";
  touchTarget?: boolean;
  /** true で先頭アイコンがスピナーに置き換わる。ラベルは変えない。aria-disabled でフォーカスを保つ。 */
  loading?: boolean;
  pressed?: boolean;
};

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

> **packages/ui の追加（#265）。** 上の参照実装の props はそのまま。`usePagination(items, pageSize?, options?)` の `options` に
> `page` / `onPageChange`（制御式。ページ番号を作業状態に保持する。再取得で items が変わってもページを戻さない）と
> `resetKey`（この値が変わったときだけ 1 ページ目へ戻す）を足した。省略時は従来どおり items が変わると 1 ページ目へ戻る。
> サーバー側のページング（offset / limit / total）は `offsetPagination({ offset, limit, total, count })` で
> `page` / `totalPages` / `range` に直し、ページの移動は `offsetForPage(page, limit)` で offset に戻す。既定は `DEFAULT_PAGE_SIZE`（10）。

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
export function Sidebar({ product, sections = [], currentPath, collapsed = false, onToggleCollapsed, onToggleSection, onNavigate, account, theme = "light", onToggleTheme, onLogout }) {
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

### md 未満のナビのドロワー（#367。案 A、決定待ち）

上の参照実装は md 以上の形です。`packages/ui` の `AppShell` は、md 未満（`NAV_DRAWER_QUERY` = `(max-width: 767px)`）で次の形に切り替えます。製品のコードは変えません（`sidebar` スロットの `Sidebar` をそのまま渡す）。

```tsx
<div data-nav-mode="drawer" className="flex h-screen flex-col">          {/* md 未満だけ縦並び */}
  <a className="pr-skip-link" href="#pr-main" inert={open}>本文へスキップ</a>
  <div data-surface="inverted" data-testid="nav-drawer-bar">              {/* 本文のスクロールの外。landmark にしない */}
    <Button variant="ghost" iconOnly touchTarget icon={Menu}
      aria-label="メニュー" aria-expanded={open} aria-controls={drawerId} />
    {/* 製品名（Sidebar の title を context で受け取る） */}
  </div>
  <main id="pr-main" tabIndex={-1} inert={open}>{children}</main>
  <div aria-hidden className="fixed inset-0 z-[var(--z-scrim)] bg-[var(--scrim)]" onClick={close} />
  <div id={drawerId} role="dialog" aria-modal="true" aria-label="メニュー" inert={!open}
    className="fixed inset-y-0 left-0 z-[var(--z-dialog)] …">                {/* Escape / Tab の閉じ込め / リンクのクリックで閉じる */}
    {sidebar}                                                             {/* 展開して描き、折りたたみボタンの代わりに「メニューを閉じる」 */}
  </div>
</div>
```

| 振る舞い | 内容 |
|---|---|
| 開く | 「メニュー」ボタン。ドロワーの閉じるボタンへフォーカス |
| 閉じる | 閉じるボタン・Escape・scrim のタップ・ナビのリンクの選択（今のページでも）・`currentPath` の変化。閉じたら「メニュー」ボタンへフォーカスを戻す（画面を移ったときは製品の画面遷移のフォーカスが本文へ移す）。離脱の確認をキャンセルしたときは開いたまま |
| フォーカス | Tab / Shift+Tab はドロワーの中で回る。背面は `inert`（本文へスキップ・上端のバー・`<main>`） |
| スクロール | 開いている間は `<main>` のスクロールを止め、scrim は `touch-action: none`、ドロワーのナビは `overscroll-behavior: contain` |
| モーション | transform 200ms ease-out。`prefers-reduced-motion` では動かさない |
| 状態 | ドロワーの開閉は保持しない（画面を開いた時は閉じている）。ui-store の `sidebarCollapsed`（md 以上の選好）は変えない |

| props / export | 型 | 説明 |
|---|---|---|
| `navDrawerLabels` | `{ menu: string; close: string }` | 既定 `DEFAULT_NAV_DRAWER_LABELS`（「メニュー」「メニューを閉じる」）。翻訳済みの文言で上書きする |
| `useSidebarCollapsed(collapsed)` | `(boolean) => boolean` | サイドバーの `footer` に置く部品が使う。ドロワーの中では `false`（展開）。`SidebarAccountFooter` と `SidebarAccountSection` は対応済み |
| `NAV_DRAWER_QUERY` | `string` | ドロワーにする幅のメディアクエリ |

Playwright では、`data-testid` の `nav-drawer-bar` / `nav-drawer-trigger` / `nav-drawer` / `nav-drawer-close` / `nav-drawer-scrim` か、role（`button` の「メニュー」、`dialog` の「メニュー」）で操作します。md 未満でサイドナビのリンクを押す spec は、先に「メニュー」を開きます（各製品の e2e の `openSidebarNav(page)`。RAG は `e2e/_helpers.ts`、NL2SQL は `tests/e2e/_helpers/sidebar-nav.ts`、Agent は `e2e/fixtures/nav.ts`）。

---

## SecretField — **新規**（#296）

API key・パスワード・token など、**保存済みの値を画面に出さない** secret の入力欄です。システム設定のモデル設定（API key）・データベース設定（DB パスワード / Wallet パスワード）・RAG の HuggingFace 設定（token）が別々に持っていた実装を `packages/ui` にまとめました。製品で secret の入力欄を再実装しないでください。

| 決めたこと | 理由 |
|---|---|
| ラベルの行の右端に「保存済み / 未設定」を `StatusBadge`（`success` / `neutral`、アイコン付き）で出す | 保存済みかどうかは値を見せずに伝える必要がある。色だけに頼らない（`StatusBadge` の規約） |
| 入力欄は `TextField` と同じ高さ（`--field-height`）・枠線・フォーカス表示。既定はマスク（`type="password"`）、`autoComplete="off"`・`spellCheck={false}` | 2 列の grid で隣の `TextField` と上端・高さをそろえる。secret をブラウザの補完・スペルチェックへ渡さない |
| 表示の切り替えは入力欄の直後の共有 `Button`（`ghost` + `iconOnly`、`Eye` / `EyeOff`）。`aria-label` は「〜を表示 / 〜を隠す」 | アイコンだけのボタンには `aria-label` が要る。`#id + button` で引ける構造を 3 製品の E2E が使う |
| 保存済みの値をサーバーから取り出す間（`revealPending`）は、切り替えが `loading`（共有 `Spinner`）になり押せない | ボタンの loading の規約と同じ。取得中の `aria-label`（例:「DB パスワードを取得中」）は `revealPendingLabel` で渡す |
| 保存済みの値の削除は `clearOption` のチェックボックスで、**入力欄の直下**に出す（保存済みのときだけ）。指定中は入力欄と切り替えを無効にする | 削除の指定と対象の入力欄を近くに置く。削除と新しい値の入力を同時に受け付けない |
| 削除の指定・取り出し・保存は呼び出し側が持つ | `packages/ui` は API と業務語彙を知らない。文言はすべて翻訳済みの文字列で渡す |

```tsx
<SecretField
  id="enterprise-api-key"
  label="API key"
  value={draft.api_key}
  onValueChange={(value) => update("api_key", value)}
  visible={apiKeyVisible}
  onVisibleChange={setApiKeyVisible}
  hasSavedSecret={draft.has_api_key}
  savedLabel="保存済み"
  notSetLabel="未設定"
  showLabel="API key を表示"
  hideLabel="API key を隠す"
  helper="OpenAI-compatible gateway の Bearer 認証で使います。"
  clearOption={{ label: "保存済み API key を削除する", checked: draft.clear_api_key, onCheckedChange: updateClear }}
/>
```

### SecretField の props

```ts
import type { ReactNode, Ref } from "react";

export interface SecretFieldClearOption {
  label: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

export interface SecretFieldProps {
  id: string;
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  hasSavedSecret: boolean;
  savedLabel: string;
  notSetLabel: string;
  showLabel: string;
  hideLabel: string;
  /** 省略すると内部の状態で切り替える。 */
  visible?: boolean;
  onVisibleChange?: (visible: boolean) => void;
  revealPending?: boolean;
  revealPendingLabel?: string;
  revealError?: string | null;
  helper?: ReactNode;
  error?: string;
  placeholder?: string;
  /** aria-required と RequiredBadge。required のときは requiredLabel を必ず渡す。 */
  required?: boolean;
  requiredLabel?: string;
  disabled?: boolean;
  /** hasSavedSecret のときだけ表示する。 */
  clearOption?: SecretFieldClearOption;
  autoComplete?: string;
  className?: string;
  ref?: Ref<HTMLInputElement>;
}

/** @dsComponent */
export declare function SecretField(props: SecretFieldProps): JSX.Element;
```

- 補足・エラー・取り出しの失敗の id は `${id}-hint` / `${id}-error` / `${id}-reveal-error`、削除のチェックボックスは `${id}-clear` です。
- 保存の操作行は `FormActionBar`（UX 契約 buttons §5.2.1）に置きます。`<form>` の中の保存は、`FormActionDescriptor` の `type: "submit"` で form の送信（Enter による暗黙の送信と `onSubmit` の検証）をそのまま使えます（既定は `type="button"`）。

---

## Toaster / toast / Banner — 変更（#351）

一時通知（`<Toaster/>` + `toast`）の表示時間と一時停止、Banner の閉じるボタンを UX 契約 messaging §3.1 にそろえました。製品で表示時間を補ったり、`toast.error` のラッパーを作ったりしないでください。

| 決めたこと | 理由 |
|---|---|
| 既定の表示時間は success / info / warning が 4 秒、danger（`toast.error`）が 0（利用者が閉じるまで残す）。`duration` を渡せば上書きできる | messaging §3.1。エラーは次の行動を決めるまで読めるようにする（以前は danger 8 秒・warning 6 秒で消えていた） |
| `action` 付きの通知は自動では消えない（従来どおり） | 「元に戻す」を押す前に消さない |
| いずれかの通知にポインタが乗っている間、または通知の中にフォーカスがある間は、**すべての**通知の自動消滅を止め、離れたら残り時間から再開する | 読んでいる・押そうとしている途中で消さない（WCAG 2.2.1 の考え方、Radix / Sonner の通例）。通知ごとに止めると、下の通知が消えて読んでいる通知の位置がずれる |
| 残り時間は store（`useToastStore`）が持つ。`pause()` / `resume()` と `paused` を公開する | 表示の部品を作り直しても残り時間が失われない。単体テストでタイマーを検証できる |
| ホバー・フォーカス中の通知が閉じられて DOM から外れたときは、その通知の一時停止を解除する | 外れた要素からは `pointerleave` / `blur` が届かず、止まったままになるのを防ぐ |
| Banner の閉じる × は Toast と同じ共有 `Button`（`ghost`・`iconOnly`・`touchTarget`・`X` 16px）。色はトーンの文字色（`text-current`）、ホバーは `fg` の薄い重ね | 手書きのボタンをやめ、フォーカスリング・当たり判定（44px）・強制カラーの輪郭を共有部品にそろえる |

```tsx
// 製品のアプリの最上位で一度だけ
<Toaster dismissLabel={t("common.close")} regionLabel={t("common.notifications")} />

toast.success(t("profiles.message.saved"));                       // 4 秒
toast.error(error instanceof ApiError ? error.message : t("…"));  // 閉じるまで残る
toast.success(t("…deleted"), { action: { label: t("common.undo"), onClick: undo } }); // 自動では消えない
```

### toast の API

```ts
export interface ToastOptions {
  description?: string;
  action?: { label: string; onClick: () => void };
  /** 自動消滅までの ms。0 で自動消滅しない。未指定はトーンの既定（danger は 0、他は 4000）。 */
  duration?: number;
}

export declare const toast: {
  success(message: string, options?: ToastOptions): string;
  info(message: string, options?: ToastOptions): string;
  warning(message: string, options?: ToastOptions): string;
  error(message: string, options?: ToastOptions): string; // danger
  dismiss(id: string): void;
};

// useToastStore.getState(): { toasts, paused, push, dismiss, clear, pause, resume }
```

---

## BlockedPageNotice — **新規**（#325）

ページ全体が使えない（ブロック状態）ときに、業務画面の代わりに主領域の中央へ出す案内カードです。RAG と NL2SQL の DB ゲートが別々に手書きしていた見た目を `packages/ui` にまとめました（3製品の DB ゲートは `@engchina/production-ready-system-settings` の `DatabaseGate` がこれを使います）。製品で全画面の案内カードを再実装しないでください。

| 決めたこと | 理由 |
|---|---|
| カードは `section` + `aria-labelledby`（見出しの `h1` の id）。見出しの id は呼び出し側が固定値で渡す | 支援技術がカードを見出しの名前で読み上げる。E2E が `aria-labelledby` でカードを引く |
| 見出しの上に 24px のアイコンを丸（トーンの `*-subtle` の面 + `*-fg` の色）で出し、`aria-hidden` にする。既定のトーンは `warning` | 色だけで意味を伝えない（状況は見出しと本文で書く）。エラー画面（danger）にせず、落ち着いた案内にする |
| 本文 → 補足（診断コードなど）→ 操作 → 区切り線の下の補足の順。渡したものだけを描く | 原因 + 次の行動を上に置く（UX 契約 messaging §0 / §9）。空の面を描かない |
| 操作の行は中央ぞろえで折り返す。主操作（設定を開くリンク等）を先頭に置く | 375px でも横にはみ出さない。Tab 順が見た目の順と同じ |
| 文言はすべて翻訳済みの文字列で渡す | `packages/ui` は i18n を持たない |

```tsx
<BlockedPageNotice
  title="データベースを起動してください"
  titleId="database-unavailable-title"
  icon={Database}
  message="データベースを起動してから再試行してください。"
  details={<p role="status">診断コード: wallet_not_found</p>}
  actions={
    <>
      <Link to="/settings/database#adb-management" className={buttonVariants({ variant: "primary" })}>…</Link>
      <Button variant="secondary" icon={RefreshCw} onClick={retry}>再試行</Button>
    </>
  }
  footer="OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。"
/>
```

### BlockedPageNotice の props

```ts
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export interface BlockedPageNoticeProps {
  title: string;
  titleId: string;
  icon: LucideIcon;
  /** 既定は "warning"。 */
  tone?: "success" | "info" | "warning" | "danger";
  message?: ReactNode;
  details?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  testId?: string;
  className?: string;
}

/** @dsComponent */
export declare function BlockedPageNotice(props: BlockedPageNoticeProps): JSX.Element;
```

---

## SelectField — 変更（#352）

選択肢の一覧を **body へ Portal で描く**ようにしました。親の `overflow: hidden / auto`（`DataTable` のセル・モーダル・カード・スクロール枠）で切れず、画面の下端では上に反転します。位置の計算は `FloatingActionMenu` と同じ `useFloatingMenuPosition`（`floating-menu.tsx`）を共有します。props・id・aria は変えていません（`id` / `${id}-option-${index}` / `aria-controls` / `aria-activedescendant` / `aria-labelledby`）。

| 決めたこと | 理由 |
|---|---|
| 一覧は `position: fixed` で body に出し、トリガーと同じ幅・左端にそろえる。開いている間はスクロール（どの祖先でも）とリサイズに追従する | 親の overflow に切られない。375px でも画面の外にはみ出さない（左右 8px の余白で止める） |
| 下に 16rem（`max-h-64` と同じ）が入らず、上のほうが広ければ上に反転する。どちらにも入らなければ広い側に出し、空きの高さまで縮めて内部スクロールにする。反転の向きは `data-floating-menu-placement`（`top` / `bottom`） | 画面の下端のフィールドでも選択肢が見える。E2E が向きを確かめられる |
| モーダル（`aria-modal="true"` / `<dialog open>`）の中で開いたときは、body ではなくそのモーダルの中に描く | モーダルの外に出すと、支援技術がモーダルの外として読まない（aria-modal）・`<dialog>` の top layer の下に隠れる |
| z-index を持つ層（モーダル・固定ヘッダーなど）の中では、その層より 1 段上に出す。層の外は `--z-dropdown` のまま | Portal で描くと、`--z-dropdown` のままではモーダルの暗幕や層の下に隠れる |
| 外側クリックの判定は、フィールド本体と一覧の両方を内側とみなす。一覧を押してもフォーカスはボタンに残す | 一覧は DOM 上フィールドの外にある。スクロールバーや余白を押しても閉じない |
| 選択は `click` で確定する（以前は `pointerdown`） | タッチで一覧をスクロールしただけで選ばれないようにする。Portal でも React のイベントは祖先に伝わるため、一覧のクリックは表の行などへ伝えない |
| 矢印キー・Home / End・PageUp / PageDown（10 件）・typeahead で強調を動かすと、強調中の選択肢を一覧の中で見せる（`scrollIntoView({ block: "nearest" })` と同じ動き）。マウスの移動ではスクロールしない | 7 件目以降が枠の外に出たままにならない。`scrollIntoView` は一覧の外（ページ本体）まで動かすので、一覧の `scrollTop` だけを動かす |
| typeahead（W3C APG の select-only combobox）: 文字を打つと、その文字で始まる選択肢を強調する（閉じていれば開く）。続けて打つと前方一致で絞り込み、同じ文字の繰り返しは順に巡る。500ms 入力がなければ入力をリセットする。大文字小文字・全角半角は区別しない | キーボードで長い一覧（リージョン・評価指標など）を選べる。Space は従来どおり選択・開閉に使う |
| 一覧が開いているときの Esc は一覧だけを閉じ、囲むモーダルには伝えない | Esc 1 回でモーダルごと閉じない（APG: Esc は一覧を閉じる） |

- E2E で一覧を引くときは、フィールドを囲む要素ではなく `page.getByRole("listbox", { name: "<ラベル>" })` で引きます（一覧は body の直下、モーダルの中ならモーダルの直下にあります）。
- 一覧は `position: fixed` です。`transform` / `filter` / `contain` を持つ要素（固定の containing block）をモーダルの外枠にすると位置がずれるため、モーダルの中央寄せは flex で行います（`ConfirmDialog` と同じ）。
- 純粋関数 `computeFloatingMenuLayout`（位置と反転）・`floatingLayerZIndex`（重なり順）・`selectPortalContainer`（描く先）・`findTypeaheadIndex` / `typeaheadStartIndex`（typeahead）・`nearestScrollTop`（強調中の選択肢のスクロール）・`isInsideAny`（外側クリック）は `packages/ui/tests/select-field.test.tsx` が確かめます（操作メニューの左右の反転は `packages/ui/tests/floating-menu.test.ts`）。パッケージのルートからは export しません。

---

## PageHeader の「その他の操作」メニュー — 変更（#363）

`lg` 未満で主操作以外をまとめる「その他の操作」メニューを、ヘッダーの中の `absolute right-0` から **`FloatingActionMenu`（body へ Portal・`position: fixed`）** に変えました。375px では操作がタイトルの下に折り返して「その他の操作」が左端に来るため、右端揃えのままだとメニューが画面の左外に切れていました。props・`data-testid="page-actions-more"`・aria（`aria-haspopup` / `aria-expanded` / `aria-controls` / `role="menu"` / `aria-label`）は変えていません。

| 決めたこと | 理由 |
|---|---|
| 左右はボタンの右端にそろえる（従来の位置）。右端揃えで画面（左右 8px の余白の内側）に入らず、左端揃えなら入るときは左端にそろえる。どちらも入らなければ画面の内側にずらす。そろえた端は `data-floating-menu-align`（`start` / `end`） | ボタンがどこにあっても全項目が見えて押せる。E2E が向きを確かめられる |
| 上下は `SelectField` と同じ（下に入らなければ上に反転、どちらも入らなければ広い側で内部スクロール） | 位置の計算は `computeFloatingMenuLayout` 1 か所にまとめる |
| 左右の反転は `FloatingActionMenu` を使うすべてのメニュー（`RowActionMenu`・`ObjectActionBar`・`FormActionBar`）に効く。`SelectField`（`stretch`）は反転しない | 同じ問題を同じ仕組みで直す。一覧はトリガーと同じ幅なので反転の必要がない |
| 外側クリックの判定は、ボタン側とメニューの両方を内側とみなす | メニューは DOM 上ヘッダーの外にある |
| Tab はメニューを閉じてボタンの次の要素へ、Shift+Tab はボタンへ戻す。開く・矢印 / Home / End・Escape（ボタンへ戻る）は従来どおり | メニューは body の末尾にあるため、既定の Tab では文書の末尾へ抜けてしまう |

- E2E でメニューを引くときは、ヘッダーの中ではなく `page.getByRole("menu", { name: "その他の操作" })` で引きます（メニューは body の直下にあります）。

---

## ToggleChip / Switch — 変更（#364）

タッチ端末（`pointer: coarse`）でだけ、当たり判定を 44px（`--control-height-touch`）以上に広げました。見た目の大きさ・props・aria は変えていません。仕様は README §4「タッチ端末の当たり判定」。

| 決めたこと | 理由 |
|---|---|
| 両部品の class に `pr-touch-target relative` を付け、`structure/touch-target.css` の `@media (pointer: coarse)` の中だけで擬似要素を作る | Button（`--button-height-*`）と同じく、画面幅ではなく入力方式で判定する。マウス環境は変えない（#338 のレビューで見送り） |
| `::before` を `inset: min(0px, (100% - 44px) / 2)` で見た目の中心から広げる（縦は高さ、横は幅に対する %）。44px 以上の辺は広げない | チップ（約 26px）は縦に約 9px ずつ、スイッチ（44 × 24px）は縦に 10px ずつ広がる。「ON」のような短いチップだけ横にも数 px 広がる |
| `::after`（`inset: -1px`・`z-index: 1`）で自分の見た目の範囲を、隣の部品の `::before` より上に置く | 後ろの部品の `::before` が前の部品の見た目を覆うと、見た目の端を押したのに隣が切り替わる。部品自体は重なり順の文脈を作らないので、`::after` はスティッキーな表頭（z-10）やメニュー（`--z-dropdown` 以上）より下にある |
| 擬似要素は背景・枠・影を持たない | 見た目も強制カラーモードも変わらない |

```tsx
// 製品は何も書かない。これまでどおり使えば、タッチ端末で当たり判定が広がる。
<div className="flex flex-wrap gap-1" role="group" aria-label="再ランキング">
  <ToggleChip selected={value === null} onClick={() => onChange(null)}>継承</ToggleChip>
  <ToggleChip selected={value === true} onClick={() => onChange(true)}>ON</ToggleChip>
  <ToggleChip selected={value === false} onClick={() => onChange(false)}>OFF</ToggleChip>
</div>
<Switch checked={enabled} onCheckedChange={setEnabled} aria-labelledby="history-label" />
```

- E2E で当たり判定の実寸を測るときは、`hasTouch` のプロジェクト（Pixel 5 など。Chromium では `(pointer: coarse)` が一致する）で `document.elementFromPoint` を見た目の外側に向けて探るか、`page.touchscreen.tap` で見た目の外側を押します（NL2SQL の `tests/e2e/touch-targets-tabs.spec.ts`）。

---

## Tabs — 変更（#364）

入りきらずに横へスクロールするとき、スクロールできる方向の端だけをフェードし、選んだタブをフェードの外まで見せるようにしました。props・id・aria・キー操作は変えていません。

| 決めたこと | 理由 |
|---|---|
| タブの列に `pr-tabs-scroll`（`structure/tabs.css`）を付け、続きがある側に `data-scroll-start` / `data-scroll-end` を付ける。付いているときだけ `mask-image` の線形グラデーションで端の `--tab-fade-width`（2rem）を透かす | 色を重ねる方式は、ヘッダー・カード・面スコープごとに背景色を合わせ直す必要がある。mask はどの背景でも同じに見える。入りきるときは何も付けない（見た目は変わらない） |
| 出し分けはスクロールのイベント・`ResizeObserver`（タブの列と各タブ）・`items` の変更で判定する（純粋関数 `tabsScrollEdges`、1px 未満の誤差は無視） | 画面の resize・サイドバーの開閉・件数バッジの変化でも正しい側だけにフェードが出る |
| 選択中のタブ（キーボード・クリック・呼び出し側の変更、初回の表示）は、タブの列の `scrollLeft` だけを動かしてフェードの外まで見せる（純粋関数 `revealTabScrollLeft`。余白は `scroll-padding-inline` = フェードの幅）。キーボードでは `focus({ preventScroll: true })` にする | ブラウザの既定のスクロールでは、タブがフェードの下に残ったり、ページ全体まで動いたりする |
| スクロールバーを隠す指定を utility から `pr-tabs-scroll` に移した。強制カラーモードでは mask を外し、`scrollbar-width: thin` でスクロールバーを出す | 強制カラーモードで文字を透かさない。utilities レイヤーの `[scrollbar-width:none]` は components レイヤーの上書きに勝つため |
| 動き（transition / animation）を持たない | 出し分けは即時。`prefers-reduced-motion` でも同じ |

- タブの列は `relative` になりました（`offsetLeft` をスクロールの内容の左端からの距離にするため）。
- 純粋関数 `tabsScrollEdges` / `revealTabScrollLeft` は `packages/ui/tests/touch-target-tabs.test.tsx` が確かめます。パッケージのルートからは export しません。

---

## Tooltip — **新規**（#372）

アイコンだけのボタンの説明に使う吹き出しです。WAI-ARIA APG の Tooltip パターンに従います（振る舞いの表は README §4「`Tooltip`」）。**`iconOnly` の `Button` は既定で `aria-label` と同じ文言を出す**ので、通常は `Tooltip` を直接書きません。

```tsx
import { Button, Tooltip } from "@engchina/production-ready-ui";
import { ChevronLeft, X } from "lucide-react";

// 既定: aria-label と同じ文言を出す（aria-describedby は付けない。二重に読み上げない）
<Button variant="ghost" iconOnly icon={X} aria-label={t("common.dismiss")} />

// 文言を変える: ショートカットキーを添える（aria-label と違うので aria-describedby で説明になる）
<Button variant="ghost" iconOnly icon={ChevronLeft} aria-label={t("preview.viewer.previousPage")}
  tooltip={`${t("preview.viewer.previousPage")} (PageUp)`} />

// 出さない（同じ場所に名前が見えている等）
<Button variant="ghost" iconOnly icon={X} aria-label={t("common.dismiss")} tooltip={false} />

// 見た目を変えずに title を置き換える（iconOnly でない、実質アイコンだけのボタン）
<Button size="sm" variant="ghost" icon={Plus} aria-label={t("graph.zoomIn")} tooltip={t("graph.zoomIn")} />

// Button 以外のフォーカスできる要素 1 つに説明を足す
<Tooltip content={t("legend.toggleHint")}>
  <button type="button" aria-pressed={enabled}>…</button>
</Tooltip>
```

### Tooltip の props

```ts
export interface TooltipProps {
  /** 翻訳済みの短い文。操作できる要素や書式は入れない。 */
  content: string;
  /** トリガー（フォーカスできる要素 1 つ）。ref とイベントを受け取れること。 */
  children: React.ReactElement;
  /** 空きがあれば出す側（既定は上）。入らなければ反転する。 */
  placement?: "top" | "bottom";
  /** true で出さず、aria-describedby の結び付けも外す。 */
  disabled?: boolean;
}
```

| 決めたこと | 理由 |
|---|---|
| 吹き出しは `role="tooltip"`。文言がトリガーの `aria-label` と違うときだけ `aria-describedby` で結び付け、閉じている間も `hidden` で置いておく。同じときは結び付けず `aria-hidden` | APG は説明（description）として結び付ける。名前と同じ文言を説明にすると二重に読み上げる。フォーカスした時点で説明が読めるように、説明の要素は先に置く |
| 開閉は `createTooltipController`（タイマーだけを使う状態機械）、位置は `computeTooltipLayout`（純粋関数）に分ける | 遅延・Escape・hoverable・タッチ端末・反転を DOM なしの単体テストで確かめる |
| Portal の描く先は `SelectField` と同じ（モーダルの中ならモーダル、それ以外は body）。重なり順は `resolveFloatingLayerZIndex(trigger, "--z-popover")`（操作メニューと共有） | `<dialog>` の top layer・`aria-modal` の外に出さない。固定ヘッダー・モーダルの中で隠れない |
| 開いている間の Escape は `window` の capture で受けて握りつぶす | 1 回目の Escape で囲むモーダル（`ConfirmDialog` は document で Escape を受ける）まで閉じない |
| ボタンを押したら閉じ、ポインタが離れるまで出さない。フォーカスで開いた吹き出しは `pointer-events: none` | 押した結果（メニュー・ダイアログ）を隠さない。キーボードで開いた吹き出しが、隣の要素のマウス操作を塞がない |

- E2E で吹き出しを引くときは、`page.locator('[role="tooltip"]:not([hidden])')` で引きます（説明用の吹き出しは閉じている間も `hidden` で body にあり、名前と同じ文言の吹き出しは `aria-hidden` なので `getByRole("tooltip")` では引けません）。説明は `toHaveAccessibleDescription` で確かめます。
- 単体テストは `packages/ui/tests/tooltip.test.tsx`、実ブラウザは NL2SQL の `tests/e2e/tooltip.spec.ts`（desktop のホバー・キーボード・Escape・反転、mobile-375 のタッチ端末）。

## 読み込み中と一覧の表示密度 — **新規**（#265）

3 製品の一覧・読み込み中の表示を NL2SQL の基準にそろえるための定数と形のある Skeleton です。値は製品で書かず、ここから import します。

```ts
// packages/ui/src/lib/list-density.ts（NL2SQL の src/lib/list-density.ts から移した。値は同じ）
INFORMATION_TABLE_VISIBLE_ROWS       // { base: 5, md: 8 } — DataTable の visibleRows
INFORMATION_TABLE_FIXED_VISIBLE_ROWS // 5 — 幅によらず 5 行の一覧
INFORMATION_TABLE_ROW_CLASS          // "h-[3.5rem]" — DataTable の rowProps の className（行の最小高さ）
INFORMATION_LIST_ROW_CLASS           // "min-h-[3.5rem]" — 表ではない行リストの行
INFORMATION_LIST_SCROLL_CLASS        // "max-h-[17.5rem] overflow-auto md:max-h-[28rem]" — 表ではない行リストの 5 / 8 行
INFORMATION_COMPACT_LIST_FIVE_ROW_SCROLL_CLASS // "h-56 max-h-56 overflow-auto"
INFORMATION_TABLE_FOCUS_CLASS        // Tab で到達できるスクロール領域のフォーカスの表示（outline）
```

```tsx
/** 見た目は 1 つ: 地は --color-surface-hover、prefers-reduced-motion では点滅しない。 */
export const SKELETON_CLASS = "animate-pulse rounded-md bg-surface-hover motion-reduce:animate-none";
export function Skeleton({ className, testId }: { className?: string; testId?: string });

/** 表の形。DataTable と同じ枠・表頭（surface-sunken、2.5rem）・行（3.5rem）に列ごとの棒。
 *  rows の既定は { base: 5, md: 8 }。md 以上だけの行は CSS（hidden md:flex）で出し分け、最初の描画で高さが決まる。 */
export function TableSkeleton({ rows, columns = 4, className, testId }: TableSkeletonProps);

/** 表ではない行リスト（カードの行・履歴）の形。角丸の塊を gap-2 で並べる。 */
export function ListSkeleton({ rows, rowClassName = "h-[3.5rem]", className, testId }: ListSkeletonProps);

/** 設定カード・エディタの形。見出し → ラベル + 入力欄（--button-height-md）× fields → 右寄せの操作行。 */
export function FormSkeleton({ fields = 4, title = true, actions = true, className, testId }: FormSkeletonProps);
```

- 形のある Skeleton は `aria-hidden` です。**読み込み中の文言と経過時間は `TimedLoadingState` が出す**ので、必ずその子に置きます（UX 契約 messaging.md §3.6 / §3.7）。
- `DataTable` の `loading` の行・`LoadingState` の棒も同じ `SKELETON_CLASS` を使います。`DataTable` の `loading` は、`loadingRows` を省略すると `visibleRows` の行数（無ければ 3 行）の Skeleton で高さを予約します。
- 同じ取得の経過時間は 1 か所だけに出します。2 つの領域が同じ取得を待つときは、片方は形だけ（`TableSkeleton` を `TimedLoadingState` で包まない）にします。

標準の組み合わせ:

```tsx
{query.isPending ? (
  <TimedLoadingState label={t("xxx.loading")} operationKey="xxx-load" testId="xxx-loading">
    <TableSkeleton columns={5} />
  </TimedLoadingState>
) : (
  <div className="grid gap-2">
    <DataTable
      rows={pageItems}
      rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
      stickyHeader
      visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
      scrollAriaLabel={t("xxx.scrollLabel")}
      …
    />
    <Pagination page={page} totalPages={totalPages} onPageChange={setPage} summary={…} prevLabel={…} nextLabel={…} />
  </div>
)}
```
