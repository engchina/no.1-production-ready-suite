"""共通設定（pydantic-settings ベース）。"""

from .base_settings import BaseServiceSettings
from .env import (
    PLATFORM_ENV_FILE_ENV,
    PLATFORM_SETTING_FIELDS,
    PlatformEnvSourcesMixin,
    isolate_env_files_for_tests,
    platform_env_file,
    platform_env_name,
    product_env_file,
    product_env_name,
    product_settings_config,
    settings_env_name,
    settings_env_names,
)

__all__ = [
    "PLATFORM_ENV_FILE_ENV",
    "PLATFORM_SETTING_FIELDS",
    "BaseServiceSettings",
    "PlatformEnvSourcesMixin",
    "isolate_env_files_for_tests",
    "platform_env_file",
    "platform_env_name",
    "product_env_file",
    "product_env_name",
    "product_settings_config",
    "settings_env_name",
    "settings_env_names",
]
