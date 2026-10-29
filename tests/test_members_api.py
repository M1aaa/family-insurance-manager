"""成员接口测试（含 409 场景）。"""

from __future__ import annotations


def _member_payload(name="张三", relation="本人", birth_date="1988-06-01", gender="男", remark=None):
    payload = {"name": name, "relation": relation, "birthDate": birth_date, "gender": gender}
    if remark is not None:
        payload["remark"] = remark
    return payload


def test_create_member(client):
    resp = client.post("/api/members", json=_member_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("m_")
    assert body["name"] == "张三"
    assert body["relation"] == "本人"
    assert body["birthDate"] == "1988-06-01"
    assert body["policyCount"] == 0
    assert body["activePolicyCount"] == 0
    assert body["annualPremium"] == 0.0


def test_create_member_future_birth_date(client):
    resp = client.post("/api/members", json=_member_payload(birth_date="2999-01-01"))
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_duplicate_primary_member(client):
    assert client.post("/api/members", json=_member_payload()).status_code == 201
    resp = client.post("/api/members", json=_member_payload(name="李四"))
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "DUPLICATE_PRIMARY_MEMBER"
    assert "本人" in body["message"]


def test_members_sorted_by_relation_weight(client):
    client.post("/api/members", json=_member_payload(name="小张", relation="子女", birth_date="2015-09-20"))
    client.post("/api/members", json=_member_payload(name="李四", relation="配偶", birth_date="1990-03-12"))
    client.post("/api/members", json=_member_payload(name="张三", relation="本人", birth_date="1988-06-01"))
    client.post("/api/members", json=_member_payload(name="王五", relation="父亲", birth_date="1960-02-10"))

    resp = client.get("/api/members")
    assert resp.status_code == 200
    relations = [m["relation"] for m in resp.json()]
    assert relations == ["本人", "配偶", "子女", "父亲"]


def test_update_member(client):
    created = client.post("/api/members", json=_member_payload()).json()
    resp = client.put(
        f"/api/members/{created['id']}",
        json=_member_payload(name="张三丰", gender="未知", remark="备注"),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "张三丰"
    assert resp.json()["remark"] == "备注"


def test_update_member_not_found(client):
    resp = client.put("/api/members/m_not_exist", json=_member_payload())
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


def test_delete_unreferenced_member(client):
    created = client.post("/api/members", json=_member_payload()).json()
    resp = client.delete(f"/api/members/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "id": created["id"]}
    assert client.get("/api/members").json() == []


def test_delete_referenced_member_conflict(client):
    holder = client.post("/api/members", json=_member_payload()).json()
    insured = client.post(
        "/api/members", json=_member_payload(name="小张", relation="子女", birth_date="2015-09-20")
    ).json()
    policy = {
        "company": "平安",
        "insuranceType": "重疾险",
        "productName": "平安福",
        "policyholderId": holder["id"],
        "insuredId": insured["id"],
        "effectiveDate": "2020-03-15",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 20,
        "paymentFrequency": "月缴",
        "premiumPerPeriod": 500,
        "status": "生效中",
    }
    assert client.post("/api/policies", json=policy).status_code == 201

    resp = client.delete(f"/api/members/{insured['id']}")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "MEMBER_IN_USE"
    assert "1" in body["message"]
    assert body["detail"]["policyCount"] == 1


def test_member_list_derived_fields(client):
    holder = client.post("/api/members", json=_member_payload()).json()
    policy = {
        "company": "平安",
        "insuranceType": "重疾险",
        "productName": "平安福",
        "policyholderId": holder["id"],
        "insuredId": holder["id"],
        "effectiveDate": "2020-03-15",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 20,
        "paymentFrequency": "月缴",
        "premiumPerPeriod": 500,
        "status": "生效中",
    }
    client.post("/api/policies", json=policy)

    member = client.get("/api/members").json()[0]
    assert member["policyCount"] == 1
    assert member["activePolicyCount"] == 1
    assert member["annualPremium"] == 6000.0
