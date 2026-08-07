from pydantic import BaseModel
from typing import Optional, Dict, Any
from pydantic import Field
from bson import ObjectId

class PersonBase(BaseModel):
    name: str = None # 姓名
    number: Optional[str] = None

class Person(PersonBase):
    id: Optional[str]  # 使用字符串类型存储 MongoDB 的 ObjectId
    class Config:
        json_encoders = {
            ObjectId: str
        }




# class PersonRecognizeRequest(BaseModel):
#     # 人脸recognize请求模型
#     photo: str
#     targets: list[PersonBase]
#     threshold: float = None
#
# def recognize_response(
#     matched: bool,
#     has_face: bool,
#     bbox: Optional[dict],
#     best_sim: float,
#     threshold: float,
#     reason: str,
#     person_doc: Optional[dict] = None,
# ) -> Dict[str, Any]:
#     """[/recognize] 接口返回包装函数"""
#     if not has_face:
#         return {
#             "has_face": False,
#             "bbox": None,
#             "threshold": threshold,
#             "match": None,
#             "message": "图像中未检测到人脸，请重新捕捉人脸",
#         }
#
#     if not matched:
#         return {
#             "has_face": True,
#             "bbox": bbox,
#             "threshold": threshold,
#             "match": None,
#             "message": "匹配失败，未能够匹配到目标人物",
#         }
#
#     # 成功匹配，且高于阈值
#     if best_sim >= threshold:
#         return {
#             "has_face": True,
#             "bbox": bbox,
#             "threshold": threshold,
#             "match": {
#                 "reason": reason,
#                 "id": str(person_doc["_id"]),
#                 "name": person_doc.get("name"),
#                 "number": person_doc.get("number"),
#                 "similarity": f"{best_sim * 100:.2f}%",
#             },
#             "message": f"匹配成功，检测相似度:{best_sim * 100:.2f}% >= 阈值{threshold * 100:.2f}%",
#         }
#
#     # score低于阈值
#     return {
#         "has_face": True,
#         "bbox": bbox,
#         "threshold": threshold,
#         "match": None,
#         "message": f"匹配失败，检测相似度:{best_sim * 100:.2f}% < 阈值{threshold * 100:.2f}%",
#     }

