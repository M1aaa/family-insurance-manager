"""QA 独立验证 —— v1.4 看板保费双口径。

需求：首页「年度总保费」拆为两个指标：

- ``annualTotalPremium``（当年应缴总保费）= ``Σ sched.annualized_premium``，
  条件 ``status ∈ {生效中, 已缴清}``（含已缴清、含 status=生效中但期次已缴完）；
- ``unpaidAnnualPremium``（当年未缴费金额）= ``Σ sched.annualized_premium``，
  条件 ``status == 生效中 and not sched.is_paid_off``（= v1.3 旧口径，**应逐值不变**）。

本文件**不采信工程师自测数字**，从 §5.6 种子数据自行重算，并攻击边界。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.enums import Gender, Relation

TODAY = "2025-05-20"


def _payload(**over) -> dict:
    base = {
        "company": "测试保险",
        "insuranceType": "重疾险",
        "productName": "测试产品",
        "policyholderId": "m1",
        "insuredIds": ["m1"],
        "primaryInsuredId": "m1",
        "effectiveDate": "2020-01-01",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 10,
        "paymentFrequency": "年缴",
        "premiumPerPeriod": 1000,
    }
    base.update(over)
    return base


def _seed1(member_factory) -> None:
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)


def _ov(client):
    return client.get("/api/stats/dashboard", params={"today": TODAY}).json()["overview"]


def _by_name(rows) -> dict:
    return {r["name"]: r for r in rows}


# ---------------------------------------------------------------------------
# 靶点 1：算例 A/B 独立重算
# ---------------------------------------------------------------------------
def test_example_a_dual_metric(client, seed_example_a) -> None:
    """A：应缴 = A+B+C+D(趸交年化0) = 17000；未缴 = A+B+C = 17000（差 0）。"""
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 17000.00
    assert ov["unpaidAnnualPremium"] == 17000.00
    # 差 = 已缴部分 = D（趸交已缴清，年化 0）= 0
    assert ov["annualTotalPremium"] - ov["unpaidAnnualPremium"] == 0.00


def test_example_b_dual_metric(client, seed_example_b) -> None:
    """B：应缴 = E+F+G = 2400+12000+0 = 14400；未缴 = E = 2400（差 12000 = 已缴完的 F）。"""
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 14400.00
    assert ov["unpaidAnnualPremium"] == 2400.00
    assert ov["annualTotalPremium"] - ov["unpaidAnnualPremium"] == 12000.00


# ---------------------------------------------------------------------------
# 靶点 2：JSON 类型（number，不是字符串）
# ---------------------------------------------------------------------------
def test_dual_metric_json_types(client, seed_example_a) -> None:
    resp = client.get("/api/stats/dashboard", params={"today": TODAY})
    ov = resp.json()["overview"]
    for key in ("annualTotalPremium", "unpaidAnnualPremium", "upcoming60Premium"):
        assert isinstance(ov[key], (int, float)) and not isinstance(ov[key], bool), f"{key} 不是 number：{ov[key]!r}"
    # 原始 JSON 文本不得把这两个字段序列化为字符串
    raw = resp.text.replace(" ", "")
    assert '"annualTotalPremium":"' not in raw, "annualTotalPremium 被序列化为字符串"
    assert '"unpaidAnnualPremium":"' not in raw, "unpaidAnnualPremium 被序列化为字符串"


# ---------------------------------------------------------------------------
# 靶点 3：边界攻击
# ---------------------------------------------------------------------------
def test_active_not_paid_off_counts_both(client, member_factory) -> None:
    """生效中且未缴清 → 两个口径都进。"""
    _seed1(member_factory)
    client.post("/api/policies", json=_payload(premiumPerPeriod=1000))  # 固定年限10，未缴完
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 1000.00
    assert ov["unpaidAnnualPremium"] == 1000.00


def test_active_but_paid_off_counts_only_total(client, member_factory) -> None:
    """★ 核心差异点：status=生效中 但 is_paid_off=True → 只进应缴，不进未缴。"""
    _seed1(member_factory)
    # 固定年限 1 年缴、生效日远在过去 → 唯一一期已缴完（is_paid_off=True），status 仍=生效中
    client.post(
        "/api/policies",
        json=_payload(paymentTermValue=1, premiumPerPeriod=1000, status="生效中"),
    )
    # 先确认该保单确实是「生效中 + 已缴完」
    p = client.get("/api/policies").json()[0]
    assert p["status"] == "生效中" and p["isPaidOff"] is True, p
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 1000.00  # 进应缴
    assert ov["unpaidAnnualPremium"] == 0.00  # 不进未缴


def test_paid_off_status_non_single_counts_only_total(client, member_factory) -> None:
    """已缴清 + 非趸交 → 只进应缴，不进未缴。"""
    _seed1(member_factory)
    client.post(
        "/api/policies",
        json=_payload(premiumPerPeriod=12000, paymentTermValue=10, status="已缴清"),
    )
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 12000.00
    assert ov["unpaidAnnualPremium"] == 0.00


def test_surrendered_and_lapsed_excluded_from_both(client, member_factory) -> None:
    """已退保 / 已失效 → 两个口径都不进。"""
    _seed1(member_factory)
    client.post("/api/policies", json=_payload(productName="退保", premiumPerPeriod=5000, status="已退保"))
    client.post("/api/policies", json=_payload(productName="失效", premiumPerPeriod=7000, status="已失效"))
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 0.00
    assert ov["unpaidAnnualPremium"] == 0.00


def test_single_premium_paid_off_zero_both(client, member_factory) -> None:
    """趸交已缴清 → 年化 0，两个口径贡献都是 0。"""
    _seed1(member_factory)
    client.post(
        "/api/policies",
        json=_payload(
            paymentTermType="趸交",
            paymentTermValue=None,
            premiumPerPeriod=50000,
            status="已缴清",
        ),
    )
    ov = _ov(client)
    assert ov["annualTotalPremium"] == 0.00
    assert ov["unpaidAnnualPremium"] == 0.00


# ---------------------------------------------------------------------------
# 靶点 4：两条不变式
# ---------------------------------------------------------------------------
def test_two_invariants_on_mixed_set(client, member_factory) -> None:
    _seed1(member_factory)
    # A 生效中未缴清 1000 → 两个口径
    client.post("/api/policies", json=_payload(productName="A", premiumPerPeriod=1000, paymentTermValue=10))
    # B 生效中已缴完 2000 → 只进应缴
    client.post("/api/policies", json=_payload(productName="B", premiumPerPeriod=2000, paymentTermValue=1))
    # C 已缴清 3000 → 只进应缴
    client.post("/api/policies", json=_payload(productName="C", premiumPerPeriod=3000, paymentTermValue=10, status="已缴清"))
    # D 已退保 9000 → 都不进
    client.post("/api/policies", json=_payload(productName="D", premiumPerPeriod=9000, status="已退保"))
    # E 趸交已缴清 50000 → 年化 0
    client.post("/api/policies", json=_payload(productName="E", paymentTermType="趸交", paymentTermValue=None, premiumPerPeriod=50000, status="已缴清"))

    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    ov = data["overview"]
    # 应缴 = A+B+C (+D 排除 +E 0) = 6000
    assert ov["annualTotalPremium"] == 6000.00
    # 未缴 = A = 1000
    assert ov["unpaidAnnualPremium"] == 1000.00
    # 不变式①：差 == 已缴部分（B 2000 + C 3000 + E 0）= 5000
    paid_part = Decimal("2000") + Decimal("3000") + Decimal("0")
    assert ov["annualTotalPremium"] - ov["unpaidAnnualPremium"] == float(paid_part)
    # 不变式②（v1.6 口径放宽后已变更）：Σ byInsured.annualPremium == annualTotalPremium
    #   维度年化已与主卡片「当年应缴总保费」同口径，**不再**等于 unpaidAnnualPremium
    by_insured = data["byMember"]["byInsured"]
    total_by_insured = sum(Decimal(str(r["annualPremium"])) for r in by_insured)
    assert total_by_insured == Decimal(str(ov["annualTotalPremium"]))


# ---------------------------------------------------------------------------
# 靶点 5：未改坏其它维度（与 v1.3 逐值一致）
# ---------------------------------------------------------------------------
def test_example_a_other_dimensions_unchanged(client, seed_example_a) -> None:
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    ins = _by_name(data["byMember"]["byInsured"])
    assert (ins["张三"]["policyCount"], ins["张三"]["annualPremium"], ins["张三"]["paidToDatePremium"]) == (2, 6000.00, 81500.00)
    assert (ins["李四"]["policyCount"], ins["李四"]["annualPremium"], ins["李四"]["paidToDatePremium"]) == (1, 8000.00, 32000.00)
    assert (ins["小张"]["policyCount"], ins["小张"]["annualPremium"], ins["小张"]["paidToDatePremium"]) == (1, 3000.00, 3000.00)

    by_type = {r["insuranceType"]: r for r in data["byType"]}
    assert by_type["医疗险"]["annualPremium"] == 8000.00
    assert by_type["重疾险"]["annualPremium"] == 6000.00
    assert by_type["意外险"]["annualPremium"] == 3000.00
    assert [r["insuranceType"] for r in data["byType"]] == ["医疗险", "重疾险", "意外险"]

    ph = _by_name(data["byMember"]["byPolicyholder"])
    assert ph["张三"]["policyCount"] == 4
    assert ph["张三"]["annualPremium"] == 17000.00

    # 近 60 天待缴不变
    assert data["overview"]["upcoming60Premium"] == 4000.00
    assert data["overview"]["upcoming60Count"] == 3


def test_example_b_other_dimensions_unchanged(client, seed_example_b) -> None:
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    ins = _by_name(data["byMember"]["byInsured"])
    assert (ins["张三"]["policyCount"], ins["张三"]["annualPremium"], ins["张三"]["paidToDatePremium"]) == (2, 2400.00, 50000.00)
    # v1.6：年化口径放宽为「生效中 + 已缴清」
    assert (ins["王五"]["policyCount"], ins["王五"]["annualPremium"], ins["王五"]["paidToDatePremium"]) == (1, 12000.00, 60000.00)
    assert data["overview"]["upcoming60Premium"] == 0.00
    assert data["overview"]["upcoming60Count"] == 0
    # 不变式②（v1.6）：Σ byInsured = 14400 = annualTotalPremium（F 已缴清现已计入）
    assert sum(Decimal(str(r["annualPremium"])) for r in data["byMember"]["byInsured"]) == Decimal("14400")
    assert data["overview"]["unpaidAnnualPremium"] == 2400.00
