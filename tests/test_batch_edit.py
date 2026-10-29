"""v1.2 批量编辑（三态语义 / 边界）测试。

三态契约（逐条锁死）：
- 键**不存在** → 不改；
- 键存在且为**字符串** → 设值（trim 后空串按「不改」）；
- 键存在且为 **``null``** → 显式清空。
"""

from __future__ import annotations


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


def _batch(client, ids, values):
    return client.post("/api/policies/batch-update", json={"ids": ids, "values": values})


def _two_policies(client) -> tuple[dict, dict]:
    holder = _member(client)
    p1 = _policy(
        client,
        holder["id"],
        advisorName="原顾问1",
        advisorPhone="111",
        channelName="渠道1",
    )
    p2 = _policy(
        client,
        holder["id"],
        advisorName="原顾问2",
        advisorPhone="222",
        channelName="渠道2",
    )
    return p1, p2


# ---------------------------------------------------------------------------
# 三态语义
# ---------------------------------------------------------------------------
def test_batch_only_named_field_changes_others_untouched(client):
    """只发 advisorName：其余字段逐保单保持原值不变。"""
    p1, p2 = _two_policies(client)
    resp = _batch(client, [p1["id"], p2["id"]], {"advisorName": "新顾问"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["requested"] == 2 and body["updated"] == 2 and body["missingIds"] == []

    a = client.get(f"/api/policies/{p1['id']}").json()
    b = client.get(f"/api/policies/{p2['id']}").json()
    assert a["advisorName"] == "新顾问" and b["advisorName"] == "新顾问"
    # 其他字段逐保单保持原值
    assert a["advisorPhone"] == "111" and b["advisorPhone"] == "222"
    assert a["channelName"] == "渠道1" and b["channelName"] == "渠道2"


def test_batch_null_clears_only_that_field(client):
    """发 advisorPhone: null：该字段被清空，其他字段不动。"""
    p1, p2 = _two_policies(client)
    resp = _batch(client, [p1["id"], p2["id"]], {"advisorPhone": None})
    assert resp.status_code == 200, resp.text

    a = client.get(f"/api/policies/{p1['id']}").json()
    b = client.get(f"/api/policies/{p2['id']}").json()
    assert a["advisorPhone"] is None and b["advisorPhone"] is None
    # 其他字段保持原值
    assert a["advisorName"] == "原顾问1" and b["advisorName"] == "原顾问2"
    assert a["channelName"] == "渠道1" and b["channelName"] == "渠道2"


def test_batch_all_whitespace_treated_as_no_change(client):
    """发 advisorName: '  '（全空格）→ 按「不改」处理，**不清空原值**（最关键）。"""
    p1, p2 = _two_policies(client)
    resp = _batch(client, [p1["id"], p2["id"]], {"advisorName": "  "})
    assert resp.status_code == 200, resp.text

    a = client.get(f"/api/policies/{p1['id']}").json()
    b = client.get(f"/api/policies/{p2['id']}").json()
    assert a["advisorName"] == "原顾问1"
    assert b["advisorName"] == "原顾问2"


# ---------------------------------------------------------------------------
# 边界与校验
# ---------------------------------------------------------------------------
def test_batch_ids_empty_422(client):
    resp = _batch(client, [], {"advisorName": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "POLICY_IDS_EMPTY"


def test_batch_values_empty_422(client):
    holder = _member(client)
    p1 = _policy(client, holder["id"])
    resp = _batch(client, [p1["id"]], {})
    assert resp.status_code == 422
    assert resp.json()["code"] == "FIELD_VALUES_EMPTY"


def test_batch_unknown_field_422(client):
    holder = _member(client)
    p1 = _policy(client, holder["id"])
    resp = _batch(client, [p1["id"]], {"bogusField": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "UNKNOWN_UPDATE_FIELD"


def test_batch_missing_ids_ignored_and_reported(client):
    """不存在的 id 计入 missingIds，其余正常更新。"""
    p1, _ = _two_policies(client)
    resp = _batch(client, [p1["id"], "p_not_exist"], {"advisorName": "有效值"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["missingIds"] == ["p_not_exist"]
    assert body["updated"] == 1
    assert body["updatedIds"] == [p1["id"]]
    # requested = updated + len(missing)
    assert body["requested"] == body["updated"] + len(body["missingIds"])
    assert client.get(f"/api/policies/{p1['id']}").json()["advisorName"] == "有效值"


def test_batch_value_too_long_422(client):
    holder = _member(client)
    p1 = _policy(client, holder["id"])
    too_long = "字" * 21  # advisorName 上限 20
    resp = _batch(client, [p1["id"]], {"advisorName": too_long})
    assert resp.status_code == 422


def test_batch_idempotent(client):
    """同一请求发两次结果一致。"""
    p1, p2 = _two_policies(client)
    payload_ids = [p1["id"], p2["id"]]
    values = {"advisorName": "幂等顾问", "channelName": "幂等渠道"}

    first = _batch(client, payload_ids, values)
    second = _batch(client, payload_ids, values)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json() == second.json()

    a = client.get(f"/api/policies/{p1['id']}").json()
    assert a["advisorName"] == "幂等顾问"
    assert a["channelName"] == "幂等渠道"


def test_batch_multiple_fields_mixed_three_states(client):
    """同一次请求混合三态：设值 + 清空 + 未提及。"""
    p1, p2 = _two_policies(client)
    resp = _batch(
        client,
        [p1["id"], p2["id"]],
        {"advisorName": "混合顾问", "advisorPhone": None},  # channelName 未提及
    )
    assert resp.status_code == 200, resp.text
    a = client.get(f"/api/policies/{p1['id']}").json()
    assert a["advisorName"] == "混合顾问"  # 设值
    assert a["advisorPhone"] is None  # 清空
    assert a["channelName"] == "渠道1"  # 未提及 → 不变
