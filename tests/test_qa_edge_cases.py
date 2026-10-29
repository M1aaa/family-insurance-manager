"""QA 边界与异常路径验证（对齐 ARCHITECTURE v1.1）。

覆盖团队指定的 11 类边界 + v1.1 新增「算例集 C — 边界」（C1–C5）。
每个用例都给出「最小可复现输入」与「期望 / 实际」。

v1.1 关键变更（本文件据此断言）：
  - `k_max = max{k >= 0 : due(k) < today}`（**严格小于**，`due(k)=add_months(生效日, k×周期)`）；
    `生效日 >= 今天 → -1`。不再用日历算术。
  - 缴费日**恰好等于今天**的期次**计入待缴、不计为已缴**；`nextDueDate` 允许等于今天。
  - 趸交：`eff < today` 才算已缴（严格）。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal

from app.calc import add_months, calc_schedule, enumerate_upcoming
from app.enums import (
    CoverageTermType,
    Gender,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)

MONTHLY = PaymentFrequency.monthly
YEARLY = PaymentFrequency.yearly


# ---------------------------------------------------------------------------
# 独立参考实现（不复用 app.calc 的 k_max / due）
# ---------------------------------------------------------------------------
def _qa_add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    year, month0 = divmod(total, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _qa_due(eff: date, k: int, freq) -> date:
    step = 1 if freq == MONTHLY else 12
    return _qa_add_months(eff, k * step)


def _qa_k_max(eff: date, freq, today: date) -> int:
    """v1.1：max{k >= 0 : due(k) < today}；eff >= today → -1。"""
    if eff >= today:
        return -1
    k = 0
    while _qa_due(eff, k + 1, freq) < today:
        k += 1
    return k


def _insured(member_factory, birth=date(1988, 6, 1)):
    return member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=birth, gender=Gender.male)


def _mk(policy_factory, **kw):
    base = dict(
        id="p_x",
        insurance_type=InsuranceType.critical_illness,
        policyholder_id="m1",
        insured_id="m1",
        effective_date=date(2020, 3, 15),
        coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None,
        payment_term_type=PaymentTermType.fixed_years,
        payment_term_value=20,
        payment_frequency=MONTHLY,
        premium_per_period="500",
        status=PolicyStatus.active,
    )
    base.update(kw)
    return policy_factory(**base)


# ---------------------------------------------------------------------------
# 1) 生效日在未来
# ---------------------------------------------------------------------------
def test_effective_date_in_future(db_session, member_factory, policy_factory):
    """生效日 > today → k_max=-1、已缴 0 期、进度 0.0%、下次缴费日=生效日、无待缴。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, effective_date=date(2025, 8, 1), payment_frequency=MONTHLY)
    today = date(2025, 5, 20)
    sched = calc_schedule(policy, insured, today)

    assert sched.k_max == -1
    assert sched.paid_periods == 0
    assert sched.progress_percent == Decimal("0.0")
    assert sched.next_due_date == date(2025, 8, 1)
    assert sched.is_paid_off is False
    assert enumerate_upcoming(policy, insured, today) == []


# ---------------------------------------------------------------------------
# 2) 月末钳制（v1.1：C1 / C2）
# ---------------------------------------------------------------------------
def test_month_end_clamp_add_months():
    """add_months 月末钳制正确。"""
    assert add_months(date(2020, 1, 31), 1) == date(2020, 2, 29)  # 2020 闰年
    assert add_months(date(2021, 1, 31), 1) == date(2021, 2, 28)
    assert add_months(date(2020, 3, 31), 1) == date(2020, 4, 30)


def test_month_end_clamp_c1(db_session, member_factory, policy_factory):
    """C1：生效日 2020-01-31 月缴，today=2020-04-30 → k_max=2、已缴 3、下次=2020-04-30(=今天)。

    due：k=0→01-31, 1→02-29, 2→03-31, 3→04-30(=today)。严格小于 → k_max=2；
    第 4 期（due=today）计入待缴清单第一期。
    """
    insured = _insured(member_factory)
    policy = _mk(policy_factory, effective_date=date(2020, 1, 31), payment_frequency=MONTHLY,
                 premium_per_period="500")
    today = date(2020, 4, 30)
    sched = calc_schedule(policy, insured, today)
    assert sched.k_max == 2
    assert sched.paid_periods == 3
    assert sched.cumulative_paid == Decimal("1500.00")
    assert sched.next_due_date == today
    assert [i.due_date for i in enumerate_upcoming(policy, insured, today)] == [today, date(2020, 5, 31)]


