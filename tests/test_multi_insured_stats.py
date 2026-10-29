"""多被保人分摊统计测试（v1.3）。

覆盖 T03 验收标准：

1. 2 人保单 ``byInsured`` 年化 = 全额 ÷ 2，``Σ = 全额``，尾差归规范排序最后一名；
2. 顶部卡片 / ``byType`` / ``byPolicyholder`` **数值与 v1.2 逐值相同**（不分摊）；
3. ``insuredPersonCount`` 去重口径不变；
4. ``coverageTypes`` 张数 / 生效中 / 分摊年化正确、排序「张数降序 → 险种枚举顺序」；
5. 删除被引用成员（投保人 ∪ 被保人）→ 409 且**计数去重**；
6. **算例集 A / B 全部逐值通过**（回归，见 ARCHITECTURE §5.6）。
"""

from __future__ import annotations

from datetime import date

from app.enums import Gender, Relation

TODAY = "2025-05-20"


def _seed3(member_factory) -> None:
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    member_factory(id="m3", name="小张", relation=Relation.child, birth_date=date(2015, 9, 20), gender=Gender.male)


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


# ---------------------------------------------------------------------------
# 1) 分摊
# ---------------------------------------------------------------------------
def test_by_insured_allocates_premium(client, member_factory) -> None:
    _seed3(member_factory)
    client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1"),
    )
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = _by_name(dash["byMember"]["byInsured"])
    # 年化 1000 分摊 2 份 → 各 500；Σ = 1000
    assert rows["张三"]["annualPremium"] == 500.00
    assert rows["李四"]["annualPremium"] == 500.00
    assert rows["张三"]["annualPremium"] + rows["李四"]["annualPremium"] == 1000.00
    # 计数类指标全额：每人各 +1
    assert rows["张三"]["policyCount"] == 1
    assert rows["李四"]["policyCount"] == 1
    # 顶部卡片不分摊（保单生效中未缴清 → 两个口径相等，均为全额 1000）
    assert dash["overview"]["unpaidAnnualPremium"] == 1000.00
    assert dash["overview"]["annualTotalPremium"] == 1000.00


def test_allocation_remainder_to_last(client, member_factory) -> None:
    _seed3(member_factory)
    client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m2", "m3"], primaryInsuredId="m1"),
    )
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = _by_name(dash["byMember"]["byInsured"])
    # 1000 / 3 → 333.33 / 333.33 / 333.34（尾差归规范排序最后一名「小张」）
    assert rows["张三"]["annualPremium"] == 333.33
    assert rows["李四"]["annualPremium"] == 333.33
    assert rows["小张"]["annualPremium"] == 333.34
    total = rows["张三"]["annualPremium"] + rows["李四"]["annualPremium"] + rows["小张"]["annualPremium"]
    assert total == 1000.00


def test_upcoming_allocated_by_head(client, member_factory) -> None:
    _seed3(member_factory)
    # 月缴 500，2 名被保人；生效 2025-04-15 → 待缴期次落在 60 天窗口内
    client.post(
        "/api/policies",
        json=_payload(
            insuredIds=["m1", "m2"],
            primaryInsuredId="m1",
            paymentFrequency="月缴",
            premiumPerPeriod=500,
            effectiveDate="2025-04-15",
        ),
    )
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = _by_name(dash["byMember"]["byInsured"])
    # 保单待缴合计分摊到 2 人；Σ(byInsured.upcoming) == 卡片待缴
    total = rows["张三"]["upcoming60Premium"] + rows["李四"]["upcoming60Premium"]
    assert total == dash["overview"]["upcoming60Premium"]


def test_by_policyholder_not_allocated(client, member_factory) -> None:
    _seed3(member_factory)
    client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1", policyholderId="m1"),
    )
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = _by_name(dash["byMember"]["byPolicyholder"])
    # 投保人 m1 不分摊：年化 = 全额 1000，保单数 1
    assert rows["张三"]["annualPremium"] == 1000.00
    assert rows["张三"]["policyCount"] == 1


def test_insured_person_count_dedup(client, member_factory) -> None:
    _seed3(member_factory)
    # m1 被两张保单保障 → 去重后仍计 1 人
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1"))
    client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1"], primaryInsuredId="m1", productName="第二张"),
    )
    dash = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    assert dash["overview"]["insuredPersonCount"] == 1


# ---------------------------------------------------------------------------
# 4) coverageTypes
# ---------------------------------------------------------------------------
def test_member_coverage_types(client, member_factory) -> None:
    _seed3(member_factory)
    # p1 重疾险：m1+m2，年化 1000；p2 医疗险：m1，年化 1000
    client.post("/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1"))
    client.post(
        "/api/policies",
        json=_payload(
            insuredIds=["m1"], primaryInsuredId="m1", insuranceType="医疗险", productName="医疗"
        ),
    )
    members = client.get("/api/members").json()
    zhangsan = next(m for m in members if m["name"] == "张三")
    cov = zhangsan["coverageTypes"]
    # 两张保单各 1 张；张数相同 → 险种枚举顺序：重疾险(0) 在 医疗险(1) 之前
    assert [c["insuranceType"] for c in cov] == ["重疾险", "医疗险"]
    by_type = {c["insuranceType"]: c for c in cov}
    assert by_type["重疾险"]["count"] == 1
    assert by_type["重疾险"]["activeCount"] == 1
    # 重疾险分摊：1000 / 2 = 500（m1 一份）
    assert by_type["重疾险"]["annualPremium"] == 500.00
    assert by_type["医疗险"]["annualPremium"] == 1000.00

    lisi = next(m for m in members if m["name"] == "李四")
    assert [c["insuranceType"] for c in lisi["coverageTypes"]] == ["重疾险"]
    assert lisi["coverageTypes"][0]["annualPremium"] == 500.00


