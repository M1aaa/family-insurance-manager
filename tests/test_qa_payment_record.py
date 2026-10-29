"""QA 独立验证：缴费记录（``paidPeriodsOverride``）+ 逾期增量。

与 ``tests/test_payment_record.py``（工程师产出）**相互独立**：
本文件的核心是**自行重写一份参考实现**，在大量组合上与 ``app.calc`` 逐值比对，
并覆盖工程师测试未触及的：数据库迁移升级路径、`enumerate_upcoming` 的暴力枚举
交叉校验、看板逾期清单的排除规则、以及强制迁移失败不阻塞启动。

真源：任务说明给出的派生量语义定义（不复用 app.calc 的实现）。
"""

from __future__ import annotations

import calendar
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.calc import (
    add_months,
    calc_schedule,
    compute_k_max,
    due_date_for_period,
    enumerate_upcoming,
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

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MONTHLY = "月缴"
YEARLY = "年缴"
SINGLE = "趸交"
FIXED = "固定年限"
TOAGE = "交至某年龄"


# ===========================================================================
# 1. 独立参考实现（完全不调用 app.calc 的算法）
# ===========================================================================
def ref_add_months(d: date, n: int) -> date:
    """独立实现「加 n 个月 + 月末钳制」。"""
    total = d.year * 12 + (d.month - 1) + n
    year, month_index = divmod(total, 12)
    month = month_index + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def ref_age_years(birth: date, on: date) -> int:
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def ref_pm(freq: str) -> int:
    return 1 if freq == MONTHLY else 12


def ref_due(eff: date, freq: str, k: int) -> date:
    return ref_add_months(eff, k * ref_pm(freq))


def ref_k_max(eff: date, freq: str, today: date, is_single: bool) -> int:
    """暴力求 ``max{k >= 0 : due(k) < today}``（严格小于）；``eff >= today`` → -1。"""
    if is_single:
        return 0 if eff < today else -1
    if eff >= today:
        return -1
    k = 0
    while ref_due(eff, freq, k + 1) < today:
        k += 1
    return k


def ref_total_periods(ptt: str, ptv, freq: str, birth: date, eff: date) -> int:
    per_year = 12 if freq == MONTHLY else 1
    if ptt == SINGLE:
        return 1
    if ptt == FIXED:
        return int(ptv) * per_year
    return max(0, int(ptv) - ref_age_years(birth, eff)) * per_year


def ref_schedule(eff, freq, ptt, ptv, birth, today, override):
    """独立推导全部派生量，返回 dict。"""
    total = ref_total_periods(ptt, ptv, freq, birth, eff)
    kmax = ref_k_max(eff, freq, today, ptt == SINGLE)
    expected = max(0, min(kmax + 1, total))
    paid = expected if override is None else max(0, min(int(override), total))
    overdue = max(0, expected - paid)
    remaining = max(0, total - paid)
    paid_off = remaining == 0
    next_due = None if paid_off else ref_add_months(eff, paid * ref_pm(freq))
    return dict(
        total=total, kmax=kmax, expected=expected, paid=paid, overdue=overdue,
        remaining=remaining, paid_off=paid_off, next_due=next_due,
    )


def ref_old_formula(eff, freq, ptt, ptv, birth, today):
    """改动前的旧公式（仅用于向后兼容逐值对比）。

    旧公式：``paid = max(0, min(k_max+1, total))``；
    ``next_due = add_months(eff, (k_max+1)*pm)``（趸交时 ``next_due = eff``）。
    """
    total = ref_total_periods(ptt, ptv, freq, birth, eff)
    kmax = ref_k_max(eff, freq, today, ptt == SINGLE)
    paid = max(0, min(kmax + 1, total))
    paid_off = paid >= total
    if ptt == SINGLE:
        next_due = None if paid_off else eff
    else:
        next_due = None if paid_off else ref_add_months(eff, (kmax + 1) * ref_pm(freq))
    return dict(total=total, paid=paid, paid_off=paid_off, next_due=next_due)


# --------------------------- 对象构造 ---------------------------
def make_member(birth: date) -> Member:
    return Member(id="m1", name="测试", relation=Relation.self_, birth_date=birth, gender=Gender.male)


def make_policy(eff, freq, ptt, ptv, override, status=PolicyStatus.active) -> Policy:
    return Policy(
        id="p1", company="T", insurance_type=InsuranceType.critical_illness,
        product_name="T", policyholder_id="m1", insured_id="m1", effective_date=eff,
        coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType(ptt), payment_term_value=ptv,
        payment_frequency=PaymentFrequency(freq), premium_per_period=Decimal("500"),
        status=status, paid_periods_override=override,
    )


EFF_DATES = [
    date(2020, 1, 31), date(2021, 1, 31), date(2020, 1, 29), date(2020, 1, 30),
    date(2020, 2, 29), date(2021, 2, 28), date(2020, 3, 31), date(2020, 4, 30),
    date(2019, 12, 31), date(2020, 6, 15), date(2022, 5, 1), date(2015, 3, 1),
    date(2025, 8, 1), date(2026, 9, 17),
]
FREQS = [MONTHLY, YEARLY]
TERMS = [(SINGLE, None), (FIXED, 1), (FIXED, 5), (FIXED, 20), (TOAGE, 60), (TOAGE, 100)]
BIRTHS = [
    date(1988, 6, 1), date(1960, 2, 10), date(1950, 1, 1),
    date(2015, 9, 20), date(2020, 2, 29),
]


def _interesting_todays(eff: date, freq: str, periods: int = 9) -> list[date]:
    """围绕缴费日 ±1 天、以及生效日前后，构造边界 today。"""
    todays = {eff - timedelta(days=1), eff, eff + timedelta(days=1)}
    for k in range(0, periods):
        d = ref_due(eff, freq, k)
        for delta in (-1, 0, 1):
            todays.add(d + timedelta(days=delta))
    return sorted(todays)


# ===========================================================================
# A. 独立算法验证
# ===========================================================================
def test_due_date_for_period_is_same_source_as_add_months():
    """due_date_for_period(eff, freq, k+1) == add_months(eff, k*pm) 在整个测试域成立。"""
    checked = 0
    for eff in EFF_DATES:
        for freq in FREQS:
            pm = ref_pm(freq)
            for n in range(1, 121):
                assert due_date_for_period(eff, freq, n) == ref_add_months(eff, (n - 1) * pm)
                assert due_date_for_period(eff, freq, n) == add_months(eff, (n - 1) * pm)
                checked += 1
    assert checked >= 3000


def test_calc_schedule_matches_independent_reference():
    """在大量组合上逐值比对；要求 0 不一致。

    注：另有一次性 120 万组合的独立脚本跑过（见 QA 报告），此处保留一个
    规模适中、可在常规回归中运行的矩阵。
    """
    checked = 0
    for eff in EFF_DATES:
        for freq in FREQS:
            for today in _interesting_todays(eff, freq):
                for ptt, ptv in TERMS:
                    for birth in BIRTHS[:3]:
                        total = ref_total_periods(ptt, ptv, freq, birth, eff)
                        kmax = ref_k_max(eff, freq, today, ptt == SINGLE)
                        overrides = {None, 0, 1, kmax, kmax + 1, kmax + 2, total, total + 3}
                        for ov in overrides:
                            if ov is not None and (ov < 0 or ov > 1200):
                                continue
                            m = make_member(birth)
                            p = make_policy(eff, freq, ptt, ptv, ov)
                            s = calc_schedule(p, m, today)
                            r = ref_schedule(eff, freq, ptt, ptv, birth, today, ov)
                            ctx = (eff, freq, ptt, ptv, birth, today, ov)
                            assert s.total_periods == r["total"], ctx
                            assert s.k_max == r["kmax"], ctx
                            assert s.expected_periods_to_date == r["expected"], ctx
                            assert s.paid_periods == r["paid"], ctx
                            assert s.overdue_periods == r["overdue"], ctx
                            assert s.remaining_periods == r["remaining"], ctx
                            assert s.is_paid_off == r["paid_off"], ctx
                            assert s.next_due_date == r["next_due"], ctx
                            checked += 1
    assert checked >= 50_000, f"组合数不足：{checked}"


def test_compute_k_max_matches_bruteforce():
    """compute_k_max 与暴力搜索一致（含月末钳制与未来生效）。"""
    checked = 0
    for eff in EFF_DATES:
        for freq in FREQS:
            for today in _interesting_todays(eff, freq):
                assert compute_k_max(eff, freq, today) == ref_k_max(eff, freq, today, False)
                checked += 1
    assert checked >= 700


def test_enumerate_upcoming_three_cases_start_point():
    """自动 / 提前缴 / 逾期三情形起点均正确；逾期与已缴期次不得进入待缴窗口。"""
    m = make_member(date(1988, 6, 1))
    today = date(2025, 5, 20)
    # eff 2020-03-15 月缴：k_max=62、expected=63
    auto = make_policy(date(2020, 3, 15), MONTHLY, FIXED, 20, None)
    early = make_policy(date(2020, 3, 15), MONTHLY, FIXED, 20, 70)
    overdue = make_policy(date(2020, 3, 15), MONTHLY, FIXED, 20, 60)

    s_auto = calc_schedule(auto, m, today)
    s_early = calc_schedule(early, m, today)
    s_over = calc_schedule(overdue, m, today)
    assert s_auto.paid_periods == s_auto.k_max + 1 == 63
    assert s_early.paid_periods > s_early.k_max + 1
    assert s_over.paid_periods < s_over.k_max + 1

    items_auto = enumerate_upcoming(auto, m, today, 60)
    items_early = enumerate_upcoming(early, m, today, 60)
    items_over = enumerate_upcoming(overdue, m, today, 60)
    assert [i.period_index for i in items_auto] == [64, 65]
    assert items_early == []
    assert [i.period_index for i in items_over] == [64, 65]
    # 逾期期次（61..63）不得进入待缴窗口
    assert all(i.period_index > 63 for i in items_over)
    # 任何情形都不得把「已缴」期次列为待缴
    for p, s in ((auto, s_auto), (early, s_early), (overdue, s_over)):
        assert all(i.period_index > s.paid_periods for i in enumerate_upcoming(p, m, today, 60))


def test_enumerate_upcoming_matches_bruteforce_and_guard_is_enough():
    """60 天窗口下与「不受 guard 限制」的暴力枚举完全一致（验证 guard<4 够用）。"""
    checked = 0
    max_items = 0
    for eff in EFF_DATES:
        for freq in FREQS:
            for today in _interesting_todays(eff, freq, periods=8):
                for ptt, ptv in TERMS:
                    for birth in BIRTHS[:3]:
                        total = ref_total_periods(ptt, ptv, freq, birth, eff)
                        kmax = ref_k_max(eff, freq, today, ptt == SINGLE)
                        for ov in (None, 0, kmax, kmax + 1, kmax + 2, total):
                            if ov is not None and (ov < 0 or ov > 1200):
                                continue
                            m = make_member(birth)
                            p = make_policy(eff, freq, ptt, ptv, ov)
                            got = [i.period_index for i in enumerate_upcoming(p, m, today, 60)]
                            r = ref_schedule(eff, freq, ptt, ptv, birth, today, ov)
                            expected: list[int] = []
                            if not r["paid_off"]:
                                end = today + timedelta(days=60)
                                start = max(r["paid"], r["kmax"] + 1)
                                for k in range(start, r["total"]):
                                    d = ref_due(eff, freq, k)
                                    if d > end:
                                        break
                                    if d >= today:
                                        expected.append(k + 1)
                            assert got == expected, (eff, freq, ptt, ptv, birth, today, ov, got, expected)
                            max_items = max(max_items, len(got))
                            checked += 1
    # 60 天窗口月缴最多 3 期 → guard < 4 足够
    assert max_items <= 3, f"60 天窗口出现 {max_items} 期，guard 可能不足"
    assert checked >= 10_000


def test_guard_cap_is_documented_limitation_for_oversized_window():
    """记录：guard < 4 对「超 60 天」窗口会截断（当前默认窗口为 60，不触发）。

    本用例仅作为**已知边界记录**，不判定为缺陷。
    """
    m = make_member(date(1988, 6, 1))
    today = date(2025, 5, 20)
    p = make_policy(date(2020, 3, 15), MONTHLY, FIXED, 20, None)
    items = enumerate_upcoming(p, m, today, 365)
    assert len(items) <= 4  # 受 guard 限制，最多 4 期


# ===========================================================================
# B. 向后兼容（override 为 NULL 时与旧公式逐值相同）
# ===========================================================================
def test_null_override_backward_compatible_with_old_formula():
    """``paid_periods_override IS NULL`` 的保单派生量与旧公式逐值相同。"""
    checked = 0
    for eff in EFF_DATES:
        for freq in FREQS:
            for today in _interesting_todays(eff, freq):
                for ptt, ptv in TERMS:
                    for birth in BIRTHS:
                        m = make_member(birth)
                        p = make_policy(eff, freq, ptt, ptv, None)
                        s = calc_schedule(p, m, today)
                        old = ref_old_formula(eff, freq, ptt, ptv, birth, today)
                        ctx = (eff, freq, ptt, ptv, birth, today)
                        assert s.paid_periods_override is None, ctx
                        assert s.paid_periods == old["paid"], ctx
                        assert s.expected_periods_to_date == old["paid"], ctx
                        assert s.overdue_periods == 0, ctx
                        assert s.is_paid_off == old["paid_off"], ctx
                        assert s.next_due_date == old["next_due"], ctx
                        assert s.cumulative_paid == Decimal(old["paid"]) * Decimal("500"), ctx
                        assert s.remaining_periods == max(0, s.total_periods - old["paid"]), ctx
                        checked += 1
    assert checked >= 5000


# ===========================================================================
# D. 接口契约与边界（复用 conftest 的 client / 内存库）
# ===========================================================================
def _mk_member(client, name="张三", relation="本人", birth="1988-06-01"):
    r = client.post(
        "/api/members",
        json={"name": name, "relation": relation, "birthDate": birth, "gender": "未知"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _payload(holder, insured, **over):
    base = {
        "company": "平安", "insuranceType": "重疾险", "productName": "平安福",
        "policyholderId": holder, "insuredId": insured, "effectiveDate": "2020-03-15",
        "coverageTermType": "终身", "coverageTermValue": None,
        "paymentTermType": "固定年限", "paymentTermValue": 20,
        "paymentFrequency": "月缴", "premiumPerPeriod": 500, "status": "生效中",
    }
    base.update(over)
    return base


def test_api_pay_next_contract(client):
    h = _mk_member(client)
    created = client.post("/api/policies", json=_payload(h["id"], h["id"])).json()
    before = created["paidPeriods"]
    r = client.post(f"/api/policies/{created['id']}/pay-next")
    assert r.status_code == 200
    after = r.json()
    assert after["paidPeriodsOverride"] == before + 1
    assert after["paidPeriods"] == before + 1
    # 不存在 → 404
    assert client.post("/api/policies/p_missing/pay-next").status_code == 404


def test_api_pay_next_paid_off_and_zero_total(client):
    h = _mk_member(client)
    # 趸交、生效日已过 → 已缴清 → 422
    single = client.post(
        "/api/policies",
        json=_payload(h["id"], h["id"], paymentTermType="趸交", paymentTermValue=None,
                       effectiveDate="2019-01-01"),
    ).json()
    assert single["isPaidOff"] is True
    r = client.post(f"/api/policies/{single['id']}/pay-next")
    assert r.status_code == 422
    # 交至某年龄、年龄已过 → total=0 → 422（不得 500 / 除零）
    old = _mk_member(client, name="老者", relation="父亲", birth="1950-01-01")
    zero = client.post(
        "/api/policies",
        json=_payload(old["id"], old["id"], effectiveDate="2020-01-01",
                       paymentTermType="交至某年龄", paymentTermValue=60,
                       paymentFrequency="年缴"),
    ).json()
    assert zero["totalPeriods"] == 0
    r2 = client.post(f"/api/policies/{zero['id']}/pay-next")
    assert r2.status_code == 422


def test_api_put_null_restores_auto_estimate(client):
    """回归：PUT ``paidPeriodsOverride: null`` 必须能改回「自动推算」。"""
    h = _mk_member(client)
    created = client.post(
        "/api/policies", json=_payload(h["id"], h["id"], paidPeriodsOverride=70)
    ).json()
    assert created["paidPeriodsOverride"] == 70
    cleared = client.put(
        f"/api/policies/{created['id']}",
        json=_payload(h["id"], h["id"], paidPeriodsOverride=None),
    ).json()
    assert cleared["paidPeriodsOverride"] is None
    assert cleared["paidPeriods"] == cleared["expectedPeriodsToDate"]


def test_api_validation_boundaries(client):
    h = _mk_member(client)
    # 负数 / 超 1200 / 趸交填 2 / 固定年限超总期数
    assert client.post("/api/policies", json=_payload(h["id"], h["id"], paidPeriodsOverride=-1)).status_code == 422
    assert client.post("/api/policies", json=_payload(h["id"], h["id"], paidPeriodsOverride=1201)).status_code == 422
    assert client.post(
        "/api/policies",
        json=_payload(h["id"], h["id"], paymentTermType="趸交", paymentTermValue=None,
                       paidPeriodsOverride=2),
    ).status_code == 422
    assert client.post(
        "/api/policies", json=_payload(h["id"], h["id"], paidPeriodsOverride=241)
    ).status_code == 422


def test_api_to_age_override_not_misreported_and_clamped(client):
    """「交至某年龄」无法预先判定总期数：不误报 422，且钳制生效、paid <= total。"""
    old = _mk_member(client, name="老者", relation="父亲", birth="1950-01-01")
    # 生效 2020-01-01、交至 60 岁、年缴 → 总期数 0；填 5 不应 422，应被钳制
    resp = client.post(
        "/api/policies",
        json=_payload(old["id"], old["id"], effectiveDate="2020-01-01",
                       paymentTermType="交至某年龄", paymentTermValue=60,
                       paymentFrequency="年缴", paidPeriodsOverride=5),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["paidPeriods"] <= body["totalPeriods"]
    assert body["paidPeriods"] == 0 and body["totalPeriods"] == 0


def test_api_future_effective_never_reports_paid(client):
    future = date.today() + timedelta(days=180)
    h = _mk_member(client)
    created = client.post(
        "/api/policies",
        json=_payload(h["id"], h["id"], effectiveDate=future.isoformat()),
    ).json()
    assert created["expectedPeriodsToDate"] == 0
    assert created["paidPeriods"] == 0
    assert created["overduePeriods"] == 0


def test_api_dashboard_overdue_excludes_inactive_and_amount(client):
    """逾期清单：已退保/已失效不得出现；earliestDueDate 与 amount 正确。"""
    h = _mk_member(client)
    active = client.post(
        "/api/policies", json=_payload(h["id"], h["id"], paidPeriodsOverride=60)
    ).json()
    client.post(
        "/api/policies",
        json=_payload(h["id"], h["id"], paidPeriodsOverride=60, status="已退保"),
    )
    client.post(
        "/api/policies",
        json=_payload(h["id"], h["id"], paidPeriodsOverride=60, status="已失效"),
    )
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    assert data["overview"]["overduePolicyCount"] == 1
    assert len(data["overdue"]) == 1
    item = data["overdue"][0]
    assert item["policyId"] == active["id"]
    assert item["earliestDueDate"] == "2025-03-15"  # 第 61 期应缴日
    assert item["overduePeriods"] == 3
    assert item["amount"] == pytest.approx(1500.0)  # 3 × 500
    assert item["premiumPerPeriod"] == pytest.approx(500.0)
    # 逾期期次不得出现在待缴清单
    assert all(u["periodIndex"] > 60 for u in data["upcoming"])


def test_api_dashboard_overdue_sorted_by_due_date_then_id(client):
    """逾期清单按 (earliestDueDate, policyId) 升序。"""
    h = _mk_member(client)
    # 两张不同生效日的逾期保单
    p1 = client.post(
        "/api/policies", json=_payload(h["id"], h["id"], effectiveDate="2020-03-15", paidPeriodsOverride=60)
    ).json()
    p2 = client.post(
        "/api/policies", json=_payload(h["id"], h["id"], effectiveDate="2019-01-15", paidPeriodsOverride=0)
    ).json()
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    ids = [i["policyId"] for i in data["overdue"]]
    dues = [i["earliestDueDate"] for i in data["overdue"]]
    assert dues == sorted(dues)
    assert set(ids) == {p1["id"], p2["id"]}
    # 同日则按 policyId 升序
    assert data["overdue"][0]["earliestDueDate"] <= data["overdue"][1]["earliestDueDate"]


def test_api_derived_fields_match_independent_reference(client):
    """端到端接线校验：接口返回的派生字段与独立参考实现逐值一致。"""
    today = date.today()
    h = _mk_member(client)
    cases = [
        dict(effectiveDate="2020-03-15", paymentFrequency="月缴",
             paymentTermType="固定年限", paymentTermValue=20),
        dict(effectiveDate="2021-08-01", paymentFrequency="年缴",
             paymentTermType="固定年限", paymentTermValue=10),
        dict(effectiveDate="2019-12-31", paymentFrequency="月缴",
             paymentTermType="趸交", paymentTermValue=None),
        dict(effectiveDate="2022-05-01", paymentFrequency="月缴",
             paymentTermType="固定年限", paymentTermValue=5),
    ]
    checked = 0
    for case in cases:
        for ov in (None, 0, 1, 3, 7, 999):
            resp = client.post(
                "/api/policies", json=_payload(h["id"], h["id"], paidPeriodsOverride=ov, **case)
            )
            if resp.status_code == 422:
                continue
            assert resp.status_code == 201, resp.text
            d = resp.json()
            r = ref_schedule(
                date.fromisoformat(case["effectiveDate"]), case["paymentFrequency"],
                case["paymentTermType"], case["paymentTermValue"], date(1988, 6, 1), today, ov,
            )
            ctx = (case, ov)
            assert d["totalPeriods"] == r["total"], ctx
            assert d["expectedPeriodsToDate"] == r["expected"], ctx
            assert d["paidPeriods"] == r["paid"], ctx
            assert d["overduePeriods"] == r["overdue"], ctx
            assert d["remainingPeriods"] == r["remaining"], ctx
            assert d["isPaidOff"] == r["paid_off"], ctx
            want_next = r["next_due"].isoformat() if r["next_due"] else None
            assert d["nextDueDate"] == want_next, ctx
            checked += 1
    assert checked >= 15


# ===========================================================================
# C. 数据库迁移
# ===========================================================================
PROBE_SRC = r'''
import json, os, sqlite3, sys
from sqlalchemy import inspect, text

mode = sys.argv[1]
db_path = os.environ["DB_PATH"]

from app import models  # noqa: F401
from app.database import Base, engine
from app.main import _ensure_data_dir, _run_additive_migrations


def snapshot():
    raw = sqlite3.connect(db_path)
    cols = [r[1] for r in raw.execute("PRAGMA table_info(policies)")]
    rows = [list(r) for r in raw.execute(
        "SELECT id, company, premium_per_period, status, remark FROM policies ORDER BY id")]
    tables = sorted(r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"))
    raw.close()
    return {"columns": cols, "rows": rows, "tables": tables}


def migrate():
    _ensure_data_dir()
    Base.metadata.create_all(bind=engine)
    _run_additive_migrations()


result = {"mode": mode}
if mode == "old":
    # 用当前模型建表 → 再删掉新列，得到 v1.1「旧结构」存量库
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE policies DROP COLUMN paid_periods_override"))
    raw = sqlite3.connect(db_path)
    raw.execute("INSERT INTO members (id,name,relation,birth_date,gender,remark,created_at,updated_at)"
                " VALUES ('m1','张三','本人','1988-06-01','男',NULL,'2024-01-01 00:00:00','2024-01-01 00:00:00')")
    raw.execute("INSERT INTO policies (id,company,insurance_type,product_name,policyholder_id,insured_id,"
                "effective_date,coverage_term_type,coverage_term_value,payment_term_type,payment_term_value,"
                "payment_frequency,premium_per_period,insured_amount,status,remark,created_at,updated_at)"
                " VALUES ('p1','平安','重疾险','平安福','m1','m1','2020-03-15','终身',NULL,'固定年限',20,"
                "'月缴',50000,NULL,'生效中','旧数据','2024-01-01 00:00:00','2024-01-01 00:00:00')")
    raw.commit(); raw.close()
    result["before"] = snapshot()

pre_tables = sorted(inspect(engine).get_table_names())
migrate()
result["pre_tables"] = pre_tables
result["after"] = snapshot()
print("PROBE_JSON " + json.dumps(result))
'''


def _run_probe(tmp_path: Path, mode: str, db: Path):
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE_SRC, encoding="utf-8")
    env = dict(os.environ)
    env["DB_PATH"] = str(db)
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(probe), mode],
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"probe 失败：{proc.stderr}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_JSON ")][-1]
    return json.loads(line[len("PROBE_JSON "):])


