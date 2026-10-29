"""QA 独立验证 —— 多被保人统计 / 算例集 A·B·C 逐值重算（v1.3）。

**不信任工程师自测结论**：本文件用独立构造的数据，逐值核对
``docs/ARCHITECTURE.md`` §5.6 的期望值，并攻击：

- 尾差归属是否真的落在**规范排序最后一名**（用打乱插入顺序的 3 人保单证伪）；
- 顶部卡片 / ``byType`` / ``byPolicyholder`` 是否**不分摊**（多被保人时仍为全额）；
- ``insuredPersonCount`` 去重口径；
- 算例集 C 的 5 条 ``k_max`` 边界（独立于 test_calc.py 重算）。
"""

from __future__ import annotations

from datetime import date

from app.calc import calc_schedule
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


def _payload(**over) -> dict:
    base = {
        "company": "测试保险",
        "insuranceType": "重疾险",
        "productName": "测试产品",
        "policyholderId": "m1",
        "insuredIds": ["m1"],
        "primaryInsuredId": "m1",
        "effectiveDate": "2025-01-01",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 10,
        "paymentFrequency": "年缴",
        "premiumPerPeriod": 1000,
    }
    base.update(over)
    return base


def _by_name(rows) -> dict:
    return {r["name"]: r for r in rows}


def _seed3(member_factory) -> None:
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    member_factory(id="m3", name="小张", relation=Relation.child, birth_date=date(2015, 9, 20), gender=Gender.male)


# ---------------------------------------------------------------------------
# 靶点 1：尾差归属 = 规范排序最后一名（打乱插入顺序证伪）
# ---------------------------------------------------------------------------
def test_remainder_follows_canonical_order_not_insertion(client, member_factory) -> None:
    """插入顺序「子女→本人→配偶」被故意打乱；尾差必须归**规范序最后**（子女 小张）。"""
    _seed3(member_factory)
    created = client.post(
        "/api/policies",
        json=_payload(
            insuredIds=["m3", "m1", "m2"],  # 插入顺序：子女, 本人, 配偶
            primaryInsuredId="m1",
            premiumPerPeriod=1000,  # 年缴 → 年化 1000
        ),
    ).json()
    # 展示顺序为规范序：本人→配偶→子女
    assert created["insuredIds"] == ["m1", "m2", "m3"]
    assert created["insuredNames"] == ["张三", "李四", "小张"]

    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = _by_name(dash["byMember"]["byInsured"])
    # 1000/3 → 333.33 / 333.33 / 333.34；尾差归规范序最后一名 = 小张
    assert rows["张三"]["annualPremium"] == 333.33
    assert rows["李四"]["annualPremium"] == 333.33
    assert rows["小张"]["annualPremium"] == 333.34
    assert (
        rows["张三"]["annualPremium"]
        + rows["李四"]["annualPremium"]
        + rows["小张"]["annualPremium"]
        == 1000.00
    )


def test_remainder_owner_matches_insured_names_order(client, member_factory) -> None:
    """以接口返回的 ``insuredNames`` 顺序为准，核对「末位=尾差承担者」自洽。"""
    _seed3(member_factory)
    created = client.post(
        "/api/policies",
        json=_payload(insuredIds=["m2", "m3", "m1"], primaryInsuredId="m1", premiumPerPeriod=100),
    ).json()
    last_name = created["insuredNames"][-1]  # 规范序最后一名
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = _by_name(dash["byMember"]["byInsured"])
    # 100/3 → 33.33/33.33/33.34，末位得 33.34
    assert rows[last_name]["annualPremium"] == 33.34


