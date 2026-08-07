from pydantic import BaseModel, field_validator
from app.models.schemas import PersonBase
from typing import Optional

"""
用于存放/persons接口的请求模型
"""

class PersonFeatureRequest(BaseModel):
    # 人脸特征请求
    photo: str
    name: str
    number: str

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('缺少name参数')
        return v

    @field_validator('number')
    @classmethod
    def validate_number(cls, v):
        if not v or not v.strip():
            raise ValueError('缺少number参数')
        return v

    @field_validator('photo')
    @classmethod
    def validate_photo(cls, v):
        if not v or not v.strip():
            raise ValueError('缺少photo参数')
        return v

class PersonsFeatureRequest(BaseModel):
    persons: list[PersonFeatureRequest]


class PersonBatchFeatureRequest(BaseModel):
    """批量接口专用模型 - 不使用field_validator，在循环中手动验证"""
    photo: str
    name: str
    number: str


class PersonsBatchFeatureRequest(BaseModel):
    persons: list[PersonBatchFeatureRequest]


class GetPersonListRequest(BaseModel):
    skip: int = 0 # 跳过数据
    limit: int = 100 # 默认返回100条数据


class SearchPersonRequest(BaseModel):
    name: Optional[str] = None
    number: Optional[str] = None


class DeletePersonRequest(BaseModel):
    id: str = None
    name: str = None
    number: str = None


class DeletePersonByIdRequest(BaseModel):
    """
    按ID删除人物请求模型
    - ID是唯一标识，不会出现误删
    """
    id: str

