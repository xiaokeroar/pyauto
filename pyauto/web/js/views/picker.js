// 屏幕取点 / 框选：截一张全屏图，在图上点或拖，换算回真实坐标。
// 框选支持二次调整：拖框内部移动，拖八个直角手柄缩放（坐标始终是屏幕像素）。

import { api } from "../api.js";
import { toast } from "../toast.js";

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const MIN_SIZE = 4; // 小于这个尺寸就当成了点选

let resolveFn = null;
let mode = "point";
let screen = [0, 0];
let point = null;
let region = null; // {x, y, w, h} 屏幕坐标
let drag = null; // {kind: "new"|"move"|"resize", dir, start, origin}
let controller = null;

const el = (id) => document.getElementById(id);

export function initPicker() {
  const dialog = el("pickerDialog");
  dialog.addEventListener("close", () => finish(null));
}

/**
 * 打开取点/框选弹层。
 * @param {{mode?: "point"|"region", title?: string}} opts
 * @returns {Promise<{x?: number, y?: number, box?: number[]} | null>}
 */
export async function openPicker({ mode: wanted = "point", title = "", initial = null } = {}) {
  const dialog = el("pickerDialog");
  if (!dialog) return null;

  mode = wanted;
  point = null;
  region = initial && initial.length === 4 ? {
    x: initial[0], y: initial[1], w: initial[2], h: initial[3],
  } : null;
  drag = null;

  dialog.querySelector("#pickerTitle").textContent =
    title || (mode === "point" ? "在屏幕上取坐标" : "框选区域");
  dialog.querySelector("#pickerHint").textContent =
    mode === "point"
      ? "点一下要点击的位置，坐标会填到表单里。"
      : "拖出一个框；拖框内部可移动，拖角上的小方块可放大缩小。";
  el("pickerPoint").textContent = "—";
  el("pickerRegion").textContent = "—";
  el("pickerBox").hidden = true;
  el("pickerConfirm").disabled = true;

  let shot;
  try {
    shot = await api.screenshot();
  } catch (err) {
    toast(err.message, "danger");
    return null;
  }

  screen = shot.screen || [0, 0];
  dialog.querySelector("#pickerHint").textContent += `（屏幕 ${screen[0]}×${screen[1]}）`;
  const img = el("pickerImage");
  img.src = `data:image/png;base64,${shot.png}`;
  // 等图片解码完（headless 里 decode() 有时不 resolve，加个超时兜底）
  await new Promise((resolve) => {
    if (img.complete && img.naturalWidth) {
      resolve();
      return;
    }
    const done = () => resolve();
    img.addEventListener("load", done, { once: true });
    img.addEventListener("error", done, { once: true });
    setTimeout(done, 1200);
  });

  dialog.showModal();

  // 等一拍让弹层完成布局，再把截图按可用空间等比放大（坐标换算依赖 img 元素本身的大小）
  await new Promise((resolve) => setTimeout(resolve, 32));

  const stage = el("pickerStage");
  const ratio = screen[0] / (screen[1] || 1) || 1;
  const availW = Math.max(160, stage.clientWidth - 4);
  const availH = Math.max(120, stage.clientHeight - 4);
  const width = Math.min(availW, availH * ratio);
  img.style.width = `${Math.floor(width)}px`;
  img.style.height = `${Math.floor(width / ratio)}px`;

  prepareBox();
  bindStage(stage, img);
  paint();
  readout();

  return new Promise((resolve) => {
    resolveFn = resolve;
  });
}

/* ------------------------------------------------------------ 框 */

function prepareBox() {
  const box = el("pickerBox");
  if (box.dataset.ready === "1") return;
  box.dataset.ready = "1";

  const label = document.createElement("span");
  label.className = "picker__size";
  box.appendChild(label);

  for (const dir of HANDLES) {
    const handle = document.createElement("span");
    handle.className = `picker__handle picker__handle--${dir}`;
    handle.dataset.dir = dir;
    box.appendChild(handle);
  }
}

function bindStage(stage, img) {
  if (controller) controller.abort();
  controller = new AbortController();
  const { signal } = controller;

  stage.addEventListener(
    "pointerdown",
    (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      const pt = toScreen(event, img);
      const dir = event.target?.dataset?.dir || "";

      if (dir && region) {
        drag = { kind: "resize", dir, start: pt, origin: { ...region } };
      } else if (region && inside(pt, region)) {
        drag = { kind: "move", start: pt, origin: { ...region } };
      } else {
        drag = { kind: "new", start: pt, origin: { x: pt.x, y: pt.y, w: 0, h: 0 } };
        region = { ...drag.origin };
        point = pt;
        paint();
      }

      event.preventDefault();
      try {
        stage.setPointerCapture?.(event.pointerId);
      } catch {
        /* 合成事件没有真实 pointerId，忽略 */
      }
      readout();
    },
    { signal },
  );

  stage.addEventListener(
    "pointermove",
    (event) => {
      if (!drag) return;
      const pt = toScreen(event, img);
      const dx = pt.x - drag.start.x;
      const dy = pt.y - drag.start.y;

      if (drag.kind === "new") region = rectOf(drag.start, pt);
      else if (drag.kind === "move") region = moveBy(drag.origin, dx, dy);
      else region = resized(drag.origin, drag.dir, dx, dy);

      paint();
      readout();
    },
    { signal },
  );

  const finishDrag = () => {
    if (!drag) return;
    drag = null;
    if (region && (region.w < MIN_SIZE || region.h < MIN_SIZE)) {
      // 只是点了一下：当成点选，清掉框
      region = null;
      el("pickerBox").hidden = true;
    }
    readout();
  };
  stage.addEventListener("pointerup", finishDrag, { signal });
  stage.addEventListener("pointercancel", finishDrag, { signal });

  el("pickerConfirm").onclick = () => {
    if (mode === "region" && region) {
      finish({ box: [region.x, region.y, region.w, region.h] });
      return;
    }
    if (point) finish({ x: point.x, y: point.y });
  };

  document.querySelectorAll("#pickerDialog [data-close]").forEach((btn) => {
    btn.onclick = () => finish(null);
  });
}

