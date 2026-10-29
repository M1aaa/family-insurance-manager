"""v1.5 QA 独立验证 —— 靶点 B：增量迁移（真实数据安全）。

使用 ``data/insurance.db`` 的**副本**做验证，**绝不修改原库**：
1. 副本删列模拟 v1.4 → 跑 ``_run_additive_migrations`` → 列出现、存量全 = 1；
2. 迁移后 dashboard 数字与「不过滤基线」完全一致；
3. 连跑两次幂等（列集合 / DDL / flags / dashboard 全等）；
4. 复查原库 md5 未被改动。

并顺带在**真实数据**上复验 A2（byInsured 按投保人过滤）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import app.main as app_main
from app.database import Base
from app.services import insured_service, stats_service

# v1.7：确保新表 ``stats_exclusions`` 已注册到 ``Base.metadata``。
import app.models  # noqa: E402,F401


def _ensure_schema(engine) -> None:
    """模拟应用启动时的 ``Base.metadata.create_all``（建出 v1.7 新表）。"""
    Base.metadata.create_all(bind=engine)

REAL_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "insurance.db"))
TODAY = date(2025, 5, 20)


def _md5(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def _copy_real(tmp_path) -> str:
    if not os.path.exists(REAL_DB):
        pytest.skip(f"真实库不存在：{REAL_DB}")
    dst = str(tmp_path / "copy.db")
    shutil.copy2(REAL_DB, dst)
    return dst


def _drop_count_column(db_path: str) -> None:
    """把副本降级为 v1.4（members 无 count_in_stats 列）。"""
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(members)")}
        if "count_in_stats" in cols:
            conn.execute("ALTER TABLE members DROP COLUMN count_in_stats")
            conn.commit()
    finally:
        conn.close()


def _member_cols(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(members)")]
    finally:
        conn.close()


def _member_ddl(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT sql FROM sqlite_master WHERE name='members'").fetchone()[0]
    finally:
        conn.close()


def _flags(db_path: str) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT count_in_stats FROM members ORDER BY id")]
    finally:
        conn.close()


def _dashboard(db_path: str) -> dict:
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _ensure_schema(engine)  # v1.7：建出 stats_exclusions（幂等）
    session = sessionmaker(bind=engine, future=True)()
    try:
        return stats_service.build_dashboard(session, TODAY).model_dump(mode="json", by_alias=True)
    finally:
        session.close()
        engine.dispose()


def test_b1_migration_adds_column_default_counted(tmp_path, monkeypatch) -> None:
    original_md5 = _md5(REAL_DB)
    db_path = _copy_real(tmp_path)
    _drop_count_column(db_path)
    assert "count_in_stats" not in _member_cols(db_path), "降级失败：副本仍有列"

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)
    app_main._run_additive_migrations()

    cols = _member_cols(db_path)
    assert "count_in_stats" in cols
    ddl = _member_ddl(db_path)
    assert "count_in_stats INTEGER NOT NULL DEFAULT 1" in ddl, ddl
    assert set(_flags(db_path)) == {1}, "存量成员应全部默认计入(1)"

    # 原库未被改动
    assert _md5(REAL_DB) == original_md5, "★ 原库被修改了！"
    engine.dispose()


def test_b2_dashboard_unchanged_after_migration(tmp_path, monkeypatch) -> None:
    """迁移后 dashboard 数字 == 「不过滤」基线（v1.4 行为）。"""
    db_path = _copy_real(tmp_path)
    _drop_count_column(db_path)

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)
    app_main._run_additive_migrations()
    engine.dispose()

    migrated = _dashboard(db_path)

    # 基线：把 excluded_holder_ids 打桩为空集（= v1.4 无过滤）
    monkeypatch.setattr(insured_service, "excluded_holder_ids", lambda members: set())
    baseline = _dashboard(db_path)
    monkeypatch.undo()

    assert migrated == baseline, "迁移后数字与「不过滤基线」不一致"
    # 顺带锁定真实库的关键数字（迁移不改变它们）
    assert migrated["overview"]["annualTotalPremium"] > 0
    # ⚠️ `policyTotal` 必须**从库本身读出**再比对，不能写死数字：
    #    真实库是用户的活数据，会随录入不断增长；写死会在每次新增保单后误报失败。
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        expected_total = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
    assert migrated["overview"]["policyTotal"] == expected_total


def test_b3_migration_idempotent(tmp_path, monkeypatch) -> None:
    db_path = _copy_real(tmp_path)
    _drop_count_column(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)

    app_main._run_additive_migrations()
    cols_1 = _member_cols(db_path)
    ddl_1 = _member_ddl(db_path)
    flags_1 = _flags(db_path)
    dash_1 = _dashboard(db_path)

    app_main._run_additive_migrations()
    assert _member_cols(db_path) == cols_1
    assert _member_ddl(db_path) == ddl_1
    assert _flags(db_path) == flags_1
    assert _dashboard(db_path) == dash_1
    engine.dispose()


def test_b4_real_data_cross_holder_filter(tmp_path, monkeypatch) -> None:
    """真实数据复验 A2：张三(投保人) 为 宋(被保人) 买的保单，排除张三后从宋行消失。"""
    db_path = _copy_real(tmp_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    _ensure_schema(engine)  # v1.7：建出 stats_exclusions（幂等）
    session = sessionmaker(bind=engine, future=True)()
    try:
        # 找出「投保人 != 被保人」的保单
        rows = session.execute(
            text(
                "SELECT p.id, ph.name AS holder, ins.name AS insured "
                "FROM policies p JOIN members ph ON ph.id=p.policyholder_id "
                "JOIN members ins ON ins.id=p.insured_id WHERE p.policyholder_id != p.insured_id"
            )
        ).all()
        assert rows, "真实数据中应存在跨投保人/被保人保单"
        holder_name = rows[0].holder
        insured_name = rows[0].insured

        before = stats_service.build_dashboard(session, TODAY).model_dump(mode="json", by_alias=True)
        ins0 = {r["name"]: r["annualPremium"] for r in before["byMember"]["byInsured"]}

        # 排除该投保人
        session.execute(
            text("UPDATE members SET count_in_stats=0 WHERE name=:n"), {"n": holder_name}
        )
        session.commit()
        after = stats_service.build_dashboard(session, TODAY).model_dump(mode="json", by_alias=True)
        ins1 = {r["name"]: r["annualPremium"] for r in after["byMember"]["byInsured"]}

        assert ins1[insured_name] < ins0[insured_name], (
            f"排除投保人 {holder_name} 后，被保人 {insured_name} 的年化未下降"
        )
        # 计数不变
        c0 = {r["name"]: r["policyCount"] for r in before["byMember"]["byInsured"]}
        c1 = {r["name"]: r["policyCount"] for r in after["byMember"]["byInsured"]}
        assert c0 == c1
    finally:
        session.close()
        engine.dispose()
