"""多段の質問の評価セット（#1335・#1352）の原稿と評価セットを、実体のデータから作る。

`rag/evaluation/multi-hop/` の原稿（`sources/*.html`・`sources/*.workbook.json`）と評価セット
（`multi-hop.json`）は、このファイルの実体（部署・システム・保守枠など）から決定的に作る。原稿の文と、
評価セットの正解（期待する語）・必要な根拠（`required_evidence`）は同じデータから作るため、資料を
増やしても正解と根拠の文がずれない。資料はすべて架空の「サンプル社」（と子会社の「サンプル物流社」）の
内容で、実在の顧客・製品の内容は入れない。

- #1335 の 33 問は ID・質問・期待する語をそのまま残し（`ORIGINAL_CASES`）、根拠の文は原稿と同じ
  データから引く。
- #1352 で足した問は、実体のデータから正解と根拠を組み立てる（`_new_cases`）。
- 資料を増やしても #1335 の問の正解が変わらないことは `_check_constraints` で確かめる（例: 重要度 A
  かつ極秘のシステムは今も 2 つだけ、情報システム部が担当するシステムは 1 つだけ）。

PDF / xlsx への変換は `generate_evaluation_corpus.py` が行う（このファイルの `write_sources` を先に
呼ぶ）。原稿だけを作り直すときは、リポジトリ直下から::

    uv run --project rag/backend python rag/scripts/generate_evaluation_corpus.py \\
        rag/evaluation/multi-hop --sources-only
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCES_DIRNAME = "sources"
GOLDEN_SET_NAME = "multi-hop.json"
# 評価セットの検索の件数（RAG の既定の top_k）。資料はこの件数で全部が文脈に入らない大きさにする。
TOP_K = 20
EXPECTED_OUTCOMES = ("answered", "conditional")

LEDGER = "system-ledger.xlsx"
ORGANIZATION = "organization-rules.pdf"
APPROVAL = "approval-rules.pdf"
CHANGE = "change-procedure.pdf"
MAINTENANCE = "maintenance-plan.pdf"
INCIDENT = "incident-contact-rules.pdf"
RETENTION = "data-retention-rules.pdf"
# #1352 で足した資料（紛らわしい規程・旧版・別の会社・新しい段の資料）。
APPROVAL_2023 = "approval-rules-2023.pdf"
MAINTENANCE_PREVIOUS = "maintenance-plan-2025.pdf"
PURCHASE = "purchase-approval-rules.pdf"
ACCESS = "access-request-procedure.pdf"
BACKUP = "backup-rules.pdf"
GUIDE = "system-operation-guide.pdf"
LOGISTICS_ORGANIZATION = "logistics-organization-rules.pdf"
LOGISTICS_LEDGER = "logistics-system-ledger.xlsx"

_STYLE = (
    'body{font-family:"Noto Sans CJK JP","Noto Sans JP",sans-serif;font-size:11pt;line-height:1.6}'
    "h1{font-size:18pt}h2{font-size:14pt;margin-top:1.4em}"
)


# ---- 実体 ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Department:
    """部署（略号は漢字 1 文字）。`division` は所属の本部（None は本部に属さない）。"""

    code: str
    slug: str
    name: str
    division: str | None
    approver: str
    approver_note: str = ""
    nickname: str | None = None


@dataclass(frozen=True)
class System:
    """システム台帳の 1 行。"""

    number: int
    name: str
    alias: str
    dept: str
    severity: str
    confidentiality: str
    prefix: str = "SYS"

    @property
    def sys_id(self) -> str:
        return f"{self.prefix}-{self.number}"


DEPARTMENTS: tuple[Department, ...] = (
    # #1335 の部署（略号・名前・承認者は変えない）。
    Department("情", "jo", "情報システム部", "管理本部", "情報システム部長", nickname="情シス"),
    Department(
        "経",
        "kei",
        "経理部",
        "管理本部",
        "管理本部長",
        "会計の記録に関わるため、部長ではなく本部長が承認します。",
    ),
    Department("人", "jin", "人事部", "管理本部", "人事部長"),
    Department("総", "so", "総務部", "管理本部", "総務部長"),
    Department("営", "ei", "営業企画部", "営業本部", "営業本部長"),
    # #1352 で足した部署（名前の似た部署・略号の紛らわしい部署を含む）。
    Department("財", "zai", "財務部", "管理本部", "財務部長"),
    Department("労", "ro", "労務部", "管理本部", "労務部長"),
    Department(
        "安", "an", "情報セキュリティ部", "管理本部", "情報セキュリティ部長", nickname="情セキ"
    ),
    Department("企", "ki", "経営企画部", "管理本部", "経営企画部長"),
    Department("法", "hou", "法務部", "管理本部", "法務部長"),
    Department("広", "koho", "広報部", "管理本部", "広報部長"),
    Department("業", "gyo", "営業推進部", "営業本部", "営業推進部長"),
    Department("顧", "kokyaku", "顧客サポート部", "営業本部", "顧客サポート部長"),
    Department(
        "製",
        "sei",
        "製造部",
        "生産本部",
        "生産本部長",
        "製造の計画に関わるため、部長ではなく本部長が承認します。",
    ),
    Department("品", "hin", "品質保証部", "生産本部", "品質保証部長"),
    Department("物", "butsu", "物流部", "生産本部", "物流部長"),
    Department("購", "kobai", "購買部", "生産本部", "購買部長"),
    Department("研", "ken", "研究開発部", "生産本部", "研究開発部長"),
    Department(
        "監",
        "kan",
        "内部監査室",
        None,
        "担当役員",
        "監査の独立を保つため、本部長ではなく担当役員が承認します。",
    ),
)
DEPARTMENT_BY_CODE = {item.code: item for item in DEPARTMENTS}

# システム台帳（80 行）。SYS-101〜108 は #1335 の行で、値を変えない。
SYSTEMS: tuple[System, ...] = tuple(
    System(number, name, alias, dept, severity, confidentiality)
    for number, name, alias, dept, severity, confidentiality in (
        (101, "経費精算ポータル", "経費 Portal", "経", "B", "社外秘"),
        (102, "勤怠管理システム", "勤怠", "人", "B", "社外秘"),
        (103, "人事評価システム", "HRM", "人", "A", "極秘"),
        (104, "受発注管理システム", "OMS、受発注", "営", "A", "社外秘"),
        (105, "ドキュメントポータル", "Document Portal", "総", "B", "社内限り"),
        (106, "会議室予約システム", "会議室予約", "総", "C", "社内限り"),
        (107, "資産管理システム", "AMS", "情", "B", "社外秘"),
        (108, "予算管理システム", "BMS", "経", "A", "極秘"),
        (109, "交通費精算ポータル", "交通費 Portal", "経", "B", "社外秘"),
        (110, "勤怠集計システム", "勤怠集計", "労", "C", "社外秘"),
        (111, "人事給与システム", "HRP", "労", "A", "社外秘"),
        (112, "人材評価分析システム", "HRA", "人", "B", "極秘"),
        (113, "受注分析システム", "受注分析", "業", "B", "社外秘"),
        (114, "発注管理システム", "POMS", "購", "A", "社外秘"),
        (115, "ドキュメント管理システム", "DMS", "総", "B", "社内限り"),
        (116, "会議室案内システム", "会議室案内", "総", "C", "社内限り"),
        (117, "資産台帳システム", "資産台帳", "財", "B", "社外秘"),
        (118, "予算実績システム", "予実", "企", "B", "極秘"),
        (119, "購買管理システム", "PMS", "購", "A", "社外秘"),
        (120, "案件管理システム", "PMS", "業", "B", "社外秘"),
        (121, "倉庫管理システム", "WMS", "物", "A", "社外秘"),
        (122, "配送管理システム", "TMS", "物", "B", "社内限り"),
        (123, "生産計画システム", "生産計画", "製", "A", "社外秘"),
        (124, "製造実行システム", "MES", "製", "A", "社外秘"),
        (125, "品質記録システム", "QMS", "品", "A", "社外秘"),
        (126, "検査成績書システム", "検査成績", "品", "B", "社外秘"),
        (127, "サプライヤーポータル", "Supplier Portal", "購", "B", "社外秘"),
        (128, "カスタマーポータル", "Customer Portal", "顧", "A", "社外秘"),
        (129, "問い合わせ管理システム", "CRM", "顧", "B", "極秘"),
        (130, "顧客台帳システム", "顧客台帳", "業", "B", "極秘"),
        (131, "契約書管理システム", "CLM", "法", "B", "極秘"),
        (132, "法令データベース", "法令DB", "法", "C", "社内限り"),
        (133, "研究データ管理システム", "RDM", "研", "B", "極秘"),
        (134, "試作管理システム", "試作管理", "研", "C", "社外秘"),
        (135, "広報素材ライブラリ", "素材ライブラリ", "広", "C", "社内限り"),
        (136, "社内報配信システム", "社内報", "広", "C", "社内限り"),
        (137, "監査記録システム", "監査記録", "監", "B", "極秘"),
        (138, "内部通報窓口システム", "通報窓口", "監", "B", "極秘"),
        (139, "ID 管理システム", "IDM", "安", "A", "社外秘"),
        (140, "ログ監視システム", "SIEM", "安", "A", "社外秘"),
        (141, "脆弱性管理システム", "脆弱性管理", "安", "B", "社外秘"),
        (142, "資金管理システム", "TMS", "財", "A", "社外秘"),
        (143, "固定資産システム", "FAS", "財", "B", "社外秘"),
        (144, "経営ダッシュボード", "経営DB", "企", "B", "極秘"),
        (145, "中期計画管理システム", "中計", "企", "C", "極秘"),
        (146, "採用管理システム", "ATS", "人", "B", "極秘"),
        (147, "研修管理システム", "LMS", "人", "C", "社内限り"),
        (148, "健康管理システム", "健康管理", "労", "B", "極秘"),
        (149, "社宅管理システム", "社宅", "総", "C", "社外秘"),
        (150, "文書保管庫システム", "文書保管庫", "総", "B", "社外秘"),
        (151, "名刺管理システム", "名刺", "業", "C", "社外秘"),
        (152, "見積管理システム", "見積", "業", "B", "社外秘"),
        (153, "販売管理システム", "SMS", "営", "A", "社外秘"),
        (154, "販売分析システム", "販売分析", "営", "C", "社外秘"),
        (155, "請求書発行システム", "請求書", "経", "A", "社外秘"),
        (156, "支払管理システム", "支払", "経", "B", "社外秘"),
        (157, "会計システム", "GL", "経", "A", "社外秘"),
        (158, "連結会計システム", "連結", "財", "B", "極秘"),
        (159, "在庫管理システム", "IMS", "物", "A", "社外秘"),
        (160, "入出荷管理システム", "入出荷", "物", "B", "社内限り"),
        (161, "設備保全システム", "設備保全", "製", "B", "社内限り"),
        (162, "工程管理システム", "工程管理", "製", "B", "社外秘"),
        (163, "図面管理システム", "PDM", "研", "B", "極秘"),
        (164, "特許管理システム", "特許", "法", "C", "極秘"),
        (165, "取引先審査システム", "与信", "財", "B", "極秘"),
        (166, "電子契約システム", "電子契約", "法", "B", "社外秘"),
        (167, "受付管理システム", "受付", "総", "C", "社内限り"),
        (168, "備品予約システム", "備品予約", "総", "C", "社内限り"),
        (169, "アンケート集計システム", "アンケート", "広", "C", "社内限り"),
        (170, "ウェブサイト管理システム", "CMS", "広", "B", "社内限り"),
        (171, "コールセンターシステム", "CTI", "顧", "A", "社外秘"),
        (172, "FAQ 管理システム", "FAQ", "顧", "C", "社内限り"),
        (173, "返品管理システム", "返品", "顧", "B", "社外秘"),
        (174, "輸出管理システム", "輸出管理", "法", "B", "社外秘"),
        (175, "原価管理システム", "原価", "経", "B", "極秘"),
        (176, "経費分析システム", "経費分析", "企", "C", "社外秘"),
        (177, "稟議システム", "稟議", "総", "A", "社外秘"),
        (178, "監査調書システム", "調書", "監", "C", "極秘"),
        (179, "端末管理システム", "MDM", "安", "B", "社外秘"),
        (180, "勤務シフト管理システム", "シフト", "労", "C", "社内限り"),
    )
)
SYSTEM_BY_NUMBER = {item.number: item for item in SYSTEMS}

# 別の会社（サンプル物流社。サンプル社の子会社という設定の架空の会社）の部署とシステム。略号と
# 略称がサンプル社のものと衝突する（「経」「総」「情」、OMS・BMS・DMS）。
LOGISTICS_DEPARTMENTS: tuple[Department, ...] = (
    Department("経", "kei", "経理課", "管理部", "経理課長"),
    Department("総", "so", "総務課", "管理部", "総務課長"),
    Department("情", "jo", "情報システム課", "管理部", "管理部長"),
    Department("運", "un", "運行管理課", "輸送部", "運行管理課長"),
    Department("倉", "so-ko", "倉庫課", "輸送部", "倉庫課長"),
)
LOGISTICS_SYSTEMS: tuple[System, ...] = tuple(
    System(number, name, alias, dept, severity, confidentiality, prefix="SL")
    for number, name, alias, dept, severity, confidentiality in (
        (201, "運行管理システム", "OMS", "運", "A", "社外秘"),
        (202, "経費精算システム", "経費精算", "経", "B", "社外秘"),
        (203, "配車計画システム", "配車", "運", "A", "社外秘"),
        (204, "庫内作業管理システム", "WES", "倉", "B", "社外秘"),
        (205, "車両管理システム", "車両", "運", "B", "社内限り"),
        (206, "勤怠打刻システム", "打刻", "総", "C", "社外秘"),
        (207, "ドライバー教育システム", "教育", "総", "C", "社内限り"),
        (208, "燃料管理システム", "燃料", "運", "C", "社内限り"),
        (209, "荷主ポータル", "Shipper Portal", "情", "B", "社外秘"),
        (210, "請求管理システム", "BMS", "経", "B", "社外秘"),
        (211, "安全運転記録システム", "安全記録", "運", "B", "極秘"),
        (212, "事故報告システム", "事故報告", "総", "B", "極秘"),
        (213, "輸送実績システム", "実績", "運", "C", "社外秘"),
        (214, "ハンディ端末管理システム", "HTM", "情", "C", "社内限り"),
        (215, "入出庫記録システム", "入出庫", "倉", "B", "社外秘"),
        (216, "棚卸システム", "棚卸", "倉", "C", "社内限り"),
        (217, "文書管理システム", "DMS", "総", "C", "社内限り"),
        (218, "給与計算システム", "給与", "経", "A", "社外秘"),
        (219, "協力会社管理システム", "協力会社", "運", "B", "社外秘"),
        (220, "運賃計算システム", "運賃", "経", "B", "社外秘"),
    )
)

# 役職 → 承認の期限（営業日）と根拠の ID（承認規程 第 2 章）。
ROLE_DEADLINE_DAYS = {"部長": 3, "本部長": 5, "担当役員": 7}
ROLE_EVIDENCE = {"部長": "ap-bucho", "本部長": "ap-honbucho", "担当役員": "ap-yakuin"}
# 機密区分 → 保管の年数（データ保管規程）と、利用権限の承認者（利用権限の申請手順）。
RETENTION_YEARS = {"極秘": 10, "社外秘": 7, "社内限り": 3}
RETENTION_EVIDENCE = {"極秘": "dr-gokuhi", "社外秘": "dr-shagaihi", "社内限り": "dr-shanai"}
ACCESS_EVIDENCE = {"極秘": "ac-gokuhi", "社外秘": "ac-shagaihi", "社内限り": "ac-shanai"}
# 重要度 → 障害の最初の連絡・復旧の目標（障害連絡規程）とバックアップ（バックアップ規程）。
FIRST_CONTACT = {
    "A": ("in-a", "検知から 30 分以内に、担当部署の承認者と情シス部長に電話で連絡します", "30分"),
    "B": ("in-b", "検知から 2 時間以内に、担当部署の承認者に連絡します", "2時間"),
    "C": ("in-c", "翌営業日までに、担当部署に報告します", "翌営業日"),
}
RECOVERY = {"A": "4 時間以内", "B": "1 営業日以内", "C": "3 営業日以内"}
RECOVERY_EVIDENCE = {"A": "in-recover-a", "B": "in-recover-b", "C": "in-recover-c"}
BACKUP_RULES = {
    "A": ("毎日 2 回（0 時と 12 時）バックアップを取り、35 世代を保管します", "2回"),
    "B": ("毎日 1 回（2 時）バックアップを取り、14 世代を保管します", "1回"),
    "C": ("毎週日曜日に 1 回バックアップを取り、4 世代を保管します", "毎週日曜"),
}


@dataclass(frozen=True)
class MaintenanceWindow:
    """定期保守計画の個別の保守枠（`label` は計画に書く表記。全角・英字の表記ゆれを含む）。"""

    slug: str
    label: str
    system: int
    window: str


MAINTENANCE_2026: tuple[MaintenanceWindow, ...] = (
    MaintenanceWindow("hrm", "ＨＲＭ", 103, "毎月第 2 土曜日の 22:00〜翌 2:00"),
    MaintenanceWindow("oms", "ＯＭＳ", 104, "毎月第 4 日曜日の 1:00〜5:00"),
    MaintenanceWindow("doc", "Document Portal", 105, "毎週水曜日の 20:00〜22:00"),
    MaintenanceWindow("wms", "ＷＭＳ", 121, "毎月第 1 火曜日の 21:00〜23:00"),
    MaintenanceWindow("mes", "ＭＥＳ", 124, "毎月第 3 日曜日の 2:00〜6:00"),
    MaintenanceWindow("customer", "Customer Portal", 128, "毎月第 1 日曜日の 0:00〜4:00"),
    MaintenanceWindow("pms", "ＰＭＳ（購買）", 119, "毎月第 3 木曜日の 20:00〜23:00"),
    MaintenanceWindow("gl", "ＧＬ", 157, "毎月第 1 土曜日の 20:00〜24:00"),
)
MAINTENANCE_COMMON_2026 = "毎月最終金曜日の 20:00〜24:00"
# 旧版（2025 年度。2026 年 3 月 31 日で終了）。今の計画と同じシステムで、別の時間帯。
MAINTENANCE_2025: tuple[MaintenanceWindow, ...] = (
    MaintenanceWindow("hrm", "ＨＲＭ", 103, "毎月第 1 土曜日の 22:00〜翌 2:00"),
    MaintenanceWindow("oms", "ＯＭＳ", 104, "毎月第 4 土曜日の 1:00〜5:00"),
    MaintenanceWindow("doc", "Document Portal", 105, "毎週木曜日の 20:00〜22:00"),
    MaintenanceWindow("wms", "ＷＭＳ", 121, "毎月第 2 火曜日の 21:00〜23:00"),
)
MAINTENANCE_COMMON_2025 = "毎月最終土曜日の 20:00〜24:00"

# 障害連絡規程で、本部長にも連絡するシステム（システム ID は全角で書く）。
ESCALATIONS = (
    ("in-oms", 104, "取引先に影響するため", "営業本部長"),
    ("in-ims", 159, "出荷が止まるため", "生産本部長"),
)

# システム運用要領の連携（連携先 → 連携元・受け取るデータ）。
LINKS: Mapping[int, tuple[int, str]] = {
    121: (104, "受注データ"),
    124: (123, "生産計画"),
    159: (121, "入出庫の実績"),
    155: (153, "売上データ"),
    157: (156, "支払データ"),
    171: (129, "問い合わせの履歴"),
    163: (133, "試験データ"),
    114: (119, "購買の依頼"),
}
_GUIDE_HOURS = ("24 時間（毎日）", "平日の 8:00〜22:00", "毎日 6:00〜24:00", "平日の 9:00〜18:00")
_GUIDE_NOTES = (
    "月末は利用が多いため、大きな変更を避けます。",
    "利用者の登録は、申請の翌営業日に反映します。",
    "操作の手引きは社内ポータルに載せています。",
    "帳票は夜間の処理で作ります。",
    "画面の表示が遅いときは、まず担当部署に知らせます。",
)


# ---- 文字の扱い ----------------------------------------------------------------------------


def fullwidth(text: str) -> str:
    """ASCII の英数字・記号を全角にする（空白はそのまま。表記ゆれの原稿に使う）。"""
    return "".join(chr(ord(char) + 0xFEE0) if 0x21 <= ord(char) <= 0x7E else char for char in text)


def normalize(text: str) -> str:
    """照合用の形（NFKC・大小文字・空白を無視。評価の `contains_normalized` と同じ考え方）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def _is_ascii(text: str) -> bool:
    return all(ord(char) < 0x80 for char in text)


