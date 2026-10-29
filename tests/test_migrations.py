"""v1.2 增量迁移测试。

覆盖：
1. 旧库（只有 v1.1 列）升级：``_run_additive_migrations()`` 补齐 7 个售后列；
2. 幂等：连跑两次结果一致、不报错；
3. 全新库：``create_all`` 建出 ``policy_attachments`` 表且列 / 唯一约束 / 索引齐全；
4. 既有数据不丢：升级前后保单行数与内容不变。
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text

import app.main as app_main
from app import models  # noqa: F401  # 确保 ORM 模型注册到 Base.metadata
from app.database import Base
from app.main import _ADDITIVE_COLUMNS, _run_additive_migrations

#: v1.2 新增的 7 个售后列（snake_case）
AFTER_SALES_COLUMNS = [
    "advisor_name",
    "advisor_phone",
    "advisor_wechat",
    "advisor_email",
    "company_phone",
    "channel_name",
    "channel_phone",
]

#: v1.1 的 ``policies`` 表结构（**不含** 7 个售后列）
_V11_POLICIES_DDL = """
CREATE TABLE policies (
    id                    VARCHAR(36) NOT NULL PRIMARY KEY,
    company               VARCHAR(50) NOT NULL,
    insurance_type        VARCHAR(8)  NOT NULL,
    product_name          VARCHAR(80) NOT NULL,
    policyholder_id       VARCHAR(36) NOT NULL,
    insured_id            VARCHAR(36) NOT NULL,
    effective_date        DATE        NOT NULL,
    coverage_term_type    VARCHAR(8)  NOT NULL,
    coverage_term_value   INTEGER,
    payment_term_type     VARCHAR(10) NOT NULL,
    payment_term_value    INTEGER,
    payment_frequency     VARCHAR(4)  NOT NULL,
    premium_per_period    INTEGER     NOT NULL,
    insured_amount        INTEGER,
    status                VARCHAR(8)  NOT NULL,
    remark                VARCHAR(200),
    paid_periods_override INTEGER,
    created_at            DATETIME    NOT NULL,
    updated_at            DATETIME    NOT NULL
)
"""

_INSERT_V11_ROW = """
INSERT INTO policies (
    id, company, insurance_type, product_name, policyholder_id, insured_id,
    effective_date, coverage_term_type, coverage_term_value, payment_term_type,
    payment_term_value, payment_frequency, premium_per_period, insured_amount,
    status, remark, paid_periods_override, created_at, updated_at
) VALUES (
    'p_old', '平安', '重疾险', '旧保单', 'm1', 'm1',
    '2020-01-01', '终身', NULL, '固定年限',
    20, '月缴', 50000, NULL,
    '在保', NULL, NULL, '2020-01-01 00:00:00', '2020-01-01 00:00:00'
)
"""


def _make_v11_db(db_path: str) -> None:
    """手工建一个只有 v1.1 列、含 1 行数据的 SQLite 库。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_V11_POLICIES_DDL)
        conn.execute(_INSERT_V11_ROW)
        conn.commit()
    finally:
        conn.close()


def test_additive_columns_registered() -> None:
    """7 个新列必须登记在 ``_ADDITIVE_COLUMNS``。"""
    registered = _ADDITIVE_COLUMNS["policies"]
    for column in AFTER_SALES_COLUMNS:
        assert column in registered, f"缺少登记：{column}"


def test_migration_upgrades_old_db_and_is_idempotent(tmp_path, monkeypatch) -> None:
    """旧库升级：补齐 7 列；连跑两次幂等；既有数据不变。"""
    db_path = str(tmp_path / "old.db")
    _make_v11_db(db_path)

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)

    # 升级前：确认这 7 列确实不存在
    before_cols = {c["name"] for c in inspect(engine).get_columns("policies")}
    for column in AFTER_SALES_COLUMNS:
        assert column not in before_cols

    # 第一次迁移：补齐 7 列
    _run_additive_migrations()
    after_cols = {c["name"] for c in inspect(engine).get_columns("policies")}
    for column in AFTER_SALES_COLUMNS:
        assert column in after_cols, f"迁移后仍缺列：{column}"

    # 既有数据不丢：行数与内容不变
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, company, paid_periods_override FROM policies")
        ).fetchone()
        count = conn.execute(text("SELECT COUNT(*) FROM policies")).scalar()
    assert count == 1
    assert row[0] == "p_old"
    assert row[1] == "平安"
    assert row[2] is None

    # 第二次迁移：幂等（不报错、列集合一致、数据仍不变）
    _run_additive_migrations()
    again_cols = {c["name"] for c in inspect(engine).get_columns("policies")}
    assert again_cols == after_cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM policies")).scalar() == 1

    engine.dispose()


def test_create_all_builds_attachment_table(tmp_path) -> None:
    """全新库：``policy_attachments`` 表 / 列 / 唯一约束 / 索引齐全；``policies`` 含 7 列。"""
    db_path = str(tmp_path / "new.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "policy_attachments" in inspector.get_table_names()

    cols = {c["name"] for c in inspector.get_columns("policy_attachments")}
    assert cols == {
        "id",
        "policy_id",
        "file_name",
        "stored_name",
        "file_size",
        "content_type",
        "uploaded_at",
    }

    policy_cols = {c["name"] for c in inspector.get_columns("policies")}
    for column in AFTER_SALES_COLUMNS:
        assert column in policy_cols

    with engine.connect() as conn:
        table_ddl = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='policy_attachments'"
            )
        ).scalar()
        index_names = (
            conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='policy_attachments'"
                )
            )
            .scalars()
            .all()
        )

    assert table_ddl is not None
    assert "uq_attachments_stored" in table_ddl
    assert "cascade" in table_ddl.lower()
    assert "idx_attachments_policy" in index_names

    engine.dispose()


def test_migration_skips_when_table_absent(tmp_path, monkeypatch) -> None:
    """目标表不存在时迁移应安全跳过（不报错）。"""
    db_path = str(tmp_path / "empty.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    # 建一张无关表，确保 policies 不存在
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE other (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(app_main, "engine", engine)

    _run_additive_migrations()  # 不应抛异常

    assert "policies" not in inspect(engine).get_table_names()
    engine.dispose()
