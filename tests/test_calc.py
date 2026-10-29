"""核心算法单测（ARCHITECTURE 5.6 算例集 A / B 逐值断言）。

本文件不依赖数据库：直接实例化未持久化的 ORM 对象进行纯函数计算。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.calc import (
    add_months,
    add_years,
    age_years,
    calc_schedule,
    compute_k_max,
    enumerate_upcoming,
    money,
    pct,
)
from app.enums import (
    CoverageTermType,
    Gender,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)
from app.models import Member, Policy

TODAY_A = date(2025, 5, 20)


# ---------------------------------------------------------------------------
# 构造辅助（不落库）
# ---------------------------------------------------------------------------
def _member(id: str, name: str, relation: Relation, birth: date) -> Member:
    return Member(id=id, name=name, relation=relation, birth_date=birth, gender=Gender.unknown)


def _policy(
    id: str,
    itype: InsuranceType,
    holder: str,
    insured: str,
    eff: date,
    ctt: CoverageTermType,
    ctv: int | None,
    ptt: PaymentTermType,
    ptv: int | None,
    freq: PaymentFrequency,
    premium: str,
    status: PolicyStatus,
) -> Policy:
    return Policy(
        id=id,
        company="测试",
        insurance_type=itype,
        product_name="测试产品",
        policyholder_id=holder,
        insured_id=insured,
        effective_date=eff,
        coverage_term_type=ctt,
        coverage_term_value=ctv,
        payment_term_type=ptt,
        payment_term_value=ptv,
        payment_frequency=freq,
        premium_per_period=Decimal(premium),
        status=status,
    )


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def test_age_years():
    assert age_years(date(1988, 6, 1), date(2025, 5, 20)) == 36
    assert age_years(date(1988, 6, 1), date(2025, 6, 1)) == 37
    assert age_years(date(1960, 2, 10), date(2015, 3, 1)) == 55


def test_add_years_leap():
    assert add_years(date(2020, 2, 29), 1) == date(2021, 2, 28)
    assert add_years(date(2020, 3, 15), 5) == date(2025, 3, 15)


def test_add_months_month_end_overflow():
    # 月末溢出必须钳制到目标月最后一天
    assert add_months(date(2020, 1, 31), 1) == date(2020, 2, 29)  # 闰年 2 月
    assert add_months(date(2021, 1, 31), 1) == date(2021, 2, 28)  # 平年 2 月
    assert add_months(date(2021, 3, 31), 1) == date(2021, 4, 30)  # 30 天月
    assert add_months(date(2021, 5, 31), 1) == date(2021, 6, 30)
    assert add_months(date(2021, 8, 31), 1) == date(2021, 9, 30)
    assert add_months(date(2020, 2, 29), 12) == date(2021, 2, 28)
    assert add_months(date(2020, 3, 15), 63) == date(2025, 6, 15)
    assert add_months(date(2020, 3, 15), 0) == date(2020, 3, 15)


def test_money_and_pct():
    assert money("500") == Decimal("500.00")
    assert money(Decimal("26.255")) == Decimal("26.26")
    assert pct(63, 240) == Decimal("26.3")
    assert pct(0, 0) == Decimal("100.0")
    assert pct(1, 1) == Decimal("100.0")


def test_compute_k_max_future_effective():
    # 生效日在未来 → -1（0.4 修正）
    assert compute_k_max(date(2025, 8, 1), "月缴", TODAY_A) == -1
    assert compute_k_max(date(2025, 8, 1), "年缴", TODAY_A) == -1


# ---------------------------------------------------------------------------
# compute_k_max 边界测试（严格语义：k_max = max{ k >= 0 : due(k) < today }）
# ---------------------------------------------------------------------------
def test_compute_k_max_clamp_effective_jan31():
    """生效日 2020-01-31（月缴）：月末钳制 + 严格小于。"""
    eff = date(2020, 1, 31)
    # due(1) = 2020-02-29（闰年 2 月钳制）；today 恰为该日 → 不计入 → k_max = 0
    assert add_months(eff, 1) == date(2020, 2, 29)
    assert compute_k_max(eff, "月缴", date(2020, 2, 29)) == 0
    # today 恰为 due(2) = 2020-03-31 → 不计入 → k_max = 1
    assert add_months(eff, 2) == date(2020, 3, 31)
    assert compute_k_max(eff, "月缴", date(2020, 3, 31)) == 1
    # 晚一天 → due(2) 已到期 → k_max = 2
    assert compute_k_max(eff, "月缴", date(2020, 4, 1)) == 2
    # today 恰为 due(3) = 2020-04-30（30 天月钳制）→ 不计入 → k_max = 2
    assert add_months(eff, 3) == date(2020, 4, 30)
    assert compute_k_max(eff, "月缴", date(2020, 4, 30)) == 2
    # 与 due(k) 反推一致：due(63) = 2025-04-30 < today，due(64) = 2025-05-31 >= today
    assert add_months(eff, 63) == date(2025, 4, 30)
    assert add_months(eff, 64) == date(2025, 5, 31)
    assert compute_k_max(eff, "月缴", TODAY_A) == 63


def test_compute_k_max_effective_mar15():
    """生效日 2020-03-15（月缴）——算例集 A 保单 A 的生效日。"""
    eff = date(2020, 3, 15)
    assert compute_k_max(eff, "月缴", TODAY_A) == 62
    assert add_months(eff, 62) == date(2025, 5, 15)
    assert add_months(eff, 63) == date(2025, 6, 15)


def test_compute_k_max_effective_aug01_yearly():
    """生效日 2021-08-01（年缴）——算例集 A 保单 B 的生效日。"""
    eff = date(2021, 8, 1)
    assert compute_k_max(eff, "年缴", TODAY_A) == 3
    assert add_months(eff, 3 * 12) == date(2024, 8, 1)
    assert add_months(eff, 4 * 12) == date(2025, 8, 1)
    # 生效日 == today → -1；次日 → due(0) 已到期
    assert compute_k_max(eff, "年缴", date(2021, 8, 1)) == -1
    assert compute_k_max(eff, "年缴", date(2021, 8, 2)) == 0


def test_compute_k_max_exact_anniversary_excluded():
    """到期日恰为 today 的期次不计入已到期（严格小于，月缴 / 年缴一致）。"""
    eff = date(2020, 3, 15)
    # 月缴：today == due(1) = 2020-04-15 → k_max = 0；晚一天 → 1
    assert compute_k_max(eff, "月缴", date(2020, 4, 15)) == 0
    assert compute_k_max(eff, "月缴", date(2020, 4, 16)) == 1
    # 年缴：today == due(1) = 2021-03-15 → k_max = 0；晚一天 → 1
    assert compute_k_max(eff, "年缴", date(2021, 3, 15)) == 0
    assert compute_k_max(eff, "年缴", date(2021, 3, 16)) == 1


def test_compute_k_max_effective_equals_today():
    """生效日 == today → k_max = -1（0.4 修正，已到期期数 0）。"""
    today = date(2025, 5, 20)
    assert compute_k_max(today, "月缴", today) == -1
    assert compute_k_max(today, "年缴", today) == -1
    # 生效日在未来同样为 -1
    assert compute_k_max(date(2025, 5, 21), "月缴", today) == -1


def test_single_pay_strict_boundary():
    """趸交：生效日 < today 才计 1 期；生效日 == today 计 0 期。"""
    m1 = _member("m1", "张三", Relation.self_, date(1988, 6, 1))
    today = date(2025, 5, 20)
    past = _policy("p1", InsuranceType.life, "m1", "m1", date(2025, 5, 19),
                   CoverageTermType.whole_life, None, PaymentTermType.single, None,
                   PaymentFrequency.monthly, "50000", PolicyStatus.active)
    same = _policy("p2", InsuranceType.life, "m1", "m1", today,
                   CoverageTermType.whole_life, None, PaymentTermType.single, None,
                   PaymentFrequency.monthly, "50000", PolicyStatus.active)
    s_past = calc_schedule(past, m1, today)
    s_same = calc_schedule(same, m1, today)
    assert (s_past.k_max, s_past.paid_periods, s_past.is_paid_off) == (0, 1, True)
    assert (s_same.k_max, s_same.paid_periods, s_same.is_paid_off) == (-1, 0, False)
    # 生效日 == today 的趸交：当天待缴，下次缴费日 = 生效日
    assert s_same.next_due_date == today


# ---------------------------------------------------------------------------
# 算例集 A（today = 2025-05-20）
# ---------------------------------------------------------------------------
@pytest.fixture()
def example_a():
    m1 = _member("m1", "张三", Relation.self_, date(1988, 6, 1))
    m2 = _member("m2", "李四", Relation.spouse, date(1990, 3, 12))
    m3 = _member("m3", "小张", Relation.child, date(2015, 9, 20))
    a = _policy("p_A", InsuranceType.critical_illness, "m1", "m1", date(2020, 3, 15),
                CoverageTermType.whole_life, None, PaymentTermType.fixed_years, 20,
                PaymentFrequency.monthly, "500", PolicyStatus.active)
    b = _policy("p_B", InsuranceType.medical, "m1", "m2", date(2021, 8, 1),
                CoverageTermType.fixed_years, 30, PaymentTermType.fixed_years, 10,
                PaymentFrequency.yearly, "8000", PolicyStatus.active)
    c = _policy("p_C", InsuranceType.accident, "m1", "m3", date(2024, 6, 1),
                CoverageTermType.fixed_years, 1, PaymentTermType.fixed_years, 20,
                PaymentFrequency.yearly, "3000", PolicyStatus.active)
    d = _policy("p_D", InsuranceType.critical_illness, "m1", "m1", date(2019, 1, 1),
                CoverageTermType.whole_life, None, PaymentTermType.single, None,
                PaymentFrequency.monthly, "50000", PolicyStatus.paid_off)
    return {"m1": m1, "m2": m2, "m3": m3, "A": a, "B": b, "C": c, "D": d}


def test_example_a_schedules(example_a):
    members = example_a
    sched = {
        key: calc_schedule(members[key], members[{"A": "m1", "B": "m2", "C": "m3", "D": "m1"}[key]], TODAY_A)
        for key in ("A", "B", "C", "D")
    }

    # A
    sa = sched["A"]
    assert sa.annualized_premium == Decimal("6000.00")
    assert sa.total_periods == 240
    assert sa.k_max == 62
    assert sa.paid_periods == 63
    assert sa.cumulative_paid == Decimal("31500.00")
    assert sa.remaining_periods == 177
    assert sa.is_paid_off is False
    assert sa.progress_percent == Decimal("26.3")
    assert sa.in_coverage is True
    assert sa.next_due_date == date(2025, 6, 15)

    # B
    sb = sched["B"]
    assert sb.annualized_premium == Decimal("8000.00")
    assert sb.total_periods == 10
    assert sb.k_max == 3
    assert sb.paid_periods == 4
    assert sb.cumulative_paid == Decimal("32000.00")
    assert sb.remaining_periods == 6
    assert sb.is_paid_off is False
    assert sb.progress_percent == Decimal("40.0")
    assert sb.in_coverage is True
    assert sb.next_due_date == date(2025, 8, 1)

    # C
    sc = sched["C"]
    assert sc.annualized_premium == Decimal("3000.00")
    assert sc.total_periods == 20
    assert sc.k_max == 0
    assert sc.paid_periods == 1
    assert sc.cumulative_paid == Decimal("3000.00")
    assert sc.remaining_periods == 19
    assert sc.is_paid_off is False
    assert sc.progress_percent == Decimal("5.0")
    assert sc.in_coverage is True
    assert sc.next_due_date == date(2025, 6, 1)

    # D（趸交 + 已缴清）
    sd = sched["D"]
    assert sd.annualized_premium == Decimal("0.00")
    assert sd.total_periods == 1
    assert sd.k_max == 0
    assert sd.paid_periods == 1
    assert sd.cumulative_paid == Decimal("50000.00")
    assert sd.remaining_periods == 0
    assert sd.is_paid_off is True
    assert sd.progress_percent == Decimal("100.0")
    assert sd.in_coverage is True
    assert sd.next_due_date is None


def test_example_a_upcoming(example_a):
    m = example_a
    up_a = enumerate_upcoming(m["A"], m["m1"], TODAY_A, 60)
    up_b = enumerate_upcoming(m["B"], m["m2"], TODAY_A, 60)
    up_c = enumerate_upcoming(m["C"], m["m3"], TODAY_A, 60)
    up_d = enumerate_upcoming(m["D"], m["m1"], TODAY_A, 60)

    # A：2025-06-15(k=63, 期次64) + 2025-07-15(k=64, 期次65)
    assert [(i.due_date, i.period_index, i.amount) for i in up_a] == [
        (date(2025, 6, 15), 64, Decimal("500.00")),
        (date(2025, 7, 15), 65, Decimal("500.00")),
    ]
    # B：下次 2025-08-01 超窗
    assert up_b == []
    # C：2025-06-01(k=1, 期次2)
    assert [(i.due_date, i.period_index, i.amount) for i in up_c] == [
        (date(2025, 6, 1), 2, Decimal("3000.00")),
    ]
    # D：已缴清
    assert up_d == []


def test_example_a_monthly_effective_jan31():
    """月缴保单缴费日推算走 add_months，月末溢出被正确钳制。"""
    m1 = _member("m1", "张三", Relation.self_, date(1988, 6, 1))
    p = _policy("p_X", InsuranceType.life, "m1", "m1", date(2020, 1, 31),
                CoverageTermType.whole_life, None, PaymentTermType.fixed_years, 20,
                PaymentFrequency.monthly, "100", PolicyStatus.active)
    sched = calc_schedule(p, m1, date(2025, 5, 20))
    # 下一期缴费日：生效日 + (k_max+1) 个月，末尾溢出钳制
    assert sched.next_due_date == add_months(date(2020, 1, 31), sched.k_max + 1)
    # 该期必须落在某个月的月末（2 月为 28/29，其余为对应月末）
    assert sched.next_due_date.day in (28, 29, 30, 31)


# ---------------------------------------------------------------------------
# 算例集 B（today = 2025-05-20）
# ---------------------------------------------------------------------------
@pytest.fixture()
def example_b():
    m1 = _member("m1", "张三", Relation.self_, date(1988, 6, 1))
    m4 = _member("m4", "王五", Relation.father, date(1960, 2, 10))
    e = _policy("p_E", InsuranceType.life, "m1", "m1", date(2025, 8, 1),
                CoverageTermType.fixed_years, 20, PaymentTermType.fixed_years, 10,
                PaymentFrequency.monthly, "200", PolicyStatus.active)
    f = _policy("p_F", InsuranceType.annuity, "m4", "m4", date(2015, 3, 1),
                CoverageTermType.whole_life, None, PaymentTermType.to_age, 60,
                PaymentFrequency.yearly, "12000", PolicyStatus.active)
    g = _policy("p_G", InsuranceType.critical_illness, "m1", "m1", date(2019, 1, 1),
                CoverageTermType.whole_life, None, PaymentTermType.single, None,
                PaymentFrequency.monthly, "50000", PolicyStatus.active)
    return {"m1": m1, "m4": m4, "E": e, "F": f, "G": g}


def test_example_b_schedules(example_b):
    m = example_b
    se = calc_schedule(m["E"], m["m1"], TODAY_A)
    sf = calc_schedule(m["F"], m["m4"], TODAY_A)
    sg = calc_schedule(m["G"], m["m1"], TODAY_A)

    # E：生效日在未来 → k_max = -1 → 已缴 0 期
    assert se.annualized_premium == Decimal("2400.00")
    assert se.total_periods == 120
    assert se.k_max == -1
    assert se.paid_periods == 0
    assert se.cumulative_paid == Decimal("0.00")
    assert se.remaining_periods == 120
    assert se.is_paid_off is False
    assert se.progress_percent == Decimal("0.0")
    assert se.in_coverage is True
    assert se.next_due_date == date(2025, 8, 1)

    # F：交至 60 岁 → 总 5 期，已缴清
    assert sf.annualized_premium == Decimal("12000.00")
    assert sf.total_periods == 5
    assert sf.k_max == 10
    assert sf.paid_periods == 5
    assert sf.cumulative_paid == Decimal("60000.00")
    assert sf.remaining_periods == 0
    assert sf.is_paid_off is True
    assert sf.progress_percent == Decimal("100.0")
    assert sf.in_coverage is True
    assert sf.next_due_date is None

    # G：趸交，年化 0
    assert sg.annualized_premium == Decimal("0.00")
    assert sg.total_periods == 1
    assert sg.k_max == 0
    assert sg.paid_periods == 1
    assert sg.cumulative_paid == Decimal("50000.00")
    assert sg.remaining_periods == 0
    assert sg.is_paid_off is True
    assert sg.progress_percent == Decimal("100.0")
    assert sg.in_coverage is True
    assert sg.next_due_date is None


def test_example_b_upcoming(example_b):
    m = example_b
    # E 下次 2025-08-01 超窗；F/G 已缴清
    assert enumerate_upcoming(m["E"], m["m1"], TODAY_A, 60) == []
    assert enumerate_upcoming(m["F"], m["m4"], TODAY_A, 60) == []
    assert enumerate_upcoming(m["G"], m["m1"], TODAY_A, 60) == []
