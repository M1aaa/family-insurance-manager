"""被保人关联（``policy_insureds``）业务逻辑 —— **被保人集合的唯一真源**（v1.3）。

职责：

- ``insured_sort_key``：被保人**规范排序**（唯一真源），同时决定
  「展示顺序、分摊顺序、尾差归属」；
- ``set_policy_insureds``：整体替换某保单的被保人集合（DELETE + INSERT 全部，含主被保人）；
- ``get_insured_ids``：读取某保单的全部被保人 id（原始插入序）；
- ``load_insured_map``：批量加载多张保单的被保人（每个列表按规范序排序），避免 N+1；
- ``count_policies_referencing_member``：成员被引用计数（**投保人 ∪ 被保人去重**），
  供删除成员保护使用；
- ``excluded_holder_ids``：**被排除出「年化保费」统计口径的投保人 id 集合**（v1.5），
  是 ``stats_service`` 与 ``member_service`` **共用的唯一真源**，避免两处口径分叉。

约定：关联表**含主被保人本身**；主被保人真源为 ``policies.insured_id``，
不变式为「主被保人必在关联表内」。
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..calc import enum_value
from ..enums import RELATION_ORDER
from ..models import Member, Policy, PolicyInsured, StatsExclusion

#: ``stats_exclusions.kind`` 的「险种」取值（v1.7 唯一写入值）。
INSURANCE_TYPE_KIND = "insurance_type"


def insured_sort_key(member: Member) -> tuple[int, str, str]:
    """被保人**规范排序键**（唯一真源）。

    排序规则：``(RELATION_ORDER[relation], name, id)``，即
    「本人 0 → 配偶 1 → 子女 2 → 父亲 3 → 母亲 4 → 其他 5，同关系按姓名、再按 id」。

    该键同时决定**展示顺序、分摊顺序、尾差归属**（三者必须一致，否则尾差归谁不稳定）。
    """
    return (
        RELATION_ORDER.get(enum_value(member.relation), 99),
        member.name,
        member.id,
    )


def dedupe_preserve_order(ids: list[str]) -> list[str]:
    """去重并保留首次出现的顺序。"""
    return list(dict.fromkeys(ids))


def set_policy_insureds(
    db: Session,
    policy_id: str,
    insured_ids: list[str],
    primary_id: str,
) -> None:
    """整体替换某保单的被保人集合（**不 commit**，由调用方提交事务）。

    语义：先删除该保单的全部关联行，再按「去重保序 + 保证主被保人在内」写入。
    主被保人 ``primary_id`` 会被**强制包含**（维持不变式：主被保人必在关联表内）。

    Args:
        db: 数据库会话。
        policy_id: 保单 id。
        insured_ids: 全部被保人 id（顺序即请求顺序，会被去重）。
        primary_id: 主被保人 id（必属于 ``insured_ids``）。
    """
    ordered = dedupe_preserve_order([mid for mid in insured_ids if mid])
    if primary_id and primary_id not in ordered:
        ordered.append(primary_id)

    db.execute(delete(PolicyInsured).where(PolicyInsured.policy_id == policy_id))
    for member_id in ordered:
        db.add(PolicyInsured(policy_id=policy_id, member_id=member_id))


def get_insured_ids(db: Session, policy_id: str) -> list[str]:
    """返回某保单的全部被保人 id（原始插入序）。"""
    return list(
        db.scalars(
            select(PolicyInsured.member_id).where(PolicyInsured.policy_id == policy_id)
        ).all()
    )


def load_insured_map(
    db: Session, policy_ids: list[str]
) -> dict[str, list[Member]]:
    """批量加载多张保单的被保人（每个列表按**规范序**排序）。

    单次 JOIN 查询避免 N+1；未命中的保单不出现在结果中（调用方按需回退）。

    Args:
        db: 数据库会话。
        policy_ids: 目标保单 id 列表。

    Returns:
        ``{policy_id: [Member, ...]}``，每个成员列表已按 ``insured_sort_key`` 升序。
    """
    ids = [pid for pid in policy_ids if pid]
    if not ids:
        return {}
    rows = db.execute(
        select(PolicyInsured.policy_id, Member)
        .join(Member, Member.id == PolicyInsured.member_id)
        .where(PolicyInsured.policy_id.in_(ids))
    ).all()
    grouped: dict[str, list[Member]] = defaultdict(list)
    for policy_id, member in rows:
        grouped[policy_id].append(member)
    for members in grouped.values():
        members.sort(key=insured_sort_key)
    return dict(grouped)


def excluded_holder_ids(members: dict[str, Member]) -> set[str]:
    """被排除出「年化保费」统计口径的**投保人** id 集合（``count_in_stats=False``，v1.5）。

    **唯一真源**：``stats_service``（看板 ``byInsured`` / ``byPolicyholder`` / ``byType`` /
    ``overview``）与 ``member_service``（成员列表 ``annualPremium`` / ``coverageTypes[].annualPremium``）
    共用本实现，确保「同一成员、同一数字」跨页面一致。

    过滤依据是**保单的投保人**（``policies.policyholder_id``）是否被排除——
    「张三给李四买的保单，若张三被排除，则该保单的年化保费不计入任何统计」。

    Args:
        members: ``{member_id: Member}`` 映射（调用方已加载的成员字典）。

    Returns:
        被排除的投保人 id 集合；全部计入时为空集。
    """
    return {member_id for member_id, member in members.items() if not member.count_in_stats}


def excluded_insurance_types(db: Session) -> set[str]:
    """被排除出「年化保费」统计口径的**险种**中文值集合（v1.7）。

    数据源：``stats_exclusions WHERE kind = 'insurance_type'``（``ref_id`` = 险种中文值）。

    **唯一真源**：``stats_service``（看板三维 + overview 年化口径）与 ``member_service``
    （成员 ``annualPremium`` / ``coverageTypes[].annualPremium``）共用本实现，
    确保「同一险种、同一数字」跨页面一致。仅作用于**年化保费**口径
    （计数 / ``upcoming60*`` / ``overdue[]`` / ``paidToDatePremium`` / ``yearUnpaidPremium`` 不受影响）。

    Args:
        db: 数据库会话。

    Returns:
        被排除的险种中文值集合；表为空（全部计入）时返回空集。
    """
    return set(
        db.scalars(
            select(StatsExclusion.ref_id).where(
                StatsExclusion.kind == INSURANCE_TYPE_KIND
            )
        ).all()
    )


def count_policies_referencing_member(db: Session, member_id: str) -> int:
    """统计引用某成员的**保单数**（投保人 ∪ 被保人，**按保单去重**）。

    用于删除成员保护：只要该成员是任一张保单的投保人或被保人（含关联表中），
    即返回非零计数，调用方据此拒绝删除（409 ``MEMBER_IN_USE``）。
    """
    insured_subquery = select(PolicyInsured.policy_id).where(
        PolicyInsured.member_id == member_id
    )
    stmt = select(func.count(func.distinct(Policy.id))).where(
        or_(Policy.policyholder_id == member_id, Policy.id.in_(insured_subquery))
    )
    return int(db.scalar(stmt) or 0)


__all__ = [
    "INSURANCE_TYPE_KIND",
    "insured_sort_key",
    "dedupe_preserve_order",
    "set_policy_insureds",
    "get_insured_ids",
    "load_insured_map",
    "count_policies_referencing_member",
    "excluded_holder_ids",
    "excluded_insurance_types",
]
