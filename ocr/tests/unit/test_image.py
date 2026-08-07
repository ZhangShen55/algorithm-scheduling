import pytest
from PIL import Image

from app.core.exceptions import ImageDecodeError
from app.utils.image import decode_base64_image


def test_decode_base64_image_returns_rgb_image(image_base64):
    image = decode_base64_image(image_base64, max_bytes=1024)

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (1, 1)


@pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg"])
def test_decode_base64_image_accepts_data_url(image_base64, mime_type):
    image = decode_base64_image(
        f"data:{mime_type};base64,{image_base64}",
        max_bytes=1024,
    )

    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size == (1, 1)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-base64",
        "YWJj",
        "data:text/plain;base64,YWJj",
        "data:image/png,YWJj",
    ],
)
def test_decode_base64_image_rejects_invalid_image(value):
    with pytest.raises(ImageDecodeError, match="图片"):
        decode_base64_image(value, max_bytes=1024)


def test_decode_base64_image_enforces_size_limit(image_base64):
    with pytest.raises(ImageDecodeError, match="大小"):
        decode_base64_image(image_base64, max_bytes=1)
