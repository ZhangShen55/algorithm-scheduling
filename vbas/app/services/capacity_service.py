# app/services/capacity_service.py
import os
from datetime import datetime
from ..schemas.capacity import CapacityResponse, Capacity, CapacityV2, CapacityV2Response
from ..schemas.error_codes import AppErrCode
from ..schemas.response import Response as GenericResponse

connection_count: int = 0
processed_images: int = 0
app_start_time: datetime = datetime.now()

def increment_connection():
    """增加连接计数"""
    global connection_count
    connection_count += 1


def increment_processed_images(count=1):
    """增加处理图片计数"""
    global processed_images
    processed_images += count


def get_running_time():
    """获取运行时间（从应用启动到现在）"""
    now = datetime.now()
    delta = now - app_start_time

    # 格式化为天、小时、分钟、秒
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"{days}天 {hours}小时 {minutes}分钟 {seconds}秒"


async def capacity_service() -> CapacityResponse:
    """
    返回当前 APP 的并发能力与库容量信息。
    """
    instance_id = os.getenv("INSTANCE_ID", "unknown")

    # 这里先用示例数据，后续可接入真实监控/统计
    cap = Capacity(
        AppID=instance_id,
        TaskNumTotal=10,
        TaskNumUsed=5,
        FtrNumTotal=0,
        FtrNumUsed=0
    )

    return CapacityResponse(
        Response=GenericResponse(ErrCode=AppErrCode.SUCCESS, Desc="成功"),
        Capacity=cap
    )


async def capacity_v2_service() -> CapacityV2Response:
    """
    返回增强版的 APP 能力信息，包括连接数、处理图片数和运行时间。
    """
    global connection_count, processed_images

    instance_id = os.getenv("INSTANCE_ID", "unknown")

    # 保留协议字段名 RegisterTime，当前含义为应用启动时间。
    register_time_str = app_start_time.strftime("%Y-%m-%d %H:%M:%S")

    # 获取运行时间
    running_time = get_running_time()

    cap = CapacityV2(
        AppID=instance_id,
        TaskNumTotal=10,
        TaskNumUsed=5,
        FtrNumTotal=0,
        FtrNumUsed=0,
        ConnectionCount=connection_count,
        ProcessedImages=processed_images,
        RegisterTime=register_time_str,
        RunningTime=running_time
    )

    return CapacityV2Response(
        Response=GenericResponse(ErrCode=AppErrCode.SUCCESS, Desc="成功"),
        Capacity=cap
    )
