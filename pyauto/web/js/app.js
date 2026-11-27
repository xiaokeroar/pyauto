// 应用入口：装配视图、绑定操作、订阅运行事件。

import { h, debounce } from "./dom.js";
import { api, subscribeEvents } from "./api.js";
import * as store from "./store.js";
import { renderPalette, renderSteps, renderCombos, refreshSelection, bindStepsKeyboard } from "./views/library.js";
import { renderEditor } from "./views/editor.js";
import { renderPreview, schedulePreviewUpdate, currentPreviewText } from "./views/preview.js";
import { renderConsole, resetConsole } from "./views/console.js";
import { initPicker } from "./views/picker.js";
import { initPointPicker } from "./views/pointpick.js";
import { initRules, openRules } from "./views/rules.js";
import { initSettings, getUi, hotkeyAction } from "./views/settings.js";
import { initLayout } from "./layout.js";
import { toast } from "./toast.js";
import { STEP_LABELS } from "./icons.js";

const dom = {
  palette: document.getElementById("palette"),
  steps: document.getElementById("steps"),
  stepsCount: document.getElementById("stepsCount"),
  editor: document.getElementById("editor"),
  editorSubtitle: document.getElementById("editorSubtitle"),
  preview: document.getElementById("previewBody"),
  console: document.getElementById("console"),
  statusDot: document.getElementById("statusDot"),
  statusText: document.getElementById("statusText"),
  fileChipText: document.getElementById("fileChipText"),
  dirtyDot: document.getElementById("dirtyDot"),
  rulesBadge: document.getElementById("rulesBadge"),
  btnRun: document.getElementById("btnRun"),
  btnStop: document.getElementById("btnStop"),
  banner: document.getElementById("errorBanner"),
  errorText: document.getElementById("errorText"),
  btnMakeCombo: document.getElementById("btnMakeCombo"),
  btnMakeComboTop: document.getElementById("btnMakeComboTop"),
  comboChips: document.getElementById("comboChips"),
  paletteCombos: document.getElementById("paletteCombos"),
  comboDialog: document.getElementById("comboDialog"),
  comboName: document.getElementById("comboName"),
  comboPreview: document.getElementById("comboPreview"),
  comboHint: document.getElementById("comboHint"),
  comboConfirm: document.getElementById("comboConfirm"),
};

function renderLibrary() {
  renderPalette(dom.palette);
  renderSteps(dom.steps);
  renderCombos(dom.comboChips, dom.paletteCombos);
  dom.stepsCount.textContent = String(store.tasks().length);
  const marked = store.markedIndexes().length;
  dom.btnMakeCombo.hidden = marked === 0;
  dom.btnMakeCombo.textContent = marked > 1 ? `存成组合（${marked} 步）` : "存成组合";
}

function renderEditorPanel() {
  renderEditor(dom.editor);
  const task = store.currentTask();
  dom.editorSubtitle.textContent = task ? `第 ${store.state.selected + 1} 步` : "选中左边的一个步骤";
}

function renderConsolePanel(options) {
  renderConsole(dom.console, options);
}

function updateChrome() {
  const state = store.state;

  dom.fileChipText.textContent = state.path ? state.path.split(/[\\/]/).pop() : "tasks.json";
  dom.dirtyDot.hidden = !state.dirty;
  dom.fileChipText.title = state.path || "";

  const count = (state.config.recover || []).length;
  dom.rulesBadge.hidden = !count;
  dom.rulesBadge.textContent = String(count);

  const [dot, text] = statusView(state);
  dom.statusDot.dataset.state = dot;
  dom.statusText.textContent = text;

  const busy = state.status !== "idle";
  dom.btnRun.disabled = busy || state.loading;
  dom.btnStop.disabled = !busy;
  dom.btnStop.setAttribute("aria-busy", String(state.status === "stopping"));

  dom.banner.hidden = !state.error;
  if (state.error) dom.errorText.textContent = state.error;
}

