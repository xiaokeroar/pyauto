"""pyauto 的网页版编辑器：本地 HTTP 服务 + JSON API。

用法：
    python -m pyauto web              # 默认 http://127.0.0.1:8937/
    python -m pyauto web --port 9000  # 换端口
    python -m pyauto web --no-browser # 不自动开浏览器

只监听 127.0.0.1，不对外网开放。
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import imagefind as image_mod
from . import ocr as ocr_mod
from . import runner as runner_mod
from . import studio as studio_mod

WEB_DIR = Path(__file__).resolve().parent / "web"
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "tasks.json"
TEMPLATE_DIR = PROJECT_DIR / "templates"
DEFAULT_PORT = 8937

DEFAULT_CONFIG: dict = {
    "log": "logs/web.log",
    "history": "logs/history.jsonl",
    "stop_on_error": True,
    "timeout": 120,
    "tasks": [
        {"name": "打开记事本", "type": "shell", "cmd": "start notepad"},
        {"name": "等界面出来", "type": "sleep", "seconds": 1.2},
        {"name": "找一下同意按钮", "type": "ocr", "screen": True, "expect": "同意",
         "click": False, "name_cn": "识别「同意」但不点击"},
    ],
    "recover": [],
}


# ---------------------------------------------------------------- 运行状态
class RunState:
    """一次运行的状态 + 事件流（给 SSE 用）。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.events: list[dict] = []
        self.seq = 0
        self.status = "idle"            # idle | running | stopping
        self.cancel: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.summary: dict | None = None

    def emit(self, event: dict) -> None:
        with self.cond:
            self.seq += 1
            event = {**event, "seq": self.seq}
            self.events.append(event)
            if len(self.events) > 4000:      # 太长的运行只留尾巴
                self.events = self.events[-3000:]
            self.cond.notify_all()

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "seq": self.seq,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "summary": self.summary,
                "busy": self.status != "idle",
            }

    def wait_events(self, since: int, timeout: float = 20.0) -> list[dict]:
        with self.cond:
            if self.seq <= since:
                self.cond.wait(timeout)
            return [e for e in self.events if e["seq"] > since]


STATE = RunState()
RUN_LOCK = threading.Lock()

# 进程启动时刻：用来判断“代码改过但服务还是老的”
BOOT_TS = time.time()
_CODE_FILES = ("runner.py", "webserver.py", "studio.py", "imagefind.py", "ocr.py", "adb.py", "cli.py")


def code_stale() -> bool:
    """包的源代码有比当前进程更新的文件时返回 True（说明需要重启 pyauto web）。"""
    base = Path(__file__).resolve().parent
    for name in _CODE_FILES:
        try:
            if (base / name).stat().st_mtime > BOOT_TS:
                return True
        except OSError:
            continue
    return False


def _worker(config: dict, dry_run: bool) -> None:
    def on_log(level: str, msg: str, line: str) -> None:
        STATE.emit({"type": "log", "level": level, "msg": msg, "line": line,
                    "t": round(time.time(), 3)})

    try:
        results = runner_mod.run_config(
            config, base_dir=PROJECT_DIR, echo=False, dry_run=dry_run,
            cancel=STATE.cancel, on_log=on_log,
        )
        ok = sum(1 for r in results if r.ok)
        summary = {
            "total": len(results), "ok": ok, "failed": len(results) - ok,
            "dry_run": dry_run,
            "items": [{"name": r.name, "type": r.type, "ok": r.ok,
                       "seconds": round(r.seconds, 2), "error": r.error,
                       "output": (r.output or "").strip()[:400]} for r in results],
        }
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        summary = {"total": 0, "ok": 0, "failed": 1, "dry_run": dry_run,
                   "error": f"{type(exc).__name__}: {exc}", "items": []}
        STATE.emit({"type": "log", "level": "ERROR",
                    "msg": summary["error"], "line": summary["error"], "t": round(time.time(), 3)})
    finally:
        with STATE.lock:
            STATE.summary = summary
            STATE.status = "idle"
            STATE.finished_at = time.time()
            STATE.thread = None
        STATE.emit({"type": "done", "summary": summary, "t": round(time.time(), 3)})


