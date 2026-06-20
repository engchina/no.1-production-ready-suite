"""NL2SQL Router アダプター(profile/複雑度ルーティング)のテスト。"""

from app.config import Settings
from app.nl2sql.router import (
    ROUTER_PROFILE_ORDER,
    KeywordDomainClassifier,
    complexity_signals,
    normalize_router_profile,
    route,
    router_adapter_runtime_settings,
)


def test_off_uses_default_backend_without_routing() -> None:
    d = route(Settings(nl2sql_router_profile="off"), "部門ごとの平均給与を教えて")
    assert d.profile_selected is None
    assert d.generation_backend == "select_ai_agent"  # 設定既定
    assert d.reason == "router_off"


def test_complexity_signals_are_deterministic() -> None:
    sig = complexity_signals("部門ごとの平均給与を教えて")
    assert "aggregate" in sig and "grouping" in sig
    assert sig == complexity_signals("部門ごとの平均給与を教えて")


def test_complexity_aware_routes_complex_to_multi_stage() -> None:
    settings = Settings(
        nl2sql_router_profile="complexity_aware", nl2sql_router_complexity_threshold=2
    )
    complex_d = route(settings, "部門ごとの平均給与を高い順に並べて")
    assert complex_d.complexity_score >= 2
    assert complex_d.generation_backend == "select_ai_agent"


def test_complexity_aware_routes_simple_to_single_stage() -> None:
    settings = Settings(
        nl2sql_router_profile="complexity_aware", nl2sql_router_complexity_threshold=2
    )
    simple_d = route(settings, "社員一覧を見せて")
    assert simple_d.complexity_score < 2
    assert simple_d.generation_backend == "select_ai"


def test_classifier_selects_profile() -> None:
    settings = Settings(nl2sql_router_profile="classifier")
    classifier = KeywordDomainClassifier(
        rules=(("hr", ("社員", "部門", "給与")), ("sales", ("売上", "受注"))),
        default_domain="default",
    )
    mapping = {"hr": "N2SPR_HR", "sales": "N2SPR_SALES", "default": "N2SPR_DEF"}
    d = route(settings, "社員の給与一覧", classifier=classifier, domain_to_profile=mapping)
    assert d.profile_selected == "N2SPR_HR"
    assert d.used_classifier is True
    assert d.reason == "classifier_selected"


def test_classifier_without_model_is_unavailable() -> None:
    d = route(Settings(nl2sql_router_profile="classifier"), "社員の給与一覧")
    assert d.profile_selected is None
    assert d.reason == "classifier_unavailable"


def test_normalize_and_runtime_settings() -> None:
    assert normalize_router_profile("xxx") == "off"
    assert normalize_router_profile("classifier") == "classifier"
    runtime = router_adapter_runtime_settings(Settings(nl2sql_router_profile="complexity_aware"))
    assert tuple(s.name for s in runtime.profiles) == ROUTER_PROFILE_ORDER
    assert [s.name for s in runtime.profiles if s.selected] == ["complexity_aware"]
    assert runtime.default_generation_backend == "select_ai_agent"
