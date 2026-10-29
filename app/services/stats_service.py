"""统计聚合业务逻辑（``GET /api/stats/dashboard``）。

按 ARCHITECTURE 5.5 聚合，并叠加 **v1.3 多被保人分摊**：

- ``overview``：年度总保费 / 近 60 天待交保费与笔数 / 保单总数 / 生效中数 / 在保人数
  / 存在逾期未缴的保单数；
- ``byMember``：双维度（按被保人 / 按投保人），按关系权重排序；
  **仅 ``byInsured`` 的保费类指标（年化 / 待缴 / 累计已缴）按人头分摊**，
  ``policyCount`` / ``activePolicyCount`` **全额**（一人被 N 张保单保障即计 N 张）；
  ``byPolicyholder`` **不分摊**、按保单去重；
- ``byType``：按险种，按年化保费降序（**不分摊**）；
- ``upcoming``：近 60 天待缴清单，按 ``(dueDate, policyId)`` 升序；
- ``overdue``：逾期未缴清单，按 ``(earliestDueDate, policyId)`` 升序。

分摊算法：``calc.allocate_amounts``（``ROUND_HALF_UP`` + 2 位小数，尾差归**规范排序**
最后一名被保人）；``Σ shares == money(total)`` 恒成立。规范排序由
``insured_service.insured_sort_key`` 决定（展示顺序、分摊顺序、尾差归属三者一致）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..calc import (
    Schedule,
    allocate_amounts,
    calc_schedule,
    enumerate_upcoming,
    enumerate_year_periods,
    enum_value,
    money,
)
from ..enums import RELATION_ORDER
from ..errors import AppError
from ..models import Member, Policy, StatsExclusion
from ..schemas import (
    ByMemberOut,
    DashboardOut,
    MemberStatOut,
    OverdueItemOut,
    OverviewOut,
    StatsExclusionsIn,
    StatsExclusionsOut,
    TypeStatOut,
    UpcomingItemOut,
)
from . import insured_service

STATUS_ACTIVE = "生效中"
# 「在保人数」计入的保单状态：生效中 / 已缴清（已退保、已失效不计入）。
INSURED_STATUSES = ("生效中", "已缴清")
WINDOW_DAYS = 60
ZERO = Decimal("0.00")


def _relation_weight(relation) -> int:  # noqa: ANN001
    """成员关系排序权重。"""
    return RELATION_ORDER.get(enum_value(relation), 99)


def _primary_of(
    policy: Policy,
    insured_map: dict[str, list[Member]],
    members: dict[str, Member],
) -> Member | None:
    """返回保单的**主被保人**（真源 ``policies.insured_id``），缺失时降级取被保人首位。"""
    insureds = insured_map.get(policy.id)
    if insureds:
        for member in insureds:
            if member.id == policy.insured_id:
                return member
        return insureds[0]
    return members.get(policy.insured_id)


def _insured_members(
    policy: Policy,
    insured_map: dict[str, list[Member]],
    members: dict[str, Member],
) -> list[Member]:
    """返回保单的全部被保人（规范序）；缺失时降级为「仅主被保人」。"""
    insureds = insured_map.get(policy.id)
    if insureds:
        return insureds
    primary = members.get(policy.insured_id)
    return [primary] if primary is not None else []


def _is_excluded(
    policy: Policy,
    excluded_holders: set[str],
    excluded_types: set[str],
) -> bool:
    """该保单的**年化保费**是否被排除（投保人 ∪ 险种，任一命中即排除，v1.7）。

    **仅**作用于「年化保费」口径；计数 / ``upcoming60*`` / ``overdue[]`` /
    ``paidToDatePremium`` / ``yearUnpaidPremium`` 一律不过滤。
    """
    return (
        policy.policyholder_id in excluded_holders
        or enum_value(policy.insurance_type) in excluded_types
    )


def build_dashboard(db: Session, today: date) -> DashboardOut:
    """构建看板全部数据。"""
    members: dict[str, Member] = {m.id: m for m in db.query(Member).all()}
    policies: list[Policy] = list(db.query(Policy).all())
    insured_map = insured_service.load_insured_map(db, [p.id for p in policies])
    # v1.5：被排除的投保人集合，仅作用于「年化保费」口径（计数 / 待缴 / 已缴均不受影响）。
    # v1.7：叠加「按险种」排除集合，二者**并集**共同作用于年化保费口径。
    # 与 member_service 共用 insured_service —— 跨页面口径唯一真源。
    excluded_holders = insured_service.excluded_holder_ids(members)
    excluded_types = insured_service.excluded_insurance_types(db)

    # 逐单派生计算（以**主被保人**为基准，复用同一份 Schedule，保证各维度口径同源）
    schedules: dict[str, Schedule] = {}
    for policy in policies:
        primary = _primary_of(policy, insured_map, members)
        if primary is not None:
            schedules[policy.id] = calc_schedule(policy, primary, today)

    # ---- 近 60 天待缴期次（按保单汇总，供分摊 / 政策维度复用） ----
    upcoming_pairs: list[tuple[Policy, object]] = []
    upcoming_by_policy: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for policy in policies:
        primary = _primary_of(policy, insured_map, members)
        if primary is None:
            continue
        for item in enumerate_upcoming(policy, primary, today, WINDOW_DAYS):
            upcoming_pairs.append((policy, item))
            upcoming_by_policy[policy.id] = upcoming_by_policy[policy.id] + item.amount  # type: ignore[attr-defined]

    upcoming_premium = sum(
        (item.amount for _, item in upcoming_pairs), ZERO  # type: ignore[attr-defined]
    )

    # ---- overview ----
    # 一次遍历同时累加两个口径（v1.4）：
    #   annual_total   = 当年应缴总保费：status ∈ {生效中, 已缴清} 的年化合计（含已缴清、含已缴完的生效中）
    #   unpaid_annual  = 当年未缴费金额：status == 生效中 且未缴清的年化合计（= v1.3 旧「年度总保费」，逐值不变）
    annual_total = ZERO
    unpaid_annual = ZERO
    for policy in policies:
        sched = schedules.get(policy.id)
        if sched is None:
            continue
        # v1.5 / v1.7：投保人或险种被排除 → 该保单年化保费不计入两个 overview 保费口径
        # （计数不受影响）。
        counted = not _is_excluded(policy, excluded_holders, excluded_types)
        status = enum_value(policy.status)
        if counted and status in INSURED_STATUSES:
            annual_total += sched.annualized_premium
        if counted and status == STATUS_ACTIVE and not sched.is_paid_off:
            unpaid_annual += sched.annualized_premium

    # ---- v1.7：当年未缴费金额（现金流口径，**不受排除筛选影响**） ----
    # 逐单枚举「缴费日落在本年度且未标记已缴」的期次，累加每期实缴金额
    # （非年化）；含已逾期、含本年度未来期次；已退保 / 已失效天然归零。
    year_unpaid = ZERO
    for policy in policies:
        primary = _primary_of(policy, insured_map, members)
        if primary is None:
            continue
        for item in enumerate_year_periods(policy, primary, today):
            year_unpaid += item.amount

    active_policy_count = sum(1 for p in policies if enum_value(p.status) == STATUS_ACTIVE)
    # 在保人数：关联表 member_id **去重**，且保障期内、保单状态 ∈ {生效中, 已缴清}。
    insured_person_ids: set[str] = set()
    for policy in policies:
        sched = schedules.get(policy.id)
        if sched is None or not sched.in_coverage:
            continue
        if enum_value(policy.status) not in INSURED_STATUSES:
            continue
        for member in _insured_members(policy, insured_map, members):
            insured_person_ids.add(member.id)
    insured_person_count = len(insured_person_ids)

    # ---- 逾期未缴清单（手动记录的已缴期数 < 按时间应缴期数） ----
    overdue_out: list[OverdueItemOut] = []
    for policy in policies:
        sched = schedules.get(policy.id)
        if sched is None or sched.overdue_periods <= 0:
            continue
        if enum_value(policy.status) not in INSURED_STATUSES:
            continue
        if sched.next_due_date is None:  # 已缴清时不会走到这里，纯防御
            continue
        insureds = _insured_members(policy, insured_map, members)
        if not insureds:
            continue
        primary = _primary_of(policy, insured_map, members) or insureds[0]
        premium = Decimal(str(policy.premium_per_period))
        overdue_out.append(
            OverdueItemOut(
                policy_id=policy.id,
                company=policy.company,
                product_name=policy.product_name,
                insured_id=primary.id,
                insured_name=primary.name,
                insured_ids=[m.id for m in insureds],
                insured_names=[m.name for m in insureds],
                premium_per_period=money(premium),
                overdue_periods=sched.overdue_periods,
                earliest_due_date=sched.next_due_date,
                amount=money(premium * sched.overdue_periods),
            )
        )
    overdue_out.sort(key=lambda item: (item.earliest_due_date, item.policy_id))

    overview = OverviewOut(
        annual_total_premium=money(annual_total),
        unpaid_annual_premium=money(unpaid_annual),
        year_unpaid_premium=money(year_unpaid),
        upcoming60_premium=money(upcoming_premium),
        upcoming60_count=len(upcoming_pairs),
        policy_total=len(policies),
        active_policy_count=active_policy_count,
        insured_person_count=insured_person_count,
        overdue_policy_count=len(overdue_out),
    )

    # ---- upcoming 清单（按 dueDate, policyId 升序） ----
    upcoming_sorted = sorted(upcoming_pairs, key=lambda pair: (pair[1].due_date, pair[0].id))  # type: ignore[attr-defined]
    upcoming_out: list[UpcomingItemOut] = []
    for policy, item in upcoming_sorted:
        insureds = _insured_members(policy, insured_map, members)
        if not insureds:
            continue
        primary = _primary_of(policy, insured_map, members) or insureds[0]
        upcoming_out.append(
            UpcomingItemOut(
                policy_id=policy.id,
                company=policy.company,
                product_name=policy.product_name,
                insured_id=primary.id,
                insured_name=primary.name,
                insured_ids=[m.id for m in insureds],
                insured_names=[m.name for m in insureds],
                premium_per_period=item.amount,  # type: ignore[attr-defined]
                due_date=item.due_date,  # type: ignore[attr-defined]
                period_index=item.period_index,  # type: ignore[attr-defined]
                total_periods=item.total_periods,  # type: ignore[attr-defined]
            )
        )

    # ---- byMember（双维度） ----
    by_insured = _group_by_insured(
        policies, members, insured_map, schedules, upcoming_by_policy,
        excluded_holders, excluded_types,
    )
    by_policyholder = _group_by_policyholder(
        policies, members, schedules, upcoming_by_policy,
        excluded_holders, excluded_types,
    )

    # ---- byType ----
    by_type = _group_by_type(
        policies, schedules, upcoming_by_policy, excluded_holders, excluded_types
    )

    return DashboardOut(
        as_of_date=today,
        overview=overview,
        by_member=ByMemberOut(by_insured=by_insured, by_policyholder=by_policyholder),
        by_type=by_type,
        upcoming=upcoming_out,
        overdue=overdue_out,
    )


def _group_by_insured(
    policies: list[Policy],
    members: dict[str, Member],
    insured_map: dict[str, list[Member]],
    schedules: dict[str, Schedule],
    upcoming_by_policy: dict[str, Decimal],
    excluded_holders: set[str],
    excluded_types: set[str],
) -> list[MemberStatOut]:
    """按**被保人**分组聚合：保费类指标**按人头分摊**，计数类指标**全额**。

    - ``policyCount`` / ``activePolicyCount``：每名被保人各 +1（全额）；
    - ``annualPremium`` / ``upcoming60Premium`` / ``paidToDatePremium``：
      对每张保单分别 ``allocate_amounts(指标, N)``，第 ``i`` 份归**规范排序**第 ``i`` 名被保人；
    - 尾差归规范排序最后一名（与展示顺序一致）。
    """
    groups: dict[str, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "active": 0, "annual": ZERO, "upcoming": ZERO, "paid": ZERO}
    )
    for policy in policies:
        insureds = _insured_members(policy, insured_map, members)
        if not insureds:
            continue
        n = len(insureds)
        sched = schedules.get(policy.id)
        status = enum_value(policy.status)
        annual_contrib = ZERO
        paid_contrib = ZERO
        if sched is not None:
            paid_contrib = sched.cumulative_paid
            # v1.5：按**投保人**过滤年化保费（被保人维度同样以保单投保人为准）；
            # 累计已缴（paid_contrib）属「实际已发生」，**不过滤**。
            # v1.6：年化保费口径**放宽为与主卡片「当年应缴总保费」一致**
            #       （`status ∈ {生效中, 已缴清}`），不再要求「未缴清」——
            #       否则一张 1 年期已缴完的医疗险会在分布图里显示 0，与主卡片对不上账。
            #       新不变式：`Σ byInsured.annualPremium == overview.annualTotalPremium`。
            if status in INSURED_STATUSES and not _is_excluded(
                policy, excluded_holders, excluded_types
            ):
                annual_contrib = sched.annualized_premium
        upcoming_contrib = upcoming_by_policy.get(policy.id, ZERO)

        annual_shares = allocate_amounts(annual_contrib, n)
        paid_shares = allocate_amounts(paid_contrib, n)
        upcoming_shares = allocate_amounts(upcoming_contrib, n)

        for index, member in enumerate(insureds):
            group = groups[member.id]
            group["count"] = int(group["count"]) + 1  # type: ignore[arg-type]
            if status == STATUS_ACTIVE:
                group["active"] = int(group["active"]) + 1  # type: ignore[arg-type]
            group["annual"] = group["annual"] + annual_shares[index]  # type: ignore[operator]
            group["paid"] = group["paid"] + paid_shares[index]  # type: ignore[operator]
            group["upcoming"] = group["upcoming"] + upcoming_shares[index]  # type: ignore[operator]

    return _rows_from_groups(groups, members)


def _group_by_policyholder(
    policies: list[Policy],
    members: dict[str, Member],
    schedules: dict[str, Schedule],
    upcoming_by_policy: dict[str, Decimal],
    excluded_holders: set[str],
    excluded_types: set[str],
) -> list[MemberStatOut]:
    """按**投保人**分组聚合：**不分摊**、按保单去重（口径与 v1.2 逐值相同）。

    v1.5：被排除的投保人**仍保留该行**（``policyCount`` / ``activePolicyCount`` 等计数照常），
    仅其 ``annualPremium`` 置 0。
    v1.7：同样叠加「按险种」排除（并集），仅影响 ``annualPremium``。
    """
    groups: dict[str, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "active": 0, "annual": ZERO, "upcoming": ZERO, "paid": ZERO}
    )
    for policy in policies:
        group = groups[policy.policyholder_id]
        group["count"] = int(group["count"]) + 1  # type: ignore[arg-type]
        sched = schedules.get(policy.id)
        if sched is not None:
            if enum_value(policy.status) == STATUS_ACTIVE:
                group["active"] = int(group["active"]) + 1  # type: ignore[arg-type]
            # v1.6：年化保费口径放宽为与主卡片「当年应缴总保费」一致（含已缴清），
            #       理由见 `_group_by_insured` 的同名注释。
            if enum_value(policy.status) in INSURED_STATUSES and not _is_excluded(
                policy, excluded_holders, excluded_types
            ):
                group["annual"] = group["annual"] + sched.annualized_premium  # type: ignore[operator]
            group["paid"] = group["paid"] + sched.cumulative_paid  # type: ignore[operator]
        group["upcoming"] = group["upcoming"] + upcoming_by_policy.get(policy.id, ZERO)  # type: ignore[operator]

    return _rows_from_groups(groups, members)


def _rows_from_groups(
    groups: dict[str, dict[str, object]],
    members: dict[str, Member],
) -> list[MemberStatOut]:
    """把分组累加结果转换为 ``MemberStatOut`` 列表（按关系权重 + 姓名排序）。"""
    rows: list[MemberStatOut] = []
    for member_id, group in groups.items():
        member = members.get(member_id)
        if member is None:
            continue
        rows.append(
            MemberStatOut(
                member_id=member_id,
                name=member.name,
                relation=member.relation,
                policy_count=int(group["count"]),  # type: ignore[arg-type]
                active_policy_count=int(group["active"]),  # type: ignore[arg-type]
                annual_premium=money(group["annual"]),  # type: ignore[arg-type]
                upcoming60_premium=money(group["upcoming"]),  # type: ignore[arg-type]
                paid_to_date_premium=money(group["paid"]),  # type: ignore[arg-type]
            )
        )
    rows.sort(key=lambda row: (_relation_weight(row.relation), row.name))
    return rows


def _group_by_type(
    policies: list[Policy],
    schedules: dict[str, Schedule],
    upcoming_by_policy: dict[str, Decimal],
    excluded_holders: set[str],
    excluded_types: set[str],
) -> list[TypeStatOut]:
    """按险种分组聚合，年化保费降序（**不分摊**、按保单去重）。

    v1.5：``policyCount`` / ``upcoming60Premium`` / ``paidToDatePremium`` 照常统计，
    仅 ``annualPremium`` 跳过「投保人被排除」的保单。

    v1.6：``annualPremium`` 口径**放宽为与主卡片「当年应缴总保费」一致**
    （``status ∈ {生效中, 已缴清}``，不再要求「未缴清」），
    理由见 `_group_by_insured` 的同名注释。
    """
    groups: dict[object, dict[str, object]] = defaultdict(
        lambda: {"count": 0, "annual": ZERO, "upcoming": ZERO, "paid": ZERO}
    )
    for policy in policies:
        group = groups[policy.insurance_type]
        group["count"] = int(group["count"]) + 1  # type: ignore[arg-type]
        sched = schedules.get(policy.id)
        if sched is not None:
            if enum_value(policy.status) in INSURED_STATUSES and not _is_excluded(
                policy, excluded_holders, excluded_types
            ):
                group["annual"] = group["annual"] + sched.annualized_premium  # type: ignore[operator]
            group["paid"] = group["paid"] + sched.cumulative_paid  # type: ignore[operator]
        group["upcoming"] = group["upcoming"] + upcoming_by_policy.get(policy.id, ZERO)  # type: ignore[operator]

    rows = [
        TypeStatOut(
            insurance_type=insurance_type,  # type: ignore[arg-type]
            policy_count=int(group["count"]),  # type: ignore[arg-type]
            annual_premium=money(group["annual"]),  # type: ignore[arg-type]
            upcoming60_premium=money(group["upcoming"]),  # type: ignore[arg-type]
            paid_to_date_premium=money(group["paid"]),  # type: ignore[arg-type]
        )
        for insurance_type, group in groups.items()
    ]
    rows.sort(key=lambda row: row.annual_premium, reverse=True)
    return rows


# ---------------------------------------------------------------------------
# v1.7：统计排除配置（读取 / 全量替换写入）
# ---------------------------------------------------------------------------
def get_exclusions(db: Session) -> StatsExclusionsOut:
    """读取当前统计排除配置（投保人 ∪ 险种）。

    - ``excludedPolicyholderIds``：``members.count_in_stats == False`` 的成员 id；
    - ``excludedInsuranceTypes``：``stats_exclusions WHERE kind='insurance_type'`` 的 ``ref_id``。

    两个列表均**排序**输出，保证幂等往返（``GET`` 结果可原样 ``PUT`` 回去）。
    """
    members = db.query(Member).all()
    holder_ids = sorted(m.id for m in members if not m.count_in_stats)
    type_ids = sorted(insured_service.excluded_insurance_types(db))
    return StatsExclusionsOut(
        excluded_policyholder_ids=holder_ids,
        excluded_insurance_types=type_ids,
    )


def set_exclusions(db: Session, data: StatsExclusionsIn) -> StatsExclusionsOut:
    """**全量替换**统计排除配置（单事务提交），返回保存后的配置。

    写入语义（见 ARCHITECTURE-v17-increment §3.3.3）：

    1. ``excludedPolicyholderIds``：对**全部成员**执行
       ``member.count_in_stats = (member.id NOT IN excludedPolicyholderIds)``；
    2. ``excludedInsuranceTypes``：``DELETE FROM stats_exclusions WHERE kind='insurance_type'``
       后按传入集合 ``INSERT``（全量替换，去重）；
    3. 校验：``excludedPolicyholderIds`` 中每个 id 必须存在，否则 422 ``VALIDATION_ERROR``
       （**不新增错误码**）；险种由 Pydantic ``InsuranceType`` 枚举校验；
    4. 单事务提交，失败回滚。
    """
    members = db.query(Member).all()
    member_ids = {m.id for m in members}
    excluded_holders = set(data.excluded_policyholder_ids)
    missing = sorted(excluded_holders - member_ids)
    if missing:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "排除的投保人不存在",
            {"missingPolicyholderIds": missing},
        )

    # 1) 投保人维度：全量覆盖 members.count_in_stats
    for member in members:
        member.count_in_stats = member.id not in excluded_holders

    # 2) 险种维度：全量替换 stats_exclusions（kind='insurance_type'）
    db.execute(
        delete(StatsExclusion).where(
            StatsExclusion.kind == insured_service.INSURANCE_TYPE_KIND
        )
    )
    for insurance_type in dict.fromkeys(data.excluded_insurance_types):
        db.add(
            StatsExclusion(
                kind=insured_service.INSURANCE_TYPE_KIND,
                ref_id=enum_value(insurance_type),
            )
        )

    db.commit()
    return get_exclusions(db)


__all__ = ["build_dashboard", "get_exclusions", "set_exclusions"]
