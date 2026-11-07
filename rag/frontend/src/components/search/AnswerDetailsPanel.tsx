import { Banner, RunUsedModels } from "@production-ready/ui";
import { parseAnswerDiagnostics, type AnswerDiagnostics } from "@/lib/answer-diagnostics";
import { t } from "@/lib/i18n";
import { answerModelCascadeView } from "@/lib/model-cascade";

/**
 * 処理の詳細に出す、回答時点のモデルの記録。確認を通らなかったときのモデルで回答し直した回答（モデルのカスケード。
 * #4581）は、そのモデルと 1 行の説明・理由を添える（本文には出さない）。
 */
export function AnswerDetailsPanel({ diagnostics, recordedModels = [] }: { diagnostics: unknown; recordedModels?: readonly string[] }) {
  const data = parseAnswerDiagnostics(diagnostics);
  const cascade = data?.modelCascade ?? null;
  const cascadeView = cascade?.escalated ? answerModelCascadeView(cascade) : null;
  const models = [
    ...(data?.models.llm ? [{ label: t("search.answerDetails.model.llm"), value: data.models.llm.label, title: data.models.llm.modelId, modelId: data.models.llm.modelId }] : []),
    ...(cascade?.escalated ? [{ label: t("search.answerDetails.model.escalation"), value: cascade.escalationModel, modelId: cascade.escalationModel, valueKind: "identifier" as const }] : []),
    ...(data?.models.embedding ? [{ label: t("search.answerDetails.model.embedding"), value: data.models.embedding, valueKind: "identifier" as const }] : []),
    ...(data?.models.rerank ? [{ label: t("search.answerDetails.model.rerank"), value: data.models.rerank, valueKind: "identifier" as const }] : []),
  ];
  const used = <RunUsedModels models={[...models, ...recordedModels.map(value => ({ label: t("search.answerDetails.model.llm"), value, valueKind: "identifier" as const }))]} />;
  if (!cascadeView) return used;
  return (
    <div className="grid min-w-0 gap-2">
      {used}
      <div className="grid min-w-0 gap-0.5 text-xs leading-5" data-testid="answer-model-cascade">
        <p className="text-fg">{cascadeView.line}</p>
        {cascadeView.reasons ? <p className="text-fg-muted">{t("search.answerDetails.cascade.reason", { reasons: cascadeView.reasons })}</p> : null}
      </div>
    </div>
  );
}

/**
 * 回答を読むときに要る案内（公開の業務ガイドを読めなかった・資料では決まらない値がある）。
 * 回答の本文の下に出す（「詳細」に畳まない。#2096）。段落の印の記録（#2738）は回答の記録の診断から読み、診断の無い
 * 保存した回答（会話を開き直したとき）は `passageSource`（会話のメッセージ）から読む。
 */
export function AnswerNotices({ diagnostics }: { diagnostics: unknown; passageSource?: unknown }) {
  const data = parseAnswerDiagnostics(diagnostics);
  const notices = data && (data.guideLoadFailed || data.externalDataRequired || data.externalDataItems.length > 0);
  if (!notices) return null;
  return (
    <div className="space-y-2">
      {data ? <AnswerNoticeBanners data={data} /> : null}
    </div>
  );
}

function AnswerNoticeBanners({ data }: { data: AnswerDiagnostics }) {
  return (
    <>
      {data.guideLoadFailed ? (
        // 公開の業務ガイドを読めず、使わずに答えた（ガイドが無かったときと区別する。#1278 / #1287）。
        <div data-testid="answer-guide-load-failed">
          <Banner severity="warning" title={t("search.answerDetails.guideLoadFailed.title")}>
            <p>{t("search.answerDetails.guideLoadFailed.body")}</p>
          </Banner>
        </div>
      ) : null}
      {data.externalDataRequired || data.externalDataItems.length ? (
        // 資料では決まらない値（rag_poc の「外部データ確認」。#651）。
        <Banner severity="warning" title={t("search.answerDetails.externalData.title")}>
          <p>{t("search.answerDetails.externalData.hint")}</p>
          {data.externalDataItems.length ? (
            <ul className="mt-1 list-disc pl-5">
              {data.externalDataItems.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </Banner>
      ) : null}
    </>
  );
}
