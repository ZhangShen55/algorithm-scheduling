"""
Image Comparison Service
图像比较服务
"""
import cv2
import numpy as np
from app.core.logger import get_logger

logger = get_logger("image_compare")


def resize_image(image, scale: float = 0.5):
    """
    缩放图像

    Args:
        image: 输入图像
        scale: 缩放比例

    Returns:
        缩放后的图像
    """
    h, w = image.shape[:2]
    new_width = int(w * scale)
    new_height = int(h * scale)
    resized_image = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
    return resized_image


def calculate_chayidu_similarity(img1, img2, sigma: float = 0.33, scale: float = 0.5) -> float:
    """
    使用像素绝对差异计算相似度

    Args:
        img1: 图像1
        img2: 图像2
        sigma: 阈值参数
        scale: 缩放比例

    Returns:
        相似度 (0-1)
    """
    # 若尺寸不一致，先对 img2 resize 成与 img1 一致
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    # 计算像素绝对差异
    diff = cv2.absdiff(img1, img2)

    # 转灰度
    diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)

    # 二值化（差异图）
    _, diff_mask = cv2.threshold(diff_gray, 50, 255, cv2.THRESH_BINARY)

    # 相似度 = 没变的像素占比
    similarity = 1 - np.count_nonzero(diff_mask) / diff_mask.size

    return similarity


def compare_histogram(image_a, image_b, scale: float = 0.5) -> float:
    """
    通过直方图比较图像相似度

    Args:
        image_a: 图像A
        image_b: 图像B
        scale: 缩放比例

    Returns:
        相似度 (0-1)
    """
    # 转换为HSV色彩空间
    hsv_a = cv2.cvtColor(image_a, cv2.COLOR_BGR2HSV)
    hsv_b = cv2.cvtColor(image_b, cv2.COLOR_BGR2HSV)

    # 统一缩放图片大小
    hsv_a = resize_image(hsv_a, scale)
    hsv_b = resize_image(hsv_b, scale)

    # 计算直方图，并归一化
    hist_a = cv2.calcHist([hsv_a], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist_b = cv2.calcHist([hsv_b], [0, 1], None, [50, 60], [0, 180, 0, 256])

    cv2.normalize(hist_a, hist_a, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
    cv2.normalize(hist_b, hist_b, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)

    # 使用相关性比较直方图
    similarity = cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL)
    return similarity


def auto_canny(gray_image, sigma: float = 0.33):
    """
    自动Canny边缘检测

    Args:
        gray_image: 灰度图像
        sigma: 阈值参数

    Returns:
        边缘图像
    """
    # 计算图像的中位值
    v = np.median(gray_image)

    # 自动计算低阈值和高阈值
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))

    # 使用 Canny 边缘检测
    edges = cv2.Canny(gray_image, lower, upper)
    return edges


def calculate_edge_similarity(image1, image2, sigma: float = 0.33, scale: float = 0.5) -> float:
    """
    计算两张图像的边缘相似性

    Args:
        image1: 图像1
        image2: 图像2
        sigma: Canny阈值参数
        scale: 缩放比例

    Returns:
        相似度 (0-1)
    """
    # 将图像转换为灰度
    gray1 = cv2.cvtColor(image1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(image2, cv2.COLOR_BGR2GRAY)

    # 统一缩放图片大小
    gray1 = resize_image(gray1, scale)
    gray2 = resize_image(gray2, scale)

    # 使用自动Canny边缘检测
    edges1 = auto_canny(gray1, sigma)
    edges2 = auto_canny(gray2, sigma)

    # 计算边缘图像的相似度
    edge_similarity = np.count_nonzero(edges1 == edges2) / edges1.size

    return edge_similarity


def compare_images(img1, img2, alpha: float = 0.5) -> float:
    """
    比较两张图像的相似度（主函数）

    Args:
        img1: 图像1
        img2: 图像2
        alpha: 权重系数（保留参数，当前未使用）

    Returns:
        相似度 (0-1)
    """
    # 使用像素差异算法计算相似度
    similarity = calculate_chayidu_similarity(img1, img2)
    return similarity


def is_image_clear(image, threshold: int = 100) -> bool:
    """
    判断图像是否清晰

    Args:
        image: 输入图像
        threshold: 清晰度阈值

    Returns:
        是否清晰
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    return laplacian_var > threshold