def approver_role(approver: str) -> str:
    """承認者の役職（承認規程の期限を決める）。"""
    if approver == "担当役員":
        return "担当役員"
    if approver.endswith("本部長"):
        return "本部長"
    if approver.endswith("部長"):
        return "部長"
    raise ValueError(f"役職が分かりません: {approver}")


def window_keyword(window: str) -> str:
    """保守枠の文から期待する語（「毎月第 2 土曜日…」→「第2土曜」、「毎週水曜日…」→「水曜」）。"""
    if match := re.search(r"第 (\d) (.)曜日", window):
        return f"第{match.group(1)}{match.group(2)}曜"
    if match := re.search(r"最終(.)曜日", window):
        return f"最終{match.group(1)}曜"
    if match := re.search(r"毎週(.)曜日", window):
        return f"{match.group(1)}曜"
    raise ValueError(f"保守枠の曜日が分かりません: {window}")


def _mention(system: System) -> str:
    """ほかの資料でのシステムの書き方（英字の略称は全角、衝突する略称は正式名を添える）。"""
    alias = system.alias.split("、")[0]
    collides = sum(alias in other.alias.split("、") for other in SYSTEMS) > 1
    if _is_ascii(alias) and " " not in alias and not collides:
        return fullwidth(alias)
    if _is_ascii(alias) and collides:
        return f"{system.name}（{fullwidth(alias)}）"
    return system.name


