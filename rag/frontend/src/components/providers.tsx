"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ConfirmProvider } from "@/components/ui/confirm-dialog";
import { Toaster } from "@/components/ui/toast";
import { AuthProvider } from "@/lib/auth";

/** TanStack Query / 認証 / メッセージ機構（確認ダイアログ・Toast）のプロバイダ。 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ConfirmProvider>
          {children}
          <Toaster />
        </ConfirmProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