def start_run(config: dict, dry_run: bool = False) -> dict:
    if not isinstance(config, dict) or not isinstance(config.get("tasks"), list):
        raise ValueError("配置里要有 tasks 数组")
    with RUN_LOCK:
        with STATE.lock:
            if STATE.status != "idle":
                raise RuntimeError("已经在跑了，先停一下")
            STATE.events = []
            STATE.summary = None
            STATE.status = "running"
            STATE.started_at = time.time()
            STATE.finished_at = None
            STATE.cancel = threading.Event()
            thread = threading.Thread(target=_worker, args=(config, dry_run), daemon=True)
            STATE.thread = thread
        thread.start()
    STATE.emit({"type": "status", "status": "running"})
    return STATE.snapshot()


def stop_run() -> dict:
    with STATE.lock:
        if STATE.status == "idle" or STATE.cancel is None:
            return {**STATE.snapshot(), "message": "没在跑"}
        STATE.status = "stopping"
        STATE.cancel.set()
    STATE.emit({"type": "status", "status": "stopping"})
    return STATE.snapshot()


# ---------------------------------------------------------------- 接口实现
def _screen_shot_bytes(box: list[int] | None = None) -> tuple[bytes, int, int]:
    """截屏（可裁剪），返回 PNG 字节 + 完整屏幕宽高。"""
    path = ocr_mod.grab_screen()
    if path is None:
        raise RuntimeError("截屏失败：需要 Pillow（pip install pillow）")
    try:
        from PIL import Image  # 延迟导入，Pillow 缺失时上面的 grab_screen 已经报错
        img = Image.open(path).convert("RGB")
        full = img.size
        if box:
            x, y, w, h = (int(v) for v in box)
            img = img.crop((x, y, x + w, y + h))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), full[0], full[1]
    finally:
        Path(path).unlink(missing_ok=True)


def api_meta() -> dict:
    info: dict = {
        "version": __version__,
        "project_dir": str(PROJECT_DIR),
        "config_path": str(DEFAULT_CONFIG_PATH),
        "config_exists": DEFAULT_CONFIG_PATH.exists(),
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "hotkeys": {"start": "F9", "stop": "F10"},
        "started_at": round(BOOT_TS, 3),
        "stale": code_stale(),
    }
    try:
        settings = studio_mod._load_settings()  # noqa: SLF001 - 同一个包里的设置
        info["hotkeys"] = {"start": settings.get("hotkey_start", "F9"),
                           "stop": settings.get("hotkey_stop", "F10")}
    except Exception:  # noqa: BLE001
        pass
    try:
        info["ocr"] = {"ok": True, "langs": ocr_mod.windows_languages()}
    except Exception as exc:  # noqa: BLE001
        info["ocr"] = {"ok": False, "error": str(exc)}
    try:
        import PIL
        info["pillow"] = PIL.__version__
    except Exception:  # noqa: BLE001
        info["pillow"] = None
    return info


def _config_target(path: str | None) -> Path:
    """把「文件名」或「工程根下的绝对路径」统一成一个要读写的文件。"""
    text = str(path or "").strip()
    if not text:
        return DEFAULT_CONFIG_PATH
    target = Path(text)
    if target.is_absolute():
        resolved = target.resolve()
        try:
            resolved.relative_to(PROJECT_DIR.resolve())
        except ValueError:
            raise ValueError("只能读写工程目录下的任务文件") from None
        return resolved
    return _safe_config_path(text)


def api_load_config(path: str | None = None) -> dict:
    target = _config_target(path)
    if target.exists():
        return {"path": str(target), "config": runner_mod.load_config(target), "exists": True}
    return {"path": str(target), "config": DEFAULT_CONFIG, "exists": False}