# ---- 資料の原稿 ----------------------------------------------------------------------------


@dataclass
class Document:
    """1 つの資料の原稿と、その中の根拠の文（ID → 文）。"""

    file_name: str
    source_name: str
    content: str
    evidence: dict[str, str] = field(default_factory=dict)


def _page(title: str, heading: str, meta: str, chapters: Sequence[tuple[str, str]]) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="ja"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>",
        f"<style>{_STYLE}</style></head>",
        "<body>",
        f"<h1>{html.escape(heading)}</h1>",
        f"<p>{meta}</p>",
    ]
    for number, (chapter, body) in enumerate(chapters, start=1):
        parts.extend(["", f"<h2>第 {number} 章 {html.escape(chapter)}</h2>", body])
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def _ul(items: Iterable[str]) -> str:
    return "<ul>\n" + "".join(f"<li>{item}</li>\n" for item in items) + "</ul>"


def _code_text(dept: Department) -> str:
    if dept.division is None:
        return f"略号「{dept.code}」: {dept.name}。どの本部にも属さず、社長の直属です"
    return f"略号「{dept.code}」: {dept.name}。{dept.division}に属します"


def _approver_text(dept: Department) -> str:
    return f"{dept.name}の承認者は{dept.approver}です"


def _nickname_text(dept: Department) -> str:
    return f"通称「{dept.nickname}」と呼びます"


def _organization() -> Document:
    evidence: dict[str, str] = {}
    codes = []
    approvers = []
    for dept in DEPARTMENTS:
        evidence[f"org-code-{dept.slug}"] = _code_text(dept)
        line = _code_text(dept) + "。"
        if dept.nickname:
            evidence[f"org-nick-{dept.slug}"] = _nickname_text(dept)
            line += _nickname_text(dept) + "。"
        codes.append(line)
        evidence[f"org-appr-{dept.slug}"] = _approver_text(dept)
        approvers.append(_approver_text(dept) + "。" + dept.approver_note)
    evidence.update(
        {
            "org-proxy-ref": "承認者が不在のときの代理は第 4 章を参照してください",
            "org-proxy": "その部署が属する本部の本部長が代理で承認します",
            "org-proxy-exec": "本部長が不在のときは、担当役員が代理で承認します",
            "org-proxy-direct": "承認者が担当役員のときは、代表取締役が代理で承認します",
        }
    )
    content = _page(
        "サンプル社 組織規程（システムの担当と承認者）",
        "組織規程（システムの担当と承認者）",
        "発行: 2026年4月1日　対象: サンプル社（架空の会社。評価用の合成資料）",
        [
            (
                "目的",
                "<p>この規程は、社内のシステムを担当する部署と、その部署の承認者を定めます。"
                "どのシステムをどの部署が担当するかは、システム台帳の「担当部署」の列に、部署の略号"
                "（漢字 1 文字）で書きます。</p>",
            ),
            ("部署と略号", _ul(codes)),
            (
                "部署の承認者",
                "<p>システムの変更・停止・データの消去の申請は、そのシステムを担当する部署の承認者が"
                "承認します。承認者は部署ごとに次のとおりです。</p>\n"
                + _ul(approvers)
                + f"\n<p>{evidence['org-proxy-ref']}。</p>",
            ),
            (
                "承認者の代理",
                "<p>承認者が 3 営業日以上不在のときは、その部署が属する本部の本部長が代理で承認します。"
                f"{evidence['org-proxy-exec']}。本部に属さない部署の承認者が不在のときと、"
                f"{evidence['org-proxy-direct']}。</p>",
            ),
        ],
    )
    return Document(ORGANIZATION, "organization-rules", content, evidence)


def _approval() -> Document:
    deadlines = {
        role: f"{role}が承認する申請は、受付から {days} 営業日以内に承認します"
        for role, days in ROLE_DEADLINE_DAYS.items()
    }
    evidence = {ROLE_EVIDENCE[role]: text for role, text in deadlines.items()}
    evidence.update(
        {
            "ap-overdue-ref": "期限までに承認されないときの扱いは第 4 章を参照してください",
            "ap-emergency-ref": "緊急の変更は第 3 章の手順で進めます",
            "ap-emergency": "作業の後 24 時間以内に承認者へ事後の承認を申請します",
            "ap-overdue": "申請者は承認者の 1 つ上の役職に催促を依頼します",
            "ap-record": "承認の記録は、承認した日から 5 年間保管します",
        }
    )
    content = _page(
        "サンプル社 承認規程",
        "承認規程",
        "発行: 2026年4月1日　対象: サンプル社（架空の会社。評価用の合成資料）",
        [
            (
                "目的",
                "<p>この規程は、申請を承認する期限と、期限を過ぎたときの扱いを定めます。"
                "誰が承認するかは組織規程で決まります。</p>",
            ),
            (
                "承認の期限",
                "<p>承認の期限は、承認者の役職で決まります。期限は、申請を受け付けた日の翌営業日から"
                "数えます。</p>\n"
                + _ul(text + "。" for text in deadlines.values())
                + f"\n<p>{evidence['ap-overdue-ref']}。{evidence['ap-emergency-ref']}。</p>",
            ),
            (
                "緊急の変更",
                "<p>障害の復旧のためにすぐに必要な変更は、承認の前に作業してかまいません。ただし、"
                f"{evidence['ap-emergency']}。</p>",
            ),
            (
                "期限を過ぎたとき",
                f"<p>期限を過ぎても承認されないときは、{evidence['ap-overdue']}（部長の 1 つ上は"
                "本部長、本部長の 1 つ上は担当役員です）。催促を依頼した日から 2 営業日以内に承認され"
                "なければ、申請は取り下げとして扱います。</p>",
            ),
            ("承認の記録", f"<p>{evidence['ap-record']}。</p>"),
        ],
    )
    return Document(APPROVAL, "approval-rules", content, evidence)


def _approval_2023() -> Document:
    """旧版の承認規程（廃止済み。期限・記録の保管が今の版と違う）。"""
    evidence = {
        "ap2023-bucho": "部長の承認は、申請を受け付けてから 5 営業日以内に行います",
        "ap2023-honbucho": "本部長の承認は、申請を受け付けてから 7 営業日以内に行います",
        "ap2023-yakuin": "担当役員の承認は、申請を受け付けてから 10 営業日以内に行います",
        "ap2023-overdue": "期限を過ぎたときは、総務部が承認者に催促します",
        "ap2023-record": "承認の記録は、承認した日から 3 年間保管します",
    }
    content = _page(
        "サンプル社 承認規程（2023年度版）",
        "承認規程（2023年度版）",
        "発行: 2023年4月1日　廃止: 2026年3月31日（2026年4月1日発行の承認規程に置き換えました）"
        "　対象: サンプル社（架空の会社。評価用の合成資料）",
        [
            (
                "目的",
                "<p>この規程は、申請の承認の期限を定めていました。この版は廃止しました。今の期限は"
                "2026年4月1日発行の承認規程で確かめます。</p>",
            ),
            (
                "承認の期限",
                _ul(
                    evidence[key] + "。"
                    for key in ("ap2023-bucho", "ap2023-honbucho", "ap2023-yakuin")
                ),
            ),
            ("期限を過ぎたとき", f"<p>{evidence['ap2023-overdue']}。</p>"),
            ("承認の記録", f"<p>{evidence['ap2023-record']}。</p>"),
        ],
    )
    return Document(APPROVAL_2023, "approval-rules-2023", content, evidence)


def _purchase() -> Document:
    """似た承認の規程（購買の申請。システムの変更の申請とは別の承認者・期限）。"""
    evidence = {
        "pu-small": "50 万円未満の購買は、購買部長が承認します",
        "pu-mid": "50 万円以上 500 万円未満の購買は、生産本部長が承認します",
        "pu-exec": "500 万円以上の購買は、担当役員が承認します",
        "pu-deadline": "購買の申請は、承認者の役職にかかわらず、受付から 2 営業日以内に承認します",
        "pu-overdue-ref": "期限を過ぎたときの扱いは第 4 章を参照してください",
        "pu-overdue": "申請者は購買部に連絡し、購買部が承認者に催促します",
    }
    content = _page(
        "サンプル社 購買承認規程",
        "購買承認規程",
        "発行: 2026年4月1日　対象: サンプル社の購買（物品・サービスの購入）の申請"
        "（架空の会社。評価用の合成資料）",
        [
            (
                "目的",
                "<p>この規程は、購買の申請の承認者と承認の期限を定めます。システムの変更の申請は"
                "承認規程で扱い、この規程の対象ではありません。</p>",
            ),
            (
                "購買の承認者",
                "<p>購買の承認者は、購買の金額で決まります。</p>\n"
                + _ul(evidence[key] + "。" for key in ("pu-small", "pu-mid", "pu-exec")),
            ),
            (
                "承認の期限",
                f"<p>{evidence['pu-deadline']}。{evidence['pu-overdue-ref']}。</p>",
            ),
            (
                "期限を過ぎたとき",
                f"<p>期限を過ぎても承認されないときは、{evidence['pu-overdue']}。</p>",
            ),
        ],
    )
    return Document(PURCHASE, "purchase-approval-rules", content, evidence)


