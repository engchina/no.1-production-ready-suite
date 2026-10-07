"""Wallet の別名から再試行の設定を外した接続記述子（#1212）。"""

from __future__ import annotations

from pathlib import Path

from pr_backend_core.oracle_dsn import (
    dsn_without_tns_retry,
    strip_tns_retry_settings,
    tns_alias_descriptor,
)

_HIGH = (
    "(description= (retry_count=20)(retry_delay=3)(address=(protocol=tcps)(port=1522)"
    "(host=adb.ap-osaka-1.oraclecloud.com))(connect_data=(service_name=x_mydb_high.adb.oraclecloud.com))"
    "(security=(ssl_server_dn_match=yes)))"
)


def _wallet(tmp_path: Path) -> Path:
    (tmp_path / "tnsnames.ora").write_text(
        f"mydb_high = {_HIGH}\n\nmydb_low = (description=(retry_count=5)(address=(host=h)))\n",
        encoding="utf-8",
    )
    return tmp_path


def test_alias_is_replaced_by_the_descriptor_without_retry(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path)

    dsn = dsn_without_tns_retry("MYDB_HIGH", wallet)

    assert "retry_count" not in dsn
    assert "retry_delay" not in dsn
    assert "service_name=x_mydb_high.adb.oraclecloud.com" in dsn
    assert "(security=(ssl_server_dn_match=yes))" in dsn
    assert dsn.count("(") == dsn.count(")")


def test_other_dsns_are_kept(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path)

    assert dsn_without_tns_retry("missing_alias", wallet) == "missing_alias"
    assert dsn_without_tns_retry("host:1521/service", None) == "host:1521/service"
    assert dsn_without_tns_retry("mydb_high", "") == "mydb_high"
    assert dsn_without_tns_retry("mydb_high", tmp_path / "no-wallet") == "mydb_high"


def test_descriptor_and_strip_helpers(tmp_path: Path) -> None:
    wallet = _wallet(tmp_path)

    assert tns_alias_descriptor(wallet, "mydb_high") == _HIGH
    assert (
        tns_alias_descriptor(wallet, "mydb_low")
        == "(description=(retry_count=5)(address=(host=h)))"
    )
    assert strip_tns_retry_settings("(a=(RETRY_COUNT = 2)( retry_delay=1 )(b=1))") == "(a=(b=1))"
