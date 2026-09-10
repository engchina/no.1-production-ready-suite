import { toast } from "@engchina/production-ready-ui";
import { Toaster } from "../../src/components/ui/toaster";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Copy, Plus, Trash2 } from "lucide-react";
import { Button, buttonVariants } from "../../src/components/ui/button";
import { FormActionBar } from "../../src/components/FormActionBar";
import { PageHeader } from "../../src/components/PageHeader";
import { RowActionMenu } from "../../src/components/ObjectActions";
import { Pagination } from "../../src/components/Pagination";
import { ErrorState } from "../../src/components/StateViews";
import { ConfirmProvider, useConfirm } from "../../src/components/ui/confirm-dialog";
import "../../src/globals.css";

function Standards() {
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(false);
  const confirm = useConfirm();
  return <>
    <PageHeader title="ボタン UI 標準" subtitle="位置と機能に応じた共通アクション"
      actions={[
        { id: "create", kind: "primary", label: "新規作成", icon: Plus, onClick: () => setCount(c => c + 1) },
        { id: "refresh", kind: "utility", label: "表示を更新", onClick: () => setCount(c => c + 1) },
      ]} />
    <main style={{ padding: 16, display: "grid", gap: 24 }}>
      {(["sm", "md", "lg"] as const).map(size => <section key={size} aria-label={size}>
        <h2>{size} — {size === "sm" ? "ツールバー・一覧" : size === "md" ? "カード内の操作" : "フォーム・実行"}</h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
          {(["primary", "secondary", "ghost", "danger"] as const).map(variant =>
            <Button key={variant} size={size} variant={variant} data-testid={`${size}-${variant}`}
              onClick={() => setCount(c => c + 1)}>
              {variant === "danger" ? <Trash2 aria-hidden /> : <Copy aria-hidden />}
              {{ primary: "保存する", secondary: "コピー", ghost: "選択解除", danger: "削除する" }[variant]}
            </Button>)}
          <Button size={size} disabled data-testid={`${size}-disabled`}>保存不可</Button>
          <Button size={size} loading data-testid={`${size}-loading`}><Plus aria-hidden />処理中</Button>
        </div>
      </section>)}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        <Button iconOnly variant="ghost" aria-label="コピーする" data-testid="icon"><Copy aria-hidden /></Button>
        <Button variant="secondary" touchTarget data-testid="field">入力横の操作</Button>
        <Button variant="secondary" tone="danger" data-testid="danger-trigger" onClick={async () => {
          if (await confirm({ title: "削除の確認", confirmLabel: "削除する", tone: "danger" })) setCount(c => c + 1);
        }}>削除</Button>
        <Button variant="secondary" aria-pressed={selected} onClick={() => setSelected(!selected)}>選択状態</Button>
        <a href="#results" className={buttonVariants({ variant: "secondary", size: "sm" })}>結果へ移動</a>
        <RowActionMenu ariaLabel="対象の操作" actions={[{ id: "delete", label: "削除", tone: "danger", onSelect: () => {} }]} />
      </div>
      <FormActionBar ariaLabel="フォーム操作" testId="form-actions"
        primaryActions={[{ id: "save", label: "変更内容を保存", icon: Copy, onClick: () => {} }]}
        secondaryActions={[{ id: "view", label: "保存済みの内容を確認", href: "#results" }]}
        dangerActions={[{ id: "delete", label: "保存済みの内容を削除", onClick: () => {} }]} />
      <Pagination page={page} totalPages={3} onPageChange={setPage} summary="30 件" prevLabel="前へ" nextLabel="次へ" pageIndicator={`${page} / 3`} />
      <ErrorState message="最新情報を取得できませんでした。" onRetry={() => setCount(c => c + 1)} />
      <Button variant="secondary" onClick={() => toast.success("保存しました", { duration: 0 })}>保存通知</Button>
      <Toaster />
      <output id="results">操作回数: {count}</output>
    </main>
  </>;
}
createRoot(document.getElementById("root")!).render(<MemoryRouter><ConfirmProvider><Standards /></ConfirmProvider></MemoryRouter>);