def _change() -> Document:
    evidence = {
        "ch-items-ref": "申請書に書く項目は第 3 章を参照してください",
        "ch-item-rollback": "切り戻しの手順",
        "ch-window-a": "重要度 A のシステムの変更は、定期保守の時間帯にだけ作業します",
        "ch-window-bc": "重要度 B と C のシステムの変更は、平日の 19 時から 22 時の間に作業します",
    }
    content = _page(
        "サンプル社 システム変更手順書",
        "システム変更手順書",
        "発行: 2026年4月1日　対象: サンプル社の本番環境のシステム（架空の会社。評価用の合成資料）",
        [
            (
                "対象",
                "<p>この手順書は、本番環境のシステムの設定の変更とリリースに使います。対象のシステムは、"
                "システム台帳に載っているものに限ります。</p>",
            ),
            (
                "申請",
                "<p>変更の申請は、変更するシステムを担当する部署の承認者に出します。担当部署はシステム"
                f"台帳で、承認者は組織規程で確かめます。{evidence['ch-items-ref']}。</p>",
            ),
            (
                "申請書の項目",
                "<ol>\n<li>変更の内容と理由</li>\n<li>影響を受けるシステムと利用者</li>\n"
                f"<li>{evidence['ch-item-rollback']}</li>\n<li>作業の日時</li>\n</ol>\n"
                "<p>経費 Portal の変更では、上の 4 項目に加えて「月次の締め処理への影響」を書きます。</p>",
            ),
            (
                "作業の時間帯",
                f"<p>{evidence['ch-window-a']}。時間帯は定期保守計画で確かめます。"
                f"{evidence['ch-window-bc']}。</p>",
            ),
            (
                "利用者への告知",
                "<p>システムの停止を伴う変更は、作業の 5 営業日前までに社内ポータルのお知らせで利用者に"
                "告知します。ただし、重要度 A のシステムは 10 営業日前までに告知します。</p>",
            ),
        ],
    )
    return Document(CHANGE, "change-procedure", content, evidence)


def _maintenance_line(item: MaintenanceWindow) -> str:
    return f"{item.label}: {item.window}"


def _maintenance(
    *,
    file_name: str,
    source_name: str,
    year: str,
    prefix: str,
    windows: Sequence[MaintenanceWindow],
    common: str,
    meta: str,
) -> Document:
    evidence = {f"{prefix}-{item.slug}": _maintenance_line(item) for item in windows}
    evidence[f"{prefix}-common-ref"] = "この章に無いシステムは、第 2 章の共通の保守枠で保守します"
    evidence[f"{prefix}-common"] = f"共通の保守枠は、{common} です"
    content = _page(
        f"サンプル社 定期保守計画 {year}年度",
        f"定期保守計画 {year}年度",
        meta,
        [
            (
                "個別の保守枠",
                "<p>次のシステムは、システムごとの保守枠で保守します。</p>\n"
                + _ul(_maintenance_line(item) for item in windows)
                + f"\n<p>{evidence[f'{prefix}-common-ref']}。</p>",
            ),
            ("共通の保守枠", f"<p>{evidence[f'{prefix}-common']}。</p>"),
            (
                "保守枠の変更",
                "<p>保守枠を変えたいときは、変えたい月の 1 か月前までに情シスへ申し出ます。</p>",
            ),
        ],
    )
    return Document(file_name, source_name, content, evidence)


def _incident() -> Document:
    evidence = {key: text for key, text, _keyword in FIRST_CONTACT.values()}
    # #1335 の根拠の文のまま（重要度 C だけは「重要度 C:」から書いている）。
    evidence["in-c"] = f"重要度 C: {FIRST_CONTACT['C'][1]}"
    evidence.update(
        {
            RECOVERY_EVIDENCE[severity]: f"重要度 {severity} のシステムは {target}に復旧します"
            for severity, target in RECOVERY.items()
        }
    )
    escalations = []
    for key, number, reason, contact in ESCALATIONS:
        evidence[key] = (
            f"{fullwidth(SYSTEM_BY_NUMBER[number].sys_id)} の障害は、{reason}、{contact}にも連絡します"
        )
        escalations.append(evidence[key] + "。")
    evidence.update(
        {
            "in-means-ref": "連絡の手段は第 3 章を参照してください",
            "in-means": "社内チャットの障害チャンネル（#incident）に書き込み、15 分ごとに電話をかけ直します",
        }
    )
    content = _page(
        "サンプル社 障害連絡規程",
        "障害連絡規程",
        "発行: 2026年4月1日　対象: サンプル社のシステム（架空の会社。評価用の合成資料）",
        [
            (
                "最初の連絡",
                "<p>障害を見つけた人は、システムの重要度に応じて次のとおり連絡します。システムの重要度は、"
                "システム台帳の「重要度」の列で確かめます。</p>\n"
                + _ul(f"重要度 {severity}: {text}。" for severity, (_k, text, _w) in FIRST_CONTACT.items())
                + "\n<p>"
                + "".join(escalations)
                + f"{evidence['in-means-ref']}。</p>",
            ),
            (
                "復旧の目標",
                _ul(evidence[RECOVERY_EVIDENCE[severity]] + "。" for severity in RECOVERY),
            ),
            (
                "連絡の手段",
                f"<p>連絡は電話で行います。電話がつながらないときは、{evidence['in-means']}。</p>",
            ),
        ],
    )
    return Document(INCIDENT, "incident-contact-rules", content, evidence)


def _retention() -> Document:
    lines = {
        "極秘": "極秘のデータは 10 年間保管します。社外への持ち出しを禁止します。",
        "社外秘": "社外秘のデータは 7 年間保管します。",
        "社内限り": "社内限りのデータは 3 年間保管します。",
    }
    evidence = {
        RETENTION_EVIDENCE[name]: f"{name}のデータは {years} 年間保管します"
        for name, years in RETENTION_YEARS.items()
    }
    evidence.update(
        {
            "dr-erase-ref": "保管期間を過ぎたデータの消し方は第 3 章を参照してください",
            "dr-erase": "システムを担当する部署の承認者の承認を得てから消去します",
            "dr-erase-report": "消去の記録は総務部に提出します",
        }
    )
    content = _page(
        "サンプル社 データ保管規程",
        "データ保管規程",
        "発行: 2026年4月1日　対象: サンプル社のシステムのデータ（架空の会社。評価用の合成資料）",
        [
            (
                "機密区分と保管期間",
                "<p>システムのデータは、そのシステムの機密区分に応じて保管します。システムの機密区分は、"
                "システム台帳の「機密区分」の列で確かめます。</p>\n"
                + _ul(lines[name] for name in RETENTION_YEARS)
                + f"\n<p>{evidence['dr-erase-ref']}。</p>",
            ),
            (
                "閲覧の記録",
                "<p>極秘のデータを扱うシステムは、閲覧の記録（アクセスログ）を 3 年間保管します。</p>",
            ),
            (
                "データの消去",
                f"<p>保管期間を過ぎたデータは、{evidence['dr-erase']}。"
                f"{evidence['dr-erase-report']}。</p>",
            ),
        ],
    )
    return Document(RETENTION, "data-retention-rules", content, evidence)


def _access() -> Document:
    """利用権限の申請手順（機密区分 → 権限の承認者。情報セキュリティ部は通称で書く）。"""
    evidence = {
        "ac-gokuhi": "極秘のシステムの権限は、担当部署の承認者と情セキ部長の両方が承認します",
        "ac-shagaihi": "社外秘のシステムの権限は、担当部署の承認者が承認します",
        "ac-shanai": "社内限りのシステムの権限は、申請者の上長が承認します",
        "ac-review-ref": "権限の見直しは第 4 章を参照してください",
        "ac-review-gokuhi": "極秘のシステムの権限は、半年ごとに見直します",
    }
    content = _page(
        "サンプル社 利用権限の申請手順",
        "利用権限の申請手順",
        "発行: 2026年4月1日　対象: サンプル社のシステムの利用権限（架空の会社。評価用の合成資料）",
        [
            (
                "対象",
                "<p>この手順は、システムの利用権限の追加・変更の申請に使います。権限の承認者は、"
                "システムの機密区分で決まります。機密区分はシステム台帳で確かめます。</p>",
            ),
            (
                "権限の承認者",
                _ul(evidence[key] + "。" for key in ("ac-gokuhi", "ac-shagaihi", "ac-shanai")),
            ),
            (
                "申請の期限",
                "<p>権限は、使い始める日の 3 営業日前までに申請します。"
                f"{evidence['ac-review-ref']}。</p>",
            ),
            (
                "権限の見直し",
                f"<p>{evidence['ac-review-gokuhi']}。それ以外のシステムの権限は、1 年ごとに"
                "見直します。</p>",
            ),
        ],
    )
    return Document(ACCESS, "access-request-procedure", content, evidence)


def _backup() -> Document:
    evidence = {
        f"bk-{severity.lower()}": f"重要度 {severity} のシステムは、{text}"
        for severity, (text, _keyword) in BACKUP_RULES.items()
    }
    evidence.update(
        {
            "bk-restore-ref": "復元の手順は第 3 章を参照してください",
            "bk-restore": "復元は、担当部署の承認者の承認を得てから情シスに依頼します",
        }
    )
    content = _page(
        "サンプル社 バックアップ規程",
        "バックアップ規程",
        "発行: 2026年4月1日　対象: サンプル社のシステム（架空の会社。評価用の合成資料）",
        [
            (
                "目的",
                "<p>この規程は、システムのバックアップの頻度と、保管する世代を定めます。どちらも"
                "システムの重要度で決まります。重要度はシステム台帳の「重要度」の列で確かめます。</p>",
            ),
            (
                "頻度と世代",
                _ul(evidence[f"bk-{severity.lower()}"] + "。" for severity in BACKUP_RULES)
                + f"\n<p>{evidence['bk-restore-ref']}。</p>",
            ),
            (
                "復元",
                f"<p>{evidence['bk-restore']}。復元した後は、担当部署が内容を確かめます。</p>",
            ),
            (
                "保管の場所",
                "<p>バックアップは本番とは別の拠点に保管します。極秘のデータのバックアップは暗号化"
                "します。</p>",
            ),
        ],
    )
    return Document(BACKUP, "backup-rules", content, evidence)


