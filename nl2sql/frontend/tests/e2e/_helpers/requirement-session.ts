/**
 * AI要件確認（SQL の要件の下書き。#1439）の e2e の応答。
 * backend の QuerySessionData（session・requirement_state）と同じ形。表・列は物理名で書く。
 */

export type RequirementStage =
  | "needs_answer"
  | "ready"
  | "corrected"
  | "limit"
  | "minimum"
  | "multi_select"
  | "applied";

export const REQUIREMENT_SESSION_ID = "requirement-session-1";
export const REQUIREMENT_QUESTION = "今年度の地域ごとの受注金額";
export const REQUIREMENT_CLARIFIED_QUESTION =
  "2026-04-01〜2027-03-31（ORDERS.ORDER_DATE）の APP.ORDERS の ORDERS.AMOUNT の合計を、CUSTOMERS.REGION ごとに、合計の降順で表示してください。";

const baseItem = {
  reason_ja: "",
  question_ja: "",
  answer_kind: "free_text",
  candidates: [] as Record<string, unknown>[],
  source: "question",
  tables: [] as string[],
  columns: [] as string[],
  relationship_ids: [] as string[],
  sql_hint: "",
  period: null as Record<string, unknown> | null,
  refs_ja: "",
  answered: false,
  answer_ja: "",
};

function item(fields: Record<string, unknown>) {
  return { ...baseItem, ...fields };
}

const fromItem = item({
  id: "item-from",
  key: "from_tables",
  section: "from",
  label_ja: "使う表",
  value_ja: "APP.ORDERS と APP.CUSTOMERS",
  status: "confirmed",
  tables: ["APP.ORDERS", "APP.CUSTOMERS"],
  refs_ja: "APP.ORDERS（受注）、APP.CUSTOMERS（顧客）",
});

const joinItem = item({
  id: "item-join",
  key: "join_relationships",
  section: "join",
  label_ja: "表の結合の条件",
  value_ja: "ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID",
  status: "inferred",
  source: "schema",
  relationship_ids: ["fk:APP.FK_ORDERS_CUSTOMER"],
  sql_hint: "ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID",
});

const selectItem = item({
  id: "item-select",
  key: "select_region_total",
  section: "select",
  label_ja: "地域と受注金額の合計",
  value_ja: "CUSTOMERS.REGION と SUM(ORDERS.AMOUNT)",
  status: "inferred",
  columns: ["APP.CUSTOMERS.REGION", "APP.ORDERS.AMOUNT"],
  refs_ja: "CUSTOMERS.REGION（地域）、ORDERS.AMOUNT（受注金額）",
  sql_hint: "CUSTOMERS.REGION, SUM(ORDERS.AMOUNT)",
});

const groupItem = item({
  id: "item-group",
  key: "group_region",
  section: "group_by",
  label_ja: "集計の単位",
  value_ja: "CUSTOMERS.REGION ごと",
  status: "confirmed",
  columns: ["APP.CUSTOMERS.REGION"],
  refs_ja: "CUSTOMERS.REGION（地域）",
  sql_hint: "CUSTOMERS.REGION",
});

const orderItem = item({
  id: "item-order",
  key: "order_total",
  section: "order_by",
  label_ja: "並び順",
  value_ja: "受注金額の合計の降順",
  status: "inferred",
  sql_hint: "SUM(ORDERS.AMOUNT) DESC",
});

const fiscalPeriod = {
  expression_ja: "今年度",
  relative_kind: "this_fiscal_year",
  reference_basis: "execution_date",
  reference_date: "2026-10-11",
  date_column: "APP.ORDERS.ORDER_DATE",
};

