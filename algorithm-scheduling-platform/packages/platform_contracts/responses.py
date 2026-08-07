from enum import IntEnum
from typing import Generic, TypeVar

from pydantic import BaseModel


class BusinessCode(IntEnum):
    SUCCESS = 0
    VALIDATION_ERROR = 40001
    NOT_FOUND = 40401
    INTERNAL_ERROR = 50000
    CAPACITY_UNAVAILABLE = 50301


DataT = TypeVar("DataT")


class BusinessResponse(BaseModel, Generic[DataT]):
    code: int
    message: str
    data: DataT | None = None

    @classmethod
    def success(cls, data: DataT, *, message: str = "操作成功") -> "BusinessResponse[DataT]":
        return cls(code=BusinessCode.SUCCESS, message=message, data=data)

    @classmethod
    def failure(
        cls,
        code: BusinessCode | int,
        message: str,
        *,
        data: DataT | None = None,
    ) -> "BusinessResponse[DataT]":
        return cls(code=int(code), message=message, data=data)
