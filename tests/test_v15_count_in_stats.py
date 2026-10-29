"""v1.5 —— 「按投保人计入家庭保费统计」开关（``count_in_stats``）。

需求（用户原话）：保单录入里可灵活选择哪些投保人的保单计入家庭保费统计。

已确认口径（**只过滤「年化保费」这一个统计口径**）：

- ✅ 过滤（按保单**投保人**是否被排除）：``overview.annualTotalPremium`` /
  ``overview.unpaidAnnualPremium`` / ``byMember.byInsured[].annualPremium`` /
  ``byType[].annualPremium`` / ``byMember.byPolicyholder[].annualPremium``；
- ❌ 一律不过滤：``policyTotal`` / ``activePolicyCount`` / ``insuredPersonCount``、
  各维度 ``policyCount`` / ``activePolicyCount``、``upcoming60Premium`` / ``upcoming60Count`` /
  ``upcoming[]`` / ``overdue[]``、各维度 ``upcoming60Premium`` / ``paidToDatePremium``。

红线：**默认全计入时，算例 A/B 逐值不变**（本文件独立重算，不采信实现数字）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlite3

from sqlalchemy import create_engine, inspect, text

import app.main as app_main
from app.enums import Gender, Relation
from app.main import _run_additive_migrations

TODAY = "2025-05-20"

#: 全部保单都归「张三(m1)」所有 —— 排除 m1 会清空所有年化口径
#: 用于验证「只过滤年化、其余全保留」的边界。


def _by_name(rows) -> dict:
    return {r["name"]: r for r in rows}


def _dashboard(client) -> dict:
    resp = client.get("/api/stats/dashboard", params={"today": TODAY})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _set_count(client, member_id: str, flag: bool) -> dict:
    """经真实 API 把某成员的 ``countInStats`` 设为 ``flag``（回传响应）。"""
    members = client.get("/api/members").json()
    m = next(x for x in members if x["id"] == member_id)
    payload = {
        "name": m["name"],
        "relation": m["relation"],
        "birthDate": m["birthDate"],
        "gender": m["gender"],
        "remark": m["remark"],
        "countInStats": flag,
    }
    resp = client.put(f"/api/members/{member_id}", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _policy(client, **over) -> dict:
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
    resp = client.post("/api/policies", json=base)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_mixed(client, member_factory) -> None:
    """混合算例：m1 自购、m2 给 m1 买、m2 自购（含一笔 60 天内待缴）。

    - p1：投保人 m1、被保人 m1、年化 1000（重疾险）；
    - p2：投保人 **m2**、被保人 **m1**、年化 2000（医疗险）—— 验证按**投保人**过滤；
    - p3：投保人 m2、被保人 m2、年化 1500（意外险），2025-05-25 到期（落在 60 天窗口内）。
    """
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)

    _policy(client, productName="p1", premiumPerPeriod=1000)  # holder m1 / insured m1
    _policy(
        client,
        productName="p2",
        insuranceType="医疗险",
        policyholderId="m2",
        insuredIds=["m1"],
        primaryInsuredId="m1",
        premiumPerPeriod=2000,
    )
    _policy(
        client,
        productName="p3",
        insuranceType="意外险",
        policyholderId="m2",
        insuredIds=["m2"],
        primaryInsuredId="m2",
        premiumPerPeriod=1500,
        effectiveDate="2020-05-25",
    )


# ---------------------------------------------------------------------------
# 靶点 1：默认全计入 → 算例 A/B 逐值不变（硬红线）
# ---------------------------------------------------------------------------
def test_default_all_counted_members_flag_true(client, seed_example_a) -> None:
    """新建 / 存量成员默认 ``countInStats=True``。"""
    for m in client.get("/api/members").json():
        assert m["countInStats"] is True, m


def test_example_a_unchanged_when_all_counted(client, seed_example_a) -> None:
    """算例 A：默认全计入，overview / byInsured / byType / byPolicyholder 逐值不变。"""
    data = _dashboard(client)
    ov = data["overview"]
    assert ov["annualTotalPremium"] == 17000.00
    assert ov["unpaidAnnualPremium"] == 17000.00
    assert ov["policyTotal"] == 4
    assert ov["activePolicyCount"] == 3
    assert ov["insuredPersonCount"] == 3
    assert ov["upcoming60Premium"] == 4000.00
    assert ov["upcoming60Count"] == 3

    ins = _by_name(data["byMember"]["byInsured"])
    assert (ins["张三"]["policyCount"], ins["张三"]["annualPremium"], ins["张三"]["paidToDatePremium"]) == (2, 6000.00, 81500.00)
    assert (ins["李四"]["policyCount"], ins["李四"]["annualPremium"], ins["李四"]["paidToDatePremium"]) == (1, 8000.00, 32000.00)
    assert (ins["小张"]["policyCount"], ins["小张"]["annualPremium"], ins["小张"]["paidToDatePremium"]) == (1, 3000.00, 3000.00)

    by_type = {r["insuranceType"]: r for r in data["byType"]}
    assert by_type["医疗险"]["annualPremium"] == 8000.00
    assert by_type["重疾险"]["annualPremium"] == 6000.00
    assert by_type["意外险"]["annualPremium"] == 3000.00

    ph = _by_name(data["byMember"]["byPolicyholder"])
    assert ph["张三"]["policyCount"] == 4
    assert ph["张三"]["annualPremium"] == 17000.00


def test_example_b_unchanged_when_all_counted(client, seed_example_b) -> None:
    """算例 B：默认全计入，逐值不变。"""
    data = _dashboard(client)
    ov = data["overview"]
    assert ov["annualTotalPremium"] == 14400.00
    assert ov["unpaidAnnualPremium"] == 2400.00
    assert ov["upcoming60Premium"] == 0.00
    assert ov["upcoming60Count"] == 0

    ins = _by_name(data["byMember"]["byInsured"])
    assert (ins["张三"]["policyCount"], ins["张三"]["annualPremium"], ins["张三"]["paidToDatePremium"]) == (2, 2400.00, 50000.00)
    # v1.6：年化口径放宽为「生效中 + 已缴清」
    assert (ins["王五"]["policyCount"], ins["王五"]["annualPremium"], ins["王五"]["paidToDatePremium"]) == (1, 12000.00, 60000.00)


# ---------------------------------------------------------------------------
# 靶点 2：排除某投保人 → 年化从 5 处消失，其余完全不变
# ---------------------------------------------------------------------------
def test_exclude_policyholder_drops_annual_from_five_places(client, member_factory) -> None:
    """排除 m2：5 处年化口径剔除其名下保单；计数 / 待缴 / 已缴 / 清单全不变。"""
    _seed_mixed(client, member_factory)

    before = _dashboard(client)
    # 基线（全计入）
    assert before["overview"]["annualTotalPremium"] == 4500.00  # 1000 + 2000 + 1500
    assert before["overview"]["unpaidAnnualPremium"] == 4500.00
    ins0 = _by_name(before["byMember"]["byInsured"])
    assert ins0["张三"]["annualPremium"] == 3000.00  # p1 + p2（p2 被保人是 m1）
    assert ins0["李四"]["annualPremium"] == 1500.00  # p3
    ph0 = _by_name(before["byMember"]["byPolicyholder"])
    assert ph0["张三"]["annualPremium"] == 1000.00
    assert ph0["李四"]["annualPremium"] == 3500.00  # p2 + p3

    # 排除 m2
    _set_count(client, "m2", False)
    after = _dashboard(client)

    # ---- ✅ 5 处年化被过滤 ----
    assert after["overview"]["annualTotalPremium"] == 1000.00  # 仅 p1
    assert after["overview"]["unpaidAnnualPremium"] == 1000.00
    ins1 = _by_name(after["byMember"]["byInsured"])
    # 按**投保人**过滤：p2 被保人是 m1，但投保人是 m2 → 从 m1 的年化里剔除
    assert ins1["张三"]["annualPremium"] == 1000.00
    assert ins1["李四"]["annualPremium"] == 0.00
    by_type1 = {r["insuranceType"]: r for r in after["byType"]}
    assert by_type1["重疾险"]["annualPremium"] == 1000.00
    assert by_type1["医疗险"]["annualPremium"] == 0.00
    assert by_type1["意外险"]["annualPremium"] == 0.00
    ph1 = _by_name(after["byMember"]["byPolicyholder"])
    assert ph1["李四"]["annualPremium"] == 0.00

    # ---- ❌ 计数 / 待缴 / 已缴 / 清单完全不变 ----
    for key in ("policyTotal", "activePolicyCount", "insuredPersonCount", "upcoming60Premium", "upcoming60Count", "overduePolicyCount"):
        assert after["overview"][key] == before["overview"][key], f"overview.{key} 不应变化"

    # 各行仍存在，计数不变，仅年化变 0
    for name in ("张三", "李四"):
        assert ins1[name]["policyCount"] == ins0[name]["policyCount"]
        assert ins1[name]["upcoming60Premium"] == ins0[name]["upcoming60Premium"]
        assert ins1[name]["paidToDatePremium"] == ins0[name]["paidToDatePremium"]
    assert ph1["李四"]["policyCount"] == ph0["李四"]["policyCount"] == 2  # 被排除的投保人「那一行」仍在
    assert ph1["李四"]["activePolicyCount"] == ph0["李四"]["activePolicyCount"]

    # byType 的计数 / 待缴 / 已缴也不变
    by_type0 = {r["insuranceType"]: r for r in before["byType"]}
    for t in by_type0:
        assert by_type1[t]["policyCount"] == by_type0[t]["policyCount"]
        assert by_type1[t]["upcoming60Premium"] == by_type0[t]["upcoming60Premium"]
        assert by_type1[t]["paidToDatePremium"] == by_type0[t]["paidToDatePremium"]

    # 待缴清单本身不受影响
    assert after["upcoming"] == before["upcoming"]


def test_excluded_policy_still_in_upcoming_list(client, member_factory) -> None:
    """被排除投保人的保单**仍出现在待缴清单**里（p3 归 m2）。"""
    _seed_mixed(client, member_factory)
    _set_count(client, "m2", False)
    data = _dashboard(client)
    upcoming = data["upcoming"]
    assert len(upcoming) == 1, upcoming
    assert upcoming[0]["productName"] == "p3"
    assert upcoming[0]["premiumPerPeriod"] == 1500.00


def test_excluded_policy_still_in_overdue_list(client, member_factory) -> None:
    """被排除投保人的保单**仍出现在逾期清单**里。"""
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    # m2 名下、月缴、生效于 2020-01-01、手动记 0 期 → 必然逾期
    _policy(
        client,
        productName="p_overdue",
        policyholderId="m2",
        insuredIds=["m2"],
        primaryInsuredId="m2",
        paymentFrequency="月缴",
        premiumPerPeriod=100,
        paidPeriodsOverride=0,
    )
    before = _dashboard(client)
    assert before["overview"]["overduePolicyCount"] == 1
    assert len(before["overdue"]) == 1

    _set_count(client, "m2", False)
    after = _dashboard(client)
    assert after["overview"]["overduePolicyCount"] == 1, "逾期提醒不应被排除影响"
    assert len(after["overdue"]) == 1
    assert after["overdue"][0]["productName"] == "p_overdue"
    # 但年化口径已剔除
    assert after["overview"]["unpaidAnnualPremium"] == 0.00


# ---------------------------------------------------------------------------
# 靶点 3：成员 API 往返（创建 / 编辑回填）
# ---------------------------------------------------------------------------
def test_member_create_with_count_false_roundtrip(client) -> None:
    resp = client.post(
        "/api/members",
        json={
            "name": "王五",
            "relation": "父亲",
            "birthDate": "1960-02-10",
            "gender": "男",
            "countInStats": False,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["countInStats"] is False
    # 列表回读一致
    listed = next(m for m in client.get("/api/members").json() if m["id"] == body["id"])
    assert listed["countInStats"] is False


def test_member_create_defaults_to_counted(client) -> None:
    resp = client.post(
        "/api/members",
        json={"name": "赵六", "relation": "母亲", "birthDate": "1962-08-08", "gender": "女"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["countInStats"] is True


def test_member_update_toggles_both_ways(client) -> None:
    created = client.post(
        "/api/members",
        json={"name": "钱七", "relation": "其他", "birthDate": "2000-01-01", "gender": "未知"},
    ).json()
    assert created["countInStats"] is True

    off = _set_count(client, created["id"], False)
    assert off["countInStats"] is False
    on = _set_count(client, created["id"], True)
    assert on["countInStats"] is True


# ---------------------------------------------------------------------------
# 靶点 4：增量迁移（存量库补列，默认计入，幂等）
# ---------------------------------------------------------------------------
_OLD_MEMBERS_DDL = """
CREATE TABLE members (
    id         VARCHAR(36) NOT NULL PRIMARY KEY,
    name       VARCHAR(20) NOT NULL,
    relation   VARCHAR(4)  NOT NULL,
    birth_date DATE        NOT NULL,
    gender     VARCHAR(4)  NOT NULL,
    remark     VARCHAR(100),
    created_at DATETIME    NOT NULL,
    updated_at DATETIME    NOT NULL
)
"""

_OLD_MEMBER_ROWS = [
    ("m1", "张三", "本人", "1988-06-01", "男", None, "2020-01-01 00:00:00", "2020-01-01 00:00:00"),
    ("m2", "李四", "配偶", "1990-03-12", "女", "备注甲", "2020-01-02 00:00:00", "2020-01-02 00:00:00"),
]

_OLD_MEMBER_COLS = ["id", "name", "relation", "birth_date", "gender", "remark", "created_at", "updated_at"]


def _build_old_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_OLD_MEMBERS_DDL)
        conn.executemany("INSERT INTO members VALUES (?,?,?,?,?,?,?,?)", _OLD_MEMBER_ROWS)
        conn.commit()
    finally:
        conn.close()


def test_migration_adds_count_in_stats_default_true(tmp_path, monkeypatch) -> None:
    """存量库补列：新列出现、旧行全部为「计入(1)」、旧数据逐行不变、幂等。"""
    db_path = str(tmp_path / "v14.db")
    _build_old_db(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)

    assert "count_in_stats" not in {c["name"] for c in inspect(engine).get_columns("members")}
    before = [
        tuple(r)
        for r in engine.connect().execute(text(f"SELECT {', '.join(_OLD_MEMBER_COLS)} FROM members")).all()
    ]

    _run_additive_migrations()

    cols = {c["name"] for c in inspect(engine).get_columns("members")}
    assert "count_in_stats" in cols, "迁移后仍缺列 count_in_stats"
    with engine.connect() as conn:
        values = [r[0] for r in conn.execute(text("SELECT count_in_stats FROM members")).all()]
    assert values == [1, 1], f"存量成员应默认计入(1)：{values}"

    after = [
        tuple(r)
        for r in engine.connect().execute(text(f"SELECT {', '.join(_OLD_MEMBER_COLS)} FROM members")).all()
    ]
    assert after == before, "迁移改动了存量数据"

    # 幂等：再跑一次，列集合与数据都不变
    _run_additive_migrations()
    assert {c["name"] for c in inspect(engine).get_columns("members")} == cols
    with engine.connect() as conn:
        again = [r[0] for r in conn.execute(text("SELECT count_in_stats FROM members")).all()]
    assert again == [1, 1]

    engine.dispose()


def test_migration_registered_for_members() -> None:
    """``count_in_stats`` 必须登记在 ``_ADDITIVE_COLUMNS['members']``。"""
    assert "count_in_stats" in app_main._ADDITIVE_COLUMNS.get("members", {})


def test_orm_default_count_in_stats_true(client, member_factory) -> None:
    """ORM 直插（不显式给 count_in_stats）→ 默认计入。"""
    member_factory(id="mX", name="默认计入", relation=Relation.other, birth_date=date(1995, 5, 5))
    listed = next(m for m in client.get("/api/members").json() if m["id"] == "mX")
    assert listed["countInStats"] is True


# ---------------------------------------------------------------------------
# 靶点 5：跨页面口径一致 —— /api/members 的年化 == dashboard.byInsured 的年化
# ---------------------------------------------------------------------------
def _members_annual_by_name(client) -> dict:
    return {m["name"]: m["annualPremium"] for m in client.get("/api/members").json()}


def _by_insured_annual_by_name(client) -> dict:
    data = _dashboard(client)
    return {r["name"]: r["annualPremium"] for r in data["byMember"]["byInsured"]}


def _assert_members_vs_insured_consistent(client) -> None:
    """逐成员断言：成员列表年化 == 看板 byInsured 年化（同一成员、同一个数）。"""
    members_map = _members_annual_by_name(client)
    insured_map = _by_insured_annual_by_name(client)
    for name, value in insured_map.items():
        assert members_map.get(name) == value, (
            f"{name}: /api/members={members_map.get(name)} vs byInsured={value}"
        )


def test_members_annual_consistent_default(client, member_factory) -> None:
    """默认全计入：/api/members 与 dashboard.byInsured 逐成员年化一致。"""
    _seed_mixed(client, member_factory)
    _assert_members_vs_insured_consistent(client)
    # 顺带锁定默认值（m1 自购 1000 + 被 m2 投保 2000；m2 自购 1500）
    assert _members_annual_by_name(client) == {"张三": 3000.00, "李四": 1500.00}


def test_members_annual_consistent_after_exclude(client, member_factory) -> None:
    """排除 m2 后：两处仍逐成员一致，且都剔除了 m2 名下保单的年化。"""
    _seed_mixed(client, member_factory)
    _set_count(client, "m2", False)
    _assert_members_vs_insured_consistent(client)
    # m1：p1(1000) 保留；p2(2000，投保人 m2) 被剔除 → 1000
    # m2：p3(1500，投保人 m2) 被剔除 → 0
    assert _members_annual_by_name(client) == {"张三": 1000.00, "李四": 0.00}


def test_members_coverage_annual_filtered_after_exclude(client, member_factory) -> None:
    """排除 m2 后：coverageTypes[].annualPremium 也剔除其名下保单，但张数计数不变。"""
    _seed_mixed(client, member_factory)
    _set_count(client, "m2", False)
    zhang = next(m for m in client.get("/api/members").json() if m["name"] == "张三")
    annual = {c["insuranceType"]: c["annualPremium"] for c in zhang["coverageTypes"]}
    counts = {c["insuranceType"]: c["count"] for c in zhang["coverageTypes"]}
    assert annual.get("重疾险") == 1000.00  # p1（投保人 m1，保留）
    assert annual.get("医疗险") == 0.00  # p2（投保人 m2，剔除年化）
    assert counts.get("医疗险") == 1  # 计数不受影响


def test_members_count_and_paid_not_filtered(client, member_factory) -> None:
    """排除 m2 后：成员列表的计数不变（年化是唯一被过滤的口径）。"""
    _seed_mixed(client, member_factory)
    before = {m["name"]: m for m in client.get("/api/members").json()}
    _set_count(client, "m2", False)
    after = {m["name"]: m for m in client.get("/api/members").json()}
    for name in before:
        assert after[name]["policyCount"] == before[name]["policyCount"]
        assert after[name]["activePolicyCount"] == before[name]["activePolicyCount"]
        assert [
            (c["insuranceType"], c["count"], c["activeCount"])
            for c in after[name]["coverageTypes"]
        ] == [
            (c["insuranceType"], c["count"], c["activeCount"])
            for c in before[name]["coverageTypes"]
        ]


# ---------------------------------------------------------------------------
# 靶点 6：保单列表 countedInStats（派生标识；annualizedPremium 本身不过滤）
# ---------------------------------------------------------------------------
def test_policy_counted_in_stats_reflects_holder(client, member_factory) -> None:
    """保单 countedInStats 反映其投保人是否计入；排除后该投保人名下保单为 False。"""
    _seed_mixed(client, member_factory)
    # 默认全开：全部 True
    for policy in client.get("/api/policies").json():
        assert policy["countedInStats"] is True, policy

    _set_count(client, "m2", False)
    by_product = {p["productName"]: p for p in client.get("/api/policies").json()}
    assert by_product["p1"]["countedInStats"] is True  # 投保人 m1
    assert by_product["p2"]["countedInStats"] is False  # 投保人 m2
    assert by_product["p3"]["countedInStats"] is False  # 投保人 m2
    # annualizedPremium 本身不过滤（保单事实数据）
    assert by_product["p2"]["annualizedPremium"] == 2000.00
    assert by_product["p3"]["annualizedPremium"] == 1500.00
