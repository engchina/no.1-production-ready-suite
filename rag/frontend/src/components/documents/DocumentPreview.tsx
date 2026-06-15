"use client";

import { FileQuestion } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { charsetFromContentType, decodeText } from "@/lib/text-decode";
import { Skeleton } from "@/components/ui/skeleton";

type Kind = "image" | "pdf" | "text" | "other";

function kindOf(fileName: string): Kind {
  const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (["txt", "csv", "md", "json", "log"].includes(ext)) return "text";
  return "other";
}

/** 原本ファイルのプレビュー（画像 / PDF / テキスト）。 */
export function DocumentPreview({
  documentId,
  fileName,
}: {
  documentId: string;
  fileName: string;
}) {
  const url = api.documentContentUrl(documentId);
  const kind = kindOf(fileName);

  if (kind === "image") {
    return (
      <img
        src={url}
        alt={fileName}
        className="max-h-[60vh] w-full rounded-md border border-border bg-card object-contain"
      />
    );
  }

  if (kind === "pdf") {
    return (
      <iframe
        src={url}
        title={fileName}
        className="h-[60vh] w-full rounded-md border border-border bg-card"
      />
    );
  }

  if (kind === "text") {
    return <TextPreview url={url} />;
  }

  return (
    <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-md border border-border bg-card text-muted">
      <FileQuestion size={24} aria-hidden />
      <p className="text-sm">このファイル形式はプレビューに対応していません。</p>
    </div>
  );
}

function TextPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setText(null);
    setError(false);
    fetch(url, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const charset = charsetFromContentType(res.headers.get("Content-Type"));
        return decodeText(await res.arrayBuffer(), charset);
      })
      .then(setText)
      .catch((e: unknown) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) setError(true);
      });
    return () => controller.abort();
  }, [url]);

  if (error) {
    return (
      <div className="rounded-md border border-border bg-card p-4 text-sm text-muted">
        プレビューを取得できませんでした。
      </div>
    );
  }
  if (text === null) return <Skeleton className="h-40 w-full" />;

  return (
    <pre className="max-h-[60vh] overflow-auto rounded-md border border-border bg-card p-4 text-sm leading-relaxed whitespace-pre-wrap break-words text-foreground">
      {text}
    </pre>
  );
}
