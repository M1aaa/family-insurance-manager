"""缴费记录（``paid_periods_override``）与逾期增量功能的回归测试。

覆盖本次增量（v1.2）：
- ``due_date_for_period`` 与 ``compute_k_max`` 同源；
- ``calc_schedule`` 的「应缴 / 已缴 / 逾期」双路径与向后兼容不变式；
- ``enumerate_upcoming`` 起点 ``max(paid_periods, k_max + 1)`` 在自动 / 提前缴 / 逾期三情形下均正确；
- ``POST /api/policies/{id}/pay-next`` 接口契约（推进 / 逾期优先 / 已缴清 422 / 不存在 404）；
- 请求校验（非负、上限、与总期数交叉校验）；
- 看板 ``overdue`` 清单与 ``overview.overduePolicyCount``（camelCase 字段对齐）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.calc import (
    add_months,
    calc_schedule,
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

TODAY = date(2025, 5, 20)


# ---------------------------------------------------------------------------
# 纯函数构造辅助（不落库）
# ---------------------------------------------------------------------------
def _member(birth: date = date(1988, 6, 1), id: str = "m1") -> Member:
    return Member(id=id, name="张三", relation=Relation.self_, birth_date=birth, gender=Gender.male)


def _policy(**kw) -> Policy:
    base = dict(
        id="p1",
        company="测试",
        insurance_type=InsuranceType.critical_illness,
        product_name="测试产品",
        policyholder_id="m1",
        insured_id="m1",
        effective_date=date(2020, 3, 15),
        coverage_term_type=CoverageTermType.whole_life,
        coverage_term_value=None,
        payment_term_type=PaymentTermType.fixed_years,
        payment_term_value=20,
        payment_frequency=PaymentFrequency.monthly,
        premium_per_period=Decimal("500"),
        status=PolicyStatus.active,
    )
    base.update(kw)
    return Policy(**base)


# ---------------------------------------------------------------------------
# due_date_for_period 与 k_max 同源
# ---------------------------------------------------------------------------
def test_due_date_for_period_matches_add_months():
    eff = date(2020, 1, 31)
    for k in range(0, 6):
        assert due_date_for_period(eff, "月缴", k + 1) == add_months(eff, k)
    # 年缴
    assert due_date_for_period(date(2021, 8, 1), "年缴", 1) == date(2021, 8, 1)
    assert due_date_for_period(date(2021, 8, 1), "年缴", 5) == date(2025, 8, 1)
    # 趸交（第 1 期即生效日）
    assert due_date_for_period(date(2019, 1, 1), "月缴", 1) == date(2019, 1, 1)


# ---------------------------------------------------------------------------
# 向后兼容不变式：override 为 None 时与旧公式逐值相同
# ---------------------------------------------------------------------------
def test_override_none_is_backward_compatible():
    m = _member()
    cases = [
        _policy(id="a", effective_date=date(2020, 3, 15)),  # 月缴 20 年
        _policy(
            id="b",
            effective_date=date(2021, 8, 1),
            payment_frequency=PaymentFrequency.yearly,
            payment_term_value=10,
            premium_per_period=Decimal("8000"),
        ),  # 年缴 10 期
        _policy(id="c", effective_date=date(2025, 8, 1)),  # 生效日在未来
    ]
    for p in cases:
        s = calc_schedule(p, m, TODAY)
        # 旧公式
        old_paid = max(0, min(s.k_max + 1, s.total_periods))
        assert s.paid_periods == old_paid
        assert s.paid_periods_override is None
        assert s.expected_periods_to_date == old_paid
        assert s.overdue_periods == 0
        assert s.cumulative_paid == Decimal(old_paid) * Decimal(str(p.premium_per_period))
        # next_due 与旧公式一致
        if s.is_paid_off:
            assert s.next_due_date is None
        else:
            assert s.next_due_date == due_date_for_period(
                p.effective_date, p.payment_frequency.value, old_paid + 1
            )


# ---------------------------------------------------------------------------
# 提前缴 / 逾期语义
# ---------------------------------------------------------------------------
def test_override_early_pay():
    m = _member()
    p = _policy(paid_periods_override=70)  # 应缴 63，手动记录 70
    s = calc_schedule(p, m, TODAY)
    assert (s.expected_periods_to_date, s.paid_periods, s.overdue_periods) == (63, 70, 0)
    assert s.is_paid_off is False
    assert s.next_due_date == date(2026, 1, 15)  # 第 71 期


def test_override_overdue():
    m = _member()
    p = _policy(paid_periods_override=60)  # 应缴 63，漏缴 3 期
    s = calc_schedule(p, m, TODAY)
    assert (s.expected_periods_to_date, s.paid_periods, s.overdue_periods) == (63, 60, 3)
    # 最早未缴期次 = 第 61 期，落在过去
    assert s.next_due_date == date(2025, 3, 15)
    assert s.next_due_date < TODAY
    assert s.cumulative_paid == Decimal("30000.00")


# ---------------------------------------------------------------------------
# enumerate_upcoming 起点：max(paid_periods, k_max + 1)
# ---------------------------------------------------------------------------
def test_enumerate_upcoming_start_point_three_cases():
    m = _member()
    auto = _policy(paid_periods_override=None)
    early = _policy(paid_periods_override=70)
    overdue = _policy(paid_periods_override=60)

    # 自动：与旧行为一致，列出 [64, 65]
    assert [i.period_index for i in enumerate_upcoming(auto, m, TODAY, 60)] == [64, 65]
    # 提前缴：已缴至 70 期 → 60 天窗口内无待缴（第 71 期在 2026-01-15）
    assert enumerate_upcoming(early, m, TODAY, 60) == []
    # 逾期：逾期期次（61..63）不得进入待缴窗口；仍列出 [64, 65]
    assert [i.period_index for i in enumerate_upcoming(overdue, m, TODAY, 60)] == [64, 65]
    # 任何情形下待缴期次都不能是「已缴」期次
    for p in (auto, early, overdue):
        s = calc_schedule(p, m, TODAY)
        assert all(i.period_index > s.paid_periods for i in enumerate_upcoming(p, m, TODAY, 60))


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_single_override_zero_and_one():
    m = _member()
    unpaid = _policy(
        paid_periods_override=0,
        effective_date=date(2019, 1, 1),
        payment_term_type=PaymentTermType.single,
        payment_term_value=None,
        premium_per_period=Decimal("50000"),
    )
    s0 = calc_schedule(unpaid, m, TODAY)
    assert (s0.total_periods, s0.paid_periods, s0.overdue_periods) == (1, 0, 1)
    assert s0.is_paid_off is False
    assert s0.next_due_date == date(2019, 1, 1)

    paid = _policy(
        paid_periods_override=1,
        effective_date=date(2019, 1, 1),
        payment_term_type=PaymentTermType.single,
        payment_term_value=None,
        premium_per_period=Decimal("50000"),
    )
    s1 = calc_schedule(paid, m, TODAY)
    assert (s1.paid_periods, s1.is_paid_off, s1.next_due_date) == (1, True, None)


def test_future_effective_never_reports_paid():
    m = _member()
    p = _policy(effective_date=date(2025, 8, 1))
    s = calc_schedule(p, m, TODAY)
    assert s.k_max == -1
    assert s.expected_periods_to_date == 0
    assert s.paid_periods == 0  # 不应显示为「已缴 1 期」
    assert s.overdue_periods == 0


def test_total_zero_no_division_and_override_clamped():
    old = _member(birth=date(1950, 1, 1), id="m9")
    p = _policy(
        id="z",
        policyholder_id="m9",
        insured_id="m9",
        effective_date=date(2020, 1, 1),
        payment_term_type=PaymentTermType.to_age,
        payment_term_value=60,
        payment_frequency=PaymentFrequency.yearly,
        paid_periods_override=5,  # 超出总期数（0），须被钳制
    )
    s = calc_schedule(p, old, TODAY)
    assert s.total_periods == 0
    assert s.paid_periods == 0  # 防御性钳制
    assert s.progress_percent == Decimal("100.0")
    assert s.is_paid_off is True
    assert s.next_due_date is None
    assert enumerate_upcoming(p, old, TODAY, 60) == []


def test_override_exceeds_total_clamped():
    m = _member()
    p = _policy(paid_periods_override=999)  # 总期数 240，但若为固定年限会更大
    s = calc_schedule(p, m, TODAY)
    assert s.paid_periods == 240
    assert s.is_paid_off is True


# ---------------------------------------------------------------------------
# API：create / update 落库 paidPeriodsOverride
# ---------------------------------------------------------------------------
def _create_member(client, name="张三", relation="本人", birth_date="1988-06-01"):
    return client.post(
        "/api/members",
        json={"name": name, "relation": relation, "birthDate": birth_date, "gender": "未知"},
    ).json()


def _payload(holder_id, insured_id, **overrides):
    payload = {
        "company": "平安",
        "insuranceType": "重疾险",
        "productName": "平安福",
        "policyholderId": holder_id,
        "insuredId": insured_id,
        "effectiveDate": "2020-03-15",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 20,
        "paymentFrequency": "月缴",
        "premiumPerPeriod": 500,
        "status": "生效中",
    }
    payload.update(overrides)
    return payload


def test_create_policy_with_paid_override(client):
    holder = _create_member(client)
    body = client.post(
        "/api/policies", json=_payload(holder["id"], holder["id"], paidPeriodsOverride=60)
    ).json()
    assert body["paidPeriodsOverride"] == 60
    assert body["paidPeriods"] == 60  # clamp(override, 0, total=240)
    # 服务端用真实「今天」计算应缴期数，此处只校验字段与不变量，不写死数值
    assert isinstance(body["expectedPeriodsToDate"], int)
    assert body["overduePeriods"] == max(0, body["expectedPeriodsToDate"] - 60)


def test_update_policy_sets_and_clears_override(client):
    holder = _create_member(client)
    created = client.post("/api/policies", json=_payload(holder["id"], holder["id"])).json()
    assert created["paidPeriodsOverride"] is None

    # 设为 70（提前缴）
    up1 = client.put(
        f"/api/policies/{created['id']}",
        json=_payload(holder["id"], holder["id"], paidPeriodsOverride=70),
    ).json()
    assert up1["paidPeriodsOverride"] == 70

    # 改回「自动推算」：必须能被清空（回归：曾因并行编辑竞态丢失该赋值）
    up2 = client.put(
        f"/api/policies/{created['id']}",
        json=_payload(holder["id"], holder["id"], paidPeriodsOverride=None),
    ).json()
    assert up2["paidPeriodsOverride"] is None


def test_override_validation(client):
    holder = _create_member(client)
    # 负数
    assert client.post(
        "/api/policies", json=_payload(holder["id"], holder["id"], paidPeriodsOverride=-1)
    ).status_code == 422
    # 超过可判定总期数（固定年限 20 年 月缴 = 240）
    assert client.post(
        "/api/policies", json=_payload(holder["id"], holder["id"], paidPeriodsOverride=241)
    ).status_code == 422
    # 上限 1200
    assert client.post(
        "/api/policies", json=_payload(holder["id"], holder["id"], paidPeriodsOverride=1201)
    ).status_code == 422


# ---------------------------------------------------------------------------
# API：pay-next
# ---------------------------------------------------------------------------
def test_pay_next_advances_one_period(client):
    holder = _create_member(client)
    created = client.post("/api/policies", json=_payload(holder["id"], holder["id"])).json()
    # 从「自动推算」起，pay-next 应写入 paid+1
    before = created["paidPeriods"]
    resp = client.post(f"/api/policies/{created['id']}/pay-next")
    assert resp.status_code == 200
    after = resp.json()
    assert after["paidPeriodsOverride"] == before + 1
    assert after["paidPeriods"] == before + 1


def test_pay_next_prefers_overdue(client):
    holder = _create_member(client)
    created = client.post("/api/policies", json=_payload(holder["id"], holder["id"])).json()
    expected = created["expectedPeriodsToDate"]
    assert expected >= 3  # 生效日 2020-03-15，服务端「今天」必已过 3 期
    # 手动少记 3 期 → 制造逾期（与真实「今天」解耦）
    overdue_override = expected - 3
    upd = client.put(
        f"/api/policies/{created['id']}",
        json=_payload(holder["id"], holder["id"], paidPeriodsOverride=overdue_override),
    ).json()
    assert upd["overduePeriods"] == 3
    after = client.post(f"/api/policies/{created['id']}/pay-next").json()
    assert after["paidPeriodsOverride"] == overdue_override + 1
    assert after["overduePeriods"] == 2  # 补缴最早那一期


def test_pay_next_paid_off_returns_422(client):
    holder = _create_member(client)
    created = client.post(
        "/api/policies",
        json=_payload(
            holder["id"],
            holder["id"],
            paymentTermType="趸交",
            paymentTermValue=None,
            effectiveDate="2019-01-01",
        ),
    ).json()
    assert created["isPaidOff"] is True
    resp = client.post(f"/api/policies/{created['id']}/pay-next")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_pay_next_zero_total_returns_422(client):
    old = _create_member(client, name="老者", relation="父亲", birth_date="1950-01-01")
    created = client.post(
        "/api/policies",
        json=_payload(
            old["id"],
            old["id"],
            effectiveDate="2020-01-01",
            paymentTermType="交至某年龄",
            paymentTermValue=60,
            paymentFrequency="年缴",
        ),
    ).json()
    assert created["totalPeriods"] == 0
    resp = client.post(f"/api/policies/{created['id']}/pay-next")
    assert resp.status_code == 422  # 不崩溃


def test_pay_next_not_found(client):
    resp = client.post("/api/policies/p_not_exist/pay-next")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# API：看板 overdue 清单与计数
# ---------------------------------------------------------------------------
def test_dashboard_overdue_list_and_count(client):
    holder = _create_member(client)
    client.post(
        "/api/policies", json=_payload(holder["id"], holder["id"], paidPeriodsOverride=60)
    )
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()

    assert data["overview"]["overduePolicyCount"] == 1
    assert len(data["overdue"]) == 1
    item = data["overdue"][0]
    # camelCase 字段对齐（earliest_due_date → earliestDueDate）
    assert item["earliestDueDate"] == "2025-03-15"
    assert item["overduePeriods"] == 3
    assert item["amount"] == 1500.0
    assert item["premiumPerPeriod"] == 500.0
    assert item["insuredName"] == "张三"
    # 逾期期次不得出现在待缴清单
    assert all(u["periodIndex"] > 60 for u in data["upcoming"])


def test_dashboard_overdue_empty_when_no_override(client):
    holder = _create_member(client)
    client.post("/api/policies", json=_payload(holder["id"], holder["id"]))
    data = client.get("/api/stats/dashboard", params={"today": "2025-05-20"}).json()
    assert data["overview"]["overduePolicyCount"] == 0
    assert data["overdue"] == []
