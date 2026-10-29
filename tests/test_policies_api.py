"""保单接口测试（含筛选 / 搜索 / 校验 / 派生字段）。"""

from __future__ import annotations


def _create_member(client, name="张三", relation="本人", birth_date="1988-06-01"):
    return client.post(
        "/api/members",
        json={"name": name, "relation": relation, "birthDate": birth_date, "gender": "未知"},
    ).json()


def _policy_payload(holder_id, insured_id, **overrides):
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


def test_create_policy_with_derived_fields(client):
    holder = _create_member(client)
    resp = client.post("/api/policies", json=_policy_payload(holder["id"], holder["id"]))
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("p_")
    # 11 个派生字段
    for field in (
        "annualizedPremium",
        "totalPeriods",
        "paidPeriods",
        "cumulativePaid",
        "remainingPeriods",
        "isPaidOff",
        "progressPercent",
        "inCoverage",
        "nextDueDate",
        "insuredName",
        "policyholderName",
    ):
        assert field in body, f"缺少派生字段 {field}"
    assert body["annualizedPremium"] == 6000.0
    assert body["totalPeriods"] == 240
    assert body["insuredName"] == "张三"
    assert body["policyholderName"] == "张三"
    # 金额为 JSON number（非字符串）
    assert isinstance(body["premiumPerPeriod"], float)


def test_create_policy_missing_insured(client):
    holder = _create_member(client)
    resp = client.post("/api/policies", json=_policy_payload(holder["id"], "m_not_exist"))
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_conditional_validation_coverage(client):
    holder = _create_member(client)
    # 终身必须无值
    bad1 = _policy_payload(holder["id"], holder["id"], coverageTermValue=20)
    assert client.post("/api/policies", json=bad1).status_code == 422
    # 固定年限必须有值
    bad2 = _policy_payload(
        holder["id"], holder["id"], coverageTermType="固定年限", coverageTermValue=None
    )
    assert client.post("/api/policies", json=bad2).status_code == 422
    # 固定年限越界
    bad3 = _policy_payload(
        holder["id"], holder["id"], coverageTermType="固定年限", coverageTermValue=101
    )
    assert client.post("/api/policies", json=bad3).status_code == 422


def test_conditional_validation_payment(client):
    holder = _create_member(client)
    # 趸交必须无值
    bad1 = _policy_payload(
        holder["id"], holder["id"], paymentTermType="趸交", paymentTermValue=20
    )
    assert client.post("/api/policies", json=bad1).status_code == 422
    # 交至某年龄必须有值
    bad2 = _policy_payload(
        holder["id"], holder["id"], paymentTermType="交至某年龄", paymentTermValue=None
    )
    assert client.post("/api/policies", json=bad2).status_code == 422
    # 趸交合法（无值）
    good = _policy_payload(
        holder["id"], holder["id"], paymentTermType="趸交", paymentTermValue=None
    )
    assert client.post("/api/policies", json=good).status_code == 201


def test_filter_and_search(client):
    holder = _create_member(client)
    other = _create_member(client, name="李四", relation="配偶", birth_date="1990-03-12")
    client.post("/api/policies", json=_policy_payload(holder["id"], holder["id"]))
    client.post(
        "/api/policies",
        json=_policy_payload(
            holder["id"],
            other["id"],
            company="人保",
            insuranceType="医疗险",
            productName="康健医疗",
            paymentFrequency="年缴",
            premiumPerPeriod=8000,
            status="已退保",
        ),
    )

    # 按被保人筛选
    by_insured = client.get("/api/policies", params={"insuredId": other["id"]}).json()
    assert len(by_insured) == 1
    assert by_insured[0]["productName"] == "康健医疗"

    # 按险种筛选
    assert len(client.get("/api/policies", params={"insuranceType": "重疾险"}).json()) == 1

    # 按状态筛选
    assert len(client.get("/api/policies", params={"status": "已退保"}).json()) == 1

    # 按缴费方式筛选
    assert len(client.get("/api/policies", params={"paymentFrequency": "年缴"}).json()) == 1

    # 关键词搜索（大小写不敏感，匹配 productName）
    kw = client.get("/api/policies", params={"keyword": "康健"}).json()
    assert len(kw) == 1
    assert kw[0]["company"] == "人保"


def test_default_sort_effective_date_desc(client):
    holder = _create_member(client)
    client.post("/api/policies", json=_policy_payload(holder["id"], holder["id"], effectiveDate="2020-03-15"))
    client.post("/api/policies", json=_policy_payload(holder["id"], holder["id"], effectiveDate="2024-06-01"))
    rows = client.get("/api/policies").json()
    assert [r["effectiveDate"] for r in rows] == ["2024-06-01", "2020-03-15"]


def test_get_update_delete_policy(client):
    holder = _create_member(client)
    created = client.post("/api/policies", json=_policy_payload(holder["id"], holder["id"])).json()

    got = client.get(f"/api/policies/{created['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]

    updated = client.put(
        f"/api/policies/{created['id']}",
        json=_policy_payload(holder["id"], holder["id"], premiumPerPeriod=1000),
    )
    assert updated.status_code == 200
    assert updated.json()["premiumPerPeriod"] == 1000.0
    assert updated.json()["annualizedPremium"] == 12000.0

    deleted = client.delete(f"/api/policies/{created['id']}")
    assert deleted.status_code == 200
    body = deleted.json()
    assert body["deleted"] is True
    assert body["id"] == created["id"]
    # v1.2：删除保单响应新增 deletedAttachments（本次实际删除的附件数）
    assert body["deletedAttachments"] == 0

    assert client.get(f"/api/policies/{created['id']}").status_code == 404


def test_get_policy_not_found(client):
    resp = client.get("/api/policies/p_not_exist")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"
