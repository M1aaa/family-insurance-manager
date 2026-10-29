"""成员 CRUD 业务逻辑。

规则：
- 「本人」全局唯一（应用层校验，冲突返回 409 ``DUPLICATE_PRIMARY_MEMBER``）；
- 被保单引用的成员禁止删除（返回 409 ``MEMBER_IN_USE``，消息含关联保单数 N）；
- 列表按 ``relation`` 权重排序（本人 0 → 其他 5），并附带派生字段
  ``policyCount`` / ``activePolicyCount`` / ``annualPremium``（口径同 5.1，按**被保人**维度）；
- **v1.5**：``annualPremium`` / ``coverageTypes[].annualPremium`` 按**保单投保人**是否
  ``count_in_stats`` 过滤（与 ``stats_service.byInsured`` **同源**，跨页面数字一致）；
  计数 / 累计已缴不受影响。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from ..calc import allocate_amounts, calc_schedule, enum_value, money
from ..enums import RELATION_ORDER, InsuranceType, Relation
from ..errors import AppError
from ..models import Member, Policy
from ..schemas import CoverageTypeStatOut, MemberCreate, MemberOut, MemberUpdate
from . import insured_service

STATUS_ACTIVE = "生效中"
# 「已有保障类型」计入的保单状态：生效中 / 已缴清（已退保 / 已失效不计入）。
INSURED_STATUSES = ("生效中", "已缴清")
#: 险种枚举顺序（用于 coverageTypes 同张数时的次级排序）
_INSURANCE_ORDER: dict[str, int] = {
    member.value: index for index, member in enumerate(InsuranceType)
}


def _empty_stat() -> dict[str, object]:
    """一份空的成员统计行（每次调用返回**新对象**，避免共享可变状态）。"""
    return {"count": 0, "active": 0, "annual": Decimal("0.00"), "coverage": {}}


def _relation_weight(relation) -> int:  # noqa: ANN001
    """成员关系排序权重。"""
    return RELATION_ORDER.get(enum_value(relation), 99)


def _member_stats(db: Session, today: date) -> dict[str, dict[str, object]]:
    """计算每位成员（**按被保人维度**）的保单数 / 生效中数 / 分摊年化保费 + 已有保障类型。

    口径（v1.3）：

    - ``count`` / ``active`` / ``annual``：**与 v1.2 同口径**，但 ``annual`` 为**分摊后**
      （单被保人时系数 1，逐值不变）；``count`` 覆盖**全部状态**保单；
    - ``coverage``：按 ``(成员, 险种)`` 聚合，**仅统计状态 ∈ {生效中, 已缴清}** 的保单，
      ``annual`` 仅计入「生效中且未缴清」的分摊年化。

    v1.5：``annual``（成员级 + 险种级）额外剔除**投保人被排除**（``count_in_stats=False``）
    的保单，与 ``stats_service._group_by_insured`` 逐值同源；``count`` / ``active`` 不变。
    """
    members = {m.id: m for m in db.query(Member).all()}
    policies = list(db.query(Policy).all())
    insured_map = insured_service.load_insured_map(db, [p.id for p in policies])
    # v1.5：被排除的投保人集合（与 stats_service 共用同一实现 → 跨页面口径一致）。
    # v1.7：叠加「按险种」排除集合（并集），共同作用于「年化保费」口径。
    # 仅作用于「年化保费」口径；计数 / 累计已缴**不受影响**。
    excluded_holders = insured_service.excluded_holder_ids(members)
    excluded_types = insured_service.excluded_insurance_types(db)

    agg: dict[str, dict[str, object]] = defaultdict(_empty_stat)
    for policy in policies:
        insureds = insured_map.get(policy.id)
        if not insureds:
            primary = members.get(policy.insured_id)
            insureds = [primary] if primary is not None else []
        if not insureds:
            continue
        primary = next((m for m in insureds if m.id == policy.insured_id), insureds[0])
        sched = calc_schedule(policy, primary, today)
        status = enum_value(policy.status)
        n = len(insureds)

        # v1.5：按**保单投保人**是否被排除来剔除年化保费（与 dashboard.byInsured 同源）；
        # 计数 / 累计已缴不参与过滤（累计已缴属「实际已发生」）。
        # v1.6：年化保费口径**放宽为与主卡片「当年应缴总保费」一致**
        #       （`status ∈ {生效中, 已缴清}`，不再要求「未缴清」），
        #       否则 1 年期已缴完的保单会在成员页/分布图里显示 0，与主卡片对不上账。
        annual_contrib = (
            sched.annualized_premium
            if (
                status in INSURED_STATUSES
                and policy.policyholder_id not in excluded_holders
                and enum_value(policy.insurance_type) not in excluded_types
            )
            else Decimal("0.00")
        )
        annual_shares = allocate_amounts(annual_contrib, n)
        in_coverage_status = status in INSURED_STATUSES
        insurance_type = enum_value(policy.insurance_type)

        for index, member in enumerate(insureds):
            row = agg[member.id]
            row["count"] = int(row["count"]) + 1  # type: ignore[arg-type]
            if status == STATUS_ACTIVE:
                row["active"] = int(row["active"]) + 1  # type: ignore[arg-type]
            row["annual"] = row["annual"] + annual_shares[index]  # type: ignore[operator]
            if in_coverage_status:
                coverage = row["coverage"]
                assert isinstance(coverage, dict)
                cell = coverage.setdefault(
                    insurance_type, {"count": 0, "active": 0, "annual": Decimal("0.00")}
                )
                cell["count"] = int(cell["count"]) + 1
                if status == STATUS_ACTIVE:
                    cell["active"] = int(cell["active"]) + 1
                cell["annual"] = cell["annual"] + annual_shares[index]
    return agg


def _to_out(member: Member, stats: dict[str, dict[str, object]]) -> MemberOut:
    """ORM 成员 + 派生字段 → ``MemberOut``（含 ``coverageTypes``）。"""
    row = stats.get(member.id) or _empty_stat()
    coverage = row.get("coverage") or {}
    assert isinstance(coverage, dict)
    coverage_types = [
        CoverageTypeStatOut(
            insurance_type=InsuranceType(insurance_type),
            count=int(cell["count"]),
            active_count=int(cell["active"]),
            annual_premium=money(cell["annual"]),
        )
        for insurance_type, cell in coverage.items()
    ]
    # 排序：张数降序 → 险种枚举顺序
    coverage_types.sort(
        key=lambda stat: (
            -stat.count,
            _INSURANCE_ORDER.get(enum_value(stat.insurance_type), 99),
        )
    )
    return MemberOut(
        id=member.id,
        name=member.name,
        relation=member.relation,
        birth_date=member.birth_date,
        gender=member.gender,
        remark=member.remark,
        count_in_stats=member.count_in_stats,
        created_at=member.created_at,
        updated_at=member.updated_at,
        policy_count=int(row["count"]),  # type: ignore[arg-type]
        active_policy_count=int(row["active"]),  # type: ignore[arg-type]
        annual_premium=money(row["annual"]),  # type: ignore[arg-type]
        coverage_types=coverage_types,
    )


def get_member_or_404(db: Session, member_id: str) -> Member:
    """按 id 获取成员，不存在则抛 404。"""
    member = db.get(Member, member_id)
    if member is None:
        raise AppError(
            404, "NOT_FOUND", f"成员不存在：{member_id}", {"memberId": member_id}
        )
    return member


def _ensure_single_primary(db: Session, relation, exclude_id: str | None = None) -> None:  # noqa: ANN001
    """校验「本人」唯一性。"""
    if enum_value(relation) != Relation.self_.value:
        return
    query = db.query(Member).filter(Member.relation == Relation.self_)
    if exclude_id is not None:
        query = query.filter(Member.id != exclude_id)
    existing = query.first()
    if existing is not None:
        raise AppError(
            409,
            "DUPLICATE_PRIMARY_MEMBER",
            "已存在「本人」成员，家庭成员中只能有一个本人",
            {"existingMemberId": existing.id},
        )


def list_members(db: Session) -> list[MemberOut]:
    """返回全部成员（含派生字段），按关系权重排序。"""
    today = date.today()
    stats = _member_stats(db, today)
    members = db.query(Member).all()
    members.sort(key=lambda m: (_relation_weight(m.relation), m.name))
    return [_to_out(m, stats) for m in members]


def create_member(db: Session, data: MemberCreate) -> MemberOut:
    """新增成员。"""
    _ensure_single_primary(db, data.relation)
    member = Member(
        name=data.name,
        relation=data.relation,
        birth_date=data.birth_date,
        gender=data.gender,
        remark=data.remark,
        count_in_stats=data.count_in_stats,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return _to_out(member, _member_stats(db, date.today()))


def update_member(db: Session, member_id: str, data: MemberUpdate) -> MemberOut:
    """编辑成员。"""
    member = get_member_or_404(db, member_id)
    _ensure_single_primary(db, data.relation, exclude_id=member_id)
    member.name = data.name
    member.relation = data.relation
    member.birth_date = data.birth_date
    member.gender = data.gender
    member.remark = data.remark
    member.count_in_stats = data.count_in_stats
    db.commit()
    db.refresh(member)
    return _to_out(member, _member_stats(db, date.today()))


def delete_member(db: Session, member_id: str) -> dict[str, object]:
    """删除成员；被保单引用（**投保人 ∪ 被保人，去重计数**）时抛 409。"""
    member = get_member_or_404(db, member_id)
    count = insured_service.count_policies_referencing_member(db, member_id)
    if count > 0:
        raise AppError(
            409,
            "MEMBER_IN_USE",
            f"该成员关联 {count} 份保单，无法删除",
            {"memberId": member_id, "policyCount": count},
        )
    db.delete(member)
    db.commit()
    return {"deleted": True, "id": member_id}


__all__ = [
    "list_members",
    "create_member",
    "update_member",
    "delete_member",
    "get_member_or_404",
]
