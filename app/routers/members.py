"""成员路由（``/api/members``）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..schemas import MemberCreate, MemberOut, MemberUpdate
from ..services import member_service

router = APIRouter(prefix="/api/members", tags=["members"])


@router.get("", response_model=list[MemberOut])
def list_members(db: Session = Depends(get_db)) -> list[MemberOut]:
    """成员列表（含派生字段，按关系权重排序）。"""
    return member_service.list_members(db)


@router.post("", response_model=MemberOut, status_code=201)
def create_member(payload: MemberCreate, db: Session = Depends(get_db)) -> MemberOut:
    """新增成员。"""
    return member_service.create_member(db, payload)


@router.put("/{member_id}", response_model=MemberOut)
def update_member(
    member_id: str, payload: MemberUpdate, db: Session = Depends(get_db)
) -> MemberOut:
    """编辑成员。"""
    return member_service.update_member(db, member_id, payload)


@router.delete("/{member_id}")
def delete_member(member_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    """删除成员（被保单引用时返回 409）。"""
    return member_service.delete_member(db, member_id)


__all__ = ["router"]