def test_member_coverage_types_sort_by_count_desc(client, member_factory) -> None:
    _seed3(member_factory)
    # 医疗险 2 张、重疾险 1 张 → 张数降序：医疗险在前
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1", insuranceType="重疾险"))
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1", insuranceType="医疗险", productName="医疗1"))
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1", insuranceType="医疗险", productName="医疗2"))
    members = client.get("/api/members").json()
    zhangsan = next(m for m in members if m["name"] == "张三")
    assert [c["insuranceType"] for c in zhangsan["coverageTypes"]] == ["医疗险", "重疾险"]
    assert zhangsan["coverageTypes"][0]["count"] == 2


def test_member_coverage_types_excludes_surrendered(client, member_factory) -> None:
    _seed3(member_factory)
    # v1.6：已缴清 → 张数与年化**都计入**（年化口径已放宽为「生效中 + 已缴清」）
    client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1"], primaryInsuredId="m1", status="已缴清"),
    )
    members = client.get("/api/members").json()
    zhangsan = next(m for m in members if m["name"] == "张三")
    cov = zhangsan["coverageTypes"]
    assert len(cov) == 1
    assert cov[0]["count"] == 1
    assert cov[0]["activeCount"] == 0
    assert cov[0]["annualPremium"] == 1000.00


# ---------------------------------------------------------------------------
# 5) 删除保护（投保人 ∪ 被保人，去重）
# ---------------------------------------------------------------------------
def test_delete_member_counts_deduped(client, member_factory) -> None:
    _seed3(member_factory)
    # m1 既是投保人又是被保人（同一张保单）→ 去重后计 1
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1"))
    resp = client.delete("/api/members/m1")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "MEMBER_IN_USE"
    assert body["detail"]["policyCount"] == 1


def test_delete_member_dedupes_across_policies(client, member_factory) -> None:
    _seed3(member_factory)
    # p1：m1 投保人 + 被保人；p2：m1 被保人（投保人 m2）→ 去重后 2 张
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1", policyholderId="m1"))
    client.post(
        "/api/policies",
        json=_payload(
            insuredIds=["m1", "m2"], primaryInsuredId="m1", policyholderId="m2", productName="p2"
        ),
    )
    resp = client.delete("/api/members/m1")
    assert resp.status_code == 409
    assert resp.json()["detail"]["policyCount"] == 2


# ---------------------------------------------------------------------------
# 6) ★ 算例集 A / B 回归（逐值）
# ---------------------------------------------------------------------------
def test_regression_example_a(client, seed_example_a) -> None:
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    overview = data["overview"]
    # v1.4 双口径：未缴费 = 旧值 17000；应缴总保费含已缴清（p_D 趸交年化 0 → 仍 17000）
    assert overview["unpaidAnnualPremium"] == 17000.00
    assert overview["annualTotalPremium"] == 17000.00
    assert overview["upcoming60Premium"] == 4000.00
    assert overview["upcoming60Count"] == 3
    assert overview["policyTotal"] == 4
    assert overview["activePolicyCount"] == 3
    assert overview["insuredPersonCount"] == 3

    by_insured = _by_name(data["byMember"]["byInsured"])
    assert by_insured["张三"]["policyCount"] == 2
    assert by_insured["张三"]["annualPremium"] == 6000.00
    assert by_insured["张三"]["upcoming60Premium"] == 1000.00
    assert by_insured["张三"]["paidToDatePremium"] == 81500.00
    assert by_insured["李四"]["policyCount"] == 1
    assert by_insured["李四"]["annualPremium"] == 8000.00
    assert by_insured["李四"]["paidToDatePremium"] == 32000.00
    assert by_insured["小张"]["annualPremium"] == 3000.00
    assert by_insured["小张"]["upcoming60Premium"] == 3000.00
    assert by_insured["小张"]["paidToDatePremium"] == 3000.00

    by_type = {r["insuranceType"]: r for r in data["byType"]}
    assert by_type["医疗险"]["annualPremium"] == 8000.00
    assert by_type["重疾险"]["annualPremium"] == 6000.00
    assert by_type["意外险"]["annualPremium"] == 3000.00
    assert [r["insuranceType"] for r in data["byType"]] == ["医疗险", "重疾险", "意外险"]


def test_regression_example_b(client, seed_example_b) -> None:
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    overview = data["overview"]
    # v1.4 双口径：未缴费 = 旧值 2400；应缴总保费新增 p_F（已缴清生效中，年化 12000）→ 14400
    assert overview["unpaidAnnualPremium"] == 2400.00
    assert overview["annualTotalPremium"] == 14400.00
    assert overview["upcoming60Premium"] == 0.00
    assert overview["upcoming60Count"] == 0
    assert overview["policyTotal"] == 3
    assert overview["activePolicyCount"] == 3
    assert overview["insuredPersonCount"] == 2

    by_insured = _by_name(data["byMember"]["byInsured"])
    assert by_insured["张三"]["policyCount"] == 2
    assert by_insured["张三"]["annualPremium"] == 2400.00
    assert by_insured["张三"]["paidToDatePremium"] == 50000.00
    assert by_insured["王五"]["policyCount"] == 1
    # v1.6：年化口径放宽为「生效中 + 已缴清」
    assert by_insured["王五"]["annualPremium"] == 12000.00
    assert by_insured["王五"]["paidToDatePremium"] == 60000.00

    by_type = {r["insuranceType"]: r for r in data["byType"]}
    assert by_type["寿险"]["annualPremium"] == 2400.00
    assert by_type["年金险"]["paidToDatePremium"] == 60000.00
    assert by_type["重疾险"]["paidToDatePremium"] == 50000.00
