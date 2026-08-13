from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: int, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(1001, message, 404)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "未授权访问"):
        super().__init__(1003, message, 401)


class ForbiddenError(AppError):
    def __init__(self, message: str = "权限不足"):
        super().__init__(1004, message, 403)


class ValidationAppError(AppError):
    def __init__(self, message: str = "参数校验失败"):
        super().__init__(1002, message, 400)


class ConflictError(AppError):
    def __init__(self, message: str = "资源已存在"):
        super().__init__(1005, message, 409)

