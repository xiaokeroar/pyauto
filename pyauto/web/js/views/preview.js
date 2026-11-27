// 右栏：JSON / Python 产物预览（带轻量高亮）。

import { h, mount } from "../dom.js";
import { api } from "../api.js";
import * as store from "../store.js";
import { debounce } from "../dom.js";

let pythonCache = "";
let pythonError = "";
let pythonLoading = false;

/** 生成 Python 预览（服务端调用 studio 的转换逻辑）。 */
const refreshPython = debounce(async () => {
  pythonLoading = true;
  renderPreview(document.getElementById("previewBody"));
  try {
    const data = await api.preview(store.cleanConfig());
    pythonCache = data.python || "";
    pythonError = "";
  } catch (err) {
    pythonError = err.message;
  } finally {
    pythonLoading = false;
    renderPreview(document.getElementById("previewBody"));
  }
}, 220);

export function schedulePreviewUpdate() {
  refreshPython();
}

export function renderPreview(root) {
  if (!root) return;
  const tab = store.state.tab;
  const text = tab === "json" ? JSON.stringify(store.cleanConfig(), null, 2) : pythonCache;

  let body;
  if (tab === "python" && pythonError) {
    body = h("p.field__hint", { text: `生成失败：${pythonError}` });
  } else if (tab === "python" && pythonLoading && !pythonCache) {
    body = h("p.field__hint", { text: "生成中…" });
  } else {
    const html = tab === "json" ? highlightJson(text) : highlightPython(text || "# 还没有可生成的内容");
    body = h("pre", { html });
  }

  mount(root, [body]);

  // 片段标题跟着 tab 变
  const panel = root.closest(".panel--preview");
  if (panel) {
    panel.querySelectorAll(".segmented__btn").forEach((btn) => {
      const selected = btn.dataset.tab === tab;
      btn.setAttribute("aria-selected", String(selected));
      btn.tabIndex = selected ? 0 : -1;
    });
    const editorPanel = root;
    editorPanel.setAttribute("aria-labelledby", tab === "json" ? "tabJson" : "tabPy");
  }
}

export function currentPreviewText() {
  return store.state.tab === "json" ? JSON.stringify(store.state.config, null, 2) : pythonCache;
}

/* ------------------------------------------------------------ 高亮 */

const escapeHtml = (value) =>
  String(value).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]);

const JSON_TOKEN =
  /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

export function highlightJson(text) {
  let out = "";
  let last = 0;
  let match;
  JSON_TOKEN.lastIndex = 0;
  while ((match = JSON_TOKEN.exec(text)) !== null) {
    out += escapeHtml(text.slice(last, match.index));
    const [raw, str, colon, bool, num] = match;
    if (str && colon) {
      out += `<span class="tok-key">${escapeHtml(str)}</span>${escapeHtml(colon)}`;
    } else if (str) {
      out += `<span class="tok-str">${escapeHtml(str)}</span>`;
    } else if (bool) {
      out += `<span class="tok-bool">${bool}</span>`;
    } else if (num) {
      out += `<span class="tok-num">${num}</span>`;
    } else {
      out += escapeHtml(raw);
    }
    last = match.index + raw.length;
  }
  out += escapeHtml(text.slice(last));
  return out;
}

const PY_TOKEN =
  /(#[^\n]*)|('''[\s\S]*?'''|"""[\s\S]*?"""|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*")|\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|in|is|not|and|or|None|True|False|raise|pass|break|continue|global|lambda|yield|assert|del|print|len|range|str|int|float|list|dict)\b|\b(\d+(?:\.\d+)?)\b/g;

export function highlightPython(text) {
  let out = "";
  let last = 0;
  let match;
  PY_TOKEN.lastIndex = 0;
  while ((match = PY_TOKEN.exec(text)) !== null) {
    out += escapeHtml(text.slice(last, match.index));
    const [raw, comment, str, keyword, num] = match;
    if (comment) out += `<span class="tok-com">${escapeHtml(comment)}</span>`;
    else if (str) out += `<span class="tok-str">${escapeHtml(str)}</span>`;
    else if (keyword) out += `<span class="tok-key">${keyword}</span>`;
    else if (num) out += `<span class="tok-num">${num}</span>`;
    else out += escapeHtml(raw);
    last = match.index + raw.length;
  }
  out += escapeHtml(text.slice(last));
  return out;
}
