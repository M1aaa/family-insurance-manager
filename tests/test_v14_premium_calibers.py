"""v1.4 看板保费「双口径」测试。

口径（唯一真源，见 ``docs/ARCHITECTURE.md`` §5.5 / ``docs/ARCHITECTURE-v13-increment.md`` §3.7）：

- ``annualTotalPremium``（**当年应缴总保费**）= Σ ``sched.annualized_premium``，
  ``status ∈ {生效中, 已缴清}``（含已缴清保单、含已缴完的生效中保单）；
- ``unpaidAnnualPremium``（**当年未缴费金额**）= Σ ``sched.annualized_premium``，
  ``status == 生效中`` 且 ``is_paid_off == False``（**= v1.3 旧「年度总保费」，逐值不变**）；
- 不变式 1（口径差 = 已缴部分）::

      annualTotalPremium - unpaidAnnualPremium
        == Σ annualized where status == 已缴清 or (status == 生效中 and is_paid_off)

- 不变式 2（分摊守恒）::

      Σ(byInsured.annualPremium) == unpaidAnnualPremium   # 注意：不是 annualTotalPremium
"""

from __future__ import annotations

from datetime import date

from app.enums import (
    CoverageTermType,
    Gender,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)

TODAY = "2025-05-20"


def _by_name(rows):
    return {r["name"]: r for r in rows}


# ---------------------------------------------------------------------------
# 算例集 A / B：旧值 → 未缴费金额；新值 → 应缴总保费
# ---------------------------------------------------------------------------
def test_example_a_calibers(client, seed_example_a):
    """算例 A：未缴费 17000（旧值）；应缴总保费 17000（p_D 趸交年化 0 → 差 0）。"""
    ov = client.get("/api/stats/dashboard", params={"today": TODAY}).json()["overview"]
    assert ov["unpaidAnnualPremium"] == 17000.00
    assert ov["annualTotalPremium"] == 17000.00
    assert ov["annualTotalPremium"] - ov["unpaidAnnualPremium"] == 0.00


def test_example_b_calibers(client, seed_example_b):
    """算例 B：未缴费 2400（旧值）；应缴总保费 14400（+ p_F 已缴清年化 12000）。"""
    ov = client.get("/api/stats/dashboard", params={"today": TODAY}).json()["overview"]
    assert ov["unpaidAnnualPremium"] == 2400.00
    assert ov["annualTotalPremium"] == 14400.00
    assert ov["annualTotalPremium"] - ov["unpaidAnnualPremium"] == 12000.00


# ---------------------------------------------------------------------------
# 不变式 2：Σ(byInsured.annualPremium) == unpaidAnnualPremium
# ---------------------------------------------------------------------------
def test_invariant_sum_by_insured_equals_unpaid_a(client, seed_example_a):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    total = sum(r["annualPremium"] for r in data["byMember"]["byInsured"])
    assert total == data["overview"]["unpaidAnnualPremium"] == 17000.00


def test_invariant_sum_by_insured_equals_unpaid_b(client, seed_example_b):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    # v1.6：维度年化口径已放宽为与主卡片一致 → 对账的是 annualTotalPremium
    total = sum(r["annualPremium"] for r in data["byMember"]["byInsured"])
    assert total == data["overview"]["annualTotalPremium"] == 14400.00


# ---------------------------------------------------------------------------
# 构造用例：已缴清保单（非趸交）确实推高应缴总保费
# ---------------------------------------------------------------------------
def test_paid_off_policy_increases_annual_total(
    client, db_session, member_factory, policy_factory
):
    """生效中年缴 3000 + 已缴清年缴 2000 → 应缴 5000、未缴 3000、差 2000。"""
    member_factory(id="m1", name="甲", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="乙", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    policy_factory(
        id="p1", insurance_type=InsuranceType.life, policyholder_id="m1", insured_id="m1",
        effective_date=date(2025, 1, 1), coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None, payment_term_type=PaymentTermType.fixed_years, payment_term_value=10,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="3000", status=PolicyStatus.active,
    )
    # 2020 生效、缴 5 期 → 2025-05-20 时已缴满 → is_paid_off True；年化 2000（非趸交）
    policy_factory(
        id="p2", insurance_type=InsuranceType.critical_illness, policyholder_id="m1", insured_id="m2",
        effective_date=date(2020, 1, 1), coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None, payment_term_type=PaymentTermType.fixed_years, payment_term_value=5,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="2000", status=PolicyStatus.paid_off,
    )
    db_session.commit()

    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    ov = data["overview"]
    assert ov["unpaidAnnualPremium"] == 3000.00
    assert ov["annualTotalPremium"] == 5000.00
    assert ov["annualTotalPremium"] - ov["unpaidAnnualPremium"] == 2000.00
    # 分摊守恒（v1.6）：Σ(byInsured.annualPremium) == 当年应缴总保费（含已缴清的 p2）
    assert sum(r["annualPremium"] for r in data["byMember"]["byInsured"]) == 5000.00
    # byType 口径同主卡片（v1.6 起含已缴清），故为 5000 而非 3000
    assert sum(r["annualPremium"] for r in data["byType"]) == 5000.00
