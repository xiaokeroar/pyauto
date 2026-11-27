/**
 * 设置面板：界面（配色/字号/密度）、快捷键、运行默认、目录信息。
 * 界面偏好只存浏览器（localStorage），运行默认写进当前任务文件。
 */
import { h } from "../dom.js";
import { icon } from "../icons.js";
import { toast } from "../toast.js";
import * as store from "../store.js";

const KEY = "pyauto.ui.v1";

export const DEFAULTS = {
  accent: "blue",
  font: 13,
  density: "cozy",
  hotkeyStart: "F9",
  hotkeyStop: "F10",
};

const ACCENTS = {
  blue: { label: "蓝", accent: "#2563eb", hover: "#1d4ed8", soft: "#eef3ff", line: "#c9d8fb" },
  teal: { label: "青", accent: "#0f766e", hover: "#0e6b64", soft: "#e6f4f2", line: "#bfe3de" },
  violet: { label: "紫", accent: "#7c3aed", hover: "#6d28d9", soft: "#f1ebff", line: "#ddd0ff" },
  slate: { label: "墨", accent: "#475569", hover: "#3f4b5c", soft: "#eef1f5", line: "#d3dae4" },
};

let ui = { ...DEFAULTS };

export function getUi() {
  return ui;
}

export function loadUi() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "{}");
    ui = { ...DEFAULTS, ...raw };
    if (!ACCENTS[ui.accent]) ui.accent = DEFAULTS.accent;
    ui.font = Math.min(15, Math.max(12, Number(ui.font) || DEFAULTS.font));
  } catch {
    ui = { ...DEFAULTS };
  }
  return ui;
}

function saveUi() {
  try {
    localStorage.setItem(KEY, JSON.stringify(ui));
  } catch {
    /* 隐私模式下写不了就算了 */
  }
}

/** 把界面偏好落到根节点上（CSS 变量 + body class）。 */
export function applyUi() {
  const accent = ACCENTS[ui.accent] || ACCENTS.blue;
  const root = document.documentElement;
  root.style.setProperty("--primary", accent.accent);
  root.style.setProperty("--primary-hover", accent.hover);
  root.style.setProperty("--primary-soft", accent.soft);
  root.style.setProperty("--primary-line", accent.line);
  root.style.setProperty("--app-font", `${ui.font}px`);
  document.body.classList.toggle("is-roomy", ui.density === "roomy");
  document.body.classList.toggle("is-cozy", ui.density !== "roomy");
}

function set(key, value) {
  ui[key] = value;
  saveUi();
  applyUi();
}

function el(tag) {
  return document.getElementById(tag);
}

/* ------------------------------------------------------------ 面板 */

export function initSettings() {
  loadUi();
  applyUi();

  const dialog = el("settingsDialog");
  if (!dialog) return;

  dialog.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => dialog.close());
  });

  // 配色
  const swatch = el("setAccent");
  const paintSwatches = () => {
    swatch.replaceChildren(
      ...Object.entries(ACCENTS).map(([key, item]) =>
        h(
          "button.swatch",
          {
            type: "button",
            class: `swatch${ui.accent === key ? " is-on" : ""}`,
            "aria-pressed": String(ui.accent === key),
            title: `${item.label}色`,
            style: `--swatch:${item.accent}`,
            "on:click": () => {
              set("accent", key);
              paintSwatches();
            },
          },
          [h("span.swatch__dot"), h("span.swatch__name", { text: item.label })],
        ),
      ),
    );
  };
  paintSwatches();

  // 字号
  const font = el("setFont");
  font.value = String(ui.font);
  font.addEventListener("input", () => set("font", Number(font.value)));

  // 密度
  const density = el("setDensity");
  density.value = ui.density;
  density.addEventListener("change", () => set("density", density.value));

  // 快捷键
  const start = el("setHotStart");
  const stop = el("setHotStop");
  start.value = ui.hotkeyStart;
  stop.value = ui.hotkeyStop;
  const commitHotkeys = () => {
    const a = start.value.trim().toUpperCase();
    const b = stop.value.trim().toUpperCase();
    if (!a || !b) return toast("两个快捷键都要填", "danger");
    if (a === b) return toast("启动和停止不能用同一个键", "danger");
    set("hotkeyStart", a);
    set("hotkeyStop", b);
    toast(`快捷键：${a} 启动、${b} 停止`);
  };
  start.addEventListener("change", commitHotkeys);
  stop.addEventListener("change", commitHotkeys);

  // 运行默认（写进任务文件）
  const fillRuntime = () => {
    const cfg = store.state.config;
    el("setTimeout").value = String(cfg.timeout ?? 60);
    el("setStopOnError").checked = cfg.stop_on_error !== false;
    el("setRecoverOnError").checked = cfg.recover_on_error !== false;
    el("setRetries").value = String(cfg.retries ?? 0);
    el("setRetryDelay").value = String(cfg.retry_delay ?? 0.5);
    el("setMeta").textContent = store.state.meta
      ? `${store.state.meta.project_dir || ""}`
      : "—";
  };
  el("setTimeout").addEventListener("input", (e) => {
    const value = Number(e.target.value);
    if (Number.isFinite(value) && value > 0) store.setSetting("timeout", value);
  });
  el("setStopOnError").addEventListener("change", (e) =>
    store.setSetting("stop_on_error", e.target.checked),
  );
  el("setRecoverOnError").addEventListener("change", (e) =>
    store.setSetting("recover_on_error", e.target.checked),
  );
  el("setRetries").addEventListener("input", (e) => {
    const value = Math.max(0, Math.round(Number(e.target.value) || 0));
    store.setSetting("retries", value);
  });
  el("setRetryDelay").addEventListener("input", (e) => {
    const value = Math.max(0, Number(e.target.value) || 0);
    store.setSetting("retry_delay", value);
  });

  dialog.addEventListener("open", fillRuntime);
  el("btnSettings").addEventListener("click", () => {
    fillRuntime();
    dialog.showModal();
  });
}

/** 快捷键是否要在这个元素上忽略（正在输入文字时不抢 F 键之外的键）。 */
function typing(node) {
  if (!node) return false;
  const tag = node.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || node.isContentEditable;
}

export function hotkeyAction(event) {
  const key = String(event.key || "").toUpperCase();
  if (!key) return "";
  if (typing(event.target) && !/^F\d{1,2}$/.test(key)) return "";
  if (key === ui.hotkeyStart) return "start";
  if (key === ui.hotkeyStop) return "stop";
  return "";
}