function statusView(state) {
  if (state.loading) return ["idle", "加载中…"];
  if (state.error) return ["failed", "没连上服务"];
  if (state.status === "running") return ["running", "运行中…"];
  if (state.status === "stopping") return ["stopping", "停止中…"];
  const summary = state.summary;
  if (summary) {
    if (summary.error) return ["failed", "上次出错"];
    if (summary.failed) return ["failed", `上次 ${summary.ok}/${summary.total} 成功`];
    return ["done", `上次 ${summary.ok}/${summary.total} 成功`];
  }
  return ["idle", "就绪"];
}

/* ------------------------------------------------------------ 文件菜单 */

async function toggleFileMenu() {
  const menu = document.getElementById("fileMenu");
  if (!menu) return;
  if (!menu.hidden) {
    closeFileMenu();
    return;
  }
  menu.hidden = false;
  document.getElementById("fileChip").setAttribute("aria-expanded", "true");
  await refreshFileMenu();
  document.getElementById("fileNewName").focus({ preventScroll: true });
}

function closeFileMenu() {
  const menu = document.getElementById("fileMenu");
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  document.getElementById("fileChip").setAttribute("aria-expanded", "false");
}

function shortName(path) {
  return path ? String(path).split(/[\\/]/).pop() : "";
}

async function refreshFileMenu() {
  const list = document.getElementById("fileList");
  if (!list) return;
  let items = [];
  try {
    const data = await api.files(store.state.path);
    items = data.items || [];
  } catch (err) {
    list.replaceChildren(h("p.filemenu__hint", { text: err.message }));
    return;
  }

  if (!items.length) {
    list.replaceChildren(h("p.filemenu__hint", { text: "目录里还没有 json 文件" }));
    return;
  }

  const current = shortName(store.state.path);
  list.replaceChildren(
    ...items.map((item) =>
      h(
        "button",
        {
          type: "button",
          class: `filemenu__item${item.name === current ? " is-current" : ""}`,
          role: "menuitemradio",
          "aria-checked": String(item.name === current),
          "on:click": () => switchFile(item.name),
        },
        [
          h("span.filemenu__name", { text: item.name }),
          h("span.filemenu__meta", {
            text: item.tasks == null ? "读不出来" : `${item.tasks} 步`,
          }),
        ],
      ),
    ),
  );
}

async function switchFile(name) {
  if (name === shortName(store.state.path)) {
    closeFileMenu();
    return;
  }
  if (store.state.dirty && !window.confirm(`${shortName(store.state.path)} 有没保存的修改，切过去会丢掉。继续？`)) {
    return;
  }
  if (store.state.status !== "idle") {
    toast("还在跑，先停一下再切文件", "danger");
    return;
  }
  try {
    await store.openConfig(name);
    closeFileMenu();
    toast(`已切到 ${name}`);
  } catch (err) {
    toast(err.message, "danger");
  }
}

/* ------------------------------------------------------------ 渲染调度 */

const renderPreviewDebounced = debounce(() => renderPreview(dom.preview), 60);

