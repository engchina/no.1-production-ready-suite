import { Button, FormStatus } from "@production-ready/ui";
import { Check, Clipboard } from "lucide-react";
import { useState } from "react";

/**
 * 文字をクリップボードにコピーするボタン（抽出した本文・文書 ID など）。コピーできたらラベルを
 * 「コピーしました」にし、できなければボタンの左に理由を出す（自動では消さない）。
 */
export function CopyTextButton({
  text,
  label,
  copiedLabel,
  failedLabel,
  testId,
}: {
  text: string;
  label: string;
  copiedLabel: string;
  failedLabel: string;
  testId?: string;
}) {
  const [state, setState] = useState<"idle" | "success" | "error">("idle");

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("success");
    } catch {
      setState("error");
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      {state === "error" ? (
        <FormStatus tone="danger" className="text-xs" message={failedLabel} />
      ) : null}
      <Button
        variant="ghost"
        size="sm"
        icon={state === "success" ? Check : Clipboard}
        data-testid={testId}
        onClick={() => void handleCopy()}
      >
        {state === "success" ? copiedLabel : label}
      </Button>
    </span>
  );
}
