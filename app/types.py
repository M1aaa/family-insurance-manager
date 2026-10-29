"""自定义 SQLAlchemy 列类型。

``Money`` 以「整数分」在 SQLite 中存储金额，读写时自动在 ``Decimal`` 与
「分」之间转换，从而彻底规避 SQLite 的浮点精度问题。
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator


class Money(TypeDecorator):
    """以整数「分」存储的金额类型，读写自动在 Decimal 与分之间转换。"""

    impl = Integer
    cache_ok = True
    EXP = Decimal("0.01")

    def process_bind_param(self, value, dialect):  # noqa: ANN001 - 遵循 SQLAlchemy 接口
        """写入数据库：Decimal/数值 → 整数分。"""
        if value is None:
            return None
        return int((Decimal(str(value)).quantize(self.EXP, ROUND_HALF_UP) * 100))

    def process_result_value(self, value, dialect):  # noqa: ANN001 - 遵循 SQLAlchemy 接口
        """从数据库读取：整数分 → Decimal（2 位小数）。"""
        if value is None:
            return None
        return (Decimal(value) / 100).quantize(self.EXP, ROUND_HALF_UP)
