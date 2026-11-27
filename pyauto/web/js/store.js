// 全局状态 + 最小订阅模型。视图订阅后按需重绘。

import { api } from "./api.js";

const EMPTY_CONFIG = {
  log: "logs/web.log",
  history: "logs/history.jsonl",
  stop_on_error: true,
  timeout: 120,
  tasks: [],
  recover: [],
};

export const state = {
  loading: true,
  error: "",
  meta: null,
  path: "tasks.json",
  exists: false,
  config: structuredClone(EMPTY_CONFIG),
  selected: -1,
  child: -1,
  tab: "json",
  dirty: false,
  status: "idle", // idle | running | stopping
  summary: null,
  templates: [],
  combos: [],
  mark: null,
  anchor: -1,
  open: new Map(),
  logs: [],
  consoleEmptyHint: "点右上角「运行」，日志会出现在这里。",
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function notify(reason = "") {
  for (const fn of listeners) fn(state, reason);
}

export function setState(changes, reason = "") {
  Object.assign(state, changes);
  notify(reason);
}

/** 保存/运行前清一遍：把空的 at / every / interval 之类去掉，别把 null/"" 写进 JSON。 */
export function cleanConfig(cfg = state.config) {
  const copy = structuredClone(cfg);
  const clean = (task) => {
    if (!task || typeof task !== "object") return;
    for (const key of ["at", "every", "interval", "times"]) {
      if (key in task && (task[key] === null || task[key] === "")) delete task[key];
    }
    if (task.type === "repeat" && !task.at && !task.every && task.interval === 0) delete task.interval;
    if (Array.isArray(task.tasks)) task.tasks.forEach(clean);
    if (Array.isArray(task.then)) task.then.forEach(clean);
  };
  (copy.tasks || []).forEach(clean);
  return copy;
}

export function tasks() {
  return state.config.tasks || [];
}

export function currentTask() {
  const list = tasks();
  return state.selected >= 0 && state.selected < list.length ? list[state.selected] : null;
}

/** 当前正在编辑的对象：选中了重复块的子步骤时返回子步骤，否则返回选中的步骤。 */
export function currentStep() {
  const task = currentTask();
  if (!task) return null;
  if (state.child >= 0 && Array.isArray(task.tasks)) return task.tasks[state.child] || null;
  return task;
}

export function isEditingChild() {
  return state.child >= 0;
}

/** 某个步骤（或它的子步骤）真正存放的数组。 */
function listAt(index, child = -1) {
  const task = state.config.tasks[index];
  if (child >= 0) return (task && Array.isArray(task.tasks)) ? task.tasks : null;
  return state.config.tasks;
}

/* ------------------------------------------------------------ 步骤语义 */

export function stepKind(task) {
  if (!task) return "raw";
  if (task.type === "input") {
    if (task.action === "type") return "type";
    if (task.action === "key") return "key";
    return "click";
  }
  if (task.type === "repeat") return "repeat";
  if (task.type === "group") return "group";
  if (task.type === "sleep") return "wait";
  if (task.type === "ocr" || task.type === "image" || task.type === "shell" || task.type === "python") {
    return task.type;
  }
  return "raw";
}

const KIND_TITLES = {
  click: "点击",
  type: "输入",
  key: "按键",
  wait: "等待",
  ocr: "找字",
  image: "找图",
  shell: "命令",
  python: "脚本",
  raw: "自定义",
  repeat: "重复",
  group: "组合",
};

export function stepTitle(task) {
  const name = (task && task.name ? String(task.name) : "").trim();
  if (name) return name;
  const kind = stepKind(task);
  return KIND_TITLES[kind] || "步骤";
}

export function stepMeta(task) {
  const kind = stepKind(task);
  const parts = [];
  if (kind === "click") parts.push(`${task.x ?? 0}, ${task.y ?? 0}`);
  if (kind === "type") parts.push(shortValue(task.value));
  if (kind === "key") parts.push(task.value || "—");
  if (kind === "wait") parts.push(`${task.seconds ?? 0} 秒`);
  if (kind === "ocr") parts.push(`「${task.expect || "未填"}」`);
  if (kind === "image") {
    parts.push(templateName(task.template));
    const pt = Array.isArray(task.point) ? task.point : null;
    if (pt && pt.length >= 2) {
      parts.push(`点 ${Math.round(Number(pt[0]) * 100)}%,${Math.round(Number(pt[1]) * 100)}%`);
    }
  }
  if (kind === "shell") parts.push(shortValue(task.cmd));
  if (kind === "python") parts.push(shortValue(task.script || task.code));
  if (kind === "repeat") {
    const inner = Array.isArray(task.tasks) ? task.tasks.length : 0;
    parts.push(`×${task.times ?? 1}`);
    parts.push(`${inner} 步`);
  }
  if (kind === "group") {
    parts.push(`${Array.isArray(task.tasks) ? task.tasks.length : 0} 步`);
  }
  if (Array.isArray(task.then) && task.then.length) parts.push(`→${task.then.length}`);
  if (Array.isArray(task.recover) && task.recover.length) parts.push(`⟲${task.recover.length}`);
  return parts.filter(Boolean).join(" · ");
}

function shortValue(value, max = 22) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) return "—";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function templateName(path) {
  const raw = String(path || "").replace(/\\/g, "/");
  return raw ? raw.split("/").pop() : "未选模板";
}

