"""pgvector's `vector(n)` column type, for SQLAlchemy and asyncpg.

Without the `pgvector` package, which would bring numpy along for one
column type. Values travel in pgvector's text form ("[0.1,0.2,...]"): bound
as text and cast in SQL, read back through a text cast. asyncpg has no codec
for an extension's type, and needs none this way.
"""

from collections.abc import Callable, Sequence
from typing import Any

from sqlalchemy import ColumnElement, Text, cast
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.engine import Dialect
from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim

    def get_col_spec(self, **kw: Any) -> str:
        return "vector" if self.dim is None else f"vector({self.dim})"

    def bind_processor(self, dialect: Dialect) -> Callable[[Any], str | None]:
        def process(value: Sequence[float] | None) -> str | None:
            return None if value is None else to_text(value)

        return process

    def bind_expression(self, bindvalue: Any) -> ColumnElement[list[float]]:
        return cast(cast(bindvalue, Text), self)

    def column_expression(self, colexpr: Any) -> ColumnElement[Any]:
        # Text on the wire; result_processor turns it into floats.
        return cast(colexpr, Text)

    def result_processor(
        self, dialect: Dialect, coltype: object
    ) -> Callable[[Any], list[float] | None]:
        def process(value: str | None) -> list[float] | None:
            if value is None:
                return None
            inner = value.strip("[]")
            return [float(v) for v in inner.split(",")] if inner else []

        return process


def to_text(values: Sequence[float]) -> str:
    """pgvector stores float32: 7 significant digits lose nothing."""
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"


# Reflection (Alembic's `alembic check`) then recognises the column's type.
ischema_names["vector"] = Vector