def test_month_end_clamp_c2(db_session, member_factory, policy_factory):
    """C2：生效日 2020-01-31 月缴，today=2020-02-29 → k_max=0、已缴 1、下次=2020-02-29(=今天)。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, effective_date=date(2020, 1, 31), payment_frequency=MONTHLY,
                 premium_per_period="500")
    today = date(2020, 2, 29)
    sched = calc_schedule(policy, insured, today)
    assert sched.k_max == 0
    assert sched.paid_periods == 1
    assert sched.cumulative_paid == Decimal("500.00")
    assert sched.next_due_date == today


# ---------------------------------------------------------------------------
# 3) 闰日生效日（年缴，v1.1）
# ---------------------------------------------------------------------------
def test_leap_day_effective_yearly(db_session, member_factory, policy_factory):
    """生效日 2020-02-29 年缴 → 周年日安全落到 2021-02-28，不崩溃。

    v1.1：today=2021-02-28 时 due(1)=2021-02-28 不 < today → k_max=0、已缴 1、下次=2021-02-28。
    """
    insured = _insured(member_factory)
    policy = _mk(policy_factory, effective_date=date(2020, 2, 29), payment_frequency=YEARLY,
                 payment_term_value=10, premium_per_period="1000")
    sched = calc_schedule(policy, insured, date(2021, 2, 28))
    assert sched.total_periods == 10
    assert sched.k_max == 0
    assert sched.paid_periods == 1
    assert sched.next_due_date == date(2021, 2, 28)
    # 不抛异常即通过
    calc_schedule(policy, insured, date(2024, 2, 29))


# ---------------------------------------------------------------------------
# 4) 趸交（v1.1：eff < today 才算已缴）
# ---------------------------------------------------------------------------
def test_single_premium(db_session, member_factory, policy_factory):
    """趸交：总期数=1、年化=0、首期后即缴清、nextDueDate=null、不产生待缴。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, effective_date=date(2019, 1, 1),
                 payment_term_type=PaymentTermType.single, payment_term_value=None,
                 premium_per_period="50000", status=PolicyStatus.active)
    today = date(2025, 5, 20)
    sched = calc_schedule(policy, insured, today)
    assert sched.total_periods == 1
    assert sched.annualized_premium == Decimal("0.00")
    assert sched.paid_periods == 1
    assert sched.is_paid_off is True
    assert sched.next_due_date is None
    assert enumerate_upcoming(policy, insured, today) == []


