# app/schemas/image.py
# 4.1.7 Image
from pydantic import BaseModel
from typing import Optional

class Image(BaseModel):
    URI: Optional[str] = None
    Data: Optional[str] = None  # base64
    ImageID: str