store.subscribe((state, reason) => {
  if (reason === "selection") {
    // 只换高亮，不重建列表：否则点第 5 步会跳回列表顶部（滚动位置被清掉）
    refreshSelection(dom.steps);
    const marked = store.markedIndexes().length;
    dom.btnMakeCombo.hidden = marked === 0;
    dom.btnMakeCombo.textContent = marked > 1 ? `存成组合（${marked} 步）` : "存成组合";
  } else if (reason === "combos") {
    renderCombos(dom.comboChips, dom.paletteCombos);
    renderEditorPanel();
  } else if (
    reason === "bootstrap" ||
    reason === "reload" ||
    reason === "config" ||
    reason === "tasks" ||
    reason === "task" ||
    reason === "steps" ||
    reason === "templates"
  ) {
    renderLibrary();
  }
  if (
    reason === "bootstrap" ||
    reason === "reload" ||
    reason === "config" ||
    reason === "selection" ||
    reason === "tasks" ||
    reason === "task-shape" ||
    reason === "steps" ||
    reason === "rules" ||
    reason === "templates"
  ) {
    renderEditorPanel();
  }
  if (
    ["bootstrap", "reload", "config", "tasks", "task-shape", "task", "settings", "rules", "tab"].includes(reason)
  ) {
    schedulePreviewUpdate();
    renderPreviewDebounced();
  }
  if (reason === "log") renderConsolePanel();
  if (reason === "bootstrap" || reason === "reload" || reason === "config") renderConsolePanel({ full: true });
  if (reason === "log-clear") {
    resetConsole();
    renderConsolePanel({ full: true });
  }
  updateChrome();
});

/* ------------------------------------------------------------ 操作 */

async function runAll() {
  if (store.state.status !== "idle") return;
  store.clearLogs();
  store.setState({ status: "running", summary: null }, "status");
  resetConsole();
  renderConsolePanel({ full: true });
  try {
    await api.run(store.cleanConfig(), false);
  } catch (err) {
    store.setState({ status: "idle", error: err.message }, "status");
    toast(err.message, "danger");
  }
}

async function stopRun() {
  try {
    await api.stop();
    store.setState({ status: "stopping" }, "status");
  } catch (err) {
    toast(err.message, "danger");
  }
}

async function saveConfig() {
  try {
    const saved = await api.saveConfig(store.cleanConfig(), store.state.path);
    store.markSaved(saved.path);
    toast(`已保存到 ${shortPath(saved.path)}`);
  } catch (err) {
    toast(err.message, "danger");
  }
}

function shortPath(path) {
  const text = String(path || "").replace(/\\/g, "/");
  const parts = text.split("/");
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : text;
}

async function copyPreview() {
  const text = currentPreviewText();
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制到剪贴板");
  } catch (err) {
    toast("浏览器不让写剪贴板，请手动选中复制", "danger");
  }
}

function exportPreview() {
  const isJson = store.state.tab === "json";
  const text = currentPreviewText();
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = isJson ? "tasks.json" : "tasks.py";
  link.click();
  URL.revokeObjectURL(url);
  toast(isJson ? "已导出 tasks.json" : "已导出 tasks.py");
}

async function editGlobalRules() {
  const result = await openRules({ rules: store.state.config.recover || [], title: "异常处理（全局）" });
  if (result) {
    store.setRules(result);
    toast(result.length ? `已保存 ${result.length} 条全局规则` : "已清空全局规则");
  }
}

/* ------------------------------------------------------------ 事件绑定 */

