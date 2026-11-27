"""按图片找位置：模板匹配（numpy FFT 归一化互相关），不依赖 OpenCV。

    from pyauto import imagefind
    hits = imagefind.find("buttons\\ok.png", threshold=0.9)
    if hits:
        print(hits[0].center)

截图来自桌面（Pillow ImageGrab）。模板图越小越快；先按原尺寸匹配，
找不到再把模板缩放到 0.9/1.1/0.8/1.25 倍重试（窗口被缩放时有用）。
大图会先在缩小的图上粗搜，再回到原尺寸局部精修，比整图 FFT 快几倍。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:  # Pillow 可选，但找图没它不行
    from PIL import Image
except Exception:  # pragma: no cover - 环境里一般都有
    Image = None  # type: ignore[assignment]

from .ocr import grab_screen

DEFAULT_SCALES = (1.0, 0.9, 1.1, 0.8, 1.25)
DEFAULT_THRESHOLD = 0.85
COARSE_MAX_SIDE = 1400   # 超过这个尺寸先缩小再搜
MIN_SIDE = 12            # 缩得太小就没法匹配了


class ImageFindError(RuntimeError):
    pass


@dataclass
class Match:
    """一处命中：左上角 + 宽高 + 相似度（0~1）。"""

    x: int
    y: int
    w: int
    h: int
    score: float
    scale: float = 1.0

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def as_dict(self) -> dict:
        cx, cy = self.center
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "cx": cx, "cy": cy, "score": round(self.score, 4),
                "scale": self.scale}


# ---- 图片 → 灰度数组 ----

def screen_image(box=None) -> "Image.Image":
    """截屏（可只截 box=(x, y, w, h) 那块），返回 RGB 图。"""
    if Image is None:
        raise ImageFindError("找图需要 Pillow：pip install pillow")
    bbox = (box[0], box[1], box[0] + box[2], box[1] + box[3]) if box else None
    path = grab_screen(bbox=bbox)
    if path is None:
        raise ImageFindError("截屏失败：拿不到屏幕画面")
    try:
        with Image.open(path) as im:
            return im.convert("RGB").copy()
    finally:
        try:
            Path(path).unlink()
        except OSError:
            pass


def screen_gray(box=None) -> np.ndarray:
    """截屏并转成 float32 灰度数组。"""
    return np.asarray(screen_image(box).convert("L"), dtype=np.float32)


def _open_image(source) -> "Image.Image":
    if Image is None:
        raise ImageFindError("找图需要 Pillow：pip install pillow")
    if isinstance(source, Image.Image):
        return source
    path = Path(str(source))
    if not path.exists():
        raise ImageFindError(f"图片不存在：{path}")
    return Image.open(path)


def to_gray(source) -> np.ndarray:
    """转 float32 灰度数组（二维）。"""
    if isinstance(source, np.ndarray) and source.ndim == 2 and source.dtype.kind == "f":
        return source.astype(np.float32, copy=False)
    if isinstance(source, np.ndarray):
        return np.asarray(Image.fromarray(source.astype("uint8")).convert("L"), np.float32)
    return np.asarray(_open_image(source).convert("L"), dtype=np.float32)


def _resize_gray(arr: np.ndarray, scale: float) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return arr
    if Image is None:
        return arr
    h, w = arr.shape
    img = Image.fromarray(arr.astype("uint8")).resize(
        (max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
    return np.asarray(img, dtype=np.float32)


# ---- 核心：归一化互相关 ----

def _window_sum(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    """滑动窗口求和，结果形状 (H-h+1, W-w+1)。"""
    s = np.zeros((arr.shape[0] + 1, arr.shape[1] + 1), dtype=np.float64)
    s[1:, 1:] = np.cumsum(np.cumsum(arr, axis=0, dtype=np.float64), axis=1)
    return s[h:, w:] - s[:-h, w:] - s[h:, :-w] + s[:-h, :-w]


def _correlate(screen: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """有效卷积：conv[u,v] = Σ screen[u+i, v+j] * tpl[i, j]，形状 (H-h+1, W-w+1)。"""
    h, w = tpl.shape
    H, W = screen.shape
    flipped = (tpl[::-1, ::-1]).astype(np.float32)
    fs = np.fft.rfft2(screen.astype(np.float32))
    ft = np.fft.rfft2(flipped, s=screen.shape)
    full = np.fft.irfft2(fs * ft, s=screen.shape)
    return full[h - 1:H, w - 1:W]


def score_map(screen: np.ndarray, tpl: np.ndarray) -> np.ndarray:
    """逐位置的归一化互相关得分（-1~1，越接近 1 越像）。"""
    h, w = tpl.shape
    H, W = screen.shape
    if h > H or w > W:
        return np.zeros((0, 0), dtype=np.float32)
    n = float(h * w)
    mean_t = float(tpl.mean())
    var_t = float(tpl.var())

    corr = _correlate(screen, tpl)
    sum_i = _window_sum(screen, h, w)
    sum_i2 = _window_sum(screen * screen, h, w)
    mean_i = sum_i / n
    var_i = sum_i2 / n - mean_i * mean_i
    num = corr / n - mean_i * mean_t
    den = np.sqrt(np.maximum(var_i, 0.0) * max(var_t, 0.0))
    # 纯色区域没有方差，相关系数没有意义，直接判为不匹配
    with np.errstate(divide="ignore", invalid="ignore"):
        ncc = np.where(den > 1e-3, num / np.maximum(den, 1e-9), -1.0)
    return np.clip(ncc, -1.0, 1.0).astype(np.float32)


def _peaks(scores: np.ndarray, threshold: float, max_results: int,
           h: int, w: int) -> list[tuple[int, int, float]]:
    """取若干个互不重叠的峰（分数从高到低）。"""
    out: list[tuple[int, int, float]] = []
    work = scores.copy()
    for _ in range(max(1, max_results)):
        if work.size == 0:
            break
        idx = int(np.argmax(work))
        y, x = divmod(idx, work.shape[1])
        score = float(work[y, x])
        if score < threshold:
            break
        out.append((x, y, score))
        y0, y1 = max(0, y - h // 2), min(work.shape[0], y + h // 2 + 1)
        x0, x1 = max(0, x - w // 2), min(work.shape[1], x + w // 2 + 1)
        work[y0:y1, x0:x1] = -1.0
    return out


def find_matches(screen_gray: np.ndarray, tpl_gray: np.ndarray,
                 threshold: float = DEFAULT_THRESHOLD,
                 max_results: int = 5) -> list[Match]:
    """在灰度数组里找模板，返回最有把握的若干处（单尺度）。"""
    scores = score_map(screen_gray, tpl_gray)
    if scores.size == 0:
        return []
    h, w = tpl_gray.shape
    return [Match(x, y, w, h, score) for x, y, score in
            _peaks(scores, threshold, max_results, h, w)]


def _refine(screen: np.ndarray, tpl: np.ndarray, x: int, y: int,
            margin: int = 8) -> Match | None:
    """在粗搜位置的邻域里用原尺寸精修，返回更准的坐标和真实得分。"""
    h, w = tpl.shape
    H, W = screen.shape
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(W, x + w + margin)
    y1 = min(H, y + h + margin)
    crop = screen[y0:y1, x0:x1]
    if crop.shape[0] < h or crop.shape[1] < w:
        return None
    scores = score_map(crop, tpl)
    if scores.size == 0:
        return None
    idx = int(np.argmax(scores))
    dy, dx = divmod(idx, scores.shape[1])
    return Match(x0 + dx, y0 + dy, w, h, float(scores[dy, dx]))


def search(screen_gray: np.ndarray, tpl_gray: np.ndarray,
           threshold: float = DEFAULT_THRESHOLD,
           max_results: int = 5) -> list[Match]:
    """单尺度搜索：大图先缩小粗搜，再回到原图精修。"""
    H, W = screen_gray.shape
    h, w = tpl_gray.shape
    if h > H or w > W:
        return []

    longest = max(H, W)
    if longest <= COARSE_MAX_SIDE:
        return find_matches(screen_gray, tpl_gray, threshold, max_results)

    factor = COARSE_MAX_SIDE / float(longest)
    small_screen = _resize_gray(screen_gray, factor)
    small_tpl = _resize_gray(tpl_gray, factor)
    if min(small_tpl.shape) < MIN_SIDE or small_tpl.shape[0] > small_screen.shape[0] \
            or small_tpl.shape[1] > small_screen.shape[1]:
        return find_matches(screen_gray, tpl_gray, threshold, max_results)

    coarse = find_matches(small_screen, small_tpl, threshold, max_results)
    if not coarse:
        return []
    pairs = []  # (真实得分, Match)，用真实得分重新排
    for hit in coarse:
        full = _refine(screen_gray, tpl_gray,
                       int(round(hit.x / factor)), int(round(hit.y / factor)))
        if full is not None and full.score >= threshold:
            pairs.append((full.score, full))
    pairs.sort(key=lambda item: item[0], reverse=True)
    return [match for _, match in pairs]


def find(template, image=None, box=None, threshold: float = DEFAULT_THRESHOLD,
         scales=DEFAULT_SCALES, max_results: int = 5,
         progress=None) -> list[Match]:
    """在 image（默认当前屏幕）里找模板。

    template  : 模板图片路径 / PIL 图 / ndarray
    image     : 大图；None 表示截屏。传 box=(x, y, w, h) 只截那块区域
    threshold : 相似度门槛，0.9 很严，0.8 比较宽松
    scales    : 依次尝试的模板缩放倍数，第一个是原尺寸
    """
    tpl = to_gray(template)
    if tpl.size == 0:
        raise ImageFindError("模板是空图片")
    if image is None:
        screen = screen_gray(box)
    else:
        screen = to_gray(image)
    ox, oy = (int(box[0]), int(box[1])) if box else (0, 0)   # 只截了一块时把坐标挪回屏幕
    if screen.shape[0] < tpl.shape[0] or screen.shape[1] < tpl.shape[1]:
        return []

    for scale in scales:
        scaled = _resize_gray(tpl, float(scale))
        if scaled.shape[0] > screen.shape[0] or scaled.shape[1] > screen.shape[1]:
            continue
        if progress:
            progress(scale)
        hits = search(screen, scaled, threshold, max_results)
        if hits:
            for hit in hits:
                hit.scale = float(scale)
                hit.x += ox
                hit.y += oy
                # 命中框就是屏幕上的实际大小（缩放过的话按缩放算），中心即点击点
            return hits
    return []


def wait(template, timeout: float = 10.0, interval: float = 0.4,
         threshold: float = DEFAULT_THRESHOLD, box=None,
         scales=DEFAULT_SCALES, cancel=None) -> Match | None:
    """反复找，找到就返回，超时返回 None。cancel 是 threading.Event，可中途放弃。"""
    deadline = time.time() + max(0.0, timeout)
    while True:
        if cancel is not None and cancel.is_set():
            return None
        hits = find(template, box=box, threshold=threshold, scales=scales)
        if hits:
            return hits[0]
        if time.time() >= deadline:
            return None
        time.sleep(interval)
