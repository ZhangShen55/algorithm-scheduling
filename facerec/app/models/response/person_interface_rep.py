from typing import Optional, List, Dict, Any

from pydantic import BaseModel
from app.models.schemas import PersonBase

"""
用于存放/persons接口的响应模型

       "id" : str(doc["_id"]),
        "name": doc.get("name"),
        "number": doc.get("number"),
        "photo_path": doc.get("photo_path"),
        "tip": tip
"""

class PersonFeatureResponse(BaseModel):
    # 人脸特征响应
    id: str
    name: str
    number: str
    photo_path: str
    tip: str

class PersonsFeatureResponse(BaseModel):
    # 人脸特征响应列表
    persons: list[PersonFeatureResponse]

# PersonRead 是返回给前端的模型
class PersonRead(BaseModel):
    id: str
    name: str
    number: Optional[str] = None
    photo_path: Optional[str] = None
    bbox: Optional[str] = None  # 人脸检测框（格式: x,y,w,h，如 "100,150,200,250"）


class SearchPersonResponse(BaseModel):
    persons: List[Dict[str, Any]]