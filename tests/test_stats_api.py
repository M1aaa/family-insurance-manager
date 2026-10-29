"""统计接口测试：数值与 ARCHITECTURE 5.6 算例集 A / B 完全一致。"""

from __future__ import annotations

TODAY = "2025-05-20"


def _rows_by_name(rows):
    return {row["name"]: row for row in rows}


# ---------------------------------------------------------------------------
# 算例集 A
# ---------------------------------------------------------------------------
def test_dashboard_example_a_overview(client, seed_example_a):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    assert data["asOfDate"] == TODAY
    overview = data["overview"]
    # v1.4：未缴费金额 = v1.3 旧「年度总保费」（17000）；应缴总保费含已缴清（A 中 p_D 趸交年化 0 → 仍 17000）
    assert overview["unpaidAnnualPremium"] == 17000.00
    assert overview["annualTotalPremium"] == 17000.00
    assert overview["upcoming60Premium"] == 4000.00
    assert overview["upcoming60Count"] == 3
    assert overview["policyTotal"] == 4
    assert overview["activePolicyCount"] == 3
    assert overview["insuredPersonCount"] == 3


def test_dashboard_example_a_by_insured(client, seed_example_a):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = data["byMember"]["byInsured"]
    # 排序：本人 → 配偶 → 子女
    assert [r["name"] for r in rows] == ["张三", "李四", "小张"]
    by_name = _rows_by_name(rows)

    # 张三：被保保单为 A + D（D 为趸交已缴清，仍计入保单数/累计已缴）
    # 注：ARCHITECTURE 5.6 算例集 A 表格此行为「count 1 / paid 31500.00」，
    #     与 5.5 伪代码（policyCount=组内保单数）及算例集 B（张三 count 2 = E+G）
    #     相互矛盾；按伪代码与 B 的一致性，正确值应为 count 2 / paid 81500.00。
    zhangsan = by_name["张三"]
    assert zhangsan["policyCount"] == 2
    assert zhangsan["activePolicyCount"] == 1
    assert zhangsan["annualPremium"] == 6000.00
    assert zhangsan["upcoming60Premium"] == 1000.00
    assert zhangsan["paidToDatePremium"] == 81500.00

    lisi = by_name["李四"]
    assert lisi["policyCount"] == 1
    assert lisi["annualPremium"] == 8000.00
    assert lisi["upcoming60Premium"] == 0.00
    assert lisi["paidToDatePremium"] == 32000.00

    xiaozhang = by_name["小张"]
    assert xiaozhang["policyCount"] == 1
    assert xiaozhang["annualPremium"] == 3000.00
    assert xiaozhang["upcoming60Premium"] == 3000.00
    assert xiaozhang["paidToDatePremium"] == 3000.00


def test_dashboard_example_a_by_policyholder(client, seed_example_a):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = data["byMember"]["byPolicyholder"]
    # 全部保单投保人均为 m1（张三）
    assert len(rows) == 1
    zhangsan = rows[0]
    assert zhangsan["name"] == "张三"
    assert zhangsan["policyCount"] == 4
    assert zhangsan["activePolicyCount"] == 3
    assert zhangsan["annualPremium"] == 17000.00
    assert zhangsan["upcoming60Premium"] == 4000.00
    assert zhangsan["paidToDatePremium"] == 116500.00


def test_dashboard_example_a_by_type(client, seed_example_a):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = data["byType"]
    # 年化保费降序：医疗险 8000 → 重疾险 6000 → 意外险 3000
    assert [r["insuranceType"] for r in rows] == ["医疗险", "重疾险", "意外险"]

    medical = rows[0]
    assert medical["policyCount"] == 1
    assert medical["annualPremium"] == 8000.00
    assert medical["upcoming60Premium"] == 0.00
    assert medical["paidToDatePremium"] == 32000.00

    critical = rows[1]
    assert critical["policyCount"] == 2
    assert critical["annualPremium"] == 6000.00
    assert critical["upcoming60Premium"] == 1000.00
    assert critical["paidToDatePremium"] == 81500.00

    accident = rows[2]
    assert accident["policyCount"] == 1
    assert accident["annualPremium"] == 3000.00
    assert accident["upcoming60Premium"] == 3000.00
    assert accident["paidToDatePremium"] == 3000.00


def test_dashboard_example_a_upcoming(client, seed_example_a):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    upcoming = data["upcoming"]
    assert [(u["dueDate"], u["insuredName"], u["periodIndex"], u["premiumPerPeriod"]) for u in upcoming] == [
        ("2025-06-01", "小张", 2, 3000.00),
        ("2025-06-15", "张三", 64, 500.00),
        ("2025-07-15", "张三", 65, 500.00),
    ]
    # 清单合计 == 卡片口径
    assert sum(u["premiumPerPeriod"] for u in upcoming) == 4000.00


# ---------------------------------------------------------------------------
# 算例集 B
# ---------------------------------------------------------------------------
def test_dashboard_example_b_overview(client, seed_example_b):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    overview = data["overview"]
    # v1.4：未缴费金额 = v1.3 旧值（2400）；应缴总保费新增 p_F（已缴清生效中，年化 12000）→ 14400
    assert overview["unpaidAnnualPremium"] == 2400.00
    assert overview["annualTotalPremium"] == 14400.00
    assert overview["upcoming60Premium"] == 0.00
    assert overview["upcoming60Count"] == 0
    assert overview["policyTotal"] == 3
    assert overview["activePolicyCount"] == 3
    assert overview["insuredPersonCount"] == 2
    assert data["upcoming"] == []


def test_dashboard_example_b_by_insured(client, seed_example_b):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = data["byMember"]["byInsured"]
    assert [r["name"] for r in rows] == ["张三", "王五"]
    by_name = _rows_by_name(rows)

    zhangsan = by_name["张三"]  # E + G
    assert zhangsan["policyCount"] == 2
    assert zhangsan["annualPremium"] == 2400.00
    assert zhangsan["upcoming60Premium"] == 0.00
    assert zhangsan["paidToDatePremium"] == 50000.00

    wangwu = by_name["王五"]  # F 已缴清
    assert wangwu["policyCount"] == 1
    # v1.6：年化口径放宽为「生效中 + 已缴清」，已缴清的 F 也计入王五的年化
    assert wangwu["annualPremium"] == 12000.00
    assert wangwu["upcoming60Premium"] == 0.00
    assert wangwu["paidToDatePremium"] == 60000.00


def test_dashboard_example_b_by_type(client, seed_example_b):
    data = client.get("/api/stats/dashboard", params={"today": TODAY}).json()
    rows = data["byType"]
    assert len(rows) == 3
    # v1.6：年化降序 → 年金险（12000，已缴清但口径已放宽）居首，寿险（2400）次之
    assert rows[0]["insuranceType"] == "年金险"
    assert rows[0]["annualPremium"] == 12000.00
    by_type = {r["insuranceType"]: r for r in rows}
    assert by_type["寿险"]["annualPremium"] == 2400.00
    assert by_type["年金险"]["annualPremium"] == 12000.00
    assert by_type["年金险"]["paidToDatePremium"] == 60000.00
    assert by_type["重疾险"]["annualPremium"] == 0.00
    assert by_type["重疾险"]["paidToDatePremium"] == 50000.00