/* ------------------------------------------------------------ 步骤操作 */

function touch(reason = "tasks") {
  state.dirty = true;
  notify(reason);
}

export function addTask(task) {
  const parent = currentTask();
  // 正在改「重复块」里的某一步时，继续往同一个块里加
  if (parent && parent.type === "repeat" && state.child >= 0) {
    if (!Array.isArray(parent.tasks)) parent.tasks = [];
    parent.tasks.push(task);
    state.child = parent.tasks.length - 1;
    touch("tasks");
    return;
  }
  state.config.tasks.push(task);
  state.selected = state.config.tasks.length - 1;
  state.child = -1;
  touch("tasks");
}

/** 往某个「重复块」里加一步（选中新加的那步）。 */
export function addChild(index, task) {
  const parent = state.config.tasks[index];
  if (!parent) return;
  if (!Array.isArray(parent.tasks)) parent.tasks = [];
  parent.tasks.push(task);
  state.selected = index;
  state.child = parent.tasks.length - 1;
  touch("tasks");
}

export function updateTask(index, patch, child = -1) {
  const list = listAt(index, child);
  if (!list) return;
  const task = list[child >= 0 ? child : index];
  if (!task) return;
  Object.assign(task, patch);
  touch("task");
}

export function removeTask(index, child = -1) {
  const list = listAt(index, child);
  if (!list) return;
  const at = child >= 0 ? child : index;
  if (at < 0 || at >= list.length) return;
  list.splice(at, 1);
  if (child >= 0) {
    const parent = state.config.tasks[index];
    state.child = Math.min(state.child, (parent.tasks || []).length - 1);
  } else if (state.selected >= state.config.tasks.length) {
    state.selected = state.config.tasks.length - 1;
    state.child = -1;
  }
  touch("tasks");
}

export function moveTask(index, delta, child = -1) {
  const list = listAt(index, child);
  if (!list) return;
  const at = child >= 0 ? child : index;
  const target = at + delta;
  if (at < 0 || at >= list.length || target < 0 || target >= list.length) return;
  const [item] = list.splice(at, 1);
  list.splice(target, 0, item);
  if (child >= 0) state.child = target;
  else state.selected = target;
  touch("tasks");
}

export function duplicateTask(index, child = -1) {
  const list = listAt(index, child);
  if (!list) return;
  const at = child >= 0 ? child : index;
  const task = list[at];
  if (!task) return;
  const copy = structuredClone(task);
  copy.name = `${stepTitle(task)} 副本`;
  list.splice(at + 1, 0, copy);
  if (child >= 0) state.child = at + 1;
  else state.selected = at + 1;
  touch("tasks");
}

