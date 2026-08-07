"""Local terminal-callback fixture for the PPT slice operator."""
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException

from app.schemas import TerminalResultCallback

app = FastAPI(docs_url="/docs")


@app.post("/LocalVideoPPTSliceTasks/v1.0.0/ppt-slice-result-callback")
async def result_callback(data: TerminalResultCallback):
    """Validate that the atomically published manifest is visible."""
    print(f"Received terminal callback data: {data}")
    manifest_path = Path(data.manifest_path)
    if not manifest_path.is_file():
        raise HTTPException(status_code=409, detail="manifest.json 尚未发布")
    return {
        "status": "success",
        "task_id": data.task_id,
        "operator_task_id": data.operator_task_id,
    }


if __name__ == "__main__":
    uvicorn.run(app=app, host="0.0.0.0", port=9004)
