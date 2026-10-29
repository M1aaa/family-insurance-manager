"""QA 独立验证（不依赖工程师测试的断言，从 ARCHITECTURE 第 5 节规格独立推导）。

本文件包含两部分：

1. **独立复算**：用一份从规格独立编写的参考实现（``_ref_*``）重新推导
   ARCHITECTURE 5.6 算例集 A / B 的每一张保单派生量与全部聚合指标，
   再与 ``app.calc`` / ``/api/stats/dashboard`` 的实际输出逐值比对。
2. **API 行为验证**：删除冲突、唯一性、条件必填、筛选、错误格式、
   金额类型、``today`` 基准日等 8 类接口行为。

约定：算例基准日 ``today = 2025-05-20``。
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.calc import calc_schedule, enumerate_upcoming
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

TODAY = date(2025, 5, 20)

MONTHLY = "月缴"
YEARLY = "年缴"
SINGLE = "趸交"
FIXED = "固定年限"
TO_AGE_PAY = "交至某年龄"
WHOLE_LIFE = "终身"
TO_AGE_COVER = "至某年龄"


# ---------------------------------------------------------------------------
# 独立参考实现（不复用 app.calc 的任何计算函数）
# ---------------------------------------------------------------------------
def _ref_add_months(d: date, n: int) -> date:
    """加 n 个月，月末钳制。"""
    total = d.year * 12 + (d.month - 1) + n
    year, month0 = divmod(total, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _ref_add_years(d: date, n: int) -> date:
    """加 n 年，2/29 溢出回退 2/28。"""
    try:
        return date(d.year + n, d.month, d.day)
    except ValueError:
        return date(d.year + n, d.month, 28)


def _ref_age(birth: date, on: date) -> int:
    """on 时点周岁。"""
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def _ref_k_max(eff: date, freq: str, today: date) -> int:
    """v1.1 定义：k_max = max{k >= 0 : due(k) < today}（严格小于）；eff >= today → -1。

    ``due(k) = add_months(eff, k × 周期)``，与月末钳制一致。
    """
    if eff >= today:
        return -1
    step = 1 if freq == MONTHLY else 12
    k = 0
    while k < 20000 and _ref_add_months(eff, (k + 1) * step) < today:
        k += 1
    return k


def _ref_pct(num: int, den: int) -> Decimal:
    if den == 0:
        return Decimal("100.0")
    return (Decimal(num) / Decimal(den) * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)


def _ref_money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _ref_schedule(policy, insured, today: date) -> dict:
    """独立推导单张保单的派生量。"""
    freq = policy.payment_frequency.value if hasattr(policy.payment_frequency, "value") else policy.payment_frequency
    ptt = policy.payment_term_type.value if hasattr(policy.payment_term_type, "value") else policy.payment_term_type
    ctt = policy.coverage_term_type.value if hasattr(policy.coverage_term_type, "value") else policy.coverage_term_type
    per_year = 12 if freq == MONTHLY else 1
    eff = policy.effective_date
    prem = Decimal(str(policy.premium_per_period))

    if ptt == SINGLE:
        annualized = Decimal("0")
    elif freq == YEARLY:
        annualized = prem
    else:
        annualized = prem * 12

    if ptt == SINGLE:
        total = 1
    elif ptt == FIXED:
        total = policy.payment_term_value * per_year
    else:
        total = max(0, policy.payment_term_value - _ref_age(insured.birth_date, eff)) * per_year

    if ptt == SINGLE:
        k_max = 0 if eff < today else -1          # v1.1：严格比较（缴费日=今天视为待缴）
        paid = 1 if eff < today else 0
    else:
        k_max = _ref_k_max(eff, freq, today)
        paid = max(0, min(k_max + 1, total))

    remaining = max(0, total - paid)
    is_off = remaining == 0
    progress = _ref_pct(paid, total)
    cumulative = _ref_money(Decimal(paid) * prem)

    if ctt == WHOLE_LIFE:
        in_cov = True
    elif ctt == FIXED:
        in_cov = today <= _ref_add_years(eff, policy.coverage_term_value)
    else:
        in_cov = today <= _ref_add_years(insured.birth_date, policy.coverage_term_value)

    if is_off:
        next_due = None
    elif ptt == SINGLE:
        next_due = eff
    else:
        next_due = _ref_add_months(eff, (k_max + 1) * (1 if freq == MONTHLY else 12))

    return {
        "annualized": _ref_money(annualized),
        "total": total,
        "k_max": k_max,
        "paid": paid,
        "cumulative": cumulative,
        "remaining": remaining,
        "is_paid_off": is_off,
        "progress": progress,
        "in_coverage": in_cov,
        "next_due": next_due,
    }


def _ref_upcoming(policy, insured, today: date, window: int = 60) -> list[tuple[date, int]]:
    """独立推导闭区间 [today, today+window] 内的待缴期次（不含逾期、不含已缴清）。"""
    st = policy.status.value if hasattr(policy.status, "value") else policy.status
    if st != "生效中":
        return []
    s = _ref_schedule(policy, insured, today)
    if s["is_paid_off"]:
        return []
    freq = policy.payment_frequency.value if hasattr(policy.payment_frequency, "value") else policy.payment_frequency
    step = 1 if freq == MONTHLY else 12
    end = today + timedelta(days=window)
    out = []
    k = s["k_max"] + 1
    while k < s["total"]:
        due = _ref_add_months(policy.effective_date, k * step)
        if due > end:
            break
        if due >= today:
            out.append((due, k + 1))
        k += 1
    return out


# 算例集 A 的逐单期望（ARCHITECTURE 5.6，逐值手算）
EXAMPLE_A_EXPECTED = {
    "p_A": dict(annualized="6000.00", total=240, paid=63, cumulative="31500.00",
                remaining=177, is_paid_off=False, progress="26.3", in_coverage=True,
                next_due=date(2025, 6, 15)),
    "p_B": dict(annualized="8000.00", total=10, paid=4, cumulative="32000.00",
                remaining=6, is_paid_off=False, progress="40.0", in_coverage=True,
                next_due=date(2025, 8, 1)),
    "p_C": dict(annualized="3000.00", total=20, paid=1, cumulative="3000.00",
                remaining=19, is_paid_off=False, progress="5.0", in_coverage=True,
                next_due=date(2025, 6, 1)),
    "p_D": dict(annualized="0.00", total=1, paid=1, cumulative="50000.00",
                remaining=0, is_paid_off=True, progress="100.0", in_coverage=True,
                next_due=None),
}

EXAMPLE_B_EXPECTED = {
    "p_E": dict(annualized="2400.00", total=120, paid=0, cumulative="0.00",
                remaining=120, is_paid_off=False, progress="0.0", in_coverage=True,
                next_due=date(2025, 8, 1)),
    "p_F": dict(annualized="12000.00", total=5, paid=5, cumulative="60000.00",
                remaining=0, is_paid_off=True, progress="100.0", in_coverage=True,
                next_due=None),
    "p_G": dict(annualized="0.00", total=1, paid=1, cumulative="50000.00",
                remaining=0, is_paid_off=True, progress="100.0", in_coverage=True,
                next_due=None),
}


def _assert_schedule_matches(sched, expected: dict) -> None:
    assert sched.annualized_premium == Decimal(expected["annualized"])
    assert sched.total_periods == expected["total"]
    assert sched.paid_periods == expected["paid"]
    assert sched.cumulative_paid == Decimal(expected["cumulative"])
    assert sched.remaining_periods == expected["remaining"]
    assert sched.is_paid_off is expected["is_paid_off"]
    assert sched.progress_percent == Decimal(expected["progress"])
    assert sched.in_coverage is expected["in_coverage"]
    assert sched.next_due_date == expected["next_due"]


# ---------------------------------------------------------------------------
# 第 1 步：算例集 A / B 逐单独立复算
# ---------------------------------------------------------------------------
def test_example_a_per_policy_matches_spec(db_session, seed_example_a):
    """算例集 A：逐单派生量与独立手算值、独立参考实现三方一致。"""
    for pid, expected in EXAMPLE_A_EXPECTED.items():
        policy = db_session.get(Policy, pid)
        insured = db_session.get(Member, policy.insured_id)
        impl = calc_schedule(policy, insured, TODAY)
        _assert_schedule_matches(impl, expected)
        ref = _ref_schedule(policy, insured, TODAY)
        assert impl.annualized_premium == ref["annualized"]
        assert impl.total_periods == ref["total"]
        assert impl.paid_periods == ref["paid"]
        assert impl.cumulative_paid == ref["cumulative"]
        assert impl.next_due_date == ref["next_due"]


def test_example_b_per_policy_matches_spec(db_session, seed_example_b):
    """算例集 B：生效日在未来(k_max=-1)、交至某年龄、趸交 逐单一致。"""
    for pid, expected in EXAMPLE_B_EXPECTED.items():
        policy = db_session.get(Policy, pid)
        insured = db_session.get(Member, policy.insured_id)
        impl = calc_schedule(policy, insured, TODAY)
        _assert_schedule_matches(impl, expected)


def test_example_a_upcoming_periods(db_session, seed_example_a):
    """算例集 A：待缴期次 = 06-01(C,k=1) / 06-15(A,k=63) / 07-15(A,k=64)。"""
    ref_items = []
    impl_items = []
    for pid in ["p_A", "p_B", "p_C", "p_D"]:
        policy = db_session.get(Policy, pid)
        insured = db_session.get(Member, policy.insured_id)
        ref_items.extend(_ref_upcoming(policy, insured, TODAY))
        impl_items.extend(
            (item.due_date, item.period_index)
            for item in enumerate_upcoming(policy, insured, TODAY)
        )
    expected = [
        (date(2025, 6, 1), 2),
        (date(2025, 6, 15), 64),
        (date(2025, 7, 15), 65),
    ]
    assert sorted(ref_items) == expected
    assert sorted(impl_items) == expected


def test_example_a_dashboard_values(client, seed_example_a):
    """算例集 A：看板全部聚合指标逐值吻合。"""
    resp = client.get("/api/stats/dashboard", params={"today": "2025-05-20"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["asOfDate"] == "2025-05-20"
    ov = data["overview"]
    # v1.4 双口径：未缴费 = 旧「年度总保费」；应缴总保费含已缴清（A 中 p_D 趸交年化 0 → 无差异）
    assert ov["unpaidAnnualPremium"] == 17000.00
    assert ov["annualTotalPremium"] == 17000.00
    assert ov["upcoming60Premium"] == 4000.00
    assert ov["upcoming60Count"] == 3
    assert ov["policyTotal"] == 4
    assert ov["activePolicyCount"] == 3
    assert ov["insuredPersonCount"] == 3

    by_insured = {row["name"]: row for row in data["byMember"]["byInsured"]}
    assert by_insured["张三"]["policyCount"] == 2
    assert by_insured["张三"]["annualPremium"] == 6000.00
    assert by_insured["张三"]["upcoming60Premium"] == 1000.00
    assert by_insured["张三"]["paidToDatePremium"] == 81500.00  # A(31500) + D(50000)
    assert by_insured["李四"]["annualPremium"] == 8000.00
    assert by_insured["李四"]["paidToDatePremium"] == 32000.00
    assert by_insured["小张"]["annualPremium"] == 3000.00
    assert by_insured["小张"]["upcoming60Premium"] == 3000.00

    # byType 按年化保费降序
    assert [t["insuranceType"] for t in data["byType"]] == ["医疗险", "重疾险", "意外险"]
    assert [t["annualPremium"] for t in data["byType"]] == [8000.00, 6000.00, 3000.00]

    # upcoming 按 dueDate 升序
    assert [u["dueDate"] for u in data["upcoming"]] == ["2025-06-01", "2025-06-15", "2025-07-15"]
    assert [u["premiumPerPeriod"] for u in data["upcoming"]] == [3000.00, 500.00, 500.00]


def test_example_b_dashboard_values(client, seed_example_b):
    """算例集 B：未缴费 2400、应缴总保费 14400（含已缴清 p_F 年化 12000）、待缴 0、在保 2 人。"""
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    ov = data["overview"]
    assert ov["unpaidAnnualPremium"] == 2400.00
    assert ov["annualTotalPremium"] == 14400.00
    assert ov["upcoming60Premium"] == 0.00
    assert ov["upcoming60Count"] == 0
    assert ov["policyTotal"] == 3
    assert ov["activePolicyCount"] == 3
    assert ov["insuredPersonCount"] == 2

    by_insured = {row["name"]: row for row in data["byMember"]["byInsured"]}
    assert by_insured["张三"]["policyCount"] == 2
    assert by_insured["张三"]["annualPremium"] == 2400.00
    assert by_insured["张三"]["paidToDatePremium"] == 50000.00
    assert by_insured["王五"]["policyCount"] == 1
    # v1.6：年化口径放宽为「生效中 + 已缴清」
    assert by_insured["王五"]["annualPremium"] == 12000.00
    assert by_insured["王五"]["paidToDatePremium"] == 60000.00


def test_by_policyholder_dimension(client, seed_example_a):
    """byPolicyholder 维度：按投保人分组（全部挂在 m1 张三下）。"""
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    rows = data["byMember"]["byPolicyholder"]
    assert len(rows) == 1
    assert rows[0]["name"] == "张三"
    assert rows[0]["policyCount"] == 4
    assert rows[0]["annualPremium"] == 17000.00


# ---------------------------------------------------------------------------
# 第 3 步：API 行为验证
# ---------------------------------------------------------------------------
def _member_payload(name, relation, birth, gender="未知"):
    return {"name": name, "relation": relation, "birthDate": birth, "gender": gender}


def _policy_payload(**overrides):
    payload = {
        "company": "中国平安",
        "insuranceType": "重疾险",
        "productName": "平安福2024",
        "policyholderId": "m1",
        "insuredId": "m1",
        "effectiveDate": "2020-03-15",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 20,
        "paymentFrequency": "月缴",
        "premiumPerPeriod": 500.0,
        "insuredAmount": 500000.0,
        "status": "生效中",
    }
    payload.update(overrides)
    return payload


def test_delete_referenced_member_returns_409(client, seed_example_a):
    """被保单引用的成员删除 → 409 MEMBER_IN_USE，message 含关联保单数。"""
    resp = client.delete("/api/members/m1")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "MEMBER_IN_USE"
    assert body["detail"]["policyCount"] == 4
    assert "4" in body["message"]


def test_delete_unreferenced_member_ok(client, seed_example_a, db_session):
    """未被引用的成员可正常删除。"""
    db_session.add(Member(id="m9", name="路人", relation=Relation.other,
                          birth_date=date(1999, 1, 1), gender=Gender.unknown))
    db_session.commit()
    resp = client.delete("/api/members/m9")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def test_duplicate_primary_member_returns_409(client, seed_example_a):
    """新增第二个「本人」→ 409 DUPLICATE_PRIMARY_MEMBER。"""
    resp = client.post("/api/members", json=_member_payload("李鬼", "本人", "1990-01-01"))
    assert resp.status_code == 409
    assert resp.json()["code"] == "DUPLICATE_PRIMARY_MEMBER"


def test_update_to_duplicate_primary_returns_409(client, seed_example_a):
    """编辑出第二个「本人」→ 409。"""
    resp = client.put("/api/members/m2", json=_member_payload("李四", "本人", "1990-03-12"))
    assert resp.status_code == 409
    assert resp.json()["code"] == "DUPLICATE_PRIMARY_MEMBER"


@pytest.mark.parametrize(
    "overrides, label",
    [
        ({"coverageTermType": "终身", "coverageTermValue": 10}, "终身+数值"),
        ({"coverageTermType": "固定年限", "coverageTermValue": None}, "固定年限无数值"),
        ({"paymentTermType": "趸交", "paymentTermValue": 5}, "趸交+缴费数值"),
    ],
)
def test_policy_conditional_required_422(client, seed_example_a, overrides, label):
    """条件必填校验：违反 → 422 VALIDATION_ERROR。"""
    resp = client.post("/api/policies", json=_policy_payload(**overrides))
    assert resp.status_code == 422, label
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_policy_missing_member_422(client, seed_example_a):
    """投保人 / 被保人 id 不存在 → 422。"""
    r1 = client.post("/api/policies", json=_policy_payload(policyholderId="m_nope"))
    assert r1.status_code == 422 and r1.json()["code"] == "VALIDATION_ERROR"
    r2 = client.post("/api/policies", json=_policy_payload(insuredId="m_nope"))
    assert r2.status_code == 422 and r2.json()["code"] == "VALIDATION_ERROR"


def test_policy_filters_and_keyword(client, seed_example_a):
    """六个筛选参数分别生效，组合为 AND；keyword 对 company/productName 不区分大小写。"""
    # 单参数
    assert {p["id"] for p in client.get("/api/policies", params={"insuredId": "m1"}).json()} == {"p_A", "p_D"}
    assert {p["id"] for p in client.get("/api/policies", params={"policyholderId": "m1"}).json()} == {"p_A", "p_B", "p_C", "p_D"}
    assert {p["id"] for p in client.get("/api/policies", params={"insuranceType": "重疾险"}).json()} == {"p_A", "p_D"}
    assert {p["id"] for p in client.get("/api/policies", params={"status": "已缴清"}).json()} == {"p_D"}
    assert {p["id"] for p in client.get("/api/policies", params={"paymentFrequency": "年缴"}).json()} == {"p_B", "p_C"}
    # keyword 不区分大小写（company=平安：p_A / p_C / p_D 公司均为「平安」）
    assert {p["id"] for p in client.get("/api/policies", params={"keyword": "平安"}).json()} == {"p_A", "p_C", "p_D"}
    # 组合 AND
    combo = client.get("/api/policies", params={"insuredId": "m1", "insuranceType": "重疾险", "status": "已缴清"}).json()
    assert {p["id"] for p in combo} == {"p_D"}


def test_keyword_case_insensitive(client, db_session):
    """keyword 对 company 不区分大小写（用英文公司名验证）。"""
    db_session.add(Member(id="mm", name="测试", relation=Relation.self_,
                          birth_date=date(1988, 1, 1), gender=Gender.male))
    db_session.add(Policy(id="p_k", company="PingAn", insurance_type=InsuranceType.medical,
                          product_name="SuperCare", policyholder_id="mm", insured_id="mm",
                          effective_date=date(2020, 1, 1),
                          coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
                          payment_term_type=PaymentTermType.fixed_years, payment_term_value=10,
                          payment_frequency=PaymentFrequency.yearly, premium_per_period=1000,
                          status=PolicyStatus.active))
    db_session.commit()
    assert {p["id"] for p in client.get("/api/policies", params={"keyword": "pingan"}).json()} == {"p_k"}
    assert {p["id"] for p in client.get("/api/policies", params={"keyword": "super"}).json()} == {"p_k"}


def test_error_body_shape(client, seed_example_a):
    """错误响应体统一为 {code, message, detail}。"""
    resp = client.delete("/api/members/m1")
    body = resp.json()
    assert set(body.keys()) == {"code", "message", "detail"}
    # 校验错误
    r2 = client.post("/api/policies", json=_policy_payload(coverageTermType="终身", coverageTermValue=10))
    assert set(r2.json().keys()) == {"code", "message", "detail"}
    # 404
    r3 = client.get("/api/policies/p_none")
    assert r3.status_code == 404
    assert set(r3.json().keys()) == {"code", "message", "detail"}


def test_money_fields_are_json_numbers(client, seed_example_a):
    """金额 / 百分比在响应中是 number 而非字符串（Pydantic v2 Decimal 陷阱）。"""
    policies = client.get("/api/policies").json()
    for p in policies:
        for key in ["premiumPerPeriod", "annualizedPremium", "cumulativePaid", "progressPercent"]:
            assert isinstance(p[key], (int, float)) and not isinstance(p[key], str), key
        if p["insuredAmount"] is not None:
            assert isinstance(p["insuredAmount"], (int, float))

    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    for key in ["annualTotalPremium", "unpaidAnnualPremium", "upcoming60Premium"]:
        assert isinstance(data["overview"][key], (int, float))
    for row in data["byMember"]["byInsured"]:
        for key in ["annualPremium", "upcoming60Premium", "paidToDatePremium"]:
            assert isinstance(row[key], (int, float))
    for row in data["byType"]:
        assert isinstance(row["annualPremium"], (int, float))


def test_today_param_changes_baseline(client, seed_example_a):
    """today 参数确实改变计算基准日（两个不同 today 结果不同）。"""
    may = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    jun = client.get("/api/stats/dashboard", params={"today": "2025-06-20"}).json()
    assert may["asOfDate"] == "2025-05-20"
    assert jun["asOfDate"] == "2025-06-20"
    # 基准日不同 → 待缴清单的缴费日集合不同
    may_dates = [u["dueDate"] for u in may["upcoming"]]
    jun_dates = [u["dueDate"] for u in jun["upcoming"]]
    assert may_dates == ["2025-06-01", "2025-06-15", "2025-07-15"]
    assert jun_dates == ["2025-07-15", "2025-08-01", "2025-08-15"]
    assert may_dates != jun_dates


def test_today_param_affects_paid_periods(client, seed_example_a):
    """today 前移后，A 的累计已缴从 31500 → 32000（多缴 1 期）。"""
    jun = client.get("/api/stats/dashboard", params={"today": "2025-06-20"}).json()
    zhang = {r["name"]: r for r in jun["byMember"]["byInsured"]}["张三"]
    assert zhang["paidToDatePremium"] == 82000.00  # A(32000) + D(50000)


# ---------------------------------------------------------------------------
# 第 4 步：疑点探测（只报告，不断言为 bug）—— 已退保 + 终身
# ---------------------------------------------------------------------------
def test_probe_surrendered_whole_life_policy(client, db_session):
    """疑点（v1.1 已裁定）：status=已退保 且 coverageTermType=终身 的保单如何计入各指标。

    v1.1 口径（ARCHITECTURE 5.5）：
      - `insuredPersonCount = DISTINCT insured_id WHERE in_coverage AND status IN ('生效中','已缴清')`
        → 已退保保单的被保人**不计入**在保人数（v1.0 会误计入）；
      - byType / byMember 的 policyCount、paidToDatePremium **计入**（历史已缴）；
      - annualPremium / 年度总保费 **排除**。
    """
    db_session.add(Member(id="mx", name="甲", relation=Relation.self_,
                          birth_date=date(1988, 6, 1), gender=Gender.male))
    db_session.add(Policy(id="p_s", company="某公司", insurance_type=InsuranceType.critical_illness,
                          product_name="退保产品", policyholder_id="mx", insured_id="mx",
                          effective_date=date(2019, 1, 1),
                          coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
                          payment_term_type=PaymentTermType.fixed_years, payment_term_value=20,
                          payment_frequency=PaymentFrequency.yearly, premium_per_period=5000,
                          status=PolicyStatus.surrendered))
    db_session.commit()

    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    ov = data["overview"]
    assert ov["insuredPersonCount"] == 0            # v1.1：已退保不计入在保人数
    assert ov["annualTotalPremium"] == 0.00         # 应缴总保费排除（已退保 ∉ {生效中, 已缴清}）
    assert ov["unpaidAnnualPremium"] == 0.00        # 未缴费金额同样排除
    assert ov["activePolicyCount"] == 0
    assert data["byType"][0]["policyCount"] == 1    # byType 计数计入（历史）
    assert data["byType"][0]["paidToDatePremium"] == 35000.00
    assert data["byMember"]["byInsured"][0]["policyCount"] == 1


def test_probe_paid_off_still_counted_in_insured_person_count(client, db_session):
    """v1.1：status=已缴清 且 in_coverage 的保单，其被保人**仍计入**在保人数。"""
    db_session.add(Member(id="mp", name="乙", relation=Relation.self_,
                          birth_date=date(1988, 6, 1), gender=Gender.male))
    db_session.add(Policy(id="p_p", company="某公司", insurance_type=InsuranceType.critical_illness,
                          product_name="缴清产品", policyholder_id="mp", insured_id="mp",
                          effective_date=date(2019, 1, 1),
                          coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
                          payment_term_type=PaymentTermType.single, payment_term_value=None,
                          payment_frequency=PaymentFrequency.monthly, premium_per_period=50000,
                          status=PolicyStatus.paid_off))
    db_session.commit()

    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    assert data["overview"]["insuredPersonCount"] == 1