function bind() {
  dom.btnRun.addEventListener("click", runAll);
  dom.btnStop.addEventListener("click", stopRun);
  document.getElementById("btnSave").addEventListener("click", saveConfig);
  document.getElementById("btnCopy").addEventListener("click", copyPreview);
  document.getElementById("btnExport").addEventListener("click", exportPreview);
  document.getElementById("btnRules").addEventListener("click", editGlobalRules);

  dom.btnMakeCombo.addEventListener("click", makeCombo);
  dom.btnMakeComboTop.addEventListener("click", makeCombo);
  // 取消 / ✕ 关掉组合弹层（弹层的 close 事件里会 resolve(null)）
  dom.comboDialog.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => dom.comboDialog.close());
  });
  document.addEventListener("combo:edit", (event) => {
    editCombo(event.detail).catch((err) => toast(err.message, "danger"));
  });

  document.getElementById("btnClear").addEventListener("click", () => {
    if (!store.tasks().length) return;
    if (!window.confirm("清空全部步骤？")) return;
    store.clearTasks();
    toast("已清空步骤");
  });

  document.getElementById("btnClearLog").addEventListener("click", () => {
    store.setState({ logs: [] }, "log-clear");
  });

  document.getElementById("fileChip").addEventListener("click", async (event) => {
    event.stopPropagation();
    toggleFileMenu();
  });

  document.getElementById("fileNewForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = document.getElementById("fileNewName").value.trim();
    if (!name) {
      toast("先给文件起个名字", "danger");
      return;
    }
    if (store.state.dirty && !window.confirm("当前文件有没保存的修改，切过去会丢掉。继续？")) return;
    try {
      const made = await store.createConfig(name);
      document.getElementById("fileNewName").value = "";
      closeFileMenu();
      toast(`已新建并切到 ${made.name}`);
    } catch (err) {
      toast(err.message, "danger");
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".filepick")) closeFileMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeFileMenu();
  });

  document.getElementById("btnRetry").addEventListener("click", async () => {
    const ok = await store.bootstrap();
    if (ok) toast("已连上本地服务");
  });

  document.querySelectorAll(".segmented__btn").forEach((btn) => {
    btn.addEventListener("click", () => store.setTab(btn.dataset.tab));
  });

  document.querySelectorAll(".tabbar__btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.goto;
      document.querySelectorAll(".tabbar__btn").forEach((item) => {
        item.setAttribute("aria-current", String(item === btn));
      });
      document.querySelectorAll("[data-pane]").forEach((panel) => {
        panel.classList.toggle("is-active", panel.dataset.pane === target);
      });
    });
  });

  document.addEventListener("keydown", (event) => {
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveConfig();
    } else if (mod && event.key === "Enter") {
      event.preventDefault();
      runAll();
    }

    // 运行快捷键（可在设置里改，默认 F9 启动 / F10 停止）
    if (!mod && !event.altKey) {
      const act = hotkeyAction(event);
      if (act === "start") {
        event.preventDefault();
        runAll();
      } else if (act === "stop") {
        event.preventDefault();
        stopRun();
      }
    }
  });

  bindStepsKeyboard(dom.steps);
}

/* ------------------------------------------------------------ 运行事件 */

const SEARCH = new URLSearchParams(window.location.search);
let eventSource = null;
let lastSeq = -1;

async function connectEvents() {
  // 只订阅「现在之后」的事件：刷新页面时不要把上一轮的日志再倒一遍
  let since = 0;
  try {
    const state = await api.state();
    since = Number(state.seq) || 0;
    lastSeq = since;
  } catch (err) {
    since = 0;
  }

  eventSource = subscribeEvents({
    since,
    onEvent: (event) => {
      if (typeof event.seq === "number") {
        if (event.seq <= lastSeq) return;
        lastSeq = event.seq;
      }
      if (event.type === "log") {
        store.pushLog({ level: event.level, msg: event.msg, t: event.t });
      } else if (event.type === "status") {
        store.setState({ status: event.status }, "status");
      } else if (event.type === "done") {
        const summary = event.summary || { total: 0, ok: 0, failed: 0 };
        store.setState({ status: "idle", summary }, "status");
        const tone = summary.failed ? "danger" : "info";
        toast(`运行结束：${summary.ok}/${summary.total} 成功`, tone);
      } else if (event.type === "hello") {
        if (event.status && event.status !== "idle") {
          store.setState({ status: event.status }, "status");
        }
      }
    },
    onError: () => {
      /* EventSource 自己会重连，这里不打断用户 */
    },
  });
}

/* ------------------------------------------------------------ 启动 */

