"""v1.7 新增指标「当年未缴费金额」``yearUnpaidPremium`` 单测。

覆盖：

- 算例 A / B 的 ``yearUnpaidPremium`` 期望值（唯一真源见
  ``tests/conftest.py`` 的 ``seed_example_a`` / ``seed_example_b``）；
- 既有指标在**默认全计入**下**逐值不变**（硬红线）；
- ``yearUnpaidPremium`` 为 JSON **number**（原始 JSON 无引号）；
- ``calc.enumerate_year_periods`` 的边界（趸交 / 已退保 / 期次数）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import calc
from app.enums import (
    CoverageTermType,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)

TODAY = date(2025, 5, 20)
TODAY_STR = "2025-05-20"


def _dashboard(client, today: str = TODAY_STR) -> dict:
    resp = client.get("/api/stats/dashboard", params={"today": today})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 算例 A
# ---------------------------------------------------------------------------
def test_example_a_year_unpaid_and_existing_metrics(client, seed_example_a) -> None:
    """算例 A：``yearUnpaidPremium == 14500.00``，且既有指标逐值不变。"""
    overview = _dashboard(client)["overview"]

    # ★ 新指标（唯一真源：p_A 7×500 + p_B 8000 + p_C 3000）
    assert overview["yearUnpaidPremium"] == 14500.00

    # 既有指标 —— 硬红线：逐值不变
    assert overview["annualTotalPremium"] == 17000.00
    assert overview["unpaidAnnualPremium"] == 17000.00
    assert overview["upcoming60Premium"] == 4000.00
    assert overview["upcoming60Count"] == 3
    assert overview["policyTotal"] == 4
    assert overview["activePolicyCount"] == 3
    assert overview["insuredPersonCount"] == 3
    assert overview["overduePolicyCount"] == 0


def test_example_a_by_insured_unchanged(client, seed_example_a) -> None:
    """算例 A：``byInsured`` 年化分布逐值不变。"""
    dash = _dashboard(client)
    by_insured = {r["name"]: r["annualPremium"] for r in dash["byMember"]["byInsured"]}
    assert by_insured == {"张三": 6000.00, "李四": 8000.00, "小张": 3000.00}


# ---------------------------------------------------------------------------
# 算例 B
# ---------------------------------------------------------------------------
def test_example_b_year_unpaid_and_existing_metrics(client, seed_example_b) -> None:
    """算例 B：``yearUnpaidPremium == 1000.00``，且既有指标逐值不变。"""
    overview = _dashboard(client)["overview"]

    assert overview["yearUnpaidPremium"] == 1000.00

    assert overview["annualTotalPremium"] == 14400.00
    assert overview["unpaidAnnualPremium"] == 2400.00
    assert overview["upcoming60Premium"] == 0.00
    assert overview["upcoming60Count"] == 0
    assert overview["policyTotal"] == 3
    assert overview["activePolicyCount"] == 3
    assert overview["insuredPersonCount"] == 2


def test_example_b_by_policyholder_unchanged(client, seed_example_b) -> None:
    dash = _dashboard(client)
    by_holder = {r["name"]: r["annualPremium"] for r in dash["byMember"]["byPolicyholder"]}
    assert by_holder == {"张三": 2400.00, "王五": 12000.00}


# ---------------------------------------------------------------------------
# JSON number 断言（Pydantic v2 Decimal 输出坑）
# ---------------------------------------------------------------------------
def test_year_unpaid_is_json_number(client, seed_example_a) -> None:
    resp = client.get("/api/stats/dashboard", params={"today": TODAY_STR})
    raw = resp.text
    assert '"yearUnpaidPremium":' in raw
    # 若是字符串会形如 "yearUnpaidPremium":"14500.00"
    assert '"yearUnpaidPremium":"' not in raw, "yearUnpaidPremium 被序列化为字符串！"
    assert '"annualTotalPremium":"' not in raw
    assert '"unpaidAnnualPremium":"' not in raw


# ---------------------------------------------------------------------------
# calc.enumerate_year_periods 单测
# ---------------------------------------------------------------------------
def _make_policy(policy_factory, member, **over):
    base = dict(
        id=over.pop("id", "p_x"),
        insurance_type=over.pop("insurance_type", InsuranceType.critical_illness),
        policyholder_id=over.pop("policyholder_id", member.id),
        insured_id=member.id,
        effective_date=over.pop("effective_date", date(2020, 3, 15)),
        coverage_term_type=over.pop("coverage_term_type", CoverageTermType.whole_life),
        coverage_term_value=over.pop("coverage_term_value", None),
        payment_term_type=over.pop("payment_term_type", PaymentTermType.fixed_years),
        payment_term_value=over.pop("payment_term_value", 20),
        payment_frequency=over.pop("payment_frequency", PaymentFrequency.monthly),
        premium_per_period=over.pop("premium_per_period", "500"),
        status=over.pop("status", PolicyStatus.active),
    )
    base.update(over)
    return policy_factory(**base)


def test_enumerate_year_periods_pa_returns_7(member_factory, policy_factory) -> None:
    """p_A：月缴 500、2020-03 生效、已缴 63 → 2025 年未缴 k=63..69 共 7 期。"""
    member = member_factory(
        id="m1", name="张三", relation=Relation.self_,
        birth_date=date(1988, 6, 1),
    )
    policy = _make_policy(
        policy_factory, member, id="p_A",
        effective_date=date(2020, 3, 15), payment_term_value=20,
        premium_per_period="500", payment_frequency=PaymentFrequency.monthly,
    )
    items = calc.enumerate_year_periods(policy, member, TODAY)
    assert len(items) == 7
    assert items[0].due_date == date(2025, 6, 15)
    assert items[-1].due_date == date(2025, 12, 15)
    assert all(i.amount == Decimal("500.00") for i in items)
    assert sum(i.amount for i in items) == Decimal("3500.00")


def test_enumerate_year_periods_single_old_policy_returns_empty(
    member_factory, policy_factory
) -> None:
    """趸交、2019 生效 → 本年度（2025）无期次 → []。"""
    member = member_factory(
        id="m1", name="张三",
        relation=Relation.self_,
        birth_date=date(1988, 6, 1),
    )
    policy = _make_policy(
        policy_factory, member, id="p_D",
        effective_date=date(2019, 1, 1),
        payment_term_type=PaymentTermType.single, payment_term_value=None,
        premium_per_period="50000", status=PolicyStatus.paid_off,
    )
    assert calc.enumerate_year_periods(policy, member, TODAY) == []


def test_enumerate_year_periods_surrendered_returns_empty(
    member_factory, policy_factory
) -> None:
    """已退保 → []。"""
    member = member_factory(
        id="m1", name="张三",
        relation=Relation.self_,
        birth_date=date(1988, 6, 1),
    )
    policy = _make_policy(
        policy_factory, member, id="p_X",
        effective_date=date(2020, 1, 1), payment_term_value=10,
        premium_per_period="100", status=PolicyStatus.surrendered,
    )
    assert calc.enumerate_year_periods(policy, member, TODAY) == []


def test_enumerate_year_periods_future_effective_counts_this_year(
    member_factory, policy_factory
) -> None:
    """生效日在未来（2025-08-01）→ 计入本年度 8~12 月共 5 期。"""
    member = member_factory(
        id="m1", name="张三",
        relation=Relation.self_,
        birth_date=date(1988, 6, 1),
    )
    policy = _make_policy(
        policy_factory, member, id="p_E",
        effective_date=date(2025, 8, 1), payment_term_value=10,
        premium_per_period="200", payment_frequency=PaymentFrequency.monthly,
    )
    items = calc.enumerate_year_periods(policy, member, TODAY)
    assert len(items) == 5
    assert items[0].due_date == date(2025, 8, 1)
    assert items[-1].due_date == date(2025, 12, 1)
    assert sum(i.amount for i in items) == Decimal("1000.00")


def test_enumerate_year_periods_explicit_year_arg(member_factory, policy_factory) -> None:
    """显式 year 参数：仅改变「匹配的年份」，``paid_periods`` 仍由 ``today`` 反推。

    p_A（today=2025-05-20，已缴 63）：
    - ``year=2026`` → k=70..81（2026-01 ~ 2026-12，均 >= 63）共 12 期；
    - ``year=2020`` → 2020 年期次 k=0..9 均 < 63（已缴）→ 0 期。
    """
    member = member_factory(
        id="m1", name="张三",
        relation=Relation.self_,
        birth_date=date(1988, 6, 1),
    )
    policy = _make_policy(
        policy_factory, member, id="p_A",
        effective_date=date(2020, 3, 15), payment_term_value=20,
        premium_per_period="500", payment_frequency=PaymentFrequency.monthly,
    )
    items = calc.enumerate_year_periods(policy, member, TODAY, year=2026)
    assert len(items) == 12
    assert items[0].due_date == date(2026, 1, 15)
    assert items[-1].due_date == date(2026, 12, 15)

    # 生效当年（2020）的期次早已标记已缴 → 无未缴期次
    assert calc.enumerate_year_periods(policy, member, TODAY, year=2020) == []
