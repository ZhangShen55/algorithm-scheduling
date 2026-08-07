import os
import time
import logging
from datetime import datetime
from ..schemas.version import VersionResponse, VersionInfo
from ..schemas.response import Response as GenericResponse
from ..schemas.error_codes import AppErrCode
from ..core.settings import APP_VER, ADP_VER, ALG_VER, Total_HaveProcess_Tasks

logger = logging.getLogger(__name__)

#启动时间
AppStarttime = time.time()

def format_duration(Sec: int) -> str:
    # time_delta = timedelta(seconds=seconds)
    # days, hours, minutes = time_delta.days, 0, 0
    # if time_delta.seconds >= 3600:
    #     hours, remainder = divmod(time_delta.seconds, 3600)
    #     minutes, seconds = divmod(remainder, 60)
    # else:
    #     minutes, seconds = divmod(time_delta.seconds, 60)
    days = Sec // 86400          # 计算天数
    remaining = Sec % 86400      # 剩余秒数
    hours = remaining // 3600         # 计算小时
    minutes = (remaining % 3600) // 60 # 计算分钟
    seconds = remaining % 60           # 剩余秒数
    return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"

async def version_service() -> VersionResponse:
    """
    返回 APP/Adapter/Algorithm 的版本信息
    """
    instance_id = os.getenv("INSTANCE_ID", os.getenv("instance_id", ""))
    # 下面的环境变量名可根据你实际设置来调整
    # app_ver = os.getenv("APP_VERSION", "")
    # adp_ver = os.getenv("ADP_VERSION", "")
    app_ver = APP_VER
    adp_ver = ADP_VER
    alg_ver = ALG_VER

    NowTime = time.time()

    # global Total_HaveProcess_Tasks
    NowTime = time.time()
    # 转换为日期时间对象
    dt = datetime.fromtimestamp(NowTime)
    # 格式化为可读的日期字符串
    date_string = dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    Total_Pro_Tasks = Total_HaveProcess_Tasks["val"]

     # 转换为日期时间对象
    dt_time = datetime.fromtimestamp(float(AppStarttime))
    # 格式化为可读的日期字符串
    start_time_str = dt_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # 取前3位毫秒
    # start_time_str = datetime.datetime.fromtimestamp(app_start_time).strftime("%Y-%m-%d %H:%M:%S")    
    logger.info(f"get_version, start_time_str={start_time_str}")

    run_time_sec = NowTime - float(AppStarttime)
    runTimeStr = format_duration(int(run_time_sec))   
    logger.info(f"get_version, runTimeStr={runTimeStr} Total_Pro_Tasks={Total_HaveProcess_Tasks['val']}")

    info = VersionInfo(
        AppID=instance_id,
        AppVer=app_ver,
        AdpVer=adp_ver,
        AlgVer=alg_ver,
        AppStartTime=start_time_str,
        NowTime=date_string,
        RunTime=runTimeStr,
        TotalTasks=str(Total_Pro_Tasks)
    )
    return VersionResponse(
        Response=GenericResponse(ErrCode=AppErrCode.SUCCESS, Desc="成功"),
        Version=info
    )