def _safe_config_path(name: str) -> Path:
    """只允许工程根目录下的 .json 文件名，防止 ../ 之类的越界。"""
    raw = str(name or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("文件名不能为空")
    if "/" in raw:
        raise ValueError("文件名不能带路径，直接写个名字就行")
    if not raw.lower().endswith(".json"):
        raw += ".json"
    stem = raw[:-5].strip()
    if not stem:
        raise ValueError("文件名不能为空")
    bad = set('<>:"|?*') | {" "}
    if any(ch in bad for ch in stem) or stem.startswith("."):
        raise ValueError("文件名里有不能用的字符（<>:\"|?* 和空格）")
    return PROJECT_DIR / raw


def api_list_files(current: str | None = None) -> dict:
    try:
        current_path = _config_target(current)
    except ValueError:
        current_path = DEFAULT_CONFIG_PATH
    items = []
    for path in sorted(PROJECT_DIR.glob("*.json")):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        count = None
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            tasks = data.get("tasks") if isinstance(data, dict) else None
            count = len(tasks) if isinstance(tasks, list) else 0
        except (OSError, ValueError):
            count = None
        items.append({
            "name": path.name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "tasks": count,
            "current": path.resolve() == current_path.resolve(),
        })
    # 只列像任务文件的（有 tasks 数组），其它 json 是数据转储，不该出现在菜单里
    items = [item for item in items if item["tasks"] is not None]
    return {"items": items, "dir": str(PROJECT_DIR), "default": DEFAULT_CONFIG_PATH.name}


def api_new_file(payload: dict) -> dict:
    name = (payload or {}).get("name")
    target = _safe_config_path(name)
    if target.exists() and not payload.get("overwrite"):
        raise ValueError(f"{target.name} 已经存在了，换个名字或者直接切换过去")
    skeleton = {
        "log": "logs/web.log",
        "history": "logs/history.jsonl",
        "stop_on_error": True,
        "timeout": 60,
        "tasks": [],
        "recover": [],
    }
    target.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "name": target.name}


COMBOS_PATH = PROJECT_DIR / "combos.json"


def _read_combos() -> list[dict]:
    if not COMBOS_PATH.exists():
        return []
    try:
        data = json.loads(COMBOS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    items = data.get("combos") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        tasks = item.get("tasks")
        if not name or not isinstance(tasks, list) or not tasks:
            continue
        out.append({
            "name": name,
            "tasks": tasks,
            "created": str(item.get("created") or ""),
        })
    return out


def _write_combos(items: list[dict]) -> None:
    payload = {"combos": items}
    COMBOS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def api_combos() -> dict:
    return {"items": _read_combos(), "path": str(COMBOS_PATH)}


def api_save_combo(payload: dict) -> dict:
    name = str((payload or {}).get("name") or "").strip()
    tasks = (payload or {}).get("tasks")
    if not name:
        raise ValueError("组合得有个名字")
    if len(name) > 40:
        raise ValueError("名字太长了（40 个字以内）")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("组合里至少要有一个动作")
    items = _read_combos()
    entry = {"name": name, "tasks": tasks, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    for index, item in enumerate(items):
        if item["name"] == name:
            items[index] = entry
            break
    else:
        items.append(entry)
    _write_combos(items)
    return {"items": items, "saved": name}


def api_delete_combo(payload: dict) -> dict:
    name = str((payload or {}).get("name") or "").strip()
    items = [item for item in _read_combos() if item["name"] != name]
    _write_combos(items)
    return {"items": items, "deleted": name}


def api_save_config(config: dict, path: str | None = None) -> dict:
    if not isinstance(config, dict) or not isinstance(config.get("tasks"), list):
        raise ValueError("配置里要有 tasks 数组")
    target = _config_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "bytes": target.stat().st_size}


def api_preview(config: dict) -> dict:
    tasks = config.get("tasks") or []
    actions = studio_mod.tasks_to_actions(tasks)
    rules = studio_mod.rules_from_config(config.get("recover") or [])
    text = studio_mod.to_python(actions, title="pyauto 生成的脚本")
    return {"json": json.dumps(config, ensure_ascii=False, indent=2), "python": text,
            "steps": len(tasks), "rules": len(rules)}


def api_ocr(payload: dict) -> dict:
    box = payload.get("box")
    path = ocr_mod.grab_screen()
    if path is None:
        raise RuntimeError("截屏失败：需要 Pillow")
    try:
        res = ocr_mod.recognize(path, lang=payload.get("lang", "auto"),
                                box=tuple(int(v) for v in box) if box else None)
        lines = [{"text": ln.text, "x": ln.box.x, "y": ln.box.y,
                  "w": ln.box.w, "h": ln.box.h,
                  "cx": ln.box.cx, "cy": ln.box.cy} for ln in res.lines]
        return {"text": res.text, "lines": lines, "lang": res.lang,
                "engine": res.engine, "elapsed_ms": res.elapsed_ms}
    finally:
        Path(path).unlink(missing_ok=True)


def api_screenshot(payload: dict) -> dict:
    png, w, h = _screen_shot_bytes(payload.get("box"))
    return {"png": base64.b64encode(png).decode("ascii"), "screen": [w, h]}


def api_grab_template(payload: dict) -> dict:
    box = payload.get("box")
    if not box:
        raise ValueError("要传 box = [x, y, w, h]")
    name = str(payload.get("name") or "").strip() or time.strftime("tpl_%m%d_%H%M%S")
    if not name.lower().endswith(".png"):
        name += ".png"
    name = Path(name).name            # 只允许纯文件名
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMPLATE_DIR / name
    img = image_mod.screen_image([int(v) for v in box])
    img.save(out)
    return {"path": str(out.relative_to(PROJECT_DIR)).replace("\\", "/"),
            "size": list(img.size), "name": name}


def api_upload_template(payload: dict) -> dict:
    """收一张本地图片，转成 PNG 存进 templates/，返回相对路径。

    前端把文件读成 dataURL 传过来（data 可以带 `data:image/png;base64,` 前缀）。
    """
    data = str(payload.get("data") or payload.get("png") or "").strip()
    if not data:
        raise ValueError("要传 data（图片的 base64）")
    if data.startswith("data:") and "," in data:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"图片数据解不开：{exc}") from exc
    if len(raw) < 32:
        raise ValueError("图片是空的")

    name = str(payload.get("name") or "").strip() or time.strftime("tpl_%m%d_%H%M%S")
    stem = Path(Path(name).name).stem or time.strftime("tpl_%m%d_%H%M%S")

    try:
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            img = im.convert("RGB")
            size = list(img.size)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("这个文件读不出图片，换一张 PNG/JPG 试试") from exc

    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    out = TEMPLATE_DIR / f"{stem}.png"
    n = 2
    while out.exists():
        out = TEMPLATE_DIR / f"{stem}_{n}.png"
        n += 1
    img.save(out, "PNG")
    return {"path": str(out.relative_to(PROJECT_DIR)).replace("\\", "/"),
            "name": out.name, "size": size}


