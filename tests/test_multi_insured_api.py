"""多被保人保单 API 测试（v1.3）。

覆盖 T02 验收标准：

1. 新建多被保人保单（2 人）后重开回填 ``insuredIds`` / ``primaryInsuredId`` 正确；
2. 未选被保人 / 主被保人不在列表 / 成员不存在 → 422；
3. 响应含 ``insuredIds`` / ``primaryInsuredId`` / ``insuredNames``，
   且 ``insuredId == primaryInsuredId``、``insuredName == 主被保人姓名``；
4. ``GET /api/policies?insuredId=mX`` 返回「被保人含 mX」的全部保单（any-of）；
5. 单人保单请求 / 响应与 v1.2 等价（仅发旧 ``insuredId``）。
"""

from __future__ import annotations

from datetime import date

from app.enums import Gender, Relation


def _seed_members(member_factory) -> None:
    """张三(本人) / 李四(配偶) / 小张(子女)。"""
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    member_factory(id="m3", name="小张", relation=Relation.child, birth_date=date(2015, 9, 20), gender=Gender.male)


def _payload(**over) -> dict:
    """默认：投保人 m1，被保人 [m1, m2]，主被保人 m1。"""
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


# ---------------------------------------------------------------------------
# 1) 新建多被保人 + 重开回填
# ---------------------------------------------------------------------------
def test_create_multi_insured_and_reopen(client, member_factory) -> None:
    _seed_members(member_factory)
    resp = client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m2"),
    )
    assert resp.status_code == 201
    body = resp.json()

    # 规范序：本人(0) 在 配偶(1) 之前
    assert body["insuredIds"] == ["m1", "m2"]
    assert body["insuredNames"] == ["张三", "李四"]
    assert body["primaryInsuredId"] == "m2"
    # 兼容字段
    assert body["insuredId"] == body["primaryInsuredId"] == "m2"
    assert body["insuredName"] == "李四"

    # 重开（GET 详情）回填一致
    policy_id = body["id"]
    got = client.get(f"/api/policies/{policy_id}").json()
    assert got["insuredIds"] == ["m1", "m2"]
    assert got["insuredNames"] == ["张三", "李四"]
    assert got["primaryInsuredId"] == "m2"
    assert got["insuredId"] == "m2"
    assert got["insuredName"] == "李四"


def test_primary_defaults_to_first_insured(client, member_factory) -> None:
    """未显式给主被保人时，默认 = insuredIds[0]。"""
    _seed_members(member_factory)
    payload = _payload(insuredIds=["m2", "m1"])
    payload.pop("primaryInsuredId")
    resp = client.post("/api/policies", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["primaryInsuredId"] == "m2"
    # 展示仍按规范序（m1 本人在前）
    assert body["insuredIds"] == ["m1", "m2"]


def test_update_changes_insured_set(client, member_factory) -> None:
    _seed_members(member_factory)
    created = client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1")).json()
    policy_id = created["id"]

    updated = client.put(
        f"/api/policies/{policy_id}",
        json=_payload(insuredIds=["m1", "m2", "m3"], primaryInsuredId="m3"),
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["insuredIds"] == ["m1", "m2", "m3"]  # 本人→配偶→子女
    assert body["primaryInsuredId"] == "m3"
    assert body["insuredName"] == "小张"

    # 再编辑缩回单选，关联表被整体替换
    back = client.put(
        f"/api/policies/{policy_id}",
        json=_payload(insuredIds=["m2"], primaryInsuredId="m2"),
    ).json()
    assert back["insuredIds"] == ["m2"]
    assert back["primaryInsuredId"] == "m2"


# ---------------------------------------------------------------------------
# 2) 校验 → 422
# ---------------------------------------------------------------------------
def test_no_insured_returns_422(client, member_factory) -> None:
    _seed_members(member_factory)
    payload = _payload(insuredIds=[], primaryInsuredId=None)
    resp = client.post("/api/policies", json=payload)
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_primary_not_in_insured_list_returns_422(client, member_factory) -> None:
    _seed_members(member_factory)
    resp = client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1"], primaryInsuredId="m2"),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_insured_member_not_exist_returns_422(client, member_factory) -> None:
    _seed_members(member_factory)
    resp = client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m_nope"], primaryInsuredId="m1"),
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"]["memberId"] == "m_nope"
    assert body["detail"]["role"] == "被保人"


# ---------------------------------------------------------------------------
# 3) 响应字段完整性
# ---------------------------------------------------------------------------
def test_response_fields_complete(client, member_factory) -> None:
    _seed_members(member_factory)
    body = client.post("/api/policies", json=_payload()).json()
    for key in ("insuredIds", "primaryInsuredId", "insuredNames", "insuredId", "insuredName"):
        assert key in body
    assert body["insuredId"] == body["primaryInsuredId"]
    assert body["insuredName"] == "张三"  # 主被保人姓名


# ---------------------------------------------------------------------------
# 4) any-of 筛选
# ---------------------------------------------------------------------------
def test_filter_insured_any_of(client, member_factory) -> None:
    _seed_members(member_factory)
    pa = client.post("/api/policies", json=_payload(insuredIds=["m1"], primaryInsuredId="m1")).json()
    pb = client.post(
        "/api/policies",
        json=_payload(insuredIds=["m1", "m2"], primaryInsuredId="m2", productName="双人被保"),
    ).json()

    by_m1 = client.get("/api/policies", params={"insuredId": "m1"}).json()
    assert {p["id"] for p in by_m1} == {pa["id"], pb["id"]}

    by_m2 = client.get("/api/policies", params={"insuredId": "m2"}).json()
    assert {p["id"] for p in by_m2} == {pb["id"]}

    by_m3 = client.get("/api/policies", params={"insuredId": "m3"}).json()
    assert by_m3 == []


# ---------------------------------------------------------------------------
# 5) 单人保单（旧 insuredId）向后兼容
# ---------------------------------------------------------------------------
def test_single_insured_legacy_payload(client, member_factory) -> None:
    _seed_members(member_factory)
    payload = _payload()
    payload.pop("insuredIds")
    payload.pop("primaryInsuredId")
    payload["insuredId"] = "m1"  # 旧字段（单值降级）

    resp = client.post("/api/policies", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["insuredId"] == "m1"
    assert body["primaryInsuredId"] == "m1"
    assert body["insuredIds"] == ["m1"]
    assert body["insuredNames"] == ["张三"]
    assert body["insuredName"] == "张三"