def _guide_name(system: System) -> str:
    """運用要領の見出しの書き方（名前の英字は全角）。"""
    return fullwidth(system.name)


def _link_text(target: int) -> str:
    source, data = LINKS[target]
    return f"{_mention(SYSTEM_BY_NUMBER[source])}から{data}を受け取ります"


def _guide() -> Document:
    """システム運用要領（システムごとの章。利用時間・連携・運用の注意）。"""
    evidence: dict[str, str] = {}
    chapters = [
        (
            "この要領の使い方",
            "<p>この要領は、システムごとの利用時間・連携・運用の注意をまとめます。担当部署・重要度・"
            "機密区分はシステム台帳で、保守の時間帯は定期保守計画で確かめます。</p>",
        )
    ]
    for system in SYSTEMS:
        sentences = [
            f"{_guide_name(system)}（{system.sys_id}）の利用時間は、"
            f"{_GUIDE_HOURS[system.number % len(_GUIDE_HOURS)]}です。"
        ]
        if system.number in LINKS:
            evidence[f"guide-{system.number}-link"] = _link_text(system.number)
            sentences.append(_link_text(system.number) + "。")
        sentences.append(_GUIDE_NOTES[system.number % len(_GUIDE_NOTES)])
        chapters.append((_guide_name(system), "<p>" + "".join(sentences) + "</p>"))
    content = _page(
        "サンプル社 システム運用要領",
        "システム運用要領",
        "発行: 2026年4月1日　作成: 情シス　対象: サンプル社のシステム台帳のシステム"
        "（架空の会社。評価用の合成資料）",
        chapters,
    )
    return Document(GUIDE, "system-operation-guide", content, evidence)


def _logistics_organization() -> Document:
    """別の会社（サンプル物流社）の組織規程。略号はサンプル社と衝突する。"""
    evidence: dict[str, str] = {}
    for dept in LOGISTICS_DEPARTMENTS:
        evidence[f"lg-org-code-{dept.slug}"] = _code_text(dept)
        evidence[f"lg-org-appr-{dept.slug}"] = _approver_text(dept)
    content = _page(
        "サンプル物流社 組織規程（システムの担当と承認者）",
        "サンプル物流社 組織規程（システムの担当と承認者）",
        "発行: 2026年4月1日　対象: サンプル物流社（サンプル社の子会社。架空の会社。評価用の"
        "合成資料）",
        [
            (
                "対象",
                "<p>この規程はサンプル物流社だけに適用します。部署の略号は、サンプル社の略号とは別に"
                "決めています。システムの担当部署は、サンプル物流社のシステム台帳で確かめます。</p>",
            ),
            (
                "部署と略号",
                _ul(evidence[f"lg-org-code-{dept.slug}"] + "。" for dept in LOGISTICS_DEPARTMENTS),
            ),
            (
                "部署の承認者",
                _ul(evidence[f"lg-org-appr-{dept.slug}"] + "。" for dept in LOGISTICS_DEPARTMENTS)
                + "\n<p>サンプル物流社では、課長が承認する申請は受付から 2 営業日以内に、部長が"
                "承認する申請は受付から 4 営業日以内に承認します。</p>",
            ),
        ],
    )
    return Document(LOGISTICS_ORGANIZATION, "logistics-organization-rules", content, evidence)


def _ledger_sheet(title: str, preamble: Sequence[str], systems: Sequence[System]) -> dict[str, Any]:
    return {
        "title": title,
        "preamble": list(preamble),
        "header": ["システムID", "正式名", "略称・別表記", "担当部署", "重要度", "機密区分"],
        "rows": [
            [s.sys_id, s.name, s.alias, s.dept, s.severity, s.confidentiality] for s in systems
        ],
        "text_columns": ["A", "B", "C", "D", "E", "F"],
    }


def _ledger() -> Document:
    sheet = _ledger_sheet(
        "システム台帳",
        [
            "サンプル社のシステム台帳（架空の会社。評価用の合成資料）",
            "担当部署は部署の略号（漢字 1 文字）で書きます。略号と部署の正式名は組織規程の第 2 章を"
            "参照してください。",
        ],
        SYSTEMS,
    )
    evidence = {f"ledger-{s.number}": s.sys_id for s in SYSTEMS}
    return Document(LEDGER, "system-ledger", _workbook_json([sheet]), evidence)


def _logistics_ledger() -> Document:
    sheet = _ledger_sheet(
        "システム台帳",
        [
            "サンプル物流社のシステム台帳（サンプル社の子会社。架空の会社。評価用の合成資料）",
            "担当部署はサンプル物流社の部署の略号です。サンプル物流社の組織規程を参照してください。",
        ],
        LOGISTICS_SYSTEMS,
    )
    evidence = {f"lg-ledger-{s.number}": s.sys_id for s in LOGISTICS_SYSTEMS}
    return Document(LOGISTICS_LEDGER, "logistics-system-ledger", _workbook_json([sheet]), evidence)


def _workbook_json(sheets: Sequence[Mapping[str, Any]]) -> str:
    """表の原稿の JSON（行は 1 行に 1 つ。読みやすさのため）。"""

    def dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = ["{", '  "sheets": [']
    for index, sheet in enumerate(sheets):
        lines.append("    {")
        lines.append(f'      "title": {dump(sheet["title"])},')
        lines.append('      "preamble": [')
        lines.append(",\n".join(f"        {dump(line)}" for line in sheet["preamble"]))
        lines.append("      ],")
        lines.append(f'      "header": {dump(sheet["header"])},')
        lines.append('      "rows": [')
        lines.append(",\n".join(f"        {dump(row)}" for row in sheet["rows"]))
        lines.append("      ],")
        lines.append(f'      "text_columns": {dump(sheet["text_columns"])}')
        lines.append("    }" + ("," if index < len(sheets) - 1 else ""))
    lines.extend(["  ]", "}"])
    return "\n".join(lines) + "\n"


def build_documents() -> list[Document]:
    """資料の原稿（評価セットが参照する順）。"""
    return [
        _approval(),
        _retention(),
        _incident(),
        _ledger(),
        _maintenance(
            file_name=MAINTENANCE,
            source_name="maintenance-plan",
            year="2026",
            prefix="mt",
            windows=MAINTENANCE_2026,
            common=MAINTENANCE_COMMON_2026,
            meta="発行: 2026年4月1日　作成: 情シス　対象: サンプル社のシステム（架空の会社。"
            "評価用の合成資料）",
        ),
        _organization(),
        _change(),
        _approval_2023(),
        _maintenance(
            file_name=MAINTENANCE_PREVIOUS,
            source_name="maintenance-plan-2025",
            year="2025",
            prefix="mt2025",
            windows=MAINTENANCE_2025,
            common=MAINTENANCE_COMMON_2025,
            meta="発行: 2025年4月1日　終了: 2026年3月31日（2026年度は「定期保守計画 2026年度」を"
            "見てください）　作成: 情シス　対象: サンプル社のシステム（架空の会社。評価用の"
            "合成資料）",
        ),
        _purchase(),
        _access(),
        _backup(),
        _guide(),
        _logistics_organization(),
        _logistics_ledger(),
    ]


# ---- 評価セット ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CaseSpec:
    """評価セットの 1 問（根拠は資料の根拠の ID で指す）。"""

    id: str
    split: str
    reasoning_type: str
    hops: int
    query: str
    keywords: tuple[str, ...]
    evidence: tuple[str, ...]


def _case(
    case_id: str,
    split: str,
    reasoning_type: str,
    hops: int,
    query: str,
    keywords: Sequence[str],
    evidence: Sequence[str],
) -> CaseSpec:
    return CaseSpec(
        case_id, split, reasoning_type, hops, query, tuple(keywords), tuple(evidence)
    )


