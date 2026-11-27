"""离线自检：不联网、不需要模拟器也能验证核心逻辑。

    python -m pyauto selftest
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .ocr import clean_text

UI_FIXTURE = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.demo"
        content-desc="" clickable="false" enabled="true" bounds="[0,0][960,540]">
    <node index="0" text="同意" resource-id="com.demo:id/ok" class="android.widget.Button"
          package="com.demo" content-desc="" clickable="true" enabled="true" bounds="[600,440][900,500]" />
    <node index="1" text="" resource-id="" class="android.widget.ImageView" package="com.demo"
          content-desc="设置" clickable="true" enabled="true" bounds="[20,20][100,100]" />
    <node index="2" text="" resource-id="" class="android.view.View" package="com.demo"
          content-desc="" clickable="false" enabled="true" bounds="[10,10][20,20]" />
  </node>
</hierarchy>
"""


class Report:
    def __init__(self) -> None:
        self.failed = 0
        self.passed = 0

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        print(f"[ok]   {name}" + (f"  {detail}" if detail else ""))

    def fail(self, name: str, detail: str = "") -> None:
        self.failed += 1
        print(f"[FAIL] {name}" + (f"  {detail}" if detail else ""))

    def skip(self, name: str, why: str) -> None:
        print(f"[skip] {name}  {why}")

    def info(self, name: str, detail: str) -> None:
        print(f"[info] {name}  {detail}")