const periodQuestion = item({
  id: "item-period",
  key: "period_this_fiscal_year",
  section: "period",
  label_ja: "期間",
  value_ja: "今年度",
  status: "ambiguous",
  question_ja: "「今年度」は、どの期間ですか？",
  reason_ja:
    "相対的な期間の表現は、具体的な日付の範囲で確かめます。基準日は実行日（2026-10-11）を既定にしています（表のデータは読みません）。範囲は開始日と終了日を含みます。年度の開始月が業務プロファイル・用語集・オントロジーに定義されていません。日付の列は ORDERS.ORDER_DATE（受注日）です。",
  answer_kind: "single_select",
  columns: ["APP.ORDERS.ORDER_DATE"],
  refs_ja: "ORDERS.ORDER_DATE（受注日）",
  period: fiscalPeriod,
  candidates: [
    {
      id: "candidate-fy-april",
      label_ja: "2026-04-01〜2027-03-31",
      description_ja: "今年度（4 月始まりの年度、年度の全体）。開始日と終了日を含みます。",
      evidence_ja: "基準日（実行日）2026-10-11 から計算",
      source: "calendar",
      tables: [],
      columns: ["APP.ORDERS.ORDER_DATE"],
      relationship_ids: [],
      sql_hint: "ORDERS.ORDER_DATE >= DATE '2026-04-01' AND ORDERS.ORDER_DATE < DATE '2027-04-01'",
      refs_ja: "ORDERS.ORDER_DATE（受注日）",
    },
    {
      id: "candidate-fy-january",
      label_ja: "2026-01-01〜2026-12-31",
      description_ja: "今年度（1 月始まりの年度、年度の全体）。開始日と終了日を含みます。",
      evidence_ja: "基準日（実行日）2026-10-11 から計算",
      source: "calendar",
      tables: [],
      columns: ["APP.ORDERS.ORDER_DATE"],
      relationship_ids: [],
      sql_hint: "ORDERS.ORDER_DATE >= DATE '2026-01-01' AND ORDERS.ORDER_DATE < DATE '2027-01-01'",
      refs_ja: "ORDERS.ORDER_DATE（受注日）",
    },
    {
      id: "candidate-fy-latest",
      label_ja: "データの最新日を基準にする",
      description_ja:
        "「今年度」を、実行日ではなくデータの最新日から数えます。表のデータは確認の段階では読まないため、日付は SQL の中で求めます。",
      evidence_ja: "基準日の選択（データの最新日）",
      source: "calendar",
      tables: [],
      columns: ["APP.ORDERS.ORDER_DATE"],
      relationship_ids: [],
      sql_hint: "",
      refs_ja: "ORDERS.ORDER_DATE（受注日）",
    },
  ],
});

const periodAnswered = item({
  ...periodQuestion,
  value_ja: "2026-04-01〜2027-03-31",
  status: "confirmed",
  question_ja: "",
  reason_ja: "",
  candidates: [],
  source: "user",
  answered: true,
  answer_ja: "2026-04-01〜2027-03-31",
  sql_hint: "ORDERS.ORDER_DATE >= DATE '2026-04-01' AND ORDERS.ORDER_DATE < DATE '2027-04-01'",
  period: {
    ...fiscalPeriod,
    start_date: "2026-04-01",
    end_date: "2027-03-31",
    range_ja: "2026-04-01〜2027-03-31（開始日と終了日を含む）",
  },
});

const currencyQuestion = item({
  id: "item-currency",
  key: "other_currency",
  section: "other",
  label_ja: "金額の税",
  value_ja: "",
  status: "missing",
  question_ja: "ORDERS.AMOUNT（受注金額）は税込と税抜のどちらで集計しますか？",
  reason_ja: "列のコメントに税の扱いが書かれていません。",
});

const statusQuestion = item({
  id: "item-status",
  key: "where_status",
  section: "where",
  label_ja: "受注状態",
  value_ja: "",
  status: "ambiguous",
  question_ja: "どの受注状態（ORDERS.STATUS）に絞りますか？",
  answer_kind: "multi_select",
  columns: ["APP.ORDERS.STATUS"],
  refs_ja: "ORDERS.STATUS（受注状態）",
  candidates: [
    {
      id: "candidate-shipped",
      label_ja: "出荷済み（SHIPPED）",
      evidence_ja: "列のコメント",
      source: "schema",
      tables: [],
      columns: ["APP.ORDERS.STATUS"],
      relationship_ids: [],
      sql_hint: "ORDERS.STATUS = 'SHIPPED'",
      refs_ja: "ORDERS.STATUS（受注状態）",
    },
    {
      id: "candidate-cancelled",
      label_ja: "取消（CANCELLED）",
      evidence_ja: "列のコメント",
      source: "schema",
      tables: [],
      columns: ["APP.ORDERS.STATUS"],
      relationship_ids: [],
      sql_hint: "ORDERS.STATUS = 'CANCELLED'",
      refs_ja: "ORDERS.STATUS（受注状態）",
    },
  ],
});

const SKELETON = [
  "SELECT CUSTOMERS.REGION, SUM(ORDERS.AMOUNT)",
  "FROM APP.ORDERS",
  "  JOIN APP.CUSTOMERS ON ORDERS.CUSTOMER_ID = CUSTOMERS.CUSTOMER_ID",
  "WHERE ORDERS.ORDER_DATE >= DATE '2026-04-01' AND ORDERS.ORDER_DATE < DATE '2027-04-01'",
  "GROUP BY CUSTOMERS.REGION",
  "ORDER BY SUM(ORDERS.AMOUNT) DESC",
].join("\n");

const PENDING_SKELETON = SKELETON.replace(
  "WHERE ORDERS.ORDER_DATE >= DATE '2026-04-01' AND ORDERS.ORDER_DATE < DATE '2027-04-01'",
  "WHERE ⟨未確定: 期間⟩"
);

