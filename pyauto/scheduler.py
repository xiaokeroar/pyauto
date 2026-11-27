"""极简调度器：间隔 / 每天定时 / 5 段 cron，不依赖第三方库。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]  # 分 时 日 月 周
DOW_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


class ScheduleError(ValueError):
    pass


def _parse_int(token: str, lo: int, hi: int, dow: bool = False) -> int:
    t = token.strip().lower()
    if dow and t in DOW_NAMES:
        return DOW_NAMES[t]
    try:
        v = int(t)
    except ValueError:
        raise ScheduleError(f"无法解析数字：{token!r}")
    if v < lo or v > hi:
        raise ScheduleError(f"{v} 超出范围 {lo}-{hi}")
    return v


def _parse_field(spec: str, lo: int, hi: int, dow: bool = False) -> set[int]:
    values: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ScheduleError(f"步长必须为正：{step}")
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = _parse_int(a, lo, hi, dow), _parse_int(b, lo, hi, dow)
        else:
            start = _parse_int(part, lo, hi, dow)
            end = hi if step > 1 else start
        values.update(range(start, end + 1, step))
    if not values:
        raise ScheduleError(f"空字段：{spec!r}")
    return values


@dataclass
class Schedule:
    kind: str                      # interval | daily | cron | once
    seconds: float = 0.0
    at: tuple[int, int] | None = None
    minute: set[int] = field(default_factory=set)
    hour: set[int] = field(default_factory=set)
    day: set[int] = field(default_factory=set)
    month: set[int] = field(default_factory=set)
    dow: set[int] = field(default_factory=set)
    spec: str = ""

    def next_after(self, now: datetime) -> datetime:
        if self.kind == "once":
            return now
        if self.kind == "interval":
            return now + timedelta(seconds=self.seconds)
        if self.kind == "daily":
            assert self.at
            hh, mm = self.at
            target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target
        # cron：从下一分钟开始逐分钟找（最多向前找 366 天）
        t = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = t + timedelta(days=366)
        while t < limit:
            if (t.minute in self.minute and t.hour in self.hour and t.month in self.month
                    and t.day in self.day and (t.weekday() + 1) % 7 in self.dow):
                return t
            t += timedelta(minutes=1)
        raise ScheduleError(f"cron 表达式在一年内没有匹配时间：{self.spec}")


def parse_spec(text: str) -> Schedule:
    """支持：
    - "every 30s" / "every 5m" / "every 2h"
    - "daily 09:30" / "at 09:30" / "09:30"
    - "cron */5 * * * *" / "*/5 * * * *"
    - "once"
    """
    raw = (text or "").strip()
    if not raw:
        raise ScheduleError("调度表达式为空")
    low = raw.lower()

    if low in ("once", "now", "一次"):
        return Schedule(kind="once", spec=raw)

    if low.startswith("every"):
        return Schedule(kind="interval", seconds=_parse_duration(low[5:].strip()), spec=raw)

    for prefix in ("daily", "at"):
        if low.startswith(prefix):
            return Schedule(kind="daily", at=_parse_hhmm(low[len(prefix):].strip()), spec=raw)
    if ":" in raw and " " not in raw:
        return Schedule(kind="daily", at=_parse_hhmm(raw), spec=raw)

    if low.startswith("cron"):
        raw = raw[4:].strip()
    fields = raw.split()
    if len(fields) == 5:
        sets = [_parse_field(f, lo, hi, dow=(i == 4))
                for i, (f, (lo, hi)) in enumerate(zip(fields, FIELD_RANGES))]
        return Schedule(kind="cron", minute=sets[0], hour=sets[1], day=sets[2],
                        month=sets[3], dow=sets[4], spec=raw)

    if len(fields) == 1:
        return Schedule(kind="interval", seconds=_parse_duration(fields[0]), spec=raw)

    raise ScheduleError(f"看不懂的调度表达式：{text!r}（示例：every 5m / daily 09:30 / */10 * * * *）")


def _parse_duration(text: str) -> float:
    t = text.strip().lower()
    unit = t[-1] if t and t[-1].isalpha() else "s"
    num = t[:-1] if t[-1:].isalpha() else t
    try:
        value = float(num)
    except ValueError:
        raise ScheduleError(f"无法解析时长：{text!r}")
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit)
    if mult is None:
        raise ScheduleError(f"未知单位：{unit!r}（支持 s/m/h/d）")
    if value <= 0:
        raise ScheduleError("时长必须大于 0")
    return value * mult


def _parse_hhmm(text: str) -> tuple[int, int]:
    t = text.strip()
    if ":" not in t:
        raise ScheduleError(f"时间格式应为 HH:MM：{text!r}")
    hh, mm = t.split(":", 1)
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ScheduleError(f"时间超出范围：{text!r}")
    return h, m


def serve(schedule: "Schedule | str", action: Callable[[], None], max_runs: int | None = None,
          log: Callable[[str], None] = print, run_immediately: bool | None = None) -> int:
    """循环触发 action，返回执行次数。Ctrl+C 退出。"""
    sched = parse_spec(schedule) if isinstance(schedule, str) else schedule
    if run_immediately is None:
        run_immediately = sched.kind == "once"

    runs = 0
    try:
        if run_immediately:
            action()
            runs += 1
            if sched.kind == "once" or (max_runs and runs >= max_runs):
                return runs
        while True:
            now = datetime.now()
            nxt = sched.next_after(now)
            delay = max(0.0, (nxt - now).total_seconds())
            log(f"下次运行：{nxt:%Y-%m-%d %H:%M:%S}（{delay:.0f}s 后）")
            _sleep_until(nxt)
            action()
            runs += 1
            if max_runs and runs >= max_runs:
                return runs
    except KeyboardInterrupt:
        log("收到 Ctrl+C，停止调度")
    return runs


def _sleep_until(target: datetime) -> None:
    while True:
        remain = (target - datetime.now()).total_seconds()
        if remain <= 0:
            return
        time.sleep(min(remain, 1.0))
