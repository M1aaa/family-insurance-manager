"""v1.7 QA 独立验证（严过关 / Edward）—— 不采信工程师自测结论，重新证明。

覆盖靶点：

- A. 红线：默认全部计入时，算例 A/B 既有数字**逐值**不变；
- B. 新指标 ``yearUnpaidPremium`` **逐单手算**（独立枚举，不复用被测函数）+ 边界；
- C. 筛选过滤范围（应变 / 绝不能变 / 并集不重复扣减）；
- D. 编辑成员**不得**把已排除成员重置为「计入」（后端行为 + 前端回填模拟）；
- E. 接口契约（GET/PUT 结构、全量替换、422、JSON number）；
- F. 新表在既有库上由 ``create_all`` 自动创建（副本，原库只读）。

本文件**只新增测试**，不改动 ``app/`` 或 ``static/`` 产品代码。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import app.main as app_main
import app.models  # noqa: F401  # 注册 Base.metadata
from app import calc
from app.database import Base
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
REAL_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "insurance.db"))


# ===========================================================================
# 独立实现：不复用 calc.enumerate_year_periods / calc_schedule，逐单手算
# ===========================================================================
def _hand_periods(policy, today: date, year: int) -> list[tuple[date, Decimal]]:
    """独立枚举「缴费日落在 ``year`` 且未标记已缴」的期次（(due_date, amount)）。

    与产品实现相互独立：这里自行按 ``payment_term_type`` / ``payment_frequency``
    推导总期数与已缴期数，日期算术复用 ``calc.add_months``（这是规格钦定的
    唯一日历真源，属**共享输入**而非被测输出）。
    """
    from app.calc import add_months, enum_value

    status = enum_value(policy.status)
    if status not in ("生效中", "已缴清"):
        return []

    freq = enum_value(policy.payment_frequency)
    ptt = enum_value(policy.payment_term_type)
    eff = policy.effective_date
    premium = Decimal(str(policy.premium_per_period))
    per_year = 12 if freq == "月缴" else 1
    period_months = 1 if freq == "月缴" else 12

    # ---- 总期数 ----
    if ptt == "趸交":
        total = 1
    elif ptt == "固定年限":
        total = int(policy.payment_term_value) * per_year
    else:  # 交至某年龄
        insured = _insured_for(policy)
        age_at_eff = (
            eff.year - insured.birth_date.year
            - ((eff.month, eff.day) < (insured.birth_date.month, insured.birth_date.day))
        )
        total = max(0, int(policy.payment_term_value) - age_at_eff) * per_year

    # ---- 已缴期数（独立推算 k_max）----
    override = getattr(policy, "paid_periods_override", None)
    if override is not None:
        paid = max(0, min(int(override), total))
    else:
        if ptt == "趸交":
            k_max = 0 if eff < today else -1
        elif eff >= today:
            k_max = -1
        else:
            k_max = 0
            while add_months(eff, (k_max + 1) * period_months) < today:
                k_max += 1
        paid = max(0, min(k_max + 1, total))

    # ---- 逐期枚举 ----
    out: list[tuple[date, Decimal]] = []
    for k in range(total):
        due = eff if ptt == "趸交" else add_months(eff, k * period_months)
        if due.year > year:
            break
        if due.year < year or k < paid:
            continue
        out.append((due, premium))
    return out


_INSURED_CACHE: dict[str, object] = {}


def _insured_for(policy):
    """取保单主被保人（供「交至某年龄」推导）。"""
    return _INSURED_CACHE[policy.insured_id]


def _hand_year_unpaid(policies, year: int) -> Decimal:
    total = Decimal("0")
    for policy in policies:
        for _due, amount in _hand_periods(policy, TODAY, year):
            total += amount
    return total


# ===========================================================================
# A. 红线：默认全部计入 → 既有数字逐值不变
# ===========================================================================
def _dash(client, today: str = TODAY_STR) -> dict:
    resp = client.get("/api/stats/dashboard", params={"today": today})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_A_example_a_all_values(client, seed_example_a) -> None:
    d = _dash(client)
    ov = d["overview"]
    assert ov["annualTotalPremium"] == 17000.00
    assert ov["unpaidAnnualPremium"] == 17000.00
    assert ov["upcoming60Premium"] == 4000.00
    assert ov["upcoming60Count"] == 3
    assert ov["policyTotal"] == 4
    assert ov["activePolicyCount"] == 3
    assert ov["insuredPersonCount"] == 3
    assert ov["overduePolicyCount"] == 0

    by_insured = {r["name"]: r["annualPremium"] for r in d["byMember"]["byInsured"]}
    assert by_insured == {"张三": 6000.00, "李四": 8000.00, "小张": 3000.00}

    zhangsan = next(r for r in d["byMember"]["byInsured"] if r["name"] == "张三")
    assert zhangsan["policyCount"] == 2
    assert zhangsan["paidToDatePremium"] == 81500.00  # 63*500 + 1*50000


def test_A_example_b_all_values(client, seed_example_b) -> None:
    d = _dash(client)
    ov = d["overview"]
    assert ov["annualTotalPremium"] == 14400.00
    assert ov["unpaidAnnualPremium"] == 2400.00
    assert ov["upcoming60Premium"] == 0.00
    assert ov["upcoming60Count"] == 0
    assert ov["policyTotal"] == 3
    assert ov["activePolicyCount"] == 3
    assert ov["insuredPersonCount"] == 2

    by_holder = {r["name"]: r["annualPremium"] for r in d["byMember"]["byPolicyholder"]}
    assert by_holder == {"张三": 2400.00, "王五": 12000.00}


# ===========================================================================
# B. yearUnpaidPremium：逐单手算 vs 接口
# ===========================================================================
def test_B_hand_vs_api_example_a(client, seed_example_a, db_session) -> None:
    _prime_insured_cache(db_session)
    policies = list(db_session.query(app.models.Policy).all())
    hand = _hand_year_unpaid(policies, 2025)
    assert hand == Decimal("14500.00"), f"手算 A = {hand}"
    assert _dash(client)["overview"]["yearUnpaidPremium"] == float(hand)


def test_B_hand_vs_api_example_b(client, seed_example_b, db_session) -> None:
    _prime_insured_cache(db_session)
    policies = list(db_session.query(app.models.Policy).all())
    hand = _hand_year_unpaid(policies, 2025)
    assert hand == Decimal("1000.00"), f"手算 B = {hand}"
    assert _dash(client)["overview"]["yearUnpaidPremium"] == float(hand)


def _prime_insured_cache(db_session) -> None:
    _INSURED_CACHE.clear()
    for m in db_session.query(app.models.Member).all():
        _INSURED_CACHE[m.id] = m


def _mk(db_session, policy_factory, **over):
    """构造保单；``paid_periods_override`` 因工厂不支持，创建后直接写 ORM 属性。"""
    member = over.pop("member")
    paid_override = over.pop("paid_periods_override", None)
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
    policy = policy_factory(**base)
    if paid_override is not None:
        policy.paid_periods_override = paid_override
        db_session.flush()
    return policy


def test_B_edge_surrendered_and_lapsed_excluded(db_session, member_factory, policy_factory) -> None:
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    p_sur = _mk(db_session, policy_factory, member=m, id="p_s", status=PolicyStatus.surrendered)
    p_lap = _mk(db_session, policy_factory, member=m, id="p_l", status=PolicyStatus.lapsed)
    assert calc.enumerate_year_periods(p_sur, m, TODAY) == []
    assert calc.enumerate_year_periods(p_lap, m, TODAY) == []


def test_B_edge_paid_off_zero(db_session, member_factory, policy_factory) -> None:
    """期次全缴（已缴清）→ 0。"""
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    p = _mk(
        db_session, policy_factory, member=m, id="p_paid",
        effective_date=date(2015, 3, 1), payment_term_value=5,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="12000",
        status=PolicyStatus.active,
    )
    assert calc.enumerate_year_periods(p, m, TODAY) == []


def test_B_edge_single_future_effective_counted(db_session, member_factory, policy_factory) -> None:
    """趸交、生效日落在本年度且**未来**（未缴）→ 计入（可能大额）。"""
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    p = _mk(
        db_session, policy_factory, member=m, id="p_single_future",
        effective_date=date(2025, 6, 1),
        payment_term_type=PaymentTermType.single, payment_term_value=None,
        premium_per_period="50000", status=PolicyStatus.active,
    )
    items = calc.enumerate_year_periods(p, m, TODAY)
    assert len(items) == 1
    assert items[0].amount == Decimal("50000.00")


def test_B_edge_single_past_effective_not_counted(db_session, member_factory, policy_factory) -> None:
    """趸交、生效日在过去（视为已缴）→ 不计入。"""
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    p = _mk(
        db_session, policy_factory, member=m, id="p_single_past",
        effective_date=date(2025, 3, 1),
        payment_term_type=PaymentTermType.single, payment_term_value=None,
        premium_per_period="50000", status=PolicyStatus.active,
    )
    assert calc.enumerate_year_periods(p, m, TODAY) == []


def test_B_edge_future_effective_counts_this_year(db_session, member_factory, policy_factory) -> None:
    """生效日在未来（2025-08-01）→ 本年度 8~12 月 5 期计入。"""
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    p = _mk(
        db_session, policy_factory, member=m, id="p_e",
        effective_date=date(2025, 8, 1), payment_term_value=10,
        premium_per_period="200", payment_frequency=PaymentFrequency.monthly,
    )
    items = calc.enumerate_year_periods(p, m, TODAY)
    assert len(items) == 5
    assert items[0].due_date == date(2025, 8, 1)
    assert items[-1].due_date == date(2025, 12, 1)


def test_B_edge_overdue_unpaid_counted(db_session, member_factory, policy_factory) -> None:
    """已逾期未缴 → 应计入（设计明确要求）。"""
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    # 月缴 100、2025-01-01 生效、只缴了 2 期（1/2 月）→ 3~12 月共 10 期未缴 = 1000
    p = _mk(
        db_session, policy_factory, member=m, id="p_ovd",
        effective_date=date(2025, 1, 1), payment_term_value=10,
        premium_per_period="100", payment_frequency=PaymentFrequency.monthly,
        paid_periods_override=2,
    )
    items = calc.enumerate_year_periods(p, m, TODAY)
    assert len(items) == 10
    assert items[0].due_date == date(2025, 3, 1)
    assert sum(i.amount for i in items) == Decimal("1000.00")


def test_B_boundary_dec31_vs_jan01(db_session, member_factory, policy_factory) -> None:
    """跨年度边界：缴费日恰为 12-31 与 01-01 的期次归属。

    - 生效 2024-12-31 月缴：due(0)=2024-12-31 归 2024；2025 年期次从 01-31 起；
    - 生效 2025-01-01 月缴：due(0)=2025-01-01 归 2025。
    """
    m = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    # 用 override=0 把「已缴」清零，隔离「年份归属」这一被测点
    p_dec = _mk(
        db_session, policy_factory, member=m, id="p_dec",
        effective_date=date(2024, 12, 31), payment_term_value=10,
        premium_per_period="100", payment_frequency=PaymentFrequency.monthly,
        paid_periods_override=0,
    )
    # 2024 年：k=0 (2024-12-31) → 归 2024
    assert [i.due_date for i in calc.enumerate_year_periods(p_dec, m, TODAY, year=2024)] == [
        date(2024, 12, 31)
    ]
    # 2025 年：k=1..12（2025-01-31 ... 2025-12-31）
    items_2025 = calc.enumerate_year_periods(p_dec, m, TODAY, year=2025)
    assert items_2025[0].due_date == date(2025, 1, 31)
    assert items_2025[-1].due_date == date(2025, 12, 31)
    assert len(items_2025) == 12

    p_jan = _mk(
        db_session, policy_factory, member=m, id="p_jan",
        effective_date=date(2025, 1, 1), payment_term_value=10,
        premium_per_period="100", payment_frequency=PaymentFrequency.monthly,
        paid_periods_override=0,
    )
    # due(0)=2025-01-01 → 归 2025（而非 2024）
    assert calc.enumerate_year_periods(p_jan, m, TODAY, year=2025)[0].due_date == date(2025, 1, 1)
    assert calc.enumerate_year_periods(p_jan, m, TODAY, year=2024) == []


def test_B_year_unpaid_not_affected_by_filter(client, seed_example_a) -> None:
    """★ yearUnpaidPremium 不受筛选影响（排掉投保人 ≠ 不用交钱）。"""
    before = _dash(client)["overview"]["yearUnpaidPremium"]
    client.put(
        "/api/stats/exclusions",
        json={"excludedPolicyholderIds": ["m1"], "excludedInsuranceTypes": ["医疗险"]},
    )
    after = _dash(client)["overview"]["yearUnpaidPremium"]
    assert before == after == 14500.00


# ===========================================================================
# C. 筛选过滤范围
# ===========================================================================
def test_C_exclude_holder_scope(client, seed_example_a) -> None:
    before = _dash(client)
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": ["m1"], "excludedInsuranceTypes": []})
    after = _dash(client)

    # 应变（年化口径）
    assert before["overview"]["annualTotalPremium"] == 17000.00
    assert after["overview"]["annualTotalPremium"] == 0.00
    assert after["overview"]["unpaidAnnualPremium"] == 0.00

    # 绝不能变
    assert after["overview"]["upcoming60Premium"] == before["overview"]["upcoming60Premium"] == 4000.00
    assert after["overview"]["upcoming60Count"] == before["overview"]["upcoming60Count"] == 3
    assert after["overview"]["policyTotal"] == before["overview"]["policyTotal"] == 4
    assert after["overview"]["activePolicyCount"] == 3
    assert after["overview"]["insuredPersonCount"] == 3
    assert after["overview"]["yearUnpaidPremium"] == 14500.00

    # 三维计数 / 已缴 / 待缴不变
    bt0 = {r["insuranceType"]: r for r in before["byType"]}
    bt1 = {r["insuranceType"]: r for r in after["byType"]}
    for t in bt0:
        assert bt1[t]["policyCount"] == bt0[t]["policyCount"]
        assert bt1[t]["paidToDatePremium"] == bt0[t]["paidToDatePremium"]
        assert bt1[t]["upcoming60Premium"] == bt0[t]["upcoming60Premium"]
    bm0 = {r["name"]: r for r in before["byMember"]["byInsured"]}
    bm1 = {r["name"]: r for r in after["byMember"]["byInsured"]}
    for n in bm0:
        assert bm1[n]["policyCount"] == bm0[n]["policyCount"]
        assert bm1[n]["activePolicyCount"] == bm0[n]["activePolicyCount"]
        assert bm1[n]["upcoming60Premium"] == bm0[n]["upcoming60Premium"]
        assert bm1[n]["paidToDatePremium"] == bm0[n]["paidToDatePremium"]

    # upcoming[] / overdue[] 清单本身不变
    assert len(after["upcoming"]) == len(before["upcoming"]) == 3
    assert after["upcoming"] == before["upcoming"]


def test_C_exclude_type_scope(client, seed_example_a) -> None:
    before = _dash(client)
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [], "excludedInsuranceTypes": ["医疗险"]})
    after = _dash(client)

    bt1 = {r["insuranceType"]: r for r in after["byType"]}
    bt0 = {r["insuranceType"]: r for r in before["byType"]}
    assert bt1["医疗险"]["annualPremium"] == 0.00
    assert bt0["医疗险"]["annualPremium"] == 8000.00
    assert bt1["医疗险"]["policyCount"] == bt0["医疗险"]["policyCount"] == 1
    assert bt1["医疗险"]["paidToDatePremium"] == bt0["医疗险"]["paidToDatePremium"]
    assert after["overview"]["annualTotalPremium"] == 9000.00
    assert after["overview"]["yearUnpaidPremium"] == 14500.00
    assert after["overview"]["upcoming60Premium"] == 4000.00


def test_C_member_api_annual_respects_type(client, seed_example_a) -> None:
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [], "excludedInsuranceTypes": ["医疗险"]})
    members = {m["name"]: m for m in client.get("/api/members").json()}
    assert members["李四"]["annualPremium"] == 0.00
    # 李四的 coverageTypes 里「医疗险」的年化也应归零
    med = next((c for c in members["李四"]["coverageTypes"] if c["insuranceType"] == "医疗险"), None)
    assert med is not None and med["annualPremium"] == 0.00
    # 计数不变
    assert members["李四"]["policyCount"] == 1


def test_C_union_no_double_deduction(client, member_factory, policy_factory) -> None:
    """两维同时排除 → 并集语义，同一保单**不重复扣减**。"""
    m1 = member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1))
    m2 = member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12))
    # p1: 张三 / 重疾险 / 6000 —— 投保人与险种**都被排除**
    policy_factory(
        id="p1", insurance_type=InsuranceType.critical_illness, policyholder_id="m1", insured_id="m1",
        effective_date=date(2020, 1, 1), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=20,
        payment_frequency=PaymentFrequency.monthly, premium_per_period="500", status=PolicyStatus.active,
    )
    # p2: 张三 / 医疗险 / 8000
    policy_factory(
        id="p2", insurance_type=InsuranceType.medical, policyholder_id="m1", insured_id="m1",
        effective_date=date(2020, 1, 1), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=20,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="8000", status=PolicyStatus.active,
    )
    # p3: 李四 / 重疾险 / 4000
    policy_factory(
        id="p3", insurance_type=InsuranceType.critical_illness, policyholder_id="m2", insured_id="m2",
        effective_date=date(2020, 1, 1), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=20,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="4000", status=PolicyStatus.active,
    )
    assert _dash(client)["overview"]["annualTotalPremium"] == 18000.00

    # 仅排投保人 m1 → 去掉 p1+p2 → 4000
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": ["m1"], "excludedInsuranceTypes": []})
    assert _dash(client)["overview"]["annualTotalPremium"] == 4000.00

    # 仅排重疾险 → 去掉 p1+p3 → 8000
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [], "excludedInsuranceTypes": ["重疾险"]})
    assert _dash(client)["overview"]["annualTotalPremium"] == 8000.00

    # 两维同时排 → p1 命中两次但只扣一次 → 0（不为负）
    client.put(
        "/api/stats/exclusions",
        json={"excludedPolicyholderIds": ["m1"], "excludedInsuranceTypes": ["重疾险"]},
    )
    after = _dash(client)["overview"]
    assert after["annualTotalPremium"] == 0.00
    assert after["policyTotal"] == 3  # 计数不受影响


# ===========================================================================
# D. 编辑成员不得把已排除成员重置为「计入」
# ===========================================================================
def _set_excluded(client, member_id: str) -> None:
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [member_id], "excludedInsuranceTypes": []})


def test_D_backend_raw_put_without_flag_resets(client, seed_example_a) -> None:
    """后端裸行为（记录陷阱）：PUT 省略 countInStats → 默认 True → 重置为计入。

    这是**设计保留**的兼容行为（MemberCreate.countInStats 默认 True），
    前端 ``members.js`` 必须回填现值来规避。
    """
    _set_excluded(client, "m1")
    assert client.get("/api/stats/exclusions").json()["excludedPolicyholderIds"] == ["m1"]

    # 裸 PUT（不带 countInStats），模拟「天真前端」
    resp = client.put(
        "/api/members/m1",
        json={"name": "张三", "relation": "本人", "birthDate": "1988-06-01", "gender": "男", "remark": "改个备注"},
    )
    assert resp.status_code == 200, resp.text
    # 陷阱成立：被重置为计入
    assert resp.json()["countInStats"] is True
    assert client.get("/api/stats/exclusions").json()["excludedPolicyholderIds"] == []


def test_D_frontend_payload_backfill_preserves(client, seed_example_a) -> None:
    """前端回填模拟：PUT 带 countInStats=false → 仍为不计入，看板不被改回。"""
    _set_excluded(client, "m1")
    before = _dash(client)["overview"]["annualTotalPremium"]
    assert before == 0.00  # 已排除

    # members.js 实际提交的 payload：{...form, countInStats: member.countInStats !== false}
    member = next(m for m in client.get("/api/members").json() if m["id"] == "m1")
    resp = client.put(
        "/api/members/m1",
        json={
            "name": "张三",
            "relation": member["relation"],
            "birthDate": member["birthDate"],
            "gender": member["gender"],
            "remark": "改了备注",
            "countInStats": member["countInStats"] is not False,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["countInStats"] is False
    assert client.get("/api/stats/exclusions").json()["excludedPolicyholderIds"] == ["m1"]
    assert _dash(client)["overview"]["annualTotalPremium"] == 0.00


# ===========================================================================
# E. 接口契约
# ===========================================================================
def test_E_get_structure(client) -> None:
    body = client.get("/api/stats/exclusions").json()
    assert set(body.keys()) == {"excludedPolicyholderIds", "excludedInsuranceTypes"}
    assert body == {"excludedPolicyholderIds": [], "excludedInsuranceTypes": []}


def test_E_full_replace_semantics(client, seed_example_a) -> None:
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [], "excludedInsuranceTypes": ["车险", "重疾险"]})
    assert set(client.get("/api/stats/exclusions").json()["excludedInsuranceTypes"]) == {"车险", "重疾险"}
    # 第二次只留车险 → 重疾险必须消失
    client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [], "excludedInsuranceTypes": ["车险"]})
    assert client.get("/api/stats/exclusions").json()["excludedInsuranceTypes"] == ["车险"]


def test_E_422_validation(client, seed_example_a) -> None:
    r1 = client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": ["m_nope"], "excludedInsuranceTypes": []})
    assert r1.status_code == 422, r1.text
    assert r1.json()["code"] == "VALIDATION_ERROR"

    r2 = client.put("/api/stats/exclusions", json={"excludedPolicyholderIds": [], "excludedInsuranceTypes": ["不存在险种"]})
    assert r2.status_code == 422, r2.text
    assert r2.json()["code"] == "VALIDATION_ERROR"

    # 失败无副作用
    assert client.get("/api/stats/exclusions").json() == {
        "excludedPolicyholderIds": [],
        "excludedInsuranceTypes": [],
    }


def test_E_year_unpaid_is_json_number(client, seed_example_a) -> None:
    raw = client.get("/api/stats/dashboard", params={"today": TODAY_STR}).text
    assert '"yearUnpaidPremium":' in raw
    assert '"yearUnpaidPremium":"' not in raw
    assert '"yearUnpaidPremium":14500.0' in raw  # 无引号 = JSON number


# ===========================================================================
# F. 新表在既有库上自动创建（副本，原库只读）
# ===========================================================================
def _md5(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def _tables(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_F_new_table_autocreated_on_existing_db(tmp_path, monkeypatch) -> None:
    if not os.path.exists(REAL_DB):
        pytest.skip(f"真实库不存在：{REAL_DB}")
    original_md5 = _md5(REAL_DB)
    original_mtime = os.path.getmtime(REAL_DB)

    dst = str(tmp_path / "v16copy.db")
    shutil.copy2(REAL_DB, dst)
    # 模拟 v1.6 库：删掉 stats_exclusions
    conn = sqlite3.connect(dst)
    conn.execute("DROP TABLE IF EXISTS stats_exclusions")
    conn.commit()
    conn.close()
    assert "stats_exclusions" not in _tables(dst)

    engine = create_engine(f"sqlite:///{dst}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)
    # 走真实启动路径的建表环节
    Base.metadata.create_all(bind=engine)

    assert "stats_exclusions" in _tables(dst), "★ 新表未被自动创建"
    cols = {c["name"] for c in inspect(engine).get_columns("stats_exclusions")}
    assert {"kind", "ref_id", "created_at"} <= cols
    pk = {c.name for c in Base.metadata.tables["stats_exclusions"].primary_key.columns}
    assert pk == {"kind", "ref_id"}

    # 幂等：连建两次表集合不变
    tables_1 = _tables(dst)
    Base.metadata.create_all(bind=engine)
    assert _tables(dst) == tables_1

    engine.dispose()
    # 原库只读
    assert _md5(REAL_DB) == original_md5, "★ 原库内容被改动！"
    assert os.path.getmtime(REAL_DB) == original_mtime, "★ 原库时间戳被改动！"