def test_single_premium_effective_today(db_session, member_factory, policy_factory):
    """趸交且生效日=今天：eff < today 不成立 → k_max=-1、已缴 0、未缴清、下次=今天。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, effective_date=date(2025, 5, 20),
                 payment_term_type=PaymentTermType.single, payment_term_value=None,
                 premium_per_period="50000", status=PolicyStatus.active)
    today = date(2025, 5, 20)
    sched = calc_schedule(policy, insured, today)
    assert sched.k_max == -1
    assert sched.paid_periods == 0
    assert sched.is_paid_off is False
    assert sched.next_due_date == today


# ---------------------------------------------------------------------------
# 5) 交至某年龄
# ---------------------------------------------------------------------------
def test_pay_to_age_total_periods(db_session, member_factory, policy_factory):
    """交至某年龄：totalPeriods = max(0, A − 生效时周岁) × 每年期数。"""
    insured = member_factory(id="m4", name="王五", relation=Relation.father,
                             birth_date=date(1960, 2, 10), gender=Gender.male)
    policy = policy_factory(
        id="p_F", insurance_type=InsuranceType.annuity, policyholder_id="m4", insured_id="m4",
        effective_date=date(2015, 3, 1), coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None, payment_term_type=PaymentTermType.to_age, payment_term_value=60,
        payment_frequency=YEARLY, premium_per_period="12000", status=PolicyStatus.active,
    )
    sched = calc_schedule(policy, insured, date(2025, 5, 20))
    assert sched.total_periods == 5
    assert sched.paid_periods == 5
    assert sched.is_paid_off is True


def test_pay_to_age_already_past(db_session, member_factory, policy_factory):
    """生效时年龄已 ≥ A → totalPeriods=0、progressPercent=100.0、isPaidOff=true、无除零异常。"""
    insured = member_factory(id="m5", name="老者", relation=Relation.father,
                             birth_date=date(1950, 1, 1), gender=Gender.male)
    policy = policy_factory(
        id="p_z", insurance_type=InsuranceType.life, policyholder_id="m5", insured_id="m5",
        effective_date=date(2020, 1, 1), coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None, payment_term_type=PaymentTermType.to_age, payment_term_value=60,
        payment_frequency=YEARLY, premium_per_period="1000", status=PolicyStatus.active,
    )
    sched = calc_schedule(policy, insured, date(2025, 5, 20))
    assert sched.total_periods == 0
    assert sched.progress_percent == Decimal("100.0")
    assert sched.is_paid_off is True
    assert sched.next_due_date is None


# ---------------------------------------------------------------------------
# 6) 待缴窗口闭区间边界（v1.1：下界 due == today 计入）
# ---------------------------------------------------------------------------
def test_upcoming_window_upper_bound(db_session, member_factory, policy_factory):
    """缴费日 = today+60 计入；= today+61 不计入。"""
    insured = _insured(member_factory)
    today = date(2025, 5, 20)
    in_window = _mk(policy_factory, id="p_in", effective_date=date(2024, 7, 19),
                    payment_frequency=YEARLY, payment_term_value=50, premium_per_period="100")
    out_window = _mk(policy_factory, id="p_out", effective_date=date(2024, 7, 20),
                     payment_frequency=YEARLY, payment_term_value=50, premium_per_period="100")
    assert [i.due_date for i in enumerate_upcoming(in_window, insured, today)] == [date(2025, 7, 19)]
    assert [i.due_date for i in enumerate_upcoming(out_window, insured, today)] == []


def test_upcoming_window_lower_bound_due_today(db_session, member_factory, policy_factory):
    """v1.1：缴费日恰好 = today 的期次**计入**待缴清单（PRD 闭区间口径）。"""
    insured = _insured(member_factory)
    today = date(2025, 5, 20)
    policy = _mk(policy_factory, id="p_today", effective_date=date(2024, 5, 20),
                 payment_frequency=YEARLY, payment_term_value=50, premium_per_period="100")
    # due(k=1) = 2025-05-20 == today
    items = enumerate_upcoming(policy, insured, today)
    assert [i.due_date for i in items] == [today]
    assert items[0].period_index == 2


# ---------------------------------------------------------------------------
# 7) 不含逾期
# ---------------------------------------------------------------------------
def test_overdue_not_in_upcoming(db_session, member_factory, policy_factory):
    """月缴、上次缴费日 3 个月前且一直未缴 → 逾期期次（due(k) < today）不进入待缴清单。"""
    insured = _insured(member_factory)
    today = date(2025, 5, 20)
    policy = _mk(policy_factory, id="p_od", effective_date=date(2025, 2, 10),
                 payment_frequency=MONTHLY, payment_term_value=20, premium_per_period="100")
    dues = [i.due_date for i in enumerate_upcoming(policy, insured, today)]
    # 逾期 02-10/03-10/04-10/05-10 均视为已缴，不出现；窗口内仅 06-10 / 07-10
    assert dues == [date(2025, 6, 10), date(2025, 7, 10)]
    assert all(d >= today for d in dues)


# ---------------------------------------------------------------------------
# 8) 60 天窗口月缴期数上限（且不漏期/不多期）
# ---------------------------------------------------------------------------
def test_window_max_three_periods_no_miss(db_session, member_factory, policy_factory):
    """极端用例：today 恰在缴费日前 1 天 → 窗口内最多 3 期，且不漏期/不多期。

    独立参考：未缴期次 = { k : today <= due(k) <= today+60, k < total }（与 k_max 无关）。
    """
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="p_w", effective_date=date(2024, 12, 1),
                 payment_frequency=MONTHLY, payment_term_value=30, premium_per_period="100")
    total = 360
    for today in [date(2025, 1, 31), date(2025, 2, 1), date(2025, 3, 31)]:
        end = today + timedelta(days=60)
        expected = [d for k in range(total) if today <= (d := _qa_due(policy.effective_date, k, MONTHLY)) <= end]
        got = [i.due_date for i in enumerate_upcoming(policy, insured, today)]
        assert got == expected, f"today={today}: 实现 {got} vs 参考 {expected}"
        assert len(got) <= 3, f"today={today}: 窗口内期数 {len(got)} 超过 3"


# ---------------------------------------------------------------------------
# 9) 已缴清保单不计入「未缴费金额」，但计入 count / paidToDate（v1.4 口径）
# ---------------------------------------------------------------------------
def test_paid_off_excluded_from_unpaid_but_counted_in_member(client, seed_example_a):
    """D 趸交已缴清：不计入「当年未缴费金额」，但 byMember/byType 的 count 与 paidToDate 计入。

    v1.4：D 的 status=已缴清 → 计入「当年应缴总保费」，但因趸交年化为 0，应缴总保费仍为 17000。
    """
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    zhang = {r["name"]: r for r in data["byMember"]["byInsured"]}["张三"]
    assert zhang["policyCount"] == 2                 # A + D
    assert zhang["annualPremium"] == 6000.00         # 仅 A（D 趸交年化 0 且已缴清）
    assert zhang["paidToDatePremium"] == 81500.00    # A 31500 + D 50000
    # v1.4 双口径
    assert data["overview"]["unpaidAnnualPremium"] == 17000.00
    assert data["overview"]["annualTotalPremium"] == 17000.00


# ---------------------------------------------------------------------------
# 10) 进度百分比舍入
# ---------------------------------------------------------------------------
def test_progress_percent_round_half_up(db_session, member_factory, policy_factory):
    """63/240 = 26.25% → ROUND_HALF_UP 保留 1 位 = 26.3。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="p_A", effective_date=date(2020, 3, 15),
                 payment_frequency=MONTHLY, payment_term_value=20, premium_per_period="500")
    sched = calc_schedule(policy, insured, date(2025, 5, 20))
    assert sched.paid_periods == 63
    assert sched.total_periods == 240
    assert sched.progress_percent == Decimal("26.3")


