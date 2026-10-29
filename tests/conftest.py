"""pytest 公共夹具。

- ``db_session``：内存 SQLite 会话（每个测试独立）；
- ``client``：注入测试会话的 FastAPI ``TestClient``；
- ``member_factory`` / ``policy_factory``：构造示例数据的工厂夹具；
- ``seed_example_a`` / ``seed_example_b``：ARCHITECTURE 5.6 的两组验收算例数据。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  # 确保模型注册到 Base.metadata
from app.database import Base, get_db
from app.enums import (
    CoverageTermType,
    Gender,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)
from app.main import app
from app.models import Member, Policy, PolicyInsured


@pytest.fixture()
def db_session():
    """每个测试一个独立的内存 SQLite 会话。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = testing_session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session):
    """注入测试数据库会话的 TestClient（不触发 lifespan，避免污染文件库）。"""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def member_factory(db_session: Session):
    """成员工厂：``_make(**kwargs)`` 返回已加入会话的 Member。"""

    def _make(
        *,
        id: str,
        name: str,
        relation: Relation,
        birth_date: date,
        gender: Gender = Gender.unknown,
        remark: str | None = None,
    ) -> Member:
        member = Member(
            id=id,
            name=name,
            relation=relation,
            birth_date=birth_date,
            gender=gender,
            remark=remark,
        )
        db_session.add(member)
        db_session.flush()
        return member

    return _make


@pytest.fixture()
def policy_factory(db_session: Session):
    """保单工厂：``_make(**kwargs)`` 返回已加入会话的 Policy。

    v1.3：额外支持 ``insured_ids`` / ``primary_insured_id``，并把**全部被保人**
    （默认 = ``[insured_id]``）写入关联表 ``policy_insureds``（含主被保人），
    使统计层以关联表为单一真源时既有算例夹具仍能正确聚合。
    """

    def _make(
        *,
        id: str,
        insurance_type: InsuranceType,
        policyholder_id: str,
        insured_id: str,
        effective_date: date,
        coverage_term_type: CoverageTermType,
        coverage_term_value: int | None,
        payment_term_type: PaymentTermType,
        payment_term_value: int | None,
        payment_frequency: PaymentFrequency,
        premium_per_period: str | Decimal,
        status: PolicyStatus = PolicyStatus.active,
        company: str = "测试保险公司",
        product_name: str = "测试产品",
        insured_amount: str | Decimal | None = None,
        advisor_name: str | None = None,
        advisor_phone: str | None = None,
        advisor_wechat: str | None = None,
        advisor_email: str | None = None,
        company_phone: str | None = None,
        channel_name: str | None = None,
        channel_phone: str | None = None,
        insured_ids: list[str] | None = None,
        primary_insured_id: str | None = None,
    ) -> Policy:
        # 主被保人真源 = policies.insured_id；兼容：未显式指定时取 insured_id
        primary = primary_insured_id or insured_id
        policy = Policy(
            id=id,
            company=company,
            insurance_type=insurance_type,
            product_name=product_name,
            policyholder_id=policyholder_id,
            insured_id=primary,
            effective_date=effective_date,
            coverage_term_type=coverage_term_type,
            coverage_term_value=coverage_term_value,
            payment_term_type=payment_term_type,
            payment_term_value=payment_term_value,
            payment_frequency=payment_frequency,
            premium_per_period=Decimal(str(premium_per_period)),
            insured_amount=None if insured_amount is None else Decimal(str(insured_amount)),
            status=status,
            advisor_name=advisor_name,
            advisor_phone=advisor_phone,
            advisor_wechat=advisor_wechat,
            advisor_email=advisor_email,
            company_phone=company_phone,
            channel_name=channel_name,
            channel_phone=channel_phone,
        )
        db_session.add(policy)
        db_session.flush()

        # 写入关联表（含主被保人）；去重保序，保证主被保人在内
        all_insured = list(
            dict.fromkeys(insured_ids if insured_ids is not None else [insured_id])
        )
        if primary not in all_insured:
            all_insured.append(primary)
        for member_id in all_insured:
            db_session.add(PolicyInsured(policy_id=policy.id, member_id=member_id))
        db_session.flush()
        return policy

    return _make


