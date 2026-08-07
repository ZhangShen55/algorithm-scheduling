# app/schemas/student_behavior.py
from pydantic import BaseModel, Field
from typing import List, Optional
from .geometry import Point
from .error_codes import AppErrCode

class ImageItem(BaseModel):
    StoragePath: str
    ImageId: str
    Points: Optional[List[Point]] = None
    frame_id: Optional[str] = None
    frame_index: Optional[int] = None
    timestamp_seconds: Optional[float] = None

class TeacherBehaviorThresholds(BaseModel):
    sit: Optional[float] = None
    stand: Optional[float] = None
    bbwriting: Optional[float] = None
    teach: Optional[float] = None

class StudentBehaviorThresholds(BaseModel):
    phone: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    hand: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    sleep: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    stand: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    read: Optional[float] = Field(default=None, ge=0.0, le=1.0)

class Stu_Tea_BehaviorRequest(BaseModel):
    ImageList: List[ImageItem]
    Teacher_Behavior_Thresd: Optional[TeacherBehaviorThresholds] = None

class StudentBehaviorRequest(BaseModel):
    ImageList: List[ImageItem]
    Student_Thresd: Optional[StudentBehaviorThresholds] = None
    task_id: Optional[str] = None
    batch_id: Optional[str] = None
    stream_type: Optional[str] = None

class TeacherBehaviorRequest(Stu_Tea_BehaviorRequest):
    ReturnHeadPose: bool = False
    task_id: Optional[str] = None
    batch_id: Optional[str] = None
    stream_type: Optional[str] = None

class ObjectPosition(BaseModel):
    LeftTopX: int
    LeftTopY: int
    RightBtmX: int
    RightBtmY: int
    Confidence: Optional[float] = None
    SuspectedSitting: Optional[bool] = None # 疑似坐
    PostureFallback: Optional[bool] = None #坐站都低于阈值，使用主体位置作为站

class ResultItem(BaseModel):
    ObjectType: int
    ObjectCount: int
    ObjectPostList: Optional[List[ObjectPosition]] = None

class BoxPosition(BaseModel):
    LeftTopX: int
    LeftTopY: int
    RightBtmX: int
    RightBtmY: int

class HeadPoseResultModel(BaseModel):
    Enabled: bool
    Status: str
    FaceDirection: str = "unknown"
    Yaw: Optional[float] = None
    Pitch: Optional[float] = None
    Roll: Optional[float] = None
    Angle: Optional[float] = None
    IsLookingDown: Optional[bool] = None
    HeadPoseConfidence: Optional[float] = None
    TeacherConfidence: Optional[float] = None
    TeacherSubjectBox: Optional[BoxPosition] = None
    HeadBox: Optional[BoxPosition] = None
    Message: Optional[str] = None

class ImageResult(BaseModel):
    StatusObject: dict
    ResultList: List[ResultItem]

class Stu_Tea_BehaviorResponse(BaseModel):
    StatusObject: dict
    DataList: List[ImageResult]

class TeacherBehaviorImageResult(ImageResult):
    HeadPoseResult: Optional[HeadPoseResultModel] = None

class TeacherBehaviorResponse(BaseModel):
    StatusObject: dict
    DataList: List[TeacherBehaviorImageResult]

HeadPoseResult = HeadPoseResultModel