# ---------------------------------------------------------------------------
# 11) totalPeriods = 0 → 进度 100.0（无除零）
# ---------------------------------------------------------------------------
def test_total_zero_progress_is_100(db_session, member_factory, policy_factory):
    """totalPeriods=0 时 progressPercent=100.0，不出现除零异常。"""
    insured = member_factory(id="m6", name="老者", relation=Relation.father,
                             birth_date=date(1950, 1, 1), gender=Gender.male)
    policy = policy_factory(
        id="p_zz", insurance_type=InsuranceType.life, policyholder_id="m6", insured_id="m6",
        effective_date=date(2020, 1, 1), coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None, payment_term_type=PaymentTermType.to_age, payment_term_value=60,
        payment_frequency=YEARLY, premium_per_period="1000", status=PolicyStatus.active,
    )
    sched = calc_schedule(policy, insured, date(2025, 5, 20))
    assert sched.total_periods == 0
    assert sched.progress_percent == Decimal("100.0")


# ---------------------------------------------------------------------------
# 算例集 C — 边界（v1.1 新增，C3 / C4 / C5 为 v1.1 关键 delta）
# ---------------------------------------------------------------------------
def test_example_c1_month_end_today_is_due(db_session, member_factory, policy_factory):
    """C1：2020-01-31 月缴 / today=2020-04-30 → k_max=2, paid=3, next=2020-04-30。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="c1", effective_date=date(2020, 1, 31), payment_frequency=MONTHLY)
    sched = calc_schedule(policy, insured, date(2020, 4, 30))
    assert (sched.k_max, sched.paid_periods, sched.next_due_date) == (2, 3, date(2020, 4, 30))


def test_example_c2_leap_month_end(db_session, member_factory, policy_factory):
    """C2：2020-01-31 月缴 / today=2020-02-29 → k_max=0, paid=1, next=2020-02-29。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="c2", effective_date=date(2020, 1, 31), payment_frequency=MONTHLY)
    sched = calc_schedule(policy, insured, date(2020, 2, 29))
    assert (sched.k_max, sched.paid_periods, sched.next_due_date) == (0, 1, date(2020, 2, 29))


def test_example_c3_exact_anniversary_strict(db_session, member_factory, policy_factory):
    """C3：2020-03-15 月缴 / today=2025-05-15（精确周年日）→ k_max=61, paid=62, next=2025-05-15。

    严格小于：due(62)=2025-05-15 不 < today → k_max=61（v1.0 日历公式会多算为 62）。
    """
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="c3", effective_date=date(2020, 3, 15), payment_frequency=MONTHLY)
    sched = calc_schedule(policy, insured, date(2025, 5, 15))
    assert (sched.k_max, sched.paid_periods, sched.next_due_date) == (61, 62, date(2025, 5, 15))


def test_example_c4_yearly_today_is_due(db_session, member_factory, policy_factory):
    """C4：2021-08-01 年缴 / today=2025-08-01 → k_max=3, paid=4, next=2025-08-01。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="c4", effective_date=date(2021, 8, 1), payment_frequency=YEARLY,
                 payment_term_value=20)
    sched = calc_schedule(policy, insured, date(2025, 8, 1))
    assert (sched.k_max, sched.paid_periods, sched.next_due_date) == (3, 4, date(2025, 8, 1))


def test_example_c5_effective_equals_today(db_session, member_factory, policy_factory):
    """C5：2020-03-15 月缴 / today=2020-03-15（生效日=今天）→ k_max=-1, paid=0, next=2020-03-15。"""
    insured = _insured(member_factory)
    policy = _mk(policy_factory, id="c5", effective_date=date(2020, 3, 15), payment_frequency=MONTHLY)
    sched = calc_schedule(policy, insured, date(2020, 3, 15))
    assert (sched.k_max, sched.paid_periods, sched.next_due_date) == (-1, 0, date(2020, 3, 15))
