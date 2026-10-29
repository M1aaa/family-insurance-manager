"""v1.2 售后字段 CRUD 与常用值聚合（``/suggestions``）测试。"""

from __future__ import annotations

from datetime import date

from app.enums import (
    CoverageTermType,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)

AFTER_SALES_KEYS = [
    "advisorName",
    "advisorPhone",
    "advisorWechat",
    "advisorEmail",
    "companyPhone",
    "channelName",
    "channelPhone",
]


def _member(client, name: str = "张三") -> dict:
    resp = client.post(
        "/api/members",
        json={"name": name, "relation": "本人", "birthDate": "1990-01-01", "gender": "男"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _policy(client, holder_id: str, **overrides) -> dict:
    payload = {
        "company": "众安",
        "insuranceType": "医疗险",
        "productName": "测试产品",
        "policyholderId": holder_id,
        "insuredId": holder_id,
        "effectiveDate": "2024-01-01",
        "coverageTermType": "固定年限",
        "coverageTermValue": 30,
        "paymentTermType": "固定年限",
        "paymentTermValue": 10,
        "paymentFrequency": "年缴",
        "premiumPerPeriod": 8000,
    }
    payload.update(overrides)
    resp = client.post("/api/policies", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 售后字段 CRUD
# ---------------------------------------------------------------------------
def test_after_sales_fields_create_read_update_clear(client):
    holder = _member(client)
    created = _policy(
        client,
        holder["id"],
        advisorName="王小明",
        advisorPhone="13800000000",
        advisorWechat="wx_wangxm",
        advisorEmail="wxm@example.com",
        companyPhone="1010-9955/952299",
        channelName="支付宝蚂蚁保",
        channelPhone="95188",
    )
    assert created["advisorName"] == "王小明"
    assert created["advisorPhone"] == "13800000000"
    assert created["advisorWechat"] == "wx_wangxm"
    assert created["advisorEmail"] == "wxm@example.com"
    assert created["companyPhone"] == "1010-9955/952299"
    assert created["channelName"] == "支付宝蚂蚁保"
    assert created["channelPhone"] == "95188"

    # 读取回填
    got = client.get(f"/api/policies/{created['id']}")
    assert got.status_code == 200
    body = got.json()
    for key in AFTER_SALES_KEYS:
        assert body[key] == created[key]

    # 更新：只改两个字段，其余保持
    updated = client.put(
        f"/api/policies/{created['id']}",
        json={
            "company": "众安",
            "insuranceType": "医疗险",
            "productName": "测试产品",
            "policyholderId": holder["id"],
            "insuredId": holder["id"],
            "effectiveDate": "2024-01-01",
            "coverageTermType": "固定年限",
            "coverageTermValue": 30,
            "paymentTermType": "固定年限",
            "paymentTermValue": 10,
            "paymentFrequency": "年缴",
            "premiumPerPeriod": 8000,
            "advisorName": "李丽",
            "advisorPhone": "13900000000",
            "advisorWechat": "wx_wangxm",
            "advisorEmail": "wxm@example.com",
            "companyPhone": "1010-9955/952299",
            "channelName": "支付宝蚂蚁保",
            "channelPhone": "95188",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["advisorName"] == "李丽"
    assert updated.json()["advisorPhone"] == "13900000000"

    # 清空：显式传 null
    cleared = client.put(
        f"/api/policies/{created['id']}",
        json={
            "company": "众安",
            "insuranceType": "医疗险",
            "productName": "测试产品",
            "policyholderId": holder["id"],
            "insuredId": holder["id"],
            "effectiveDate": "2024-01-01",
            "coverageTermType": "固定年限",
            "coverageTermValue": 30,
            "paymentTermType": "固定年限",
            "paymentTermValue": 10,
            "paymentFrequency": "年缴",
            "premiumPerPeriod": 8000,
            "advisorName": None,
            "advisorPhone": None,
            "advisorWechat": None,
            "advisorEmail": None,
            "companyPhone": None,
            "channelName": None,
            "channelPhone": None,
        },
    )
    assert cleared.status_code == 200, cleared.text
    for key in AFTER_SALES_KEYS:
        assert cleared.json()[key] is None


def test_after_sales_all_blank_can_save(client):
    """全部留空也能正常保存（B-09）。"""
    holder = _member(client)
    created = _policy(client, holder["id"])
    for key in AFTER_SALES_KEYS:
        assert created[key] is None


def test_after_sales_whitespace_normalized_to_none(client):
    """首尾空格被去除；纯空串归一为 None。"""
    holder = _member(client)
    created = _policy(client, holder["id"], advisorName="  王小明  ", advisorPhone="   ")
    assert created["advisorName"] == "王小明"
    assert created["advisorPhone"] is None


def test_legacy_policy_without_after_sales_reads_ok(client, db_session, member_factory, policy_factory):
    """存量保单（无售后字段）读取不报错（B-10）。"""
    member_factory(
        id="m_legacy",
        name="老保单户",
        relation=Relation.self_,
        birth_date=date(1980, 1, 1),
    )
    policy_factory(
        id="p_legacy",
        insurance_type=InsuranceType.medical,
        policyholder_id="m_legacy",
        insured_id="m_legacy",
        effective_date=date(2020, 1, 1),
        coverage_term_type=CoverageTermType.fixed_years,
        coverage_term_value=30,
        payment_term_type=PaymentTermType.fixed_years,
        payment_term_value=10,
        payment_frequency=PaymentFrequency.yearly,
        premium_per_period="8000",
        status=PolicyStatus.active,
    )
    db_session.commit()

    got = client.get("/api/policies/p_legacy")
    assert got.status_code == 200, got.text
    body = got.json()
    for key in AFTER_SALES_KEYS:
        assert body[key] is None


def test_attachment_count_default_zero(client):
    """新建保单 attachmentCount 为 0。"""
    holder = _member(client)
    created = _policy(client, holder["id"])
    assert created["attachmentCount"] == 0


# ---------------------------------------------------------------------------
# 常用值聚合 /suggestions
# ---------------------------------------------------------------------------
def test_suggestions_not_shadowed_by_policy_id(client):
    """``/suggestions`` 未被 ``/{policy_id}`` 吞掉：返回聚合结构而非 404。"""
    resp = client.get("/api/policies/suggestions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "advisorName" in body and "channelPhone" in body


def test_suggestions_empty_history_returns_empty_arrays(client):
    """无历史值 → 各字段空数组（B-12）。"""
    resp = client.get("/api/policies/suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "advisorName",
        "advisorPhone",
        "advisorWechat",
        "advisorEmail",
        "channelName",
        "channelPhone",
    }
    for value in body.values():
        assert value == []


def test_suggestions_aggregation_order_and_dedup(client):
    """去重 + 计数降序；同次数按值升序；companyPhone 不参与。"""
    holder = _member(client)
    # advisorName: 王小明 x3, 李丽 x1
    for _ in range(3):
        _policy(client, holder["id"], advisorName="王小明", companyPhone="1010-9955")
    _policy(client, holder["id"], advisorName="李丽", companyPhone="1010-9955")
    # channelName: 支付宝蚂蚁保 x2
    for _ in range(2):
        _policy(client, holder["id"], channelName="支付宝蚂蚁保")

    body = client.get("/api/policies/suggestions").json()

    assert body["advisorName"] == [
        {"value": "王小明", "count": 3},
        {"value": "李丽", "count": 1},
    ]
    assert body["channelName"] == [{"value": "支付宝蚂蚁保", "count": 2}]
    # companyPhone 刻意不参与聚合
    assert "companyPhone" not in body
    # 未填字段为空数组
    assert body["advisorPhone"] == []
    assert body["channelPhone"] == []


def test_suggestions_tie_break_value_ascending(client):
    """同次数按 value 升序。"""
    holder = _member(client)
    _policy(client, holder["id"], advisorName="张三")
    _policy(client, holder["id"], advisorName="李四")
    body = client.get("/api/policies/suggestions").json()
    assert [item["value"] for item in body["advisorName"]] == ["张三", "李四"]


def test_suggestions_limit_caps_per_field(client):
    """limit 限制每个字段返回条数。"""
    holder = _member(client)
    for name in ["A顾问", "B顾问", "C顾问"]:
        _policy(client, holder["id"], advisorName=name)
    body = client.get("/api/policies/suggestions?limit=2").json()
    assert len(body["advisorName"]) == 2


def test_suggestions_limit_out_of_range_422(client):
    """limit 越界 → 422。"""
    assert client.get("/api/policies/suggestions?limit=0").status_code == 422
    assert client.get("/api/policies/suggestions?limit=51").status_code == 422
    assert client.get("/api/policies/suggestions?limit=1").status_code == 200
    assert client.get("/api/policies/suggestions?limit=50").status_code == 200