@pytest.fixture()
def seed_example_a(db_session: Session, member_factory, policy_factory):
    """ARCHITECTURE 5.6 算例集 A 数据。"""
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m2", name="李四", relation=Relation.spouse, birth_date=date(1990, 3, 12), gender=Gender.female)
    member_factory(id="m3", name="小张", relation=Relation.child, birth_date=date(2015, 9, 20), gender=Gender.male)

    policy_factory(
        id="p_A", insurance_type=InsuranceType.critical_illness, policyholder_id="m1", insured_id="m1",
        effective_date=date(2020, 3, 15), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=20,
        payment_frequency=PaymentFrequency.monthly, premium_per_period="500", status=PolicyStatus.active,
        company="平安", product_name="平安福",
    )
    policy_factory(
        id="p_B", insurance_type=InsuranceType.medical, policyholder_id="m1", insured_id="m2",
        effective_date=date(2021, 8, 1), coverage_term_type=CoverageTermType.fixed_years, coverage_term_value=30,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=10,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="8000", status=PolicyStatus.active,
        company="人保", product_name="康健医疗",
    )
    policy_factory(
        id="p_C", insurance_type=InsuranceType.accident, policyholder_id="m1", insured_id="m3",
        effective_date=date(2024, 6, 1), coverage_term_type=CoverageTermType.fixed_years, coverage_term_value=1,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=20,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="3000", status=PolicyStatus.active,
        company="平安", product_name="综合意外",
    )
    policy_factory(
        id="p_D", insurance_type=InsuranceType.critical_illness, policyholder_id="m1", insured_id="m1",
        effective_date=date(2019, 1, 1), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.single, payment_term_value=None,
        payment_frequency=PaymentFrequency.monthly, premium_per_period="50000", status=PolicyStatus.paid_off,
        company="平安", product_name="终身重疾",
    )
    db_session.commit()


@pytest.fixture()
def seed_example_b(db_session: Session, member_factory, policy_factory):
    """ARCHITECTURE 5.6 算例集 B 数据。"""
    member_factory(id="m1", name="张三", relation=Relation.self_, birth_date=date(1988, 6, 1), gender=Gender.male)
    member_factory(id="m4", name="王五", relation=Relation.father, birth_date=date(1960, 2, 10), gender=Gender.male)

    policy_factory(
        id="p_E", insurance_type=InsuranceType.life, policyholder_id="m1", insured_id="m1",
        effective_date=date(2025, 8, 1), coverage_term_type=CoverageTermType.fixed_years, coverage_term_value=20,
        payment_term_type=PaymentTermType.fixed_years, payment_term_value=10,
        payment_frequency=PaymentFrequency.monthly, premium_per_period="200", status=PolicyStatus.active,
        company="国寿", product_name="定期寿险",
    )
    policy_factory(
        id="p_F", insurance_type=InsuranceType.annuity, policyholder_id="m4", insured_id="m4",
        effective_date=date(2015, 3, 1), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.to_age, payment_term_value=60,
        payment_frequency=PaymentFrequency.yearly, premium_per_period="12000", status=PolicyStatus.active,
        company="太保", product_name="养老年金",
    )
    policy_factory(
        id="p_G", insurance_type=InsuranceType.critical_illness, policyholder_id="m1", insured_id="m1",
        effective_date=date(2019, 1, 1), coverage_term_type=CoverageTermType.whole_life, coverage_term_value=None,
        payment_term_type=PaymentTermType.single, payment_term_value=None,
        payment_frequency=PaymentFrequency.monthly, premium_per_period="50000", status=PolicyStatus.active,
        company="平安", product_name="终身重疾",
    )
    db_session.commit()


# ---------------------------------------------------------------------------
# v1.2：附件相关夹具
# ---------------------------------------------------------------------------
@pytest.fixture()
def attachments_dir(tmp_path, monkeypatch):
    """把 ``settings.attachments_dir`` 指向临时目录。

    **绝不**把测试附件写进真实的 ``./data/attachments``。
    """
    from app.config import settings

    target = tmp_path / "attachments"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "attachments_dir", str(target), raising=False)
    return target


@pytest.fixture()
def small_attachment_size(monkeypatch):
    """把单文件大小上限调小（用于构造 413，无需真造 20MB 文件）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "max_attachment_size", 64, raising=False)
    return 64


@pytest.fixture()
def small_attachment_limit(monkeypatch):
    """把单保单附件数上限调小（用于构造 409）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "max_attachments_per_policy", 2, raising=False)
    return 2
