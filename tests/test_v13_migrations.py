"""v1.3 增量迁移测试。

覆盖：

1. ``create_all`` 建出 ``policy_insureds`` 表（复合主键 / 索引 / 两个外键 ondelete 正确）；
2. ``_backfill_policy_insureds()`` 对既有库把每张保单 ``insured_id`` 写入关联表；
3. **幂等**：连跑两次结果一致、不报错；
4. 既有数据不丢；
5. 目标表不存在时安全跳过（不报错、不阻塞启动）；
6. ``InsuranceType`` 含「分红险」共 10 值且顺序正确（插在「年金险」之后）。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

import app.main as app_main
from app import models  # noqa: F401  # 确保 ORM 模型注册到 Base.metadata
from app.database import Base
from app.enums import InsuranceType
from app.main import _backfill_policy_insureds
from app.models import Member, Policy

#: 关联表期望列
_INSURED_COLUMNS = {"policy_id", "member_id"}


def _make_v12_db(db_path: str):
    """手工建一个「只有 members + policies、无 policy_insureds」的库并插入数据。

    Returns:
        绑定该库的 engine（尚未创建 ``policy_insureds``）。
    """
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine, tables=[Member.__table__, Policy.__table__])
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO members "
                "(id, name, relation, birth_date, gender, created_at, updated_at) VALUES "
                "('m1', '张三', '本人', '1988-06-01', '男', "
                "'2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO members "
                "(id, name, relation, birth_date, gender, created_at, updated_at) VALUES "
                "('m2', '李四', '配偶', '1990-03-12', '女', "
                "'2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO policies "
                "(id, company, insurance_type, product_name, policyholder_id, insured_id, "
                " effective_date, coverage_term_type, coverage_term_value, payment_term_type, "
                " payment_term_value, payment_frequency, premium_per_period, insured_amount, "
                " status, remark, paid_periods_override, created_at, updated_at) VALUES "
                "('p_old', '平安', '重疾险', '旧保单', 'm1', 'm1', "
                " '2020-01-01', '终身', NULL, '固定年限', "
                " 20, '月缴', 50000, NULL, "
                " '生效中', NULL, NULL, '2020-01-01 00:00:00', '2020-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO policies "
                "(id, company, insurance_type, product_name, policyholder_id, insured_id, "
                " effective_date, coverage_term_type, coverage_term_value, payment_term_type, "
                " payment_term_value, payment_frequency, premium_per_period, insured_amount, "
                " status, remark, paid_periods_override, created_at, updated_at) VALUES "
                "('p_old2', '人保', '医疗险', '旧保单2', 'm1', 'm2', "
                " '2021-08-01', '固定年限', 30, '固定年限', "
                " 10, '年缴', 8000, NULL, "
                " '生效中', NULL, NULL, '2021-08-01 00:00:00', '2021-08-01 00:00:00')"
            )
        )
    return engine


def test_insurance_type_includes_dividend() -> None:
    """``InsuranceType`` 含「分红险」共 10 值，且插在「年金险」之后。"""
    values = [member.value for member in InsuranceType]
    assert values == [
        "重疾险",
        "医疗险",
        "意外险",
        "寿险",
        "年金险",
        "分红险",
        "教育金",
        "车险",
        "财产险",
        "其他",
    ]
    assert len(values) == 10
    assert values.index("分红险") == values.index("年金险") + 1


def test_create_all_builds_policy_insureds_table(tmp_path) -> None:
    """全新库：``policy_insureds`` 表 / 列 / 复合主键 / 索引 / 外键 ondelete 齐全。"""
    db_path = str(tmp_path / "new.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    assert "policy_insureds" in inspector.get_table_names()

    cols = {c["name"] for c in inspector.get_columns("policy_insureds")}
    assert cols == _INSURED_COLUMNS

    # 复合主键 (policy_id, member_id)
    pk = inspector.get_pk_constraint("policy_insureds")
    assert set(pk["constrained_columns"]) == _INSURED_COLUMNS

    # 索引 idx_policy_insureds_member（建在 member_id 上）
    index_names = {idx["name"] for idx in inspector.get_indexes("policy_insureds")}
    assert "idx_policy_insureds_member" in index_names

    # 两个外键的 ondelete：policy 侧 CASCADE、member 侧 RESTRICT
    fks = inspector.get_foreign_keys("policy_insureds")
    by_table = {fk["referred_table"]: fk for fk in fks}
    assert set(by_table) == {"policies", "members"}
    assert (by_table["policies"].get("options") or {}).get("ondelete", "").upper() == "CASCADE"
    assert (by_table["members"].get("options") or {}).get("ondelete", "").upper() == "RESTRICT"

    # 底层 DDL 也确认 cascade / restrict 存在
    with engine.connect() as conn:
        ddl = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='policy_insureds'"
            )
        ).scalar()
    assert ddl is not None
    assert "cascade" in ddl.lower()
    assert "restrict" in ddl.lower()

    engine.dispose()


def test_backfill_is_idempotent_and_preserves_data(tmp_path, monkeypatch) -> None:
    """旧库升级：回填关联表；连跑两次幂等；既有数据不丢。"""
    db_path = str(tmp_path / "old.db")
    engine = _make_v12_db(db_path)

    # 升级前：policy_insureds 不存在
    assert "policy_insureds" not in inspect(engine).get_table_names()

    # 模拟启动：create_all 建新表
    Base.metadata.create_all(engine)
    assert "policy_insureds" in inspect(engine).get_table_names()

    monkeypatch.setattr(app_main, "engine", engine)

    # 第一次回填：每张保单的 insured_id 写入关联表
    _backfill_policy_insureds()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT policy_id, member_id FROM policy_insureds ORDER BY policy_id")
        ).all()
    assert rows == [("p_old", "m1"), ("p_old2", "m2")]
    with engine.connect() as conn:
        count_after_first = conn.execute(
            text("SELECT COUNT(*) FROM policy_insureds")
        ).scalar()
    assert count_after_first == 2

    # 既有数据不丢
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM policies")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM members")).scalar() == 2

    # 第二次回填：幂等（不报错、行集合一致）
    _backfill_policy_insureds()
    with engine.connect() as conn:
        again = conn.execute(
            text("SELECT policy_id, member_id FROM policy_insureds ORDER BY policy_id")
        ).all()
        count_after_second = conn.execute(
            text("SELECT COUNT(*) FROM policy_insureds")
        ).scalar()
    assert again == rows
    assert count_after_second == 2

    engine.dispose()


def test_backfill_skips_when_table_absent(tmp_path, monkeypatch) -> None:
    """``policy_insureds`` 表不存在时回填应安全跳过（不报错）。"""
    db_path = str(tmp_path / "empty.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE other (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(app_main, "engine", engine)

    _backfill_policy_insureds()  # 不应抛异常

    assert "policy_insureds" not in inspect(engine).get_table_names()
    engine.dispose()


def test_backfill_does_not_block_startup_on_error(tmp_path, monkeypatch) -> None:
    """回填内部异常必须被吞掉（仅 WARNING，不阻塞启动）。"""

    class _BoomEngine:
        def begin(self):  # noqa: ANN201
            raise RuntimeError("boom")

    monkeypatch.setattr(app_main, "engine", _BoomEngine())
    # 即使 engine.begin() 抛错，也不应向上抛出
    _backfill_policy_insureds()


@pytest.mark.parametrize("n", [1, 2, 3, 7, 100])
def test_allocate_amounts_invariant(n) -> None:
    """分摊不变式：Σ shares == money(total)（覆盖多个份数）。"""
    from decimal import Decimal

    from app.calc import allocate_amounts, money

    total = Decimal("12345.67")
    shares = allocate_amounts(total, n)
    assert len(shares) == n
    assert sum(shares) == money(total)
