"use client";

import { TraceDetailsLabelsProvider, dataLoadRetryDelay, retryDataLoad } from "@production-ready/ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { Toaster } from "@/components/ui/toast";
import { AuthProvider } from "@/components/security/AuthProvider";
import { RAG_TRACE_DETAILS_LABELS } from "@/lib/answer-process";

/** TanStack Query / 認証 / メッセージ機構（確認ダイアログ・Toast）/「トレースの詳細」の文言のプロバイダ。 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        // 読み取りの自動の再試行は回数を抑えて間隔を空ける（混んでいる DB を繰り返し読まない。#4217）。
        defaultOptions: { queries: { retry: retryDataLoad, retryDelay: dataLoadRetryDelay } },
      }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ConfirmProvider>
          <TraceDetailsLabelsProvider labels={RAG_TRACE_DETAILS_LABELS}>{children}</TraceDetailsLabelsProvider>
          <Toaster />
        </ConfirmProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
