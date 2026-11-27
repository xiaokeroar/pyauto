"""鼠标/键盘动作（ctypes 调 Win32，无第三方依赖）。

只用于把 OCR 结果落成点击，方便做「看到按钮 -> 点一下」的自动化。
在 DSH 沙箱里可能被限制，普通终端下运行正常。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
try:  # 让坐标与截图像素一致（高 DPI 屏必需）
    _user32.SetProcessDPIAware()
except Exception:  # pragma: no cover
    pass

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def screen_size() -> tuple[int, int]:
    """主屏分辨率（物理像素）。"""
    return _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)


def move_to(x: int, y: int) -> None:
    _user32.SetCursorPos(int(x), int(y))


def click(x: int, y: int, button: str = "left", double: bool = False, delay: float = 0.05) -> None:
    """在屏幕坐标 (x, y) 单击（物理像素）。"""
    move_to(x, y)
    time.sleep(0.02)
    down, up = ((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button == "left"
                else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP))
    for _ in range(2 if double else 1):
        _user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(delay)
        _user32.mouse_event(up, 0, 0, 0, 0)
        if double:
            time.sleep(0.06)


def type_text(text: str, per_char_delay: float = 0.0) -> None:
    """输入文本（Unicode，支持中文）。需要目标窗口已获得焦点。"""
    for ch in text:
        code = ord(ch)
        # 代理对（emoji 等）拆成两个 UTF-16 码元
        units = [code] if code <= 0xFFFF else [0xD800 + ((code - 0x10000) >> 10),
                                               0xDC00 + ((code - 0x10000) & 0x3FF)]
        for u in units:
            for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
                inp = _INPUT(type=INPUT_KEYBOARD, u=_INPUT._U(ki=_KEYBDINPUT(
                    wVk=0, wScan=u, dwFlags=flags, time=0, dwExtraInfo=None)))
                _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if per_char_delay:
            time.sleep(per_char_delay)
    time.sleep(0.05)


def hotkey(*keys: str) -> None:
    """按下组合键，如 hotkey('ctrl', 'v')。"""
    vk = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "enter": 0x0D,
          "esc": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08}
    codes = [vk.get(k.lower(), ord(k.upper()) if len(k) == 1 else 0) for k in keys]
    for c in codes:
        _user32.keybd_event(c, 0, 0, 0)
    for c in reversed(codes):
        _user32.keybd_event(c, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)


# --------------------------------------------------------------------------
# 窗口（把某个窗口提到最前）
# --------------------------------------------------------------------------
SW_RESTORE = 9
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
VK_MENU = 0x12

_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def list_windows(title_part: str = "") -> list[dict]:
    """列出可见的顶层窗口：[{hwnd, title, pid}]，title_part 为空则全要。"""
    found: list[dict] = []
    part = (title_part or "").lower()

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if part and part not in title.lower():
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"hwnd": int(hwnd), "title": title, "pid": int(pid.value)})
        return True

    _user32.EnumWindows(_ENUM_PROC(_cb), 0)
    return found


def bring_to_front(hwnd: int) -> bool:
    """还原 + 置顶再取消置顶 + 抢焦点；成功返回 True。"""
    try:
        _user32.ShowWindow(hwnd, SW_RESTORE)
        # 先 TOPMOST 再 NOTOPMOST：不改 z 序的常规位置，但把窗口抬到最前
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        # 前台锁：先按一下 Alt 再设置，成功率更高
        _user32.keybd_event(VK_MENU, 0, 0, 0)
        _user32.SetForegroundWindow(hwnd)
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        _user32.BringWindowToTop(hwnd)
        return int(_user32.GetForegroundWindow()) == int(hwnd)
    except Exception:  # noqa: BLE001 - 窗口操作失败不该让脚本崩
        return False


def focus_window(title_part: str, timeout: float = 0.0, interval: float = 0.5) -> dict | None:
    """找标题含 title_part 的窗口并提到最前；timeout>0 时轮询等待。

    返回命中的窗口信息 {hwnd, title, pid, foreground}，找不到返回 None。
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        wins = list_windows(title_part)
        if wins:
            win = max(wins, key=lambda w: len(w["title"]))
            ok = bring_to_front(win["hwnd"])
            return {**win, "foreground": ok}
        if time.time() >= deadline:
            return None
        time.sleep(interval)