export function clearTasks() {
  state.config.tasks = [];
  state.selected = -1;
  state.child = -1;
  touch("tasks");
}

export function selectStep(index, child = -1) {
  state.selected = index;
  state.child = child;
  state.mark = null;
  state.anchor = index;
  notify("selection");
}

/** 选中从 anchor 到 index 的一段（Shift 点出来的），只对顶层步骤有效。 */
export function markTo(index) {
  const from = state.anchor >= 0 ? state.anchor : index;
  state.mark = { from: Math.min(from, index), to: Math.max(from, index) };
  notify("selection");
}

export function clearMark() {
  if (!state.mark) return;
  state.mark = null;
  notify("selection");
}

export function markedIndexes() {
  if (!state.mark) {
    return state.selected >= 0 && state.child < 0 ? [state.selected] : [];
  }
  const out = [];
  for (let i = state.mark.from; i <= state.mark.to; i += 1) out.push(i);
  return out;
}

export function markedTasks() {
  const list = state.config.tasks;
  return markedIndexes()
    .map((i) => list[i])
    .filter(Boolean);
}

/** 把一组动作（组合）插到当前选中项后面；选中重复块就插进块里。 */
export function insertTasks(tasks) {
  const copies = (tasks || []).map((task) => structuredClone(task));
  if (!copies.length) return 0;

  const current = state.config.tasks[state.selected];

  // 正在改重复块里的某一步 → 插到块里那一步后面
  if (state.child >= 0 && current && Array.isArray(current.tasks)) {
    const at = state.child + 1;
    current.tasks.splice(at, 0, ...copies);
    state.child = at;
    state.anchor = state.selected;
    state.mark = null;
    touch("tasks");
    return copies.length;
  }

  // 选中的是重复块本身 → 整段塞进这个块，作为要重复的动作
  if (current && current.type === "repeat") {
    if (!Array.isArray(current.tasks)) current.tasks = [];
    current.tasks.push(...copies);
    state.child = current.tasks.length - 1;
    state.anchor = state.selected;
    state.mark = null;
    touch("tasks");
    return copies.length;
  }

  const list = state.config.tasks;
  const at = state.selected >= 0 ? state.selected + 1 : list.length;
  list.splice(at, 0, ...copies);
  state.selected = at;
  state.child = -1;
  state.anchor = at;
  state.mark = null;
  touch("tasks");
  return copies.length;
}

/**
 * 把组合作为「一项」插进步骤列表（可折叠的组合块）。
 * 选中重复块时塞进块里；选中块内某一步时插在那一步后面。
 */
export function insertGroup(name, tasks) {
  const copies = (tasks || []).map((task) => structuredClone(task));
  const block = {
    type: "group",
    name: `组合：${name}`,
    tasks: copies,
  };
  if (!copies.length) {
    block.tasks = [];
  }

  const current = state.config.tasks[state.selected];

  if (state.child >= 0 && current && Array.isArray(current.tasks)) {
    current.tasks.splice(state.child + 1, 0, block);
    state.child += 1;
  } else if (current && current.type === "repeat") {
    // 重复块里不再套组合块，直接把步骤摊进块里（"组合能插进重复" 的老行为）
    if (!Array.isArray(current.tasks)) current.tasks = [];
    current.tasks.push(...copies.map((task) => structuredClone(task)));
    state.child = current.tasks.length - 1;
  } else {
    const list = state.config.tasks;
    const at = state.selected >= 0 ? state.selected + 1 : list.length;
    list.splice(at, 0, block);
    state.selected = at;
    state.child = -1;
    state.anchor = at;
  }
  state.mark = null;
  touch("tasks");
  return copies.length;
}

/** 折叠/展开一个块（group / repeat），只影响显示：组合块默认收起，重复块默认展开。 */
export function toggleExpanded(index) {
  const task = state.config.tasks[index];
  state.open.set(index, !isExpanded(index, task));
  notify("steps");
}

export function isExpanded(index, task = null) {
  if (state.open.has(index)) return state.open.get(index);
  const node = task || state.config.tasks[index];
  return !(node && node.type === "group");
}

