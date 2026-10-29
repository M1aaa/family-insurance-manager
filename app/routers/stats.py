"""统计路由（``/api/stats``）。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas import DashboardOut, StatsExclusionsIn, StatsExclusionsOut
from ..services import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    today: Optional[date] = Query(None, alias="today"),
    db: Session = Depends(get_db),
) -> DashboardOut:
    """看板全部数据（``today`` 可选，默认服务端当天）。"""
    return stats_service.build_dashboard(db, today or date.today())


@router.get("/exclusions", response_model=StatsExclusionsOut)
def get_exclusions(db: Session = Depends(get_db)) -> StatsExclusionsOut:
    """读取「不计入统计」排除配置（投保人 id ∪ 险种中文值）。"""
    return stats_service.get_exclusions(db)


@router.put("/exclusions", response_model=StatsExclusionsOut)
def put_exclusions(
    data: StatsExclusionsIn,
    db: Session = Depends(get_db),
) -> StatsExclusionsOut:
    """**全量替换**排除配置（非法险种 / 不存在的成员 → 422 ``VALIDATION_ERROR``）。"""
    return stats_service.set_exclusions(db, data)


__all__ = ["router"]