# ---------------------------------------------------------------------------
# 靶点 2：卡片 / byType / byPolicyholder 不分摊（多被保人时仍为全额）
# ---------------------------------------------------------------------------
def test_card_bytype_bypolicyholder_not_allocated(client, member_factory) -> None:
    _seed3(member_factory)
    client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1", premiumPerPeriod=1000),
    )
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    # 卡片全额（不分摊）；保单生效中未缴清 → 两个口径相等
    assert dash["overview"]["annualTotalPremium"] == 1000.00
    assert dash["overview"]["unpaidAnnualPremium"] == 1000.00
    # byType 全额（不分摊）
    by_type = {r["insuranceType"]: r for r in dash["byType"]}
    assert by_type["重疾险"]["annualPremium"] == 1000.00
    assert by_type["重疾险"]["policyCount"] == 1
    # byPolicyholder 全额（不分摊）
    holders = _by_name(dash["byMember"]["byPolicyholder"])
    assert holders["张三"]["annualPremium"] == 1000.00
    # byInsured 分摊为 500/500
    insured = _by_name(dash["byMember"]["byInsured"])
    assert insured["张三"]["annualPremium"] == 500.00
    assert insured["李四"]["annualPremium"] == 500.00


def test_policy_count_full_per_insured(client, member_factory) -> None:
    """计数类指标全额：2 人保单，每人各 +1（不是 0.5）。"""
    _seed3(member_factory)
    client.post("/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1"))
    rows = _by_name(client.get("/api/stats/dashboard", params={"today": TODAY}).json()["byMember"]["byInsured"])
    assert rows["张三"]["policyCount"] == 1
    assert rows["李四"]["policyCount"] == 1
    assert rows["张三"]["activePolicyCount"] == 1


def test_insured_person_count_multi_insured(client, member_factory) -> None:
    """2 人保单 → 在保人数 2（关联表去重口径）。"""
    _seed3(member_factory)
    client.post("/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1"))
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    assert dash["overview"]["insuredPersonCount"] == 2


# ---------------------------------------------------------------------------
# 靶点 3：算例集 A —— 逐值独立重算（today = 2025-05-20）
# ---------------------------------------------------------------------------
def test_example_a_independent_recompute(client, seed_example_a) -> None:
    d = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    o = d["overview"]
    # v1.4 双口径：未缴费 = 旧值 17000；应缴总保费含已缴清（p_D 趸交年化 0 → 仍 17000）
    assert o["unpaidAnnualPremium"] == 17000.00
    assert o["annualTotalPremium"] == 17000.00
    assert o["upcoming60Premium"] == 4000.00
    assert o["upcoming60Count"] == 3
    assert o["policyTotal"] == 4
    assert o["activePolicyCount"] == 3
    assert o["insuredPersonCount"] == 3

    ins = _by_name(d["byMember"]["byInsured"])
    assert (ins["张三"]["policyCount"], ins["张三"]["annualPremium"], ins["张三"]["upcoming60Premium"], ins["张三"]["paidToDatePremium"]) == (2, 6000.00, 1000.00, 81500.00)
    assert (ins["李四"]["policyCount"], ins["李四"]["annualPremium"], ins["李四"]["upcoming60Premium"], ins["李四"]["paidToDatePremium"]) == (1, 8000.00, 0.00, 32000.00)
    assert (ins["小张"]["policyCount"], ins["小张"]["annualPremium"], ins["小张"]["upcoming60Premium"], ins["小张"]["paidToDatePremium"]) == (1, 3000.00, 3000.00, 3000.00)

    # byType 排序：医疗险 8000 → 重疾险 6000 → 意外险 3000
    assert [r["insuranceType"] for r in d["byType"]] == ["医疗险", "重疾险", "意外险"]
    assert [r["annualPremium"] for r in d["byType"]] == [8000.00, 6000.00, 3000.00]

    # upcoming 顺序：2025-06-01(小张) → 2025-06-15(张三) → 2025-07-15(张三)
    assert [u["dueDate"] for u in d["upcoming"]] == ["2025-06-01", "2025-06-15", "2025-07-15"]
    assert [u["insuredName"] for u in d["upcoming"]] == ["小张", "张三", "张三"]

    # byPolicyholder 与 v1.2 逐值相同（m1 投保全部 4 张）
    ph = _by_name(d["byMember"]["byPolicyholder"])
    assert ph["张三"]["policyCount"] == 4
    assert ph["张三"]["annualPremium"] == 17000.00
    assert ph["张三"]["paidToDatePremium"] == 116500.00  # 31500+32000+3000+50000


