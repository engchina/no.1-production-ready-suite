/** 画面共通ヘッダー（タイトル + 説明）。 */
export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="border-b border-border bg-card px-8 py-5">
      <h1 className="text-xl font-bold text-foreground">{title}</h1>
      {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
    </header>
  );
}