async function main() {
  initPicker();
  initPointPicker();
  initRules();
  initSettings();
  initLayout(document.getElementById("workspace"));
  bind();

  // 移动端先显示「动作」栏
  document.querySelector('[data-pane="library"]')?.classList.add("is-active");

  const ok = await store.bootstrap();
  if (!ok) {
    store.setState({ error: store.state.error || "连不上本地服务" }, "error");
  } else if (SEARCH.has("file")) {
    // ?file=other.json 直接打开另一个任务文件（方便收藏/分享链接）
    try {
      await store.openConfig(SEARCH.get("file"));
    } catch (err) {
      toast(err.message, "danger");
    }
  }
  // 截图/离线预览时用 ?noevents=1 关掉 SSE，否则浏览器一直等长连接
  if (!SEARCH.has("noevents")) {
    connectEvents();
  }
  schedulePreviewUpdate();

  if (store.state.meta && store.state.meta.stale) {
    toast("后台代码更新过，重启 pyauto web 之后新功能才会生效", "warn");
  }


}

main();

/**
 * 组合对话框：新建 / 改名 / 覆盖都用它。
 * @param {{title?: string, name?: string, tasks: object[], hint?: string}} options
 * @returns {Promise<string | null>} 组合名，取消返回 null
 */
function askComboName(options) {
  const { title = "存成组合", name = "", tasks = [], hint = "" } = options || {};
  return new Promise((resolve) => {
    const dialog = dom.comboDialog;
    dialog.querySelector(".modal__title").textContent = title;
    dom.comboHint.textContent =
      hint || `把选中的 ${tasks.length} 步存成组合，以后在动作库下面的「我的组合」里一键插进来。`;
    dom.comboName.value = name;
    dom.comboName.select();

    // 预览：这串组合里到底是哪几步
    dom.comboPreview.replaceChildren(
      ...tasks.map((task, i) => {
        const kind = store.stepKind(task);
        return h("div.combo-preview__row", {}, [
          h("span.combo-preview__no", { text: String(i + 1) }),
          h("span.combo-preview__kind", { text: STEP_LABELS[kind] || kind }),
          h("span.combo-preview__name", {
            text: store.stepTitle(task, i) || "（未命名）",
          }),
        ]);
      }),
    );

    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      dom.comboConfirm.removeEventListener("click", onConfirm);
      dialog.removeEventListener("close", onClose);
      dialog.close();
      resolve(value);
    };
    const onConfirm = () => {
      const value = dom.comboName.value.trim();
      if (!value) {
        dom.comboName.focus();
        toast("给组合起个名字", "warn");
        return;
      }
      finish(value);
    };
    const onClose = () => finish(null);
    dom.comboConfirm.addEventListener("click", onConfirm);
    dialog.addEventListener("close", onClose);
    dialog.showModal();
    setTimeout(() => dom.comboName.focus(), 40);
    dom.comboName.onkeydown = (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        onConfirm();
      }
    };
  });
}

/** 把当前选中的一段步骤存成组合。 */
async function makeCombo() {
  const tasks = store.markedTasks();
  if (!tasks.length) {
    toast("先选中要存成组合的步骤（按住 Shift 点另一个步骤可以选中一段）", "warn");
    return;
  }
  const name = await askComboName({ tasks });
  if (!name) return;
  const dup = (store.state.combos || []).find((combo) => combo.name === name);
  if (dup && !confirm(`已经有同名组合「${name}」了，覆盖它的内容？`)) return;
  try {
    await store.saveCombo(name, tasks);
    toast(`已存成组合「${name}」（${tasks.length} 步）`);
    store.clearMark();
  } catch (err) {
    toast(err.message, "danger");
  }
}

/** 编辑已有组合：改名就 存新的 + 删旧的，同名就是覆盖内容。 */
async function editCombo(item) {
  if (!item) return;
  const name = await askComboName({
    title: "编辑组合",
    name: item.name,
    tasks: item.tasks,
    hint: `改名字会另存一份并删掉旧的；只改内容就保持原名直接保存。`,
  });
  if (!name) return;
  try {
    await store.saveCombo(name, item.tasks);
    if (name !== item.name) await store.deleteCombo(item.name);
    toast(name === item.name ? `已更新「${name}」` : `已重命名为「${name}」`);
  } catch (err) {
    toast(err.message, "danger");
  }
}