# #1335 の 33 問（ID・質問・期待する語は変えない。根拠の文は資料の原稿から引く）。
ORIGINAL_CASES: tuple[CaseSpec, ...] = (
    _case("sh-approval-record-retention", "dev", "single_hop", 1,
          "承認の記録は何年間保管しますか？", ["5年"], ["ap-record"]),
    _case("sh-confidential-retention", "dev", "single_hop", 1,
          "社外秘のデータは何年間保管しますか？", ["7年"], ["dr-shagaihi"]),
    _case("sh-severity-c-report", "holdout", "single_hop", 1,
          "重要度 C のシステムで障害が起きたら、いつまでにどこへ報告しますか？",
          ["翌営業日", "担当部署"], ["in-c"]),
    _case("sh-ledger-sys107", "holdout", "single_hop", 1,
          "システム ID が SYS-107 のシステムの略称と担当部署の略号を教えてください。",
          ["AMS", "情"], ["ledger-107"]),
    _case("tl-second-saturday-confidentiality", "dev", "table_lookup", 2,
          "定期保守計画で毎月第 2 土曜日に保守するシステムの機密区分は何ですか？",
          ["極秘"], ["mt-hrm", "ledger-103"]),
    _case("tl-wednesday-department", "dev", "table_lookup", 2,
          "毎週水曜日に定期保守をするシステムは、どの部署の略号で台帳に載っていますか？",
          ["総"], ["mt-doc", "ledger-105"]),
    _case("tl-severity-a-top-secret", "dev", "table_lookup", 1,
          "重要度が A で、機密区分が極秘のシステムをすべて挙げてください。",
          ["人事評価システム", "予算管理システム"], ["ledger-103", "ledger-108"]),
    _case("tl-sales-escalation-system", "holdout", "table_lookup", 2,
          "障害連絡規程で、営業本部長にも連絡するとされているシステムの正式名は何ですか？",
          ["受発注管理システム"], ["in-oms", "ledger-104"]),
    _case("tl-it-department-system", "holdout", "table_lookup", 2,
          "情報システム部が担当するシステムの正式名と機密区分を教えてください。",
          ["資産管理システム", "社外秘"], ["org-code-jo", "ledger-107"]),
    _case("br-expense-approver-deadline", "dev", "bridge", 4,
          "経費精算ポータルの変更の申請は、誰が承認し、受付から何営業日以内に承認されますか？",
          ["管理本部長", "5営業日"],
          ["ledger-101", "org-code-kei", "org-appr-kei", "ap-honbucho"]),
    _case("br-attendance-deadline", "dev", "bridge", 4,
          "勤怠管理システムの変更を申請したら、受付から何営業日以内に承認されますか？",
          ["3営業日"], ["ledger-102", "org-code-jin", "org-appr-jin", "ap-bucho"]),
    _case("br-order-approver-deadline", "holdout", "bridge", 4,
          "受発注管理システムの変更の承認者と、承認の期限を教えてください。",
          ["営業本部長", "5営業日"],
          ["ledger-104", "org-code-ei", "org-appr-ei", "ap-honbucho"]),
    _case("br-room-booking-department", "dev", "bridge", 2,
          "会議室予約システムを担当する部署の正式名は何ですか？",
          ["総務部"], ["ledger-106", "org-code-so"]),
    _case("br-hr-evaluation-retention", "dev", "bridge", 2,
          "人事評価システムのデータは何年間保管しますか？", ["10年"], ["ledger-103", "dr-gokuhi"]),
    _case("br-document-portal-retention", "holdout", "bridge", 2,
          "ドキュメントポータルのデータの保管期間は何年ですか？",
          ["3年"], ["ledger-105", "dr-shanai"]),
    _case("br-budget-incident-contact", "dev", "bridge", 4,
          "予算管理システムで障害が起きたら、何分以内に誰へ連絡しますか？",
          ["30分", "管理本部長"], ["ledger-108", "in-a", "org-code-kei", "org-appr-kei"]),
    _case("br-asset-recovery-target", "holdout", "bridge", 2,
          "資産管理システムで障害が起きたときの復旧の目標はどれくらいですか？",
          ["1営業日"], ["ledger-107", "in-recover-b"]),
    _case("br-hr-evaluation-change-window", "dev", "bridge", 3,
          "人事評価システムの本番の変更は、いつ作業できますか？",
          ["第2土曜"], ["ledger-103", "ch-window-a", "mt-hrm"]),
    _case("br-budget-change-window", "dev", "bridge", 4,
          "予算管理システムの本番の変更は、いつ作業できますか？",
          ["最終金曜"], ["ledger-108", "ch-window-a", "mt-common-ref", "mt-common"]),
    _case("br-order-change-window", "holdout", "bridge", 3,
          "受発注管理システムの変更作業ができる時間帯はいつですか？",
          ["第4日曜"], ["ledger-104", "ch-window-a", "mt-oms"]),
    _case("br-room-booking-proxy", "holdout", "bridge", 3,
          "会議室予約システムの変更の承認者が 3 営業日以上不在のとき、だれが代理で承認しますか？",
          ["管理本部長"], ["ledger-106", "org-code-so", "org-proxy"]),
    _case("br-expense-approver-proxy", "dev", "bridge", 5,
          "経費精算ポータルの変更を承認する人が不在のときは、だれが代理で承認しますか？",
          ["担当役員"],
          ["ledger-101", "org-code-kei", "org-appr-kei", "org-proxy-ref", "org-proxy-exec"]),
    _case("ir-overdue-approval", "dev", "intra_document_reference", 2,
          "申請が承認の期限までに承認されないとき、申請者は何をすればよいですか？",
          ["催促"], ["ap-overdue-ref", "ap-overdue"]),
    _case("ir-room-booking-maintenance", "dev", "intra_document_reference", 2,
          "会議室予約システムの定期保守はいつ行いますか？",
          ["最終金曜"], ["mt-common-ref", "mt-common"]),
    _case("ir-change-application-items", "dev", "intra_document_reference", 2,
          "本番環境の設定を変えるときの申請には、何を書けばよいですか？",
          ["切り戻し"], ["ch-items-ref", "ch-item-rollback"]),
    _case("ir-incident-phone-unreachable", "dev", "intra_document_reference", 2,
          "障害の最初の連絡で、電話がつながらないときはどうしますか？",
          ["#incident"], ["in-means-ref", "in-means"]),
    _case("ir-emergency-change", "holdout", "intra_document_reference", 2,
          "緊急の変更を承認の前に作業したときは、そのあと何をしますか？",
          ["24時間", "事後"], ["ap-emergency-ref", "ap-emergency"]),
    _case("ir-expired-data-erasure", "holdout", "intra_document_reference", 2,
          "保管期間を過ぎたデータは、どのように消せばよいですか？",
          ["承認", "総務部"], ["dr-erase-ref", "dr-erase", "dr-erase-report"]),
    _case("cmp-expense-vs-attendance-deadline", "dev", "comparison", 4,
          "経費精算ポータルと勤怠管理システムでは、変更の承認の期限はどちらが長いですか？",
          ["経費精算ポータル"],
          ["ledger-101", "ledger-102", "org-code-kei", "org-code-jin", "org-appr-kei",
           "org-appr-jin", "ap-honbucho", "ap-bucho"]),
    _case("cmp-hr-vs-attendance-retention", "dev", "comparison", 2,
          "人事評価システムと勤怠管理システムでは、どちらのデータを長く保管しますか？",
          ["人事評価システム", "10年"], ["ledger-103", "ledger-102", "dr-gokuhi", "dr-shagaihi"]),
    _case("cmp-hrm-vs-oms-maintenance", "dev", "comparison", 1,
          "HRM と OMS では、どちらの定期保守が月の早い時期にありますか？",
          ["HRM"], ["mt-hrm", "mt-oms"]),
    _case("cmp-hr-vs-asset-first-contact", "holdout", "comparison", 2,
          "人事評価システムと資産管理システムでは、障害のときの最初の連絡の期限はどちらが短いですか？",
          ["人事評価システム", "30分"], ["ledger-103", "ledger-107", "in-a", "in-b"]),
    _case("cmp-document-vs-room-recovery", "holdout", "comparison", 2,
          "ドキュメントポータルと会議室予約システムでは、障害からの復旧の目標はどちらが短いですか？",
          ["ドキュメントポータル", "1営業日"],
          ["ledger-105", "ledger-106", "in-recover-b", "in-recover-c"]),
)  # fmt: skip


# ---- #1352 の問（実体のデータから正解と根拠を組み立てる） ------------------------------------


def _system(number: int) -> System:
    return SYSTEM_BY_NUMBER[number]


def _dept(system: System) -> Department:
    return DEPARTMENT_BY_CODE[system.dept]


def _approval_chain(system: System) -> tuple[list[str], str, int]:
    """システム → 担当部署 → 承認者 → 期限 の根拠、承認者、期限（営業日）。"""
    dept = _dept(system)
    role = approver_role(dept.approver)
    evidence = [
        f"ledger-{system.number}",
        f"org-code-{dept.slug}",
        f"org-appr-{dept.slug}",
        ROLE_EVIDENCE[role],
    ]
    return evidence, dept.approver, ROLE_DEADLINE_DAYS[role]


def _approver_deadline_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    evidence, approver, days = _approval_chain(_system(number))
    return _case(case_id, split, "bridge", 4, query, [approver, f"{days}営業日"], evidence)


def _window_chain(system: System) -> tuple[list[str], str]:
    """重要度 A のシステム → 定期保守の時間帯（個別の枠か、共通の枠）の根拠と期待する語。"""
    if system.severity != "A":
        raise ValueError(f"{system.sys_id} は重要度 A ではありません。")
    for item in MAINTENANCE_2026:
        if item.system == system.number:
            return [f"ledger-{system.number}", "ch-window-a", f"mt-{item.slug}"], window_keyword(
                item.window
            )
    return [
        f"ledger-{system.number}",
        "ch-window-a",
        "mt-common-ref",
        "mt-common",
    ], window_keyword(MAINTENANCE_COMMON_2026)


def _change_window_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    evidence, keyword = _window_chain(_system(number))
    return _case(case_id, split, "bridge", len(evidence), query, [keyword], evidence)


def _retention_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    system = _system(number)
    return _case(
        case_id,
        split,
        "bridge",
        2,
        query,
        [f"{RETENTION_YEARS[system.confidentiality]}年"],
        [f"ledger-{number}", RETENTION_EVIDENCE[system.confidentiality]],
    )


def _backup_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    system = _system(number)
    return _case(
        case_id,
        split,
        "bridge",
        2,
        query,
        [BACKUP_RULES[system.severity][1]],
        [f"ledger-{number}", f"bk-{system.severity.lower()}"],
    )


def _proxy_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    """承認者が部長の部署 → 所属の本部の本部長、承認者が担当役員の部署 → 代表取締役。"""
    system = _system(number)
    dept = _dept(system)
    role = approver_role(dept.approver)
    ledger = f"ledger-{number}"
    if role == "部長" and dept.division:
        return _case(
            case_id,
            split,
            "bridge",
            3,
            query,
            [f"{dept.division}長"],
            [ledger, f"org-code-{dept.slug}", "org-proxy"],
        )
    if role == "担当役員":
        evidence = [
            ledger,
            f"org-code-{dept.slug}",
            f"org-appr-{dept.slug}",
            "org-proxy-ref",
            "org-proxy-direct",
        ]
        return _case(case_id, split, "bridge", 5, query, ["代表取締役"], evidence)
    raise ValueError(f"{system.sys_id} の代理の問は作れません。")


def _incident_contact_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    system = _system(number)
    dept = _dept(system)
    key, _text, keyword = FIRST_CONTACT[system.severity]
    evidence = [f"ledger-{number}", key, f"org-code-{dept.slug}", f"org-appr-{dept.slug}"]
    return _case(case_id, split, "bridge", 4, query, [keyword, dept.approver], evidence)


def _department_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    system = _system(number)
    dept = _dept(system)
    return _case(
        case_id,
        split,
        "bridge",
        2,
        query,
        [dept.name],
        [f"ledger-{number}", f"org-code-{dept.slug}"],
    )


def _link_department_case(case_id: str, split: str, target: int, query: str) -> CaseSpec:
    """運用要領の連携元 → 台帳の担当部署 → 部署の正式名。"""
    source = _system(LINKS[target][0])
    dept = _dept(source)
    evidence = [f"guide-{target}-link", f"ledger-{source.number}", f"org-code-{dept.slug}"]
    return _case(case_id, split, "bridge", 3, query, [dept.name], evidence)


def _access_approver_case(case_id: str, split: str, number: int, query: str) -> CaseSpec:
    """機密区分 → 権限の承認者（極秘は担当部署の承認者と情セキ部長 = 情報セキュリティ部長）。"""
    system = _system(number)
    dept = _dept(system)
    evidence = [
        f"ledger-{number}",
        ACCESS_EVIDENCE[system.confidentiality],
        f"org-code-{dept.slug}",
        f"org-appr-{dept.slug}",
    ]
    keywords = [dept.approver]
    if system.confidentiality == "極秘":
        security = next(item for item in DEPARTMENTS if item.nickname == "情セキ")
        evidence.append(f"org-nick-{security.slug}")
        keywords.append(security.approver)
    elif system.confidentiality != "社外秘":
        raise ValueError(f"{system.sys_id} の権限の承認者は担当部署で決まりません。")
    return _case(case_id, split, "bridge", len(evidence), query, keywords, evidence)


