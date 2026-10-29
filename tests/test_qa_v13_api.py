"""QA 独立验证 —— 多被保人**写路径边界**与枚举下发（v1.3）。

攻击工程师可能漏测的写路径：

- 2 被保人 → 1 被保人：关联表是否**清理干净**（无残留旧关联）；
- 更换主被保人：``policies.insured_id`` 与关联表是否**同时更新**；
- 删除成员引用计数**去重**（投保人 ∪ 被保人，同一张保单只算 1）；
- ``primaryInsuredId`` 不在 ``insuredIds`` / 成员不存在 → 422 且错误格式 ``{code,message,detail}``；
- ``insuredId`` 查询参数 **any-of**（真实 HTTP）；
- ``/api/meta/enums`` 自动下发「分红险」；新建「分红险」保单零迁移可成功。
"""

from __future__ import annotations

from datetime import date

from app.models import Policy, PolicyInsured
from app.enums import Gender, Relation


def _payload(**over) -> dict:
    base = {
        "company": "测试保险",
        "insuranceType": "重疾险",
        "productName": "测试产品",
        "policyholderId": "m1",
        "insuredIds": ["m1", "m2"],
        "primaryInsuredId": "m1",
        "effectiveDate": "2020-03-15",
        "coverageTermType": "终身",
        "coverageTermValue": None,
        "paymentTermType": "固定年限",
        "paymentTermValue": 20,
        "paymentFrequency": "月缴",
        "premiumPerPeriod": 500,
    }
    base.update(over)
    return base


def _seed3(member_factory) -> None:
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    member_factory(id="m3", name="小张", relation=Relation.child, birth_date=date(2015, 9, 20), gender=Gender.male)


def _rows(db_session, policy_id) -> set[str]:
    db_session.expire_all()
    return {
        r[0]
        for r in db_session.query(PolicyInsured.member_id).filter(PolicyInsured.policy_id == policy_id).all()
    }


# ---------------------------------------------------------------------------
# 1) 2 → 1：关联表清理
# ---------------------------------------------------------------------------
def test_update_reduce_insureds_cleans_relation(client, db_session, member_factory) -> None:
    _seed3(member_factory)
    created = client.post("/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1")).json()
    pid = created["id"]
    assert _rows(db_session, pid) == {"m1", "m2"}

    resp = client.put(f"/api/policies/{pid}", json=_payload(insuredIds=["m1"], primaryInsuredId="m1"))
    assert resp.status_code == 200
    # ★ 关联表不得残留 m2
    assert _rows(db_session, pid) == {"m1"}
    got = client.get(f"/api/policies/{pid}").json()
    assert got["insuredIds"] == ["m1"]
    assert got["insuredNames"] == ["张三"]
    assert got["primaryInsuredId"] == "m1"


def test_update_reduce_then_filter_no_phantom(client, member_factory) -> None:
    """缩为单选后，any-of 筛选 m2 不应再命中该保单。"""
    _seed3(member_factory)
    pid = client.post("/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1")).json()["id"]
    client.put(f"/api/policies/{pid}", json=_payload(insuredIds=["m1"], primaryInsuredId="m1"))
    assert client.get("/api/policies", params={"insuredId": "m2"}).json() == []


# ---------------------------------------------------------------------------
# 2) 更换主被保人：policies.insured_id 与关联表同时更新
# ---------------------------------------------------------------------------
def test_update_changes_primary_updates_both(client, db_session, member_factory) -> None:
    _seed3(member_factory)
    pid = client.post("/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m1")).json()["id"]
    resp = client.put(f"/api/policies/{pid}", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m2"))
    assert resp.status_code == 200

    db_session.expire_all()
    policy = db_session.get(Policy, pid)
    assert policy.insured_id == "m2"  # 主被保人真源已更新
    assert _rows(db_session, pid) == {"m1", "m2"}  # 关联表仍含两人

    body = resp.json()
    assert body["primaryInsuredId"] == "m2"
    assert body["insuredId"] == "m2"  # 兼容字段
    assert body["insuredName"] == "李四"
    # 展示仍按规范序（本人在前）
    assert body["insuredIds"] == ["m1", "m2"]


# ---------------------------------------------------------------------------
# 3) 删除成员引用计数去重
# ---------------------------------------------------------------------------
def test_delete_member_policyholder_and_insured_same_policy_dedup(client, member_factory) -> None:
    """m1 既是投保人又是被保人（同一张保单）→ 计数应为 1（去重）。"""
    _seed3(member_factory)
    client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1", policyholderId="m1"))
    resp = client.delete("/api/members/m1")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "MEMBER_IN_USE"
    assert body["detail"]["policyCount"] == 1


def test_delete_member_dedup_across_policies(client, member_factory) -> None:
    """m1 是 p1 的投保人+被保人，且是 p2 的被保人 → 去重后 2 张。"""
    _seed3(member_factory)
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
# 4) 422 错误格式
# ---------------------------------------------------------------------------
def test_primary_not_in_list_422_format(client, member_factory) -> None:
    _seed3(member_factory)
    resp = client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m2"))
    assert resp.status_code == 422
    body = resp.json()
    assert {"code", "message", "detail"} <= set(body)
    assert body["code"] == "VALIDATION_ERROR"


def test_member_not_exist_422_format(client, member_factory) -> None:
    _seed3(member_factory)
    resp = client.post("/api/policies", json=_payload(insuredIds=["m1", "m_nope"], primaryInsuredId="m1"))
    assert resp.status_code == 422
    body = resp.json()
    assert {"code", "message", "detail"} <= set(body)
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"]["memberId"] == "m_nope"
    assert body["detail"]["role"] == "被保人"


def test_empty_insureds_422(client, member_factory) -> None:
    _seed3(member_factory)
    payload = _payload(insuredIds=[], primaryInsuredId=None)
    resp = client.post("/api/policies", json=payload)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 5) any-of 筛选（真实 HTTP）
# ---------------------------------------------------------------------------
def test_filter_insured_any_of(client, member_factory) -> None:
    _seed3(member_factory)
    pa = client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1")).json()
    pb = client.post(
        "/api/policies", json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m2", productName="双人")
    ).json()
    assert {p["id"] for p in client.get("/api/policies", params={"insuredId": "m1"}).json()} == {pa["id"], pb["id"]}
    assert {p["id"] for p in client.get("/api/policies", params={"insuredId": "m2"}).json()} == {pb["id"]}
    assert client.get("/api/policies", params={"insuredId": "m3"}).json() == []


# ---------------------------------------------------------------------------
# 6) 枚举单一真源：分红险自动下发 + 零迁移可建单
# ---------------------------------------------------------------------------
def test_meta_enums_includes_dividend(client) -> None:
    enums = client.get("/api/meta/enums").json()
    types = enums["insuranceType"]
    assert len(types) == 10
    assert types.index("分红险") == types.index("年金险") + 1


def test_create_dividend_policy_succeeds(client, member_factory) -> None:
    _seed3(member_factory)
    resp = client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1", insuranceType="分红险"))
    assert resp.status_code == 201
    assert resp.json()["insuranceType"] == "分红险"
