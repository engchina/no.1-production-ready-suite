"""Oracle の変換エラー既定値を、省略せず AST に保持する dialect。"""

from __future__ import annotations

from typing import cast

from sqlglot import exp
from sqlglot.dialects.oracle import Oracle
from sqlglot.generators.oracle import OracleGenerator
from sqlglot.parsers.oracle import OracleParser
from sqlglot.tokenizer_core import TokenType


class ConversionDefault(exp.Expression):
    arg_types = {"this": True, "default": True}


class OracleConversion(Oracle):
    class Parser(OracleParser):
        FUNCTION_PARSERS = {
            **Oracle.Parser.FUNCTION_PARSERS,
            "TO_DATE": lambda self: self._parse_date_conversion(),
        }

        def _parse_date_conversion(self) -> exp.Expression:
            value = self._parse_assignment()
            if value is None:
                self.raise_error("Expected TO_DATE input")
            if self._match(TokenType.DEFAULT):
                fallback = self._parse_assignment()
                if fallback is None or not self._match_text_seq("ON", "CONVERSION", "ERROR"):
                    self.raise_error("Expected DEFAULT expression ON CONVERSION ERROR")
                value = self.expression(ConversionDefault(this=value, default=fallback))
            args = [value]
            while self._match(TokenType.COMMA):
                argument = self._parse_assignment()
                if argument is None:
                    self.raise_error("Expected TO_DATE argument")
                args.append(argument)
            if len(args) > 3:
                self.raise_error("TO_DATE accepts at most three arguments")
            if not isinstance(value, ConversionDefault):
                builder = self.FUNCTIONS["TO_DATE"]
                try:
                    return cast(exp.Expression, self.validate_expression(builder(args), args))
                except TypeError:
                    return cast(
                        exp.Expression,
                        self.validate_expression(builder(args, dialect=self.dialect), args),
                    )
            # Anonymous は引数の子ノードを保つ。既定値の関数・列も通常の安全検査の対象になる。
            return self.expression(exp.Anonymous(this="TO_DATE", expressions=args))

    class Generator(OracleGenerator):
        TRANSFORMS = {
            **Oracle.Generator.TRANSFORMS,
            ConversionDefault: lambda self, expression: (
                f"{self.sql(expression, 'this')} DEFAULT "
                f"{self.sql(expression, 'default')} ON CONVERSION ERROR"
            ),
        }


def has_grouping_suffix(expression: exp.Expr) -> bool:
    """他方言の GROUP BY 後置句を AST で判定する。"""
    return any(
        group.args.get(key)
        for group in expression.find_all(exp.Group)
        for key in ("rollup", "cube")
    )


def normalize_generated_grouping(sql: str) -> str:
    """生成 SQL の後置 ROLLUP/CUBE を、同じ集約集合の Oracle 構文にする。"""
    import sqlglot
    from sqlglot.errors import ParseError

    try:
        statements = sqlglot.parse(sql, read=OracleConversion)
    except (ParseError, ValueError, TypeError):
        return sql
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return sql
    root = statements[0]
    changed = False
    for group in root.find_all(exp.Group):
        suffixes: list[tuple[str, list[exp.Expression]]] = [
            (key, cast(list[exp.Expression], group.args.get(key)))
            for key in ("rollup", "cube")
            if group.args.get(key)
        ]
        if not suffixes:
            continue
        # 複数の拡張を組み合わせた曖昧な形は変換せず、安全検査で拒否する。
        if len(suffixes) != 1 or not group.expressions:
            return sql
        key, values = suffixes[0]
        if len(values) != 1 or values[0].expressions or group.args.get("grouping_sets"):
            return sql
        grouping = exp.Rollup if key == "rollup" else exp.Cube
        group.set(
            "expressions", [grouping(expressions=[item.copy() for item in group.expressions])]
        )
        group.set(key, None)
        changed = True
    return root.sql(dialect=OracleConversion) if changed else sql
