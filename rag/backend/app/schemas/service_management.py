"""サービス管理(前処理 / Parser マイクロサービスの稼働可視化・起動/停止)スキーマ。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.catalog import ServiceCategory, ServiceExecutionPolicy, ServiceProfile
from app.services.status import ServiceRuntimeStatus

DeploymentMode = Literal["dev", "prod"]
ServiceLogsSource = Literal["docker"]


class ServiceModelCacheData(BaseModel):
    """モデル DL を行うサービスのキャッシュマウント情報(dev・読み取り専用表示)。"""

    container_path: str = Field(description="コンテナ内 HF キャッシュ実体パス。")
    volume_name: str = Field(
        description="dev で mount する Docker Compose named volume の論理名。",
    )
    editable: Literal[False] = Field(
        default=False,
        description="volume 名と mount 先は固定。UI からは編集不可。",
    )


class ServiceCatalogItemData(BaseModel):
    """1 マイクロサービスの非機密カタログ情報(稼働プローブなし)。"""

    service_id: str
    category: ServiceCategory
    profile: ServiceProfile
    label_key: str
    execution_policy: ServiceExecutionPolicy = Field(
        description="停止時・未使用時の runtime 契約。fallback 境界の UI 表示に使う。",
    )
    deployable: bool = Field(
        default=True,
        description=(
            "UI/API からデプロイ操作を提供するか。False は backend 内処理で動作し"
            "(status=in_process)、起動/停止等の操作系を出さない(サービス化は将来対応)。"
        ),
    )
    configured: bool = Field(
        description="base URL が設定済みか(未設定なら status=unconfigured)。",
    )
    model_cache: ServiceModelCacheData | None = Field(
        default=None,
        description="モデル DL を行うサービスのキャッシュマウント情報。なければ None。",
    )


class ServiceStatusData(ServiceCatalogItemData):
    """1 マイクロサービスの非機密ステータス。"""

    status: ServiceRuntimeStatus


class ServiceCatalogData(BaseModel):
    """サービス一覧の静的メタデータ + 制御可否 + 配備モード。"""

    control_enabled: bool = Field(
        description="起動/停止制御が有効か。False なら可視化のみ。dev は自動的に有効。",
    )
    deployment_mode: DeploymentMode = Field(
        description="dev は uv プロセス起動、prod は docker compose 制御。ENVIRONMENT 由来。",
    )
    services: list[ServiceCatalogItemData] = Field(default_factory=list)


class ServiceListData(BaseModel):
    """サービス一覧 + 制御可否 + 配備モード。"""

    control_enabled: bool = Field(
        description="起動/停止制御が有効か。False なら可視化のみ。dev は自動的に有効。",
    )
    deployment_mode: DeploymentMode = Field(
        description="dev は uv プロセス起動、prod は docker compose 制御。ENVIRONMENT 由来。",
    )
    services: list[ServiceStatusData] = Field(default_factory=list)


class ServiceControlResultData(BaseModel):
    """起動/停止実行後の結果(更新後ステータス込み)。"""

    service_id: str
    action: str
    status: ServiceRuntimeStatus


class ServiceLogsData(BaseModel):
    """サービスログの末尾。"""

    service_id: str
    source: ServiceLogsSource
    lines: int = Field(description="取得したログ末尾の最大行数。")
    content: str = Field(description="ログ本文。ログが無い場合は空文字。")
