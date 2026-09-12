"""業務別サンプルの固定レジストリ。SQL のパスや対象名は入力から組み立てない。"""

from dataclasses import dataclass

from .models import SampleDataset


@dataclass(frozen=True)
class SampleDatasetDefinition:
    directory: str
    confirmation: str
    tables: tuple[str, ...]
    views: tuple[str, ...]

    @property
    def objects(self) -> tuple[str, ...]:
        return self.tables + self.views


SAMPLE_DATASETS = {
    SampleDataset.HR: SampleDatasetDefinition(
        directory="sql_assist_sample",
        confirmation="SQL_ASSIST_SAMPLE",
        tables=("DEPARTMENT", "EMPLOYEE", "PROJECT"),
        views=("V_EMP_DEPT", "V_DEPT_PROJECT"),
    ),
    SampleDataset.SALES: SampleDatasetDefinition(
        directory="sales",
        confirmation="NL2SQL_SALES_SAMPLE",
        tables=(
            "SAMPLE_NL2SQL_SALES_CUSTOMER",
            "SAMPLE_NL2SQL_SALES_PRODUCT",
            "SAMPLE_NL2SQL_SALES_ORDER",
        ),
        views=("SAMPLE_NL2SQL_V_SALES_DETAIL",),
    ),
    SampleDataset.INQUIRIES: SampleDatasetDefinition(
        directory="inquiries",
        confirmation="NL2SQL_INQUIRY_SAMPLE",
        tables=(
            "SAMPLE_NL2SQL_INQUIRY_CUSTOMER",
            "SAMPLE_NL2SQL_INQUIRY_CATEGORY",
            "SAMPLE_NL2SQL_INQUIRY_TICKET",
        ),
        views=("SAMPLE_NL2SQL_V_INQUIRY_DETAIL",),
    ),
}
