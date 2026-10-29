"""统一业务异常。

所有业务错误通过 ``AppError`` 抛出，由 ``app.main`` 中注册的异常处理器
统一转换为 ``{"code", "message", "detail"}`` 格式的 JSON 响应。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """业务异常。

    Args:
        status_code: HTTP 状态码（如 404 / 409 / 422）。
        code: 业务错误码（如 ``NOT_FOUND`` / ``MEMBER_IN_USE``）。
        message: 面向用户的错误描述。
        detail: 附加结构化信息（可选）。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail


__all__ = ["AppError"]