/** 把屏幕坐标的框画到截图元素上（截图可能被缩放）。 */
function paint() {
  const boxEl = el("pickerBox");
  const img = el("pickerImage");
  const stage = el("pickerStage");
  if (!region || region.w < 1 || region.h < 1) {
    boxEl.hidden = true;
    return;
  }

  const ir = img.getBoundingClientRect();
  const sr = stage.getBoundingClientRect();
  const sx = ir.width / (screen[0] || 1);
  const sy = ir.height / (screen[1] || 1);

  boxEl.hidden = false;
  boxEl.style.left = `${ir.left - sr.left + region.x * sx}px`;
  boxEl.style.top = `${ir.top - sr.top + region.y * sy}px`;
  boxEl.style.width = `${region.w * sx}px`;
  boxEl.style.height = `${region.h * sy}px`;

  const label = boxEl.querySelector(".picker__size");
  if (label) label.textContent = `${Math.round(region.w)} × ${Math.round(region.h)}`;
}

function readout() {
  if (region && region.w >= 1 && region.h >= 1) {
    el("pickerPoint").textContent = `${Math.round(region.x)}, ${Math.round(region.y)}`;
    el("pickerRegion").textContent =
      `${Math.round(region.x)}, ${Math.round(region.y)}  ${Math.round(region.w)}×${Math.round(region.h)}`;
    el("pickerConfirm").disabled = false;
    return;
  }
  el("pickerRegion").textContent = "—";
  if (point) el("pickerPoint").textContent = `${point.x}, ${point.y}`;
  el("pickerConfirm").disabled = !point;
}

/* ------------------------------------------------------------ 几何 */

function rectOf(a, b) {
  const x = Math.max(0, Math.min(a.x, b.x));
  const y = Math.max(0, Math.min(a.y, b.y));
  const w = Math.min(screen[0] - x, Math.abs(b.x - a.x));
  const h = Math.min(screen[1] - y, Math.abs(b.y - a.y));
  return { x, y, w, h };
}

function inside(pt, box) {
  return pt.x >= box.x && pt.x <= box.x + box.w && pt.y >= box.y && pt.y <= box.y + box.h;
}

function moveBy(origin, dx, dy) {
  const x = Math.min(Math.max(0, origin.x + dx), Math.max(0, screen[0] - origin.w));
  const y = Math.min(Math.max(0, origin.y + dy), Math.max(0, screen[1] - origin.h));
  return { x, y, w: origin.w, h: origin.h };
}

function resized(origin, dir, dx, dy) {
  let { x, y, w, h } = origin;
  if (dir.includes("w")) {
    const nx = Math.max(0, Math.min(origin.x + dx, origin.x + origin.w - MIN_SIZE));
    w = origin.x + origin.w - nx;
    x = nx;
  }
  if (dir.includes("e")) {
    w = Math.max(MIN_SIZE, Math.min(origin.w + dx, screen[0] - origin.x));
  }
  if (dir.includes("n")) {
    const ny = Math.max(0, Math.min(origin.y + dy, origin.y + origin.h - MIN_SIZE));
    h = origin.y + origin.h - ny;
    y = ny;
  }
  if (dir.includes("s")) {
    h = Math.max(MIN_SIZE, Math.min(origin.h + dy, screen[1] - origin.y));
  }
  return { x, y, w, h };
}

function toScreen(event, img) {
  const rect = img.getBoundingClientRect();
  const x = Math.round(((event.clientX - rect.left) / rect.width) * screen[0]);
  const y = Math.round(((event.clientY - rect.top) / rect.height) * screen[1]);
  return {
    x: Math.max(0, Math.min(screen[0], x)),
    y: Math.max(0, Math.min(screen[1], y)),
  };
}

function finish(result) {
  const dialog = el("pickerDialog");
  if (dialog && dialog.open) dialog.close();
  if (controller) {
    controller.abort();
    controller = null;
  }
  drag = null;
  const fn = resolveFn;
  resolveFn = null;
  if (fn) fn(result);
}
