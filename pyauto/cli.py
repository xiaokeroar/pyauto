"""命令行入口：pyauto doctor | ocr | run | schedule | init"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, actions, adb as adb_mod, ocr as ocr_mod, runner as runner_mod, scheduler as sched_mod

EXIT_OK, EXIT_ERROR, EXIT_NOT_FOUND = 0, 1, 3


# --------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    from . import runner  # noqa: F401  (确认模块可导入)

    backends = ocr_mod.available_backends()
    langs = ocr_mod.windows_languages() if backends["windows"] else []
    print(f"pyauto {__version__}")
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"平台        : {sys.platform}")
    print(f"Pillow      : {'有 ' + ocr_mod.Image.__version__ if backends['pillow'] else '无（预处理/截屏不可用）'}")
    print(f"Windows OCR : {'可用' if backends['windows'] else '不可用'}")
    if langs:
        print(f"  识别语言  : {', '.join(langs)}")
    print(f"Tesseract   : {ocr_mod.tesseract_exe() or '未安装（可选）'}")
    print(f"动作模块    : {'可用' if sys.platform == 'win32' else '仅 Windows'}")
    if args.image:
        try:
            res = ocr_mod.recognize(args.image)
            print(f"\n试识别 {args.image}: {res.engine} / {res.lang} / {res.elapsed_ms}ms / {len(res.lines)} 行")
            print(res.text[:500])
        except Exception as exc:  # noqa: BLE001
            print(f"\n试识别失败：{exc}")
            return EXIT_ERROR
    if not (backends["windows"] or backends["tesseract"]):
        print("\n没有可用 OCR 引擎：Windows 10/11 自带 OCR 不可用，请安装 Tesseract 或启用系统语言包。")
        return EXIT_ERROR
    return EXIT_OK


# --------------------------------------------------------------------------
def _parse_box(text: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in text.replace("，", ",").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--box 需要 4 个数字：x,y,w,h")
    try:
        return tuple(int(float(p)) for p in parts)  # type: ignore[return-value]
    except ValueError:
        raise argparse.ArgumentTypeError("--box 只能是数字：x,y,w,h")


def _collect_images(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            files += sorted(q for q in path.iterdir()
                            if q.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"})
        else:
            files.append(path)
    return files


def _only(names: list[str] | None) -> list[str] | None:
    """--only 支持 “a b” 和 “a,b” 两种写法。"""
    if not names:
        return None
    out: list[str] = []
    for item in names:
        out += [p.strip() for p in item.split(",") if p.strip()]
    return out or None


def cmd_ocr(args: argparse.Namespace) -> int:
    dev = None
    shot = None
    if args.device:
        dev = adb_mod.ensure(args.serial)
        shot = (dev.screenshot() if (args.no_wait or args.wait <= 0)
                else dev.stable_screenshot(timeout=args.wait))
        targets = [shot]
    elif args.screen:
        shot = ocr_mod.grab_screen()
        assert shot is not None
        targets = [shot]
    else:
        if not args.paths:
            print("请给出图片路径，或用 --screen / --device 取图", file=sys.stderr)
            return EXIT_ERROR
        targets = _collect_images(args.paths)
        shot = None

    if args.lang == "auto" and args.engine == "tesseract":
        args.lang = "chi_sim+eng"

    worst = EXIT_OK
    out_chunks: list[str] = []
    try:
        for img in targets:
            try:
                res = ocr_mod.recognize(img, lang=args.lang, scale=args.scale, box=args.box,
                                        backend=args.engine, enhance=args.enhance)
            except Exception as exc:  # noqa: BLE001
                print(f"OCR 失败 {img}: {exc}", file=sys.stderr)
                worst = EXIT_ERROR
                continue

            text = res.raw_text if args.raw else res.text
            if args.json:
                payload = res.to_dict()
                payload["text"] = text
                out_chunks.append(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                header = f"# {img.name}  ({res.engine}, {res.lang}, {res.elapsed_ms}ms, {len(res.lines)} 行)"
                out_chunks.append(header + "\n" + text)

            if args.find:
                hits = res.locate(args.find)
                if not hits:
                    print(f"未找到「{args.find}」", file=sys.stderr)
                    worst = EXIT_NOT_FOUND
                else:
                    for h in hits[:5]:
                        print(f"找到「{args.find}」: {h['text']!r} @ ({h['cx']}, {h['cy']}) "
                              f"box=({h['x']:.0f},{h['y']:.0f},{h['w']:.0f},{h['h']:.0f}) [{h['level']}]",
                              file=sys.stderr)
                    if args.click:
                        x, y = hits[0]["cx"], hits[0]["cy"]
                        if dev:
                            dev.tap(x, y)
                            print(f"已 adb tap ({x}, {y})", file=sys.stderr)
                        else:
                            actions.click(x, y)
                            print(f"已点击 ({x}, {y})", file=sys.stderr)

            if args.annotate:
                try:
                    path = ocr_mod.annotate(res, args.annotate)
                    print(f"标注图：{path}", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001
                    print(f"标注失败：{exc}", file=sys.stderr)
    finally:
        if (args.screen or args.device) and shot:
            try:
                shot.unlink()
            except OSError:
                pass

    body = "\n\n".join(out_chunks)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        print(f"已写入 {p}", file=sys.stderr)
    else:
        print(body)
    return worst


# --------------------------------------------------------------------------
def cmd_adb(args: argparse.Namespace) -> int:
    act = args.adb_cmd
    try:
        if act == "devices":
            found = adb_mod.devices()
            if not found:
                print("没有设备。启动模拟器后再试，或 adb connect 127.0.0.1:16384", file=sys.stderr)
                return EXIT_ERROR
            for serial, state in found.items():
                print(f"{serial}\t{state}")
            return EXIT_OK

        if act == "connect":
            ok = adb_mod.connect(args.serial or "127.0.0.1:16384")
            print("已连接" if ok else "连接失败", file=sys.stderr)
            return EXIT_OK if ok else EXIT_ERROR

        if act == "launch":
            print("正在启动 MuMu…", file=sys.stderr)
            ok = adb_mod.mumu_launch(args.vmindex, timeout=args.timeout)
            print("模拟器就绪" if ok else "等不到设备上线", file=sys.stderr)
            return EXIT_OK if ok else EXIT_ERROR

        dev = adb_mod.Device(args.serial or None)

        if act == "size":
            w, h = dev.size()
            print(f"{dev.serial}\t{w}x{h}")
            return EXIT_OK

        if act == "boot":
            ok = adb_mod.wait_boot(args.timeout, dev)
            print("已启动完成" if ok else "超时", file=sys.stderr)
            return EXIT_OK if ok else EXIT_ERROR

        if act == "shot":
            path = dev.screenshot(args.out)
            print(path)
            return EXIT_OK

        if act == "tap":
            if args.text:
                hits = dev.find(args.text, partial=not args.exact,
                                clickable_only=args.clickable_only)
                if not hits:
                    print(f"没找到「{args.text}」", file=sys.stderr)
                    return EXIT_NOT_FOUND
                node = hits[min(args.index, len(hits) - 1)]
                x, y = node.center
                dev.tap(x, y)
                print(f"点了 {node.text or node.desc!r} @ ({x}, {y})")
                return EXIT_OK
            if args.coords is None or len(args.coords) != 2:
                print("tap 需要 x y 坐标，或 --text 文字", file=sys.stderr)
                return EXIT_ERROR
            x, y = args.coords
            dev.tap(x, y)
            print(f"点了 ({x}, {y})")
            return EXIT_OK

        if act == "swipe":
            if len(args.coords) != 4:
                print("swipe 需要 x1 y1 x2 y2", file=sys.stderr)
                return EXIT_ERROR
            dev.swipe(*args.coords, ms=args.ms)
            print(f"滑了 {args.coords}")
            return EXIT_OK

        if act == "text":
            payload = " ".join(args.words)
            how = dev.text(payload)
            print(f"已输入（{how}）")
            return EXIT_OK

        if act == "key":
            dev.key(args.key)
            print(f"按键 {args.key}")
            return EXIT_OK

        if act == "ui":
            xml = dev.ui_xml()
            try:
                nodes = adb_mod.parse_ui_xml(xml)
            except adb_mod.AdbError:
                nodes = []
            body = "\n".join(str(n) for n in nodes)
            if args.out:
                p = Path(args.out)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(xml, encoding="utf-8")
                print(f"{len(nodes)} 个可读节点，原始 XML -> {p}", file=sys.stderr)
            elif nodes:
                print(body)
            else:
                print("这个界面上没有可读控件（游戏、视频、自绘 UI 常见）。\n"
                      "改用 OCR 定位：pyauto ocr --device --find 文字", file=sys.stderr)
            return EXIT_OK

        if act == "find":
            hits = dev.find(args.text, partial=not args.exact)
            if not hits:
                print(f"没找到「{args.text}」", file=sys.stderr)
                return EXIT_NOT_FOUND
            for node in hits[:10]:
                print(str(node))
            if args.click:
                x, y = hits[min(args.index, len(hits) - 1)].center
                dev.tap(x, y)
                print(f"已 tap ({x}, {y})", file=sys.stderr)
            return EXIT_OK

        if act == "start":
            print(dev.start(args.package).strip()[:300])
            return EXIT_OK

        if act == "current":
            print(dev.current_app())
            return EXIT_OK

        print(f"未知动作：{act}", file=sys.stderr)
        return EXIT_ERROR
    except adb_mod.AdbError as exc:
        print(f"adb 错误：{exc}", file=sys.stderr)
        return EXIT_ERROR


def cmd_image(args: argparse.Namespace) -> int:
    from . import imagefind

    if args.action == "grab":
        try:
            img = imagefind.screen_image(args.box)
        except Exception as exc:  # noqa: BLE001
            print(f"截屏失败：{exc}", file=sys.stderr)
            return EXIT_ERROR
        out = Path(args.out or "template.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        print(f"已截取模板 {out}（{img.width}×{img.height}）")
        return EXIT_OK

    tpl = Path(args.template)
    if not tpl.exists():
        print(f"模板图片不存在：{tpl}", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.wait > 0:
            hit = imagefind.wait(tpl, timeout=args.wait, threshold=args.threshold, box=args.box)
            hits = [hit] if hit else []
        else:
            hits = imagefind.find(tpl, box=args.box, threshold=args.threshold,
                                  max_results=args.max)
    except Exception as exc:  # noqa: BLE001
        print(f"找图失败：{exc}", file=sys.stderr)
        return EXIT_ERROR

    if not hits:
        print(f"没找到 {tpl.name}（阈值 {args.threshold}）")
        return EXIT_NOT_FOUND

    if args.json:
        print(json.dumps([h.as_dict() for h in hits], ensure_ascii=False, indent=2))
    else:
        for h in hits:
            print(f"{h.score:.3f}  {h.w}×{h.h} @ ({h.x}, {h.y})  "
                  f"中心 ({h.center[0]}, {h.center[1]})  scale {h.scale:.2f}")

    first = hits[0]
    if args.annotate or args.out:
        try:
            from PIL import ImageDraw
            img = imagefind.screen_image()
            draw = ImageDraw.Draw(img)
            for h in hits:
                draw.rectangle([h.x, h.y, h.x + h.w, h.y + h.h], outline="#ff2d55", width=3)
                draw.text((h.x + 3, max(0, h.y - 14)), f"{h.score:.2f}", fill="#ff2d55")
            target = Path(args.out or "found.png")
            target.parent.mkdir(parents=True, exist_ok=True)
            img.save(target)
            print(f"标注图已保存：{target}")
        except Exception as exc:  # noqa: BLE001
            print(f"画标注图失败：{exc}", file=sys.stderr)

    if args.click:
        actions.click(*first.center)
        print(f"已点击 ({first.center[0]}, {first.center[1]})")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    config = runner_mod.load_config(args.config)
    results = runner_mod.run_config(config, base_dir=Path(args.config).resolve().parent,
                                    only=_only(args.only), dry_run=args.dry_run, echo=not args.quiet)
    return EXIT_OK if all(r.ok for r in results) else EXIT_ERROR


def cmd_emu(args: argparse.Namespace) -> int:
    print(f"正在启动 MuMu（实例 {args.vmindex}，引擎 {args.ver}）…", file=sys.stderr)
    res = adb_mod.mumu_open(args.vmindex, timeout=args.timeout, version=args.ver,
                            front=not args.no_front)
    if not res["ok"]:
        print(f"失败：{res['msg']}", file=sys.stderr)
        return EXIT_ERROR

    win = res.get("window")
    if win:
        title = win.get("title") or "MuMu"
        if win.get("foreground"):
            print(f"OK {title} 已置于最前")
        else:
            print(f"OK {title} 已启动，但抢前台被系统拦住了（窗口应该也在最前面，只是没拿到焦点）")
    else:
        print("OK 已启动（未抢前台）")
    return EXIT_OK


def cmd_web(args: argparse.Namespace) -> int:
    from . import webserver

    try:
        webserver.serve(port=args.port, host=args.host,
                        open_browser=not args.no_browser, config=args.config)
    except OSError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def cmd_schedule(args: argparse.Namespace) -> int:
    config = runner_mod.load_config(args.config)
    spec = args.every or args.cron or args.at or config.get("schedule")
    if not spec:
        print("缺少调度表达式：用 --every 5m / --cron \"*/10 * * * *\" / --at 09:30，"
              "或在配置里写 \"schedule\": \"every 5m\"", file=sys.stderr)
        return EXIT_ERROR

    base = Path(args.config).resolve().parent
    quiet = args.quiet

    def run_once() -> None:
        runner_mod.run_config(config, base_dir=base, only=_only(args.only), echo=not quiet)

    try:
        sched = sched_mod.parse_spec(spec)
    except sched_mod.ScheduleError as exc:
        print(f"调度表达式无效：{exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"进入调度：{sched.spec}（Ctrl+C 停止）", flush=True)
    try:
        sched_mod.serve(sched, run_once, max_runs=args.max_runs,
                        log=lambda m: print(f"[调度] {m}", flush=True),
                        run_immediately=args.now)
    except sched_mod.ScheduleError as exc:
        print(f"调度失败：{exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


# --------------------------------------------------------------------------
EXAMPLE = {
    "log": "logs/pyauto.log",
    "history": "logs/history.jsonl",
    "stop_on_error": False,
    "tasks": [
        {"name": "hello", "type": "shell", "cmd": "echo pyauto 启动", "timeout": 10},
        {"name": "读一张图", "type": "ocr",
         "image": "shot.png", "lang": "zh-Hans-CN", "out": "logs/ocr.txt",
         "expect": "", "enabled": False},
        {"name": "找按钮并点击", "type": "ocr",
         "image": "shot.png", "expect": "同意", "click": True, "enabled": False},
    ],
}


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir or ".").resolve()
    target.mkdir(parents=True, exist_ok=True)
    path = target / "tasks.example.json"
    if path.exists() and not args.force:
        print(f"{path} 已存在，加 --force 覆盖", file=sys.stderr)
        return EXIT_ERROR
    path.write_text(json.dumps(EXAMPLE, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 {path}")
    print("跑一下：pyauto run tasks.example.json")
    return EXIT_OK


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pyauto", description="脚本自动化工具箱：OCR + 定时任务 + 命令编排")
    p.add_argument("--version", action="version", version=f"pyauto {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="检查环境与 OCR 引擎")
    d.add_argument("image", nargs="?", help="可选：顺手识别一张图验证")
    d.set_defaults(func=cmd_doctor)

    o = sub.add_parser("ocr", help="识别图片/截屏文字")
    o.add_argument("paths", nargs="*", help="图片、目录或通配符（见 shell 展开）")
    o.add_argument("--screen", action="store_true", help="截取当前屏幕再识别")
    o.add_argument("--lang", default="auto", help="识别语言，如 zh-Hans-CN / en-US / auto")
    o.add_argument("--scale", default="auto", help="缩放：auto 或数字，如 2")
    o.add_argument("--box", type=_parse_box, help="只识别区域 x,y,w,h（原图坐标）")
    o.add_argument("--engine", default="auto", choices=["auto", "windows", "tesseract"])
    o.add_argument("--enhance", action="store_true", help="灰度+增强对比度后再识别")
    o.add_argument("--json", action="store_true", help="输出 JSON（含每行坐标）")
    o.add_argument("--raw", action="store_true", help="保留原始的 CJK 逐字空格")
    o.add_argument("-o", "--out", help="结果写入文件")
    o.add_argument("--find", help="在结果中查找文字，返回中心坐标")
    o.add_argument("--click", action="store_true", help="找到后点击（配合 --find）")
    o.add_argument("--annotate", help="把识别框画到图上另存，便于校验")
    o.add_argument("--device", action="store_true", help="从 adb 设备（模拟器/真机）截图再识别")
    o.add_argument("--serial", help="adb 设备序列号，如 127.0.0.1:16384")
    o.add_argument("--wait", type=float, default=5.0,
                   help="设备截图前等画面稳定的最长秒数（默认 5，0=不等）")
    o.add_argument("--no-wait", action="store_true", help="不等画面稳定，立刻截图")
    o.set_defaults(func=cmd_ocr)

    r = sub.add_parser("run", help="执行任务配置（可重复执行/带重试）")
    r.add_argument("config", help="任务 JSON 路径")
    r.add_argument("--only", nargs="+", metavar="NAME", help="只跑指定名字的任务")
    r.add_argument("--dry-run", action="store_true", help="只打印不执行")
    r.add_argument("-q", "--quiet", action="store_true")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("schedule", help="按间隔/定时/cron 循环执行任务配置")
    s.add_argument("config", help="任务 JSON 路径")
    s.add_argument("--every", help="间隔，如 30s / 5m / 1h")
    s.add_argument("--cron", help="5 段 cron，如 \"*/10 * * * *\"")
    s.add_argument("--at", help="每天定时，如 09:30")
    s.add_argument("--now", action="store_true", help="启动后先立即跑一次")
    s.add_argument("--max-runs", type=int, help="最多执行几次（调试用）")
    s.add_argument("--only", nargs="+", metavar="NAME")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=cmd_schedule)

    a = sub.add_parser("adb", help="通过 adb 控制模拟器/真机（截图/点击/输入/UI 树）")
    a.add_argument("--serial", help="设备序列号，如 127.0.0.1:16384")
    asub = a.add_subparsers(dest="adb_cmd", required=True)

    asub.add_parser("devices", help="列出设备").set_defaults(func=cmd_adb)

    ac = asub.add_parser("connect", help="adb connect 一个地址")
    ac.add_argument("serial", nargs="?", help="默认 127.0.0.1:16384")
    ac.set_defaults(func=cmd_adb)

    al = asub.add_parser("launch", help="启动 MuMu 并等设备上线")
    al.add_argument("vmindex", nargs="?", type=int, default=0)
    al.add_argument("--timeout", type=float, default=120)
    al.set_defaults(func=cmd_adb)

    asub.add_parser("size", help="设备分辨率").set_defaults(func=cmd_adb)

    ab = asub.add_parser("boot", help="等系统启动完成")
    ab.add_argument("--timeout", type=float, default=120)
    ab.set_defaults(func=cmd_adb)

    ash = asub.add_parser("shot", help="截图到文件")
    ash.add_argument("-o", "--out")
    ash.set_defaults(func=cmd_adb)

    at = asub.add_parser("tap", help="点击坐标，或按文字点击")
    at.add_argument("coords", nargs="*", type=int)
    at.add_argument("--text", help="按控件文字点击")
    at.add_argument("--index", type=int, default=0)
    at.add_argument("--exact", action="store_true")
    at.add_argument("--clickable-only", action="store_true")
    at.set_defaults(func=cmd_adb)

    asw = asub.add_parser("swipe", help="滑动 x1 y1 x2 y2")
    asw.add_argument("coords", nargs="*", type=int)
    asw.add_argument("--ms", type=int, default=300)
    asw.set_defaults(func=cmd_adb)

    atx = asub.add_parser("text", help="输入文本")
    atx.add_argument("words", nargs="+")
    atx.set_defaults(func=cmd_adb)

    ak = asub.add_parser("key", help="按键，如 back/home/enter/66")
    ak.add_argument("key")
    ak.set_defaults(func=cmd_adb)

    au = asub.add_parser("ui", help="列出当前界面控件")
    au.add_argument("-o", "--out")
    au.set_defaults(func=cmd_adb)

    af = asub.add_parser("find", help="按文字找控件")
    af.add_argument("text")
    af.add_argument("--exact", action="store_true")
    af.add_argument("--index", type=int, default=0)
    af.add_argument("--click", action="store_true")
    af.set_defaults(func=cmd_adb)

    ast = asub.add_parser("start", help="启动应用")
    ast.add_argument("package", help="包名或 pkg/Activity")
    ast.set_defaults(func=cmd_adb)

    asub.add_parser("current", help="当前前台应用").set_defaults(func=cmd_adb)

    g = sub.add_parser("gui", help="打开可视化编辑器（pyauto studio）")
    g.add_argument("config", nargs="?", help="要编辑的任务文件，留空则新建")
    g.set_defaults(func=lambda a: __import__("pyauto.studio", fromlist=["main"]).main(
        [a.config] if a.config else []))

    w = sub.add_parser("web", help="在浏览器里打开编辑器（本地端口）")
    w.add_argument("--port", type=int, default=8937, help="端口，默认 8937（占用时自动往后找）")
    w.add_argument("--host", default="127.0.0.1", help="监听地址，默认只开本机")
    w.add_argument("--config", help="要编辑的任务文件，默认工程根 tasks.json")
    w.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    w.set_defaults(func=cmd_web)

    im = sub.add_parser("image", help="按图片找屏幕位置（模板匹配，不用 OpenCV）")
    isub = im.add_subparsers(dest="action", required=True)
    imf = isub.add_parser("find", help="在屏幕上找模板图片")
    imf.add_argument("template", help="模板图片路径")
    imf.add_argument("--box", type=_parse_box, help="只在这块区域找：x,y,w,h")
    imf.add_argument("--threshold", type=float, default=0.85, help="相似度阈值，默认 0.85")
    imf.add_argument("--wait", type=float, default=0.0, help="最多等几秒它出现")
    imf.add_argument("--max", type=int, default=5, help="最多报几个结果")
    imf.add_argument("--click", action="store_true", help="找到就点中心")
    imf.add_argument("--annotate", action="store_true", help="截图画出命中框")
    imf.add_argument("--json", action="store_true")
    imf.add_argument("-o", "--out", help="标注图输出路径")
    imf.set_defaults(func=cmd_image)

    img_ = isub.add_parser("grab", help="从屏幕截一块当模板")
    img_.add_argument("--box", type=_parse_box, help="截取区域：x,y,w,h（不填就是全屏）")
    img_.add_argument("-o", "--out", help="保存路径，默认 template.png")
    img_.set_defaults(func=cmd_image)

    em = sub.add_parser("emu", help="启动安卓模拟器（MuMu），并把窗口提到最前")
    em.add_argument("--vm", "--vmindex", dest="vmindex", type=int, default=0,
                    help="实例序号，默认 0")
    em.add_argument("--ver", default="15", help="引擎版本 15 / 12（默认 15）")
    em.add_argument("--timeout", type=float, default=120.0, help="等窗口出现的秒数")
    em.add_argument("--no-front", action="store_true", help="只启动，不抢到最前")
    em.set_defaults(func=cmd_emu)

    sel = sub.add_parser("selftest", help="离线自检：文本清理/UI 解析/调度/配置/OCR/adb")
    sel.set_defaults(func=lambda a: __import__("pyauto.selftest", fromlist=["main"]).main())

    i = sub.add_parser("init", help="生成示例任务配置")
    i.add_argument("dir", nargs="?", help="输出目录，默认当前目录")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)
    return p


MENU_HINT = """\
pyauto - 脚本自动化工具箱

  常用命令（直接输入，不用带 pyauto.cmd）
    gui                打开桌面版编辑器
    web                在浏览器里打开（127.0.0.1:8937）
    doctor             检查环境
    run tasks.json     跑任务文件
    ocr 截图.png        识别图片里的文字
    selftest           离线自检
    help / ?           再看一遍这份菜单
    exit / q           退出

  完整命令
    doctor  ocr  run  schedule  adb  gui  web  image  emu  selftest  init
