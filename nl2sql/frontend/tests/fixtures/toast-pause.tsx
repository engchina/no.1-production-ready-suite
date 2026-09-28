// #351: 共有 Toaster の一時停止・既定の表示時間と、Banner の閉じるボタンを実ブラウザで確かめる fixture。
import { Banner, Button, PageBody, Toaster, toast } from "@engchina/production-ready-ui";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import "../../src/globals.css";

function ToastPause() {
  const [bannerOpen, setBannerOpen] = useState(true);
  return (
    <PageBody>
      <div className="grid gap-4">
        {bannerOpen ? (
          <Banner severity="warning" title="データベースが縮退モードです" onDismiss={() => setBannerOpen(false)}>
            一部の機能は読み取りだけになります。
          </Banner>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => toast.success("保存しました")}>成功通知</Button>
          <Button variant="secondary" onClick={() => toast.warning("接続が不安定です")}>警告通知</Button>
          <Button variant="secondary" onClick={() => toast.error("保存できませんでした", { description: "もう一度お試しください。" })}>
            エラー通知
          </Button>
        </div>
      </div>
      <Toaster />
    </PageBody>
  );
}

createRoot(document.getElementById("root")!).render(<ToastPause />);
