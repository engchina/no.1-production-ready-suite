"""NL2SQL パイプラインアダプター(Select AI 中核)。

各アダプターは **外部依存なし・決定論**でパイプライン挙動を束ねる(既存 RAG アダプターと同規約)。
本パッケージは scaffold として router / guardrail / cache を提供する。schema_linking / knowledge /
generation / correction / agentic / result / evaluation は後続で同規約に従い追加する。
"""
