"""source profile に基づく解析の profile。"""

from app.schemas.document import SourceProfile

DEFAULT_PARSER_PROFILE = "enterprise_ai_generic"


def parser_profile_for_source(source_profile: SourceProfile | None) -> str:
    """取込の品質の記録に残す解析の profile(source profile が無ければ汎用)。"""
    return source_profile.parser_profile if source_profile is not None else DEFAULT_PARSER_PROFILE
