"""v1.7「不计入统计」排除配置（按投保人 + 按险种）单测。

覆盖：

- ``GET`` / ``PUT /api/stats/exclusions`` 往返一致（全量替换语义）；
- 非法险种 / 不存在的成员 → 422 ``VALIDATION_ERROR``（**不新增错误码**）；
- 过滤范围：**仅**「年化保费」口径受影响，计数 / ``upcoming60*`` / ``overdue[]`` /
  ``paidToDatePremium`` / ``yearUnpaidPremium`` 一律不变；
- 迁移：新表 ``stats_exclusions`` 由 ``create_all`` 建出，**不**登记 ``_ADDITIVE_COLUMNS``。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import inspect

from app.database import Base

# 确保模型注册到 Base.metadata
import app.models  # noqa: E402,F401

TODAY_STR = "2025-05-20"


def _dashboard(client, today: str = TODAY_STR) -> dict:
    resp = client.get("/api/stats/dashboard", params={"today": today})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _get_exclusions(client) -> dict:
    resp = client.get("/api/stats/exclusions")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _put_exclusions(client, holders=None, types=None) -> dict:
    payload = {
        "excludedPolicyholderIds": holders or [],
        "excludedInsuranceTypes": types or [],
    }
    return client.put("/api/stats/exclusions", json=payload)


# ---------------------------------------------------------------------------
# 默认 / 往返
# ---------------------------------------------------------------------------
def test_exclusions_default_empty(client) -> None:
    body = _get_exclusions(client)
    assert body == {"excludedPolicyholderIds": [], "excludedInsuranceTypes": []}


def test_exclusions_roundtrip(client, seed_example_a) -> None:
    resp = _put_exclusions(client, holders=["m1"], types=["医疗险"])
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["excludedPolicyholderIds"] == ["m1"]
    assert saved["excludedInsuranceTypes"] == ["医疗险"]
    # GET 回读一致
    assert _get_exclusions(client) == saved


def test_exclusions_full_replace_semantics(client, seed_example_a) -> None:
    _put_exclusions(client, holders=["m1"], types=["医疗险"])
    # 第二次 PUT 用空集合 → 全部恢复计入
    resp = _put_exclusions(client, holders=[], types=[])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"excludedPolicyholderIds": [], "excludedInsuranceTypes": []}
    assert _get_exclusions(client) == {"excludedPolicyholderIds": [], "excludedInsuranceTypes": []}


# ---------------------------------------------------------------------------
# 校验 → 422（不新增错误码）
# ---------------------------------------------------------------------------
def test_exclusions_invalid_insurance_type_422(client) -> None:
    resp = _put_exclusions(client, types=["不存在的险种"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_exclusions_nonexistent_member_422(client, seed_example_a) -> None:
    resp = _put_exclusions(client, holders=["m_does_not_exist"])
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "VALIDATION_ERROR"
    # 失败不产生副作用
    assert _get_exclusions(client)["excludedPolicyholderIds"] == []


# ---------------------------------------------------------------------------
# 过滤范围：仅年化口径
# ---------------------------------------------------------------------------
def test_exclude_holder_changes_only_annual(client, seed_example_a) -> None:
    """排除投保人 m1（张三）→ 年化口径变化，其余口径（含新指标）不变。"""
    before = _dashboard(client)["overview"]

    resp = _put_exclusions(client, holders=["m1"])
    assert resp.status_code == 200, resp.text
    after = _dashboard(client)["overview"]

    # 年化口径变化（m1 是全部 4 张保单的投保人 → 归零）
    assert before["annualTotalPremium"] == 17000.00
    assert after["annualTotalPremium"] == 0.00
    assert after["unpaidAnnualPremium"] == 0.00

    # 计数 / 待缴 / 新指标 —— 一律不变
    assert after["upcoming60Premium"] == before["upcoming60Premium"] == 4000.00
    assert after["upcoming60Count"] == before["upcoming60Count"] == 3
    assert after["policyTotal"] == before["policyTotal"] == 4
    assert after["activePolicyCount"] == before["activePolicyCount"] == 3
    assert after["insuredPersonCount"] == before["insuredPersonCount"] == 3
    # ★ 新指标不受筛选影响
    assert after["yearUnpaidPremium"] == before["yearUnpaidPremium"] == 14500.00


def test_exclude_type_changes_only_annual_of_that_type(client, seed_example_a) -> None:
    """排除险种「医疗险」→ 该行 annualPremium 归零，计数 / 已缴不变。"""
    before = _dashboard(client)
    before_medical = next(
        r for r in before["byType"] if r["insuranceType"] == "医疗险"
    )
    assert before_medical["annualPremium"] == 8000.00

    resp = _put_exclusions(client, types=["医疗险"])
    assert resp.status_code == 200, resp.text
    after = _dashboard(client)
    after_medical = next(r for r in after["byType"] if r["insuranceType"] == "医疗险")

    # 年化变化
    assert after_medical["annualPremium"] == 0.00
    assert after["overview"]["annualTotalPremium"] == 9000.00  # 17000 - 8000
    # 计数 / 已缴 / 新指标不变
    assert after_medical["policyCount"] == before_medical["policyCount"] == 1
    assert after_medical["paidToDatePremium"] == before_medical["paidToDatePremium"]
    assert after_medical["upcoming60Premium"] == before_medical["upcoming60Premium"]
    assert after["overview"]["yearUnpaidPremium"] == 14500.00
    assert after["overview"]["upcoming60Premium"] == 4000.00
    assert after["overview"]["policyTotal"] == 4


def test_member_annual_respects_type_exclusion(client, seed_example_a) -> None:
    """``/api/members`` 的 annualPremium 叠加险种过滤（跨页面口径一致）。"""
    before = {m["name"]: m["annualPremium"] for m in client.get("/api/members").json()}
    assert before["李四"] == 8000.00  # 李四是 p_B(医疗险) 的被保人

    _put_exclusions(client, types=["医疗险"])
    after = {m["name"]: m["annualPremium"] for m in client.get("/api/members").json()}
    assert after["李四"] == 0.00
    # 张三作为被保人仅关联 p_A(重疾 6000) 与 p_D(趸交 0) → 不受医疗险排除影响
    assert after["张三"] == 6000.00


# ---------------------------------------------------------------------------
# 迁移：新表由 create_all 建出，不登记 _ADDITIVE_COLUMNS
# ---------------------------------------------------------------------------
def test_stats_exclusion_table_registered() -> None:
    assert "stats_exclusions" in Base.metadata.tables
    table = Base.metadata.tables["stats_exclusions"]
    cols = {c.name for c in table.columns}
    assert {"kind", "ref_id", "created_at"} <= cols
    pk = {c.name for c in table.primary_key.columns}
    assert pk == {"kind", "ref_id"}


def test_stats_exclusion_created_by_create_all(db_session) -> None:
    inspector = inspect(db_session.get_bind())
    assert "stats_exclusions" in set(inspector.get_table_names())
    cols = {c["name"] for c in inspector.get_columns("stats_exclusions")}
    assert {"kind", "ref_id", "created_at"} <= cols


def test_new_table_not_in_additive_columns() -> None:
    import app.main as app_main

    assert "stats_exclusions" not in app_main._ADDITIVE_COLUMNS


def test_set_exclusions_writes_and_reads(db_session) -> None:
    """服务层直调：写入险种排除后，``excluded_insurance_types`` 可读回。"""
    from app.services import insured_service, stats_service
    from app.schemas import StatsExclusionsIn

    stats_service.set_exclusions(
        db_session, StatsExclusionsIn(excluded_insurance_types=["车险", "财产险"])
    )
    assert insured_service.excluded_insurance_types(db_session) == {"车险", "财产险"}
    out = stats_service.get_exclusions(db_session)
    assert out.excluded_insurance_types == ["财产险", "车险"]  # 排序后
    assert out.excluded_policyholder_ids == []


def test_enumerate_year_periods_year_boundary_no_regression(client, seed_example_a) -> None:
    """守护：默认全计入下 ``yearUnpaidPremium`` 稳定为 14500（不随筛选漂移）。"""
    assert _dashboard(client)["overview"]["yearUnpaidPremium"] == 14500.00
    _put_exclusions(client, holders=["m1"], types=["医疗险"])
    assert _dashboard(client)["overview"]["yearUnpaidPremium"] == 14500.00