function stateFor(stage: RequirementStage, items: Record<string, unknown>[]) {
  const unresolved = items.filter(
    (value) => !value.answered && (value.status === "missing" || value.status === "ambiguous")
  );
  const minimum = stage === "minimum"
    ? ["どの表から（FROM）が明確ではありません。", "何を表示するか（SELECT）が明確ではありません。"]
    : [];
  const limit = stage === "limit";
  const ready = unresolved.length === 0 && minimum.length === 0;
  return {
    status: ready ? "ready" : "needs_answer",
    current_item: unresolved[0] ?? null,
    unresolved_items: unresolved,
    answered_count: items.filter((value) => value.answered).length,
    review_round: limit ? 3 : stage === "needs_answer" || stage === "minimum" || stage === "multi_select" ? 0 : 1,
    max_review_rounds: 3,
    review_limit_reached: limit,
    select_from_clear: minimum.length === 0,
    minimum_missing_ja: minimum,
    can_apply: minimum.length === 0 && (ready || limit),
    summary_ja: "",
    sql_skeleton: ready || limit ? SKELETON : PENDING_SKELETON,
    message_ja: ready
      ? "SQL の要件を確認できました。まとめを確かめてから反映してください。"
      : limit
        ? "AI の再検査は上限（3 回）に達しました。まだ確認の要る項目が 1 件あります。"
        : "SQL を正しく作るため、足りない情報・明確でない情報を 1 つずつ確認します。",
  };
}

function itemsFor(stage: RequirementStage): Record<string, unknown>[] {
  switch (stage) {
    case "needs_answer":
      return [fromItem, selectItem, joinItem, periodQuestion, groupItem, orderItem];
    case "multi_select":
      return [fromItem, selectItem, joinItem, statusQuestion, groupItem, orderItem];
    case "ready":
    case "applied":
      return [fromItem, selectItem, joinItem, periodAnswered, groupItem, orderItem];
    case "corrected":
      return [
        fromItem,
        {
          ...selectItem,
          value_ja: "CUSTOMERS.REGION と AVG(ORDERS.AMOUNT)",
          status: "confirmed",
          source: "user",
          answered: true,
          answer_ja: "CUSTOMERS.REGION と AVG(ORDERS.AMOUNT)",
          sql_hint: "",
        },
        joinItem,
        periodAnswered,
        groupItem,
        orderItem,
      ];
    case "limit":
      return [fromItem, selectItem, joinItem, periodAnswered, groupItem, orderItem, currencyQuestion];
    case "minimum":
      return [
        item({
          id: "item-from-missing",
          key: "from_tables",
          section: "from",
          label_ja: "使う表",
          status: "ambiguous",
          question_ja: "どの表のデータを使いますか？",
          answer_kind: "multi_select",
          candidates: [
            {
              id: "candidate-orders",
              label_ja: "APP.ORDERS（受注）",
              evidence_ja: "業務プロファイルの対象の表",
              source: "schema",
              tables: ["APP.ORDERS"],
              columns: [],
              relationship_ids: [],
              refs_ja: "APP.ORDERS（受注）",
            },
          ],
        }),
        item({
          id: "item-select-missing",
          key: "select_output",
          section: "select",
          label_ja: "表示する項目",
          status: "missing",
          question_ja: "結果に表示する列・集計を、Schema の列名で教えてください。",
        }),
      ];
  }
}

/** `POST /query-sessions` などの応答（`data` の中身）。 */
export function requirementSessionData(stage: RequirementStage, version = 1) {
  const items = itemsFor(stage);
  return {
    session: {
      id: REQUIREMENT_SESSION_ID,
      profile_id: "default",
      profile_view_id: "profile-view-requirement",
      ontology_revision_id: "revision-1",
      status: stage === "applied" ? "done" : "awaiting_intent_confirmation",
      original_question: REQUIREMENT_QUESTION,
      current_intent_version: version,
      intents: [],
      clarification_mode: "guided",
      requirement: {
        version,
        question_original: REQUIREMENT_QUESTION,
        clarified_question_ja: REQUIREMENT_CLARIFIED_QUESTION,
        items,
        unanswerable_reason_ja: "",
        warnings_ja: stage === "needs_answer" ? ["Schema に無い・業務プロファイルの範囲の外の名前を除きました: APP.SALES"] : [],
        reference_date: "2026-10-11",
        model: "stub",
        sql_skeleton: "",
      },
      requirement_review_round: 0,
      requirement_applied_at: stage === "applied" ? "2026-10-11T00:00:00Z" : null,
      actor_user_uuid: "user-1",
    },
    requirement_state: stateFor(stage, items),
  };
}