def main() -> int:
    rep = Report()
    scratch = Path(__file__).resolve().parent.parent / ".selftest"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)
    print(f"pyauto selftest / Python {sys.version.split()[0]} / {sys.platform}\n")

    # ---- 1. 包导入 ----
    try:
        import pyauto
        missing = [n for n in pyauto.__all__ if not hasattr(pyauto, n)]
        if missing:
            rep.fail("导入 pyauto", f"缺少导出：{missing}")
        else:
            rep.ok("导入 pyauto", f"v{pyauto.__version__}, {len(pyauto.__all__)} 个导出")
    except Exception as exc:  # noqa: BLE001
        rep.fail("导入 pyauto", f"{type(exc).__name__}: {exc}")
        return 1

    from pyauto.adb import AdbError, parse_ui_xml
    from pyauto.scheduler import ScheduleError, parse_spec

    # ---- 2. 文本清理 ----
    cases = [
        ("应 用 市 场", "应用市场"),
        ("01 ： 09", "01：09"),
        ("取消\n同意", "取消\n同意"),
        ("点 击“同 意”", "点击“同意”"),
    ]
    bad = [(src, clean_text(src), want) for src, want in cases if clean_text(src) != want]
    if bad:
        rep.fail("中文空格清理", f"{bad}")
    else:
        rep.ok("中文空格清理", f"{len(cases)} 个用例")

    # ---- 3. UI XML 解析 ----
    try:
        nodes = parse_ui_xml(UI_FIXTURE)
        texts = [n.text for n in nodes if n.text]
        centers = {n.text: n.center for n in nodes if n.text}
        descs = {n.desc: n.center for n in nodes if n.desc}
        if texts != ["同意"] or centers["同意"] != (750, 470) or descs.get("设置") != (60, 60):
            rep.fail("UI XML 解析", f"texts={texts} centers={centers} descs={descs}")
        elif len(nodes) != 2:
            rep.fail("UI XML 解析", f"应只保留 2 个有意义的节点，实际 {len(nodes)}")
        else:
            rep.ok("UI XML 解析", "bounds→中心坐标正确，空节点已过滤")
    except Exception as exc:  # noqa: BLE001
        rep.fail("UI XML 解析", f"{type(exc).__name__}: {exc}")

    # ---- 4. 调度表达式 ----
    now = datetime.now()
    expected = {"every 30s": "interval", "daily 09:30": "daily", "09:30": "daily",
                "cron */5 * * * *": "cron", "cron 0 9 * * 1-5": "cron", "once": "once"}
    wrong = []
    for spec, kind in expected.items():
        try:
            sc = parse_spec(spec)
            if sc.kind != kind:
                wrong.append((spec, sc.kind, kind))
            nxt = sc.next_after(now)
            if spec != "once" and nxt <= now:
                wrong.append((spec, "next<=now", str(nxt)))
        except ScheduleError as exc:
            wrong.append((spec, "报错", str(exc)))
    for spec in ("every 5x", "cron 60 * * * *", "daily 25:00", "明天"):
        try:
            parse_spec(spec)
            wrong.append((spec, "本该报错却通过了", ""))
        except ScheduleError:
            pass
    if wrong:
        rep.fail("调度表达式", f"{wrong}")
    else:
        rep.ok("调度表达式", f"{len(expected)} 个正常 + 4 个错误用例")

    # ---- 5. 任务配置 ----
    try:
        from pyauto.runner import load_config, merge_defaults
        cfg_path = scratch / "tasks.json"
        cfg_path.write_text(json.dumps({
            "timeout": 7, "log": "x.log",
            "tasks": [{"name": "a", "type": "sleep", "seconds": 0},
                      {"name": "b", "type": "sleep", "timeout": 1}],
        }), encoding="utf-8")
        cfg = load_config(cfg_path)
        merged = merge_defaults(cfg)
        if merged[0].get("timeout") != 7 or merged[1].get("timeout") != 1:
            rep.fail("任务配置合并", f"{merged}")
        elif "log" in merged[0]:
            rep.fail("任务配置合并", "log 不应下发给单个任务")
        else:
            rep.ok("任务配置合并", "默认值下发、log 例外")
    except Exception as exc:  # noqa: BLE001
        rep.fail("任务配置合并", f"{type(exc).__name__}: {exc}")

    # ---- 6. OCR（需要 Pillow） ----
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (640, 160), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=64)
        except TypeError:  # Pillow < 10.1
            font = ImageFont.load_default()
        draw.text((24, 40), "HELLO 2024", fill="black", font=font)
        png = scratch / "ocr.png"
        img.save(png)
        res = pyauto.recognize(png, lang="en-US")
        text = res.text.upper().replace(" ", "")
        if "HELLO" in text and "2024" in text:
            rep.ok("OCR 识别", f"{res.engine}/{res.lang}/{res.elapsed_ms}ms -> {res.text!r}")
        else:
            rep.fail("OCR 识别", f"{res.engine}/{res.lang} 结果不含 HELLO/2024：{res.text!r}")
    except ImportError:
        rep.skip("OCR 识别", "没有 Pillow，跳过")
    except Exception as exc:  # noqa: BLE001
        rep.fail("OCR 识别", f"{type(exc).__name__}: {exc}")

    # ---- 7. adb（有设备就算通过，没有只提示） ----
    try:
        from pyauto import adb as adb_mod
        devs = adb_mod.devices()
        online = [s for s, st in devs.items() if st == "device"]
        if online:
            dev = adb_mod.Device(online[0])
            w, h = dev.size()
            rep.ok("adb 设备", f"{online[0]}  {w}x{h}")
        else:
            rep.info("adb 设备", "当前没有在线设备（模拟器没启动？这不影响离线自检）")
    except AdbError as exc:
        rep.skip("adb 设备", str(exc))
    except Exception as exc:  # noqa: BLE001
        rep.fail("adb 设备", f"{type(exc).__name__}: {exc}")

    # ---- 8. 找图 ----
    try:
        import numpy as np

        from pyauto import imagefind
        rng = np.random.default_rng(0)
        screen = rng.integers(20, 220, size=(150, 200), dtype=np.uint8)
        patch = screen[50:80, 60:100].copy()
        hits = imagefind.find(patch, image=screen, scales=(1.0,), threshold=0.9)
        if not hits:
            rep.fail("找图（FFT 模板匹配）", "同一块图都没找到")
        else:
            hit = hits[0]
            if (hit.x, hit.y, hit.w, hit.h) != (60, 50, 40, 30) or hit.score < 0.99:
                rep.fail("找图（FFT 模板匹配）", f"位置不对：{hit.as_dict()}")
            else:
                blank = imagefind.find(np.zeros((20, 20), np.uint8), image=screen,
                                       scales=(1.0,), threshold=0.9)
                if blank:
                    rep.fail("找图（FFT 模板匹配）", f"纯色模板不该命中：{blank[0].as_dict()}")
                else:
                    rep.ok("找图（FFT 模板匹配）",
                           f"命中 {hit.center} score={hit.score:.3f}，纯色反例 0 命中")
    except ImportError:
        rep.skip("找图（FFT 模板匹配）", "没有 numpy/Pillow，跳过")
    except Exception as exc:  # noqa: BLE001
        rep.fail("找图（FFT 模板匹配）", f"{type(exc).__name__}: {exc}")

    # ---- 9. studio 规则转换 ----
    try:
        from pyauto import studio
        act = {"kind": "image", "template": "templates/a.png", "action": "check",
               "threshold": 0.9, "wait": 1.0}
        task = studio.to_task(act)
        cfg = studio.to_config([act], rules=[{
            "when": {"kind": "ocr", "text": "同意"},
            "then": [{"kind": "wait", "seconds": 1}],
            "then_action": "retry"}])
        if task.get("type") != "image" or cfg["recover"][0]["when"].get("ocr") != "同意":
            rep.fail("studio 规则转换", f"{task} / {cfg.get('recover')}")
        else:
            try:
                studio.to_task({"kind": "image", "template": ""})
            except ValueError:
                rep.ok("studio 规则转换", "找图任务 / 全局异常规则 JSON 正确，空模板会报错")
            else:
                rep.fail("studio 规则转换", "空模板本该报错")
    except Exception as exc:  # noqa: BLE001
        rep.fail("studio 规则转换", f"{type(exc).__name__}: {exc}")

    shutil.rmtree(scratch, ignore_errors=True)
    print(f"\n结果：{rep.passed} 通过，{rep.failed} 失败")
    return 0 if rep.failed == 0 else 1
