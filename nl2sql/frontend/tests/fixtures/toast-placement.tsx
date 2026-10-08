// #411: 通知（Toaster）が主操作を覆わないことを実ブラウザで確かめる fixture。
// PageHeader の右端にページの操作、ページの末尾の右にコンテンツの操作（NL2SQL のコメント管理の「SQL 生成」と同じ位置）を置く。
// 本文の先頭の右にも内容の面の操作（ContentActionBar。ObjectActionBar と同じ位置）を置く。
// 通知は PageHeader に重ねてページの操作のすぐ左に出る（desktop）。375px は上端のバーに重なる。
import { AppShell, Button, ContentActionBar, PageBody, PageHeader, Section, Toaster, toast } from "@production-ready/ui";
import { Copy, RefreshCw, Save, Sparkles } from "lucide-react";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/globals.css";

const lines = Array.from({ length: 40 }, (_, index) => `- COLUMN_${index + 1}: VARCHAR2(120) NULLABLE=Y COMMENT=列 ${index + 1}`);

function ToastPlacement() {
  const [saved, setSaved] = useState(0);
  const [generated, setGenerated] = useState(0);
  const [copied, setCopied] = useState(0);
  return (
    <AppShell sidebar={null}>
      <PageHeader
        title="コメント管理"
        subtitle="テーブル・ビューを選択し、COMMENT ON SQL を生成して確認語入力後に適用します。"
        actions={[
          { id: "refresh", kind: "utility", label: "表示を更新", icon: RefreshCw, onClick: () => toast.info("表示を更新しました") },
          {
            id: "save",
            kind: "primary",
            label: "保存",
            icon: Save,
            onClick: () => {
              setSaved((value) => value + 1);
              toast.success("保存しました");
            },
          },
        ]}
      />
      <PageBody>
        <Section title="入力確認・SQL生成" description="取得した構造情報を確認して SQL を生成します。">
          <ContentActionBar ariaLabel="構造情報の操作" title="構造情報">
            <Button variant="secondary" icon={Copy} onClick={() => setCopied((value) => value + 1)}>
              コピー
            </Button>
          </ContentActionBar>
          <output data-testid="counts">
            保存 {saved} 回・生成 {generated} 回・コピー {copied} 回
          </output>
          <pre className="whitespace-pre-wrap text-sm text-fg">{lines.join("\n")}</pre>
          <ContentActionBar ariaLabel="SQL 生成の操作">
            <Button
              size="lg"
              icon={Sparkles}
              onClick={() => {
                setGenerated((value) => value + 1);
                toast.success("SQL を生成しました", { description: "SQL 実行のタブで確認できます。" });
              }}
            >
              SQL 生成
            </Button>
          </ContentActionBar>
        </Section>
      </PageBody>
      <Toaster />
    </AppShell>
  );
}

createRoot(document.getElementById("root")!).render(<ToastPlacement />);
