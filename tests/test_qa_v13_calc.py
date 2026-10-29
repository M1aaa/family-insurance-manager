"""QA 独立验证 —— ``calc.allocate_amounts`` 边界攻击（v1.3）。

目标：证伪 / 证实工程师的分摊实现（ARCHITECTURE-v13-increment §3.6）。

- 不变式 ``Σ shares == money(total)`` 对 ``total ∈ {0, 1, 100, 999.99, 大值}``、
  ``n ∈ {1, 2, 3, 7, 100, 1000}`` 是否恒成立；
- ``n == 1`` 恒等；``n <= 0`` 返回 ``[]``；
- 尾差是否落在**最后一份**（由调用方保证最后一份 = 规范排序最后一名）；
- 负数 / 非法输入不崩溃（契约外，仅要求稳健）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.calc import allocate_amounts, money

TOTALS = ["0", "1", "100", "999.99", "123456789.99"]
NS = [1, 2, 3, 7, 100, 1000]


@pytest.mark.parametrize("total", TOTALS)
@pytest.mark.parametrize("n", NS)
def test_invariant_sum_equals_money_total(total: str, n: int) -> None:
    """★ 核心不变式：Σ shares == money(total)，且份数与类型正确。"""
    shares = allocate_amounts(Decimal(total), n)
    assert isinstance(shares, list)
    assert len(shares) == n
    assert all(isinstance(s, Decimal) for s in shares)
    assert sum(shares) == money(total)
    # 每份均为 2 位小数
    assert all(s == s.quantize(Decimal("0.01")) for s in shares)


@pytest.mark.parametrize("total", TOTALS)
def test_n1_is_identity(total: str) -> None:
    """``n == 1`` 恒等返回 ``[money(total)]``。"""
    assert allocate_amounts(Decimal(total), 1) == [money(total)]


def test_n_non_positive_returns_empty() -> None:
    """``n <= 0`` → ``[]``（不抛异常）。"""
    assert allocate_amounts(Decimal("100"), 0) == []
    assert allocate_amounts(Decimal("100"), -3) == []


def test_remainder_goes_to_last_share() -> None:
    """尾差归**最后一份**（1000/3 → 333.33/333.33/333.34）。"""
    assert allocate_amounts(Decimal("1000"), 3) == [
        Decimal("333.33"),
        Decimal("333.33"),
        Decimal("333.34"),
    ]


def test_remainder_goes_to_last_share_7() -> None:
    """7 份：100/7 → 前 6 份 14.29，末份吸收尾差 14.26。"""
    shares = allocate_amounts(Decimal("100"), 7)
    assert shares[:6] == [Decimal("14.29")] * 6
    assert shares[6] == Decimal("14.26")
    assert sum(shares) == Decimal("100.00")


def test_zero_total_all_zero() -> None:
    assert allocate_amounts(Decimal("0"), 5) == [Decimal("0.00")] * 5


def test_negative_total_does_not_crash_and_conserves() -> None:
    """契约外输入（负 total）：不崩溃，且仍满足 Σ == total（稳健性）。"""
    shares = allocate_amounts(Decimal("-100"), 3)
    assert sum(shares) == money(Decimal("-100"))


def test_int_and_str_inputs_normalized() -> None:
    """int / str 输入被规整为 Decimal（2 位小数）。"""
    assert allocate_amounts(100, 2) == [Decimal("50.00"), Decimal("50.00")]
    assert allocate_amounts("999.99", 3) == [
        Decimal("333.33"),
        Decimal("333.33"),
        Decimal("333.33"),
    ]