def _compare_deadline_case(case_id: str, split: str, numbers: tuple[int, int]) -> CaseSpec:
    chains = [_approval_chain(_system(number)) for number in numbers]
    if chains[0][2] == chains[1][2]:
        raise ValueError(f"{case_id}: 期限が同じです。")
    winner = _system(numbers[0] if chains[0][2] > chains[1][2] else numbers[1])
    evidence = [chains[0][0][0], chains[1][0][0]]
    for position in (1, 2, 3):
        for chain in chains:
            if chain[0][position] not in evidence:
                evidence.append(chain[0][position])
    names = "と".join(_system(number).name for number in numbers)
    query = f"{names}では、変更の承認の期限はどちらが長いですか？"
    keywords = [winner.name, f"{max(chain[2] for chain in chains)}営業日"]
    return _case(case_id, split, "comparison", 4, query, keywords, evidence)


def _compare_retention_case(case_id: str, split: str, numbers: tuple[int, int]) -> CaseSpec:
    systems = [_system(number) for number in numbers]
    years = [RETENTION_YEARS[item.confidentiality] for item in systems]
    if years[0] == years[1]:
        raise ValueError(f"{case_id}: 保管の年数が同じです。")
    winner = systems[0] if years[0] > years[1] else systems[1]
    query = f"{systems[0].name}と{systems[1].name}では、どちらのデータを長く保管しますか？"
    evidence = [f"ledger-{item.number}" for item in systems] + [
        RETENTION_EVIDENCE[item.confidentiality] for item in systems
    ]
    keywords = [winner.name, f"{max(years)}年"]
    return _case(case_id, split, "comparison", 2, query, keywords, evidence)


def _systems_with_alias(alias: str) -> list[System]:
    return [item for item in SYSTEMS if alias in item.alias.split("、")]


def _compare_alias_recovery_case(case_id: str, split: str, alias: str) -> CaseSpec:
    """略称が衝突する 2 つのシステムの復旧の目標を比べる。"""
    systems = _systems_with_alias(alias)
    order = "ABC"
    if len(systems) != 2 or systems[0].severity == systems[1].severity:
        raise ValueError(f"{case_id}: 略称 {alias} の 2 つのシステムを比べられません。")
    winner = min(systems, key=lambda item: order.index(item.severity))
    query = f"略称が {alias} のシステム 2 つでは、障害からの復旧の目標はどちらが短いですか？"
    evidence = [f"ledger-{item.number}" for item in systems] + [
        RECOVERY_EVIDENCE[item.severity] for item in systems
    ]
    keyword = normalize(RECOVERY[winner.severity]).removesuffix("以内")
    return _case(case_id, split, "comparison", 2, query, [winner.name, keyword], evidence)


def _compare_alias_severity_case(case_id: str, split: str, alias: str) -> CaseSpec:
    systems = _systems_with_alias(alias)
    order = "ABC"
    if len(systems) != 2 or systems[0].severity == systems[1].severity:
        raise ValueError(f"{case_id}: 略称 {alias} の 2 つのシステムを比べられません。")
    winner = min(systems, key=lambda item: order.index(item.severity))
    query = f"略称が {alias} のシステムは 2 つあります。重要度が高いのはどちらですか？"
    evidence = [f"ledger-{item.number}" for item in systems]
    return _case(case_id, split, "comparison", 1, query, [winner.name], evidence)


def _window_lookup_case(case_id: str, split: str, window_prefix: str, query: str) -> CaseSpec:
    """2026 年度の保守枠の曜日 → 台帳の行 → 担当部署の正式名（正式名も答えに入れる）。"""
    matches = [item for item in MAINTENANCE_2026 if item.window.startswith(window_prefix)]
    if len(matches) != 1:
        raise ValueError(f"{case_id}: 保守枠「{window_prefix}」のシステムが 1 つではありません。")
    item = matches[0]
    system = _system(item.system)
    dept = _dept(system)
    evidence = [f"mt-{item.slug}", f"ledger-{system.number}", f"org-code-{dept.slug}"]
    return _case(case_id, split, "table_lookup", 3, query, [system.name, dept.name], evidence)


def _escalation_case(case_id: str, split: str, contact: str) -> CaseSpec:
    matches = [item for item in ESCALATIONS if item[3] == contact]
    if len(matches) != 1:
        raise ValueError(f"{case_id}: {contact}にも連絡するシステムが 1 つではありません。")
    key, number, _reason, _contact = matches[0]
    query = f"障害連絡規程で、{contact}にも連絡するとされているシステムの正式名は何ですか？"
    return _case(
        case_id, split, "table_lookup", 2, query, [_system(number).name], [key, f"ledger-{number}"]
    )


def _department_filter_case(case_id: str, split: str, code: str, severity: str) -> CaseSpec:
    dept = DEPARTMENT_BY_CODE[code]
    systems = [item for item in SYSTEMS if item.dept == code and item.severity == severity]
    if not systems:
        raise ValueError(f"{case_id}: 該当するシステムがありません。")
    query = f"{dept.name}が担当するシステムのうち、重要度が {severity} のものをすべて挙げてください。"
    evidence = [f"org-code-{dept.slug}"] + [f"ledger-{item.number}" for item in systems]
    return _case(
        case_id, split, "table_lookup", 2, query, [item.name for item in systems], evidence
    )


def _logistics_alias_case(case_id: str, split: str, alias: str) -> CaseSpec:
    matches = [item for item in LOGISTICS_SYSTEMS if alias in item.alias.split("、")]
    if len(matches) != 1:
        raise ValueError(f"{case_id}: サンプル物流社の略称 {alias} が 1 つではありません。")
    system = matches[0]
    query = f"サンプル物流社のシステム台帳で、略称が {alias} のシステムの正式名は何ですか？"
    return _case(
        case_id, split, "table_lookup", 1, query, [system.name], [f"lg-ledger-{system.number}"]
    )


def _logistics_approver_case(case_id: str, split: str, number: int) -> CaseSpec:
    system = next(item for item in LOGISTICS_SYSTEMS if item.number == number)
    dept = next(item for item in LOGISTICS_DEPARTMENTS if item.code == system.dept)
    query = f"サンプル物流社の{system.name}の変更は、だれが承認しますか？"
    evidence = [f"lg-ledger-{number}", f"lg-org-code-{dept.slug}", f"lg-org-appr-{dept.slug}"]
    return _case(case_id, split, "bridge", 3, query, [dept.approver], evidence)


def _version_maintenance_case(case_id: str, split: str, slug: str) -> CaseSpec:
    now = next(item for item in MAINTENANCE_2026 if item.slug == slug)
    old = next(item for item in MAINTENANCE_2025 if item.slug == slug)
    query = f"定期保守計画の 2025 年度と 2026 年度では、{now.label} の保守枠はどう変わりましたか？"
    keywords = [window_keyword(old.window), window_keyword(now.window)]
    return _case(
        case_id, split, "comparison", 1, query, keywords, [f"mt-{slug}", f"mt2025-{slug}"]
    )


