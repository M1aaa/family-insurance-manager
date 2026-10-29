"""QA 独立验证 · v1.2 增量迁移（第 3 组）。

覆盖：
1. 真实 v1.1 结构库（``policies`` 无 7 列、无 ``policy_attachments`` 表）升级：
   7 列补齐、新表建好、**原有数据逐行一字不改**；
2. 幂等：连跑两次不报错、结果一致；
3. 失败不阻塞启动：模拟 ``ALTER TABLE`` 失败（inspector 谎报列缺失）→ 不抛异常；
4. 全新库 ``create_all`` 建出 ``policy_attachments``（列 / 唯一约束 / 索引 / CASCADE）。

本文件独立于工程师的 ``test_migrations.py``，构造的旧库**含成员与多张保单全字段数据**。
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, inspect, text

import app.main as app_main
from app import models  # noqa: F401  # 注册 ORM 模型
from app.database import Base
from app.main import _ADDITIVE_COLUMNS, _run_additive_migrations

AFTER_SALES_COLUMNS = [
    "advisor_name",
    "advisor_phone",
    "advisor_wechat",
    "advisor_email",
    "company_phone",
    "channel_name",
    "channel_phone",
]

#: v1.1 结构（无 7 售后列；无 policy_attachments 表）
_V11_MEMBERS_DDL = """
CREATE TABLE members (
    id         VARCHAR(36) NOT NULL PRIMARY KEY,
    name       VARCHAR(20) NOT NULL,
    relation   VARCHAR(4)  NOT NULL,
    birth_date DATE        NOT NULL,
    gender     VARCHAR(4)  NOT NULL,
    remark     VARCHAR(100),
    created_at DATETIME    NOT NULL,
    updated_at DATETIME    NOT NULL
)
"""

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

_MEMBERS_ROWS = [
    ("m1", "张三", "本人", "1988-06-01", "男", None, "2020-01-01 00:00:00", "2020-01-01 00:00:00"),
    ("m2", "李四", "配偶", "1990-03-12", "女", "备注甲", "2020-01-02 00:00:00", "2020-01-02 00:00:00"),
]

_POLICIES_ROWS = [
    ("p1", "平安", "重疾险", "平安福", "m1", "m1", "2020-01-01", "终身", None, "固定年限",
     20, "月缴", 5000000, 30000000, "生效中", "旧备注", None, "2020-01-01 00:00:00", "2020-01-01 00:00:00"),
    ("p2", "人保", "医疗险", "康健医疗", "m1", "m2", "2021-08-01", "固定年限", 30, "固定年限",
     10, "年缴", 800000, 20000000, "生效中", None, 5, "2021-08-01 00:00:00", "2021-08-01 00:00:00"),
    ("p3", "太保", "年金险", "养老年金", "m2", "m2", "2019-01-01", "终身", None, "交至某年龄",
     60, "年缴", 1200000, None, "已缴清", "尾注", 12, "2019-01-01 00:00:00", "2019-01-01 00:00:00"),
]


def _build_v11_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_V11_MEMBERS_DDL)
        conn.execute(_V11_POLICIES_DDL)
        conn.executemany(
            "INSERT INTO members VALUES (?,?,?,?,?,?,?,?)", _MEMBERS_ROWS
        )
        conn.executemany(
            "INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _POLICIES_ROWS
        )
        conn.commit()
    finally:
        conn.close()


#: v1.1 ``policies`` 的原始列（迁移前后应逐行一致；不含新增的 7 售后列）
_V11_POLICY_COLS = [
    "id", "company", "insurance_type", "product_name", "policyholder_id", "insured_id",
    "effective_date", "coverage_term_type", "coverage_term_value", "payment_term_type",
    "payment_term_value", "payment_frequency", "premium_per_period", "insured_amount",
    "status", "remark", "paid_periods_override", "created_at", "updated_at",
]
_MEMBER_COLS = ["id", "name", "relation", "birth_date", "gender", "remark", "created_at", "updated_at"]


def _dump(engine, table: str, cols: list[str]) -> list[tuple]:
    with engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(text(f"SELECT {', '.join(cols)} FROM {table}")).all()
        ]


def test_migration_real_v11_db_preserves_all_data(tmp_path, monkeypatch):
    """真实 v1.1 库升级：7 列补齐、新表建好、成员与保单数据逐行不变。"""
    db_path = str(tmp_path / "v11.db")
    _build_v11_db(db_path)

    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)

    before_cols = {c["name"] for c in inspect(engine).get_columns("policies")}
    for col in AFTER_SALES_COLUMNS:
        assert col not in before_cols, f"构造的 v1.1 库不应含列 {col}"
    assert "policy_attachments" not in inspect(engine).get_table_names()

    members_before = _dump(engine, "members", _MEMBER_COLS)
    policies_before = _dump(engine, "policies", _V11_POLICY_COLS)

    # 第一次迁移
    _run_additive_migrations()

    after_cols = {c["name"] for c in inspect(engine).get_columns("policies")}
    for col in AFTER_SALES_COLUMNS:
        assert col in after_cols, f"迁移后仍缺列 {col}"

    # 新表由 create_all 建（模拟启动顺序：create_all 在迁移之前）
    Base.metadata.create_all(engine)
    assert "policy_attachments" in inspect(engine).get_table_names()

    # 原有数据逐行一字不改
    assert _dump(engine, "members", _MEMBER_COLS) == members_before, "迁移改动了 members 数据"
    assert _dump(engine, "policies", _V11_POLICY_COLS) == policies_before, "迁移改动了 policies 数据"

    # 新列在旧行上应为 NULL
    with engine.connect() as conn:
        for col in AFTER_SALES_COLUMNS:
            nulls = conn.execute(
                text(f"SELECT COUNT(*) FROM policies WHERE {col} IS NOT NULL")
            ).scalar()
            assert nulls == 0, f"旧行 {col} 应为 NULL"

    engine.dispose()


def test_migration_idempotent_real_db(tmp_path, monkeypatch):
    """连跑两次迁移：不报错、列集合一致、数据不变。"""
    db_path = str(tmp_path / "v11_idem.db")
    _build_v11_db(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    monkeypatch.setattr(app_main, "engine", engine)

    _run_additive_migrations()
    cols_1 = {c["name"] for c in inspect(engine).get_columns("policies")}
    data_1 = _dump(engine, "policies", _V11_POLICY_COLS)

    _run_additive_migrations()
    cols_2 = {c["name"] for c in inspect(engine).get_columns("policies")}
    data_2 = _dump(engine, "policies", _V11_POLICY_COLS)

    assert cols_1 == cols_2
    assert data_1 == data_2
    engine.dispose()


def test_migration_failure_does_not_raise(tmp_path, monkeypatch):
    """模拟 ``ALTER TABLE`` 失败（inspector 谎报列缺失）→ 迁移不抛异常、不阻塞启动。"""
    db_path = str(tmp_path / "v12_like.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)  # 已含全部列（含 7 售后列）
    monkeypatch.setattr(app_main, "engine", engine)

    class _LyingInspector:
        """谎报：policies 只有基列，7 售后列与 paid_periods_override 都「不存在」。"""

        def get_table_names(self):
            return ["policies"]

        def get_columns(self, table):  # noqa: ARG002
            return [
                {"name": "id"},
                {"name": "company"},
                {"name": "product_name"},
            ]

    monkeypatch.setattr(app_main, "inspect", lambda eng: _LyingInspector())

    # 不应抛异常（每个失败的 ALTER 都被 try/except 吞掉并记 WARNING）
    result = _run_additive_migrations()
    assert result is None

    # 列仍在（未被破坏）
    cols = {c["name"] for c in inspect(engine).get_columns("policies")}
    for col in AFTER_SALES_COLUMNS:
        assert col in cols
    engine.dispose()


def test_migration_skips_absent_table(tmp_path, monkeypatch):
    """目标表不存在时安全跳过。"""
    db_path = str(tmp_path / "no_policies.db")
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE other (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(app_main, "engine", engine)

    _run_additive_migrations()  # 不应抛异常
    assert "policies" not in inspect(engine).get_table_names()
    engine.dispose()


def test_additive_columns_registered_exactly():
    """7 个售后列必须全部登记（且 paid_periods_override 仍在）。"""
    registered = _ADDITIVE_COLUMNS["policies"]
    for col in AFTER_SALES_COLUMNS:
        assert col in registered, f"缺少登记：{col}"
    assert "paid_periods_override" in registered


def test_new_db_attachment_table_full_shape(tmp_path):
    """全新库：policy_attachments 列 / 唯一约束 / 索引 / FK CASCADE 齐全。"""
    db_path = str(tmp_path / "fresh.db")
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

    with engine.connect() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='policy_attachments'")
        ).scalar()
        idx = (
            conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='policy_attachments'"
                )
            )
            .scalars()
            .all()
        )

    assert "uq_attachments_stored" in ddl
    assert "cascade" in ddl.lower()
    assert "idx_attachments_policy" in idx

    # 外键 CASCADE 真实生效（PRAGMA foreign_keys=ON 时）
    from sqlalchemy import event as sa_event
    from sqlalchemy.engine import Engine

    @sa_event.listens_for(Engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO members (id,name,relation,birth_date,gender,remark,created_at,updated_at)"
                " VALUES ('m1','A','本人','1990-01-01','男',NULL,'2020-01-01','2020-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO policies (id,company,insurance_type,product_name,policyholder_id,"
                "insured_id,effective_date,coverage_term_type,coverage_term_value,payment_term_type,"
                "payment_term_value,payment_frequency,premium_per_period,insured_amount,status,remark,"
                "paid_periods_override,created_at,updated_at) VALUES "
                "('p1','X','医疗险','Y','m1','m1','2024-01-01','固定年限',30,'固定年限',10,'年缴',1000,"
                "NULL,'生效中',NULL,NULL,'2024-01-01','2024-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO policy_attachments (id,policy_id,file_name,stored_name,file_size,"
                "content_type,uploaded_at) VALUES ('a1','p1','f.pdf','s.pdf',10,'application/pdf',"
                "'2024-01-01')"
            )
        )
        conn.execute(text("DELETE FROM policies WHERE id='p1'"))
        left = conn.execute(text("SELECT COUNT(*) FROM policy_attachments")).scalar()
    assert left == 0, "FK CASCADE 未生效"

    engine.dispose()
