"""adb 通道：通过 adb 控制模拟器/真机（截图、点击、输入、UI 树）。

比「抓窗口截图 + 鼠标点击」稳：
  - 截图是设备帧缓冲，分辨率就是设备分辨率，不受窗口缩放/位置/遮挡影响
  - 点击是 `input tap`，坐标是设备像素，模拟器最小化也能跑
  - 还能读 uiautomator 的控件树，有文字就直接按 text 精确定位（比 OCR 快且准）
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import actions as actions_mod

try:  # Pillow 只用于「等画面稳定」的帧比较，缺了也能用
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

# 常见模拟器的 adb 端口：MuMu 12 / MuMu 6 / 蓝叠、雷电 / 夜神 / MEmu
DEFAULT_SERIALS = (
    "127.0.0.1:16384",
    "127.0.0.1:7555",
    "127.0.0.1:5555",
    "127.0.0.1:62001",
    "127.0.0.1:21503",
    "emulator-5554",
)

MUMU_MANAGER_PATHS = (
    r"F:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe",
    r"C:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe",
    r"D:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe",
    r"E:\Program Files\Netease\MuMu\nx_main\MuMuManager.exe",
)
MUMU_ADB_HINTS = (
    r"F:\Program Files\Netease\MuMu\nx_main\adb.exe",
    r"C:\Program Files\Netease\MuMu\nx_main\adb.exe",
    r"D:\Program Files\Netease\MuMu\nx_main\adb.exe",
)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ADB_KEYBOARD_PKG = "com.android.adbkeyboard"
ADB_KEYBOARD_IME = f"{ADB_KEYBOARD_PKG}/.AdbIME"


class AdbError(RuntimeError):
    """adb 不可用 / 没设备 / 命令失败。"""


# --------------------------------------------------------------------------
# 找 adb 可执行文件
# --------------------------------------------------------------------------
def adb_path() -> str:
    env = os.environ.get("PYAUTO_ADB")
    if env and Path(env).exists():
        return env
    here = Path(__file__).resolve().parent.parent
    for cand in (here / "platform-tools" / "adb.exe", here / "platform-tools" / "adb"):
        if cand.exists():
            return str(cand)
    found = shutil.which("adb")
    if found:
        return found
    for cand in MUMU_ADB_HINTS:
        if Path(cand).exists():
            return cand
    raise AdbError("找不到 adb：把 platform-tools 放到工程目录，或设环境变量 PYAUTO_ADB")


def _run(args: list[str], timeout: float = 60.0, binary: bool = False) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run([adb_path(), *args], capture_output=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError as exc:
        raise AdbError(f"adb 启动失败：{exc}") from None
    except subprocess.TimeoutExpired:
        raise AdbError(f"adb 超时（{timeout}s）：{' '.join(args)}") from None
    if not binary:
        proc.stdout = _decode(proc.stdout or b"")
        proc.stderr = _decode(proc.stderr or b"")
    return proc


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _check(proc: subprocess.CompletedProcess, what: str) -> str:
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise AdbError(f"{what} 失败（rc={proc.returncode}）：{detail[:400]}")
    return proc.stdout or ""


def _frame_signature(path: str | Path, side: int = 32) -> bytes | None:
    """把截图缩成 32x32 灰度，用于比较两帧是否相同。"""
    if Image is None:
        return None
    try:
        with Image.open(path) as im:
            return im.convert("L").resize((side, side)).tobytes()
    except Exception:  # noqa: BLE001
        return None


def _frame_diff(a: bytes, b: bytes) -> float:
    """两帧平均像素差（0–255）。"""
    if len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


# --------------------------------------------------------------------------
# 设备发现
# --------------------------------------------------------------------------
def devices() -> dict[str, str]:
    """{serial: state}，state 一般是 device / offline / unauthorized。"""
    out = _check(_run(["devices"]), "adb devices")
    found: dict[str, str] = {}
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            found[parts[0]] = parts[1]
    return found


def connect(serial: str, timeout: float = 15.0) -> bool:
    proc = _run(["connect", serial], timeout=timeout)
    text = ((proc.stdout or "") + (proc.stderr or "")).lower()
    return "connected" in text and "cannot" not in text and "failed" not in text


def resolve_serial(serial: str | None = None) -> str:
    """决定用哪台设备：显式指定 > 唯一在线设备 > 按常见模拟器端口去连。"""
    if serial:
        return serial
    online = [s for s, st in devices().items() if st == "device"]
    if len(online) == 1:
        return online[0]
    if len(online) > 1:
        raise AdbError(f"有多台在线设备：{', '.join(online)}；请显式指定 serial")

    env = os.environ.get("PYAUTO_SERIAL")
    if env:
        return env
    for cand in DEFAULT_SERIALS:
        if connect(cand):
            return cand
    raise AdbError("没有可用设备：先启动模拟器（或 adb connect 127.0.0.1:16384）")


# --------------------------------------------------------------------------
# UI 树
# --------------------------------------------------------------------------
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


@dataclass
class Node:
    text: str
    desc: str
    rid: str
    cls: str
    pkg: str
    x1: int
    y1: int
    x2: int
    y2: int
    clickable: bool = False
    enabled: bool = True

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2 - self.x1, self.y2 - self.y1

    def __str__(self) -> str:
        label = self.text or self.desc or self.rid or self.cls
        return f"{label!r} @ {self.center} bounds={self.bounds}"


def parse_ui_xml(xml_text: str) -> list[Node]:
    """解析 uiautomator dump 的 XML，返回所有带文字/描述/可点的节点。"""
    start = xml_text.find("<hierarchy")
    if start < 0:
        raise AdbError("uiautomator 输出里没有 <hierarchy>，可能是 dump 失败或界面无控件树")
    try:
        root = ET.fromstring(xml_text[start:])
    except ET.ParseError as exc:
        raise AdbError(f"UI XML 解析失败：{exc}") from None

    nodes: list[Node] = []
    for el in root.iter("node"):
        text = (el.get("text") or "").strip()
        desc = (el.get("content-desc") or "").strip()
        rid = (el.get("resource-id") or "").strip()
        clickable = (el.get("clickable") or "").lower() == "true"
        if not (text or desc or clickable):
            continue
        m = _BOUNDS.match(el.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = (int(v) for v in m.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        nodes.append(Node(text=text, desc=desc, rid=rid, cls=(el.get("class") or ""),
                          pkg=(el.get("package") or ""), x1=x1, y1=y1, x2=x2, y2=y2,
                          clickable=clickable, enabled=(el.get("enabled") or "").lower() != "false"))
    return nodes


# --------------------------------------------------------------------------
# 设备
# --------------------------------------------------------------------------
class Device:
    """一台设备。所有操作都会先确认 serial。"""

    def __init__(self, serial: str | None = None, connect_timeout: float = 15.0):
        self._serial = serial or os.environ.get("PYAUTO_SERIAL")
        self._connect_timeout = connect_timeout
        self._size: tuple[int, int] | None = None

    # ---- 基础 ----
    @property
    def serial(self) -> str:
        if not self._serial:
            self._serial = resolve_serial()
        return self._serial

    def raw(self, *args: str, timeout: float = 60.0, binary: bool = False):
        return _run(["-s", self.serial, *args], timeout=timeout, binary=binary)

    def shell(self, cmd: str, timeout: float = 60.0) -> str:
        return _check(self.raw("shell", cmd, timeout=timeout), f"shell {cmd[:80]}")

    # ---- 屏幕 ----
    def size(self) -> tuple[int, int]:
        """设备分辨率（优先用 wm size 的 Override 值）。"""
        if self._size:
            return self._size
        text = self.shell("wm size").strip()
        m = re.search(r"Override size:\s*(\d+)x(\d+)", text) or re.search(r"Physical size:\s*(\d+)x(\d+)", text)
        if not m:
            raise AdbError(f"读不到分辨率：{text[:200]!r}")
        self._size = (int(m.group(1)), int(m.group(2)))
        return self._size

    def screenshot(self, path: str | Path | None = None, timeout: float = 30.0) -> Path:
        """把设备当前画面存成 PNG，返回路径。默认落在工程目录下的 .pyauto_shots。"""
        if path is None:
            base = Path(__file__).resolve().parent.parent / ".pyauto_shots"
            path = base / f"device_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = self.raw("exec-out", "screencap", "-p", timeout=timeout, binary=True)
        data = proc.stdout or b""
        if proc.returncode != 0 or not data.startswith(b"\x89PNG"):
            raise AdbError(f"screencap 失败（rc={proc.returncode}）：{_decode(data[:300])}")
        target.write_bytes(data)
        return target

    def stable_screenshot(self, path: str | Path | None = None, timeout: float = 5.0,
                          interval: float = 0.35) -> Path:
        """等画面稳定再截图：连续两帧几乎一样才返回。

        游戏/视频每一帧都在变，直接截容易抓到动画中间态，OCR 就会漏字。
        没有 Pillow 时退化成普通截图。
        """
        if Image is None:
            return self.screenshot(path)
        prev_sig = None
        last: Path | None = None
        deadline = time.time() + timeout
        while True:
            cur = self.screenshot()
            sig = _frame_signature(cur)
            same = prev_sig is not None and sig is not None and _frame_diff(prev_sig, sig) <= 2.0
            if same:
                if last:
                    Path(last).unlink(missing_ok=True)
                if path:
                    target = Path(path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(cur.read_bytes())
                    cur.unlink(missing_ok=True)
                    return target
                return cur
            if last:
                Path(last).unlink(missing_ok=True)
            last, prev_sig = cur, sig
            if time.time() >= deadline:
                return cur
            time.sleep(interval)

    # ---- 交互 ----
    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def long_press(self, x: int, y: int, ms: int = 800) -> None:
        self.shell(f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(ms)}")

    def key(self, key: str | int) -> None:
        name = str(key)
        if name.upper().startswith("KEYCODE_"):
            self.shell(f"input keyevent {name.upper()}")
        elif name.isdigit():
            self.shell(f"input keyevent {name}")
        else:
            self.shell(f"input keyevent KEYCODE_{name.upper()}")

    def text(self, value: str) -> str:
        """输入文本。ASCII 走 input text；中文需要设备装了 ADBKeyboard。"""
        if value.isascii():
            escaped = shlex.quote(value.replace(" ", "%s"))
            self.shell(f"input text {escaped}")
            return "input"
        if self.has_adb_keyboard():
            self._ensure_adb_ime()
            esc = value.replace("'", "'\\''")
            self.shell(f"am broadcast -a ADB_INPUT_TEXT --es msg '{esc}'")
            return "adbkeyboard"
        raise AdbError(
            "中文输入需要设备端装 ADBKeyboard（com.android.adbkeyboard）："
            "装好后会自动走广播输入；否则请用 ASCII，或改用 OCR/坐标点击绕开输入"
        )

    def has_adb_keyboard(self) -> bool:
        try:
            out = self.shell("pm list packages com.android.adbkeyboard")
        except AdbError:
            return False
        return ADB_KEYBOARD_PKG in out

    def _ensure_adb_ime(self) -> None:
        self.shell(f"ime enable {ADB_KEYBOARD_IME}")
        self.shell(f"ime set {ADB_KEYBOARD_IME}")

    # ---- UI ----
    def ui_xml(self, timeout: float = 60.0) -> str:
        """dump 当前界面的原始 XML。"""
        remote = "/sdcard/pyauto_ui.xml"
        self.shell(f"uiautomator dump {remote}", timeout=timeout)
        data = self.raw("exec-out", "cat", remote, timeout=timeout, binary=True).stdout or b""
        self.shell(f"rm -f {remote}")
        return _decode(data)

    def ui(self, timeout: float = 60.0) -> list[Node]:
        """当前界面的控件节点。

        注意：游戏（Vulkan/GL 自绘）通常只有一层 FrameLayout，没有任何 text/desc，
        这时返回空列表，属于设备侧限制，请改用 `ocr --device` 按文字找坐标。
        """
        return parse_ui_xml(self.ui_xml(timeout))

    def find(self, text: str, partial: bool = True, clickable_only: bool = False) -> list[Node]:
        needle = text.strip().lower()
        hits = []
        for node in self.ui():
            hay = f"{node.text} {node.desc}".lower()
            if not hay.strip():
                continue
            if (needle in hay) if partial else (node.text.strip().lower() == needle):
                if clickable_only and not node.clickable:
                    continue
                hits.append(node)
        # 完全相等的排前面
        hits.sort(key=lambda n: 0 if n.text.strip().lower() == needle else 1)
        return hits

    def wait_for(self, text: str, timeout: float = 15.0, interval: float = 0.8,
                 partial: bool = True) -> Node | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                hits = self.find(text, partial=partial)
            except AdbError:
                hits = []
            if hits:
                return hits[0]
            time.sleep(interval)
        return None

    # ---- 应用 ----
    def current_app(self) -> str:
        out = self.shell("dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp'")
        m = re.search(r"([\w.]+)/([\w.$]+)", out)
        return f"{m.group(1)}/{m.group(2)}" if m else out.strip()[:200]

    def start(self, component: str) -> str:
        """component 可以是包名（自动找启动 Activity）或 pkg/Activity。"""
        if "/" not in component:
            out = self.shell(f"cmd package resolve-activity --brief {component}")
            line = out.strip().splitlines()[-1].strip()
            if "/" not in line:
                raise AdbError(f"找不到 {component} 的启动 Activity：{out.strip()[:200]}")
            component = line
        return self.shell(f"am start -n {component}")

    def stop(self, package: str) -> str:
        return self.shell(f"am force-stop {package}")


# --------------------------------------------------------------------------
# 模拟器（MuMu）
# --------------------------------------------------------------------------
def mumu_manager() -> str | None:
    env = os.environ.get("PYAUTO_MUMU")
    if env and Path(env).exists():
        return env
    for cand in MUMU_MANAGER_PATHS:
        if Path(cand).exists():
            return cand
    return None


def mumu_control(vmindex: int = 0, action: str = "launch", version: int | str = 15,
                 timeout: float = 90.0) -> dict:
    """调 MuMuManager 的 control 子命令，返回解析后的 JSON。

    注意：MuMu 12 的双引擎（12/15）必须带 `-ver 15`，不带会返回
    `{"errcode": -502, "errmsg": "mainnx request failed"}` 且不启动。
    """
    mgr = mumu_manager()
    if not mgr:
        return {"errcode": -1, "errmsg": "没找到 MuMuManager.exe（可用 PYAUTO_MUMU 指定路径）"}

    attempts: list[list[str]] = []
    if version not in (None, "", 0, "0"):
        attempts.append(["-ver", str(version)])
    attempts.append([])          # 老版本没有 -ver，兜底再试一次

    last: dict = {"errcode": -1, "errmsg": "没有尝试任何命令"}
    for extra in attempts:
        cmd = [mgr, "control", "-v", str(vmindex), *extra, action]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            last = {"errcode": -2, "errmsg": "MuMuManager 超时"}
            continue
        except OSError as exc:
            last = {"errcode": -3, "errmsg": f"跑不起来：{exc}"}
            continue
        text = _decode(proc.stdout).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"errcode": proc.returncode, "errmsg": text[:300] or "没有输出"}
        if int(data.get("errcode", -1)) == 0:
            return data
        last = data
    return last


MUMU_WINDOW_TITLE = "MuMu"


def mumu_open(vmindex: int = 0, timeout: float = 120.0, version: int | str = 15,
              front: bool = True) -> dict:
    """启动 MuMu 实例；等窗口出现并提到最前。

    返回 {"ok": bool, "msg": str, "window": {...} | None, "errcode": int}
    """
    res = mumu_control(vmindex, "launch", version)
    if int(res.get("errcode", -1)) != 0:
        return {"ok": False, "msg": f"启动失败：{res.get('errmsg') or res}", "window": None,
                "errcode": int(res.get("errcode", -1))}

    win = None
    if front:
        win = actions_mod.focus_window(MUMU_WINDOW_TITLE, timeout=timeout, interval=0.8)
        if win is None:
            return {"ok": False, "msg": f"等了 {timeout:.0f}s 没看到 MuMu 窗口", "window": None,
                    "errcode": 0}
    return {"ok": True, "msg": "已启动", "window": win, "errcode": 0}


def mumu_launch(vmindex: int = 0, timeout: float = 120.0) -> bool:
    """启动 MuMu 实例（兼容旧调用），成功返回 True。"""
    return bool(mumu_open(vmindex, timeout=timeout, front=False)["ok"])


def wait_boot(timeout: float = 120.0, device: Device | None = None) -> bool:
    """等 sys.boot_completed=1。"""
    dev = device or Device()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if dev.shell("getprop sys.boot_completed").strip() == "1":
                return True
        except AdbError:
            pass
        time.sleep(2)
    return False


def ensure(serial: str | None = None) -> Device:
    """拿到一台可用设备（必要时自动 connect 常见模拟器端口）。"""
    return Device(serial)
