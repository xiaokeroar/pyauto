"""任务执行器：把 JSON 里描述的任务跑起来，带重试、超时、日志。"""

from __future__ import annotations

import json
import os
import random
import re
import shlex
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import ocr as ocr_mod
from . import adb as adb_mod
from . import imagefind as image_mod

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_RECOVER = 3          # 同一个任务最多被异常处理「救」几次，防死循环


class TaskCancelled(Exception):
    """收到停止请求（快捷键 / 事件），主动中断。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _decode(raw: bytes) -> str:
    """Windows 子进程输出编码不定：先按 UTF-8 试，再退回 GBK，最后兜底替换。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


@dataclass
class TaskResult:
    name: str
    type: str
    ok: bool
    seconds: float = 0.0
    attempts: int = 1
    rc: int | None = None
    output: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"time": _now(), "name": self.name, "type": self.type, "ok": self.ok,
                "seconds": round(self.seconds, 2), "attempts": self.attempts,
                "rc": self.rc, "error": self.error, **self.extra}


class Runner:
    """执行一组任务，控制台 + 文件双写日志。"""

    def __init__(self, base_dir: "str | Path | None" = None, log_file: "str | Path | None" = None,
                 echo: bool = True, cancel: "threading.Event | None" = None,
                 recover: Sequence[dict] | None = None, on_log=None,
                 recover_on_error: bool = True):
        self.base_dir = Path(base_dir or Path.cwd()).resolve()
        self.echo = echo
        self.cancel = cancel
        self.recover = list(recover or [])
        self.recover_on_error = recover_on_error
        self.on_log = on_log
        self.log_file = Path(log_file).resolve() if log_file else None
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise TaskCancelled("已手动停止")

    def _sleep(self, seconds: float) -> None:
        """可被「停止」打断的等待。"""
        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            self._check_cancel()
            left = deadline - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.1, left))

    # ---- 文本里的占位符 ----
    _VAR_RE = re.compile(r"\{(random|rand|now|date)(?::([^}]*))?\}", re.IGNORECASE)

    def _expand(self, value):
        """把文本里的 {random:1-5} / {now} / {date} 换成真的值；非字符串原样返回。"""
        if not isinstance(value, str) or "{" not in value:
            return value

        def repl(match: "re.Match[str]") -> str:
            kind = match.group(1).lower()
            arg = (match.group(2) or "").strip()
            if kind in ("random", "rand"):
                if "-" in arg:
                    left, _, right = arg.partition("-")
                    try:
                        lo, hi = int(float(left)), int(float(right))
                    except ValueError:
                        lo, hi = 1, 5
                else:
                    try:
                        lo, hi = 1, int(float(arg))
                    except ValueError:
                        lo, hi = 1, 5
                if hi < lo:
                    lo, hi = hi, lo
                return str(random.randint(lo, hi))
            fmt = arg or ("%Y-%m-%d %H:%M:%S" if kind == "now" else "%Y-%m-%d")
            try:
                return datetime.now().strftime(fmt)
            except ValueError:
                return datetime.now().strftime("%Y-%m-%d")

        return self._VAR_RE.sub(repl, value)

    # ---- 日志 ----
    def log(self, msg: str, level: str = "INFO") -> None:
        line = f"[{_now()}] {level:<7} {msg}"
        if self.echo:
            try:
                print(line, flush=True)
            except UnicodeEncodeError:  # 老控制台
                print(line.encode("gbk", "replace").decode("gbk", "replace"), flush=True)
        if self.log_file:
            with self.log_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        if self.on_log is not None:
            try:
                self.on_log(level, msg, line)
            except Exception:  # noqa: BLE001 - 回调不该拖垮任务
                pass

    # ---- 单任务 ----
    def run_task(self, task: dict, dry_run: bool = False,
                 allow_recover: bool = True) -> TaskResult:
        name = task.get("name") or task.get("type") or "task"
        ttype = (task.get("type") or "shell").lower()
        timeout = float(task.get("timeout", 300))
        retries = int(task.get("retries", 0))
        retry_delay = float(task.get("retry_delay", 2))

        if dry_run:
            self.log(f"[dry-run] {name} ({ttype}) -> {json.dumps(task, ensure_ascii=False)}")
            return TaskResult(name=name, type=ttype, ok=True, extra={"dry_run": True})

        attempt = 0
        recoveries = 0
        last: TaskResult | None = None
        while attempt <= retries:
            attempt += 1
            self._check_cancel()

            # 因果规则：屏幕上冒出别的画面就先处理，处理完再回到本步
            if allow_recover and recoveries < MAX_RECOVER:
                handled = self._recover(task)
                if handled is not None:
                    recoveries += 1
                    if handled.get("skip"):
                        self.log(f"SKIP {name}：{handled.get('what')}（按规则跳过本步）", "WARN")
                        return TaskResult(name=name, type=ttype, ok=True, attempts=attempt,
                                          output=f"已由异常处理跳过：{handled.get('what')}",
                                          extra={"recovered": True, "skipped": True})

            t0 = time.perf_counter()
            try:
                out, extra = self._dispatch(ttype, task, timeout)
                res = TaskResult(name=name, type=ttype, ok=True, attempts=attempt,
                                 seconds=time.perf_counter() - t0, output=out, extra=extra)
            except TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - 任务失败要吃掉异常继续
                res = TaskResult(name=name, type=ttype, ok=False, attempts=attempt,
                                 seconds=time.perf_counter() - t0, error=f"{type(exc).__name__}: {exc}")
                if task.get("traceback"):
                    res.error += "\n" + traceback.format_exc()
            last = res
            if res.ok:
                self.log(f"OK   {name} ({res.seconds:.2f}s)"
                         + (f" -> {res.output.strip()[:160]}" if res.output.strip() else ""))
                return res
            if attempt <= retries:
                self.log(f"RETRY {name} 第 {attempt} 次失败：{res.error}；{retry_delay}s 后重试", "WARN")
                time.sleep(retry_delay)
        assert last is not None
        self.log(f"FAIL {name} ({last.seconds:.2f}s) {last.error}", "ERROR")
        return last

    # ---- 重复块 ----
    # ---- 时间计划（repeat 的 at / every） ----
    _EVERY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|秒|m|min|mins|minute|minutes|分|分钟|h|hr|hour|hours|小时)?\s*$", re.IGNORECASE)
    _AT_RE = re.compile(r"^\s*(\d{1,2})\s*[:：]\s*(\d{2})(?:\s*[:：]\s*(\d{2}))?\s*(z|utc)?\s*$", re.IGNORECASE)

    def _parse_every(self, text) -> float:
        """把 "10m" / "30s" / "2h" / "90" 解析成秒。"""
        if isinstance(text, (int, float)):
            return float(text)
        match = self._EVERY_RE.match(str(text or ""))
        if not match:
            raise ValueError(f"看不懂的间隔：{text}（写成 30s / 10m / 2h 这样）")
        number = float(match.group(1))
        unit = (match.group(2) or "s").lower()
        if unit.startswith("h") or unit == "小时":
            return number * 3600
        if unit.startswith("m") or unit.startswith("分"):
            return number * 60
        return number

    def _parse_at(self, text) -> datetime:
        """把 "18:00" / "18:00:30" / "18:00Z" 解析成下一个该到的本地时间（已过就顺延到明天）。"""
        match = self._AT_RE.match(str(text or ""))
        if not match:
            raise ValueError(f"看不懂的开始时间：{text}（写成 18:00 这样，末尾加 Z 表示 UTC）")
        hour, minute = int(match.group(1)), int(match.group(2))
        second = int(match.group(3) or 0)
        is_utc = bool(match.group(4))
        if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError(f"时间超出范围：{text}")
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
        if is_utc:
            # 把它当成 UTC 的这个时刻，换算成本地时间
            utc_now = datetime.now(timezone.utc)
            utc_target = utc_now.replace(hour=hour, minute=minute, second=second, microsecond=0)
            if utc_target <= utc_now:
                utc_target = utc_target + timedelta(days=1)
            target = utc_target.astimezone().replace(tzinfo=None)
        elif target <= now:
            target = target + timedelta(days=1)
        return target

    def _wait_until(self, target: datetime, label: str) -> None:
        """等到某个时刻；期间可以被「停止」打断。"""
        delta = (target - datetime.now()).total_seconds()
        if delta <= 0:
            return
        if delta >= 60:
            self.log(f"等到 {target:%H:%M}（还有 {int(delta // 60)} 分 {int(delta % 60)} 秒）")
        else:
            self.log(f"等到 {target:%H:%M:%S}（还有 {int(delta)} 秒）")
        self._sleep(delta)

    def _run_repeat(self, task: dict, timeout: float) -> tuple[str, dict]:
        """把一组动作整体重复 N 次；也可以按时间计划（at / every）来跑。"""
        children = task.get("tasks") or task.get("steps") or []
        if not isinstance(children, list) or not children:
            raise ValueError("repeat 任务里要有个 tasks 数组（要重复执行的动作）")
        if task.get("at") or task.get("every"):
            return self._run_repeat_scheduled(task, children)
        times = int(task.get("times", task.get("count", 1)) or 1)
        times = max(1, min(times, 100000))
        interval = float(task.get("interval", 0) or 0)
        total = 0
        for round_no in range(1, times + 1):
            self._check_cancel()
            for child in children:
                if not isinstance(child, dict):
                    raise ValueError("repeat 里的每个动作都要写成对象")
                res = self.run_task(dict(child))
                total += 1
                if not res.ok:
                    raise RuntimeError(
                        f"第 {round_no}/{times} 轮、动作「{res.name}」失败：{res.error or ''}".strip()
                    )
            if interval > 0 and round_no < times:
                self._sleep(interval)
        steps = len(children)
        return (f"重复 {times} 轮 × {steps} 步，共执行 {total} 个动作",
                {"times": times, "steps": steps, "executed": total})

    def _run_repeat_scheduled(self, task: dict, children: list) -> tuple[str, dict]:
        """按 「开始时间 at + 间隔 every + 总次数 times」 来跑这一组动作。"""
        at = task.get("at")
        every_text = task.get("every")
        every = self._parse_every(every_text) if every_text else 0.0
        times = max(1, min(int(task.get("times", task.get("count", 1)) or 1), 100000))

        first = self._parse_at(at) if at else datetime.now()
        if at and first.date() != datetime.now().date():
            self.log(f"今天的 {at} 已经过了，顺延到 {first:%m-%d %H:%M}")
        plan = f"{at} 起" if at else "现在起"
        if every > 0:
            plan += f"，每 {self._human_seconds(every)} 一次"
        plan += f"，共 {times} 次"

        total = 0
        started = datetime.now()
        for round_no in range(1, times + 1):
            self._check_cancel()
            target = first + timedelta(seconds=every * (round_no - 1))
            self._wait_until(target, f"第 {round_no} 次")
            self.log(f"PLAN  第 {round_no}/{times} 次 · {datetime.now():%H:%M:%S}")
            for child in children:
                if not isinstance(child, dict):
                    raise ValueError("repeat 里的每个动作都要写成对象")
                res = self.run_task(dict(child))
                total += 1
                if not res.ok:
                    raise RuntimeError(
                        f"第 {round_no}/{times} 次、动作「{res.name}」失败：{res.error or ''}".strip()
                    )
        steps = len(children)
        elapsed = (datetime.now() - started).total_seconds()
        return (f"{plan}：执行 {times} 轮 × {steps} 步，共 {total} 个动作（耗时 {self._human_seconds(elapsed)}）",
                {"times": times, "steps": steps, "executed": total, "scheduled": True})

    @staticmethod
    def _human_seconds(seconds: float) -> str:
        seconds = int(round(seconds))
        if seconds % 3600 == 0 and seconds >= 3600:
            return f"{seconds // 3600} 小时"
        if seconds % 60 == 0 and seconds >= 60:
            return f"{seconds // 60} 分钟"
        return f"{seconds} 秒"

    # ---- 异常处理（因果规则） ----
    def _recover(self, task: dict) -> dict | None:
        """按「如果屏幕上出现 X，就先做 Y」的规则处理；返回 {what, skip} 或 None。

        规则来源：整份配置的 recover（全局，每步之前都查）+ 任务自己的 recover。
        """
        rules: list[dict] = list(self.recover) + list(task.get("recover") or [])
        if not rules:
            return None
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            hit = self._match_rule(rule)
            if hit is None:
                continue
            label = rule.get("name") or hit
            self.log(f"RECOVER 命中「{label}」，先执行处理动作", "WARN")
            ok_all = True
            for sub in rule.get("then") or []:
                sub_res = self.run_task(dict(sub), allow_recover=False)
                ok_all = ok_all and sub_res.ok
            if not ok_all:
                self.log(f"RECOVER 「{label}」的处理动作有失败，仍继续", "WARN")
            skip = str(rule.get("then_action", "retry")).lower() in ("skip", "continue", "跳过")
            return {"what": str(label), "skip": skip}
        return None

    def _match_rule(self, rule: dict) -> str | None:
        when = rule.get("when") or {}
        if not isinstance(when, dict):
            return None
        box = when.get("box")
        if when.get("image"):
            path = self._resolve(when["image"])
            hits = image_mod.find(path, box=box,
                                  threshold=float(when.get("threshold", 0.88)))
            if hits:
                return f"屏幕上出现了图片 {Path(str(when['image'])).name}（{hits[0].score:.2f}）"
            return None
        if when.get("ocr"):
            shot = ocr_mod.grab_screen(bbox=box)
            try:
                res = ocr_mod.recognize(shot)
            finally:
                try:
                    Path(shot).unlink()
                except OSError:
                    pass
            found = res.find(str(when["ocr"]), exact=bool(when.get("exact", False)))
            if found:
                return f"屏幕上出现了文字「{when['ocr']}」"
        return None

    def _resolve(self, value: "str | Path") -> Path:
        p = Path(str(value))
        return p if p.is_absolute() else (self.base_dir / p)

    # ---- 各类型任务 ----
    def _dispatch(self, ttype: str, task: dict, timeout: float) -> tuple[str, dict]:
        if ttype == "shell":
            return self._run_shell(task, timeout)
        if ttype == "python":
            return self._run_python(task, timeout)
        if ttype == "ocr":
            return self._run_ocr(task, timeout)
        if ttype == "adb":
            return self._run_adb(task, timeout)
        if ttype == "input":
            return self._run_input(task, timeout)
        if ttype == "image":
            return self._run_image(task, timeout)
        if ttype == "repeat":
            return self._run_repeat(task, timeout)
        if ttype == "group":
            return self._run_group(task, timeout)
        if ttype == "sleep":
            seconds = task.get("seconds", 1)
            if isinstance(seconds, str):
                seconds = self._expand(seconds)
            self._sleep(float(seconds))
            return "", {}
        raise ValueError(
            f"未知任务类型：{ttype}（可选 shell / python / ocr / image / repeat / group / input / adb / sleep）")

    def _run_group(self, task: dict, timeout: float) -> tuple[str, dict]:
        """组合块：把里面这段动作依次跑一遍（相当于只跑 1 轮的重复）。"""
        children = task.get("tasks") or task.get("steps") or []
        if not isinstance(children, list) or not children:
            raise ValueError("组合块里要有个 tasks 数组（里面这段动作）")
        for ci, child in enumerate(children, start=1):
            self._check_cancel()
            res = self.run_task(dict(child), allow_recover=False)
            if not res.ok:
                raise RuntimeError(f"组合块第 {ci} 步、动作「{res.name}」失败：{res.error}")
        label = task.get("name") or "组合块"
        return (f"{label}：执行 {len(children)} 个动作",
                {"steps": len(children), "children": [c.get("name") for c in children]})

    def _image_click_point(self, hit, task: dict) -> tuple[int, int]:
        """命中框里要点哪一处：默认正中，配了 point 就按比例换算。

        `point` 是 `[横向比例, 纵向比例]`（0–1，相对模板图左上角），
        换分辨率或缩放命中也能落在同一处。
        """
        point = task.get("point")
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                rx = min(1.0, max(0.0, float(point[0])))
                ry = min(1.0, max(0.0, float(point[1])))
            except (TypeError, ValueError):
                return hit.center
            return int(round(hit.x + hit.w * rx)), int(round(hit.y + hit.h * ry))
        if isinstance(point, dict):
            try:
                rx = min(1.0, max(0.0, float(point.get("rx", 0.5))))
                ry = min(1.0, max(0.0, float(point.get("ry", 0.5))))
            except (TypeError, ValueError):
                return hit.center
            return int(round(hit.x + hit.w * rx)), int(round(hit.y + hit.h * ry))
        return hit.center

    def _run_image(self, task: dict, timeout: float) -> tuple[str, dict]:
        """找图：找到就点 / 只判断在不在 / 等它出现。"""
        template = task.get("template") or task.get("image")
        if not template:
            raise ValueError("image 任务缺少 template（模板图片路径）")
        path = self._resolve(template)
        if not path.exists():
            raise FileNotFoundError(f"模板图片不存在：{path}")
        threshold = float(task.get("threshold", 0.88))
        box = task.get("box")
        wait_s = float(task.get("wait", 0) or 0)
        action = str(task.get("action") or ("click" if task.get("click") else "check")).lower()
        expect = str(task.get("expect") or "").strip()
        exact = bool(task.get("exact"))

        hit = None
        if wait_s > 0:
            deadline = time.time() + wait_s
            while True:
                self._check_cancel()
                hits = self._filter_by_text(
                    image_mod.find(path, box=box, threshold=threshold), expect, exact
                )
                if hits:
                    hit = hits[0]
                    break
                if time.time() >= deadline:
                    break
                self._sleep(min(0.3, max(0.0, deadline - time.time())))
        else:
            hits = self._filter_by_text(
                image_mod.find(path, box=box, threshold=threshold), expect, exact
            )
            hit = hits[0] if hits else None
        self._check_cancel()

        if hit is None:
            if task.get("allow_missing"):
                self.log(f"SKIP  屏幕上没有 {path.name}，按设置跳过这一步")
                return f"没找到 {path.name}，已跳过", {"found": False, "skipped": True}
            if expect:
                raise RuntimeError(
                    f"屏幕上没找到图片：{path.name} + 文字「{expect}」"
                    f"（threshold={threshold}，图和字要同时出现）"
                )
            raise RuntimeError(f"屏幕上没找到图片：{path.name}（threshold={threshold}）")

        cx, cy = self._image_click_point(hit, task)
        extra = {"found": True, "match": hit.as_dict()}
        if expect:
            extra["expect"] = expect
        label = f"{path.name}（{hit.score:.3f}）"
        if expect:
            label += f" + 「{expect}」"
        if action in ("click", "tap", "double"):
            from . import actions
            actions.click(cx, cy, double=(action == "double"))
            extra["clicked"] = [cx, cy]
            output = f"找到 {label} -> 已点击 ({cx}, {cy})"
        else:
            output = f"找到 {label} @ ({cx}, {cy})"

        # 点完之后接着跑这一步自带的一段动作（then）
        then = task.get("then")
        if isinstance(then, list) and then:
            self.log(f"THEN  点完接着做 {len(then)} 个动作")
            for ti, child in enumerate(then, start=1):
                self._check_cancel()
                res = self.run_task(dict(child), allow_recover=False)
                if not res.ok:
                    raise RuntimeError(f"点完之后的第 {ti} 个动作「{res.name}」失败：{res.error}")
            extra["then"] = len(then)
            output += f"，接着做完 {len(then)} 步"
        return output, extra

    def _filter_by_text(self, hits: list, expect: str, exact: bool) -> list:
        """只留下「框里也能认出 expect」的命中，用来提高找图的准确度。"""
        if not expect or not hits:
            return hits
        shot = ocr_mod.grab_screen()
        if shot is None:  # 没有 Pillow 截不了屏，只能信图
            return hits
        try:
            kept = []
            for hit in hits:
                pad = 12
                region = (
                    max(0, int(hit.x) - pad),
                    max(0, int(hit.y) - pad),
                    int(hit.w) + pad * 2,
                    int(hit.h) + pad * 2,
                )
                try:
                    res = ocr_mod.recognize(shot, box=region)
                except Exception:
                    continue
                if res.find(expect, exact=exact):
                    kept.append(hit)
                self._check_cancel()
            return kept
        finally:
            try:
                shot.unlink()
            except OSError:
                pass

    def _run_input(self, task: dict, timeout: float) -> tuple[str, dict]:
        """鼠标键盘动作：click / double / move / type / key / hotkey。"""
        from . import actions

        action = (task.get("action") or "click").lower()
        extra: dict = {}

        if action in ("click", "double", "move"):
            x, y = int(task.get("x", 0)), int(task.get("y", 0))
            if action == "move":
                actions.move_to(x, y)
            else:
                actions.click(x, y, button=str(task.get("button", "left")),
                              double=(action == "double"))
            extra.update(x=x, y=y)
            return f"{action} ({x}, {y})", extra

        if action in ("type", "text", "write"):
            value = self._expand(str(task.get("value") or task.get("text") or ""))
            if not value:
                raise ValueError("type 动作需要 value（要输入的文字）")
            actions.type_text(value, per_char_delay=float(task.get("interval", 0.01)))
            return f"type {value!r}", extra

        if action in ("key", "hotkey"):
            combo = task.get("value") or task.get("keys") or task.get("key")
            if not combo:
                raise ValueError("key 动作需要 value，如 ctrl+s 或 enter")
            keys = combo if isinstance(combo, (list, tuple)) else str(combo).replace("+", " ").split()
            actions.hotkey(*keys)
            return f"hotkey {'+'.join(str(k) for k in keys)}", extra

        raise ValueError(f"未知 input 动作：{action}（click / double / move / type / key）")


    def _cwd(self, task: dict) -> Path:
        cwd = Path(task.get("cwd") or self.base_dir)
        return cwd if cwd.is_absolute() else (self.base_dir / cwd).resolve()

    def _env(self, task: dict) -> dict:
        env = os.environ.copy()
        for k, v in (task.get("env") or {}).items():
            env[str(k)] = str(v)
        return env

    def _run_shell(self, task: dict, timeout: float) -> tuple[str, dict]:
        cmd = task.get("cmd") or task.get("command")
        if not cmd:
            raise ValueError("shell 任务缺少 cmd")
        if isinstance(cmd, list):
            cmd = " ".join(shlex.quote(str(self._expand(c))) for c in cmd)
        else:
            cmd = self._expand(str(cmd))
        proc = subprocess.run(cmd, shell=True, cwd=str(self._cwd(task)), env=self._env(task),
                              capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        out = _decode(proc.stdout or b"") + _decode(proc.stderr or b"")
        if proc.returncode != 0:
            raise RuntimeError(f"退出码 {proc.returncode}：{out.strip()[:600]}")
        return out, {"rc": proc.returncode}

    def _run_python(self, task: dict, timeout: float) -> tuple[str, dict]:
        script = task.get("script")
        code = task.get("code")
        args = [str(a) for a in (task.get("args") or [])]
        if script:
            path = Path(script)
            if not path.is_absolute():
                path = (self._cwd(task) / path).resolve()
            cmd = [sys.executable, str(path), *args]
        elif code:
            cmd = [sys.executable, "-c", self._expand(str(code)), *args]
        else:
            raise ValueError("python 任务需要 script 或 code")
        env = self._env(task)
        env.setdefault("PYTHONUTF8", "1")     # 保证子进程输出是 UTF-8
        env.setdefault("PYTHONIOENCODING", "utf-8")
        proc = subprocess.run(cmd, cwd=str(self._cwd(task)), env=env,
                              capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        out = _decode(proc.stdout or b"") + _decode(proc.stderr or b"")
        if proc.returncode != 0:
            raise RuntimeError(f"退出码 {proc.returncode}：{out.strip()[:600]}")
        return out, {"rc": proc.returncode}

    def _run_ocr(self, task: dict, timeout: float) -> tuple[str, dict]:
        if task.get("sleep_before"):
            self._sleep(float(task["sleep_before"]))
        dev = None
        image = task.get("image") or task.get("screen") or task.get("device")
        if task.get("device"):
            dev = adb_mod.ensure(task.get("serial"))
            wait = float(task.get("wait", 5.0))
            image = dev.screenshot() if wait <= 0 else dev.stable_screenshot(timeout=wait)
            temp = True
        elif task.get("screen"):
            shot = ocr_mod.grab_screen(task.get("screen_box"))
            assert shot
            image, temp = shot, True
        else:
            temp = False
            if not image:
                raise ValueError("ocr 任务需要 image、screen: true 或 device: true")
            image = Path(image)
            if not image.is_absolute():
                image = (self._cwd(task) / image).resolve()

        try:
            result = ocr_mod.recognize(
                image,
                lang=task.get("lang", "auto"),
                scale=task.get("scale", "auto"),
                box=task.get("box"),
                backend=task.get("backend", "auto"),
                enhance=bool(task.get("enhance", False)),
                timeout=timeout,
            )
        finally:
            if temp:
                try:
                    Path(image).unlink()
                except OSError:
                    pass

        extra: dict = {"lang": result.lang, "lines": len(result.lines)}
        out_file = task.get("out")
        if out_file:
            p = Path(out_file)
            if not p.is_absolute():
                p = (self._cwd(task) / p).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.suffix.lower() == ".json":
                p.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                p.write_text(result.text, encoding="utf-8")
            extra["out"] = str(p)

        expect = task.get("expect")
        hits: list[dict] = []
        if expect:
            hits = result.locate(str(expect))
            extra["hits"] = hits
            if not hits:
                if task.get("allow_missing"):
                    extra["skipped"] = True
                    self.log(f"SKIP  屏上没看到「{expect}」，按设置跳过这一步")
                    return f"没找到「{expect}」，已跳过", extra
                raise RuntimeError(f"屏上没找到「{expect}」。识别到：{result.text[:300]!r}")
            if task.get("click"):
                x, y = hits[0]["cx"], hits[0]["cy"]
                if dev:
                    dev.tap(x, y)
                    extra["clicked"] = [x, y, "adb"]
                    self.log(f"TAP   {expect} @ ({x}, {y})")
                else:
                    from . import actions
                    actions.click(x, y)
                    extra["clicked"] = [x, y]
                    self.log(f"CLICK {expect} @ ({x}, {y})")

        first = next((h["text"] for h in hits), None) or (result.text.splitlines()[0] if result.text else "")
        return first, extra

    # ---- adb 任务 ----
    def _run_adb(self, task: dict, timeout: float) -> tuple[str, dict]:
        dev = adb_mod.Device(task.get("serial"))
        action = (task.get("action") or "tap").lower()
        extra: dict = {"serial": None}

        def target_node() -> "adb_mod.Node":
            want = task.get("target") or task.get("expect") or task.get("text_value") or task.get("label")
            if not want:
                raise ValueError("这个 adb 动作需要 target（要找的文字）")
            hits = dev.find(str(want), partial=bool(task.get("partial", True)),
                            clickable_only=bool(task.get("clickable_only", False)))
            if not hits:
                raise RuntimeError(f"界面里没找到「{want}」")
            idx = int(task.get("index", 0))
            if idx >= len(hits):
                raise RuntimeError(f"「{want}」只找到 {len(hits)} 个，取不到第 {idx} 个")
            return hits[idx]

        if action == "tap":
            if "x" in task and "y" in task:
                x, y = int(task["x"]), int(task["y"])
                dev.tap(x, y)
                extra.update(clicked=[x, y, "coords"])
                return f"tap ({x}, {y})", extra
            node = target_node()
            x, y = node.center
            dev.tap(x, y)
            extra.update(clicked=[x, y, "node"], node=str(node))
            return f"tap {node.text or node.desc or node.rid!r} @ ({x}, {y})", extra

        if action == "swipe":
            x1, y1 = int(task.get("x1", 0)), int(task.get("y1", 0))
            x2, y2 = int(task.get("x2", 0)), int(task.get("y2", 0))
            dev.swipe(x1, y1, x2, y2, int(task.get("ms", 300)))
            return f"swipe ({x1},{y1})->({x2},{y2})", extra

        if action == "longpress":
            node = None
            if "x" in task and "y" in task:
                x, y = int(task["x"]), int(task["y"])
            else:
                node = target_node()
                x, y = node.center
            dev.long_press(x, y, int(task.get("ms", 800)))
            extra.update(clicked=[x, y, "long"])
            return f"longpress @ ({x}, {y})", extra

        if action == "text":
            value = self._expand(str(task.get("value") or task.get("text") or ""))
            if not value:
                raise ValueError("text 动作需要 value")
            how = dev.text(value)
            return f"text {value!r} ({how})", extra

        if action == "key":
            key = task.get("key") or task.get("value")
            if key is None:
                raise ValueError("key 动作需要 key，如 back / home / 66")
            dev.key(key)
            return f"key {key}", extra

        if action == "shot":
            out = task.get("out")
            if out:
                p = Path(out)
                if not p.is_absolute():
                    p = (self._cwd(task) / p).resolve()
                p.parent.mkdir(parents=True, exist_ok=True)
                path = dev.screenshot(p)
            else:
                path = dev.screenshot()
            extra["out"] = str(path)
            return str(path), extra

        if action == "size":
            w, h = dev.size()
            extra.update(width=w, height=h)
            return f"{w}x{h}", extra

        if action == "ui":
            xml = dev.ui_xml()
            try:
                nodes = adb_mod.parse_ui_xml(xml)
            except adb_mod.AdbError:
                nodes = []
            extra["nodes"] = len(nodes)
            out = task.get("out")
            if out:
                p = Path(out)
                if not p.is_absolute():
                    p = (self._cwd(task) / p).resolve()
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(xml, encoding="utf-8")
                extra["out"] = str(p)
            if not nodes:
                return "0 个可读节点（游戏/自绘界面用 OCR 定位）", extra
            return f"{len(nodes)} 个节点", extra

        if action == "find":
            want = str(task.get("target") or task.get("expect") or "")
            if not want:
                raise ValueError("find 动作需要 target")
            hits = dev.find(want, partial=bool(task.get("partial", True)))
            extra["hits"] = [{"text": n.text, "desc": n.desc, "center": list(n.center),
                              "bounds": list(n.bounds), "rid": n.rid} for n in hits]
            if not hits:
                raise RuntimeError(f"界面里没找到「{want}」")
            return f"找到 {len(hits)} 个：{hits[0]}", extra

        if action == "wait":
            want = str(task.get("target") or task.get("expect") or "")
            if not want:
                raise ValueError("wait 动作需要 target")
            node = dev.wait_for(want, timeout=float(task.get("wait_timeout", timeout)),
                                interval=float(task.get("interval", 0.8)))
            if node is None:
                raise RuntimeError(f"等了 {task.get('wait_timeout', timeout)}s 也没出现「{want}」")
            extra["node"] = str(node)
            return f"出现：{node}", extra

        if action == "start":
            comp = str(task.get("package") or task.get("component") or "")
            if not comp:
                raise ValueError("start 动作需要 package")
            return dev.start(comp).strip()[:200], extra

        if action == "stop":
            pkg = str(task.get("package") or "")
            if not pkg:
                raise ValueError("stop 动作需要 package")
            return dev.stop(pkg).strip()[:200], extra

        if action == "current":
            return dev.current_app(), extra

        if action == "boot":
            ok = adb_mod.wait_boot(float(task.get("wait_timeout", timeout)))
            if not ok:
                raise RuntimeError("等不到 sys.boot_completed=1")
            return "booted", extra

        if action == "launch":
            ok = adb_mod.mumu_launch(int(task.get("vmindex", 0)), timeout=float(task.get("wait_timeout", timeout)))
            if not ok:
                raise RuntimeError("模拟器没起来（或不是 MuMu 环境）")
            return "emulator ready", extra

        if action == "shell":
            cmd = task.get("cmd") or task.get("command")
            if not cmd:
                raise ValueError("shell 动作需要 cmd")
            return dev.shell(str(cmd)).strip()[:400], extra

        raise ValueError(f"未知 adb 动作：{action}（tap/swipe/longpress/text/key/shot/ui/find/wait/"
                         f"start/stop/current/boot/launch/shell）")


# --------------------------------------------------------------------------
# 配置加载
# --------------------------------------------------------------------------
def load_config(path: "str | Path") -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"任务文件不存在：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"任务文件 JSON 解析失败（{p} 第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}") from None
    if isinstance(data, list):
        return {"tasks": data}
    if not isinstance(data, dict) or "tasks" not in data:
        raise ValueError("任务文件需要顶层 tasks 数组")
    return data


def merge_defaults(config: dict) -> list[dict]:
    # 顶层这些键是给「整份配置」用的，不下发给单个任务；下划线开头的是注释
    skip = {"log", "history", "stop_on_error", "schedule", "recover", "recover_on_error", "studio"}
    defaults = {k: v for k, v in config.items()
                if k != "tasks" and not str(k).startswith("_")}
    tasks = []
    for t in config.get("tasks", []):
        merged = {**{k: v for k, v in defaults.items() if k not in skip}, **t}
        tasks.append(merged)
    return tasks


def run_config(config: dict, base_dir: "str | Path | None" = None, only: Sequence[str] | None = None,
               dry_run: bool = False, echo: bool = True,
               cancel: "threading.Event | None" = None, on_log=None) -> list[TaskResult]:
    tasks = merge_defaults(config)
    if only:
        wanted = {o.lower() for o in only}
        tasks = [t for t in tasks if str(t.get("name", "")).lower() in wanted]
        if not tasks:
            raise ValueError(f"没有匹配的任务：{', '.join(only)}")

    base = Path(base_dir or Path.cwd()).resolve()
    log_file = config.get("log")
    if log_file and not Path(log_file).is_absolute():
        log_file = base / log_file          # 相对路径按任务文件所在目录算，别落到 cwd
    runner = Runner(base_dir=base, log_file=log_file, echo=echo, cancel=cancel,
                    recover=config.get("recover") or [],
                    recover_on_error=bool(config.get("recover_on_error", True)),
                    on_log=on_log)
    runner.log(f"开始执行 {len(tasks)} 个任务" + ("（dry-run）" if dry_run else ""))

    results: list[TaskResult] = []
    stop_on_error = bool(config.get("stop_on_error", False))
    stopped = False
    for task in tasks:
        if task.get("enabled") is False:
            runner.log(f"SKIP {task.get('name')}（enabled=false）")
            continue
        try:
            res = runner.run_task(task, dry_run=dry_run)
        except TaskCancelled:
            stopped = True
            runner.log("收到停止请求，后面的任务不再执行", "WARN")
            break
        results.append(res)

        # 出错时先跑全局异常处理（比如关掉突然冒出来的弹窗），再重试这一步；
        # 重试还不行就接着跑后面的步骤，不整份停在这里。
        if not res.ok and runner.recover_on_error and runner.recover:
            try:
                handled = runner._recover(task)
            except TaskCancelled:
                stopped = True
                runner.log("收到停止请求，后面的任务不再执行", "WARN")
                break
            except Exception as exc:  # noqa: BLE001
                runner.log(f"全局异常处理自己也出错了：{exc}", "WARN")
                handled = None
            if handled:
                runner.log(f"已处理「{handled['what']}」，重试这一步", "WARN")
                try:
                    retry = runner.run_task(task, dry_run=dry_run, allow_recover=False)
                except TaskCancelled:
                    stopped = True
                    runner.log("收到停止请求，后面的任务不再执行", "WARN")
                    break
                if retry.ok:
                    retry.extra["recovered"] = handled["what"]
                    results[-1] = retry
                    runner.log(f"重试成功：{retry.name}", "WARN")
                    continue
                res.extra["recovered"] = handled["what"]
                res.extra["gave_up"] = True
                runner.log(f"重试仍失败，继续后面的步骤：{res.name}", "WARN")
                continue

        if not res.ok and stop_on_error and not task.get("continue_on_error"):
            runner.log("遇到失败，按 stop_on_error 停止", "ERROR")
            break

    ok_n = sum(1 for r in results if r.ok)
    runner.log(f"结束：{ok_n}/{len(results)} 成功" + ("（已停止）" if stopped else ""))

    history = config.get("history")
    if history and not dry_run:
        hp = Path(history)
        if not hp.is_absolute():
            hp = runner.base_dir / hp
        hp.parent.mkdir(parents=True, exist_ok=True)
        with hp.open("a", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r.as_dict(), ensure_ascii=False) + "\n")
    return results
