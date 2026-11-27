"""OCR 引擎封装：Windows 自带 OCR（免安装）+ Tesseract（可选回退）。

对上层只暴露 :func:`recognize`，返回带坐标的 :class:`OcrResult`，
坐标默认已换算回原图，可直接用于点击/断言。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

try:  # Pillow 只用于预处理/裁剪/标注，缺了也能跑
    from PIL import Image, ImageOps
    HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    HAVE_PIL = False

HERE = Path(__file__).resolve().parent
WINOCR_PS1 = HERE / "winocr.ps1"

# Windows OCR 单边最大像素；超过需要先缩放
WINOCR_MAX_DIM = 8000
# 让长边落在这个区间，识别率最好（过大反而糊，过小认不出）
TARGET_MIN, TARGET_MAX = 900, 4000
CREATE_NO_WINDOW = 0x08000000

CJK = r"\u2e80-\u9fff\u3000-\u303f\uff00-\uffef"
# 标点：全角标点 + 弯引号 + ASCII 双引号
_FULLWIDTH_PUNCT = r"\u3000-\u303f\uff01-\uff5e\u2018-\u201f\""
_HSPACE = r"[ \t\u00a0]"
# 中文逐字识别出的空格（不能吃掉换行，否则两行会粘成一行）
_CJK_GAP = re.compile(rf"(?<=[{CJK}]){_HSPACE}+(?=[{CJK}])")
# 全角标点前后的多余空格："01 ： 09" -> "01：09"
_SPACE_BEFORE_PUNCT = re.compile(rf"{_HSPACE}+(?=[{_FULLWIDTH_PUNCT}])")
_SPACE_AFTER_PUNCT = re.compile(rf"(?<=[{_FULLWIDTH_PUNCT}]){_HSPACE}+(?=[\d\u4e00-\u9fff])")


class OcrError(RuntimeError):
    """OCR 调用失败。"""


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[int, int]:
        return int(round(self.x + self.w / 2)), int(round(self.y + self.h / 2))

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def as_list(self) -> list[float]:
        return [round(self.x, 1), round(self.y, 1), round(self.w, 1), round(self.h, 1)]


@dataclass
class Line:
    text: str
    box: Box
    words: list["Word"] = field(default_factory=list)


@dataclass
class Word:
    text: str
    box: Box


@dataclass
class OcrResult:
    image: str
    engine: str
    lang: str
    text: str                      # 已做 CJK 空格清理
    raw_text: str                  # 引擎原始输出
    lines: list[Line]
    elapsed_ms: int
    scale: float = 1.0             # 识别时用的缩放比例
    offset: tuple[float, float] = (0.0, 0.0)  # 裁剪偏移（原图坐标）

    # ---- 坐标换算：识别图 -> 原图 ----
    def _map_box(self, box: Box) -> Box:
        s = self.scale or 1.0
        return Box(box.x / s + self.offset[0], box.y / s + self.offset[1], box.w / s, box.h / s)

    def mapped_lines(self) -> list[Line]:
        out = []
        for ln in self.lines:
            out.append(Line(ln.text, self._map_box(ln.box),
                            [Word(w.text, self._map_box(w.box)) for w in ln.words]))
        return out

    def find(self, pattern: str, exact: bool = False, ignore_case: bool = True) -> list[Line]:
        """按整行匹配，返回原图坐标的行（完全相等的排前面，方便点按钮）。"""
        if not pattern:
            return []
        needle = pattern.lower() if ignore_case else pattern
        exact_hits: list[Line] = []
        partial_hits: list[Line] = []
        for ln in self.mapped_lines():
            hay = ln.text.lower() if ignore_case else ln.text
            if hay == needle:
                exact_hits.append(ln)
            elif not exact and needle in hay:
                partial_hits.append(ln)
        return exact_hits + partial_hits

    def find_words(self, pattern: str) -> list[Word]:
        """按词/短标签匹配（整行匹配不到时用），返回原图坐标。"""
        if not pattern:
            return []
        needle = pattern.lower()
        hits = []
        for ln in self.mapped_lines():
            for w in ln.words:
                if needle in w.text.lower():
                    hits.append(w)
        return hits

    def locate(self, pattern: str, exact: bool = False) -> list[dict]:
        """统一入口：先找行，再找词；返回 [{text, x, y, w, h, cx, cy, level}]。"""
        res = []
        for ln in self.find(pattern, exact=exact):
            cx, cy = ln.box.center
            res.append({"text": ln.text, "x": ln.box.x, "y": ln.box.y, "w": ln.box.w,
                        "h": ln.box.h, "cx": cx, "cy": cy, "level": "line"})
        if not res:
            for w in self.find_words(pattern):
                cx, cy = w.box.center
                res.append({"text": w.text, "x": w.box.x, "y": w.box.y, "w": w.box.w,
                            "h": w.box.h, "cx": cx, "cy": cy, "level": "word"})
        return res

    def to_dict(self) -> dict:
        lines = self.mapped_lines()
        return {
            "ok": True,
            "image": self.image,
            "engine": self.engine,
            "lang": self.lang,
            "text": self.text,
            "lines": [
                {"text": ln.text, "x": round(ln.box.x, 1), "y": round(ln.box.y, 1),
                 "w": round(ln.box.w, 1), "h": round(ln.box.h, 1),
                 "words": [{"t": w.text, "x": round(w.box.x, 1), "y": round(w.box.y, 1),
                            "w": round(w.box.w, 1), "h": round(w.box.h, 1)} for w in ln.words]}
                for ln in lines
            ],
            "elapsed_ms": self.elapsed_ms,
        }


# --------------------------------------------------------------------------
# 文本清理
# --------------------------------------------------------------------------
def clean_text(text: str, compact_cjk: bool = True) -> str:
    """Windows OCR 会把中文逐字拆词并加空格（"应 用 市 场"）。

    默认把 CJK 字符之间的空格去掉，恢复成 "应用市场"；英文单词之间的空格保留。
    """
    if not compact_cjk:
        return text
    out = _CJK_GAP.sub("", text)
    out = _SPACE_BEFORE_PUNCT.sub("", out)
    out = _SPACE_AFTER_PUNCT.sub("", out)
    # 行首尾空白清理
    return "\n".join(ln.strip() for ln in out.splitlines())


# --------------------------------------------------------------------------
# Windows 自带 OCR
# --------------------------------------------------------------------------
def _ps_exe() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("powershell")


def _rmdir_quiet(path: Path) -> None:
    """Windows 上子进程刚退出时句柄可能还没释放，删不掉就算了。"""
    for _ in range(3):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(0.05)
    shutil.rmtree(path, ignore_errors=True)


_MARK = b"##PYAUTO##"


def _call_winocr(args: list[str], timeout: float) -> dict:
    """调用 winocr.ps1，从 stdout 取回 UTF-8 JSON。

    用 stdout 而不是临时文件：沙箱/权限环境里子进程未必能写 %TEMP%。
    """
    ps = _ps_exe()
    if not ps:
        raise OcrError("找不到 powershell.exe，无法使用 Windows OCR")
    cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WINOCR_PS1), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise OcrError(f"Windows OCR 超时（>{timeout}s）")
    raw = proc.stdout or b""
    idx = raw.find(_MARK)
    if idx < 0:
        detail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise OcrError(f"Windows OCR 没有返回结果：{detail[:500]}")
    return json.loads(raw[idx + len(_MARK):].decode("utf-8"))


def _run_winocr(image: Path, lang: str, timeout: float) -> dict:
    args = ["-Image", str(image)]
    if lang:
        args += ["-Lang", lang]
    data = _call_winocr(args, timeout)
    if not data.get("ok"):
        raise OcrError(f"Windows OCR 失败：{data.get('error') or data}")
    return data


def windows_languages() -> list[str]:
    if not (_ps_exe() and WINOCR_PS1.exists()):
        return []
    try:
        data = _call_winocr(["-ListLangs"], timeout=60)
    except Exception:
        return []
    return [x["tag"] for x in data.get("langs", [])]


def _auto_lang(preferred: str | None) -> str:
    langs = windows_languages()
    if preferred:
        return preferred
    for cand in ("zh-Hans-CN", "zh-CN", "zh-Hant-TW"):
        if cand in langs:
            return cand
    return langs[0] if langs else ""


# --------------------------------------------------------------------------
# Tesseract（可选）
# --------------------------------------------------------------------------
def tesseract_exe() -> str | None:
    return shutil.which("tesseract")


def _run_tesseract(image: Path, lang: str, timeout: float) -> dict:
    exe = tesseract_exe()
    if not exe:
        raise OcrError("未安装 tesseract")
    cmd = [exe, str(image), "stdout", "-l", lang or "eng", "--psm", "6", "tsv"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise OcrError(f"tesseract 失败：{(proc.stderr or '')[:400]}")
    rows = [r.split("\t") for r in proc.stdout.splitlines() if r.strip()]
    if not rows:
        raise OcrError("tesseract 无输出")
    header, body = rows[0], rows[1:]
    idx = {name: i for i, name in enumerate(header)}
    lines: dict[tuple, dict] = {}
    for r in body:
        try:
            if len(r) < len(header) or not r[idx["text"]].strip():
                continue
            key = (r[idx["page_num"]], r[idx["block_num"]], r[idx["par_num"]], r[idx["line_num"]])
            text = r[idx["text"]].strip()
            x, y = int(r[idx["left"]]), int(r[idx["top"]])
            w, h = int(r[idx["width"]]), int(r[idx["height"]])
        except (ValueError, KeyError, IndexError):
            continue
        ent = lines.setdefault(key, {"words": [], "minx": x, "miny": y, "maxx": x + w, "maxy": y + h})
        ent["words"].append({"t": text, "x": x, "y": y, "w": w, "h": h})
        ent["minx"] = min(ent["minx"], x)
        ent["miny"] = min(ent["miny"], y)
        ent["maxx"] = max(ent["maxx"], x + w)
        ent["maxy"] = max(ent["maxy"], y + h)
    out_lines = []
    for key in sorted(lines):
        ent = lines[key]
        out_lines.append({
            "text": " ".join(w["t"] for w in ent["words"]),
            "x": ent["minx"], "y": ent["miny"],
            "w": ent["maxx"] - ent["minx"], "h": ent["maxy"] - ent["miny"],
            "words": ent["words"],
        })
    return {"ok": True, "engine": "tesseract", "lang": lang, "lines": out_lines,
            "text": "\n".join(l["text"] for l in out_lines),
            "width": 0, "height": 0, "elapsed_ms": 0}


# --------------------------------------------------------------------------
# 预处理
# --------------------------------------------------------------------------
def _plan_scale(size: tuple[int, int], scale: "str | float") -> float:
    w, h = size
    long_side = max(w, h)
    if isinstance(scale, (int, float)):
        return float(scale)
    if long_side <= 0:
        return 1.0
    if long_side > TARGET_MAX:
        return round(TARGET_MAX / long_side, 4)
    if long_side < TARGET_MIN:
        return round(min(TARGET_MIN / long_side, 4.0), 4)
    return 1.0


def _prepare(image: Path, scale, box: Sequence[int] | None, enhance: bool):
    """返回 (可用图片路径, 缩放比例, 偏移, 临时文件或 None)。"""
    if not HAVE_PIL:
        return image, 1.0, (0, 0), None
    with Image.open(image) as im:
        im = im.convert("RGB")
        off = (0, 0)
        if box:
            x, y, w, h = (int(v) for v in box)
            x, y = max(0, x), max(0, y)
            im = im.crop((x, y, min(im.width, x + w), min(im.height, y + h)))
            off = (x, y)
        factor = _plan_scale(im.size, scale)
        if factor != 1.0:
            im = im.resize((max(1, int(im.width * factor)), max(1, int(im.height * factor))),
                           Image.LANCZOS)
        if enhance:
            im = ImageOps.autocontrast(ImageOps.grayscale(im)).convert("RGB")
        if factor == 1.0 and not enhance and off == (0, 0):
            return image, 1.0, off, None
        fd, tmp = tempfile.mkstemp(prefix="pyauto_", suffix=".png")
        os.close(fd)
        im.save(tmp, "PNG")
        return Path(tmp), factor, off, Path(tmp)


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------
def recognize(image: "str | Path", lang: str = "auto", scale: "str | float" = "auto",
              box: Sequence[int] | None = None, backend: str = "auto",
              enhance: bool = False, timeout: float = 120.0,
              compact_cjk: bool = True) -> OcrResult:
    """识别一张图片。

    image     图片路径
    lang      "auto" / "zh-Hans-CN" / "en-US" ...
    scale     "auto" 或数字；auto 会把长边缩放到 900~2600
    box       (x, y, w, h) 只识别该区域（原图坐标）
    backend   "auto" | "windows" | "tesseract"
    """
    path = Path(image).expanduser().resolve()
    if not path.exists():
        raise OcrError(f"图片不存在：{path}")

    use = backend
    if use == "auto":
        use = "windows" if (_ps_exe() and WINOCR_PS1.exists()) else ("tesseract" if tesseract_exe() else "")
        if not use:
            raise OcrError("没有可用的 OCR 引擎：需要 Windows 10/11 或安装 tesseract")

    prepared, factor, offset, tmp = _prepare(path, scale, box, enhance)
    t0 = time.perf_counter()
    try:
        if use == "windows":
            data = _run_winocr(prepared, _auto_lang(None if lang == "auto" else lang), timeout)
        else:
            data = _run_tesseract(prepared, "chi_sim+eng" if lang == "auto" else lang, timeout)
    finally:
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass

    lines = []
    for raw in data.get("lines") or []:
        words = [Word(w["t"], Box(w["x"], w["y"], w["w"], w["h"])) for w in raw.get("words", [])]
        # 行文本也做 CJK 空格压缩，否则 find() 匹配不到「同 意」
        lines.append(Line(clean_text(raw["text"], compact_cjk),
                          Box(raw["x"], raw["y"], raw["w"], raw["h"]), words))

    raw_text = data.get("text") or "\n".join(l.text for l in lines)
    return OcrResult(
        image=str(path),
        engine=data.get("engine", use),
        lang=data.get("lang", lang),
        text=clean_text(raw_text, compact_cjk),
        raw_text=raw_text,
        lines=lines,
        elapsed_ms=data.get("elapsed_ms") or int((time.perf_counter() - t0) * 1000),
        scale=factor,
        offset=offset,
    )


def grab_screen(bbox: Sequence[int] | None = None) -> "Path | None":
    """截屏到临时 PNG（需要 Pillow）。返回路径，调用方负责删除。"""
    if not HAVE_PIL:
        raise OcrError("截屏需要 Pillow：pip install pillow")
    from PIL import ImageGrab  # 延迟导入

    img = ImageGrab.grab(bbox=tuple(bbox) if bbox else None, all_screens=True)
    fd, tmp = tempfile.mkstemp(prefix="pyauto_shot_", suffix=".png")
    os.close(fd)
    img.save(tmp, "PNG")
    return Path(tmp)


def annotate(result: OcrResult, out_path: "str | Path", color: str = "#ff2d55") -> Path:
    """把识别框画回原图，方便肉眼校验。"""
    if not HAVE_PIL:
        raise OcrError("标注需要 Pillow")
    from PIL import ImageDraw

    out = Path(out_path)
    with Image.open(result.image) as im:
        im = im.convert("RGB")
        draw = ImageDraw.Draw(im)
        for ln in result.mapped_lines():
            b = ln.box
            draw.rectangle([b.x, b.y, b.right, b.bottom], outline=color, width=2)
            draw.text((b.x + 2, max(0, b.y - 12)), ln.text[:40], fill=color)
        im.save(out)
    return out


def available_backends() -> dict:
    return {
        "windows": bool(_ps_exe() and WINOCR_PS1.exists()),
        "tesseract": bool(tesseract_exe()),
        "pillow": HAVE_PIL,
    }