def _new_cases() -> list[CaseSpec]:
    """#1352 で足した問（名前の似たシステム・部署、略称の衝突、表記ゆれ、旧版・別の会社）。"""
    return [
        # -- dev ---------------------------------------------------------------------------
        _approver_deadline_case(
            "br-mes-approver-deadline",
            "dev",
            124,
            "製造実行システムの変更の申請は、誰が承認し、受付から何営業日以内に承認されますか？",
        ),
        _approver_deadline_case(
            "br-audit-record-approver-deadline",
            "dev",
            137,
            "監査記録システムの変更の承認者と、承認の期限を教えてください。",
        ),
        _department_case(
            "br-supplier-portal-department",
            "dev",
            127,
            "Supplier Portal を担当する部署の正式名は何ですか？",
        ),
        _change_window_case(
            "br-wms-change-window", "dev", 121, "倉庫管理システムの本番の変更は、いつ作業できますか？"
        ),
        _change_window_case(
            "br-purchase-pms-change-window",
            "dev",
            119,
            "購買管理システムの本番の変更は、いつ作業できますか？",
        ),
        _retention_case(
            "br-crm-retention", "dev", 129, "問い合わせ管理システムのデータは何年間保管しますか？"
        ),
        _incident_contact_case(
            "br-idm-incident-contact",
            "dev",
            139,
            "ＩＤ管理システムで障害が起きたら、何分以内に誰へ連絡しますか？",
        ),
        _proxy_case(
            "br-audit-record-proxy",
            "dev",
            137,
            "監査記録システムの変更を承認する人が不在のときは、だれが代理で承認しますか？",
        ),
        _link_department_case(
            "br-wms-link-department",
            "dev",
            121,
            "倉庫管理システムに受注データを送る連携元のシステムを担当する部署の正式名は何ですか？",
        ),
        _backup_case(
            "br-siem-backup", "dev", 140, "ログ監視システムのバックアップは、1 日に何回取りますか？"
        ),
        _access_approver_case(
            "br-contract-access-approver",
            "dev",
            131,
            "契約書管理システムの利用権限の申請は、だれが承認しますか？",
        ),
        _compare_alias_severity_case("cmp-pms-severity", "dev", "PMS"),
        _compare_deadline_case("cmp-mes-vs-qms-deadline", "dev", (124, 125)),
        _case(
            "cmp-approval-deadline-versions",
            "dev",
            "comparison",
            1,
            "今の承認規程と 2023 年度版の承認規程では、本部長が承認する申請の期限はどう変わりましたか？",
            [f"{ROLE_DEADLINE_DAYS['本部長']}営業日", "7営業日"],
            ["ap-honbucho", "ap2023-honbucho"],
        ),
        _window_lookup_case(
            "tl-first-tuesday-department",
            "dev",
            "毎月第 1 火曜日",
            "2026 年度の定期保守計画で毎月第 1 火曜日に保守するシステムの正式名と、担当する部署の"
            "正式名を教えてください。",
        ),
        _logistics_alias_case("tl-logistics-oms", "dev", "OMS"),
        _escalation_case("tl-production-escalation-system", "dev", "生産本部長"),
        _case(
            "ir-backup-restore",
            "dev",
            "intra_document_reference",
            2,
            "システムのデータをバックアップから戻したいときは、どうすればよいですか？",
            ["情シス"],
            ["bk-restore-ref", "bk-restore"],
        ),
        _case(
            "ir-access-review",
            "dev",
            "intra_document_reference",
            2,
            "極秘のシステムの利用権限は、どれくらいの間隔で見直しますか？",
            ["半年"],
            ["ac-review-ref", "ac-review-gokuhi"],
        ),
        _case(
            "sh-purchase-large-approver",
            "dev",
            "single_hop",
            1,
            "金額が 500 万円以上の購買の申請は、だれが承認しますか？",
            ["担当役員"],
            ["pu-exec"],
        ),
        # -- holdout -----------------------------------------------------------------------
        _approver_deadline_case(
            "br-gl-approver-deadline",
            "holdout",
            157,
            "会計システムの変更の申請は、誰が承認し、受付から何営業日以内に承認されますか？",
        ),
        _approver_deadline_case(
            "br-payroll-approver-deadline",
            "holdout",
            111,
            "人事給与システムの変更の承認者と、承認の期限を教えてください。",
        ),
        _change_window_case(
            "br-customer-portal-change-window",
            "holdout",
            128,
            "Customer Portal の本番の変更は、いつ作業できますか？",
        ),
        _change_window_case(
            "br-sales-change-window",
            "holdout",
            153,
            "販売管理システムの変更作業ができる時間帯はいつですか？",
        ),
        _retention_case(
            "br-delivery-retention",
            "holdout",
            122,
            "配送管理システムのデータの保管期間は何年ですか？",
        ),
        _backup_case(
            "br-shipping-backup",
            "holdout",
            160,
            "入出荷管理システムのバックアップは、どれくらいの頻度で取りますか？",
        ),
        _proxy_case(
            "br-quality-record-proxy",
            "holdout",
            125,
            "品質記録システムの変更の承認者が 3 営業日以上不在のとき、だれが代理で承認しますか？",
        ),
        _link_department_case(
            "br-mes-link-department",
            "holdout",
            124,
            "製造実行システムに生産計画を送る連携元のシステムを担当する部署の正式名は何ですか？",
        ),
        _logistics_approver_case("br-logistics-oms-approver", "holdout", 201),
        _compare_retention_case("cmp-crm-vs-faq-retention", "holdout", (129, 172)),
        _compare_alias_recovery_case("cmp-tms-recovery", "holdout", "TMS"),
        _version_maintenance_case("cmp-hrm-maintenance-versions", "holdout", "hrm"),
        _department_filter_case("tl-security-severity-a", "holdout", "安", "A"),
        _window_lookup_case(
            "tl-third-sunday-system",
            "holdout",
            "毎月第 3 日曜日",
            "2026 年度の定期保守計画で毎月第 3 日曜日に保守するシステムの正式名と、担当する部署の"
            "正式名を教えてください。",
        ),
        _case(
            "ir-purchase-overdue",
            "holdout",
            "intra_document_reference",
            2,
            "購買の申請が期限までに承認されないときは、どうなりますか？",
            ["購買部"],
            ["pu-overdue-ref", "pu-overdue"],
        ),
        _case(
            "sh-backup-c-frequency",
            "holdout",
            "single_hop",
            1,
            "重要度 C のシステムのバックアップは、どれくらいの頻度で取りますか？",
            [BACKUP_RULES["C"][1]],
            ["bk-c"],
        ),
    ]


def _check_constraints() -> None:
    """資料を増やしても #1335 の問の正解が変わらないことと、データの形を確かめる。"""
    names = [item.name for item in (*SYSTEMS, *LOGISTICS_SYSTEMS)]
    if len(names) != len(set(names)):
        raise ValueError("システムの正式名が重複しています。")
    if len({item.code for item in DEPARTMENTS}) != len(DEPARTMENTS):
        raise ValueError("部署の略号が重複しています。")
    for item in SYSTEMS:
        if item.dept not in DEPARTMENT_BY_CODE:
            raise ValueError(f"{item.sys_id} の担当部署の略号がありません。")
    # 重要度 A かつ極秘のシステム（両社）は SYS-103・108 だけ（tl-severity-a-top-secret）。
    top = [s.sys_id for s in (*SYSTEMS, *LOGISTICS_SYSTEMS) if (s.severity, s.confidentiality) == ("A", "極秘")]
    if top != ["SYS-103", "SYS-108"]:
        raise ValueError(f"重要度 A かつ極秘のシステムが変わりました: {top}")
    # 情報システム部が担当するシステムは SYS-107 だけ（tl-it-department-system）。
    if [s.number for s in SYSTEMS if s.dept == "情"] != [107]:
        raise ValueError("情報システム部が担当するシステムが変わりました。")
    # 第 2 土曜日・毎週水曜日の保守枠は 1 つずつ（tl-second-saturday-* / tl-wednesday-*）、
    # 会議室予約・予算管理は共通の保守枠（ir-room-booking-maintenance / br-budget-change-window）。
    for plan in (MAINTENANCE_2026, MAINTENANCE_2025):
        for prefix in ("毎月第 2 土曜日", "毎週水曜日"):
            matches = [item.system for item in plan if item.window.startswith(prefix)]
            if matches not in ([], [103], [105]) or (plan is MAINTENANCE_2025 and matches):
                raise ValueError(f"保守枠「{prefix}」のシステムが変わりました: {matches}")
        if {106, 108} & {item.system for item in plan}:
            raise ValueError("会議室予約・予算管理に個別の保守枠を付けないでください。")
    # HRM の保守は今も旧版も OMS より月の早い時期（cmp-hrm-vs-oms-maintenance）。
    for plan in (MAINTENANCE_2026, MAINTENANCE_2025):
        windows = {item.slug: window_keyword(item.window) for item in plan}
        if not windows["hrm"] < windows["oms"]:
            raise ValueError("HRM と OMS の保守の順が変わりました。")
    for item in MAINTENANCE_2026:
        if _system(item.system).severity != "A" and item.slug != "doc":
            raise ValueError(f"{item.label} は重要度 A ではありません。")


def _case_payload(spec: CaseSpec, evidence: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    missing = [key for key in spec.evidence if key not in evidence]
    if missing:
        raise ValueError(f"{spec.id}: 根拠がありません: {missing}")
    documents: list[str] = []
    for key in spec.evidence:
        document = f"file:{evidence[key][0]}"
        if document not in documents:
            documents.append(document)
    return {
        "id": spec.id,
        "category": "document_answerable",
        "split": spec.split,
        "reasoning_type": spec.reasoning_type,
        "hops": spec.hops,
        "query": spec.query,
        "relevant_document_ids": documents,
        "expected_answer_keywords": list(spec.keywords),
        "expected_outcomes": list(EXPECTED_OUTCOMES),
        "required_evidence": [
            {"id": key, "document_id": f"file:{evidence[key][0]}", "text": evidence[key][1]}
            for key in spec.evidence
        ],
    }


def _document_text(document: Document) -> str:
    if document.file_name.endswith(".xlsx"):
        spec = json.loads(document.content)
        return "\n".join(
            "\t".join(str(value) for value in row)
            for sheet in spec["sheets"]
            for row in [[line] for line in sheet["preamble"]] + [sheet["header"]] + sheet["rows"]
        )
    return html.unescape(re.sub(r"<[^>]+>", "", document.content))


def evidence_registry(documents: Sequence[Document]) -> dict[str, tuple[str, str]]:
    """根拠の ID → （資料のファイル名・文）。文が資料の原稿に無ければ失敗する。"""
    registry: dict[str, tuple[str, str]] = {}
    for document in documents:
        text = normalize(_document_text(document))
        for key, sentence in document.evidence.items():
            if key in registry:
                raise ValueError(f"根拠の ID が重複しています: {key}")
            if normalize(sentence) not in text:
                raise ValueError(f"{document.file_name}: 根拠の文がありません: {sentence}")
            registry[key] = (document.file_name, sentence)
    return registry


def build_golden_set(documents: Sequence[Document]) -> dict[str, Any]:
    _check_constraints()
    registry = evidence_registry(documents)
    specs = [*ORIGINAL_CASES, *_new_cases()]
    if len({spec.id for spec in specs}) != len(specs):
        raise ValueError("ケースの ID が重複しています。")
    cases = [_case_payload(spec, registry) for spec in specs]
    referenced = {name for case in cases for name in case["relevant_document_ids"]}
    unused = [doc.file_name for doc in documents if f"file:{doc.file_name}" not in referenced]
    if unused:
        # 評価の CLI は評価セットが参照するファイルだけを取り込む。紛らわしい資料も 1 問は参照する。
        raise ValueError(f"どの問も参照しない資料があります: {unused}")
    return {"top_k": TOP_K, "cases": cases}


def source_path(corpus_dir: Path, document: Document) -> Path:
    suffix = ".workbook.json" if document.file_name.endswith(".xlsx") else ".html"
    return corpus_dir / SOURCES_DIRNAME / f"{document.source_name}{suffix}"


def write_sources(corpus_dir: Path) -> list[Path]:
    """原稿（sources/）と評価セット（multi-hop.json）を書き、書いたファイルを返す。"""
    documents = build_documents()
    golden_set = build_golden_set(documents)
    (corpus_dir / SOURCES_DIRNAME).mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for document in documents:
        path = source_path(corpus_dir, document)
        path.write_text(document.content, encoding="utf-8")
        written.append(path)
    golden = corpus_dir / GOLDEN_SET_NAME
    golden.write_text(
        json.dumps(golden_set, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written.append(golden)
    return written


def estimate_chunks(documents: Sequence[Document]) -> dict[str, int]:
    """資料ごとの子 chunk の数の見積もり（README と PR の根拠）。

    #1335 の評価（Docling の解析・親子階層の分割）では、PDF は前書きの段落と章（h2）ごとに 1 つ
    （章の本文は子の目標 1000 文字より短い）、Excel は表の行ごとに 1 つ（#1349 の 1 行 = 1 chunk）と
    前書きで 1 つになる。
    """
    counts: dict[str, int] = {}
    for document in documents:
        if document.file_name.endswith(".xlsx"):
            spec = json.loads(document.content)
            counts[document.file_name] = sum(
                len(sheet["rows"]) + (1 if sheet["preamble"] else 0) for sheet in spec["sheets"]
            )
        else:
            counts[document.file_name] = 1 + document.content.count("<h2>")
    return counts
