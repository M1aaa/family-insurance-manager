"""依赖注入辅助。

统一再导出数据库会话依赖，供各路由层引用，避免路由直接耦合 ``database`` 模块。
"""

from __future__ import annotations

from .database import get_db

__all__ = ["get_db"]
