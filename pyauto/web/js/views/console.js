// 底部：运行日志。只增量追加，避免每条日志重建整块 DOM。

import { h, mount } from "../dom.js";
import * as store from "../store.js";

let rendered = 0;

export function renderConsole(root, { full = false } = {}) {
  if (!root) return;
  const logs = store.state.logs;

  if (logs.length < rendered) full = true;

  if (full || !rendered) {
    mount(root, logs.length ? [] : [h("p.console__empty", { text: store.state.consoleEmptyHint })]);
    rendered = 0;
  }

  for (let i = rendered; i < logs.length; i += 1) {
    root.append(row(logs[i]));
  }
  rendered = logs.length;

  if (store.state.logs.length) {
    const empty = root.querySelector(".console__empty");
    if (empty) empty.remove();
  }

  const auto = document.getElementById("autoScroll");
  if (!auto || auto.checked) root.scrollTop = root.scrollHeight;
}

export function resetConsole() {
  rendered = 0;
}

function row(entry) {
  const level = String(entry.level || "INFO").toUpperCase();
  const time = formatTime(entry.t);
  const text = clean(entry.msg || "");
  return h("div.logrow", { dataset: { level } }, [
    h("span.logrow__time", { text: time }),
    h("span.logrow__level", { dataset: { level }, text: levelLabel(level) }),
    h("span.logrow__msg", { text, title: text }),
  ]);
}

/** 把 OCR 之类来源里的控制字符变成空格，避免一条日志把版面撑开。 */
function clean(text) {
  return String(text)
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]+/g, " ")
    .replace(/\s*\n\s*/g, " · ")
    .trim();
}

function levelLabel(level) {
  if (level === "OK") return "OK";
  if (level === "WARN") return "WARN";
  if (level === "ERROR") return "ERR";
  if (level === "RECOVER") return "规则";
  return "INFO";
}

function formatTime(seconds) {
  const date = seconds ? new Date(seconds * 1000) : new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}