# ---------------------------------------------------------------------------
# 靶点 4：算例集 B —— 逐值独立重算
# ---------------------------------------------------------------------------
def test_example_b_independent_recompute(client, seed_example_b) -> None:
    d = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    o = d["overview"]
    # v1.4 双口径：未缴费 = 旧值 2400；应缴总保费新增 p_F（已缴清生效中，年化 12000）→ 14400
    assert o["unpaidAnnualPremium"] == 2400.00
    assert o["annualTotalPremium"] == 14400.00
    assert o["upcoming60Premium"] == 0.00
    assert o["upcoming60Count"] == 0
    assert o["policyTotal"] == 3
    assert o["activePolicyCount"] == 3
    assert o["insuredPersonCount"] == 2

    ins = _by_name(d["byMember"]["byInsured"])
    assert (ins["张三"]["policyCount"], ins["张三"]["annualPremium"], ins["张三"]["paidToDatePremium"]) == (2, 2400.00, 50000.00)
    # v1.6：年化口径放宽为「生效中 + 已缴清」
    assert (ins["王五"]["policyCount"], ins["王五"]["annualPremium"], ins["王五"]["paidToDatePremium"]) == (1, 12000.00, 60000.00)


# ---------------------------------------------------------------------------
# 靶点 5：算例集 C —— 5 条边界独立重算（calc_schedule 直算）
# ---------------------------------------------------------------------------
class _MemberStub:
    """最小被保人桩（仅需 ``birth_date``）。"""

    def __init__(self, birth_date: date) -> None:
        self.birth_date = birth_date


def test_example_c_boundaries(member_factory, policy_factory) -> None:
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)

    cases = [
        # (eff, freq, today, k_max, paid, next_due)
        (date(2020, 1, 31), PaymentFrequency.monthly, date(2020, 4, 30), 2, 3, date(2020, 4, 30)),  # C1
        (date(2020, 1, 31), PaymentFrequency.monthly, date(2020, 2, 29), 0, 1, date(2020, 2, 29)),  # C2
        (date(2020, 3, 15), PaymentFrequency.monthly, date(2025, 5, 15), 61, 62, date(2025, 5, 15)),  # C3
        (date(2021, 8, 1), PaymentFrequency.yearly, date(2025, 8, 1), 3, 4, date(2025, 8, 1)),  # C4
        (date(2020, 3, 15), PaymentFrequency.monthly, date(2020, 3, 15), -1, 0, date(2020, 3, 15)),  # C5
    ]
    for idx, (eff, freq, today, k_max, paid, next_due) in enumerate(cases, start=1):
        policy = policy_factory(
            id=f"p_c{idx}",
            insurance_type=InsuranceType.critical_illness,
            policyholder_id="m1",
            insured_id="m1",
            effective_date=eff,
            coverage_term_type=CoverageTermType.fixed_years,
            coverage_term_value=30,
            payment_term_type=PaymentTermType.fixed_years,
            payment_term_value=20,
            payment_frequency=freq,
            premium_per_period="100",
            status=PolicyStatus.active,
        )
        sched = calc_schedule(policy, _MemberStub(date(1988, 6, 1)), today)
        assert sched.k_max == k_max, f"C{idx} k_max 期望 {k_max} 实得 {sched.k_max}"
        assert sched.paid_periods == paid, f"C{idx} paid 期望 {paid} 实得 {sched.paid_periods}"
        assert sched.next_due_date == next_due, f"C{idx} next_due 期望 {next_due} 实得 {sched.next_due_date}"
