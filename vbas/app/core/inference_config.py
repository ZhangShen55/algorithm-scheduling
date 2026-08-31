from pydantic import BaseModel


class InferenceSettings(BaseModel):
    """单个请求内部的推理执行策略与逐模型精度。"""

    StudentModelsSequential: bool = True
    SyncTasks2PolygonsSequential: bool = True
    PersonUseHalf: bool = False
    FaceUseHalf: bool = False
    StudentUseHalf: bool = False
    TeacherUseHalf: bool = False

    model_config = {"extra": "ignore"}