"""


def menu() -> int:
    """不带参数启动时进的小命令行：输入命令按回车即可，窗口不会一闪就关。"""
    print(MENU_HINT)
    while True:
        try:
            line = input("pyauto> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return EXIT_OK
        if not line:
            continue
        lowered = line.lower()
        if lowered in {"exit", "quit", "q", "bye"}:
            return EXIT_OK
        if lowered in {"help", "?", "menu", "h"}:
            print(MENU_HINT)
            continue
        if lowered.startswith("cd ") or lowered == "cd":
            target = line[2:].strip().strip('"') or "."
            try:
                os.chdir(os.path.expanduser(target))
                print(os.getcwd())
            except OSError as exc:
                print(f"切不过去：{exc}", file=sys.stderr)
            continue
        if lowered in {"cls", "clear"}:
            print("\033[2J\033[H", end="")
            continue
        if lowered in {"pwd", "ls", "dir"}:
            print(os.getcwd())
            continue
        try:
            import shlex

            argv = shlex.split(line, posix=False)
            argv = [item.strip('"') for item in argv]
            rc = main(argv)
        except SystemExit as exc:  # argparse 用法错误：只退出这一条命令
            code = exc.code
            rc = int(code) if isinstance(code, int) else EXIT_ERROR
        except KeyboardInterrupt:
            print("\n已中断", file=sys.stderr)
            rc = 130
        if rc:
            print(f"（退出码 {rc}）")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return menu()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            raise
        return EXIT_ERROR