export async function refreshCombos() {
  const data = await api.combos();
  setState({ combos: data.items || [] }, "combos");
  return state.combos;
}

export async function saveCombo(name, tasks) {
  const data = await api.saveCombo(name, tasks);
  setState({ combos: data.items || [] }, "combos");
  return data;
}

export async function deleteCombo(name) {
  const data = await api.deleteCombo(name);
  setState({ combos: data.items || [] }, "combos");
  return data;
}

export function setTab(tab) {
  state.tab = tab;
  notify("tab");
}

export function setSetting(key, value) {
  state.config[key] = value;
  touch("settings");
}

export function setRules(rules) {
  state.config.recover = rules;
  touch("rules");
}

export function upsertRulesForTask(index, rules, child = -1) {
  const task = child >= 0 ? (state.config.tasks[index].tasks || [])[child] : state.config.tasks[index];
  if (!task) return;
  if (rules.length) task.recover = rules;
  else delete task.recover;
  touch("rules");
}

export function markSaved(path) {
  state.dirty = false;
  state.exists = true;
  state.path = path;
  notify("saved");
}

/* ------------------------------------------------------------ 日志 */

const LOG_LIMIT = 600;

export function pushLog(entry) {
  state.logs.push(entry);
  if (state.logs.length > LOG_LIMIT) state.logs.splice(0, state.logs.length - LOG_LIMIT);
  notify("log");
}

export function clearLogs() {
  state.logs = [];
  notify("log");
}

/* ------------------------------------------------------------ 载入 */

export async function bootstrap() {
  try {
    const meta = await api.meta();
    const loaded = await api.loadConfig();
    const templates = await api.templates().catch(() => ({ items: [] }));
    const combos = await api.combos().catch(() => ({ items: [] }));
    setState(
      {
        loading: false,
        error: "",
        meta,
        path: loaded.path,
        exists: loaded.exists,
        config: normalizeConfig(loaded.config),
        templates: templates.items || [],
        combos: combos.items || [],
        mark: null,
        anchor: loaded.config && (loaded.config.tasks || []).length ? 0 : -1,
        selected: loaded.config && (loaded.config.tasks || []).length ? 0 : -1,
        dirty: false,
      },
      "bootstrap",
    );
    return true;
  } catch (err) {
    setState({ loading: false, error: err.message }, "error");
    return false;
  }
}

export function normalizeConfig(config) {
  const next = { ...EMPTY_CONFIG, ...(config || {}) };
  if (!Array.isArray(next.tasks)) next.tasks = [];
  if (!Array.isArray(next.recover)) next.recover = [];
  return next;
}

export async function reloadConfig() {
  const loaded = await api.loadConfig(state.path);
  setState(
    {
      path: loaded.path,
      exists: loaded.exists,
      config: normalizeConfig(loaded.config),
      selected: loaded.exists ? 0 : -1,
      child: -1,
      dirty: false,
      error: "",
    },
    "reload",
  );
  await refreshTemplates().catch(() => {});
}

/** 切到另一个任务文件（顶部文件菜单）。 */
export async function openConfig(path) {
  const loaded = await api.loadConfig(path);
  const list = loaded.config && Array.isArray(loaded.config.tasks) ? loaded.config.tasks : [];
  setState(
    {
      path: loaded.path,
      exists: loaded.exists,
      config: normalizeConfig(loaded.config),
      selected: list.length ? 0 : -1,
      child: -1,
      dirty: false,
      error: "",
      summary: null,
      logs: [],
    },
    "config",
  );
  await refreshTemplates().catch(() => {});
  return loaded;
}

/** 新建一个任务文件并切过去。 */
export async function createConfig(name) {
  const made = await api.newFile(name);
  await openConfig(made.path);
  return made;
}

export async function refreshTemplates() {
  try {
    const data = await api.templates();
    setState({ templates: data.items || [] }, "templates");
  } catch (err) {
    /* 模板列表拿不到不影响主流程 */
  }
}
