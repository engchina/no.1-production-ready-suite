import { Banner, RunUsedModels } from "@production-ready/ui";
import { parseAnswerDiagnostics, type AnswerDiagnostics } from "@/lib/answer-diagnostics";
import { t } from "@/lib/i18n";
import { answerModelCascadeView } from "@/lib/model-cascade";

/**
 * 処理の詳細に出す、回答時点のモデルの記録。確認を通らなかったときのモデルで回答し直した回答（モデルのカスケード。
 * #4581）は、そのモデルと 1 行の説明・理由を添える（本文には出さない）。補助の工程が縮退した回答は、その工程の名前も出す。
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
  const degradedSteps = data?.degradedSteps ?? [];
  const retrievalCounts = data?.retrievalCounts ?? null;
  if (!cascadeView && degradedSteps.length === 0 && !retrievalCounts) return used;
  return (
    <div className="grid min-w-0 gap-2">
      {used}
      {retrievalCounts ? (
        // 検索の件数（#4735）。内訳と点数は出さない。
        <p className="text-xs leading-5 text-fg-muted" data-testid="answer-retrieval-counts">
          {t("search.answerDetails.retrievalCounts", retrievalCounts)}
        </p>
      ) : null}
      {cascadeView ? (
        <div className="grid min-w-0 gap-0.5 text-xs leading-5" data-testid="answer-model-cascade">
          <p className="text-fg">{cascadeView.line}</p>
          {cascadeView.reasons ? <p className="text-fg-muted">{t("search.answerDetails.cascade.reason", { reasons: cascadeView.reasons })}</p> : null}
        </div>
      ) : null}
      {degradedSteps.length > 0 ? (
        // 失敗して省いた補助の工程（#4681）。回答の本文には出さない。
        <div data-testid="answer-degraded-steps">
          <Banner severity="warning" title={t("search.answerDetails.degraded.title")}>
            <p>{t("search.answerDetails.degraded.hint")}</p>
            <ul className="mt-1 list-disc pl-5">
              {degradedSteps.map((label) => (
                <li key={label}>{label}</li>
              ))}
            </ul>
          </Banner>
        </div>
      ) : null}
    </div>
  );
}

/**
 * 回答を読むときに要る案内（公開の業務ガイドを読めなかった・資料では決まらない値がある）。
 * 回答の本文の下に出す（「詳細」に畳まない。#2096）。
 */
export function AnswerNotices({ diagnostics }: { diagnostics: unknown }) {
  const data = parseAnswerDiagnostics(diagnostics);
  const notices =
    data &&
    (data.approvedFaq?.direct ||
      data.guideLoadFailed ||
      data.externalDataRequired ||
      data.externalDataItems.length > 0);
  if (!notices) return null;
  return (
    <div className="space-y-2">
      {data ? <AnswerNoticeBanners data={data} /> : null}
    </div>
  );
}

function AnswerNoticeBanners({ data }: { data: AnswerDiagnostics }) {
  const directFaq = data.approvedFaq?.direct ? data.approvedFaq : null;
  return (
    <>
      {directFaq ? (
        // 一致度が下限以上で、登録済みQ&Aの回答をそのまま返した回答（検索と生成をしていない。#4788）。
        <div data-testid="answer-faq-direct">
          <Banner severity="info" title={t("search.answerDetails.faqDirect.title")}>
            <p className="break-words">
              {directFaq.direct?.score == null
                ? t("search.answerDetails.faqDirect.bodyWithoutScore", { question: directFaq.question })
                : t("search.answerDetails.faqDirect.body", {
                    question: directFaq.question,
                    score: Math.round(directFaq.direct.score * 100),
                  })}
            </p>
          </Banner>
        </div>
      ) : null}
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
