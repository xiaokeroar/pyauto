"""pyauto studio —— 可视化编辑自动化脚本，实时生成 JSON 任务配置和 Python 代码。

    python -m pyauto gui              打开空白工程
    python -m pyauto gui tasks.json   打开已有配置

窗口分三栏：左边加/排动作，中间填参数（坐标能点屏幕拾取，区域能框选），
右边实时给出 JSON 和 Python，底部能一键试跑。
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import actions as _actions  # noqa: F401  导入即声明 DPI 感知，坐标和截图才对得上

FONT = ("Microsoft YaHei UI", 10)
FONT_MONO = ("Consolas", 10)
FONT_H1 = ("Microsoft YaHei UI", 15, "bold")
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")

# ---- 配色：浅色底 + 蓝色强调 ----
BG = "#f4f6fb"
CARD = "#ffffff"
BORDER = "#dfe6f2"
TEXT = "#1e293b"
MUTED = "#64748b"
ACCENT = "#2f6fed"
ACCENT_DARK = "#1d4ed8"
ACCENT_SOFT = "#e8f0ff"
DANGER = "#dc2626"
DANGER_SOFT = "#fdecec"
OK = "#16a34a"
CONSOLE_BG = "#0f172a"
CONSOLE_FG = "#d7e3f7"
CONSOLE_MUTED = "#7c8db0"

SETTINGS_FILE = Path(__file__).resolve().parent.parent / ".pyauto_studio.json"
DEFAULT_SETTINGS = {"hotkey_start": "F9", "hotkey_stop": "F10"}

# 快捷键只认这些键名（键名不区分大小写，也可以写 ctrl+shift+k 这种组合）
VK_NAMES = {
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
    **{chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)},
    **{str(d): ord(str(d)) for d in range(10)},
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
MOD_NAMES = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "lwin": 0x5B}


def parse_hotkey(spec: str) -> tuple[tuple[int, ...], int]:
    """把 'ctrl+shift+k' / 'F9' 解析成 (修饰键 VK 列表, 主键 VK)。"""
    parts = [p.strip().lower() for p in str(spec or "").replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("快捷键是空的")
    mods = tuple(MOD_NAMES[p] for p in parts[:-1] if p in MOD_NAMES)
    if len(mods) != len(parts) - 1:
        raise ValueError(f"不认识的修饰键：{spec}")
    key = parts[-1]
    if key in MOD_NAMES:
        raise ValueError(f"最后要有一个主键，不能只有修饰键：{spec}（比如 ctrl+f9）")
    if key not in VK_NAMES:
        raise ValueError(f"不认识的键：{key}（可用 F1-F12、字母、数字、空格等）")
    return mods, VK_NAMES[key]


def _key_down(vk: int) -> bool:
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:  # noqa: BLE001 - 非 Windows 上不该炸
        return False


def _apply_theme(root: tk.Misc) -> None:
    """把 ttk 调成浅色 + 蓝色强调的现代样式。"""
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=TEXT, font=FONT, borderwidth=0)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
    style.configure("H1.TLabel", background=BG, foreground=TEXT, font=FONT_H1)
    style.configure("Bold.TLabel", background=BG, foreground=TEXT, font=FONT_BOLD)
    style.configure("CardBold.TLabel", background=CARD, foreground=TEXT, font=FONT_BOLD)
    style.configure("Accent.TLabel", background=CARD, foreground=ACCENT, font=FONT_BOLD)
    style.configure("TLabelframe", background=BG, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("TButton", background="#eef2f9", foreground=TEXT, padding=(11, 5),
                    borderwidth=0, focusthickness=0, relief="flat")
    style.map("TButton",
              background=[("active", "#e0e8f6"), ("pressed", "#d3dff5"), ("disabled", "#f2f5fa")],
              foreground=[("disabled", "#a8b3c4")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=(14, 6),
                    font=FONT_BOLD)
    style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("disabled", "#9db8f5")])
    style.configure("Danger.TButton", background=DANGER_SOFT, foreground=DANGER, padding=(12, 5))
    style.map("Danger.TButton", background=[("active", "#fbdada")])
    style.configure("Ghost.TButton", background=CARD, foreground=MUTED, padding=(8, 4))
    style.map("Ghost.TButton", background=[("active", "#eef2f9")])
    style.configure("TEntry", fieldbackground="#ffffff", foreground=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, insertcolor=TEXT, padding=5)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff",
                    bordercolor=BORDER, arrowcolor=MUTED, padding=4)
    style.configure("TSpinbox", fieldbackground="#ffffff", bordercolor=BORDER, padding=4)
    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", BG)])
    style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
    style.map("Card.TCheckbutton", background=[("active", CARD)])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background="#e9eef8", foreground=MUTED,
                    padding=(18, 7), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", CARD)],
              foreground=[("selected", ACCENT)],
              expand=[("selected", (0, 0, 0, 0))])
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    bordercolor=BORDER, rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background="#f1f5fb", foreground=MUTED,
                    borderwidth=0, font=("Microsoft YaHei UI", 9, "bold"))
    style.configure("Vertical.TScrollbar", background="#e4ebf6", troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, borderwidth=0, arrowsize=12)
    style.configure("Horizontal.TScrollbar", background="#e4ebf6", troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, borderwidth=0)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=8, gripcount=0)
    style.configure("TSeparator", background=BORDER)


def _load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if isinstance(raw, dict):
        for key in DEFAULT_SETTINGS:
            value = str(raw.get(key) or "").strip()
            if value:
                data[key] = value
    for key in ("hotkey_start", "hotkey_stop"):
        try:
            parse_hotkey(data[key])
        except ValueError:
            data[key] = DEFAULT_SETTINGS[key]
    if data["hotkey_start"].lower() == data["hotkey_stop"].lower():
        data["hotkey_stop"] = DEFAULT_SETTINGS["hotkey_stop"]
    return data


def _save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


# 动作类型：key -> 中文名
KINDS = {    "click": "点击",
    "type": "输入文字",
    "key": "按键",
    "ocr": "找字 / 点字",
    "image": "找图 / 点图",
    "wait": "等待",
    "shell": "运行命令",
    "python": "运行脚本",
    "run": "打开程序",
    "raw": "自定义",
}

DEFAULT_ACTION = {
    "click": {"kind": "click", "x": 0, "y": 0, "desc": ""},
    "type": {"kind": "type", "value": "", "desc": ""},
    "key": {"kind": "key", "value": "ctrl+s", "desc": ""},
    "ocr": {"kind": "ocr", "expect": "", "box": None, "click": True, "wait": 0, "desc": ""},
    "image": {"kind": "image", "template": "", "action": "click", "threshold": 0.88,
              "wait": 0, "box": None, "desc": ""},
    "wait": {"kind": "wait", "seconds": 1.0, "desc": ""},
    "shell": {"kind": "shell", "cmd": "", "desc": ""},
    "python": {"kind": "python", "script": "", "desc": ""},
    "run": {"kind": "run", "path": "", "desc": ""},
    "raw": {"kind": "raw", "json": '{"type": "shell", "cmd": "echo hi"}', "desc": ""},
}


# --------------------------------------------------------------------------
# 纯逻辑：动作 -> 任务 JSON / Python 代码（方便单独测试）
# --------------------------------------------------------------------------
def _title(action: dict, index: int) -> str:
    desc = (action.get("desc") or "").strip()
    kind = KINDS.get(action.get("kind", ""), action.get("kind", "?"))
    tail = f"  ⟲{len(action['recover'])}" if action.get("recover") else ""
    if desc:
        return f"{index + 1}. {desc}{tail}"
    body = {
        "click": f"({action.get('x')}, {action.get('y')})",
        "type": repr(action.get("value", ""))[:18],
        "key": str(action.get("value", "")),
        "ocr": repr(action.get("expect", ""))[:18],
        "image": Path(str(action.get("template") or "?")).name[:20],
        "wait": f"{action.get('seconds')}s",
        "shell": str(action.get("cmd", ""))[:22],
        "python": str(action.get("script", ""))[:22],
        "run": str(action.get("path", ""))[:22],
        "raw": "…",
    }.get(action.get("kind"), "")
    label = f"{index + 1}. {kind} {body}".rstrip()
    if action.get("recover"):
        label += f"  ⟲{len(action['recover'])}"
    return label


def _name_of(action: dict) -> str:
    """任务名：优先用「说明」，否则用动作类型名。"""
    desc = (action.get("desc") or "").strip()
    return desc or KINDS.get(str(action.get("kind")), "动作")


def to_task(action: dict) -> dict:
    """单个动作 -> pyauto 任务字典。"""
    kind = action.get("kind")
    desc = (action.get("desc") or "").strip()
    if kind == "click":
        task = {"type": "input", "action": "click",
                "x": int(action.get("x") or 0), "y": int(action.get("y") or 0)}
        if action.get("double"):
            task["action"] = "double"
    elif kind == "type":
        task = {"type": "input", "action": "type", "value": str(action.get("value") or "")}
    elif kind == "key":
        task = {"type": "input", "action": "key", "value": str(action.get("value") or "")}
    elif kind == "ocr":
        task = {"type": "ocr", "screen": True, "expect": str(action.get("expect") or ""),
                "click": bool(action.get("click"))}
        box = action.get("box")
        if box and len(box) == 4 and any(box):
            task["box"] = [int(v) for v in box]
        if action.get("wait"):
            task["sleep_before"] = float(action["wait"])
    elif kind == "image":
        template = str(action.get("template") or "").strip()
        if not template:
            raise ValueError("找图动作还没选模板图片")
        task = {"type": "image", "template": template,
                "threshold": float(action.get("threshold") or 0.88),
                "action": str(action.get("action") or "click")}
        box = action.get("box")
        if box and len(box) == 4 and any(box):
            task["box"] = [int(v) for v in box]
        if action.get("wait"):
            task["wait"] = float(action["wait"])
    elif kind == "wait":
        task = {"type": "sleep", "seconds": float(action.get("seconds") or 0)}
    elif kind == "shell":
        task = {"type": "shell", "cmd": str(action.get("cmd") or "")}
    elif kind == "python":
        task = {"type": "python", "script": str(action.get("script") or "")}
    elif kind == "run":
        task = {"type": "shell", "cmd": f'start "" "{action.get("path") or ""}"'}
    elif kind == "raw":
        try:
            task = json.loads(action.get("json") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"自定义动作的 JSON 不合法：{exc}") from None
        if not isinstance(task, dict):
            raise ValueError("自定义动作必须是 JSON 对象")
    else:
        raise ValueError(f"未知动作：{kind}")
    if desc:
        task["name"] = desc
    return task


def rule_to_config(rule: dict) -> dict:
    """studio 的异常处理规则 -> runner 认识的 recover 规则。"""
    when = rule.get("when") or {}
    kind = when.get("kind") or "image"
    if kind == "image":
        template = str(when.get("template") or "").strip()
        if not template:
            raise ValueError("异常处理规则里有一条「当出现图片」，但没选图片")
        cond: dict = {"image": template, "threshold": float(when.get("threshold") or 0.88)}
    else:
        text = str(when.get("text") or "").strip()
        if not text:
            raise ValueError("异常处理规则里有一条「当出现文字」，但没填文字")
        cond = {"ocr": text}
        if when.get("exact"):
            cond["exact"] = True
    out: dict = {"when": cond,
                 "then": [to_task(a) for a in (rule.get("then") or [])],
                 "then_action": str(rule.get("then_action") or "retry")}
    if rule.get("name"):
        out["name"] = str(rule["name"])
    if not out["then"]:
        raise ValueError(f"异常处理规则「{rule.get('name') or '未命名'}」没有任何「先做这些」的动作")
    return out


def to_config(actions: list[dict], log: str = "logs/studio.log",
              stop_on_error: bool = True, schedule: str = "",
              rules: list[dict] | None = None) -> dict:
    tasks = []
    for action in actions:
        task = to_task(action)
        task.setdefault("name", _name_of(action))
        if action.get("recover"):
            task["recover"] = [rule_to_config(r) for r in action["recover"]]
        tasks.append(task)
    config: dict = {"log": log, "history": "logs/history.jsonl",
                    "stop_on_error": stop_on_error, "timeout": 120, "tasks": tasks}
    if schedule.strip():
        config["schedule"] = schedule.strip()
    if rules:
        config["recover"] = [rule_to_config(r) for r in rules]
    return config


def to_json_text(actions: list[dict], **kw) -> str:
    return json.dumps(to_config(actions, **kw), ensure_ascii=False, indent=2)


def _py_lines(action: dict) -> list[str]:
    kind = action.get("kind")
    desc = (action.get("desc") or "").strip()
    tag = f"  # {desc}" if desc else ""

    if kind == "click":
        return [f"actions.click({int(action.get('x') or 0)}, {int(action.get('y') or 0)}){tag}"]
    if kind == "type":
        return [f"actions.type_text({action.get('value') or ''!r}){tag}"]
    if kind == "key":
        keys = [p for p in str(action.get("value") or "").replace("+", " ").split() if p]
        args = ", ".join(repr(k) for k in keys)
        return [f"actions.hotkey({args}){tag}"]
    if kind == "wait":
        return [f"time.sleep({float(action.get('seconds') or 0)}){tag}"]
    if kind == "ocr":
        box = action.get("box")
        src = f"ocr.grab_screen(box={tuple(int(v) for v in box)})" if box and any(box) else "ocr.grab_screen()"
        out = [f"res = ocr.recognize({src}){tag}",
               f"hit = res.find({action.get('expect') or ''!r})"]
        if action.get("click"):
            out += ['if not hit:',
                    f'    raise SystemExit("屏幕上没找到：{action.get("expect") or ""}")',
                    "actions.click(*hit[0].center)"]
        else:
            out += ["print(res.text)"]
        return out
    if kind == "image":
        tpl = action.get("template") or ""
        thr = float(action.get("threshold") or 0.88)
        mode = str(action.get("action") or "click")
        out = [f"hits = imagefind.find({tpl!r}, threshold={thr}){tag}"]
        if mode in ("click", "double"):
            dbl = mode == "double"
            out += ["if not hits:",
                    f'    raise SystemExit("屏幕上没找到图片：{tpl}")',
                    f"actions.click(*hits[0].center{', double=True' if dbl else ''})"]
        else:
            out += ["print('找到' if hits else '没找到', [h.as_dict() for h in hits])"]
        return out
    if kind == "shell":
        return [f"subprocess.run({action.get('cmd') or ''!r}, shell=True, check=True){tag}"]
    if kind == "python":
        return [f"subprocess.run([sys.executable, {action.get('script') or ''!r}], check=True){tag}"]
    if kind == "run":
        return [f"os.startfile({action.get('path') or ''!r}){tag}"]
    if kind == "raw":
        return [f"# 自定义任务：{str(action.get('json'))[:120]}"]
    return ["# 未知动作"]


def to_python(actions: list[dict], title: str = "pyauto 生成的脚本") -> str:
    lines = [
        f'"""{title} —— 由 pyauto studio 生成，改完直接运行。"""',
        "",
        "import os",
        "import subprocess",
        "import sys",
        "import time",
        "",
        "from pyauto import actions, imagefind, ocr",
        "",
        "",
        "def main() -> None:",
    ]
    if not actions:
        lines.append("    pass")
    for action in actions:
        if action.get("recover"):
            lines.append(f"    # ← 这一步配了 {len(action['recover'])} 条异常处理，"
                         "只在 JSON 任务里生效（studio/runner 会先处理再重试）")
        for line in _py_lines(action):
            lines.append("    " + line)
    lines += ["", "", 'if __name__ == "__main__":', "    main()", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 已有配置 -> 动作列表（打开别人的 tasks.json 也能改）
# --------------------------------------------------------------------------
def rule_from_config(rule: dict) -> dict:
    """runner 的 recover 规则 -> studio 的规则（打开已有配置时用）。"""
    when = rule.get("when") or {}
    if when.get("image"):
        cond = {"kind": "image", "template": str(when.get("image")),
                "threshold": float(when.get("threshold") or 0.88)}
    else:
        cond = {"kind": "ocr", "text": str(when.get("ocr") or ""), "exact": bool(when.get("exact"))}
    return {
        "name": str(rule.get("name") or ""),
        "when": cond,
        "then": [task_to_action(t) for t in (rule.get("then") or []) if isinstance(t, dict)],
        "then_action": str(rule.get("then_action") or "retry"),
    }


def task_to_action(task: dict) -> dict:
    desc = str(task.get("name") or "")
    ttype = task.get("type")
    if ttype == "sleep":
        return {"kind": "wait", "seconds": float(task.get("seconds", 1)), "desc": desc}
    if ttype == "input":
        act = str(task.get("action", "click")).lower()
        if act in ("type", "text"):
            return {"kind": "type", "value": task.get("value", ""), "desc": desc}
        if act in ("key", "hotkey"):
            return {"kind": "key", "value": task.get("value", ""), "desc": desc}
        return {"kind": "click", "x": task.get("x", 0), "y": task.get("y", 0),
                "double": act == "double", "desc": desc}
    if ttype == "ocr":
        return {"kind": "ocr", "expect": task.get("expect", ""),
                "box": task.get("box"), "click": bool(task.get("click")),
                "wait": task.get("sleep_before", 0), "desc": desc}
    if ttype == "image":
        return {"kind": "image", "template": task.get("template") or task.get("image") or "",
                "action": str(task.get("action") or "click"),
                "threshold": float(task.get("threshold") or 0.88),
                "wait": task.get("wait", 0), "box": task.get("box"), "desc": desc}
    if ttype == "shell":
        return {"kind": "shell", "cmd": task.get("cmd", ""), "desc": desc}
    if ttype == "python":
        return {"kind": "python", "script": task.get("script", ""), "desc": desc}
    return {"kind": "raw", "json": json.dumps(task, ensure_ascii=False), "desc": desc}


def tasks_to_actions(tasks: list[dict]) -> list[dict]:
    actions = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        action = task_to_action(task)
        rules = [rule_from_config(r) for r in (task.get("recover") or []) if isinstance(r, dict)]
        if rules:
            action["recover"] = rules
        actions.append(action)
    return actions


def rules_from_config(items: list[dict] | None) -> list[dict]:
    return [rule_from_config(r) for r in (items or []) if isinstance(r, dict)]


class _QueueWriter:
    def __init__(self, sink: "queue.Queue[str]") -> None:
        self.sink = sink

    def write(self, text: str) -> None:
        self.sink.put(text)

    def flush(self) -> None:
        pass


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------
class Studio(tk.Tk):
    def __init__(self, config_path: str | None = None) -> None:
        super().__init__()
        self.title("pyauto studio — 自动化脚本编辑器")
        self.geometry("1280x820")
        self.minsize(1040, 680)
        self.actions: list[dict] = []
        self.rules: list[dict] = []          # 全局异常处理规则
        self.current = -1
        self.vars: dict[str, tk.Variable] = {}
        self._form_frame: ttk.Frame | None = None
        self._loading = False
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.settings = _load_settings()
        self.path_var = tk.StringVar(value=config_path or "tasks.json")
        self.log_var = tk.StringVar(value="logs/studio.log")
        self.stop_var = tk.BooleanVar(value=True)
        self.schedule_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="就绪")
        self._busy = False
        self._cancel: threading.Event | None = None
        self._stopped = False
        self._hotkey_state: dict[str, bool] = {}

        _apply_theme(self)
        self._build_menu()
        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(150, self._drain_log)
        self.after(120, self._poll_hotkeys)

        if config_path and Path(config_path).exists():
            self.load_config(Path(config_path))
        else:
            self._refresh_preview()
        self.bind("<Control-s>", lambda e: self.save_config())
        self.bind("<Control-Return>", lambda e: self.run_all())
        # 窗口有焦点时用 Tk 自己的按键事件兜底（全局轮询在后台也管用）
        self.bind_all("<F9>", lambda e: self.start_run())
        self.bind_all("<F10>", lambda e: self.stop_run())

    # ---- 外观 ----
    def _init_style(self) -> None:
        _apply_theme(self)

    def _build_menu(self) -> None:
        bar = tk.Menu(self)
        m = tk.Menu(bar, tearoff=0)
        m.add_command(label="打开…", accelerator="Ctrl+O", command=self.open_config)
        m.add_command(label="保存", accelerator="Ctrl+S", command=self.save_config)
        m.add_command(label="另存为…", command=lambda: self.save_config(as_new=True))
        m.add_separator()
        m.add_command(label="导出 Python…", command=self.export_python)
        m.add_separator()
        m.add_command(label="退出", command=self.destroy)
        bar.add_cascade(label="文件", menu=m)

        run = tk.Menu(bar, tearoff=0)
        run.add_command(label=f"开始（{self.settings['hotkey_start']}）", command=self.start_run)
        run.add_command(label=f"停止（{self.settings['hotkey_stop']}）", command=self.stop_run)
        run.add_separator()
        run.add_command(label="试跑全部", accelerator="Ctrl+Enter", command=self.run_all)
        run.add_command(label="只跑选中动作", command=self.run_current)
        bar.add_cascade(label="运行", menu=run)

        tools = tk.Menu(bar, tearoff=0)
        tools.add_command(label="快捷键…", command=self.open_hotkey_settings)
        tools.add_command(label="全局异常处理…", command=lambda: self.open_rules(None))
        bar.add_cascade(label="设置", menu=tools)
        self.config(menu=bar)

    # ---- 布局 ----
    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(16, 12, 16, 4))
        header.pack(fill="x")
        title_box = ttk.Frame(header)
        title_box.pack(side="left")
        ttk.Label(title_box, text="pyauto studio", style="H1.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="排好动作，实时生成 JSON 任务和 Python 脚本",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        actions = ttk.Frame(header)
        actions.pack(side="right")
        self.btn_start = ttk.Button(actions, text=self._hotkey_label(self.settings["hotkey_start"], "▶ 开始"),
                                    style="Accent.TButton", command=self.start_run)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(actions, text=self._hotkey_label(self.settings["hotkey_stop"], "■ 停止"),
                                   style="Danger.TButton", command=self.stop_run)
        self.btn_stop.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="快捷键…", style="Ghost.TButton",
                   command=self.open_hotkey_settings).pack(side="left", padx=(8, 0))

        bar = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        bar.pack(fill="x", padx=16, pady=(8, 8))
        inner = ttk.Frame(bar, style="Card.TFrame", padding=(12, 9))
        inner.pack(fill="x")
        ttk.Label(inner, text="任务文件", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(inner, textvariable=self.path_var, width=28).pack(side="left", padx=(6, 14))
        ttk.Label(inner, text="日志", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(inner, textvariable=self.log_var, width=20).pack(side="left", padx=(6, 14))
        ttk.Label(inner, text="定时", style="CardMuted.TLabel").pack(side="left")
        ttk.Entry(inner, textvariable=self.schedule_var, width=16).pack(side="left", padx=(6, 14))
        ttk.Checkbutton(inner, text="出错就停", variable=self.stop_var,
                        style="Card.TCheckbutton").pack(side="left")
        self.btn_rules = ttk.Button(inner, text="异常处理（全局）", style="Ghost.TButton",
                                    command=lambda: self.open_rules(None))
        self.btn_rules.pack(side="right")
        for var in (self.log_var, self.stop_var):
            var.trace_add("write", lambda *_: self._refresh_preview())

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.body = body

        # 左：动作列表
        left = ttk.Frame(body)
        body.add(left, weight=1)

        palette = ttk.LabelFrame(left, text="加动作", padding=8)
        palette.pack(fill="x")
        self._palette_grid = [
            ("点击", "click"), ("输入", "type"), ("按键", "key"),
            ("找字", "ocr"), ("找图", "image"), ("等待", "wait"),
            ("命令", "shell"), ("脚本", "python"), ("程序", "run"),
            ("自定义", "raw"),
        ]
        for i, (label, kind) in enumerate(self._palette_grid):
            ttk.Button(palette, text=label, width=7,
                       command=lambda k=kind: self.add_action(k)).grid(
                row=i // 3, column=i % 3, padx=2, pady=2, sticky="ew")
        for col in range(3):
            palette.columnconfigure(col, weight=1)

        list_frame = ttk.LabelFrame(left, text="动作顺序", padding=8)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.listbox = tk.Listbox(list_frame, activestyle="none", exportselection=False,
                                  font=FONT, height=12, bg=CARD, fg=TEXT,
                                  selectbackground=ACCENT_SOFT, selectforeground=ACCENT_DARK,
                                  relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                  highlightcolor=BORDER, bd=0)
        self.listbox.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._on_select())

        tools = ttk.Frame(left)
        tools.pack(fill="x", pady=(6, 0))
        for text, cmd in (("↑", lambda: self.move_action(-1)), ("↓", lambda: self.move_action(1)),
                          ("复制", self.duplicate_action), ("删除", self.delete_action)):
            ttk.Button(tools, text=text, width=5, command=cmd).pack(side="left", padx=2)

        # 中：参数
        middle = ttk.LabelFrame(body, text="参数", padding=10)
        body.add(middle, weight=2)
        self._form_host = middle
        hint = ttk.Label(middle, text="左边加一个动作，这里填参数。", style="Muted.TLabel")
        hint.pack(anchor="w")
        self._hint = hint

        # 右：代码预览
        right = ttk.Frame(body)
        body.add(right, weight=2)
        card = tk.Frame(right, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(card)
        self.nb.pack(fill="both", expand=True, padx=1, pady=1)
        self.json_text = self._make_code_tab("JSON 任务配置")
        self.py_text = self._make_code_tab("Python 脚本")

        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="保存 JSON", command=self.save_config).pack(side="left")
        ttk.Button(buttons, text="导出 Python", command=self.export_python).pack(side="left", padx=6)
        ttk.Button(buttons, text="▶ 试跑全部", style="Accent.TButton",
                   command=self.run_all).pack(side="right")
        ttk.Button(buttons, text="只跑选中", command=self.run_current).pack(side="right", padx=6)

        # 底部：输出
        bottom = ttk.LabelFrame(self, text="输出", padding=(8, 4))
        bottom.pack(fill="both", padx=16, pady=(0, 8))
        self.out = tk.Text(bottom, height=8, font=FONT_MONO, wrap="word",
                           bg=CONSOLE_BG, fg=CONSOLE_FG, insertbackground=CONSOLE_FG,
                           relief="flat", bd=0, padx=10, pady=8)
        self.out.pack(fill="both", expand=True, side="left")
        osb = ttk.Scrollbar(bottom, orient="vertical", command=self.out.yview)
        osb.pack(side="right", fill="y")
        self.out.config(yscrollcommand=osb.set, state="disabled")
        self.out.tag_configure("ok", foreground="#4ade80")
        self.out.tag_configure("bad", foreground="#f87171")

        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", anchor="w").pack(
            fill="x", padx=18, pady=(0, 6))
        self.after(80, self._init_sashes)
        self._refresh_rule_buttons()

    def _init_sashes(self) -> None:
        """给三栏一个顺眼的初始宽度：列表窄、参数中等、代码宽。"""
        try:
            total = self.body.winfo_width()
        except tk.TclError:
            return
        if total < 700:  # 窗口还没真正铺开，等它
            self.after(120, self._init_sashes)
            return
        try:
            left = 208
            middle = max(380, int((total - left) * 0.42))
            self.body.sashpos(0, left)
            self.body.sashpos(1, min(left + middle, total - 260))
        except tk.TclError:
            pass

    def _make_code_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.nb)
        self.nb.add(frame, text=title)
        text = tk.Text(frame, font=FONT_MONO, wrap="none", undo=False,
                       bg="#fbfcff", fg=TEXT, relief="flat", bd=0,
                       padx=12, pady=8, insertbackground=TEXT, highlightthickness=0)
        text.pack(fill="both", expand=True, side="left")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        scroll.pack(side="right", fill="y")
        text.config(yscrollcommand=scroll.set)
        return text

    # ---- 动作编辑 ----
    def add_action(self, kind: str) -> None:
        action = json.loads(json.dumps(DEFAULT_ACTION[kind]))
        self.actions.append(action)
        self._reload_list(select=len(self.actions) - 1)

    def delete_action(self) -> None:
        if self.current < 0:
            return
        del self.actions[self.current]
        self._reload_list(select=min(self.current, len(self.actions) - 1))

    def duplicate_action(self) -> None:
        if self.current < 0:
            return
        self.actions.insert(self.current + 1, json.loads(json.dumps(self.actions[self.current])))
        self._reload_list(select=self.current + 1)

    def move_action(self, delta: int) -> None:
        i = self.current
        j = i + delta
        if i < 0 or not (0 <= j < len(self.actions)):
            return
        self.actions[i], self.actions[j] = self.actions[j], self.actions[i]
        self._reload_list(select=j)

    def _reload_list(self, select: int | None = None) -> None:
        self.listbox.delete(0, "end")
        for i, action in enumerate(self.actions):
            self.listbox.insert("end", _title(action, i))
        if self.actions:
            index = select if select is not None and select >= 0 else 0
            index = min(index, len(self.actions) - 1)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(index)
            self.listbox.see(index)
            self.current = index
            self._build_form(self.actions[index])
        else:
            self.current = -1
            self._clear_form()
        self._refresh_preview()

    def _on_select(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        index = int(sel[0])
        if index == self.current:
            return
        self.current = index
        self._build_form(self.actions[index])

    def _clear_form(self) -> None:
        for w in self._form_host.winfo_children():
            if w is not self._hint:
                w.destroy()
        self._hint.pack(anchor="w")
        self.vars.clear()

    # ---- 参数表单 ----
    def _build_form(self, action: dict) -> None:
        self._clear_form()
        self._hint.pack_forget()
        self.vars.clear()
        frame = ttk.Frame(self._form_host)
        frame.pack(fill="both", expand=True)
        self._form_frame = frame

        def row(r: int, label: str, widget_builder) -> tk.Widget:
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="w", pady=4)
            widget = widget_builder(frame)
            widget.grid(row=r, column=1, sticky="ew", pady=4, padx=(6, 0))
            return widget

        def entry(key: str, cast=str, width: int = 26) -> tk.Entry:
            var = tk.StringVar(value=str(action.get(key, "") if action.get(key) is not None else ""))
            self.vars[key] = var

            def write(*_: object) -> None:
                try:
                    action[key] = cast(var.get()) if var.get() != "" else (0 if cast is int else "")
                except ValueError:
                    self.status_var.set(f"{key} 只能是数字")
                    return
                self._touch()

            var.trace_add("write", write)
            return ttk.Entry(frame, textvariable=var, width=width)

        kind = action.get("kind")
        frame.columnconfigure(1, weight=1)

        if kind == "click":
            row(0, "X", lambda f: entry("x", int, 10))
            row(1, "Y", lambda f: entry("y", int, 10))
            pick = ttk.Frame(frame)
            pick.grid(row=2, column=1, sticky="w", pady=2)
            ttk.Button(pick, text="3 秒后拾取鼠标位置",
                       command=lambda: self.pick_point(action)).pack(side="left")
            dbl = tk.BooleanVar(value=bool(action.get("double")))
            self.vars["double"] = dbl
            ttk.Checkbutton(pick, text="双击", variable=dbl,
                            command=lambda: (action.__setitem__("double", dbl.get()),
                                             self._touch())).pack(side="left", padx=8)
        elif kind in ("type", "key"):
            row(0, "内容" if kind == "type" else "组合键",
                lambda f: entry("value", str, 40))
            if kind == "key":
                ttk.Label(frame, text="例：ctrl+s、enter、alt+tab", foreground="#777").grid(
                    row=1, column=1, sticky="w")
        elif kind == "ocr":
            row(0, "要找的字", lambda f: entry("expect", str, 36))
            self._box_area(frame, 1, action)

            click_var = tk.BooleanVar(value=bool(action.get("click", True)))
            self.vars["click"] = click_var
            ttk.Checkbutton(frame, text="找到就点它", variable=click_var,
                            command=lambda: (action.__setitem__("click", click_var.get()),
                                             self._touch())).grid(
                row=2, column=1, sticky="w", pady=(2, 6))

            wait_row = ttk.Frame(frame)
            wait_row.grid(row=3, column=0, columnspan=2, sticky="w")
            ttk.Label(wait_row, text="点之前等几秒").pack(side="left")
            wait_var = tk.StringVar(value=str(action.get("wait") or 0))
            self.vars["wait"] = wait_var

            def set_wait(*_: object) -> None:
                try:
                    value = float(wait_var.get() or 0)
                except ValueError:
                    self.status_var.set("等待秒数只能是数字")
                    return
                action["wait"] = value
                self._touch()

            wait_var.trace_add("write", set_wait)
            ttk.Entry(wait_row, textvariable=wait_var, width=8).pack(side="left", padx=(6, 0))
        elif kind == "image":
            row(0, "模板图片", lambda f: entry("template", str, 30))
            ttk.Button(frame, text="浏览…", command=lambda: self._pick_template(action)).grid(
                row=0, column=2, padx=(4, 0))

            mode_row = ttk.Frame(frame)
            mode_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Label(mode_row, text="找到后").pack(side="left")
            mode_display = {"点到中心": "click", "双击中心": "double", "只判断": "check"}
            mode_var = tk.StringVar(value={v: k for k, v in mode_display.items()}.get(
                str(action.get("action") or "click"), "点到中心"))
            self.vars["mode"] = mode_var
            combo = ttk.Combobox(mode_row, textvariable=mode_var, width=10, state="readonly",
                                 values=list(mode_display))
            combo.pack(side="left", padx=(6, 14))
            combo.bind("<<ComboboxSelected>>",
                       lambda e: (action.__setitem__("action", mode_display[mode_var.get()]),
                                  self._touch()))

            ttk.Label(mode_row, text="阈值").pack(side="left")
            thr_var = tk.StringVar(value=str(action.get("threshold") or 0.88))
            self.vars["threshold"] = thr_var

            def set_threshold(*_: object) -> None:
                try:
                    action["threshold"] = float(thr_var.get() or 0.88)
                except ValueError:
                    self.status_var.set("阈值只能是小数，例如 0.88")
                    return
                self._touch()

            thr_var.trace_add("write", set_threshold)
            ttk.Entry(mode_row, textvariable=thr_var, width=6).pack(side="left", padx=(6, 0))

            self._box_area(frame, 2, action, grab_template=True)

            wrow = ttk.Frame(frame)
            wrow.grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))
            ttk.Label(wrow, text="最多等几秒（0 = 只找一次）").pack(side="left")
            wait_var2 = tk.StringVar(value=str(action.get("wait") or 0))
            self.vars["wait"] = wait_var2

            def set_wait2(*_: object) -> None:
                try:
                    action["wait"] = float(wait_var2.get() or 0)
                except ValueError:
                    self.status_var.set("等待秒数只能是数字")
                    return
                self._touch()

            wait_var2.trace_add("write", set_wait2)
            ttk.Entry(wrow, textvariable=wait_var2, width=8).pack(side="left", padx=(6, 0))
        elif kind == "wait":
            row(0, "秒数", lambda f: entry("seconds", float, 10))
        elif kind == "shell":
            row(0, "命令", lambda f: entry("cmd", str, 46))
        elif kind == "python":
            row(0, "脚本路径", lambda f: entry("script", str, 46))
            ttk.Button(frame, text="选文件…",
                       command=lambda: self._pick_script(action)).grid(
                row=0, column=2, padx=4)
        elif kind == "run":
            row(0, "程序路径", lambda f: entry("path", str, 46))
            ttk.Button(frame, text="选文件…",
                       command=lambda: self._pick_program(action)).grid(
                row=0, column=2, padx=4)
        elif kind == "raw":
            ttk.Label(frame, text="直接写 pyauto 任务 JSON（对象）").grid(
                row=0, column=0, columnspan=2, sticky="w")
            text = tk.Text(frame, height=8, font=FONT_MONO, wrap="none")
            text.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=4)
            frame.rowconfigure(1, weight=1)
            text.insert("1.0", action.get("json", "{}"))
            self._raw_text = text

            def on_edit(*_: object) -> None:
                action["json"] = text.get("1.0", "end-1c")
                self._touch()

            text.bind("<KeyRelease>", on_edit)

        ttk.Label(frame, text="说明（会变成任务名）").grid(row=10, column=0, sticky="w", pady=(10, 0))
        desc_var = tk.StringVar(value=action.get("desc", ""))
        self.vars["desc"] = desc_var
        desc_var.trace_add("write", lambda *_: (
            action.__setitem__("desc", desc_var.get()), self._touch()))
        ttk.Entry(frame, textvariable=desc_var, width=34).grid(
            row=10, column=1, sticky="ew", pady=(10, 0), padx=(6, 0))

        rules_row = ttk.Frame(frame)
        rules_row.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        ttk.Separator(rules_row, orient="horizontal").grid(row=0, column=0, columnspan=2,
                                                           sticky="ew", pady=(0, 8))
        count = len(action.get("recover") or [])
        self.btn_action_rules = ttk.Button(
            rules_row, text=f"这一步的异常处理…（{count} 条）",
            command=lambda: self.open_rules(action))
        self.btn_action_rules.grid(row=1, column=0, sticky="w")
        ttk.Label(rules_row, text="跑的时候先看这些画面，先处理再重试本步",
                  style="Muted.TLabel").grid(row=2, column=0, columnspan=2,
                                             sticky="w", pady=(6, 0))

    # ---- 查找区域控件（找字/找图共用） ----
    def _box_area(self, frame: ttk.Frame, row: int, action: dict,
                  grab_template: bool = False) -> None:
        box = action.get("box") or [0, 0, 0, 0]
        area = ttk.LabelFrame(frame, text="查找区域（0 = 整屏）", padding=8)
        area.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 6), padx=(0, 6))
        self.box_vars = []
        for i, name in enumerate(("x", "y", "宽", "高")):
            var = tk.StringVar(value=str(box[i] if i < len(box) else 0))
            self.box_vars.append(var)
            ttk.Label(area, text=name).grid(row=i // 2, column=(i % 2) * 2,
                                            sticky="e", padx=(0, 2), pady=2)
            ttk.Entry(area, textvariable=var, width=7).grid(
                row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 12), pady=2)

        def apply_box(*_: object) -> None:
            try:
                action["box"] = [int(v.get() or 0) for v in self.box_vars]
            except ValueError:
                self.status_var.set("区域只能是数字")
                return
            self._touch()

        for var in self.box_vars:
            var.trace_add("write", apply_box)

        buttons = ttk.Frame(area)
        buttons.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Button(buttons, text="框选屏幕区域",
                   command=lambda: self.pick_region(action)).pack(side="left")
        if grab_template:
            ttk.Button(buttons, text="截取这片当模板", style="Accent.TButton",
                       command=lambda: self._grab_template(action)).pack(side="left", padx=(8, 0))

    def _pick_template(self, action: dict) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
        if path:
            action["template"] = path
            if "template" in self.vars:
                self.vars["template"].set(path)
            self._touch()

    def _grab_template(self, action: dict) -> None:
        def after(box: list[int]) -> None:
            try:
                from pyauto import imagefind
                folder = Path("templates")
                folder.mkdir(exist_ok=True)
                out = folder / f"tpl_{time.strftime('%m%d_%H%M%S')}.png"
                imagefind.screen_image(box).save(out)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("截取失败", str(exc))
                return
            action["template"] = str(out).replace("\\", "/")
            if "template" in self.vars:
                self.vars["template"].set(action["template"])
            self.status_var.set(f"模板已保存：{out}（{box[2]}×{box[3]}）")
            self._touch()

        self.pick_region(action, on_done=after)

    def _pick_script(self, action: dict) -> None:
        path = filedialog.askopenfilename(filetypes=[("Python", "*.py"), ("所有文件", "*.*")])
        if path:
            action["script"] = path
            self.vars["script"].set(path)
            self._touch()

    def _pick_program(self, action: dict) -> None:
        path = filedialog.askopenfilename(filetypes=[("程序", "*.exe"), ("所有文件", "*.*")])
        if path:
            action["path"] = path
            self.vars["path"].set(path)
            self._touch()

    def _touch(self) -> None:
        """参数变了：刷新列表文字和右侧预览。"""
        if self._loading:
            return
        if 0 <= self.current < len(self.actions):
            self.listbox.delete(self.current)
            self.listbox.insert(self.current, _title(self.actions[self.current], self.current))
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(self.current)
        self._refresh_preview()

    # ---- 屏幕拾取 ----
    def pick_point(self, action: dict) -> None:
        self.status_var.set("3 秒内把鼠标移到目标位置…")
        for remaining in (3, 2, 1):
            self.status_var.set(f"把鼠标移到目标位置：{remaining}")
            self.update_idletasks()
            time.sleep(0.7)
        try:
            import ctypes
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            action["x"], action["y"] = point.x, point.y
            self.vars["x"].set(str(point.x))
            self.vars["y"].set(str(point.y))
            self.status_var.set(f"已拾取 ({point.x}, {point.y})")
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"拾取失败：{exc}")
        self._touch()

    def pick_region(self, action: dict, on_done=None) -> None:
        self.status_var.set("按住左键拖一个框，Esc 取消")
        self.withdraw()
        overlay = tk.Toplevel(self)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-alpha", 0.3)
        overlay.configure(bg="black")
        overlay.config(cursor="crosshair")
        canvas = tk.Canvas(overlay, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        state: dict = {"x0": 0, "y0": 0, "px": 0, "py": 0, "rect": None, "pressed": False}

        def on_press(event: tk.Event) -> None:
            state["x0"], state["y0"] = event.x_root, event.y_root
            state["px"], state["py"] = event.x, event.y
            state["pressed"] = True
            if state["rect"]:
                canvas.delete(state["rect"])
            state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                    outline="#00e5ff", width=2)

        def on_drag(event: tk.Event) -> None:
            if state["rect"]:
                canvas.coords(state["rect"], state["px"], state["py"], event.x, event.y)

        def finish(event: tk.Event | None) -> None:
            overlay.destroy()
            self.deiconify()
            if not state["pressed"]:
                self.status_var.set("已取消")
                return
            x1, y1 = state["x0"], state["y0"]
            x2, y2 = (event.x_root, event.y_root) if event else (x1, y1)
            box = [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]
            if box[2] > 4 and box[3] > 4:
                action["box"] = box
                for var, value in zip(self.box_vars, box):
                    var.set(str(value))
                self.status_var.set(f"已框选 {box[2]}×{box[3]} @ ({box[0]}, {box[1]})")
                self._touch()
                if on_done is not None:
                    on_done(box)
            else:
                self.status_var.set("没框选到有效区域")

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", finish)
        overlay.bind("<Escape>", lambda e: (overlay.destroy(), self.deiconify(),
                                            self.status_var.set("已取消")))
        overlay.focus_force()

    # ---- 预览 ----
    def _refresh_preview(self) -> None:
        try:
            json_text = to_json_text(self.actions, log=self.log_var.get(),
                                     stop_on_error=self.stop_var.get(),
                                     schedule=self.schedule_var.get(), rules=self.rules)
        except Exception as exc:  # noqa: BLE001
            json_text = f"# 配置有问题：{exc}"
        self._set_text(self.json_text, json_text)
        self._set_text(self.py_text, to_python(self.actions))

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.config(state="normal")
        yview = widget.yview()[0]
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.config(state="normal")
        widget.yview_moveto(yview)

    # ---- 文件 ----
    def open_config(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("任务配置", "*.json"), ("所有文件", "*.*")])
        if path:
            self.load_config(Path(path))

    def load_config(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            messagebox.showerror("打不开", f"{path}\n{exc}")
            return
        self._loading = True
        try:
            self.actions = tasks_to_actions(data.get("tasks", []))
            self.rules = rules_from_config(data.get("recover"))
            self.log_var.set(data.get("log", "logs/studio.log"))
            self.stop_var.set(bool(data.get("stop_on_error", True)))
            self.schedule_var.set(data.get("schedule", ""))
            self.path_var.set(str(path))
            self.title(f"pyauto studio — {path.name}")
        finally:
            self._loading = False
        self._reload_list(select=0)
        self._refresh_rule_buttons()
        self.status_var.set(f"已加载 {path}（{len(self.actions)} 个动作）")

    def save_config(self, as_new: bool = False) -> None:
        target = Path(self.path_var.get() or "tasks.json")
        if as_new or not target.is_absolute():
            chosen = filedialog.asksaveasfilename(defaultextension=".json",
                                                  initialfile=target.name,
                                                  filetypes=[("任务配置", "*.json")])
            if not chosen:
                return
            target = Path(chosen)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            config = to_config(self.actions, log=self.log_var.get(),
                               stop_on_error=self.stop_var.get(),
                               schedule=self.schedule_var.get())
        except ValueError as exc:
            messagebox.showerror("配置有问题", str(exc))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.path_var.set(str(target))
        self.title(f"pyauto studio — {target.name}")
        self._log(f"已保存 {target}（{len(config['tasks'])} 个任务）\n")

    def export_python(self) -> None:
        chosen = filedialog.asksaveasfilename(defaultextension=".py",
                                              initialfile="auto_script.py",
                                              filetypes=[("Python", "*.py")])
        if not chosen:
            return
        Path(chosen).write_text(to_python(self.actions), encoding="utf-8")
        self._log(f"已导出 {chosen}\n")

    def _refresh_hotkey_buttons(self) -> None:
        if hasattr(self, "btn_start"):
            self.btn_start.config(text=self._hotkey_label(self.settings["hotkey_start"], "▶ 开始"))
        if hasattr(self, "btn_stop"):
            self.btn_stop.config(text=self._hotkey_label(self.settings["hotkey_stop"], "■ 停止"))

    # ---- 异常处理规则 ----
    def _refresh_rule_buttons(self) -> None:
        count = len(self.rules)
        if hasattr(self, "btn_rules"):
            self.btn_rules.config(text=f"异常处理（全局）  {count} 条" if count else "异常处理（全局）")
        if hasattr(self, "btn_action_rules") and 0 <= self.current < len(self.actions):
            action = self.actions[self.current]
            per = len(action.get("recover") or [])
            self.btn_action_rules.config(text=f"这一步的异常处理…（{per} 条）")

    def open_rules(self, action: dict | None) -> None:
        if action is None:
            rules = list(self.rules)
            title = "全局异常处理"
        else:
            rules = list(action.get("recover") or [])
            title = "这一步的异常处理"
        dialog = RuleDialog(self, rules, title)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        if action is None:
            self.rules = dialog.result
        else:
            action["recover"] = dialog.result
        self._reload_list(select=self.current)
        self._refresh_rule_buttons()
        self.status_var.set(f"{title}：{len(dialog.result)} 条规则")

    # ---- 快捷键 ----
    @staticmethod
    def _hotkey_label(spec: str, fallback: str) -> str:
        try:
            parse_hotkey(spec)
        except ValueError:
            return fallback
        return f"{fallback}  {spec.upper() if len(spec) <= 3 else spec}"

    def _poll_hotkeys(self) -> None:
        """全局轮询：窗口不在前台也响应。"""
        try:
            if self.grab_current() is not None:  # 弹窗录快捷键时别触发
                return
            for key in ("hotkey_start", "hotkey_stop"):
                try:
                    mods, vk = parse_hotkey(self.settings[key])
                except ValueError:
                    continue
                down = _key_down(vk) and all(_key_down(m) for m in mods)
                was = self._hotkey_state.get(key, False)
                self._hotkey_state[key] = down
                if down and not was:
                    if key == "hotkey_start":
                        if not self._busy:
                            self.status_var.set(f"按下 {self.settings[key].upper()}，开始")
                            self.start_run()
                    elif self._busy:
                        self.stop_run()
        finally:
            self.after(70, self._poll_hotkeys)

    def open_hotkey_settings(self) -> None:
        HotkeyDialog(self)

    # ---- 运行 ----
    def start_run(self) -> None:
        if self._busy:  # 快捷键和按钮可能同时触发
            return
        self._run(self.actions)

    def run_all(self) -> None:
        self.start_run()

    def run_current(self) -> None:
        if self.current < 0:
            self.status_var.set("先选一个动作")
            return
        self._run([self.actions[self.current]])

    def stop_run(self) -> None:
        if not self._busy or self._cancel is None:
            self.status_var.set("没在跑")
            return
        self._cancel.set()
        self.status_var.set(f"停止中…（按了 {self.settings['hotkey_stop'].upper()}，当前动作结束就停）")

    def _run(self, actions: list[dict], cancel: threading.Event | None = None) -> None:
        if self._busy:
            self.status_var.set("正在跑，先停止再开始")
            return
        try:
            config = to_config(actions, log=self.log_var.get(),
                               stop_on_error=self.stop_var.get(), rules=self.rules)
        except ValueError as exc:
            messagebox.showerror("配置有问题", str(exc))
            return
        self._busy = True
        self._stopped = False
        self._cancel = cancel or threading.Event()
        total = len(config["tasks"])
        self.status_var.set(f"运行中…（{self.settings['hotkey_stop'].upper()} 停止）")

        def worker() -> None:
            from pyauto import runner as runner_mod
            writer = _QueueWriter(self.log_queue)
            try:
                with redirect_stdout(writer):
                    results = runner_mod.run_config(config, base_dir=Path.cwd(), echo=True,
                                                    cancel=self._cancel)
                ok = sum(1 for r in results if r.ok)
                self._stopped = self._cancel.is_set()
                if self._stopped:
                    writer.write(f"\n已停止：完成 {ok}/{total}\n")
                else:
                    writer.write(f"\n结果：{ok}/{total} 成功\n")
            except Exception as exc:  # noqa: BLE001
                writer.write(f"\n运行出错：{type(exc).__name__}: {exc}\n")
            finally:
                self.log_queue.put("__DONE__")

        threading.Thread(target=worker, daemon=True).start()

    def _drain_log(self) -> None:
        try:
            while True:
                chunk = self.log_queue.get_nowait()
                if chunk == "__DONE__":
                    self._busy = False
                    self._cancel = None
                    self.status_var.set("已停止" if self._stopped else "就绪")
                    continue
                self._log(chunk)
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _log(self, text: str) -> None:
        self.out.config(state="normal")
        self.out.insert("end", text)
        self.out.see("end")
        self.out.config(state="disabled")


def _new_rule() -> dict:
    return {
        "when": {"kind": "image", "template": "", "text": "", "exact": False, "threshold": 0.88},
        "then": [],
        "then_action": "retry",
        "name": "",
    }


def _normalise_rule(rule: dict) -> dict:
    base = _new_rule()
    when = dict(base["when"])
    when.update(rule.get("when") or {})
    base["when"] = when
    base["then"] = [dict(item) for item in (rule.get("then") or [])]
    base["then_action"] = rule.get("then_action") or "retry"
    base["name"] = rule.get("name") or ""
    return base


THEN_LABEL = {"click": "点击", "wait": "等待", "key": "按键", "image": "找图", "type": "输入"}


class HotkeyDialog(tk.Toplevel):
    """设置「开始 / 停止」的全局快捷键。"""

    _KEYSYM = {
        "Return": "enter", "Escape": "esc", "space": "space", "Tab": "tab",
        "BackSpace": "backspace", "Delete": "delete", "Insert": "insert",
        "Home": "home", "End": "end", "Prior": "pageup", "Next": "pagedown",
        "Page_Up": "pageup", "Page_Down": "pagedown",
        "Left": "left", "Right": "right", "Up": "up", "Down": "down",
        "Print": "printscreen", "Pause": "pause", "Scroll_Lock": "scrolllock",
    }
    _IGNORE = {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
               "Super_L", "Super_R", "Win_L", "Win_R", "Caps_Lock"}

    def __init__(self, master: "Studio") -> None:
        super().__init__(master)
        self.app = master
        self.title("快捷键设置")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.transient(master)
        self.start_var = tk.StringVar(value=master.settings["hotkey_start"])
        self.stop_var = tk.StringVar(value=master.settings["hotkey_stop"])
        self._recording: str | None = None
        self._build()
        self.grab_set()
        self.bind("<KeyPress>", self._on_key)
        self.focus_force()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=16, pady=16)
        inside = ttk.Frame(card, padding=18)
        inside.pack(fill="both", expand=True)

        ttk.Label(inside, text="全局快捷键", style="H1.TLabel").pack(anchor="w")
        ttk.Label(inside, text="窗口在后台也生效。支持 F1-F12、字母、数字和 ctrl/alt/shift 组合。",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 14))

        for attr, label, text in (("start_var", "开始", "按这个键开始跑"),
                                  ("stop_var", "停止", "跑的时候按这个键停")):
            row = ttk.Frame(inside)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label, width=4).pack(side="left")
            ttk.Entry(row, textvariable=getattr(self, attr), width=18,
                      state="readonly").pack(side="left", padx=(4, 10))
            ttk.Button(row, text="按一下新键…", style="Ghost.TButton",
                       command=lambda a=attr: self._watch(a)).pack(side="left")
            ttk.Label(row, text=text, style="Muted.TLabel").pack(side="left", padx=10)

        self.hint = ttk.Label(inside, text="", style="Muted.TLabel")
        self.hint.pack(anchor="w", pady=(10, 0))

        footer = ttk.Frame(inside)
        footer.pack(fill="x", pady=(18, 0))
        ttk.Button(footer, text="恢复默认（F9 / F10）", style="Ghost.TButton",
                   command=self._reset).pack(side="left")
        ttk.Button(footer, text="保存", style="Accent.TButton",
                   command=self._save).pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right", padx=8)

    def _watch(self, attr: str) -> None:
        self._recording = attr
        self.hint.configure(text="现在按你想用的键…（Esc 取消）", foreground=ACCENT)

    def _reset(self) -> None:
        self.start_var.set(DEFAULT_SETTINGS["hotkey_start"])
        self.stop_var.set(DEFAULT_SETTINGS["hotkey_stop"])
        self.hint.configure(text="已恢复默认，记得保存", foreground=MUTED)

    def _on_key(self, event: tk.Event) -> None:
        if not self._recording:
            return
        keysym = event.keysym
        if keysym in self._IGNORE:
            return
        attr, self._recording = self._recording, None
        if keysym == "Escape":
            self.hint.configure(text="取消了", foreground=MUTED)
            return
        key = self._KEYSYM.get(keysym, keysym.lower() if len(keysym) == 1 else keysym.lower())
        state = int(event.state)
        mods = []
        if state & 0x0004:
            mods.append("ctrl")
        if state & 0x0001:
            mods.append("shift")
        if state & 0x0008 or state & 0x20000:
            mods.append("alt")
        spec = "+".join(mods + [key])
        try:
            parse_hotkey(spec)
        except ValueError as exc:
            self.hint.configure(text=f"{spec} 用不了：{exc}", foreground=DANGER)
            return
        getattr(self, attr).set(spec)
        self.hint.configure(text=f"已设为 {spec.upper() if len(spec) <= 3 else spec}，记得保存",
                            foreground=OK)

    def _save(self) -> None:
        start, stop = self.start_var.get().strip(), self.stop_var.get().strip()
        for label, spec in (("开始", start), ("停止", stop)):
            try:
                parse_hotkey(spec)
            except ValueError as exc:
                messagebox.showerror("快捷键不合法", f"{label}：{spec}\n{exc}")
                return
        if start.lower() == stop.lower():
            messagebox.showerror("快捷键重复", "开始和停止不能用同一个键")
            return
        self.app.settings["hotkey_start"] = start
        self.app.settings["hotkey_stop"] = stop
        _save_settings(self.app.settings)
        self.app._refresh_hotkey_buttons()
        self.app.status_var.set(f"快捷键：{start.upper()} 开始、{stop.upper()} 停止")
        self.destroy()


class RuleDialog(tk.Toplevel):
    """编辑一组异常处理规则：当出现某画面 → 先做这些 → 重试/跳过原动作。"""

    def __init__(self, master: "Studio", rules: list[dict], title: str) -> None:
        super().__init__(master)
        self.app = master
        self.title(title)
        self.geometry("900x640")
        self.minsize(780, 540)
        self.configure(bg=BG)
        self.transient(master)
        self.result: list[dict] | None = None
        self.rules = [_normalise_rule(r) for r in rules]
        self.index = -1
        self.then_index = -1
        self.when_kind_var = tk.StringVar(value="图片")
        self.when_text_var = tk.StringVar()
        self.when_threshold_var = tk.StringVar(value="0.88")
        self.exact_var = tk.BooleanVar(value=False)
        self.then_action_var = tk.StringVar(value="处理完重试本步")
        self._build()
        self._reload_rules(select=0 if self.rules else None)
        self.grab_set()

    # ---- 布局 ----
    def _build(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        body = ttk.PanedWindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="规则（从上往下，先匹配先用）", padding=8)
        body.add(left, weight=1)
        self.listbox = tk.Listbox(left, exportselection=False, font=FONT, borderwidth=0,
                                  highlightthickness=1, highlightbackground=BORDER,
                                  bg=CARD, selectbackground=ACCENT_SOFT,
                                  selectforeground=ACCENT_DARK)
        self.listbox.pack(fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._select_rule())
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="+ 新增", command=self.add_rule).pack(side="left")
        ttk.Button(row, text="删除", style="Danger.TButton",
                   command=self.delete_rule).pack(side="left", padx=6)

        right = ttk.Frame(body)
        body.add(right, weight=3)
        right.configure(padding=(12, 0, 0, 0))

        when = ttk.LabelFrame(right, text="① 当屏幕出现", padding=10)
        when.pack(fill="x")
        top = ttk.Frame(when)
        top.pack(fill="x")
        self.kind_box = ttk.Combobox(top, textvariable=self.when_kind_var, width=6,
                                     state="readonly", values=("图片", "文字"))
        self.kind_box.pack(side="left")
        self.kind_box.bind("<<ComboboxSelected>>", lambda e: self._on_kind())
        self.when_entry = ttk.Entry(top, textvariable=self.when_text_var)
        self.when_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.when_text_var.trace_add("write", lambda *_: self._sync_rule())
        self.browse_btn = ttk.Button(top, text="浏览…", style="Ghost.TButton",
                                     command=self._browse_image)
        self.browse_btn.pack(side="left")

        bottom = ttk.Frame(when)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, text="相似度阈值").pack(side="left")
        thr = ttk.Entry(bottom, textvariable=self.when_threshold_var, width=6)
        thr.pack(side="left", padx=(6, 14))
        self.when_threshold_var.trace_add("write", lambda *_: self._sync_rule())
        self.exact_check = ttk.Checkbutton(bottom, text="只认完全相同的文字",
                                           variable=self.exact_var)
        self.exact_check.pack(side="left")
        self.exact_var.trace_add("write", lambda *_: self._sync_rule())

        then = ttk.LabelFrame(right, text="② 先做这些（做完再回到原来的动作）", padding=10)
        then.pack(fill="both", expand=True, pady=(10, 0))
        palette = ttk.Frame(then)
        palette.pack(fill="x")
        for label, kind in (("点击", "click"), ("等待", "wait"),
                            ("按键", "key"), ("找图", "image")):
            ttk.Button(palette, text=f"+ {label}", style="Ghost.TButton",
                       command=lambda k=kind: self.add_then(k)).pack(side="left", padx=(0, 6))
        self.then_box = tk.Listbox(then, height=5, exportselection=False, font=FONT,
                                   borderwidth=0, highlightthickness=1,
                                   highlightbackground=BORDER, bg=CARD,
                                   selectbackground=ACCENT_SOFT, selectforeground=ACCENT_DARK)
        self.then_box.pack(fill="both", expand=True, pady=(8, 0))
        self.then_box.bind("<<ListboxSelect>>", lambda e: self._select_then())
        tools = ttk.Frame(then)
        tools.pack(fill="x", pady=(6, 0))
        ttk.Button(tools, text="↑", width=4,
                   command=lambda: self.move_then(-1)).pack(side="left")
        ttk.Button(tools, text="↓", width=4,
                   command=lambda: self.move_then(1)).pack(side="left", padx=4)
        ttk.Button(tools, text="删除这一步", style="Danger.TButton",
                   command=self.delete_then).pack(side="left", padx=4)

        self.then_form = ttk.LabelFrame(right, text="③ 选中那一步的参数", padding=10)
        self.then_form.pack(fill="x", pady=(10, 0))

        mode = ttk.Frame(right)
        mode.pack(fill="x", pady=(10, 0))
        ttk.Label(mode, text="处理完之后").pack(side="left")
        combo = ttk.Combobox(mode, textvariable=self.then_action_var, width=14,
                             state="readonly", values=("处理完重试本步", "处理完跳过本步"))
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", lambda e: self._sync_rule())

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(footer, text="保存", style="Accent.TButton",
                   command=self.save).pack(side="right", padx=8)

    # ---- 规则列表 ----
    def _rule_label(self, rule: dict, index: int) -> str:
        when = rule["when"]
        if when.get("kind") == "image":
            what = f"图片 {Path(when.get('template') or '?').name}"
        else:
            what = f"文字「{when.get('text') or '?'}」"
        return f"{index + 1}. {what} → {len(rule['then'])} 步"

    def _reload_rules(self, select: int | None = None) -> None:
        self.listbox.delete(0, "end")
        for i, rule in enumerate(self.rules):
            self.listbox.insert("end", self._rule_label(rule, i))
        if select is not None and self.rules:
            select = max(0, min(select, len(self.rules) - 1))
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(select)
            self.index = select
            self._load_rule(select)
        elif not self.rules:
            self.index = -1
            self.when_text_var.set("")
            self.then_box.delete(0, "end")
            self._clear_then_form()

    def add_rule(self) -> None:
        self.rules.append(_new_rule())
        self._reload_rules(select=len(self.rules) - 1)

    def delete_rule(self) -> None:
        if self.index < 0:
            return
        del self.rules[self.index]
        self._reload_rules(select=min(self.index, len(self.rules) - 1))

    def _select_rule(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        if self.index >= 0 and self.index < len(self.rules):
            self._sync_rule()
        self.index = int(sel[0])
        self._load_rule(self.index)

    def _load_rule(self, index: int) -> None:
        rule = self.rules[index]
        when = rule["when"]
        is_image = when.get("kind") == "image"
        self.when_kind_var.set("图片" if is_image else "文字")
        self.when_text_var.set(str(when.get("template") if is_image else when.get("text")) or "")
        self.when_threshold_var.set(str(when.get("threshold") or 0.88))
        self.exact_var.set(bool(when.get("exact")))
        self.browse_btn.configure(state="normal" if is_image else "disabled")
        self.exact_check.configure(state="normal" if not is_image else "disabled")
        self.when_entry.configure(state="normal")
        self.then_action_var.set("处理完跳过本步" if rule["then_action"] == "skip"
                                 else "处理完重试本步")
        self._reload_then()

    def _on_kind(self) -> None:
        if self.index < 0:
            return
        rule = self.rules[self.index]
        rule["when"]["kind"] = "image" if self.when_kind_var.get() == "图片" else "ocr"
        self.when_text_var.set("")
        self._load_rule(self.index)

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
        if path:
            self.when_text_var.set(path)

    def _sync_rule(self) -> None:
        if self.index < 0 or self.index >= len(self.rules):
            return
        rule = self.rules[self.index]
        when = rule["when"]
        if when.get("kind") == "image":
            when["template"] = self.when_text_var.get()
            try:
                when["threshold"] = float(self.when_threshold_var.get() or 0.88)
            except ValueError:
                pass
        else:
            when["text"] = self.when_text_var.get()
            when["exact"] = bool(self.exact_var.get())
        rule["then_action"] = "skip" if self.then_action_var.get() == "处理完跳过本步" else "retry"
        self.listbox.delete(self.index)
        self.listbox.insert(self.index, self._rule_label(rule, self.index))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(self.index)

    # ---- 先做这些 ----
    def _reload_then(self) -> None:
        self.then_box.delete(0, "end")
        if self.index < 0:
            return
        for i, item in enumerate(self.rules[self.index]["then"]):
            label = THEN_LABEL.get(item.get("kind", ""), item.get("kind", "?"))
            if item.get("kind") == "click":
                label += f" ({item.get('x', 0)}, {item.get('y', 0)})"
            elif item.get("kind") == "wait":
                label += f" {item.get('seconds', 0)} 秒"
            elif item.get("kind") in ("key", "type"):
                label += f" {item.get('value', '')}"
            elif item.get("kind") == "image":
                label += f" {Path(item.get('template') or '?').name}"
            self.then_box.insert("end", f"{i + 1}. {label}")
        if self.rules[self.index]["then"]:
            self.then_index = 0
            self.then_box.selection_set(0)
            self._build_then_form(self.rules[self.index]["then"][0])
        else:
            self.then_index = -1
            self._clear_then_form()

    def add_then(self, kind: str) -> None:
        if self.index < 0:
            self.status_hint("先选一条规则（左边）")
            return
        item = json.loads(json.dumps(DEFAULT_ACTION[kind]))
        self.rules[self.index]["then"].append(item)
        self._reload_then()
        self.then_index = len(self.rules[self.index]["then"]) - 1
        self.then_box.selection_clear(0, "end")
        self.then_box.selection_set(self.then_index)
        self._build_then_form(self.rules[self.index]["then"][self.then_index])

    def delete_then(self) -> None:
        if self.index < 0 or self.then_index < 0:
            return
        del self.rules[self.index]["then"][self.then_index]
        self._reload_then()

    def move_then(self, delta: int) -> None:
        if self.index < 0 or self.then_index < 0:
            return
        items = self.rules[self.index]["then"]
        target = self.then_index + delta
        if not 0 <= target < len(items):
            return
        items[self.then_index], items[target] = items[target], items[self.then_index]
        self.then_index = target
        self._reload_then()

    def _select_then(self) -> None:
        sel = self.then_box.curselection()
        if not sel or self.index < 0:
            return
        self.then_index = int(sel[0])
        self._build_then_form(self.rules[self.index]["then"][self.then_index])

    def _clear_then_form(self) -> None:
        for child in self.then_form.winfo_children():
            child.destroy()

    def _build_then_form(self, item: dict) -> None:
        self._clear_then_form()
        frame = self.then_form
        kind = item.get("kind")

        def number(key: str, label: str, cast=int) -> None:
            row = ttk.Frame(frame)
            row.pack(side="left", padx=(0, 14))
            ttk.Label(row, text=label).pack(side="left")
            var = tk.StringVar(value=str(item.get(key, 0)))

            def write(*_: object) -> None:
                try:
                    item[key] = cast(float(var.get() or 0)) if cast is int else float(var.get() or 0)
                except ValueError:
                    self.app.status_var.set(f"{label} 只能是数字")
                    return
                self._reload_then()

            var.trace_add("write", write)
            ttk.Entry(row, textvariable=var, width=7).pack(side="left", padx=(4, 0))

        def text(key: str, label: str, width: int = 26) -> None:
            row = ttk.Frame(frame)
            row.pack(side="left", padx=(0, 14))
            ttk.Label(row, text=label).pack(side="left")
            var = tk.StringVar(value=str(item.get(key, "")))
            var.trace_add("write", lambda *_: (item.__setitem__(key, var.get()), self._reload_then()))
            ttk.Entry(row, textvariable=var, width=width).pack(side="left", padx=(4, 0))

        if kind == "click":
            number("x", "X")
            number("y", "Y")
        elif kind == "wait":
            number("seconds", "秒数", float)
        elif kind in ("key", "type"):
            text("value", "内容", 24)
        elif kind == "image":
            text("template", "图片", 24)
            ttk.Button(frame, text="浏览…", style="Ghost.TButton",
                       command=lambda: self._pick_then_image(item)).pack(side="left")
        else:
            ttk.Label(frame, text="这个动作没有参数", style="Muted.TLabel").pack(side="left")

    def _pick_then_image(self, item: dict) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
        if path:
            item["template"] = path
            self._build_then_form(item)

    def status_hint(self, message: str) -> None:
        self.app.status_var.set(message)

    # ---- 保存 ----
    def save(self) -> None:
        if self.index >= 0:
            self._sync_rule()
        for i, rule in enumerate(self.rules, 1):
            try:
                rule_to_config(rule)
            except ValueError as exc:
                messagebox.showerror("第 %d 条规则有问题" % i, str(exc))
                return
        self.result = self.rules
        self.destroy()


def _report_crash() -> None:
    """启动/运行中炸了：写日志 + 弹窗，别让窗口一闪就没。"""
    import traceback

    text = traceback.format_exc()
    log = Path(__file__).resolve().parent.parent / "logs" / "studio-error.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text, encoding="utf-8")
    except OSError:
        log = None
    tail = text[-1500:]
    try:
        messagebox.showerror(
            "pyauto studio 出错了",
            f"{tail}\n\n日志：{log}" if log else tail,
        )
    except Exception:
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                (f"{tail}\n\n日志：{log}" if log else tail),
                "pyauto studio 出错了",
                0x10,
            )
        except Exception:
            print(text, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    config = args[0] if args else None
    try:
        app = Studio(config)
    except Exception:
        _report_crash()
        return 1
    try:
        app.mainloop()
    except Exception:
        _report_crash()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
