class ConfigurationError(RuntimeError):
    pass


class OCRServiceError(RuntimeError):
    def __init__(self, err_no: int, public_message: str):
        super().__init__(public_message)
        self.err_no = err_no
        self.public_message = public_message


class RequestFormatError(OCRServiceError):
    def __init__(self, message: str = "请求中的 key 和 value 无法正确对应"):
        super().__init__(4001, message)


class ImageDecodeError(OCRServiceError):
    def __init__(self, message: str = "图片数据无效"):
        super().__init__(4002, message)


class InferenceError(OCRServiceError):
    def __init__(self):
        super().__init__(5001, "OCR 推理失败")
