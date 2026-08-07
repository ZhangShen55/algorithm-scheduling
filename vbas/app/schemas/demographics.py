# app/schemas/demographics.py
from pydantic import BaseModel
from typing import List
from .geometry import ROI

class PersonPosition(BaseModel):
    PersonThresd: float
    ROI:        ROI

class PersonInfo(BaseModel):
    PersonCnt:    int
    Personlist:   List[PersonPosition]

# 同理为人脸
class FacePosition(BaseModel):
    FaceThresd: float
    ROI:        ROI

class FaceInfo(BaseModel):
    FaceCnt:    int
    Facelist:   List[FacePosition]







