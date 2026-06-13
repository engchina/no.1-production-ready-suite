import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";

export default async function DocumentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div>
      <div className="border-b border-border bg-card px-8 py-4">
        <Link
          href={APP_ROUTES.fileList}
          className="inline-flex items-center gap-1 text-sm text-muted transition-colors hover:text-foreground"
        >
          <ChevronLeft size={16} aria-hidden />
          {t("workspace.back")}
        </Link>
      </div>
      <div className="p-8">
        <DocumentWorkspace documentId={id} />
      </div>
    </div>
  );
}
