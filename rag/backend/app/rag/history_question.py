"""会話履歴による質問補完の共有契約。"""

from pr_backend_core.chat_history import CHAT_HISTORY_UNTRUSTED_NOTE
from pydantic import BaseModel, ConfigDict


class HistoryRewrite(BaseModel):
    """補完した質問だけを受け取る。説明など別の出力は受理しない。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    question: str


HISTORY_REWRITE_SYSTEM_PROMPT = (
    "## 目的\n検索に使う日本語の質問を、会話履歴を読まなくても意味が通る形に整えます。\n\n"
    "## Step 1: 最新の質問を読む\n"
    "対象・条件・否定・比較・すべての小問を確認します。条件の変更や話題の切替は最新の質問を"
    "優先し、無関係になった過去の条件を引き継ぎません。\n\n"
    "## Step 2: 履歴で省略を補う\n"
    "履歴から一意に分かる指代・省略・対象の関係だけを補います。利用者が明示した条件を使い、"
    "過去の回答の推測を利用者の事実にしません。候補が複数なら推測で確定しません。\n\n"
    "## Step 3: 要求を保持する\n"
    "複数の文・小問を一つに減らさず、条件・否定・比較をすべて保持します。要求を追加しません。"
    "補完が不要なら元の質問をそのまま使います。\n\n"
    "## Step 4: 質問を返す\n"
    "出力の question に質問の全文を入れます。複数の文を含めて構いません。"
    "回答・説明・分析・Step 番号を含めません。\n\n" + CHAT_HISTORY_UNTRUSTED_NOTE
)
