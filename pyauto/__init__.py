"""pyauto —— 脚本自动化小工具箱。

三大块：
    ocr        用 Windows 自带 OCR（免安装、可离线）把屏幕/图片转成带坐标的文字
    runner     用 JSON 描述任务，串起来跑，带超时/重试/日志
    scheduler  按间隔、每天定时或 cron 反复跑这些任务

示例：
    python -m pyauto doctor
    python -m pyauto ocr shot.png --find 同意 --click
    python -m pyauto run tasks.json
    python -m pyauto schedule tasks.json --every 5m
"""

__version__ = "0.1.0"

from .ocr import OcrResult, OcrError, recognize, grab_screen, annotate  # noqa: F401
from .runner import Runner, TaskResult, load_config, run_config  # noqa: F401
from .scheduler import parse_spec, serve, Schedule  # noqa: F401
from . import actions as actions  # noqa: F401
from .actions import click, move_to, type_text, hotkey, screen_size  # noqa: F401
from . import adb as adb  # noqa: F401
from .adb import Device, AdbError  # noqa: F401
from . import imagefind as imagefind  # noqa: F401
from .imagefind import Match  # noqa: F401

__all__ = [
    "__version__",
    "OcrResult", "OcrError", "recognize", "grab_screen", "annotate",
    "Runner", "TaskResult", "load_config", "run_config",
    "parse_spec", "serve", "Schedule",
    "actions", "click", "move_to", "type_text", "hotkey", "screen_size",
    "adb", "Device", "AdbError",
    "imagefind", "Match",
]
