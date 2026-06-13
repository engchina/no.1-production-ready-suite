import type { Metadata } from "next";

import { Providers } from "@/components/providers";
import { Sidebar } from "@/components/layout/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "お任せ！RAG",
  description: "production-ready RAG システム",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>
        <Providers>
          <div className="flex">
            <Sidebar />
            <main className="h-screen flex-1 overflow-y-auto" aria-label="メイン領域">
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
