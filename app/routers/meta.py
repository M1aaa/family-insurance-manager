"""元数据与健康检查路由。

- ``GET /api/health``      Docker HEALTHCHECK 用，含数据库连通性检查；
- ``GET /api/meta/enums``  前端下拉框字典（一次取全 7 组枚举）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import __version__
from ..database import engine
from ..enums import (
    CoverageTermType,
    Gender,
    InsuranceType,
    PaymentFrequency,
    PaymentTermType,
    PolicyStatus,
    Relation,
)

router = APIRouter(prefix="/api", tags=["meta"])


def _values(enum_cls) -> list[str]:  # noqa: ANN001
    """返回枚举成员的中文值列表。"""
    return [member.value for member in enum_cls]


@router.get("/health")
def health() -> JSONResponse:
    """健康检查：验证服务与数据库连通性。"""
    db_status = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - 健康检查需吞掉具体异常
        db_status = "error"

    status = "ok" if db_status == "ok" else "degraded"
    body = {
        "status": status,
        "db": db_status,
        "version": __version__,
        "time": datetime.now(UTC).isoformat(),
    }
    status_code = 200 if db_status == "ok" else 503
    return JSONResponse(status_code=status_code, content=body)


@router.get("/meta/enums")
def meta_enums() -> dict[str, list[str]]:
    """返回前端所需的全部枚举字典。"""
    return {
        "insuranceType": _values(InsuranceType),
        "policyStatus": _values(PolicyStatus),
        "coverageTermType": _values(CoverageTermType),
        "paymentTermType": _values(PaymentTermType),
        "paymentFrequency": _values(PaymentFrequency),
        "relation": _values(Relation),
        "gender": _values(Gender),
    }


__all__ = ["router"]