def api_templates() -> dict:
    items = []
    if TEMPLATE_DIR.exists():
        for p in sorted(TEMPLATE_DIR.glob("*.png")):
            size = None
            try:
                from PIL import Image
                with Image.open(p) as im:
                    size = list(im.size)
            except Exception:  # noqa: BLE001
                pass
            items.append({"name": p.name,
                          "path": str(p.relative_to(PROJECT_DIR)).replace("\\", "/"),
                          "size": size, "mtime": int(p.stat().st_mtime)})
    return {"items": items, "dir": str(TEMPLATE_DIR.relative_to(PROJECT_DIR))}


def api_template_image(name: str | None) -> tuple[bytes, str]:
    """把 templates/ 里的一张图原样吐出来，给前端做「点哪一处」的预览。"""
    text = str(name or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("要传 name（模板文件名）")
    target = (PROJECT_DIR / text).resolve() if "/" in text else (TEMPLATE_DIR / text).resolve()
    try:
        target.relative_to(TEMPLATE_DIR.resolve())
    except ValueError:
        raise ValueError("只能看 templates/ 里的图") from None
    if not target.is_file():
        raise FileNotFoundError(f"找不到模板：{target.name}")
    mime = "image/png"
    suffix = target.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        mime = "image/jpeg"
    elif suffix == ".webp":
        mime = "image/webp"
    elif suffix == ".bmp":
        mime = "image/bmp"
    return target.read_bytes(), mime


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "pyauto-web/" + __version__
    protocol_version = "HTTP/1.0"       # 短连接，SSE 靠关闭连接结束

    # ---- 工具 ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, exc: Exception, code: int = 400) -> None:
        self._json({"ok": False, "error": str(exc) or type(exc).__name__}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"请求体不是合法 JSON：{exc.msg}") from None
        if not isinstance(data, dict):
            raise ValueError("请求体要是 JSON 对象")
        return data

    def log_message(self, fmt: str, *args) -> None:  # 静音默认访问日志
        pass

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        qs = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        path = parsed.path

        if path.startswith("/api/"):
            try:
                if path == "/api/meta":
                    return self._json({"ok": True, **api_meta()})
                if path == "/api/config":
                    return self._json({"ok": True, **api_load_config(qs.get("path"))})
                if path == "/api/state":
                    return self._json({"ok": True, **STATE.snapshot()})
                if path == "/api/templates":
                    return self._json({"ok": True, **api_templates()})
                if path == "/api/template/image":
                    data, mime = api_template_image(qs.get("name"))
                    return self._send(200, data, mime)
                if path == "/api/files":
                    return self._json({"ok": True, **api_list_files(qs.get("path"))})
                if path == "/api/combos":
                    return self._json({"ok": True, **api_combos()})
                if path == "/api/events":
                    return self._stream(int(qs.get("since") or 0))
                return self._json({"ok": False, "error": f"没有这个接口：{path}"}, 404)
            except Exception as exc:  # noqa: BLE001
                return self._error(exc)
        return self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as exc:
            return self._error(exc)

        try:
            if path == "/api/config":
                return self._json({"ok": True, **api_save_config(body.get("config") or {},
                                                                 body.get("path"))})
            if path == "/api/file/new":
                return self._json({"ok": True, **api_new_file(body)})
            if path == "/api/combos":
                return self._json({"ok": True, **api_save_combo(body)})
            if path == "/api/combos/delete":
                return self._json({"ok": True, **api_delete_combo(body)})
            if path == "/api/preview":
                return self._json({"ok": True, **api_preview(body.get("config") or {})})
            if path == "/api/run":
                snap = start_run(body.get("config") or {}, bool(body.get("dry_run")))
                return self._json({"ok": True, **snap})
            if path == "/api/stop":
                return self._json({"ok": True, **stop_run()})
            if path == "/api/ocr":
                return self._json({"ok": True, **api_ocr(body)})
            if path == "/api/screenshot":
                return self._json({"ok": True, **api_screenshot(body)})
            if path == "/api/template":
                return self._json({"ok": True, **api_grab_template(body)})
            if path == "/api/template/upload":
                return self._json({"ok": True, **api_upload_template(body)})
            return self._json({"ok": False, "error": f"没有这个接口：{path}"}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._error(exc, 500 if not isinstance(exc, ValueError) else 400)

    # ---- 静态文件 ----
    def _static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._json({"ok": False, "error": "路径不合法"}, 403)
        if not target.is_file():
            return self._json({"ok": False, "error": f"没有这个文件：{rel}"}, 404)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # ---- SSE ----
    def _stream(self, since: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        try:
            head = {"type": "hello", **STATE.snapshot()}
            self._sse(head)
            cursor = since
            while True:
                events = STATE.wait_events(cursor, timeout=15.0)
                if not events:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                for event in events:
                    self._sse(event)
                    cursor = event["seq"]
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001
            return

    def _sse(self, event: dict) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()


# ---------------------------------------------------------------- 启动
def find_port(host: str = "127.0.0.1", port: int = DEFAULT_PORT, tries: int = 20) -> int:
    import socket
    for candidate in range(port, port + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"{host}:{port}-{port + tries - 1} 都被占用了")


def serve(port: int = DEFAULT_PORT, host: str = "127.0.0.1",
          open_browser: bool = True, config: str | None = None) -> None:
    if config:
        global DEFAULT_CONFIG_PATH
        p = Path(config)
        DEFAULT_CONFIG_PATH = p if p.is_absolute() else PROJECT_DIR / p

    actual = find_port(host, port)
    httpd = ThreadingHTTPServer((host, actual), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{actual}/"
    print(f"pyauto web 已启动：{url}")
    print(f"工程目录：{PROJECT_DIR}")
    print(f"任务文件：{DEFAULT_CONFIG_PATH}")
    print("按 Ctrl+C 结束。")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
