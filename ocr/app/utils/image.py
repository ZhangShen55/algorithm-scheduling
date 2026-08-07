import base64
import binascii
from io import BytesIO
import re

from PIL import Image, UnidentifiedImageError

from app.core.exceptions import ImageDecodeError


IMAGE_DATA_URL = re.compile(
    r"\Adata:image/[a-z0-9.+-]+;base64,(?P<payload>.*)\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


def decode_base64_image(value: str, max_bytes: int) -> Image.Image:
    if not value:
        raise ImageDecodeError("图片数据不能为空")
    encoded = value.strip()
    if encoded.lower().startswith("data:"):
        match = IMAGE_DATA_URL.fullmatch(encoded)
        if match is None:
            raise ImageDecodeError("图片 Base64 MIME 前缀无效")
        encoded = match.group("payload")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageDecodeError("图片 Base64 数据无效") from error
    if not raw:
        raise ImageDecodeError("图片数据不能为空")
    if len(raw) > max_bytes:
        raise ImageDecodeError(f"图片大小超过限制：{max_bytes} 字节")
    try:
        with Image.open(BytesIO(raw)) as source:
            source.load()
            return source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImageDecodeError("图片格式无法解析") from error
