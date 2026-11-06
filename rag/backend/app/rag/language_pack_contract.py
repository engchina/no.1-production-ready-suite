"""言語資源の保存契約の失敗。保存先やAPI層へ依存しない。"""


class LanguagePackNotInitializedError(LookupError):
    """状態行も公開履歴もない、新しい環境だけを表す。"""


class LanguagePackSnapshotError(ValueError):
    """保存済みの言語資源が現在の完全なスナップショット契約に合わない。"""
