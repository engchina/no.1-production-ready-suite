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
  /**
   * 詳細・作成・編集の画面の「一覧へ戻る」（#618）。タイトルの上の左端に ghost の sm ボタン（ArrowLeft）で出す。
   * actions に id: "back" の操作を入れない。2 階層のパンくずの代わり（3 階層以上だけ breadcrumbs）。
   */
  back?: { label: string; onClick: () => void; ariaLabel?: string; disabled?: boolean; testId?: string };
  /** 並びは danger → utility → secondary → primary（右端が primary）。詳細・作成・編集の画面は 変更を破棄（secondary）→ 保存（primary）。 */
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
  // 寸法は sm / md / lg とも 16px。スピナーは fg-muted（disabled の地の上で 3:1 以上。README §4「Spinner」、#395）。
  const leading = loading ? <Spinner size={16} className="text-fg-muted" /> : icon ? <Icon name={icon} size={16} /> : null;

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
  /** マウス環境でも 44px にする。製品では使わない（adherence の lint が検出する。#613）。packages/ui の閉じる・メニューのボタンだけ。 */
  touchTarget?: boolean;
  /** true で先頭アイコンがスピナーに置き換わる。ラベルは変えない。aria-disabled でフォーカスを保つ。 */
  loading?: boolean;
  pressed?: boolean;
};

/** @dsComponent */
export declare function Button(props: ButtonProps): JSX.Element;
```

### ButtonLink（#800）

画面を移るだけの操作を、`Button` と同じ見た目のリンクで出す（README §4「Button」の「画面を移るだけの操作は `ButtonLink`」）。

```ts
/** react-router の Link をそのまま渡せる（packages/ui はルーターに依存しない）。 */
export type ButtonLinkComponent = React.ComponentType<{
  to: string;
  className?: string;
  children: React.ReactNode;
  "aria-label"?: string;
  "data-testid"?: string;
}>;

export type ButtonLinkProps = ButtonVariantToneProps & {
  to: string;
  /** 省略すると <a href>。 */
  linkComponent?: ButtonLinkComponent;
  size?: "sm" | "md" | "lg";
  icon?: LucideIcon;
  trailingIcon?: LucideIcon;
  children: React.ReactNode;
  className?: string;
  "aria-label"?: string;
  testId?: string;
};

/** 既定の variant は secondary。loading / disabled は持たない。 */
export declare function ButtonLink(props: ButtonLinkProps): JSX.Element;
```

```tsx
<ButtonLink to={`/settings/security/permissions?role=${id}`} linkComponent={Link} size="sm" icon={LockKeyhole}>
  権限管理で設定
</ButtonLink>
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

### md 未満のナビのドロワー（#367。案 A に決定）

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
  /** aria-required と RequiredBadge。required だけで「必須」を出す（requiredLabel は条件付きの必須の文言だけ上書き。#531）。 */
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

## Toaster / toast / Banner — 変更（#351 / #411）

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

### 置き場所（#411）

`<Toaster/>` は主操作を覆わない位置に通知を出します。製品で位置を変えないでください（`placement` プロップは削除）。規則の正本は UX 契約 messaging §3.1、見た目の決定は README §4「Toaster」。

| 決めたこと | 理由 |
|---|---|
| md 以上は `PageHeader` に重ね、ページの操作のすぐ左に右端をそろえる（上端は `PageHeader` の上端 + 1rem。幅は内容に合わせて `--toast-width-min` 22rem〜`--toast-width-max` 32rem。#899）。操作が無い・タイトルの下へ折り返したときは右端から 1rem、操作の左右に 14rem が取れなければ `PageHeader` の下端 + 1rem の右、`PageHeader` が見えなければ画面の右上 | `PageHeader` のタイトルの面には操作が無い。ページの操作、内容の面の右上の操作（`ObjectActionBar` / `ContentActionBar`）、ページの末尾の操作（スクロールしきると画面の下端に来る）のどれからも離れる |
| md 未満は上端の全幅。上端のバーに重ねて上端から 0.5rem、左はメニューのボタンの後ろ（`calc(1rem + var(--control-height-touch))`） | 375px では末尾の操作が全幅になり、下端の通知は必ず覆う。上端のバーの製品名には操作が無い |
| `PageHeader` の `<header>` に `data-page-header`、ページの操作の並びに `data-page-header-actions` を付け、`Toaster` はその位置を読む。通知が出ている間だけ、スクロール・リサイズ・`PageHeader` の大きさの変化に追従する | lg 未満の `PageHeader` は本文と一緒にスクロールする。画面を移ると `PageHeader` が差し替わる |
| 通知の領域に `data-toast-placement`（`page-header` / `below-page-header` / `top-right` / `top-bar`） | E2E と目視で、どの規則で置いたかを確かめられる |
| 新しい通知は下に足し、上から降りてくる（`toast-in`） | 読み上げ・Tab の順と見た目の順をそろえる。上端から出るものは上から現れる |

E2E は NL2SQL の `tests/e2e/_helpers/toast.ts` の `expectToastStackAtTop`（ページの操作・メニューのボタンと重ならない、規則どおりの上端）で確かめます。通知を閉じてから押す回避（`dismissToasts`）は要りません。