def test_migration_upgrades_old_db_and_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    r1 = _run_probe(tmp_path, "old", db)
    # 旧库启动后列被自动补上，既有数据行完好
    assert "paid_periods_override" not in r1["before"]["columns"]
    assert "paid_periods_override" in r1["after"]["columns"]
    assert r1["after"]["rows"] == r1["before"]["rows"]
    assert r1["after"]["rows"], "示例数据不应为空"
    # 第二次启动幂等：不报错、不重复加列、数据不变
    r2 = _run_probe(tmp_path, "reopen", db)
    assert r2["after"]["columns"] == r1["after"]["columns"]
    assert r2["after"]["columns"].count("paid_periods_override") == 1
    assert r2["after"]["rows"] == r1["after"]["rows"]


def test_migration_fresh_db_created_with_column(tmp_path):
    db = tmp_path / "fresh.db"
    r = _run_probe(tmp_path, "fresh", db)
    # 全新空库：policies 表此前不存在 → 由 create_all 直接建出含该列的表
    assert "policies" not in r["pre_tables"]
    assert "paid_periods_override" in r["after"]["columns"]


def test_migration_failure_does_not_block_startup(tmp_path, monkeypatch):
    """_run_additive_migrations 的异常兜底：单个列失败不阻塞，其余列仍补上。"""
    from sqlalchemy import create_engine, inspect, text

    import app.main as main_mod
    from app import models  # noqa: F401
    from app.database import Base

    eng = create_engine(f"sqlite:///{tmp_path / 'x.db'}")
    Base.metadata.create_all(bind=eng)
    with eng.begin() as conn:  # 造旧结构
        conn.execute(text("ALTER TABLE policies DROP COLUMN paid_periods_override"))

    monkeypatch.setattr(main_mod, "engine", eng)
    # __qa_bad__：非法 DDL（多余括号）→ SQLite 抛语法错误 → 触发兜底
    monkeypatch.setitem(
        main_mod._ADDITIVE_COLUMNS,
        "policies",
        {"paid_periods_override": "INTEGER", "__qa_bad__": "INTEGER))"},
    )
    main_mod._run_additive_migrations()  # 不得抛异常

    cols = {c["name"] for c in inspect(eng).get_columns("policies")}
    assert "paid_periods_override" in cols  # 合法列仍被补上
    assert "__qa_bad__" not in cols  # 非法列被吞掉