メッセージの部品（`Toast`・`Banner`・`FormStatus`・`ProcessingIndicator`・`BlockedPageNotice` の面と `MessageText`）は、共有の CSS の `.pr-message-text`（`word-break: auto-phrase`・`overflow-wrap: anywhere`・`text-wrap: pretty`）で日本語を文節で折り返します（#899。README §4「メッセージの本文の折り返し」）。製品でメッセージに `word-break` などを書かないでください。語の途中で折り返さないことは NL2SQL の `tests/e2e/message-wrapping.spec.ts`（文字ごとの描画位置から折り返しの位置を求め、`Intl.Segmenter` の語の境界と比べる）で確かめます。

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
  title="データベースに接続できません"
  titleId="database-unavailable-title"
  icon={Unplug}
  message="データベースが停止しているか、ネットワーク経由で到達できません。データベース設定で起動状態と接続情報を確認してから、再試行してください。"
  details={<p role="status">診断コード: wallet_not_found</p>}
  actions={
    <>
      <ButtonLink to="/settings/database#adb-management" linkComponent={Link} variant="primary" icon={Settings} trailingIcon={ArrowRight}>データベース設定を開く</ButtonLink>
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

### SelectField — `disabled` / `labelHidden` / `data-testid`（#631）

製品のネイティブの `<select>` を置き換えるために、次の props を足しました（既存の props・id・aria は変えていません）。

```tsx
<SelectField
  id="relation-source-key"
  label={t("expression.sourceKey")}
  value={key}
  options={columnOptions}
  onValueChange={setKey}
  disabled={relationSource !== "MANUAL"}   // 無効
  labelHidden                              // 表の行などで、ラベルを読み上げだけにする
  data-testid="relation-source-key"        // ボタン（role=combobox）に付く
  size="sm"
/>
```

| 決めたこと | 理由 |
|---|---|
| `disabled` はボタンのネイティブの `disabled`（`aria-disabled` ではない）。押しても・矢印キー・Enter / Space・typeahead でも開かない。開いているときに無効になったら一覧を閉じる | 無効な選択欄は操作できないので Tab で止めない（ネイティブの select と同じ）。`Button` の `loading` と違い、フォーカス中に無効になる流れ（押した直後の処理中）が無い |
| 無効の地・文字は `--color-surface-disabled` / `--color-fg-disabled`、シェブロンも `--color-fg-disabled`、カーソルは `not-allowed`。ホバーの地は有効なときだけ（`enabled:hover:`）。枠線は残す | `TextField` の無効と同じ見た目にし、押せそうに見せない。欄の形は残して、並んだ欄と位置がそろう |
| `labelHidden` はラベルを `sr-only` にし、`aria-labelledby` で読み上げ名は残す | 表の行・一覧のツールバーのように周りで目的が分かる所だけに使う（`TextField` の `labelHidden` と同じ）。フォームの欄では使わない |
| ボタンと選択肢（`role=option`）に値の `data-value` を出す | e2e が値で選び（`selectOption` の代わり）、選択中の値を確かめられる（`toHaveValue` の代わり）。表示には使わない |
| `SearchableSelectField` の無効も同じ見た目・動き（ホバーは有効なときだけ、シェブロンと文字の色、開いているときに無効になったら閉じる）にそろえた | 2 つの選択欄で無効の見え方が違わないように |

- ネイティブの `<select>` は adherence の lint が検出します。`<optgroup>` や選べない選択肢（`<option disabled>`）が要るなど `SelectField` で表せない所だけ、理由を添えて局所的に除外し、`fieldControlClassName({ size, width })` で見た目をそろえます。
- e2e は combobox を押して一覧の選択肢を選びます（NL2SQL `tests/e2e/_helpers/select-field.ts`・Agent `e2e/fixtures/select-field.ts` の `chooseSelectFieldOption(combobox, "値" | { label })` / `expectSelectFieldValue`）。
- 単体テストは `packages/ui/tests/select-field-disabled.test.tsx`。

### SelectField — `emptyOptionLabel` / `describedBy`（#647）

任意の選択欄を未選択へ戻す選択肢と、欄の外の説明への `aria-describedby` を足しました（既存の props・id・aria は変えていません）。

```tsx
<SelectField
  id="scope-filter-column"
  label={t("scopeFilterColumn")}
  value={columnName}                         // 未選択は ""（値の型が "" を含むときだけ emptyOptionLabel を渡せる）
  placeholder={t("scopeFilterColumnPlaceholder")}   // 未選択のときのボタンの文言（例:「列を選択」）
  emptyOptionLabel={t("unselected")}         // 一覧の先頭の「未選択」。選ぶと onValueChange("")
  options={columnOptions}
  onValueChange={setColumnName}
/>

<SelectField id="dataset" label={t("dataset")} value={dataset} options={datasets}
  describedBy="dataset-description"          // 右に置いた説明の id（helper・error の id の後ろに足す）
  onValueChange={setDataset} />
<p id="dataset-description">{t(`dataset.${dataset}.description`)}</p>
```

| 決めたこと | 理由 |
|---|---|
| 未選択へ戻す手段は、消去のボタンではなく**一覧の先頭の空の値の選択肢**（`emptyOptionLabel`） | W3C APG の select-only combobox は、ボタンの中に別の操作（消去のボタン）を入れない（Tab の止まり・読み上げ名が増える）。ネイティブの select の `<option value="">` と同じ操作で、↑↓・Home / End・typeahead・Enter・読み上げがそのまま効く。`SearchableSelectField` には消去の手段が無く、そろえる既存の形は無かった。値の選択肢と区別できるよう文字は `--color-fg-muted`（選択中・強調中は他と同じ） |
| 未選択のあいだ、ボタンは `placeholder`（無ければ `emptyOptionLabel`）を控えめの色で出す。`data-value` は `""` | 「列を選択」のように何を選ぶ欄かを見せ続ける。e2e は `chooseSelectFieldOption(combobox, "")` で未選択を選べる |
| `required` の欄と、`options` に空の値が既にある欄（「すべて」「既定の Binding」など、空の値が意味を持つ選択肢）には出さない | 必須の欄を未選択へ戻しても送信の検証で止まるだけ（誤りは別の値を選び直す）。空の値を 2 つ並べない |
| 値の選択肢が 0 件のあいだは開かない（「未選択」だけの一覧を出さない） | 候補の取得中に開いて空の一覧に見えるのを防ぐ（既存の動きのまま） |
| `emptyOptionLabel` の型は `"" extends T ? string : never` | 空の値を取れない列挙（`SelectField<"hr" \| "sales">` など）に渡すと型エラーにし、`onValueChange` に想定外の `""` が届かないようにする |
| `describedBy` は `helper`・`error` の id の後ろに足して `aria-describedby` に渡す（`SearchableSelectField` も同じ） | `TextField` の `aria-describedby` と同じ順。欄の横・下に置いた説明（NL2SQL のサンプルデータの種類の説明）を読み上げる |

- 使っている所: NL2SQL の Deep Data Security（`ScopeExpressionEditor` の「列」・関連テーブル条件の「候補を選ぶ Profile」「関連テーブル」「対象テーブルの列」「関連テーブルの列」）、`describedBy` は NL2SQL のサンプルデータの種類。Agent の Run の「実行先 Binding」は既定が無いとき必須なので出さない。
- 単体テストは `packages/ui/tests/select-field-empty-option.test.tsx`。

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
  /** false で説明として結び付けない（吹き出しは aria-hidden）。読み上げ名に同じ内容が既に入っているとき（#421 の RowTitleButton）。 */
  describe?: boolean;
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

---

## InfoTip — **新規**（#901）

操作・欄の補足の説明を常設せず、ラベル（または操作）の横の info アイコン（lucide `Info`、16px）から吹き出しで出します。振る舞いの表・業界の指針・常設の hint との使い分けは README §4「`InfoTip`」。作業に欠かせない情報（入力の条件・押せない理由・エラー・結果）には使いません。

```tsx
import { InfoTip, SelectField } from "@engchina/production-ready-ui";

// ラベルの横に置く（ラベルとアイコンは inline-flex でまとめ、行が折り返しても離さない）
<span className="inline-flex items-center gap-0.5">
  <span className="text-xs font-medium text-fg-muted">{t("chat.compare.label")}</span>
  <InfoTip
    label={t("chat.compare.infoLabel")}          // 「回答するモデルの説明」
    content={t("chat.compare.default")}
    contentTestId="chat-default-model"
  />
</span>

// 選択欄にも同じ説明を結び付ける（選んだ値ごとに説明が変わるとき）
const descriptionId = useId();
<InfoTip label={t("chat.engine.infoLabel")} content={engineOption.description} contentId={descriptionId} />
<SelectField id="sql-chat-engine" label={t("chat.engine")} labelHidden describedBy={descriptionId} … />
```

### InfoTip の props

```ts
export interface InfoTipProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "content" | "aria-label" | "type"> {
  /** 説明の文（翻訳済み。1〜3 文。リンク・ボタンなど操作できる要素は入れない）。 */
  content: string;
  /** アイコンのボタンの読み上げ名（翻訳済み）。「<ラベル>の説明」など、何の説明か分かる名前。 */
  label: string;
  /** 空きがあれば出す側（既定は上）。入らなければ反転する。 */
  placement?: "top" | "bottom";
  /** 吹き出しの id。説明の対象の欄の aria-describedby（SelectField の describedBy）からも結び付けるときに渡す。 */
  contentId?: string;
  /** 吹き出しの data-testid（E2E 用）。ボタンの data-testid はそのまま渡す。 */
  contentTestId?: string;
}
```

| 決めたこと | 理由 |
|---|---|
| 開閉は `Tooltip` と同じ `createTooltipController` に `press`（押して開いたまま固定。固定中はもう一度で閉じる）と `dismiss`（外側を押した）を足して使う。開いたきっかけは `data-tooltip-reason="press"` | 遅延・hoverable・Escape・「画面に 1 つだけ」（#655）を `Tooltip` と共有し、DOM なしの単体テストで確かめる |
| 吹き出しの描画（Portal・位置・重なり順・Escape）は `Tooltip` と同じ `TooltipBubble`。`InfoTip` は幅（20rem）・余白・太さ（400）・行間だけを足す | 見た目と位置の規則を 1 か所に保つ |
| 押して開いている間だけ、`document` の `pointerdown`（capture）で外側を押したら閉じる | クリックでフォーカスが移らないブラウザ（Safari）とタッチ端末でも閉じる |
| ボタンの `data-state` は `open` / `closed`（見た目のホバーの色に使う）。`aria-expanded` は付けない | 説明は `aria-describedby` で閉じていても読めるので、開閉は目で見るためのもの |

- E2E: 閉じている間は `toBeHidden()`、説明は `toHaveAccessibleDescription`、開いた吹き出しは `contentTestId`。タッチ端末の project ではホバーの代わりに `tap()`（ホバー・フォーカスでは開かない）。info アイコンの名前は「<ラベル>の説明」なので、同じ行のボタンは `exact: true` で引く。
- 単体テストは `packages/ui/tests/info-tip.test.tsx`（`press` / `dismiss`・タッチ端末・Escape・外側・読み上げの結び付け）。実ブラウザは RAG `e2e/search-answers.spec.ts`・NL2SQL `tests/e2e/sql-chat.spec.ts` / `nl2sql-ontology.spec.ts`・Agent `e2e/agent-versions.spec.ts` / `run-stream-auth.spec.ts`（desktop / 375px のタップ・キーボード・Escape）。

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

/** チャットの会話の形（#1153）。右寄せの質問の吹き出し（h-12・最大 85%）と左の回答の塊（h-32）を turns 組。
 *  3 製品のチャットの会話の欄で、前提の読み込み中に空の状態の代わりに出す（UX 契約 messaging.md §11.7）。 */
export function ChatSkeleton({ turns = 2, className, testId }: ChatSkeletonProps);
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

クライアント側で全件を持つ一覧は、上の組み合わせを 1 つにした `PagedDataTable` を使います（#265。RAG・Agent が使う）。

```tsx
// packages/ui/src/components/data/paged-data-table.tsx
export interface PaginationLabels {
  summary: (range: PaginationRange) => string;               // 「1 - 10 / 42 件」
  pageIndicator?: (page: number, totalPages: number) => string; // 「1 / 5 ページ」
  prev: string;
  next: string;
  ariaLabel?: string;
}

<PagedDataTable
  rows={rows}                 // 全件。表示する 10 件はこの部品が切り出す
  columns={columns}
  getRowKey={(row) => row.id}
  paginationLabels={labels}   // 製品の i18n で作る（パッケージは i18n に依存しない）
  scrollAriaLabel={t("xxx.scrollLabel")}
  resetKey={query}            // 任意。検索語・絞り込みが変わったときだけ 1 ページ目へ戻す
  page={page} onPageChange={setPage} // 任意。ページ番号を作業状態に残すとき
/>
```

- `stickyHeader`・`visibleRows`（`INFORMATION_TABLE_VISIBLE_ROWS`）・行の最小高さ（`INFORMATION_TABLE_ROW_CLASS`。`rowProps` の className と合わせる）はこの部品が付けます。
- `resetKey` を省くと、再取得で行が変わってもページを戻しません（Agent の Run・承認の 5 秒ごとの再取得など）。行が減って範囲外になったら表示だけ末尾のページに寄せます。
- 製品は文言だけを渡す薄いラッパーを持ちます（RAG の `components/PagedDataTable.tsx`、Agent の `components/ListViews.tsx`。Agent はページ番号を `sessionStorage` の作業状態に残す `pageKey` を足している）。
- サーバー側のページング（offset / limit / total）は `DataTable` + `Pagination` に `offsetPagination` の結果を渡します（Agent の監査、RAG の文書一覧・チャットの会話一覧）。
- カーソル（`next_cursor`）と `total` を返す API を「前へ / 次へ」で送る一覧は、前へ戻るカーソルを画面が積み、`page`（積んだ数 + 1）・`totalPages`・`range` を `Pagination` に渡します（NL2SQL の SQL生成評価の `cursorPagination`、#403）。
- 表ではない行リスト（カードの行・選択と連動する一覧）は `INFORMATION_LIST_SCROLL_CLASS` で 5 / 8 行の高さにし、中をスクロールします。行が表より高くても手書きの `max-h-[…]` にしません（NL2SQL の実行履歴、RAG の chunk・抽出セグメント、#403）。
- 基準から外す一覧（選択と連動する一覧・カーソル型の「さらに読み込む」・分析の一覧の件数の切り替え）と理由は、UX 契約 `page-archetypes.md` の「一覧の型と、基準から外す例外」にあります。

---

## ExecutionConfirmationField — **新規**（#379）

破壊的な操作の **実行確認語（type-to-confirm）** の入力欄です。NL2SQL の `ExecutionConfirmationField`（製品のコード、14 か所で使用）と、system-settings のシステムテーブルの確認語欄（#325 で作ったコピー）を `packages/ui` に一本化しました。Agent の実行時スナップショットの置換の確認語もこれに置き換えました。**製品で確認語の入力欄を再実装しないでください。**

| 決めたこと | 理由 |
|---|---|
| 面は中立（`--color-surface-sunken` + `--color-border`）。入力前は danger 色を使わない | 操作前から赤いと、エラーが起きているように見える（README §4「確認語欄（実行確認語）の色」、UX 契約 messaging §3.5） |
| 見出しの行に「入力条件: `{phrase}`」と状態のバッジ（未入力 / 不一致 / 確認済み）。バッジは `aria-live="polite"` | 状態を色だけで示さない。入力の結果を読み上げで知らせる |
| 確認語は等幅・太字で、`.` / `_` / `$` / `#` の直後で折り返す（`<wbr>`） | 識別子（`ADMIN_EXECUTE`・`OWNER.OBJECT`）を読み違えない。375px でも横にはみ出さない |
| 一致しない語を入れたときだけ `aria-invalid="true"`、説明文とバッジを danger 色にする | 空白だけの入力は未入力として扱う |
| 入力欄は下の操作行（実行・取消）と同じ lg（`--control-height-lg` の 40px、タッチ端末は 44px。#613 で 44px 固定から変更）、`required` / `aria-required`、説明を `aria-describedby`。自動補完・自動修正・スペルチェックをしない。フォーカス中は枠線を `--color-danger-fg` にする（ring は重ねない） | 操作行と同じ高さの段にそろえる（README §4「操作部品の高さと幅」）。確認語は手で入力させる |
| 実行 / キャンセル等の操作は `actions` で渡し、区切り線（`border-t`）の下に置く。640px 未満は縦に並べる | 確認語と実行を 1 つの区画にまとめる（README §4「カード内の操作行」） |
| **一致の判定は呼び出し側**（`confirmed`）。前後の空白を許すかどうか・確認語の値は製品が決める | backend の確認と同じ規則で判定するため。`packages/ui` は業務の語を知らない |
| 文言は `labels` で差し替える。未指定の項目は既定（`DEFAULT_EXECUTION_CONFIRMATION_LABELS`：実行確認語 / 必須 / 入力条件: {phrase} / 未入力 / 不一致 / 確認済み） | 3 製品で同じ語をそろえる。製品固有の見出し（Agent の「確認入力」）や system-settings の上書き可能な文言は `labels` で渡す |

```tsx
<ExecutionConfirmationField
  value={confirmation}
  onChange={setConfirmation}
  confirmed={confirmation.trim() === "ADMIN_EXECUTE"}
  expectedLabel="ADMIN_EXECUTE"
  helper="ADMIN_EXECUTE を入力すると実行できます。"
  disabled={running}
  actions={
    <>
      <Button variant="danger" size="lg" className="w-full sm:w-auto" icon={Play} loading={running} disabled={!confirmed}>
        実行
      </Button>
      <ClearActionButton label="入力をリセット" size="lg" className="w-full sm:w-auto" onClick={clear} />
    </>
  }
/>
```

### ExecutionConfirmationField の props

```ts
export interface ExecutionConfirmationLabels {
  label: string;     // 見出し。既定「実行確認語」
  required: string;  // 必須のタグ。既定「必須」
  expected: string;  // `{phrase}` の位置に確認語を差し込む。既定「入力条件: {phrase}」
  pending: string;   // 既定「未入力」
  mismatch: string;  // 既定「不一致」
  confirmed: string; // 既定「確認済み」
}

export interface ExecutionConfirmationFieldProps {
  value: string;
  onChange: (value: string) => void;
  /** 一致の判定は呼び出し側。 */
  confirmed: boolean;
  expectedLabel: string;
  /** 既定は expectedLabel。 */
  placeholder?: string;
  helper: ReactNode;
  disabled?: boolean;
  /** 既定は useId。E2E やページ内リンクで引くときだけ渡す。説明の id は `${id}-helper`。 */
  id?: string;
  labels?: Partial<ExecutionConfirmationLabels>;
  actions?: ReactNode;
  className?: string;
}

/** 表示の区分（空白だけは pending）。 */
export declare function executionConfirmationStatus(value: string, confirmed: boolean): "pending" | "mismatch" | "confirmed";

/** @dsComponent */
export declare function ExecutionConfirmationField(props: ExecutionConfirmationFieldProps): JSX.Element;
```

- ルート要素は `data-testid="execution-confirmation-field"` と `data-confirmation-status`（`pending` / `mismatch` / `confirmed`）を持ちます。3 製品の E2E はこの testid で確認語欄を引きます。
- `actions` の実行ボタンを一致するまで無効にし、使えない理由として説明（`helper`）を読み上げるときは、`id` を渡し、ボタンの `aria-describedby` に `${id}-helper` を一致しない間だけ指定します（例: Agent の実行時スナップショットの置換。#426）。
- 確認語はページを離れる・戻るとき、対象や入力が変わったときに呼び出し側で空に戻します（UX 契約 workspace-state.md）。

## TextField — 変更（#384）

先頭アイコン（`leadingIcon`）・後置スロット（`trailing`）・クリア（`onClear`）を足しました。検索欄は製品で手書きせず、これで作ります（決めたことの表は README §4「`TextField` の先頭アイコン・後置スロット」）。既存の props・id・aria は変えていません。

```tsx
import { TextField } from "@engchina/production-ready-ui";
import { Search } from "lucide-react";

// 一覧の絞り込みの検索欄は TextField ではなく SearchField で作る（下の「SearchField」、#535）。

// 重い検索の質問欄（明示的に実行する）: Enter の判定は IME 対応の isSubmitEnter
<TextField
  id="search-test-query"
  label={t("knowledgeBases.searchTest.title")}
  labelHidden
  value={query}
  onValueChange={setQuery}
  onKeyDown={(e) => { if (isSubmitEnter(e)) void submit(); }}
  leadingIcon={Search}
/>

// lg の Button と同じ行に並べる質問欄
<div className="flex flex-col gap-2 sm:flex-row">
  <TextField id="search-query" label={t("nav.search")} labelHidden size="lg" leadingIcon={Search}
    value={query} onValueChange={setQuery} className="min-w-0 flex-1" />
  <Button size="lg" icon={Search} loading={busy}>{t("search.button")}</Button>
</div>

// 単位・件数などの後置
<TextField id="limit" label="取得件数上限" inputMode="numeric" value={limit} onValueChange={setLimit}
  trailing={<span className="px-3 text-sm text-fg-muted">件</span>} />
```

### TextField の props（追加分）

```ts
export type TextFieldSize = ControlSize; // "sm" | "md" | "lg"（#613）

export type TextFieldProps = {
  /** label を sr-only にする（検索欄だけ。フォームの入力欄では使わない）。 */
  labelHidden?: boolean;
  /** 高さ。sm 32px / md 36px（既定）/ lg 40px（--control-height-*）。タッチ端末は 44px。同じ行の Button と同じ size にする。 */
  size?: TextFieldSize;
  /** 幅（xs / sm / md / lg / full。既定は親の幅いっぱい）。sm 未満は全幅（#613）。 */
  width?: FieldWidth;
  /** 先頭の 16px のアイコン（lucide-react）。aria-hidden・pointer-events: none。 */
  leadingIcon?: LucideIcon;
  /** 末尾の任意の要素（単位・件数・ボタン）。実際の幅だけ文字の右の余白を空ける。 */
  trailing?: ReactNode;
  /** 値を消す。値があるときだけクリアボタンを出し、Escape でも消す。value を制御して使う。 */
  onClear?: () => void;
  /** クリアボタンの読み上げ名と Tooltip（翻訳済み）。onClear と一緒に渡す。 */
  clearLabel?: string;
  // …既存の id / label / helper / error / required / requiredLabel / className / inputClassName / onValueChange / ref
} & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "required" | "size">;
```

| 決めたこと | 理由 |
|---|---|
| 入力欄を常に `div.relative` で包み、先頭アイコンは入力欄の**後ろ**（DOM 上）に置いて `peer-disabled:` で色を変える | クリアボタンの出し入れで入力欄が作り直されない（フォーカスと IME の変換を失わない）。`peer` は前の兄弟にしか効かない |
| 後置スロットは枠線まで含めた右端（`inset-y-0 right-0`。`SecretField` の表示切替と同じ）。幅は `ResizeObserver` で測り、入力欄の `padding-right` にする。測る前（SSR・初回）は `pr-[var(--field-height)]`（四角のボタン 1 つ分） | 幅の決まらない要素でも文字と重ならない。スロットのボタン（クリア・`SearchableMultiSelect` の一覧の開閉）が入力欄と同じ高さ（md 36px・タッチ端末 44px）になる。以前の枠線の内側（`inset-y-px right-px`）では 2px 低かった（#1132） |
| スロットのボタンは `TEXT_FIELD_TRAILING_BUTTON_CLASS`（`h-full` の正方形・外側の角だけ `rounded-r-control`・`bg-clip-padding`） | 枠線は透明のまま地を枠線の内側だけに塗るので、ホバーの地が入力欄の枠線に重ならず、見た目は枠線の内側に収まる |
| クリアボタンは `aria-controls` で入力欄を指し、`mousedown` を止める。押したら `onClear()` の後に入力欄へ `focus()` | ボタンが消えてもフォーカスが body に落ちない。blur で確定する検索欄が消す前の値を確定しない |
| 強制カラーモードでは、スロットのボタンの輪郭の上・右・下が入力欄の枠線とちょうど重なり、左の区切りだけが増える（クリアの左に並ぶ一覧の開閉は、右を `Canvas` にする） | Button は強制カラーモードで輪郭を出すが、入力欄の枠線と二重の線にしない |
| `type="search"` の `::-webkit-search-cancel-button` / `::-webkit-search-decoration` を `appearance: none` | 共有のクリアと二重にしない（README §7 #33） |

- 純粋関数 `hasTextValue` / `shouldClearOnEscape` / `clearTextField` と class の組み立ては `packages/ui/tests/text-field-slots.test.tsx` が確かめます（パッケージのルートからは export しません）。実ブラウザは RAG の `e2e/feedback.spec.ts`（desktop / 375px の高さ・角丸・アイコン・クリア・Tab 順・Escape、強制カラーモードのタブ）。
- 検索欄の E2E は `getByRole("searchbox" | "textbox", { name })` で引きます。先頭アイコンは入力欄の親（`xpath=..`）の `[data-text-field-slot="leading"]`、後置は `[data-text-field-slot="trailing"]` です。

## TextField — 候補（`suggestions`、#547）

候補から選べて、候補に無い値もそのまま入力できる入力欄（editable combobox）。RAG の文書の分類（大・中・小分類）で、保存済みの値を候補に出して表記の揺れを防ぐために足しました。既存の props・id・aria は変えていません。

```tsx
<TextField
  id="document-classification-large_category"
  label={t("documents.classification.large_category")}
  value={value}
  onValueChange={setValue}
  suggestions={options?.large_categories}   // 翻訳しないデータの値。空・未指定なら普通の入力欄
/>
```

| 決めたこと | 理由 |
|---|---|
| 候補の一覧は独自の listbox ではなく、ネイティブの `<datalist>`（入力欄の `list`） | キー操作（↓ で開く・Enter で選ぶ）・読み上げ（role=combobox）・モバイルの候補表示・`color-scheme` によるダークテーマを OS / ブラウザに任せる。`type="date"` の日付選択と同じ扱いで、選んだ値は普通の入力と同じく `onChange` / `onValueChange` に届く |
| 候補があるときだけ `list` と `autoComplete="off"`（呼び出し側の `autoComplete` が優先） | ブラウザの入力履歴を候補に混ぜない。候補が無いときは従来と同じ入力欄 |
| `list` 属性は props で受け取らない（`TextFieldProps` から除く） | datalist の id は `useId` で作り、ほかの入力欄の候補と取り違えない |

- 候補の一覧の見た目（ポップアップ）はブラウザが描くため、トークンの色・角丸にはなりません（日付選択と同じ）。候補を絞り込む・複数選ぶ・候補に無い値を拒む入力は、この部品ではなく `SelectField` や `SearchableSelectField` / `SearchableMultiSelect`（#578）の型です。
- テストは `packages/ui/tests/text-field-slots.test.tsx`（datalist との結び付き・`autocomplete`）、実ブラウザは RAG の `e2e/structure-explainability.spec.ts`（文書詳細の分類、desktop / 375px、light / dark）。


## TextareaField — 新規（#584）

複数行の入力欄。3 製品が `<textarea>` を手書きしていて、地（`bg-surface` / `bg-surface-sunken`）・角丸（`rounded-md`）・余白・フォーカスの枠線・disabled の見た目（`opacity-50` など）・ラベルと必須とエラーの付け方・文字数の表示が画面ごとに違いました。`TextField` と同じ見た目と API の部品にそろえます。

```tsx
import { TextareaField } from "@engchina/production-ready-ui";

<TextareaField
  id="search-answer-profile-system-prompt"
  label={t("searchAnswerProfiles.field.systemPrompt")}
  helper={t("searchAnswerProfiles.field.systemPromptHelper")}
  value={prompt}
  onValueChange={setPrompt}
  rows={3}
/>

// 必須・エラー・文字数（上限があれば「12 / 1,000」。関数で翻訳済みの文言にできる）
<TextareaField id="faq-answer" label={t("faq.answer")} required error={errors.answer} maxLength={20000}
  showCount={(count) => t("faq.answerCount", { count })} value={answer} onValueChange={setAnswer} />

// SQL・JSON・プロンプトの雛形は等幅。読み取り専用のプレビューは resize="none"
<TextareaField id="preview" label={title} labelHidden readOnly monospace resize="none" value={json} />
```

| 決めたこと | 理由 |
|---|---|
| 枠線・角丸・地・フォーカス・disabled・read-only は `TextField` と同じ class（`fieldControlClass`）。上下の余白は `py-2`、行間は `leading-relaxed` | 1 行の入力欄と並べても同じ部品に見える。read-only は `bg-surface-sunken`（プレビュー）、disabled は `bg-surface-disabled`（`opacity` で薄めない） |
| 既定 `rows={3}`・縦だけ伸ばせる（`resize-y`）。`resize="none"` で固定 | 横に伸ばすと列の幅を崩す。高さは `rows` か `textareaClassName`（`min-h-*`）で決める |
| `monospace` は `--font-mono` の 12px | SQL・JSON・プロンプトの雛形を画面ごとに `font-mono text-xs` で書いていた |
| `surface="code"` は暗いコードの面（`data-surface="code"`）・等幅で、read-only でもコードの地のまま | 生成した SQL などの読み取り専用の表示。`bg-surface-sunken` はコードの面で定義し直されず、明るい地に明るい文字になるため |
| `showCount` は `maxLength` があれば「現在 / 上限」、無ければ「n 文字」。右下に `tabular-nums` で置き、補足と同じく `aria-describedby` で結ぶ（`aria-live` にしない） | 打鍵ごとに読み上げると入力の邪魔になる。欄に入ったときに上限と一緒に読める |
| 必須は `required`（`aria-required` と「必須」のタグ）、エラーは `error`（`aria-invalid`・枠線の色・欄の直下の `FieldError`） | `TextField` と同じ（README §4「必須の表示」、UX 契約 messaging.md §3.2.1） |
| 条件付きの必須は `requiredAnnouncedByControl={false}`（`aria-required` を付けず、`requiredLabel` のタグをラベルの一部として読ませる） | 「「違う」のとき必須」のように常に必須ではない欄で、条件を読み上げに残す（`FieldLabel` の同名の prop と同じ） |
| 自動で高さを伸ばす機能は持たない | ブラウザの対応（`field-sizing: content`）がそろうまで、`rows` と `resize-y` で足りる |

- adherence の lint が製品の JSX の `<textarea>` を検出します（`design-system/restricted-syntax`）。例外は元の文書の表を再現して編集するグリッドのセルなど、`TextareaField` で表せない所だけで、理由を添えて `eslint-disable-next-line` / `oxlint-disable-next-line` で局所的に除外します。
- 単体テストは `packages/ui/tests/textarea-field.test.tsx`、lint の規則は RAG の `src/design-system-adherence.test.ts`。

## Tabs — 変更（#374）

強制カラーモードで、選ばれていないタブの下線を `Canvas`（`forced-colors:border-b-[Canvas]`）、選んだタブの下線を `Highlight`（`forced-colors:aria-selected:border-b-[Highlight]`）にしました。props・id・aria・キー操作は変えていません。

- 強制カラーモードでは `border-color: transparent` も `CanvasText` に置き換わります（Chromium で確認）。system color を明示すればそのまま使われます。
- 単体テストは `packages/ui/tests/text-field-slots.test.tsx`（class）、実ブラウザは RAG の `e2e/feedback.spec.ts`（`page.emulateMedia({ forcedColors: "active" })`）。

---

## Tabs — 変更（#396）: ペインの中の見方の切り替え

ページより小さい単位（ペイン・カードの中）で同じ対象の見方を切り替えるときも `Tabs` + `TabPanel` を使います。枠の中にボタンを並べたセグメントコントロールは作りません（理由は README §4「`Tabs`」）。`TabItem` に `disabledReason` を足しました。ほかの props・id・aria・キー操作は変えていません。

```tsx
<div className="mb-2 flex flex-wrap items-center justify-between gap-2">
  <h3 className="text-sm font-semibold text-fg">{t("flow.preview")}</h3>
  {/* 選んだ見方に効く操作（ダウンロード等）は見出しの行に置く */}
</div>
<Tabs
  idPrefix="preview"                       // 同じ画面の別の Tabs と分ける
  ariaLabel={t("flow.preview")}
  className="mb-2 shrink-0"
  value={variant}
  onChange={(id) => setVariant(id === "prepared" ? "prepared" : "original")}
  items={[
    { id: "original", label: t("flow.preview.before") },
    {
      id: "prepared",
      label: t("flow.preview.after"),
      disabled: !hasPrepared,
      disabledReason: hasPrepared ? undefined : t("flow.preview.preparedUnavailable"),
    },
  ]}
/>
<TabPanel id={variant} value={variant} idPrefix="preview" className="flex min-h-0 flex-1 flex-col">
  <DocumentPreview … />
</TabPanel>
```

| 決めたこと | 理由 |
|---|---|
| 役割は `tablist` / `tab`（`aria-selected`）/ `tabpanel`。`radiogroup` / `aria-pressed` にしない | WAI-ARIA APG に segmented control のパターンは無い。見方の切り替えは Tabs パターン。`radiogroup` はフォームの値の選択、`aria-pressed` はオン / オフのトグルで、中身が入れ替わることを伝えない |
| 表示中のパネルが 1 つでも `TabPanel` で包む（`id` と `value` に選択中の id を渡す） | `aria-controls` の参照先と、パネルの名前（`aria-labelledby` = 選んだタブ）を持たせる。中身の要素は同じ位置のままなので、切り替えで作り直されない |
| `disabledReason` は無効のタブにだけ `title` として付ける | 無効のタブはフォーカスを受けず Tooltip を出せない。ホバーと読み上げ（説明）で理由を伝える。有効なタブには付けない |
| 入れ子（右ペインのタブの中の形式のタブ）でもよい。`idPrefix` を分ける | 入れ子の tablist は、外側の tablist の外（外側の `tabpanel` の中）にあるので、矢印キーが干渉しない |

- E2E: 1 画面に `tabpanel` が複数になる。`page.getByRole("tabpanel")` で引いていたテストは、ペイン（`getByTestId("document-inspector-pane")`）やパネルの名前（`{ name: "抽出エクスポート" }`）で絞る。
- 単体テストは `packages/ui/tests/components.test.tsx`（役割・選択・無効の理由・キー操作）、実ブラウザは RAG の `e2e/document-workspace-file-processing.spec.ts`（「処理前/処理後と抽出エクスポートの形式は共有の Tabs で…」）。

## Tabs — 変更（#542）: エラーのあるタブ

フォームを複数のタブに分けたとき（モデル設定のプライマリ接続 / セカンダリ接続）、選んでいないタブの欄のエラーを見落とさないように、`TabItem` に `invalid` を足しました。ほかの props・id・キー操作は変えていません。

```tsx
<Tabs
  idPrefix="enterprise-connection"
  ariaLabel="OCI Enterprise AI の接続"
  value={tab}
  onChange={setTab}
  items={[
    { id: "primary", label: "プライマリ接続" },
    // 保存で止めたときは、呼び出し側が最初のエラーのタブへ切り替えて、最初のエラーの欄へフォーカスする。
    { id: "secondary", label: "セカンダリ接続", badge: unsaved ? "未保存" : undefined, invalid: hasErrors },
  ]}
/>
```

```ts
export interface TabItem {
  // …
  /** タブの中に入力のエラーがある。ラベルの後ろに danger 色の CircleAlert、読み上げは invalidLabel。 */
  invalid?: boolean;
  /** invalid のときの読み上げの説明（翻訳済み）。既定 DEFAULT_TAB_INVALID_LABEL（「入力にエラーがあります」）。 */
  invalidLabel?: string;
}
export declare const DEFAULT_TAB_INVALID_LABEL: string;
```

| 決めたこと | 理由 |
|---|---|
| 印はラベル（とバッジ）の後ろの `CircleAlert`（14px、`text-danger-fg`、`aria-hidden`） | エラーは利用者の対応が要る状態なので状態色を使う。アイコンの形でも伝え、色だけに頼らない（WCAG 1.4.1）。選択中のタブの下線・文字色は変えない |
| 読み上げは `sr-only` の説明を `aria-describedby` で結ぶ（タブの名前は変えない） | タブの名前（「セカンダリ接続」）を保ったまま、フォーカスしたときに「入力にエラーがあります」と伝える。件数バッジと同じ結び方 |
| `data-invalid` を付ける | E2E・単体テストで状態を引ける（見た目の class に依存しない） |
| タブの切り替えとフォーカスは呼び出し側が行う | どの欄が最初のエラーかは画面が知っている。Tabs は表示と読み上げだけを持つ |

- 単体テストは `packages/ui/tests/components.test.tsx`（「Tabs のエラーの表示（#542）」）。実ブラウザは RAG の `e2e/model-settings-switch.spec.ts`（「接続はプライマリ接続とセカンダリ接続のタブで切り替え…」）。

## Disclosure — **新規** / DisclosureChevron — 変更（#397）

開閉できる領域の標準形です。ネイティブの `<details>` / `<summary>` を包み、見出しの行全体を押せる領域にし、開閉の状態を右端（`plain` は見出しの直後）の `DisclosureChevron` で示します。**製品で `<details>` / `<summary>` を手書きしないでください**（adherence の lint が JSX の `<details>` を検出します）。見た目と振る舞いの決定は README §4「`Disclosure`」。

```
┌───────────────────────────────────────────────┐  card（既定）
│ [icon] 見出し                    [meta]  ›    │  ← summary（行全体を押せる。hover で地が付く）
├───────────────────────────────────────────────┤  ← 開いているときだけ区切り線
│ 内容（p-3）                                    │
└───────────────────────────────────────────────┘
見出し ›                                             plain（枠なし。Chevron は見出しの直後）
```

```tsx
import { Disclosure } from "@engchina/production-ready-ui";
import { Wrench } from "lucide-react";

// 受控: 開閉を URL や作業状態に残すとき
<Disclosure
  summary={t("flow.inspector.details")}
  icon={Wrench}
  open={diagnosticsOpen}
  onOpenChange={setDiagnosticsOpen}
  data-testid="document-diagnostics"
  contentClassName="space-y-5"
>
  <SourceProfilePanel … />
</Disclosure>

// 非受控 + 件数（meta）+ 面の上に重ねる（sunken）
<Disclosure summary="構造化要素" meta={<span className="tnum text-xs text-fg-muted">{count}</span>} surface="sunken">…</Disclosure>

// 状態が変わったら開き直す（失敗したら開く）: key を状態で変え、defaultOpen を渡す
<Disclosure key={failed ? "failed" : "active"} defaultOpen={failed} tone="warning" summary={`警告 (${n})`}>…</Disclosure>

// 表のセルの中・回答の補足: plain + sm。summary に data-testid を付けるときは summaryProps
<Disclosure variant="plain" size="sm" summary="分析" summaryProps={{ "data-testid": "analysis-toggle" }}>…</Disclosure>
```

### Disclosure の props

```ts
export type DisclosureVariant = "card" | "plain";
export type DisclosureSurface = "surface" | "sunken";
export type DisclosureTone = "neutral" | "warning" | "danger";
export type DisclosureSize = "md" | "sm";

export interface DisclosureProps
  extends Omit<DetailsHTMLAttributes<HTMLDetailsElement>, "open" | "onToggle" | "title" | "children"> {
  summary: ReactNode;            // 見出し。操作できる要素（ボタン・リンク）を入れない
  icon?: LucideIcon;             // 先頭のアイコン（md 16px / sm 14px、--color-fg-muted）
  description?: ReactNode;       // 見出しの下の補足（card のみ、12px）
  meta?: ReactNode;              // 見出しと Chevron の間（件数・StatusBadge）
  open?: boolean;                // 受控。渡すときは onOpenChange も渡す
  defaultOpen?: boolean;         // 非受控の初期状態
  onOpenChange?: (open: boolean) => void;
  variant?: DisclosureVariant;   // 既定 "card"
  surface?: DisclosureSurface;   // card の地。既定 "surface"
  tone?: DisclosureTone;         // card の状態色。既定 "neutral"
  size?: DisclosureSize;         // 見出しの文字。md 14px / sm 12px
  summaryProps?: HTMLAttributes<HTMLElement>;  // data-testid など
  summaryClassName?: string;     // 見出しの局所的な調整（sticky・余白など）
  contentClassName?: string;     // 内容の領域。card の既定は border-t + p-3、plain は pt-2
  children?: ReactNode;
}
```

- ルートの `<details>` は `data-state="open" | "closed"`、Chevron は `data-state="expanded" | "collapsed"` を持ちます。E2E は `summary` を押して、`details` の `open` 属性か Chevron の `data-state` で確かめます（Chevron に独自の testid を付けない）。
- 見出しの高さは `--button-height-lg`（card、タッチ端末 44px）/ `--button-height-sm`（plain）。hover の地は `--color-surface-hover`（`tone` のときは文字色の 5%）。フォーカスは outline（card は枠の内側）。
- summary のクリックは部品が状態を切り替えます（ブラウザの切り替えを止める）。`summaryProps.onClick` で `preventDefault()` すると開閉しません。
- 内容は閉じている間もマウントされたままです（ページ内検索で見つかる）。重い内容を開いたときだけ取得するなら、`onOpenChange` で状態を持ち、取得の `enabled` に渡します。

### DisclosureChevron の props（変更）

```ts
export interface DisclosureChevronProps extends Omit<LucideProps, "aria-hidden"> {
  /** boolean（推奨）か、`group/disclosure` を付けた <details> に CSS で追従する "group"（入れ子に弱いので新規コードでは使わない）。 */
  expanded: boolean | "group";
}
```

- 折りたたみ = `-rotate-90`（右向き）、展開 = `rotate-0`（下向き）。`transition-transform duration-200 ease-out`、`motion-reduce:transition-none`。`aria-hidden` / `focusable="false"` で読み上げない（状態は `<details>` か `aria-expanded` が伝える）。
- **button + region の開閉**（`Disclosure` で表せないもの）: `aria-expanded` と `aria-controls` を付けたボタンの中に `<DisclosureChevron expanded={open} size={16} />` を置きます（`Button` なら子の末尾。`icon` / `trailingIcon` は回らないので使わない）。共有 `Sidebar` のセクション、`FormActionBar` / `ObjectActionBar` の「その他の操作」も同じ部品です。

## RowTitleButton — **新規**（#421）

一覧の行の先頭セル（または 375px のカード）に置く対象名のボタンです。仕様と判断の理由は README §4「`RowTitleButton`」。

```tsx
import { DataTable, RowTitleButton } from "@engchina/production-ready-ui";

<DataTable<Feedback>
  columns={[
    {
      key: "question",
      header: t("feedback.table.question"),
      rowHeader: true,
      render: (item) => (
        <RowTitleButton
          title={item.question}
          subtitle={<QuestionMeta item={item} />}
          current={item.id === selectedId}
          aria-label={t("feedback.list.selectNamed", { name: item.question })}
          onClick={() => onSelect(item.id)}
        />
      ),
    },
    // …
  ]}
  rows={items}
  getRowKey={(item) => item.id}
  selectedRowKey={selectedId}
  onRowClick={(item) => onSelect(item.id)}
/>

// 長い内容は行数で切り詰め、切り詰めたときだけ全文の Tooltip を出す
<RowTitleButton title={entry.content} maxLines={2} current={entry.id === selectedId} onClick={() => select(entry.id)} />

// 識別子の表示部品を題名に渡す（色は部品の既定の --color-fg にそろえる）
<RowTitleButton title={<DbObjectName value={name} size="xs" />} subtitle={<DbObjectCommentText id={commentId} comment={comment} />}
  aria-describedby={commentId} current={name === selectedName} onClick={() => onSelect(name)} />
```

### RowTitleButton の props

```ts
export interface RowTitleButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "title" | "children" | "type" | "aria-current"> {
  /** 対象名。文字列のほか、製品の表示部品も渡せる。 */
  title: React.ReactNode;
  /** 対象名の下の補足（12px、--color-fg-muted）。 */
  subtitle?: React.ReactNode;
  /** 詳細に表示中の対象か。aria-current="true" を付ける。 */
  current?: boolean;
  /** 切り詰める最大行数（1〜3）。既定は切り詰めずに折り返す。 */
  maxLines?: 1 | 2 | 3;
  /** 切り詰めたときの Tooltip の全文。title が文字列なら省略できる。 */
  fullTitle?: string;
  ref?: React.Ref<HTMLButtonElement>;
}
```

| 決めたこと | 理由 |
|---|---|
| `type="button"` の文字だけのボタン（共有 `Button` は使わない）。`aria-label`・`aria-describedby`・`data-*`・`disabled`・`ref` はそのまま渡す | 情報一覧の構造コントロールで、アクションボタンの枠・高さを持たせない。詳細を閉じたときのフォーカスの戻り先を `data-*` や `ref` で探せる |
| 選択の状態は `aria-current="true"` だけ。見た目は行（`DataTable` の `selectedRowKey`）やカードが持つ | 行とボタンの両方が同じ意味の「現在の項目」を持つ（行は背景と左バー、ボタンは Tab で届いたときの読み上げ）。`DataTable` は行の中のボタンのクリックで `onRowClick` を重ねて呼ばない |
| `maxLines` の切り詰めは `ResizeObserver` で `scrollHeight > clientHeight` を測り、切り詰めているときだけ `Tooltip`（`describe={false}`）を有効にする。文言は `rowTitleTooltipText`（先頭 120 文字 + 「…」） | 切り詰めていない題名に同じ文言の吹き出しを出さない。読み上げ名は全文なので説明として二重に結び付けない。長い全文の吹き出しで一覧を覆わない |
| class は `pr-touch-target relative`（タッチ端末の当たり判定）と、題名の `group-hover/row-title:underline` | Button の `--button-height-*` と同じく入力方式で判定し、見た目は変えない |

- 製品の置き換え（#421）: RAG（フィードバック・検索・回答プロファイルの管理・検索・回答プロファイルの用語 / ルール）、Agent（エージェント・承認・ツール・メモリ・MCP サーバー・Skill・Plugin・マーケットプレイス・Run）、NL2SQL（プロファイル・DB 管理のオブジェクト一覧・データ管理の対象の選択・フィードバック管理のエントリ）、system-settings（ユーザー・ロール・ロール権限。`SecurityIdentityRowTitleButton`）。
- 置き換えないもの: 一覧から別ページへ移るリンク（RAG のナレッジベース・ファイル一覧）、カード全体が 1 つのボタンの履歴（NL2SQL のフィードバック履歴）、listbox の選択肢（NL2SQL の DeepSec の対象）、チェックボックスのラベル。
- 単体テストは `packages/ui/tests/row-title-button.test.tsx`。実ブラウザは RAG `e2e/feedback.spec.ts`・Agent `e2e/entity-archetypes.spec.ts`・NL2SQL `tests/e2e/profile-archive-reset.spec.ts`（desktop / 375px、Tab・Enter・Space・`aria-current`・タッチ端末の当たり判定・切り詰めの Tooltip）。

---

## 必須の表示: RequiredBadge — 変更 / FieldLabel・FieldLegend・Fieldset — **新規**（#531）

3 製品と system-settings のフォームで、必須の欄の見せ方と読み上げを 1 通りにします。仕様と判断の理由は README §4「必須の表示」、画面の振る舞い（未入力のエラー・フォーカス）は UX 契約 `messaging.md` §3.2.1。

```tsx
import { FieldLabel, FieldLegend, Fieldset, SelectField, TextField } from "@engchina/production-ready-ui";

// 1 行の入力・選択・secret: required だけで「必須」のタグと aria-required が付く（requiredLabel の既定は「必須」）
<TextField id="profile-name" label={t("profiles.field.name")} required value={name} onValueChange={setName}
  error={nameError ?? undefined} />
<SelectField id="region" label={t("settings.oci.region")} required value={region} options={regions} onValueChange={setRegion} />
// 条件付きの必須だけ文言を上書きする（別の見た目を作らない）
<TextField id="compartment" label={t("settings.model.compartment")} required requiredLabel={t("settings.model.requiredInOci")} />

// textarea・ファイル選択・独自の入力: FieldLabel + 入力の aria-required（タグは読み上げない）
<FieldLabel htmlFor="direct-sql" label={t("directSql.field.sql")} required />
<textarea id="direct-sql" aria-required="true" aria-invalid={Boolean(sqlError)} aria-describedby={sqlError ? "direct-sql-error" : undefined} />
<FieldError id="direct-sql-error" message={sqlError} />

// aria-required を持てない独自の入力（グリッドの選択など）: タグを読み上げ対象に残す
<FieldLabel htmlFor="scope-grid" label={t("search.scope")} required requiredAnnouncedByControl={false} />

// チェックボックスの群・複数選択: Fieldset（role=group）。必須は legend の中のタグで伝える
<Fieldset id="user-roles" legend={t("security.users.roles")} required helper={t("security.users.rolesHelper")}
  error={rolesError}>
  {roles.map((role) => <label key={role.code}><input type="checkbox" … /> {role.name}</label>)}
</Fieldset>

// ラジオ: role="radiogroup" で aria-required を群に付ける（タグは読み上げない）
<Fieldset id="scope-mode" legend={t("deepsec.scope.mode")} role="radiogroup" required>…</Fieldset>

// 選択肢のレイアウトを自分で組む fieldset は FieldLegend だけを使ってよい
<fieldset><FieldLegend required>{t("deepsec.targets")}</FieldLegend>…</fieldset>
```

### props

```ts
/** 既定の文言「必須」。packages/ui が持ち、3 製品で同じ語にそろえる。 */
export const DEFAULT_REQUIRED_LABEL = "必須";

export function RequiredBadge(props: {
  /** 既定「必須」。 */
  label?: string;
  className?: string;
  /** 入力側の aria-required が必須を伝えるときは true（二重読み上げを避ける）。 */
  "aria-hidden"?: boolean;
}): JSX.Element;

type RequiredProps = {
  required?: boolean;
  /** 既定「必須」。条件付きの必須（「OCI 運用時必須」）だけ上書きする。 */
  requiredLabel?: string;
  /** 入力・群が aria-required で伝えるなら true（タグは aria-hidden）。FieldLabel の既定 true、FieldLegend の既定 false。 */
  requiredAnnouncedByControl?: boolean;
};

export function FieldLabel(props: {
  id?: string;
  htmlFor: string;
  label: ReactNode;
  className?: string;
  /** タグの後ろに置く要素。 */
  children?: ReactNode;
} & RequiredProps): JSX.Element;

export function FieldLegend(props: { id?: string; children: ReactNode; className?: string } & RequiredProps): JSX.Element;

export type FieldsetProps = {
  id?: string;
  legend: ReactNode;
  /** legend の直下の補足。fieldset の aria-describedby に結ぶ。 */
  helper?: ReactNode;
  /** 群の直下の FieldError（「〇〇を選択してください。」）。aria-describedby に結ぶ。 */
  error?: string | null;
  /** "radiogroup" のときだけ fieldset に aria-required / aria-invalid を付け、タグは読み上げない。 */
  role?: "radiogroup";
  className?: string;
  legendClassName?: string;
  children: ReactNode;
  required?: boolean;
  requiredLabel?: string;
};
```

| 決めたこと | 理由 |
|---|---|
| `requiredLabel` の既定を「必須」にし、`TextField` / `SelectField` / `SecretField` は `required` だけでタグを出す | 呼び出し側が毎回文言を渡す作りでは、渡し忘れた欄が見た目で必須と分からない（WCAG 3.3.2）。`packages/ui` は日本語の既定文言を持つ（「閉じる」「本文へスキップ」と同じ扱い） |
| `FieldLabel` / `FieldLegend` を `packages/ui` に置く（NL2SQL と system-settings が同じ実装を別々に持っていた） | TextField 以外の入力（textarea・ファイル選択・チェックボックスの群・ラジオ・独自の入力）でも、タグの位置・余白・読み上げの扱いを 1 か所で決める |
| `FieldLabel` のタグは既定で `aria-hidden`、`FieldLegend` のタグは既定で読み上げる | label に結ばれた入力は `aria-required` で「必須」と読まれるので、タグも読むと「必須、必須」になる。fieldset（role=group）は `aria-required` を持てない（WAI-ARIA 1.2）ので、legend の中の文字で伝える |
| `Fieldset` の `role="radiogroup"` では fieldset に `aria-required` を付け、タグを読み上げない | radiogroup は `aria-required` を持てるロール。群に入ったときに 1 回だけ伝える |
| 任意の欄には何も付けない。placeholder・ラベルの「(任意)」は lint で検出する | 必須だけを示せば、印の無い欄は任意と分かる（凡例が要らない）。placeholder は入力を始めると消える |

- 製品で `RequiredBadge` を直接ラベルに並べない（adherence の lint が検出する）。ラベルでない所（カードの見出しに付ける条件付きの必須など）に置く必要があるときだけ、理由を添えて局所的に lint を外す。
- 単体テストは `packages/ui/tests/required-field.test.tsx`（既定の文言・`aria-required`・タグの読み上げの扱い・任意の欄に何も付かないこと・`Fieldset` の `aria-describedby`）。lint の検出と許容の例は RAG `frontend/src/design-system-adherence.test.ts`。

---

## SearchField — **新規**（#535）

一覧の絞り込み（画面上の一覧・表を名前などで絞る）の検索欄。入力に合わせて絞り込み（debounce 300ms、Enter はすぐ）、IME の変換中は絞り込まず、消去と件数の読み上げを持ちます。検索ボタンは置きません。決めたことの表は README §4「`SearchField`」、どの検索に使うか（重い検索は明示実行）は UX 契約 [page-archetypes.md「一覧の絞り込みの検索」](../ux-contracts/page-archetypes.md#一覧の絞り込みの検索535)。

```tsx
import { ClearActionButton, EmptyState, SearchField } from "@engchina/production-ready-ui";

// サーバー側で絞り込む一覧（RAG の検索・回答プロファイル）。q は作業状態に保存した適用中の検索語。
const [view, setView] = useWorkspaceState("searchAnswerProfiles.view", INITIAL_VIEW, isView);
const query = useSearchAnswerProfiles({ q: view.q || undefined, limit, offset: view.offset });
// ↑ queryKey に q を入れ、placeholderData: keepPreviousData（古い応答で上書きしない・前の一覧を出したまま）

<SearchField
  id="search-answer-profile-search"
  label={t("searchAnswerProfiles.search.placeholder")}
  labelHidden
  value={view.q}
  onSearch={(next) => setView((current) => ({ ...current, q: next, offset: 0 }))} // 変わったら 1 ページ目
  clearLabel={t("common.clearSearch")}
  resultCountLabel={query.data ? t("common.searchResultCount", { count: query.data.total }) : ""}
  placeholder={t("searchAnswerProfiles.search.placeholder")}
  className="w-full sm:w-64"
/>

// 0 件: 空の状態と「検索語をクリア」
{view.q ? (
  <EmptyState
    title={t("searchAnswerProfiles.search.noResultsTitle")}
    hint={t("searchAnswerProfiles.search.noResultsHint")}
    action={<ClearActionButton label={t("common.clearSearch")} onClick={() => applySearch("")} />}
  />
) : null}

// 所有者名のように、入力中も大文字で見せ、確定した値も大文字にする（NL2SQL の DbOwnerPrefixFilterField）
<SearchField id="owner" label="所有者" value={owner} onSearch={setOwner} clearLabel="入力をクリア"
  formatInput={(v) => v.toUpperCase()} normalize={(v) => v.trim().toUpperCase()} />
```

### SearchField の props

```ts
export const SEARCH_FIELD_DEBOUNCE_MS = 300;

export type SearchFieldProps = Omit<
  TextFieldProps,
  "type" | "value" | "defaultValue" | "onChange" | "onValueChange" | "onClear" | "leadingIcon" | "required" | "requiredLabel"
> & {
  /** 適用中の検索語（作業状態・URL に保存している値）。外から変わったときだけ入力欄を合わせる。 */
  value: string;
  /** 確定した検索語（正規化済み）。入力が止まって debounceMs・Enter・消去・入力欄が外れるときに呼ぶ。IME の変換中は呼ばない。前回と同じなら呼ばない。 */
  onSearch: (value: string) => void;
  /** 消去ボタンの読み上げ名と Tooltip（翻訳済み）。 */
  clearLabel: string;
  /** 既定 300。0 なら確定した入力のたびにすぐ呼ぶ。 */
  debounceMs?: number;
  /** onSearch に渡す前の正規化（既定は trim）。 */
  normalize?: (value: string) => string;
  /** 入力中の文字の見せ方（例: 大文字）。IME の変換中は適用しない。 */
  formatInput?: (value: string) => string;
  /** 件数の文言。検索語があるときだけ role="status"（aria-live="polite"、sr-only）で読み上げる。 */
  resultCountLabel?: string;
  /** 入力中の文字（下書き）が変わったとき。絞り込みには onSearch を使う。 */
  onDraftChange?: (draft: string) => void;
};

// IME 対応の Enter の判定（重い検索の明示実行・条件フォーム・追加の入力欄で使う）
export function isImeComposing(event: KeyboardEventLike): boolean; // isComposing または keyCode 229（Safari）
export function isSubmitEnter(event: KeyboardEventLike): boolean;  // key === "Enter" かつ変換中でない
```

| 決めたこと | 理由 |
|---|---|
| 入力中の文字（下書き）は部品が持ち、`value` は適用中の値だけにする。最後に `onSearch` へ渡した値を覚え、`value` がそれと違う値に変わったときだけ外からの変更として入力欄を合わせる | 親が `trim` した値・大文字にした値を返しても、入力中の空白・文字を書き換えない |
| debounce のタイマーは部品の中の 1 つ。親で `useDebouncedValue` などを重ねない（NL2SQL の 5 ページの 250ms を外した） | 反映が 550ms に遅れない。debounce と IME の判定を 1 か所にする |
| `compositionstart` で待っている分を取り消す。`compositionend` で確定した値を予約する（ブラウザにより確定後の `input` が来ないため） | 変換の途中の読みで絞り込まない。確定した値を取りこぼさない |
| Enter は `preventDefault` して囲む form を送信しない。変換を確定する Enter は既定の動作も止めない | 一覧の絞り込みは form の送信ではない。IME の確定を妨げない |
| 入力欄が外れるときは待っている分を確定する。部品の外の状態（作業状態）が同じ画面に残るとき、消した・入力した検索語が戻らない。画面ごと外れる（別のページへ移る）ときは親の状態も消えるので、残したい e2e は Enter で確定してから移る | 一覧 ⇄ 作成の切り替えで、消したはずの検索語が一覧に戻っていた |

- 単体テストは `packages/ui/tests/search-field.test.tsx`（debounce・Enter・trim・正規化・`formatInput`・IME の `compositionstart` 〜 `compositionend` と確定の Enter・消去・外からの変更・外れるときの確定・件数の読み上げ。fake timers）。
- 実ブラウザは RAG `e2e/list-search.spec.ts`（検索・回答プロファイル・ナレッジベース。ボタンなし・入力に合わせた問い合わせ・IME・0 件の「検索語をクリア」、desktop / 375px、ライト / ダーク）、Agent `e2e/list-search.spec.ts`（メモリ）、NL2SQL `tests/e2e/nl2sql-workflows.spec.ts`（学習候補・アプリ内フィードバック）。IME は `compositionstart` → `isComposing` の `input` → `compositionend` の DOM event を出して確かめる（Playwright の keyboard は IME を通さない）。
- 製品の置き換え: RAG（ナレッジベース・検索・回答プロファイル・文書・フィードバック・ナレッジベース詳細の追加する文書）、NL2SQL（`DbManagementSearchField` / `DbOwnerPrefixFilterField` を使う全一覧・DB 管理のオブジェクト一覧・スキーマ参照・アプリ内フィードバック・学習候補）、Agent（メモリ）、system-settings（`SecuritySearchField`: ユーザー・ロール・権限管理・権限の対象。NL2SQL の Deep Data Security も使う）。

## SearchableSelectField / SearchableMultiSelect — **新規**（#578）

数十〜数百件の選択肢から検索して選ぶ部品（単一・複数）。十数件までの固定の選択肢は `SelectField`、大量の候補から一覧で見比べて選ぶもの（表・ビューの選択など）は別の一覧型の部品にする。見た目の変更は README §7 の 50。

```tsx
import { SearchableMultiSelect, SearchableSelectField } from "@engchina/production-ready-ui";

// 複数選択: 検索欄（combobox）＋ 候補の一覧（開いている間だけ）＋ 選択済みの chip
<SearchableMultiSelect
  id="kb-scope"
  label="参照するナレッジベース"
  required
  options={options}                 // { value, label, description?, meta?, badge?, searchText?, hideable? }[]
  value={selectedIds}
  onValueChange={setSelectedIds}
  selectedOptions={chips}           // 候補のページに無い選択済みの名前・状態（任意）
  onQueryChange={setQ}              // 確定した検索語（300ms・Enter・IME の確定後）
  remote={many ? { total, hasMore, loadingMore, searching, onLoadMore } : undefined}
  labels={{ searchPlaceholder: "ナレッジベースを検索して追加…" /* ほか */ }}
/>

// 単一選択: ボタン（選択中の名前を折り返して全体を出す）→ 検索欄と候補の一覧を重ねて開く
<SearchableSelectField
  id="kb-filter"
  label="ナレッジベース"
  value={value}
  options={options}
  selectedOption={current}          // 候補のページに無い選択中の名前（任意）
  onValueChange={setValue}
  onQueryChange={setQ}
  remote={many ? { total, hasMore, loadingMore, searching, onLoadMore } : undefined}
/>
```

| 決めたこと | 理由 |
|---|---|
| 検索欄は `SearchField`（debounce 300ms・Enter はすぐ・IME の変換中は絞らない・× と Esc で消す）を role=combobox で使う。親で遅延させない | #535 の規則を 1 か所で守る。変換の途中の読みで問い合わせない |
| `remote` を渡さなければ `options` を画面側で絞る（全角半角・大文字小文字を区別しない部分一致、空白区切りはすべて含む）。渡すと絞らず、`onQueryChange` の検索語で呼び出し側が問い合わせる | 件数の少ないときは 1 文字ごとに問い合わせない。多いときは全件を読まない（切り替えの基準は製品が決める。RAG の KB は 201 件以上） |
| ↑↓ で強調（`aria-activedescendant`）、Enter で選ぶ。IME の変換中の矢印・Enter は候補の操作に使わない。複数選択は選んでも閉じず、「完了」・Esc で閉じて検索欄へ戻る。閉じた後の Esc で検索語を消す | APG の Combobox。続けて複数を選べる。Esc 1 回で入力まで消えない |
| 単一選択の開いた層は `role="dialog"`（非モーダル）。開くと検索欄へ、選ぶ・Esc・Tab で閉じてボタンへ戻る（Shift+Tab はボタンに止まる）。ボタンの名前はラベルと選択中の名前（`aria-labelledby`） | Portal の層に Tab で入れない問題を避け、フォーカスの行き先を 1 つにする。読み上げで今の値が分かる |
| 続きのページは、複数選択は「さらに表示」、単一選択は一覧の下端までのスクロールか最後の候補からの ↓ | 単一選択の層の中に Tab で届くボタンを置かない |
| 選択済みは chip（`<ul aria-label="選択中の…">`）で、名前を省略せず折り返す。`badge` で状態（アーカイブ済み・見つかりません）を文字で添える | 長い名前を最後まで読める。色だけに頼らない |
| 単一選択の一覧の高さは一覧側（`bounded-scroll-area`）で決め、層全体は実寸で測る | 層に max-height を掛けると実寸の測り直しで高さが揺れる |

- 単体テストは `packages/ui/tests/searchable-select.test.tsx`（300 件の画面側の絞り込み・`remote`・↑↓/Enter/Esc/Tab・IME の `compositionstart`〜`compositionend` と確定の Enter・chip・hideable・件数の読み上げ・ボタンの名前）。
- 実ブラウザは RAG `e2e/knowledge-base-searchable-select.spec.ts`（モックで 300 件と 120 件。評価・文書インデックス・アップロード・検索・回答プロファイル、desktop / 375px、ライト / ダーク）。
- 製品の置き換え: RAG（文書インデックスの絞り込み、アップロードの登録先、検索・回答プロファイルの参照 KB、品質評価、文書詳細の所属先、RAG 検索・チャットの対象の検索・回答プロファイル（#635 で単一選択の `SearchableSelectField` に統一））。RAG 固有の `MultiSelectCombobox` は削除した。
- `leadingIcon`（任意。#635）: 単一選択のボタンの先頭に 16px のアイコン（読み上げない）を出す。検索して選ぶ欄であることを開く前から見せたいときに `leadingIcon={Search}` を渡す（RAG の対象の検索・回答プロファイル）。

---

## ApiErrorBanner / ApiErrorState / presentApiError — **新規**（#900 / #906）

API の失敗を、**利用者向けの要約（何が起きたか）・次の操作・「詳細」（技術的な情報）**に分けて出す部品と関数（UX 契約 messaging.md §10.3.1）。NL2SQL の #900 の仕組みを platform に移し、3 製品で使う。

```tsx
// 失敗の面（Banner）。要約・次の操作・開いた「詳細」（要求・待ち時間の上限・エラー種別・元の文・HTTP ステータス・request ID）
<ApiErrorBanner error={query.error} fallback={t("…loadError")} testId="…-error" />

// 領域の取得の失敗（ErrorState + 「詳細」。再試行付き）
<ApiErrorState error={query.error} fallback={t("…loadError")} onRetry={() => void query.refetch()} retryLabel={t("common.retry")} />

// 1 つの文しか出せない所（Toast・FormStatus・SaveErrorBanner）
toast.error(apiErrorMessage(error, t("…failed")));
```

| 関数・型 | 役割 |
|---|---|
| `ApiTransportError` / `toApiTransportError(cause, request)` | fetch・本文の読み取りが投げた timeout（`TimeoutError`）・通信断（`TypeError`）を、日本語の要約 + 次の操作の例外にする。英語の元の文は `causeMessage`。`AbortError` は `null`（変換しない） |
| `transportErrorOf(error)` | `ApiTransportError` か、製品の `ApiError` が `cause` に包んだものを取り出す（RAG） |
| `ApiErrorPresentable` / `httpApiErrorPresentation` | 製品の `ApiError` が `toApiErrorPresentation()` を実装し、backend の文を要約に、HTTP ステータス・エラーコード・request ID を詳細に分ける |
| `presentApiError(error, fallback)` | 要約・次の操作・詳細を返す。組み込みの例外（英語の文）は既定の文にし、元の文は詳細へ |
| `apiErrorMessage(error, fallback)` | 1 つの文。timeout・通信断は要約 + 次の操作、組み込みの例外は既定の文、それ以外は `message` のまま |

- 既定の文言（`DEFAULT_API_TRANSPORT_MESSAGES` / `DEFAULT_API_ERROR_DETAIL_LABELS`）は日本語。`ApiErrorDetailList` は「詳細」の折りたたみ（`Disclosure`）だけを出す。`ErrorState` は `details` で同じ折りたたみを本文の下に置ける。
- 単体テストは `packages/ui/tests/api-error.test.tsx`。製品の確認は各製品の API のラッパーのテストと e2e（`route.abort` で通信断）。

---

## SaveErrorBanner — **新規**（#585）

ヘッダー（`PageHeader`）に保存がある全画面のエディタで、**欄に結び付かない保存の失敗**を 1 か所に出す部品。UX 契約 messaging.md §3.3.1 の実装で、`PageBody` の最初の子に置く。欄に結び付く失敗は欄の直下（`FieldError`）に出し、この部品にも Toast にも重ねない。

```tsx
<PageBody wide>
  <SaveErrorBanner
    message={mutation.isError ? (mutation.error instanceof ApiError ? mutation.error.message : t("…error.save")) : null}
    attemptKey={mutation.submittedAt}
    testId="search-answer-profile-save-error"
  />
  {/* 対象の状態の警告 Banner・本文の節 */}
</PageBody>
```

### SaveErrorBanner の props

```ts
type SaveErrorBannerProps = {
  /** 失敗の文言（原因 + 次の行動）。空・null なら何も描かない。 */
  message?: string | null;
  /** 保存を試みるたびに変わる値（mutation.submittedAt など）。同じ文言の失敗でも画面に入れ直す。 */
  attemptKey?: string | number;
  title?: string;
  testId?: string;
  className?: string;
};
```

| 決めたこと | 理由 |
|---|---|
| 見た目は danger の `Banner`（アイコン付き・`role="alert"`）。閉じる × は付けない | 状態を色だけで示さない。次の保存まで残し、失敗を消して保存し直したように見せない |
| 失敗が出たら `scrollIntoView({ block: "center" })` で画面に入れる。フォーカスは動かさない | lg 以上のヘッダーは sticky で、長いフォームを下までスクロールしてから保存すると、本文の先頭の Banner は画面の外にある。中央へ寄せると本文の先頭の Banner はページの先頭まで戻り、sticky のヘッダーに隠れない |
| Toast・フォームの下の `FormStatus` と併用しない | 同じ失敗が 2 か所に出ていた（検索・回答プロファイルなど）。フォームの下はヘッダーの保存ボタンから遠く、気づけない |

- 単体テストは `packages/ui/tests/save-error-banner.test.tsx`（空のときは描かない・`role="alert"`・失敗と再試行のときだけ画面に入れる）。
- 使う画面: RAG（検索・回答プロファイル・ナレッジベースのエディタ）、Agent（Agent・Skill・MCP 接続・プラグインの導入・マーケットプレイスの追加）。NL2SQL の業務プロファイルは保存ボタンがフォームの中（確認語の欄と並ぶ）なので、ボタンの直下の `FormStatus`（§3.3）。

---

## ListToolbar / ListPicker / LoadMoreFooter — **新規**（#600）

一覧の上のツールバー（検索欄の位置）と、数千〜数万件の候補から一覧で複数を選ぶ部品。数十〜数百件を選択欄で選ぶものは `SearchableSelectField` / `SearchableMultiSelect`（#578）。規則は UX 契約 page-archetypes.md「一覧のツールバー」「大量の候補から選ぶ」、見た目の変更は README §7 の 53。

```tsx
import { ListPicker, ListToolbar, SearchField, FormActionBar } from "@engchina/production-ready-ui";

// 一覧のツールバー: 左に検索（先頭）→ 絞り込み、右に件数 → 一覧への操作。検索欄に幅の class を付けない。
<ListToolbar
  search={<SearchField id="kb-docs-search" label="所属文書を検索" labelHidden value={q} onSearch={setQ} clearLabel="検索語をクリア" />}
  filters={<SelectField … />}
  summary="25 件"
  actions={<Button variant="secondary" icon={FilePlus2} aria-expanded={open}>文書を追加</Button>}
/>

// 大量の候補から選ぶ: サーバーの検索（q）と追加読み込み（total / hasMore / loadingMore / onLoadMore は #578 の remote と同じ名前）
<ListPicker
  id="kb-add-documents"
  title="追加する文書を選ぶ"
  label="追加する文書の候補"            // listbox の名前
  items={items}                          // { key, label, textValue, description?, meta?, disabled?, disabledReason?, groupKey? }[]
  groups={groups}                        // 任意: { key, label, textValue, countLabel?, onSelectAll?, onClearAll?, … }[]
  selectedKeys={selectedKeys}            // ReadonlySet<string>
  onToggle={(item, selected) => …}
  onSelectMany={(items) => …}            // 任意:「表示中をすべて選択」
  onClearSelection={() => …}             // 任意:「選択をすべて解除」
  selectedItems={selectedItems}          // 任意:「選択中だけ表示（K）」
  search={{ label: "追加する文書を検索", value: q, onSearch: setQ }}
  total={total} hasMore={hasMore} loadingMore={loadingMore} loadMoreError={error} onLoadMore={loadMore}
  loading={isPending} refreshing={isPlaceholderData}
  disabled={saving}                      // 任意: 保存中は切り替えない（<fieldset disabled> の中も同じ）
  fixedHeight                            // 任意: 並べた 2 つの一覧の高さをそろえる
  labels={…}                             // 翻訳済みの文言（既定は日本語。DEFAULT_LIST_PICKER_LABELS）
  actions={<FormActionBar primaryActions={[{ id: "add", label: "選択した 3 件を追加", icon: FilePlus2 }]} … />}
/>
```

| 決めたこと | 理由 |
|---|---|
| 候補の一覧は listbox（`aria-multiselectable`・`aria-checked`）。Tab で listbox に入り、↑↓ / Home / End / PageUp / PageDown で移り、Space（Enter。IME の変換中は無視）で切り替える。グループの端の ↑↓ で隣のグループの listbox へフォーカスを移す | APG の multi-select listbox。候補の数だけ Tab を押させない。仮想スクロールで外れたグループにも矢印で届く |
| グループは見出し（件数・「すべて選択」「選択をすべて解除」。非同期でよく、実行中は同じピッカーの一括操作を止める）と listbox を分ける | listbox の子は選択肢だけ（ボタンを入れない） |
| 行の名前は `aria-labelledby`、補足・選べない理由は `aria-describedby` | Playwright の `getByLabel` / `getByRole("option", { name })`、読み上げで名前が短く伝わる |
| 100 行を超えたら見えている行と、キーボードの位置の行だけを描く（行の高さは描いた行から測る。`lib/list-window.ts`） | 数千件でも重くならない。スクロールしてもフォーカスが body に落ちない |
| 検索語・「選択中だけ表示」を変えたら一覧の先頭へ戻す。「選択中だけ表示」は `selectedItems` を検索語で画面側で絞る | 読み込みの範囲の外にある選択も確かめられる |
| `LoadMoreFooter` は失敗中は「さらに読み込む」を出さず、Banner の「再試行」だけにする | 同じ処理のスピナー・ボタンを 1 つにする（messaging §3.7） |

- 単体テストは `packages/ui/tests/list-picker.test.tsx`（読み上げの属性・↑↓ / Home / End / Space / Enter・IME の確定の Enter・グループ間の移動・3,000 件の仮想スクロール・検索欄の IME と件数の読み上げ・0 件・追加読み込みと再試行・一括選択と「選択中だけ表示」・無効・`list-window` の計算）。
- 実ブラウザは RAG `e2e/knowledge-bases.spec.ts`（モックで 3,000 件から検索して複数を追加、desktop / 375px、ライト / ダーク、キーボード。所属文書の検索とページング）と NL2SQL `tests/e2e/profile-allowed-objects.spec.ts`（許可する表・ビュー）。
- 製品の置き換え: RAG（ナレッジベースの「文書を追加」、所属文書・ナレッジベース・検索・回答プロファイル・文書の一覧のツールバー）、NL2SQL（業務プロファイルの許可する表・ビュー、`DbObjectSelectorFooter`）、system-settings（権限管理の「利用できる対象」。`RolePermissionTargetOption` は削除）。

## 操作部品の高さと幅: `size` / `width` / `FieldActionRow` / `fieldControlClassName` — **新規**（#613）

入力欄・選択欄・ボタンの高さと、入力欄・選択欄の幅を部品の prop で決めます（規則は README §4「操作部品の高さと幅」）。製品は `h-*` / `min-h-*` / `w-*` を書かず、`touchTarget` を使いません（adherence の lint が検出する）。

```tsx
import { FieldActionRow, SelectField, TextField, TextareaField, fieldControlClassName } from "@engchina/production-ready-ui";

// 高さ: 同じ行の部品に同じ size（既定 md）。幅: 値の長さで選ぶ（grid のセルに置く欄は指定しない）
<SelectField id="retention" label="保存期間" value={v} options={o} onValueChange={set} size="lg" width="md" />
<Button size="lg" icon={Save}>保存期間を保存</Button>

// 入力欄と、その値への操作（送信・実行・取得）の行。操作は入力欄の下端にそろい、375px では下に全幅
<FieldActionRow actions={<RunStopButton size="lg" … />}>
  <TextareaField id="chat-composer" label="メッセージ" labelHidden rows={2} … />
</FieldActionRow>

// ネイティブの select / input を例外として残す画面（<optgroup> など。#631 で理由付きの lint の除外が要る）は、同じ見た目・高さ・幅のクラスを使う
<select className={fieldControlClassName({ size: "lg", width: "sm" })}>…</select>
```

```ts
export type ControlSize = "sm" | "md" | "lg";                 // 32 / 36 / 40px。タッチ端末は 44px
export type FieldWidth = "xs" | "sm" | "md" | "lg" | "full";  // 8 / 12 / 20 / 28rem / 100%。sm 未満は全幅

// TextField / SearchField / SecretField / SelectField / SearchableSelectField に追加
size?: ControlSize;   // 既定 md（SearchableSelectField は最小の高さ。長い名前は折り返して高くなる）
width?: FieldWidth;   // 既定なし（親の幅いっぱい）。欄の外枠（ラベル・補足・エラーを含む）に付く

export function FieldActionRow(props: {
  children: ReactNode;   // 入力欄 1 つ（行の残りを埋める）
  actions: ReactNode;    // 操作（入力欄と同じ size の Button）。null / false なら操作の列を描かない（#631）
  footer?: ReactNode;    // 補足・エラー（欄の helper / error の代わり）
  className?: string;
  "data-testid"?: string;
}): JSX.Element;

export function fieldControlClassName(options?: { size?: ControlSize; width?: FieldWidth; className?: string }): string;
export const CONTROL_HEIGHT_CLASS: Record<ControlSize, string>;      // h-[var(--control-height-*)]
export const CONTROL_MIN_HEIGHT_CLASS: Record<ControlSize, string>;  // min-h-[var(--control-height-*)]
export const FIELD_WIDTH_CLASS: Record<FieldWidth, string>;          // w-full sm:w-[var(--field-width-*)] sm:max-w-full
export function fieldWidthClass(width?: FieldWidth): string | undefined;
```

| 決めたこと | 理由 |
|---|---|
| 高さのトークンを `--control-height-sm` / `md` / `lg`（32 / 36 / 40px）に一本化し、`--button-height-*` と `--field-height` はその別名にした | ボタンと入力欄が同じ値を参照するので、同じ `size` なら必ず同じ高さになる |
| タッチ端末（`pointer: coarse`）では 3 段とも 44px。**入力欄・選択欄も 44px**（以前は Button だけ） | タッチ端末で入力欄 36px・ボタン 44px とずれていた。Apple HIG 44pt / WCAG 2.5.5。画面幅ではなく入力方式で決める（マウスで狭いウィンドウを使うときは密度を保つ） |
| `touchTarget`（マウス環境でも 44px）は製品で使わない。`TextField` の `touchTarget` は削除 | 「入力欄の横の操作は 44px」の規則で、同じ画面の入力欄 36px と 44px が混ざっていた |
| 幅は欄の外枠に付け、sm 未満は `w-full`、sm 以上は段の幅と `max-w-full` | 補足・エラーの文も欄の幅で折り返す。狭い親の中で親より広くならない。375px では全幅（片手で押しやすい） |
| `FieldActionRow` は `items-end`。欄の helper / error は `footer` に出す | 上に見えるラベルがある欄でも、複数行の入力欄でも、ボタンの下端が入力欄の下端に合う。欄の下に文があると下端でそろえた操作が文の下端に合ってしまう |
| `ClearActionButton` の `matchButtonHeight` を削除。既定は `sm`（空の状態の「検索語をクリア」）で、行に置くときは行と同じ `size` を渡す | 以前はマウス環境でも 44px が既定で、`matchButtonHeight` を付け忘れた行がずれた |
| `fieldControlClassName` は TextField と同じ見た目（枠線 `--color-border-control`・`--radius-control`・地・フォーカス・disabled・read-only）に、高さ・幅・`aria-invalid` の枠線を足す | ネイティブの `<select>` / `<input>` を残す画面も、手書きの `h-10` / `h-11` / `min-h-[44px]`（35 / 38.5 / 44px）をやめて同じ段にする |

- 単体テストは `packages/ui/tests/control-size.test.tsx`（段のクラス・各部品の `size` / `width`・`fieldControlClassName`・`FieldActionRow`）と `tokens-css.test.ts`（トークンと `pointer: coarse` の 44px）。
- 実ブラウザの高さは、e2e の helper `expectedControlHeight(page, size)`（RAG `e2e/_helpers.ts`、NL2SQL `tests/e2e/_helpers/control-height.ts`）で入力方式から期待値を出して確かめる（タッチ端末 44px、それ以外は 32 / 36 / 40px）。

---

## SideSheet — **新規**（#664）

画面の中の補助的な一覧・詳細（RAG のチャットの会話の履歴など）を、狭い画面で本文の上に重ねて出すモーダルの side sheet です。振る舞いの表は README §4「`SideSheet`」。広い画面では製品が同じ中身を本文の横にインラインで置き、狭い画面だけ `SideSheet` で開きます（どちらか一方だけを描く）。

```tsx
import { Button, SideSheet } from "@engchina/production-ready-ui";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

const inline = useMediaQuery("(min-width: 1024px)"); // 製品が決める
const [sheetOpen, setSheetOpen] = useState(false);
const toggleRef = useRef<HTMLButtonElement>(null);
const historyId = useId();

<Button ref={toggleRef} variant="ghost" size="sm" iconOnly
  icon={open ? PanelLeftClose : PanelLeftOpen} aria-label={t("chat.sessions.title")}
  aria-expanded={open} aria-controls={historyId} onClick={toggle} />

{inline ? (
  <aside id={historyId} className={open ? "flex" : "hidden"}>{history}</aside>
) : (
  <SideSheet open={sheetOpen} onClose={() => setSheetOpen(false)}
    title={t("chat.sessions.title")} closeLabel={t("chat.sessions.close")}
    id={historyId} returnFocusRef={toggleRef} data-testid="chat-history">
    {history}   {/* 項目を選んだら製品が setSheetOpen(false) */}
  </SideSheet>
)}
```

### SideSheet の props

```ts
export interface SideSheetProps {
  open: boolean;
  /** 閉じる要求（閉じるボタン・Escape・scrim のタップ）。製品が open を false にする。 */
  onClose: () => void;
  /** 見出しとダイアログの名前（翻訳済み）。 */
  title: string;
  /** 閉じるボタンの名前（翻訳済み。Tooltip にも出る）。 */
  closeLabel: string;
  /** 出す側（既定は左）。 */
  side?: "left" | "right";
  /** 幅（#1154）。default は 22rem、wide は sm 以上 64rem（画面幅 − 3.5rem まで）・sm 未満は全画面。 */
  size?: "default" | "wide";
  /** シートの要素の id（開くボタンの aria-controls に渡す）。 */
  id?: string;
  /** 閉じたときにフォーカスを戻す先。省略時は開く前にフォーカスがあった要素。 */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
  /** 見出しの行の右（閉じるボタンの左）に置く操作。 */
  headerActions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** 本文（flex の縦並び・中でスクロール・p-3）への追加のクラス。 */
  bodyClassName?: string;
  /** シートの data-testid。scrim は `<testId>-scrim`。 */
  "data-testid"?: string;
}
```

- 閉じている間も描いたまま（`inert`・`data-state="closed"`）。開いている間は `data-state="open"`。
- キー操作はナビのドロワーと同じ helper（`focusableIn` / `navDrawerKeyAction`）。閉じるボタンの Tooltip が出ている間の 1 回目の Escape は吹き出しだけを閉じる（README §4「`Tooltip`」）。
- 単体テストは `packages/ui/tests/side-sheet.test.tsx`、実ブラウザは RAG の `e2e/chat.spec.ts`（375px の開閉・Esc・外側・フォーカスの戻り）。

---

## RunStopButton — **新規**（#805。#413 で RAG に作った部品を移した）

その場で結果を待つ操作（検索・チャットの送信・検索テスト）の「実行」と「停止」を 1 つのボタンで出す部品。規則は UX 契約 buttons.md §3.1「その場の実行と停止」。

```tsx
import { FieldActionRow, RunStopButton, TextareaField, isSubmitEnter } from "@engchina/production-ready-ui";
import { SendHorizontal } from "lucide-react";

<FieldActionRow
  actions={
    <RunStopButton
      running={sending}                 // true の間は同じボタンが「停止」
      onRun={send}
      onStop={stop}                     // RAG: AbortController で止める / Agent: Run の中止の API
      runLabel={t("chat.send")}         // 翻訳済み
      stopLabel={t("chat.stop")}
      runIcon={SendHorizontal}
      runDisabled={!draft.trim()}       // aria-disabled（フォーカスを受ける）。停止には効かない
      size="lg"                         // 並べる入力欄と同じ size（既定 lg）
      testId="chat-send"
    />
  }
>
  <TextareaField
    id="chat-composer" label={t("chat.composer.label")} labelHidden rows={2} value={draft}
    onKeyDown={(event) => {
      // 入力欄の Enter は送信だけ（実行中の Enter で停止しない）。IME の確定の Enter では送らない。
      if (isSubmitEnter(event) && !event.shiftKey) { event.preventDefault(); send(); }
    }}
    …
  />
</FieldActionRow>
```

### RunStopButton の props

```ts
export interface RunStopButtonProps {
  running: boolean;
  onRun: () => void;
  onStop: () => void;
  runLabel: string;            // 翻訳済み
  stopLabel: string;           // 翻訳済み
  runIcon: LucideIcon;         // 停止のアイコンは Square に固定
  runDisabled?: boolean;       // 実行できない間。aria-disabled（ネイティブの disabled にしない）
  size?: "sm" | "md" | "lg";   // 既定 lg
  className?: string;
  testId?: string;             // data-testid。data-state は "idle" / "running"
}

// DOM に依存しない判定（単体テスト用にも export）
export function runStopClickAction(options: { running: boolean; runDisabled?: boolean; clickCount: number }): "run" | "stop" | "ignore";
export function isRepeatedActivationKey(event: { key: string; repeat?: boolean }): boolean;
```

| 決めたこと | 理由 |
|---|---|
| 同じ `<button>` のまま、実行中は `secondary` の「停止」（`Square`）。`loading` は使わない | 要素が替わらないのでフォーカスが残る。処理中のスピナーと経過時間は結果の領域の `ProcessingIndicator` が出す（動くスピナーは 1 つ。messaging §3.7） |
| 2 つのラベルを同じセルに重ね、見えない方は `invisible` | 「検索テスト」→「停止」で幅が縮まず、位置もずれない。見えない方は読み上げの名前に入らない |
| ダブルクリックの 2 回目（`detail >= 2`）と押し続けた Enter / Space の繰り返しは無視する | 実行で「停止」に変わった直後に、続けて停止しない |
| 停止は赤塗り・赤文字にしない | 取り消せる停止で、途中までの内容は残る（buttons.md §3） |

- 単体テストは `packages/ui/tests/run-stop-button.test.tsx`（判定・要素が替わらない・`aria-disabled`・ダブルクリック・Enter の繰り返し）。
- 実ブラウザは RAG `e2e/chat.spec.ts` / `e2e/search-review.spec.ts` / `e2e/knowledge-base-search-test.spec.ts` / `e2e/answer-progress.spec.ts`、Agent `e2e/chat.spec.ts`（実行中の停止 → `POST /api/runs/{id}/cancel` → 送信に戻る。desktop / 375px、ライト / ダーク）。
- 製品の置き換え: RAG の `components/RunStopButton.tsx` と `lib/run-stop.ts` は削除した。Agent のチャットの送信（`loading` / `disabled` の `Button`）を置き換えた。

---

## FeedbackControls — **新規**（#805）

回答・引用への評価（役に立った / 役に立たなかった）の部品。RAG の回答・引用の評価と、Agent のチャットの回答の評価・管理者の評価（#774）が同じ部品を使う。保存の API・payload・権限は製品が `onSubmit` で持ち、部品は業務の文言を持たない（`labels`）。

```tsx
import { FeedbackControls, toast } from "@engchina/production-ready-ui";

<FeedbackControls<FeedbackReason>
  value={current ? { rating: current.rating, reason: current.reason, comment: current.comment } : null}
  reasons={REASONS.map((value) => ({ value, label: t(`feedback.reason.${value}`) }))}
  labels={{
    question: t("…question"), helpful: t("…helpful"), notHelpful: t("…notHelpful"),
    savedInline: t("…savedInline"), reasonLegend: t("…reasonLegend"),
    commentLabel: t("…commentLabel"), commentPlaceholder: t("…commentPlaceholder"),
    commentCount: (count, max) => t("…commentCount", { count, max }),
    save: t("…save"), cancel: t("common.cancel"), retry: t("common.retry"), saveError: t("…saveError"),
  }}
  getErrorMessage={(error) => (error instanceof ApiError ? error.message : null)}
  onSubmit={async (submission) => {   // { rating, reason, comment, correctedAnswer }（空は null）
    await api.saveFeedback(toPayload(submission));
    toast.success(t("…saved"));        // 成功の Toast は製品が出す
  }}
  correctedAnswer                      // 任意:「修正した回答」の欄（RAG の回答の評価）
  compact                              // 任意: 引用のカードの中の小さな形
  disabled={loadingCurrent}            // 任意
  readOnly                             // 任意: 保存済みの評価を見せるだけ
  commentId="feedback-comment-answer"  // 任意: 欄の id（e2e で使うとき）
  data-testid="chat-feedback-run-1"
/>
```

### FeedbackControls の props

```ts
export type FeedbackRating = "helpful" | "not_helpful";
export interface FeedbackControlsValue<R extends string = string> {
  rating: FeedbackRating; reason?: R | null; comment?: string | null; correctedAnswer?: string | null;
}
export interface FeedbackControlsSubmission<R extends string = string> {
  rating: FeedbackRating; reason: R | null; comment: string | null; correctedAnswer: string | null;
}
export interface FeedbackControlsProps<R extends string = string> {
  value: FeedbackControlsValue<R> | null | undefined;
  reasons: readonly { value: R; label: string }[];
  onSubmit: (submission: FeedbackControlsSubmission<R>) => Promise<unknown>; // reject で失敗の表示
  labels: FeedbackControlsLabels;               // すべて翻訳済み（既定の日本語は持たない）
  getErrorMessage?: (error: unknown) => string | null | undefined; // 無ければ labels.saveError
  commentMaxLength?: number;                    // 既定 1000
  correctedAnswer?: boolean;                    // 「修正した回答」の欄
  correctedAnswerMaxLength?: number;            // 既定 20000
  compact?: boolean;
  disabled?: boolean;
  readOnly?: boolean;
  commentId?: string;
  correctedAnswerId?: string;
  className?: string;
  "data-testid"?: string;
}
export function isSameFeedback(value, submission): boolean; // 空白を除いて比べ、空文字と null を同じとみなす
```

| 決めたこと | 理由 |
|---|---|
| 「役に立った」はすぐ保存、「役に立たなかった」は理由（必須。`FieldLegend required`）・コメント（任意）・修正した回答（任意）をその場で開く | 良い評価は 1 回の操作で済ませ、悪い評価は改善に使える理由を必ず残す |
| ボタンは `ghost` / 評価済みは `secondary` + `aria-pressed`、`iconOnly`（Tooltip は `aria-label` と同じ文言）。評価済みの 👍 は `text-success-fg`、👎 は `text-danger-fg` | 色だけに頼らず押された状態を `aria-pressed` と枠で示す |
| 保存済みと同じ内容は送り直さず閉じる | 二重の保存・Toast を出さない |
| 保存中は押したボタンだけ `loading`（ほかは `disabled`） | 動くスピナーは操作 1 つに 1 つ（messaging §3.7） |
| 失敗は部品の下に `role="alert"` の文言と「再試行」（同じ内容を送り直す）。次の保存まで残す | 結果は起点の操作の直下に出す（messaging §10）。成功は Toast（製品） |
| 問い・ボタン・保存済みの表示は 1 行。`compact` は問いを読み上げだけにし、右寄せ・保存済みの表示なし・上の区切り線なし | 引用のカードの中に収める |

- 単体テストは `packages/ui/tests/feedback-controls.test.tsx`（すぐ保存・理由の必須・コメントの整形・修正した回答・同じ評価・失敗と再試行・保存中・`compact`・`readOnly`）。
- 実ブラウザは RAG `e2e/chat.spec.ts` / `e2e/search-review.spec.ts` / `e2e/feedback.spec.ts` / `e2e/citation-variant-badge.spec.ts`、Agent `e2e/feedback.spec.ts`（チャットの回答の評価・管理者の評価。既存の spec のまま）。
- 製品の置き換え: RAG の `components/feedback/FeedbackControls.tsx` と Agent の `components/chat/AnswerFeedback.tsx` は、API と文言をつなぐ薄いラッパーになった（見た目・振る舞いは変えない）。

## useActionPending — **新規**（#819）

`Button` の `loading` は押したボタンだけが持つ（UX 契約 buttons.md §8）。押したボタンが始めた処理の間だけ `true` になる状態を作る hook。

```ts
import { useActionPending } from "@engchina/production-ready-ui";

export interface ActionPending {
  pending: boolean;                                        // track に渡した処理のどれかが終わっていない間だけ true
  track: <T>(work: () => Promise<T>) => Promise<T>;        // 処理を実行し、結果・例外はそのまま返す
}
export function useActionPending(): ActionPending;

// 「表示を更新」: 押した取り直しの間だけ回す
const manualRefresh = useActionPending();
<PageHeader actions={[{ id: "refresh", kind: "utility", label, icon: RefreshCw,
  loading: manualRefresh.pending, onClick: () => void manualRefresh.track(() => query.refetch()) }]} />
```

| 決めたこと | 理由 |
|---|---|
| 再取得のボタンに query の `isFetching` をそのまま渡さず、押した取り直しを `track` で包む | `isFetching` は定期の取り直し（`refetchInterval`）・他の操作の後の invalidate・絞り込みやページの切り替え（`keepPreviousData`）・フォーカスでも true になり、押していないボタンが回るため |
| 重なった `track` はすべて終わるまで `pending`。unmount 後は state を更新しない | 連続で押したとき・画面を離れたときに状態がずれない |
| 1 つの mutation を複数のボタンが使うときは、この hook ではなく `activeOperation` の state・`mutation.variables`（`isPending` のときだけ読む）・行の id で押したボタンを区別する | `mutation.isPending` だけでは押したボタンが分からない |

- 単体テストは `packages/ui/tests/action-pending.test.tsx`。
- 使う所: system-settings の `SystemTablesCard`（状態を再取得）、RAG のサービスのログの「再取得」、Agent の「表示を更新」（Runtime・実行履歴・監査・Snapshot・フィードバック・利用状況）と監査の「フィルター適用」、NL2SQL の一覧の「表示を更新」など。

## ChatUserMessage / createOptimisticChatMessage — **新規**（#907）

チャットの利用者のメッセージ（右寄せの吹き出し）と、送った質問を楽観的に出すための仮のメッセージの形。振る舞いの規則は UX 契約 messaging.md §11「チャットの送信」。送信・置き換えの API は製品ごとに違う（RAG は SSE、NL2SQL はジョブ、Agent は Run）ので、部品は形と状態だけを持つ。

```tsx
import {
  ChatUserMessage,
  createOptimisticChatMessage,
  withOptimisticChatStatus,
  type OptimisticChatMessage,
} from "@engchina/production-ready-ui";

const [pending, setPending] = useState<OptimisticChatMessage | null>(null);

function submit() {
  setPending(createOptimisticChatMessage(draft.trim()));   // status: "sending"、localId は画面の中だけの ID
  setDraft("");
  send.mutate(draft.trim(), {
    onSuccess: (created) => { putIntoConversationCache(created); setPending(null); },   // 確定したものに置き換える
    onError: () => setPending((p) => (p ? withOptimisticChatStatus(p, "failed") : p)),  // 残して再送信を出す
  });
}

{pending ? (
  <>
    <ChatUserMessage status={pending.status} failedLabel={t("chat.sendFailed")} testId="chat-pending">
      {pending.content}
    </ChatUserMessage>
    {pending.status === "failed" ? (
      <Banner severity="danger" action={<Button variant="secondary" size="sm" icon={RotateCcw} onClick={resend}>{t("chat.resend")}</Button>}>
        {reason}
      </Banner>
    ) : (
      <ProcessingIndicator active operationKey={pending.localId} startedAt={pending.sentAtMs} label={t("chat.answering")} />
    )}
  </>
) : null}
```

| 決めたこと | 理由 |
|---|---|
| 送信中（`sending`）も吹き出しの見た目は送信後と同じ（薄くしない・スピナーを付けない） | ChatGPT・Claude・Gemini と同じ。待ちの表示は回答の場所の 1 つだけにする（messaging.md §3.7 の単一スピナー） |
| 失敗（`failed`）は吹き出しの下に `failedLabel` を `AlertCircle` 付きで出す。原因と「再送信」は製品が Banner で出す | 質問の状態（送れていない）と原因・対処を分ける（messaging.md §9 P1 / P2）。色だけに頼らない |
| 状態は `data-status`（`sending` / `sent` / `failed` / `stopped`）に出す | e2e で状態を確かめる |
| 仮の ID は `crypto.randomUUID` を使わず時刻と連番で作る | http の IP 直打ちなど安全でない文脈で `randomUUID` が無い |
| `withOptimisticChatStatus(m, "sending")` は送信の時刻を数え直す。失敗・停止は送信の時刻を保つ | 再送信の経過時間を 0 から出す |

- 単体テストは `packages/ui/tests/chat-message.test.tsx`。
- 実ブラウザは RAG `e2e/chat.spec.ts`・NL2SQL `tests/e2e/sql-chat.spec.ts`・Agent `e2e/chat.spec.ts` の「#907」のテスト（応答を遅らせて、送信の直後に質問と作成中の表示が出ること、新しい会話・続きの会話、失敗 → 再送信、停止。desktop / 375px、ライト / ダーク）。
- 使う所: RAG の `components/chat/ChatClient.tsx`、NL2SQL の `features/nl2sql/SqlChatPage.tsx`、Agent の `pages/ChatPage.tsx`（保存済みの質問の吹き出しも同じ部品）。

## ChatProgress — **新規**（#1145）

チャットの回答の場所（アシスタントの吹き出しの中、回答の上）に置く、backend の処理の段階の控えめな表示。3 製品（RAG・NL2SQL・Agent）のチャットで同じ部品・同じ段階の形を使う。SQL 生成の画面の工程の表示（NL2SQL の `WorkflowProgressStrip`）より情報を絞る。

### 段階の形（3 製品共通の契約）

AG-UI の `STEP_STARTED` / `STEP_FINISHED` / `TOOL_CALL_*` / `RUN_ERROR` に倣う。backend は製品の既存の配信（polling / SSE / WebSocket）で、この形の一覧を画面へ渡す（画面が backend の値からこの形を作ってもよい。NL2SQL はジョブの `steps` から作る）。

```ts
import type { ChatProgressStep } from "@engchina/production-ready-ui";

type ChatProgressStep = {
  id: string;                 // 段階の識別子（例: "classify" / "schema" / "generate_sql" / "execute" / "summarize" / "tool:<name>"）
  label: string;              // 利用者向けの日本語（翻訳済み。例:「SQL を生成しています」）
  status: "pending" | "running" | "done" | "failed" | "skipped";
  startedAt?: string;         // ISO 8601
  finishedAt?: string;        // ISO 8601
  detail?: string;            // 任意の短い補足（対象の表・ツール名・件数）。SQL 全文・ORA コード等の技術的な詳細は入れない
};
```

- `label` は状態に合わせて製品が言い換えてよい（実行中「〜しています」、完了「〜しました」、失敗「〜できませんでした」、未実行は名詞）。
- 失敗の原因・対処は段階に入れず、回答の場所の danger の `Banner`（`ApiErrorBanner`）で出す（messaging.md §9 / §10）。

### 使い方

```tsx
import { ChatProgress } from "@engchina/production-ready-ui";

<Card>
  <CardContent className="space-y-3">
    <ChatProgress
      steps={steps}                 // ChatProgressStep[]
      active={inFlight}             // 省略時は段階から決める（running がある / 失敗が無く pending が残る）
      elapsedMs={totalMs}           // 完了後の全体の所要時間。省略時は段階の最初の開始から最後の終了まで
      labels={{ status: { ...DEFAULT_CHAT_PROGRESS_LABELS.status, skipped: "未実行" } }}
      testId="chat-progress"
    />
    {answer}
  </CardContent>
</Card>
```

### ChatProgress の props

| prop | 型 | 既定 | 説明 |
|---|---|---|---|
| `steps` | `ChatProgressStep[]` | — | 段階の一覧（表示の順）。空なら何も出さない |
| `active` | `boolean` | 段階から決める | 処理中か。回答の本文の受信中など、段階の外で処理が続くときは明示する |
| `elapsedMs` | `number \| null` | 段階の時刻から | 完了後の 1 行の全体の所要時間 |
| `slowAfterMs` | `number` | `10000` | この時間を超えた今の段階に遅延の案内を付ける |
| `defaultOpen` | `boolean` | 失敗があれば `true` | 完了後の 1 行を最初から開くか |
| `reconnecting` | `boolean` | `false` | 更新が途絶え、状態を取り直している（#1160）。今の段階の行の予約した行に、遅延の案内の代わりに「接続を確認しています。」を出す。`useChatProgressTracker` の `progressProps` で渡す |
| `labels` | `Partial<ChatProgressLabels>` | `DEFAULT_CHAT_PROGRESS_LABELS` | 文言（`completedSteps(count)` / `summary(count, duration)` / `steps` / `elapsed` / `slow` / `reconnecting` / `status` / `formatDuration(ms)`） |
| `testId` | `string` | — | 根に `data-testid`。`-current`（今の段階。`data-step-id` / `data-slow` / `data-reconnecting`）・`-timer`・`-slow`・`-reconnecting`・`-completed`（実行中の畳んだ見出し）・`-summary`（完了後の 1 行）・`-step-<id>`（一覧の行。`data-status`）を付ける |

根の要素は `data-chat-progress-state`（`running` / `done` / `failed`）と `aria-busy` を持つ。

| 決めたこと | 理由 |
|---|---|
| 実行中は今の段階の 1 行だけを出し、完了した段階は「✓ N ステップ完了」に畳む（既定は閉じる） | チャットの回答の場所を工程の一覧で埋めない（ChatGPT・Claude・Perplexity の「考えています / 検索しています」と同じ密度）。何をしているかは 1 行で分かり、詳しく見たい利用者だけが開く |
| 経過時間は今の段階の開始から数え、遅延の案内も今の段階の行に付ける | どの段階で時間がかかっているか（送信・開始待ち・生成など）が分かる。全体の時間は完了後の 1 行に出す |
| 遅延の案内の行は最初から高さを予約する（`ProcessingIndicator` の #902 と同じ） | 10 秒後に行が足されてスピナーの行が動かない |
| 今の段階の行を上に、完了した段階の畳んだ見出しを下に置く | 段階が完了して見出しが現れても、スピナーの行の位置が変わらない |
| 完了後は「処理の経過（N ステップ・M 秒）」の 1 行に畳む。失敗した段階があれば開いて出す | 回答を読む邪魔をしない。失敗はどこで止まったかを最初から見せる |
| 状態はアイコン（`CheckCircle2` / `XCircle` / `MinusCircle` / `Circle` / `Spinner`）と文字（失敗・スキップは見える文字、完了は読み上げの文字）で示す | 色だけに頼らない |
| 段階の切り替わりだけを `role="status"` で読み上げる。経過時間は `role="timer"` + `aria-live="off"` | 毎秒の更新を読み上げない。完了後は読み上げの文を空にする（回答は会話の欄の `role="log"` が知らせる） |
| 動くスピナーは今の段階の 1 つだけ。スピナーは reduced-motion でも回す（#440） | 同じ処理のスピナーは 1 つ（messaging.md §3.7）。開閉の Chevron は `Disclosure` が reduced-motion で回転を止める |
| 所要時間の表記は「0.4 秒」（10 秒未満は 0.1 秒単位）・「12 秒」・「1 分 5 秒」（`formatChatProgressDuration`） | 1 秒未満の段階が多く、「0 秒」と出すと情報が無い |
| `ProcessingIndicator` / `WorkflowProgressStrip` を拡張せず、新しい部品にした | `ProcessingIndicator` は 1 つの処理の 1 行（段階を持たない）で、設定・一覧の読み込みなど多くの所で使う。`WorkflowProgressStrip` は NL2SQL の SQL 生成の画面だけの工程の帯（工程ごとの説明・結果の中身・中止を持つ）。チャットの段階は両者の間の密度で、3 製品が使うため `packages/ui` に置く。経過時間の計算は `useOperationTiming`、開閉は `Disclosure`、スピナーは `Spinner` を使い回す |

- 単体テストは `packages/ui/tests/chat-progress.test.tsx`。
- 実ブラウザは NL2SQL `tests/e2e/sql-chat.spec.ts` の「#1145」のテスト（応答を遅らせて、送信・開始待ち・実行中・完了・失敗の段階を desktop / 375px・light / dark で確かめる）。
- 使う所: NL2SQL の `features/nl2sql/SqlChatPage.tsx`（段階は `features/nl2sql/chatProgress.ts` がジョブの `steps` から作る）、RAG の `components/chat/ChatClient.tsx`（SSE の `progress`）、Agent の `pages/ChatPage.tsx`（Run から `lib/chat-progress.ts`）。

### useChatProgressTracker — 処理の経過の状態を追う（#1160）

3 製品のチャットが「処理の経過」の状態を追う処理を 1 つにした hook。表示は `ChatProgress`、終端の判定と、配信が途絶えたときの取り直しはこの hook が持つ。製品は自分の配信を hook の入力に合わせる薄いアダプタだけを持つ。

```tsx
import { ChatProgress, useChatProgressTracker } from "@engchina/production-ready-ui";

const progress = useChatProgressTracker({
  key: job.id,                      // 追う対象（ジョブ ID・Run ID・保存した質問の ID）。変わったら数え直す
  steps,                            // 今の段階（ChatProgressStep[]）
  active: inFlight(job),            // 省略時は段階から（isChatProgressActive）。false で取り直しをやめる
  elapsedMs,                        // 完了後の全体の所要時間
  receivedAt: query.dataUpdatedAt, // polling: 最後に取得できた時刻。push の配信は progress.touch() を呼ぶ
  refresh: () => query.refetch({ cancelRefetch: true, throwOnError: false }),
  staleAfterMs: 10_000,             // 既定 15 秒
});

<ChatProgress {...progress.progressProps} labels={labels} testId="chat-progress" />;
```

| 入力 / 戻り値 | 説明 |
|---|---|
| `refresh(signal)` | 状態を取り直す。配信が `staleAfterMs` 届かなければ呼び、`refreshTimeoutMs`（既定 15 秒）で打ち切る（signal を見ない関数・終わらない promise も打ち切る）。失敗・時間切れは 1 秒・2 秒・4 秒 … `maxBackoffMs`（既定 30 秒）の間隔で続け、終端（`active` が false）か対象が変わるまで止めない |
| `enabled` | false の間は追わない（keep-alive で画面が隠れている・承認待ちなど取り直さない状態） |
| `touch()` | push の配信（SSE の event・heartbeat、WebSocket のメッセージ）を受け取ったときに呼ぶ。取り直している間に届いたときだけ描画する |
| `refreshNow()` | 待たずに取り直す（配信が終端の前に終わった・切れたと分かったとき）。次に配信が届くまで、途絶えの時間に関係なく backoff して取り直し続ける |
| `reconnecting` | 取り直しを始めた後に配信が届いていない。`progressProps.reconnecting` で `ChatProgress` に「接続を確認しています。」を出す |

| 決めたこと | 理由 |
|---|---|
| 「途絶え」は段階が進まないことではなく、配信（取得の成功・SSE のバイト）が届かないこと | 準備・生成が数分かかるのは正常（#1155）。応答が返らない取得・切れた接続だけを取り直す |
| 取り直しは backoff して終端まで続け、あきらめない（あきらめる条件は製品のアダプタが持つ） | NL2SQL のチャットで、応答の返らない取得を待ったまま 12 分更新が止まった（#1160）。終わった結果が画面に出ないまま止まる経路を残さない |
| タブが非表示の間は取り直さず、表示に戻ったら待たずに確かめる | polling もタブが非表示の間は止まる。戻ったときに古い状態を見せ続けない |
| 案内は遅延の案内と同じ予約した行に出す | 案内が出ても今の段階の行・経過時間は動かない |

- 単体テストは `packages/ui/tests/chat-progress-tracker.test.tsx`。
- 製品のアダプタ: NL2SQL（会話の polling。`refresh` は応答しない取得を打ち切る `refetch({ cancelRefetch: true })`）、RAG（SSE。`touch()` は受け取ったバイトごと、`all_done` の前に終わったら `refreshNow()`。`refresh` は保存済みの会話から回答を取り直す。backend は event の無い間 10 秒ごとに heartbeat を送る）、Agent（Run の polling。`waiting_approval` の間は `enabled: false`）。
- 実ブラウザは NL2SQL `tests/e2e/sql-chat-progress-refresh.spec.ts`・RAG `e2e/chat-progress-refresh.spec.ts`・Agent `e2e/chat-progress-refresh.spec.ts`。

## ChatResultTable — **新規**（#1154）

チャットの回答の吹き出しの中に、SQL・ツールの実行の結果の表を出す部品です（3 製品で共通。NL2SQL のチャットの SQL の実行が最初の利用者）。振る舞いの表は README §4「`ChatResultTable`」。製品は列・取得した行・打ち切りの有無を渡すだけで、要約・プレビュー・打ち切りの明示・すべての行・CSV・NULL・数値の右寄せは部品が持ちます。実行中（`ProcessingIndicator`）・失敗（danger の `Banner`）・実行の操作（`Button`）は製品が部品の外に置きます。

```tsx
import { ChatResultTable, toast } from "@engchina/production-ready-ui";

<ChatResultTable
  columns={[{ name: "CATEGORY" }, { name: "AMOUNT", type: "number" }]}
  rows={[["家電", 1200], ["食品", null]]}      // 列の順の値の配列。NULL は null
  truncated={result.has_more}                  // 取得の上限で打ち切った
  rowLimit={1000}                              // 打ち切りの案内に出す上限
  elapsedMs={800}
  csvFilename="nl2sql-chat-result-20261005-140312.csv"
  onCsvDownloaded={() => toast.success(t("common.action.downloaded"))}
  fullResult={{                                 // 上限を超える全件の導線（製品の画面）
    href: "/direct-sql",
    label: "SELECT SQL を実行で開く",
    linkComponent: DirectSqlLink,               // react-router の Link（state で SQL を渡す等）
    hint: "すべての行が必要なときは、…で実行してください。",
  }}
  testId="sql-chat-result"
/>
```

### ChatResultTable の props

```ts
export interface ChatResultTableColumn { name: string; type?: string }   // type が number 等なら右寄せ
export interface ChatResultTableProps {
  columns: readonly ChatResultTableColumn[];
  rows: readonly (readonly unknown[])[];       // 取得した行（列の順）。NULL は null
  truncated?: boolean;                         // 上限（行数・応答の大きさ）で打ち切った
  totalRowCount?: number | null;               // 総件数が分かるときだけ（「全 N 行」）
  rowLimit?: number | null;                    // 1 回の取得の上限（案内に出す）
  cellsTruncated?: boolean;                    // セルの文字数の上限で値を切った
  maxCellChars?: number | null;
  elapsedMs?: number | null;
  previewRows?: number;                        // 吹き出しに描く行（既定 50 = CHAT_RESULT_PREVIEW_ROWS）
  pageSizeOptions?: readonly number[];         // すべての行の 1 ページの行数（既定 10 / 50 / 100）
  csvFilename?: string;                        // 既定 result.csv
  onDownloadCsv?: (csv: string, filename: string) => void;  // 省略時は部品がダウンロード
  onCsvDownloaded?: () => void;
  fullResult?: { href?: string; label?: string; linkComponent?: ButtonLinkComponent; hint?: string };
  actions?: React.ReactNode;                   // 要約の行の右に足す製品の操作
  labels?: Partial<ChatResultTableLabels>;     // 既定は DEFAULT_CHAT_RESULT_TABLE_LABELS（日本語）
  className?: string;
  testId?: string;  // <testId>-summary / -table / -scroll / -preview-note / -truncated / -cells-truncated / -view-all / -csv / -sheet / -all-table / -all-scroll / -all-pagination
}
```

- あわせて export: `ResultCell`（セルの表示。結果の画面の表も同じ表示にする）、`isNumericResultColumn`、`isNullResultValue`、`resultValueText`、`resultRowsToCsv`（BOM・CRLF・RFC 4180・式の無害化）、`chatResultSummaryText`、`CHAT_RESULT_PREVIEW_ROWS`、`CHAT_RESULT_PAGE_SIZES`、`DEFAULT_CHAT_RESULT_TABLE_LABELS`。
- 単体テストは `packages/ui/tests/chat-result-table.test.tsx`、実ブラウザは NL2SQL の `tests/e2e/sql-chat-execution.spec.ts`（desktop / 375px、プレビューの表の中の縦横のスクロール、打ち切り、シートのページ送り、CSV）。

### 表の形の判定（`toTabularData` / `splitMarkdownTables`。#1158）

ツールの結果・成果物の JSON と、回答の本文の Markdown の表を `ChatResultTable` の列と行にします。製品に依存しない規則なので、製品で判定を書かず、この関数を通します（使う所: Agent のチャットと実行履歴の詳細）。

```tsx
import { ChatResultTable, MessageText, splitMarkdownTables, toTabularData } from "@engchina/production-ready-ui";

// JSON: 表の形なら列と行、そうでなければ null（今の JSON の表示のまま）。
const table = toTabularData(step.tool_result.output);
{table ? (
  <ChatResultTable
    columns={table.columns}
    rows={table.rows}
    truncated={table.truncated}        // truncated / has_more
    totalRowCount={table.totalRowCount} // total / total_row_count / row_count
    elapsedMs={table.elapsedMs}         // elapsed_ms
  />
) : (
  <JsonPreview value={step.tool_result.output} />
)}

// 回答の本文: 表とそれ以外の文に分ける（コードブロックの中の表は表にしない）。
splitMarkdownTables(answer).map((segment) =>
  segment.kind === "table" ? <ChatResultTable {...segment.data} /> : <MessageText text={segment.text} />
);
```

- 表とみなす形: `{ columns, rows }`（`columns` は列名の文字列か `{ name, label?, type? }`、`rows` はオブジェクトか配列の配列。列の指定があれば 0 行も表）、`{ rows }`（オブジェクトの配列。1 行以上）、オブジェクトの配列（1 行以上。key の和集合を列にする）。行に無い値は `null`。
- セルは文字列・数値・真偽値・null だけ。入れ子のオブジェクト・配列を持つ値、列の分からない 0 行、`columns: []` の 0 行（実行中のジョブ）は表にしない（`null`）。
- Markdown の表は GFM の表頭・区切りの行・本文の行。セルの足りない行は空の文字列、`\|` は `|`、`**` と `` ` `` は除く。値がすべて数（桁区切り・小数・%）の列は `type: "number"`（右寄せ）。
- 単体テストは `packages/ui/tests/tabular-data.test.ts`、実ブラウザは Agent の `e2e/run-result-tables.spec.ts`（チャットと実行履歴の詳細、desktop / 375px、light / dark、60 行・0 行・打ち切り・表でない JSON）。
